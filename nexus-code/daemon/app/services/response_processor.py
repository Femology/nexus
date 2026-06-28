import asyncio
import logging
from typing import Dict, Any

from .router import LLMResult
from ..models.context import RequestContext, ToolLoopState
from ..models.response import NexusResponse, UsageStats

logger = logging.getLogger(__name__)

class ResponseProcessor:
    def __init__(self, router, summary_updater):
        self.router = router
        self.summary_updater = summary_updater

    async def process(
        self,
        llm_result: LLMResult,
        ctx: RequestContext,
        session_manager,
        memory_graph,
        cache
    ) -> NexusResponse:
        
        session = ctx.session
        payload = ctx.payload

        if llm_result.tool_calls:
            # CASE 1: Tool calls present
            if not session.active_tool_loop:
                session.active_tool_loop = ToolLoopState()
                
            session.active_tool_loop.iteration_count += 1
            # In a real app we'd append the tool calls to the session history here
            
            return NexusResponse(
                request_id=payload.request_id,
                session_id=session.session_id,
                response_text="",
                tool_calls=llm_result.tool_calls,
                is_final=False,
                cache_hit=False,
                cache_tier=None,
                memory_nodes_retrieved=ctx.memory_nodes_count,
                pre_compression_tokens=ctx.pre_compression_tokens,
                post_compression_tokens=ctx.post_compression_tokens,
                compression_ratio=0.0,
                usage=UsageStats(
                    prompt_tokens=llm_result.usage.prompt_tokens,
                    completion_tokens=llm_result.usage.completion_tokens,
                    total_tokens=llm_result.usage.total_tokens
                ),
                model_used=llm_result.model_used,
                cost_estimate_usd=self.router.calculate_cost(payload.model_alias, llm_result.usage.model_dump()),
                error=None
            )

        # CASE 2: Final text response
        response_text = llm_result.text or ""
        session.turn_count += 1
        
        # Fire and forget tasks
        asyncio.create_task(
            memory_graph.write_memory(
                user_message=payload.user_message,
                response_text=response_text,
                context_bundle=payload.context_bundle,
                session_id=session.session_id
            )
        )

        asyncio.create_task(
            self.summary_updater.update_summary(
                session=session,
                user_message=payload.user_message,
                response_text=response_text
            )
        )
        
        if cache and payload.user_message:
            # Build cache key
            active_file = payload.context_bundle.get("active_file", {})
            lang_id = active_file.get("language_id", "plaintext")
            has_selection = bool(payload.context_bundle.get("selection"))
            
            # Need embedding service for key embedding
            embedding_service = memory_graph.embedding_service
            cache_key = embedding_service.build_composite_key(payload.user_message, lang_id, has_selection)
            
            async def _store_cache():
                emb = await embedding_service.embed(cache_key)
                meta = {"request_id": payload.request_id}
                await cache.store(session.session_id, emb, response_text, llm_result.model_used, meta)
                
            asyncio.create_task(_store_cache())
            
        # Clear tool loop
        session.active_tool_loop = None

        return NexusResponse(
            request_id=payload.request_id,
            session_id=session.session_id,
            response_text=response_text,
            tool_calls=None,
            is_final=True,
            cache_hit=False,
            cache_tier=None,
            memory_nodes_retrieved=ctx.memory_nodes_count,
            pre_compression_tokens=ctx.pre_compression_tokens,
            post_compression_tokens=ctx.post_compression_tokens,
            compression_ratio=ctx.compressed_prompt.compression_ratio if ctx.compressed_prompt else 0.0,
            usage=UsageStats(
                prompt_tokens=llm_result.usage.prompt_tokens,
                completion_tokens=llm_result.usage.completion_tokens,
                total_tokens=llm_result.usage.total_tokens
            ),
            model_used=llm_result.model_used,
            cost_estimate_usd=self.router.calculate_cost(payload.model_alias, llm_result.usage.model_dump()),
            error=None
        )
