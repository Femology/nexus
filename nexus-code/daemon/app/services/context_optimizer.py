import asyncio
import numpy as np
import logging
import re
from typing import Dict, Any, Optional

from ..models.context import FilteredContextBundle
from .embedding import EmbeddingService

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

    async def optimize(self, context_bundle: dict, query_text: str, query_embedding: np.ndarray) -> FilteredContextBundle:
        query_words = extract_significant_words(query_text)
        query_lower = query_text.lower()
        
        filtered = FilteredContextBundle()

        # 1. active_file: ALWAYS included
        active_file = context_bundle.get("active_file")
        if active_file and "content" in active_file:
            content = active_file["content"]
            lines = content.split("\n")
            total_lines = len(lines)
            
            if total_lines > 300:
                # Window logic
                cursor_line = active_file.get("cursor_position", {}).get("line", 0)
                window_start = max(0, cursor_line - self.window_before)
                window_end = min(total_lines, cursor_line + self.window_after)
                
                # Check if selection falls outside
                selection = context_bundle.get("selection")
                sel_start, sel_end = -1, -1
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
                    
                active_file["content"] = extracted_content
            
            filtered.active_file = active_file

        # 2. selection: ALWAYS included
        filtered.selection = context_bundle.get("selection")

        # 3. open_tabs
        open_tabs = context_bundle.get("open_tabs", [])
        if open_tabs:
            scored_tabs = []
            for tab in open_tabs:
                text = tab.get("content", "")
                score = await self._compute_score(text, query_words, query_embedding)
                if score >= self.tab_relevance_min:
                    scored_tabs.append((score, tab))
                    
            scored_tabs.sort(key=lambda x: x[0], reverse=True)
            
            # Estimate tokens roughly (chars / 4)
            total_tokens = 0
            for score, tab in scored_tabs:
                est_tokens = len(tab.get("content", "")) / 4
                if total_tokens + est_tokens <= 4000:
                    filtered.open_tabs.append(tab)
                    total_tokens += est_tokens
                else:
                    break

        # 4. workspace_structure
        ws = context_bundle.get("workspace_structure")
        if ws:
            ws_words = extract_significant_words(ws)
            overlap = len(query_words & ws_words) / len(query_words) if query_words else 0
            has_nav = any(kw in query_lower for kw in ["find", "where is", "which file", "locate", "structure", "directory", "folder"])
            if overlap > 0.20 or has_nav:
                filtered.workspace_structure = ws

        # 5. git_diff
        diff = context_bundle.get("git_diff")
        if diff:
            score = await self._compute_score(diff, query_words, query_embedding)
            has_diff = any(kw in query_lower for kw in ["changed", "diff", "modified", "broke", "refactor", "undo", "commit", "staged", "unstaged"])
            if score > 0.40 or has_diff:
                filtered.git_diff = diff

        # 6. diagnostics
        diag = context_bundle.get("diagnostics")
        if diag:
            score = await self._compute_score(diag, query_words, query_embedding)
            has_err = any(kw in query_lower for kw in ["error", "fix", "broken", "failing", "crash", "exception", "undefined", "null", "warning", "lint", "type error"])
            if score > 0.35 or has_err:
                filtered.diagnostics = diag

        # 7. terminal_snapshot
        term = context_bundle.get("terminal_snapshot")
        if term:
            score = await self._compute_score(term, query_words, query_embedding)
            has_term = any(kw in query_lower for kw in ["run", "output", "command", "terminal", "failed", "exit code", "logs", "npm", "pip", "cargo"])
            if score > 0.40 or has_term:
                filtered.terminal_snapshot = term

        return filtered
