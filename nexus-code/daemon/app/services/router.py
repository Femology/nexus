import asyncio
import json
import logging
import httpx
import os
import aiosqlite
from datetime import datetime
from typing import Dict, List, Optional, Any, AsyncGenerator, Union
from pydantic import BaseModel

import litellm
# litellm config setup
litellm.telemetry = False
litellm.drop_params = True

from ..models.context import CompressedPrompt
from .intent_router import IntentTier

logger = logging.getLogger(__name__)

class LLMUsage(BaseModel):
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0

class LLMResult(BaseModel):
    text: Optional[str] = None
    tool_calls: Optional[List[Dict[str, Any]]] = None
    finish_reason: str
    usage: LLMUsage
    model_used: str

class StreamChunk(BaseModel):
    delta: str = ""
    request_id: str = ""
    is_final: bool = False
    llm_result: Optional[LLMResult] = None

class LLMRouter:
    def __init__(self, config=None):
        self.config = config
        self.models_map = {}
        self.retry_delay = getattr(config.litellm, 'retry_initial_delay_seconds', 1.0) if config and hasattr(config, 'litellm') else 1.0
        self.max_retries = getattr(config.litellm, 'retry_max_attempts', 3) if config and hasattr(config, 'litellm') else 3
        self.fallback_chain = getattr(config.litellm, 'fallback_chain', []) if config and hasattr(config, 'litellm') else []
        
        self.ledger_db_path = os.path.expanduser("~/.nexus-code/ledger.db")
        
        self.tier_map = {
            "cheap": getattr(config.router, 'cheap_model', 'gemini-1.5-flash') if config and hasattr(config, 'router') else 'gemini-1.5-flash',
            "mid": getattr(config.router, 'mid_model', 'gemini-1.5-pro') if config and hasattr(config, 'router') else 'gemini-1.5-pro',
            "high": getattr(config.router, 'high_model', 'gemini-1.5-pro') if config and hasattr(config, 'router') else 'gemini-1.5-pro',
        }
        
        # Load from config
        if config and hasattr(config, 'models'):
            for m in config.models:
                self.models_map[m.alias] = {
                    "provider": m.provider,
                    "model_id": m.model_id,
                    "context_window": m.context_window,
                    "pricing": getattr(m, 'pricing', {})
                }
                
        # Attempt to discover Ollama models
        self._discover_ollama()

    def _discover_ollama(self):
        try:
            resp = httpx.get("http://localhost:11434/api/tags", timeout=2.0)
            if resp.status_code == 200:
                data = resp.json()
                for model in data.get("models", []):
                    name = model["name"]
                    alias = f"ollama/{name}"
                    self.models_map[alias] = {
                        "provider": "ollama",
                        "model_id": f"ollama/{name}",
                        "context_window": 8192,
                        "pricing": {"input_cost_per_token": 0, "output_cost_per_token": 0}
                    }
                logger.info(f"Discovered {len(data.get('models', []))} Ollama models.")
        except Exception:
            logger.info("Ollama not reachable.")

    def get_context_window(self, model_alias: str) -> int:
        return self.models_map.get(model_alias, {}).get("context_window", 8192)

    def calculate_cost(self, model_alias: str, usage: Dict[str, int]) -> float:
        model_info = self.models_map.get(model_alias, {})
        pricing = model_info.get("pricing", {})
        prompt_cost = usage.get("prompt_tokens", 0) * pricing.get("input_cost_per_token", 0.0)
        comp_cost = usage.get("completion_tokens", 0) * pricing.get("output_cost_per_token", 0.0)
        return prompt_cost + comp_cost

    async def _init_ledger(self):
        os.makedirs(os.path.dirname(self.ledger_db_path), exist_ok=True)
        async with aiosqlite.connect(self.ledger_db_path) as db:
            await db.execute('''
                CREATE TABLE IF NOT EXISTS cost_ledger (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TEXT,
                    intent_tier TEXT,
                    model_used TEXT,
                    tokens_in INTEGER,
                    tokens_out INTEGER,
                    cost_usd REAL
                )
            ''')
            await db.commit()

    async def _log_usage(self, intent_tier_str: str, model_used: str, usage: LLMUsage):
        cost = self.calculate_cost(model_used, {"prompt_tokens": usage.prompt_tokens, "completion_tokens": usage.completion_tokens})
        async with aiosqlite.connect(self.ledger_db_path) as db:
            await db.execute('''
                INSERT INTO cost_ledger (timestamp, intent_tier, model_used, tokens_in, tokens_out, cost_usd)
                VALUES (?, ?, ?, ?, ?, ?)
            ''', (datetime.utcnow().isoformat(), intent_tier_str, model_used, usage.prompt_tokens, usage.completion_tokens, cost))
            await db.commit()

    def select_tier(self, intent_tier: IntentTier, context_tokens: int, override_high: bool) -> str:
        if override_high:
            return "high"
        
        if intent_tier == IntentTier.LOCAL_EDIT:
            return "cheap"
        elif intent_tier == IntentTier.EXPLAIN:
            return "cheap" if context_tokens < 2000 else "mid"
        elif intent_tier == IntentTier.DEBUG_LOOP:
            return "mid"
        elif intent_tier == IntentTier.REPO_QUERY:
            return "high" if context_tokens > 10000 else "mid"
        return "mid"

    async def _execute_request(self, messages, resolved_model, api_key, stream, tools, attempt=0) -> Any:
        try:
            resp = await litellm.acompletion(
                model=resolved_model,
                messages=messages,
                api_key=api_key,
                tools=tools,
                stream=stream
            )
            return resp
        except Exception as e:
            err_str = str(e).lower()
            if "401" in err_str or "authentication" in err_str:
                raise ValueError(f"AUTH_FAILURE: {e}")
            if "403" in err_str:
                raise ValueError(f"FORBIDDEN: {e}")
            if "404" in err_str or "not found" in err_str:
                raise ValueError(f"MODEL_NOT_FOUND: {e}")
            
            is_timeout = "timeout" in err_str or "timed out" in err_str
            
            if attempt < self.max_retries:
                delay = self.retry_delay * (2 ** attempt)
                logger.warning(f"LiteLLM error ({e}), retrying in {delay}s...")
                await asyncio.sleep(delay)
                return await self._execute_request(messages, resolved_model, api_key, stream, tools, attempt + 1)
            
            if is_timeout:
                raise ValueError(f"PROVIDER_TIMEOUT: {e}")
            raise ValueError(f"PROVIDER_ERROR: {e}")

    async def dispatch(
        self,
        compressed_prompt: CompressedPrompt,
        api_key: str,
        stream: bool,
        tools: Optional[List[Dict[str, Any]]] = None,
        intent_tier: Optional[IntentTier] = None,
        override_high: bool = False
    ) -> Union[LLMResult, AsyncGenerator[StreamChunk, None]]:
        
        await self._init_ledger()
        
        context_tokens = getattr(compressed_prompt, 'post_compression_tokens', 0)
        tier_name = "mid"
        intent_tier_str = "UNKNOWN"
        
        if intent_tier is not None:
            tier_name = self.select_tier(intent_tier, context_tokens, override_high)
            intent_tier_str = intent_tier.name
            
        model_alias = self.tier_map.get(tier_name, 'gemini-1.5-pro')
        chain = [model_alias] + self.fallback_chain
        
        for alias in chain:
            model_info = self.models_map.get(alias)
            resolved_model = model_info["model_id"] if model_info else alias
            
            try:
                if not stream:
                    response = await self._execute_request(compressed_prompt.messages, resolved_model, api_key, stream=False, tools=tools)
                    
                    choice = response.choices[0]
                    msg = choice.message
                    
                    usage = LLMUsage()
                    if hasattr(response, 'usage') and response.usage:
                        usage.prompt_tokens = response.usage.prompt_tokens
                        usage.completion_tokens = response.usage.completion_tokens
                        usage.total_tokens = response.usage.total_tokens
                    
                    tool_calls = None
                    if hasattr(msg, 'tool_calls') and msg.tool_calls:
                        tool_calls = [tc.model_dump() for tc in msg.tool_calls]
                        
                    result = LLMResult(
                        text=msg.content,
                        tool_calls=tool_calls,
                        finish_reason=choice.finish_reason,
                        usage=usage,
                        model_used=resolved_model
                    )
                    await self._log_usage(intent_tier_str, resolved_model, usage)
                    return result
                else:
                    return self._stream_generator(compressed_prompt.messages, resolved_model, api_key, tools, intent_tier_str)
                    
            except Exception as e:
                logger.error(f"Model {alias} failed: {e}")
                err_str = str(e)
                if "AUTH_FAILURE" in err_str or "FORBIDDEN" in err_str or "MODEL_NOT_FOUND" in err_str:
                    raise
                continue
                
        raise RuntimeError("All models in fallback chain failed.")

    async def _stream_generator(self, messages, resolved_model, api_key, tools, intent_tier_str) -> AsyncGenerator[StreamChunk, None]:
        response = await self._execute_request(messages, resolved_model, api_key, stream=True, tools=tools)
        
        full_text = ""
        req_id = ""
        prompt_tokens = 0
        completion_tokens = 0
        
        async for chunk in response:
            if hasattr(chunk, 'id'):
                req_id = chunk.id
            if chunk.choices and len(chunk.choices) > 0:
                delta = chunk.choices[0].delta
                content = getattr(delta, 'content', None) or ""
                if content:
                    full_text += content
                    completion_tokens += 1
                    yield StreamChunk(delta=content, request_id=req_id, is_final=False)

        usage = LLMUsage(prompt_tokens=prompt_tokens, completion_tokens=completion_tokens, total_tokens=prompt_tokens+completion_tokens)
        
        llm_result = LLMResult(
            text=full_text,
            tool_calls=None,
            finish_reason="stop",
            usage=usage,
            model_used=resolved_model
        )
        
        await self._log_usage(intent_tier_str, resolved_model, usage)
        yield StreamChunk(delta="", request_id=req_id, is_final=True, llm_result=llm_result)

