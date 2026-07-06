import re
import asyncio
import logging
from enum import IntEnum
from typing import Optional

logger = logging.getLogger(__name__)

# Optional: numpy + embedding support (requires sentence-transformers + torch)
try:
    import numpy as np
    from app.services.embedding import embedding_service
    _EMBEDDINGS_AVAILABLE = True
except ImportError:
    _EMBEDDINGS_AVAILABLE = False
    logger.warning("sentence-transformers/numpy not installed. Intent router running in heuristic-only mode.")


class IntentTier(IntEnum):
    # Ordered by the "amount" of context required. Escalating goes to a higher value.
    LOCAL_EDIT = 1
    EXPLAIN = 2
    DEBUG_LOOP = 3
    REPO_QUERY = 4


# Exemplars for the embedding fallback
EXEMPLARS = {
    IntentTier.LOCAL_EDIT: [
        "change the color of the button to red",
        "refactor this function to use list comprehensions",
        "fix the typo here",
        "add a docstring to this method",
        "rewrite this block to be async"
    ],
    IntentTier.EXPLAIN: [
        "what does this code do?",
        "how does this algorithm work?",
        "explain this file to me",
        "what is the purpose of this variable?",
        "walk me through this logic"
    ],
    IntentTier.DEBUG_LOOP: [
        "fix the test failure",
        "why is the build failing?",
        "the terminal says error: module not found",
        "help me debug this stack trace",
        "fix the crash on line 50"
    ],
    IntentTier.REPO_QUERY: [
        "where is the user model defined?",
        "how is authentication handled in this project?",
        "find all usages of this symbol",
        "where do we connect to the database?",
        "explain the architecture of this repo"
    ]
}


class IntentRouter:
    def __init__(self):
        self._exemplar_embeddings = {}
        self._initialized = False
        self._init_lock = asyncio.Lock()

    async def _initialize_exemplars(self):
        if not _EMBEDDINGS_AVAILABLE:
            return
        async with self._init_lock:
            if self._initialized:
                return
            try:
                embedding_service.load_model()
                for tier, phrases in EXEMPLARS.items():
                    embeddings = await embedding_service.embed_batch(phrases)
                    self._exemplar_embeddings[tier] = embeddings
                self._initialized = True
                logger.info("Intent router: embedding exemplars initialized.")
            except Exception as e:
                logger.warning(f"Intent router: failed to initialize embeddings: {e}. Falling back to heuristics.")

    def _heuristic_classify(self, user_message: str, has_selection: bool) -> Optional[IntentTier]:
        msg_lower = user_message.lower()

        # Terminal/Test debugging
        if any(kw in msg_lower for kw in ["test failure", "stack trace", "terminal says", "build failing", "exception", "traceback"]):
            return IntentTier.DEBUG_LOOP

        # Repo query
        if re.search(r"where is|find all usages|how is .* handled in this project|explain the architecture|where do we|which file", msg_lower):
            return IntentTier.REPO_QUERY

        # Explain
        if re.search(r"what does this .* do\??|explain this|how does this .* work\??|what is this", msg_lower):
            return IntentTier.EXPLAIN

        # Local edit (often implicit with selection)
        if has_selection and re.search(r"refactor|fix|change|rewrite|add|rename|move|extract|simplify", msg_lower):
            return IntentTier.LOCAL_EDIT

        return None

    async def classify(self, user_message: str, has_selection: bool) -> IntentTier:
        """
        Classify the user's intent. Uses heuristics first (always available),
        then falls back to embedding similarity if torch/sentence-transformers
        are installed.
        """
        # 1. Fast heuristic path
        heuristic_tier = self._heuristic_classify(user_message, has_selection)
        if heuristic_tier is not None:
            logger.info(f"Intent classified by heuristic: {heuristic_tier.name}")
            return heuristic_tier

        # 2. Embedding fallback (only if available)
        if _EMBEDDINGS_AVAILABLE:
            if not self._initialized:
                await self._initialize_exemplars()

            if self._initialized:
                try:
                    query_emb = await embedding_service.embed(user_message)
                    if len(query_emb.shape) == 1:
                        query_emb = np.expand_dims(query_emb, axis=0)

                    best_tier = IntentTier.REPO_QUERY
                    best_sim = -1.0
                    tier_scores = {}

                    for tier, embeddings in self._exemplar_embeddings.items():
                        sims = np.dot(embeddings, query_emb.T).flatten()
                        max_sim = float(np.max(sims))
                        tier_scores[tier] = max_sim
                        if max_sim > best_sim:
                            best_sim = max_sim
                            best_tier = tier

                    sorted_tiers = sorted(tier_scores.items(), key=lambda x: x[1], reverse=True)
                    top_tier, top_score = sorted_tiers[0]
                    second_tier, second_score = sorted_tiers[1]

                    CONFIDENCE_THRESHOLD = 0.4
                    MARGIN_THRESHOLD = 0.05

                    if top_score < CONFIDENCE_THRESHOLD or (top_score - second_score) < MARGIN_THRESHOLD:
                        escalated = max(top_tier, second_tier)
                        if top_score < CONFIDENCE_THRESHOLD:
                            escalated = IntentTier.REPO_QUERY
                        logger.info(f"Intent ambiguous — escalating to {escalated.name}")
                        return escalated

                    logger.info(f"Intent classified by embeddings: {top_tier.name} (score={top_score:.3f})")
                    return top_tier
                except Exception as e:
                    logger.warning(f"Embedding classification failed: {e}")

        # 3. Safe default: EXPLAIN (middle-ground, safe)
        logger.info("Intent defaulting to EXPLAIN (heuristic-only mode)")
        return IntentTier.EXPLAIN


# Module-level instance
intent_router = IntentRouter()
