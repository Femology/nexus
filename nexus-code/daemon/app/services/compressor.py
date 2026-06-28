import logging
import tiktoken
from typing import Dict, Any

from ..models.context import TaggedPrompt, CompressedPrompt

logger = logging.getLogger(__name__)

class PromptCompressor:
    def __init__(self, config=None):
        self.config = config
        self.encoding = tiktoken.get_encoding("cl100k_base")
        self.lingua = None
        self.degraded = False
        try:
            from llmlingua import PromptCompressor as LLMLinguaCompressor
            # Use a smaller model for faster CPU execution
            self.lingua = LLMLinguaCompressor(
                model_name="microsoft/llmlingua-2-xlm-roberta-large-meetingbank",
                use_llmlingua2=True,
                device_map="cpu"
            )
            logger.info("LLMLingua Compressor initialized successfully.")
        except Exception as e:
            logger.warning(f"Failed to load LLMLingua2, falling back to original: {e}")
            try:
                from llmlingua import PromptCompressor as LLMLinguaCompressor
                self.lingua = LLMLinguaCompressor(
                    model_name="albert-base-v2",
                    device_map="cpu"
                )
                logger.info("LLMLingua original initialized.")
            except Exception as e2:
                logger.error(f"Failed to initialize LLMLingua entirely: {e2}")
                self.degraded = True

        # Base rates
        cfg_comp = config.compression if config and hasattr(config, 'compression') else None
        self.base_rates = {
            "SYSTEM": getattr(cfg_comp, 'system_prompt_rate', 0.15) if cfg_comp else 0.15,
            "MEMORY": getattr(cfg_comp, 'memory_rate', 0.60) if cfg_comp else 0.60,
            "SUMMARY": getattr(cfg_comp, 'summary_rate', 0.35) if cfg_comp else 0.35,
            "CONTEXT_CODE": getattr(cfg_comp, 'code_context_rate', 0.40) if cfg_comp else 0.40,
            "CONTEXT_CODE_HEAVY": getattr(cfg_comp, 'code_context_heavy_rate', 0.60) if cfg_comp else 0.60,
            "CONTEXT_META": getattr(cfg_comp, 'meta_context_rate', 0.50) if cfg_comp else 0.50,
            "TOOL_HISTORY": 0.25,
            "USER_QUERY": 0.00
        }

    def _count_tokens(self, text: str) -> int:
        return len(self.encoding.encode(text))

    async def compress(self, tagged_prompt: TaggedPrompt, heavy_context: bool, model_context_window: int) -> CompressedPrompt:
        if self.degraded or not self.lingua:
            return CompressedPrompt(
                messages=tagged_prompt.messages,
                post_compression_tokens=tagged_prompt.pre_token_count,
                compression_ratio=0.0
            )

        try:
            # 1. Determine rates
            rates = self.base_rates.copy()
            if heavy_context:
                rates["CONTEXT_CODE"] = rates["CONTEXT_CODE_HEAVY"]

            # 2. Dynamic rate adjustment
            pre_count = tagged_prompt.pre_token_count
            if pre_count < model_context_window * 0.50:
                for k in rates:
                    if k != "USER_QUERY":
                        rates[k] *= 0.50
            elif pre_count > model_context_window * 0.85:
                # Need to scale up compression
                rates["CONTEXT_CODE"] = min(0.80, rates["CONTEXT_CODE"] * 1.5)
                rates["CONTEXT_META"] = min(0.80, rates["CONTEXT_META"] * 1.5)
                rates["MEMORY"] = min(0.80, rates["MEMORY"] * 1.2)

            # 3. Apply compression
            compressed_messages = []
            for i, msg in enumerate(tagged_prompt.messages):
                tag = tagged_prompt.segment_tags.get(i, "UNKNOWN")
                
                # Check for Context Injection
                if tag == "CONTEXT_INJECTION":
                    # Since Context Injection is a single message, we apply a mixed rate
                    # For a more advanced setup we could split it, but here we'll use CONTEXT_CODE
                    rate = rates["CONTEXT_CODE"]
                else:
                    rate = rates.get(tag, 0.0)

                content = msg.get("content", "")
                
                if tag == "USER_QUERY" or rate <= 0.01 or not isinstance(content, str):
                    compressed_messages.append(msg)
                    continue

                # Run lingua
                res = self.lingua.compress_prompt(
                    [content],
                    instruction="",
                    question="",
                    target_token=max(1, int(self._count_tokens(content) * (1.0 - rate)))
                )
                
                compressed_content = res.get("compressed_prompt", content)
                new_msg = msg.copy()
                new_msg["content"] = compressed_content
                compressed_messages.append(new_msg)

            # 4. Measure post compression
            post_count = 0
            for msg in compressed_messages:
                post_count += 4
                if isinstance(msg.get("content"), str):
                    post_count += self._count_tokens(msg["content"])
            post_count += 2
            
            ratio = 1.0 - (post_count / pre_count) if pre_count > 0 else 0.0

            return CompressedPrompt(
                messages=compressed_messages,
                post_compression_tokens=post_count,
                compression_ratio=ratio
            )

        except Exception as e:
            logger.error(f"Compression failed: {e}", exc_info=True)
            self.degraded = True
            return CompressedPrompt(
                messages=tagged_prompt.messages,
                post_compression_tokens=tagged_prompt.pre_token_count,
                compression_ratio=0.0
            )
