import asyncio
import faiss
import networkx as nx
import numpy as np
import logging
import json
import uuid
import re
import math
import aiosqlite
from datetime import datetime
from typing import Dict, List, Optional, Any, Set, Tuple

from ..models.context import MemorySnippetBundle, NodeSummary
from .embedding import EmbeddingService

logger = logging.getLogger(__name__)

class MemoryGraph:
    def __init__(self, embedding_service: EmbeddingService, config=None):
        self.embedding_service = embedding_service
        self.graph = nx.DiGraph()
        self.node_index = faiss.IndexFlatIP(384)
        self.node_id_map: List[str] = []
        self.lock = asyncio.Lock()
        
        self.max_nodes = getattr(config.memory, 'max_nodes_per_partition', 500) if config and hasattr(config, 'memory') else 500
        self.retrieval_seed_k = getattr(config.memory, 'retrieval_seed_k', 15) if config and hasattr(config, 'memory') else 15
        self.retrieval_top_n = getattr(config.memory, 'retrieval_top_n', 8) if config and hasattr(config, 'memory') else 8
        self.traversal_depth = getattr(config.memory, 'graph_traversal_depth', 2) if config and hasattr(config, 'memory') else 2

    async def retrieve(self, query_text: str, language_id: str, session_id: str, request_intent: Optional[str] = None) -> MemorySnippetBundle:
        async with self.lock:
            if self.node_index.ntotal == 0:
                return MemorySnippetBundle(text="No prior memory available.", node_count=0, nodes=[])

        # Step 1: Query Encoding
        query_encoded_text = f"{query_text} [LANG:{language_id}]"
        query_embedding = await self.embedding_service.embed(query_encoded_text)
        
        if len(query_embedding.shape) == 1:
            query_embedding = np.expand_dims(query_embedding, axis=0)

        # Step 2: KNN Seed Search
        async with self.lock:
            k = min(self.retrieval_seed_k, self.node_index.ntotal)
            D, I = self.node_index.search(query_embedding, k)
            
            seeds = []
            for i in range(k):
                idx = I[0][i]
                if idx != -1:
                    seeds.append((self.node_id_map[idx], D[0][i]))

            if not seeds:
                return MemorySnippetBundle(text="No relevant memory found.", node_count=0, nodes=[])

            # Edge type filtering based on intent
            intent_lower = request_intent.lower() if request_intent else ""
            allowed_edges = set()
            if any(kw in intent_lower for kw in ["code", "generate", "write", "function", "class"]):
                allowed_edges.update(["imports", "calls", "references"])
            elif any(kw in intent_lower for kw in ["debug", "error", "fix", "fail", "broken"]):
                allowed_edges.update(["solves", "error_pattern", "references"])
            else:
                allowed_edges.update(["imports", "calls", "solves", "contradicts", "references", "prefers", "error_pattern"]) # all

            # Step 3: Graph Traversal (BFS)
            collected_nodes: Set[str] = set()
            for seed_id, _ in seeds:
                collected_nodes.add(seed_id)
                
                # BFS to traversal_depth
                queue = [(seed_id, 0)]
                visited = {seed_id}
                
                while queue:
                    curr_node, depth = queue.pop(0)
                    if depth >= self.traversal_depth:
                        continue
                        
                    for neighbor, edge_data in self.graph[curr_node].items():
                        edge_type = edge_data.get("edge_type", "references")
                        if edge_type in allowed_edges and neighbor not in visited:
                            visited.add(neighbor)
                            collected_nodes.add(neighbor)
                            queue.append((neighbor, depth + 1))

            # Step 4: Relevance Ranking
            scored_nodes = []
            now = datetime.utcnow()
            for node_id in collected_nodes:
                node_data = self.graph.nodes[node_id]
                
                node_emb = node_data["content_embedding"]
                cos_sim = float(np.dot(query_embedding[0], node_emb))
                
                # Recency score (1.0 if accessed today, decays over 7 days linearly)
                days_old = (now - node_data["last_accessed"]).days
                recency_score = max(0.0, 1.0 - (days_old / 7.0))
                
                # Frequency score (log normalized)
                access_count = node_data["access_count"]
                freq_score = min(1.0, math.log1p(access_count) / math.log1p(100)) # capped at 100 accesses
                
                confidence = node_data.get("confidence", 0.8)
                
                combined_score = (0.40 * cos_sim) + (0.30 * recency_score) + (0.20 * freq_score) + (0.10 * confidence)
                
                scored_nodes.append((node_id, combined_score))

            # Step 5: Top-N Selection
            scored_nodes.sort(key=lambda x: x[1], reverse=True)
            top_nodes = scored_nodes[:self.retrieval_top_n]
            
            # Update access
            node_summaries = []
            for node_id, score in top_nodes:
                node_data = self.graph.nodes[node_id]
                node_data["last_accessed"] = now
                node_data["access_count"] += 1
                
                n_type = node_data["node_type"]
                text = node_data["content_text"]
                # Formatted snippet
                if n_type == "code_entity":
                    name = node_data.get("name", "Unknown")
                    file_path = node_data.get("file_path", "unknown")
                    desc = node_data.get("description", text)
                    snippet = f"[{n_type}] {name} in {file_path}: {desc}"
                elif n_type in ["solution", "user_preference", "project_fact", "error_pattern"]:
                    snippet = f"[{n_type}] {text}"
                else:
                    snippet = f"[{n_type}] {text}"
                    
                node_summaries.append(NodeSummary(id=node_id, type=n_type, text=snippet, score=score))

            # Step 6: Serialization
            combined_text = "\n".join(ns.text for ns in node_summaries)
            return MemorySnippetBundle(text=combined_text, node_count=len(node_summaries), nodes=node_summaries)

    async def write_memory(self, user_message: str, response_text: str, context_bundle: dict, session_id: str) -> None:
        nodes_to_add = []
        user_lower = user_message.lower()
        resp_lower = response_text.lower()
        now = datetime.utcnow()

        # Simple heuristic extractors
        # User preferences
        if any(kw in user_lower for kw in ["i prefer", "always use", "don't use", "i like", "please use"]):
            pref_match = re.search(r"(?i)(i prefer|always use|don't use|i like|please use) (.*?)(?:\.|$)", user_message)
            if pref_match:
                nodes_to_add.append({
                    "node_type": "user_preference",
                    "content_text": f"User prefers: {pref_match.group(2).strip()}",
                    "confidence": 0.9,
                    "preference_text": pref_match.group(2).strip()
                })

        # Solutions
        if any(kw in resp_lower for kw in ["fix", "solve", "resolve", "implement", "create"]) and "?" not in response_text[-10:]:
            # Assuming it's a solution
            nodes_to_add.append({
                "node_type": "solution",
                "content_text": f"Solution implemented: {user_message[:50]}...",
                "confidence": 0.8,
                "problem_summary": user_message[:100],
                "solution_strategy": response_text[:200]
            })

        # Project facts
        if any(kw in resp_lower for kw in ["the api is under", "tests use", "the config is at"]):
            nodes_to_add.append({
                "node_type": "project_fact",
                "content_text": f"Project Fact: {response_text[:150]}",
                "confidence": 0.85,
                "fact_text": response_text[:150]
            })
            
        # Error patterns
        if any(kw in user_lower for kw in ["error", "exception", "failed", "crash"]) and "fix" in resp_lower:
            nodes_to_add.append({
                "node_type": "error_pattern",
                "content_text": f"Error resolved: {user_message[:50]}",
                "confidence": 0.85,
                "error_signature": user_message[:100],
                "resolution": response_text[:200]
            })

        if not nodes_to_add:
            return

        async with self.lock:
            for node_data in nodes_to_add:
                node_id = str(uuid.uuid4())
                emb = await self.embedding_service.embed(node_data["content_text"])
                if len(emb.shape) == 1:
                    emb = np.expand_dims(emb, axis=0)
                
                # Add to FAISS to find neighbors
                if self.node_index.ntotal > 0:
                    D, I = self.node_index.search(emb, 3)
                    neighbors = []
                    for i in range(len(I[0])):
                        if I[0][i] != -1 and D[0][i] > 0.85:
                            neighbors.append(self.node_id_map[I[0][i]])
                else:
                    neighbors = []

                # Add node
                self.graph.add_node(
                    node_id,
                    node_type=node_data["node_type"],
                    content_embedding=emb[0],
                    created_at=now,
                    last_accessed=now,
                    access_count=1,
                    session_id=session_id,
                    confidence=node_data["confidence"],
                    content_text=node_data["content_text"],
                    **{k: v for k, v in node_data.items() if k not in ["node_type", "confidence", "content_text"]}
                )
                self.node_index.add(emb)
                self.node_id_map.append(node_id)
                
                # Add edges
                for nbr in neighbors:
                    self.graph.add_edge(node_id, nbr, edge_type="references")
                    self.graph.add_edge(nbr, node_id, edge_type="references")

            # Eviction
            if self.graph.number_of_nodes() > self.max_nodes:
                # Evict lowest-scoring node not from this session
                evict_candidates = []
                for n, d in self.graph.nodes(data=True):
                    if d["session_id"] != session_id:
                        days_old = (now - d["last_accessed"]).days
                        recency = max(0.01, 1.0 - (days_old / 7.0))
                        score = d["access_count"] * recency
                        evict_candidates.append((n, score))
                
                if evict_candidates:
                    evict_candidates.sort(key=lambda x: x[1])
                    to_remove = evict_candidates[0][0]
                    
                    # Remove from graph
                    self.graph.remove_node(to_remove)
                    # Rebuild FAISS index
                    self._rebuild_index_locked()

    def _rebuild_index_locked(self):
        new_index = faiss.IndexFlatIP(384)
        new_map = []
        embs = []
        for n, d in self.graph.nodes(data=True):
            embs.append(np.expand_dims(d["content_embedding"], axis=0))
            new_map.append(n)
            
        if embs:
            new_index.add(np.vstack(embs))
        self.node_index = new_index
        self.node_id_map = new_map

    async def save_to_sqlite(self, db_path: str) -> None:
        async with self.lock:
            if self.graph.number_of_nodes() == 0:
                return

            nodes_data = []
            for n, d in self.graph.nodes(data=True):
                attrs = dict(d)
                emb = attrs.pop("content_embedding").tobytes()
                # convert datetimes
                attrs["created_at"] = attrs["created_at"].isoformat()
                attrs["last_accessed"] = attrs["last_accessed"].isoformat()
                node_type = attrs.pop("node_type")
                nodes_data.append((n, node_type, json.dumps(attrs), emb))

            edges_data = []
            for u, v, d in self.graph.edges(data=True):
                edge_type = d.get("edge_type", "references")
                edges_data.append((u, v, edge_type, json.dumps(d)))

            async with aiosqlite.connect(db_path) as db:
                await db.execute('''CREATE TABLE IF NOT EXISTS graph_nodes (
                                    id TEXT PRIMARY KEY, type TEXT, attrs TEXT, embedding BLOB)''')
                await db.execute('''CREATE TABLE IF NOT EXISTS graph_edges (
                                    source TEXT, target TEXT, type TEXT, attrs TEXT,
                                    PRIMARY KEY (source, target, type))''')
                
                await db.execute('DELETE FROM graph_nodes')
                await db.execute('DELETE FROM graph_edges')
                
                await db.executemany('INSERT INTO graph_nodes VALUES (?, ?, ?, ?)', nodes_data)
                await db.executemany('INSERT INTO graph_edges VALUES (?, ?, ?, ?)', edges_data)
                await db.commit()

    async def load_from_sqlite(self, db_path: str) -> None:
        async with self.lock:
            try:
                async with aiosqlite.connect(db_path) as db:
                    async with db.execute('SELECT id, type, attrs, embedding FROM graph_nodes') as cursor:
                        async for row in cursor:
                            n_id, n_type, attrs_json, emb_bytes = row
                            attrs = json.loads(attrs_json)
                            attrs["created_at"] = datetime.fromisoformat(attrs["created_at"])
                            attrs["last_accessed"] = datetime.fromisoformat(attrs["last_accessed"])
                            emb = np.frombuffer(emb_bytes, dtype=np.float32)
                            
                            self.graph.add_node(
                                n_id,
                                node_type=n_type,
                                content_embedding=emb,
                                **attrs
                            )
                    
                    async with db.execute('SELECT source, target, type, attrs FROM graph_edges') as cursor:
                        async for row in cursor:
                            src, tgt, e_type, attrs_json = row
                            attrs = json.loads(attrs_json)
                            self.graph.add_edge(src, tgt, edge_type=e_type, **attrs)
                
                self._rebuild_index_locked()
                logger.info(f"Loaded Memory Graph: {self.graph.number_of_nodes()} nodes.")
            except aiosqlite.OperationalError:
                # Tables don't exist yet
                pass
            except Exception as e:
                logger.error(f"Failed to load memory graph: {e}")
