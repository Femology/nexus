import asyncio
import numpy as np
import logging

logger = logging.getLogger(__name__)

class EmbeddingService:
    _instance = None

    def __init__(self):
        if EmbeddingService._instance is not None:
            raise Exception("EmbeddingService is a singleton. Use get_instance().")
        self.model = None

    @classmethod
    def get_instance(cls):
        if cls._instance is None:
            cls._instance = EmbeddingService()
        return cls._instance

    def load_model(self):
        if self.model is None:
            logger.info("Loading sentence-transformers model...")
            from sentence_transformers import SentenceTransformer
            self.model = SentenceTransformer("all-MiniLM-L6-v2")
            logger.info("SentenceTransformer model loaded successfully.")

    async def embed(self, text: str) -> np.ndarray:
        if self.model is None:
            raise RuntimeError("Embedding model is not loaded.")
        
        # Run synchronous embedding in an executor to avoid blocking the async event loop
        loop = asyncio.get_running_loop()
        embedding = await loop.run_in_executor(None, self._embed_sync, text)
        return embedding

    async def embed_batch(self, texts: list[str]) -> np.ndarray:
        if self.model is None:
            raise RuntimeError("Embedding model is not loaded.")
            
        loop = asyncio.get_running_loop()
        embeddings = await loop.run_in_executor(None, self._embed_batch_sync, texts)
        return embeddings

    def _embed_sync(self, text: str) -> np.ndarray:
        # returns normalized 384-dimensional float32 vector
        vec = self.model.encode(text, normalize_embeddings=True)
        return np.array(vec, dtype=np.float32)

    def _embed_batch_sync(self, texts: list[str]) -> np.ndarray:
        vecs = self.model.encode(texts, normalize_embeddings=True)
        return np.array(vecs, dtype=np.float32)

    @staticmethod
    def build_composite_key(user_message: str, language_id: str, has_selection: bool) -> str:
        sel_tag = "[SEL]" if has_selection else "[NOSEL]"
        return f"{user_message} [LANG:{language_id}] {sel_tag}"

# Provide a module-level instance helper
embedding_service = EmbeddingService.get_instance()
