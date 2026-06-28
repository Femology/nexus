import asyncio
import aiosqlite
import json
import logging
from datetime import datetime, timedelta
from typing import Dict, Optional

from ..models.context import SessionObject, ToolLoopState
from .cache import SemanticCache

logger = logging.getLogger(__name__)

class SessionManager:
    def __init__(self, semantic_cache: SemanticCache, config=None):
        self.sessions: Dict[str, SessionObject] = {}
        self.dirty_flags: Dict[str, bool] = {}
        self.lock = asyncio.Lock()
        self.semantic_cache = semantic_cache
        
        self.persist_interval = config.session.sqlite_flush_interval_seconds if config else 60
        self.evict_interval = config.session.eviction_check_interval_minutes * 60 if config else 3600
        self.session_ttl_hours = config.session.ttl_hours if config else 24

        self.db_path = "sessions.sqlite"

    async def get_or_create(self, session_id: str, model_alias: str) -> SessionObject:
        async with self.lock:
            if session_id in self.sessions:
                session = self.sessions[session_id]
                session.last_active = datetime.utcnow()
                session.model_alias = model_alias
                self.dirty_flags[session_id] = True
                return session
            else:
                session = SessionObject(
                    session_id=session_id,
                    model_alias=model_alias
                )
                self.sessions[session_id] = session
                self.dirty_flags[session_id] = True
                return session

    async def update_summary(self, session_id: str, new_summary: str) -> None:
        async with self.lock:
            if session_id in self.sessions:
                self.sessions[session_id].conversation_summary = new_summary
                self.sessions[session_id].last_active = datetime.utcnow()
                self.dirty_flags[session_id] = True

    async def increment_turn(self, session_id: str) -> None:
        async with self.lock:
            if session_id in self.sessions:
                self.sessions[session_id].turn_count += 1
                self.sessions[session_id].last_active = datetime.utcnow()
                self.dirty_flags[session_id] = True

    async def set_tool_loop(self, session_id: str, state: Optional[ToolLoopState]) -> None:
        async with self.lock:
            if session_id in self.sessions:
                self.sessions[session_id].active_tool_loop = state
                self.sessions[session_id].last_active = datetime.utcnow()
                self.dirty_flags[session_id] = True

    async def get_tool_loop(self, session_id: str) -> Optional[ToolLoopState]:
        async with self.lock:
            if session_id in self.sessions:
                return self.sessions[session_id].active_tool_loop
            return None

    async def load_from_sqlite(self, db_path: str) -> None:
        self.db_path = db_path
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute('''
                CREATE TABLE IF NOT EXISTS sessions (
                    session_id TEXT PRIMARY KEY,
                    data BLOB,
                    last_modified TIMESTAMP
                )
            ''')
            await db.commit()
            
            cutoff = datetime.utcnow() - timedelta(hours=self.session_ttl_hours)
            
            async with db.execute('SELECT session_id, data FROM sessions WHERE last_modified > ?', (cutoff.isoformat(),)) as cursor:
                async for row in cursor:
                    try:
                        session_id, data_str = row
                        data_dict = json.loads(data_str)
                        if 'created_at' in data_dict:
                            data_dict['created_at'] = datetime.fromisoformat(data_dict['created_at'])
                        if 'last_active' in data_dict:
                            data_dict['last_active'] = datetime.fromisoformat(data_dict['last_active'])
                        
                        session = SessionObject(**data_dict)
                        self.sessions[session_id] = session
                        self.dirty_flags[session_id] = False
                    except Exception as e:
                        logger.error(f"Failed to load session {row[0]}: {e}")
        
        logger.info(f"Loaded {len(self.sessions)} sessions from SQLite.")

    async def persist_to_sqlite_loop(self):
        while True:
            await asyncio.sleep(self.persist_interval)
            await self.persist_to_sqlite()

    async def persist_to_sqlite(self):
        to_save = []
        async with self.lock:
            for sid, dirty in list(self.dirty_flags.items()):
                if dirty and sid in self.sessions:
                    to_save.append(self.sessions[sid])
                    self.dirty_flags[sid] = False
        
        if not to_save:
            return

        try:
            async with aiosqlite.connect(self.db_path) as db:
                for session in to_save:
                    data_str = session.model_dump_json()
                    last_modified = session.last_active.isoformat()
                    await db.execute('''
                        INSERT OR REPLACE INTO sessions (session_id, data, last_modified)
                        VALUES (?, ?, ?)
                    ''', (session.session_id, data_str, last_modified))
                await db.commit()
        except Exception as e:
            logger.error(f"Failed to persist sessions: {e}")
            # Mark them dirty again so they are retried
            async with self.lock:
                for session in to_save:
                    self.dirty_flags[session.session_id] = True

    async def evict_expired_loop(self):
        while True:
            await asyncio.sleep(self.evict_interval)
            await self.evict_expired()

    async def evict_expired(self):
        cutoff = datetime.utcnow() - timedelta(hours=self.session_ttl_hours)
        to_evict = []
        
        async with self.lock:
            for sid, session in list(self.sessions.items()):
                if session.last_active < cutoff:
                    to_evict.append(sid)
            
            for sid in to_evict:
                del self.sessions[sid]
                if sid in self.dirty_flags:
                    del self.dirty_flags[sid]
        
        if to_evict:
            for sid in to_evict:
                self.semantic_cache.remove_session_l1(sid)
            
            try:
                async with aiosqlite.connect(self.db_path) as db:
                    for sid in to_evict:
                        await db.execute('DELETE FROM sessions WHERE session_id = ?', (sid,))
                    await db.commit()
            except Exception as e:
                logger.error(f"Failed to delete expired sessions from db: {e}")
            logger.info(f"Evicted {len(to_evict)} expired sessions.")
