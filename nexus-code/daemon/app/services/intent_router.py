import re
import asyncio
import numpy as np
from enum import IntEnum
import logging
from typing import Optional, Dict, List, Tuple
from app.services.embedding import embedding_service

logger = logging.getLogger(__name__)

class IntentTier(IntEnum):
    # Ordered by the "amount" of context required. Escalating goes to a higher value.
    LOCAL_EDIT = 1
    EXPLAIN = 2
    DEBUG_LOOP = 3
    REPO_QUERY = 4

# Exemplars for the embedding fallback
# These map typical user phrases to their specific intent tier.
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
        async with self._init_lock:
            if self._initialized:
                return

            # Ensure embedding model is loaded
            embedding_service.load_model()

            for tier, phrases in EXEMPLARS.items():
                # Embed batch
                embeddings = await embedding_service.embed_batch(phrases)
                self._exemplar_embeddings[tier] = embeddings

            self._initialized = True

    def _heuristic_classify(self, user_message: str, has_selection: bool) -> Optional[IntentTier]:
        msg_lower = user_message.lower()

        # Terminal/Test debugging heuristics
        if any(kw in msg_lower for kw in ["test failure", "stack trace", "terminal says", "build failing", "exception"]):
            return IntentTier.DEBUG_LOOP

        # Repo query heuristics
        if re.search(r"where is|find all usages|how is .* handled in this project|explain the architecture", msg_lower):
            return IntentTier.REPO_QUERY

        # Explain heuristics
        if re.search(r"what does this .* do\??|explain this|how does this .* work\??", msg_lower):
            return IntentTier.EXPLAIN

        # Local edit heuristics (often implicit with selection)
        if has_selection and re.search(r"refactor|fix|change|rewrite|add", msg_lower):
            return IntentTier.LOCAL_EDIT

        return None

    async def classify(self, user_message: str, has_selection: bool) -> IntentTier:
        """
        Classify the user's intent based on the prompt and UI context.
        """
        # 1. Fast path heuristic
        heuristic_tier = self._heuristic_classify(user_message, has_selection)
        if heuristic_tier is not None:
            logger.info(f"Intent classified by heuristic: {heuristic_tier.name}")
            return heuristic_tier

        # 2. Embedding fallback
        if not self._initialized:
            await self._initialize_exemplars()

        query_emb = await embedding_service.embed(user_message)
        # Ensure shape is (1, d)
        if len(query_emb.shape) == 1:
            query_emb = np.expand_dims(query_emb, axis=0)

        # Compute cosine similarity against all exemplars
        best_tier = None
        best_sim = -1.0
        tier_scores = {}

        for tier, embeddings in self._exemplar_embeddings.items():
            # embeddings shape is (num_exemplars, d)
            # cosine similarity = dot product since normalized
            sims = np.dot(embeddings, query_emb.T).flatten()
            max_sim = np.max(sims)
            tier_scores[tier] = max_sim

            if max_sim > best_sim:
                best_sim = max_sim
                best_tier = tier

        # Confidence margin check
        # If the highest score is very close to the second highest, or below a threshold, it's ambiguous.
        sorted_tiers = sorted(tier_scores.items(), key=lambda x: x[1], reverse=True)
        top_tier, top_score = sorted_tiers[0]
        second_tier, second_score = sorted_tiers[1]

        CONFIDENCE_THRESHOLD = 0.4
        MARGIN_THRESHOLD = 0.05

        if top_score < CONFIDENCE_THRESHOLD or (top_score - second_score) < MARGIN_THRESHOLD:
            # Ambiguous: Default upward (take the max enum value between top two, or just default to REPO_QUERY)
            # The prompt says: "ambiguous queries must default upward, not downward" and "escalation to the tier that uses more context, never less"
            escalated_tier = max(top_tier, second_tier)
            if top_score < CONFIDENCE_THRESHOLD:
                # Too little confidence overall, escalate to REPO_QUERY to be perfectly safe
                escalated_tier = IntentTier.REPO_QUERY
            
            logger.info(f"Intent ambiguous (scores: {top_tier.name}={top_score:.3f}, {second_tier.name}={second_score:.3f}). Escalating to {escalated_tier.name}.")
            return escalated_tier

        logger.info(f"Intent classified by embeddings: {top_tier.name} (score={top_score:.3f})")
        return top_tier

# Module-level instance
intent_router = IntentRouter()
