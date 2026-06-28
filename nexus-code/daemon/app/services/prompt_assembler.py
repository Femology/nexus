import os
import yaml
import tiktoken
import logging
from typing import List, Dict, Any, Optional

from ..models.context import FilteredContextBundle, TaggedPrompt

logger = logging.getLogger(__name__)

class PromptAssembler:
    def __init__(self, config=None):
        self.system_prompt = "You are an elite AI assistant."
        self.tool_definitions = []
        self.encoding = tiktoken.get_encoding("cl100k_base")

        # Load from config if available
        base_dir = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
        config_dir = os.path.join(base_dir, "config")
        
        sp_path = os.path.join(config_dir, "system_prompt.yaml")
        if os.path.exists(sp_path):
            try:
                with open(sp_path, "r", encoding="utf-8") as f:
                    data = yaml.safe_load(f)
                    if isinstance(data, dict) and "system_prompt" in data:
                        self.system_prompt = data["system_prompt"]
            except Exception as e:
                logger.error(f"Failed to load system prompt: {e}")

        tools_path = os.path.join(config_dir, "tools.yaml")
        if os.path.exists(tools_path):
            try:
                with open(tools_path, "r", encoding="utf-8") as f:
                    data = yaml.safe_load(f)
                    if isinstance(data, dict) and "tools" in data:
                        self.tool_definitions = data["tools"]
            except Exception as e:
                logger.error(f"Failed to load tools: {e}")

    def _count_tokens(self, text: str) -> int:
        return len(self.encoding.encode(text))

    def _count_message_tokens(self, messages: List[Dict[str, Any]]) -> int:
        # Approximate token count for chat format
        num_tokens = 0
        for msg in messages:
            num_tokens += 4  # message start/end
            for key, value in msg.items():
                if isinstance(value, str):
                    num_tokens += self._count_tokens(value)
        num_tokens += 2  # assistant reply start
        return num_tokens

    async def assemble(
        self,
        memory_context: str,
        conversation_summary: str,
        filtered_context: FilteredContextBundle,
        user_message: str,
        tool_history: Optional[List[Dict[str, Any]]] = None
    ) -> TaggedPrompt:
        
        messages = []
        segment_tags = {}
        
        # Message 1 (system)
        sys_msg = {"role": "system", "content": self.system_prompt}
        messages.append(sys_msg)
        segment_tags[0] = "SYSTEM"

        # Message 2 (user): Context injection
        ctx_lines = []
        if memory_context:
            ctx_lines.append(f"## Retrieved Memory\n{memory_context}")
        
        if conversation_summary:
            ctx_lines.append(f"\n## Conversation Summary\n{conversation_summary}")
            
        ctx_lines.append("\n## Current Context")
        
        if filtered_context.active_file:
            af = filtered_context.active_file
            path = af.get("file_path", "unknown")
            lang = af.get("language_id", "text")
            content = af.get("content", "")
            ctx_lines.append(f"### Active File: {path} ({lang})\n```{lang}\n{content}\n```")

        if filtered_context.selection:
            sel = filtered_context.selection
            text = sel.get("text", "")
            lang = filtered_context.active_file.get("language_id", "text") if filtered_context.active_file else "text"
            ctx_lines.append(f"### Selection\n```{lang}\n{text}\n```")

        for tab in filtered_context.open_tabs:
            path = tab.get("file_path", "unknown")
            lang = tab.get("language_id", "text")
            content = tab.get("content", "")
            ctx_lines.append(f"### Open Tab: {path}\n```{lang}\n{content}\n```")

        if filtered_context.workspace_structure:
            ctx_lines.append(f"### Workspace Structure\n{filtered_context.workspace_structure}\n")

        if filtered_context.git_diff:
            ctx_lines.append(f"### Git Diff\n```diff\n{filtered_context.git_diff}\n```")

        if filtered_context.diagnostics:
            ctx_lines.append(f"### Diagnostics\n{filtered_context.diagnostics}")

        if filtered_context.terminal_snapshot:
            ctx_lines.append(f"### Terminal Output\n```\n{filtered_context.terminal_snapshot}\n```")

        ctx_msg = {"role": "user", "content": "\n".join(ctx_lines)}
        messages.append(ctx_msg)
        segment_tags[1] = "CONTEXT_INJECTION" # Using one tag for the injection message

        # Message 3 (assistant)
        ack_msg = {"role": "assistant", "content": "I have access to the context provided. How can I help?"}
        messages.append(ack_msg)
        segment_tags[2] = "SUMMARY"

        # Messages 4+: Tool history
        curr_idx = 3
        if tool_history:
            for th_msg in tool_history:
                messages.append(th_msg)
                segment_tags[curr_idx] = "TOOL_HISTORY"
                curr_idx += 1

        # Final message (user)
        final_msg = {"role": "user", "content": user_message}
        messages.append(final_msg)
        segment_tags[curr_idx] = "USER_QUERY"

        pre_tokens = self._count_message_tokens(messages)
        
        return TaggedPrompt(
            messages=messages,
            segment_tags=segment_tags,
            pre_token_count=pre_tokens
        )
