import asyncio
import faiss
import numpy as np
import logging
import json
import aiosqlite
import os
import tempfile
from datetime import datetime, timedelta
from typing import Dict, Optional, List, Tuple
from pydantic import BaseModel, Field

from ..models.context import CacheResult

logger = logging.getLogger(__name__)

class CacheEntry(BaseModel):
    embedding_id: int
    response_text: str
    model_used: str
    created_at: datetime
    metadata: dict
    session_id: Optional[str] = None

class SessionCache:
    def __init__(self, max_entries: int):
        # Using IndexFlatIP for cosine similarity (assumes normalized vectors)
        self.index = faiss.IndexFlatIP(384)
        self.entries: List[CacheEntry] = []
        self.max_entries = max_entries
        self.lock = asyncio.Lock()

class SemanticCache:
    def __init__(self, config=None):
        # config should be DaemonConfig, here we set defaults if None
        self.l1_similarity_threshold = config.cache.l1_similarity_threshold if config else 0.95
        self.l2_similarity_threshold = config.cache.l2_similarity_threshold if config else 0.88
        self.l1_max_entries = config.cache.l1_max_entries_per_session if config else 500
        self.l2_max_entries = config.cache.l2_max_entries_per_workspace if config else 5000
        self.l2_ttl_days = config.cache.l2_ttl_days if config else 7

        self.l1_caches: Dict[str, SessionCache] = {}
        
        self.l2_index = faiss.IndexFlatIP(384)
        self.l2_entries: List[CacheEntry] = []
        self.l2_lock = asyncio.Lock()

        self.degraded = False

    def _get_l1_cache(self, session_id: str) -> SessionCache:
        if session_id not in self.l1_caches:
            self.l1_caches[session_id] = SessionCache(self.l1_max_entries)
        return self.l1_caches[session_id]

    async def check(self, session_id: str, query_embedding: np.ndarray) -> Optional[CacheResult]:
        if self.degraded:
            return None

        # Ensure embedding shape is (1, d)
        if len(query_embedding.shape) == 1:
            query_embedding = np.expand_dims(query_embedding, axis=0)

        try:
            # 1. Check L1 for this session
            l1_cache = self._get_l1_cache(session_id)
            async with l1_cache.lock:
                if l1_cache.index.ntotal > 0:
                    D, I = l1_cache.index.search(query_embedding, 1)
                    sim = D[0][0]
                    idx = I[0][0]
                    if sim > self.l1_similarity_threshold and idx != -1:
                        entry = l1_cache.entries[idx]
                        logger.info(f"L1 Cache Hit: session={session_id}, sim={sim:.4f}")
                        return CacheResult(hit=True, tier="L1", response=entry.response_text)

            # 2. Check L2 (Workspace)
            async with self.l2_lock:
                if self.l2_index.ntotal > 0:
                    D, I = self.l2_index.search(query_embedding, 1)
                    sim = D[0][0]
                    idx = I[0][0]
                    if sim > self.l2_similarity_threshold and idx != -1:
                        entry = self.l2_entries[idx]
                        # Verify TTL
                        age = datetime.utcnow() - entry.created_at
                        if age.days < self.l2_ttl_days:
                            logger.info(f"L2 Cache Hit: sim={sim:.4f}")
                            return CacheResult(hit=True, tier="L2", response=entry.response_text)
                        else:
                            # Expired
                            pass
        except Exception as e:
            logger.error(f"FAISS search error: {e}")
            self.degraded = True
            return None

        return None

    async def store(self, session_id: str, query_embedding: np.ndarray, response_text: str, model_used: str, metadata: dict) -> None:
        if self.degraded:
            return

        if len(query_embedding.shape) == 1:
            query_embedding = np.expand_dims(query_embedding, axis=0)

        try:
            now = datetime.utcnow()

            # Store in L1
            l1_cache = self._get_l1_cache(session_id)
            async with l1_cache.lock:
                idx_l1 = l1_cache.index.ntotal
                entry_l1 = CacheEntry(
                    embedding_id=idx_l1,
                    response_text=response_text,
                    model_used=model_used,
                    created_at=now,
                    metadata=metadata
                )
                l1_cache.index.add(query_embedding)
                l1_cache.entries.append(entry_l1)
                
                # Evict if full
                if l1_cache.index.ntotal > self.l1_max_entries:
                    await self._evict_l1(l1_cache)

            # Store in L2
            async with self.l2_lock:
                idx_l2 = self.l2_index.ntotal
                entry_l2 = CacheEntry(
                    embedding_id=idx_l2,
                    response_text=response_text,
                    model_used=model_used,
                    created_at=now,
                    metadata=metadata,
                    session_id=session_id
                )
                self.l2_index.add(query_embedding)
                self.l2_entries.append(entry_l2)
                
                # Evict if full
                if self.l2_index.ntotal > self.l2_max_entries:
                    await self._evict_l2()

        except Exception as e:
            logger.error(f"FAISS insert error: {e}")
            self.degraded = True

    async def _evict_l1(self, l1_cache: SessionCache):
        # Evict oldest 20% by creation timestamp
        evict_count = max(1, int(self.l1_max_entries * 0.2))
        # Find indices of oldest entries
        indexed_entries = list(enumerate(l1_cache.entries))
        indexed_entries.sort(key=lambda x: x[1].created_at)
        indices_to_remove = set(idx for idx, _ in indexed_entries[:evict_count])

        new_entries = []
        new_index = faiss.IndexFlatIP(384)
        new_embeddings = []

        for i, entry in enumerate(l1_cache.entries):
            if i not in indices_to_remove:
                emb = np.expand_dims(l1_cache.index.reconstruct(i), axis=0)
                new_embeddings.append(emb)
                entry.embedding_id = len(new_entries)
                new_entries.append(entry)

        if new_embeddings:
            new_index.add(np.vstack(new_embeddings))
        
        l1_cache.index = new_index
        l1_cache.entries = new_entries

    async def _evict_l2(self):
        # Evict lowest-similarity 10% relative to the current distribution
        evict_count = max(1, int(self.l2_max_entries * 0.1))
        
        # 1. Compute mean embedding
        all_embs = []
        for i in range(self.l2_index.ntotal):
            all_embs.append(self.l2_index.reconstruct(i))
        all_embs_np = np.array(all_embs)
        
        mean_emb = np.mean(all_embs_np, axis=0, keepdims=True)
        # Normalize mean
        faiss.normalize_L2(mean_emb)
        
        # 2. Compute similarity of each to mean (inner product = cosine sim)
        sims = np.dot(all_embs_np, mean_emb.T).flatten()
        
        # 3. Find 10% farthest (lowest similarity)
        indices_by_sim = np.argsort(sims) # ascending, so lowest first
        indices_to_remove = set(indices_by_sim[:evict_count])

        new_entries = []
        new_index = faiss.IndexFlatIP(384)
        new_embeddings = []

        for i, entry in enumerate(self.l2_entries):
            if i not in indices_to_remove:
                emb = np.expand_dims(all_embs_np[i], axis=0)
                new_embeddings.append(emb)
                entry.embedding_id = len(new_entries)
                new_entries.append(entry)

        if new_embeddings:
            new_index.add(np.vstack(new_embeddings))
        
        self.l2_index = new_index
        self.l2_entries = new_entries

    async def evict_expired(self) -> int:
        async with self.l2_lock:
            now = datetime.utcnow()
            indices_to_remove = set()
            for i, entry in enumerate(self.l2_entries):
                if (now - entry.created_at).days >= self.l2_ttl_days:
                    indices_to_remove.add(i)

            if not indices_to_remove:
                return 0

            new_entries = []
            new_index = faiss.IndexFlatIP(384)
            new_embeddings = []

            for i, entry in enumerate(self.l2_entries):
                if i not in indices_to_remove:
                    emb = np.expand_dims(self.l2_index.reconstruct(i), axis=0)
                    new_embeddings.append(emb)
                    entry.embedding_id = len(new_entries)
                    new_entries.append(entry)

            if new_embeddings:
                new_index.add(np.vstack(new_embeddings))
            
            self.l2_index = new_index
            self.l2_entries = new_entries

            return len(indices_to_remove)

    def remove_session_l1(self, session_id: str):
        if session_id in self.l1_caches:
            del self.l1_caches[session_id]

    async def persist_l2(self, db_path: str) -> None:
        async with self.l2_lock:
            if self.l2_index.ntotal == 0:
                return
            
            # Serialize FAISS index
            with tempfile.NamedTemporaryFile(delete=False) as tmp:
                faiss.write_index(self.l2_index, tmp.name)
                tmp.seek(0)
                index_bytes = tmp.read()
            os.unlink(tmp.name)

            # Serialize entries
            entries_data = [e.model_dump() for e in self.l2_entries]
            
            # Save to SQLite
            async with aiosqlite.connect(db_path) as db:
                await db.execute('''
                    CREATE TABLE IF NOT EXISTS cache_l2 (
                        id INTEGER PRIMARY KEY CHECK (id = 1),
                        index_data BLOB,
                        entries_json TEXT
                    )
                ''')
                await db.execute('''
                    INSERT OR REPLACE INTO cache_l2 (id, index_data, entries_json) 
                    VALUES (1, ?, ?)
                ''', (index_bytes, json.dumps(entries_data, default=str)))
                await db.commit()

    async def load_l2(self, db_path: str) -> None:
        if not os.path.exists(db_path):
            return

        async with aiosqlite.connect(db_path) as db:
            try:
                async with db.execute('SELECT index_data, entries_json FROM cache_l2 WHERE id = 1') as cursor:
                    row = await cursor.fetchone()
                    if not row:
                        return
                    index_bytes, entries_json = row
            except aiosqlite.OperationalError:
                # Table doesn't exist
                return

        # Deserialize
        try:
            with tempfile.NamedTemporaryFile(delete=False) as tmp:
                tmp.write(index_bytes)
                tmp.flush()
                self.l2_index = faiss.read_index(tmp.name)
            os.unlink(tmp.name)

            raw_entries = json.loads(entries_json)
            self.l2_entries = []
            for item in raw_entries:
                if 'created_at' in item and isinstance(item['created_at'], str):
                    item['created_at'] = datetime.fromisoformat(item['created_at'])
                self.l2_entries.append(CacheEntry(**item))
            
            logger.info(f"Loaded L2 cache with {len(self.l2_entries)} entries.")
        except Exception as e:
            logger.error(f"Failed to deserialize L2 cache: {e}")
            self.degraded = True
