import asyncio
import logging

logger = logging.getLogger(__name__)

# Check if numpy is available
try:
    import numpy as np
    _NUMPY_AVAILABLE = True
except ImportError:
    _NUMPY_AVAILABLE = False

# Check if sentence-transformers is available
try:
    from sentence_transformers import SentenceTransformer # type: ignore
    _ST_AVAILABLE = True
except ImportError:
    _ST_AVAILABLE = False
    logger.info("sentence-transformers not installed — embedding service disabled (heuristic-only mode)")


class EmbeddingService:
    _instance = None

    def __init__(self):
        if EmbeddingService._instance is not None:
            raise Exception("EmbeddingService is a singleton. Use get_instance().")
        self.model = None
        self.available = _ST_AVAILABLE and _NUMPY_AVAILABLE

    @classmethod
    def get_instance(cls):
        if cls._instance is None:
            cls._instance = EmbeddingService()
        return cls._instance

    def load_model(self):
        if not self.available:
            logger.info("Embedding service skipped — sentence-transformers not installed.")
            return
        if self.model is None:
            logger.info("Loading sentence-transformers model...")
            self.model = SentenceTransformer("all-MiniLM-L6-v2")
            logger.info("SentenceTransformer model loaded successfully.")

    async def embed(self, text: str):
        if not self.available or self.model is None:
            raise RuntimeError("Embedding model is not available.")
        loop = asyncio.get_running_loop()
        embedding = await loop.run_in_executor(None, self._embed_sync, text)
        return embedding

    async def embed_batch(self, texts: list[str]):
        if not self.available or self.model is None:
            raise RuntimeError("Embedding model is not available.")
        loop = asyncio.get_running_loop()
        embeddings = await loop.run_in_executor(None, self._embed_batch_sync, texts)
        return embeddings

    def _embed_sync(self, text: str):
        vec = self.model.encode(text, normalize_embeddings=True)
        return np.array(vec, dtype=np.float32)

    def _embed_batch_sync(self, texts: list[str]):
        vecs = self.model.encode(texts, normalize_embeddings=True)
        return np.array(vecs, dtype=np.float32)

    @staticmethod
    def build_composite_key(user_message: str, language_id: str, has_selection: bool) -> str:
        sel_tag = "[SEL]" if has_selection else "[NOSEL]"
        return f"{user_message} [LANG:{language_id}] {sel_tag}"


# Module-level instance
embedding_service = EmbeddingService.get_instance()
