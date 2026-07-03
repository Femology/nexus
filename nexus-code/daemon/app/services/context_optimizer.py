import asyncio
import numpy as np
import logging
import re
from typing import Dict, Any, Optional

from ..models.context import FilteredContextBundle
from .embedding import EmbeddingService
from .intent_router import IntentTier

logger = logging.getLogger(__name__)

def extract_significant_words(text: str) -> set:
    if not text:
        return set()
    # Basic tokenization, removing short words and common stop words
    words = re.findall(r'\b[a-zA-Z_]\w+\b', text.lower())
    stop_words = {"the", "a", "an", "is", "in", "it", "to", "and", "of", "for", "with", "on", "as", "by", "at", "from", "this", "that"}
    return {w for w in words if len(w) > 2 and w not in stop_words}

class ContextOptimizer:
    def __init__(self, embedding_service: EmbeddingService, config=None):
        self.embedding_service = embedding_service
        self.tab_relevance_min = getattr(config.context_optimizer, 'tab_relevance_min', 0.30) if config and hasattr(config, 'context_optimizer') else 0.30
        self.window_before = getattr(config.context_optimizer, 'active_file_window_lines_before', 100) if config and hasattr(config, 'context_optimizer') else 100
        self.window_after = getattr(config.context_optimizer, 'active_file_window_lines_after', 150) if config and hasattr(config, 'context_optimizer') else 150

    async def _compute_score(self, text: str, query_words: set, query_embedding: np.ndarray) -> float:
        if not text:
            return 0.0
        piece_words = extract_significant_words(text)
        if not query_words:
            keyword_overlap = 0.0
        else:
            keyword_overlap = len(query_words & piece_words) / len(query_words)
            
        emb = await self.embedding_service.embed(text)
        semantic_sim = float(np.dot(query_embedding[0] if len(query_embedding.shape) == 2 else query_embedding, emb))
        
        return 0.60 * semantic_sim + 0.40 * keyword_overlap

    async def optimize(self, context_bundle: dict, intent_tier: IntentTier) -> FilteredContextBundle:
        filtered = FilteredContextBundle()

        active_file = context_bundle.get("active_file")
        selection = context_bundle.get("selection")
        open_tabs = context_bundle.get("open_tabs", [])
        ws = context_bundle.get("workspace_structure")
        term = context_bundle.get("terminal_snapshot")
        diag = context_bundle.get("diagnostics")
        memory_graph = context_bundle.get("memory_graph")

        if intent_tier == IntentTier.LOCAL_EDIT:
            filtered.active_file = self._window_active_file(active_file, selection)
            filtered.selection = selection
            return filtered

        elif intent_tier == IntentTier.EXPLAIN:
            filtered.active_file = self._window_active_file(active_file, selection)
            filtered.selection = selection
            filtered.memory_graph = memory_graph
            return filtered

        elif intent_tier == IntentTier.DEBUG_LOOP:
            filtered.active_file = self._window_active_file(active_file, selection)
            filtered.selection = selection
            filtered.terminal_snapshot = term
            filtered.diagnostics = diag
            filtered.memory_graph = memory_graph
            return filtered

        elif intent_tier == IntentTier.REPO_QUERY:
            filtered.workspace_structure = ws
            filtered.memory_graph = memory_graph
            if selection:
                filtered.active_file = self._window_active_file(active_file, selection)
                filtered.selection = selection
            if open_tabs:
                filtered.open_tabs = open_tabs[:2]
            return filtered

        return filtered

    def _window_active_file(self, active_file: Optional[dict], selection: Optional[dict]) -> Optional[dict]:
        if not active_file or "content" not in active_file:
            return active_file
        
        content = active_file["content"]
        lines = content.split("\n")
        total_lines = len(lines)
        
        if total_lines <= 300:
            return active_file
            
        cursor_line = active_file.get("cursor_position", {}).get("line", 0)
        window_start = max(0, cursor_line - self.window_before)
        window_end = min(total_lines, cursor_line + self.window_after)
        
        if selection:
            sel_start = selection.get("start", {}).get("line", -1)
            sel_end = selection.get("end", {}).get("line", -1)
            if sel_start != -1 and sel_end != -1:
                window_start = min(window_start, sel_start)
                window_end = max(window_end, sel_end)
                
        extracted_lines = lines[window_start:window_end]
        extracted_content = ""
        if window_start > 0:
            extracted_content += f"// ... [lines 1-{window_start} omitted] ...\n"
        extracted_content += "\n".join(extracted_lines)
        if window_end < total_lines:
            extracted_content += f"\n// ... [lines {window_end + 1}-{total_lines} omitted] ..."
            
        windowed_file = dict(active_file)
        windowed_file["content"] = extracted_content
        return windowed_file
