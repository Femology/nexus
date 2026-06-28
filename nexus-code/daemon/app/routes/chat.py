"""POST /v1/chat — Primary chat endpoint.

Validates the incoming NexusPayload, builds the internal Request Context,
and runs the optimization pipeline.  During Phase 1 the pipeline stages
(cache, memory, compression, router) are placeholders that pass through,
so this route returns a mock response indicating the daemon is connected.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Request
from pydantic import BaseModel

from app.middleware.auth import require_api_key
from app.models.request import NexusPayload
from app.models.response import NexusResponse, UsageStats
from app.models.context import RequestContext

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1")

@router.post("/chat", response_model=NexusResponse)
async def chat(request: Request, payload: NexusPayload) -> NexusResponse:
    # Extract API key from middleware
    api_key = require_api_key()

    # Access Phase 4 services from app state
    session_manager = request.app.state._state.get("session_manager")
    semantic_cache = request.app.state._state.get("semantic_cache")
    embedding_service = request.app.state._state.get("embedding_service")

    # Step 3: Session lookup / creation
    session = await session_manager.get_or_create(payload.session_id, payload.model_alias)

    # Step 4: Build internal request context
    ctx = RequestContext(
        payload=payload,
        api_key=api_key,
        session=session
    )

    logger.info(
        "Chat request received — session=%s request=%s model=%s stream=%s",
        session.session_id,
        payload.request_id,
        payload.model_alias,
        payload.stream,
    )

    # Step 5: Semantic Cache check
    # Build composite cache key
    active_file = payload.context_bundle.get("active_file", {})
    lang_id = active_file.get("language_id", "plaintext")
    has_selection = bool(payload.context_bundle.get("selection"))
    
    cache_key = embedding_service.build_composite_key(
        payload.user_message,
        lang_id,
        has_selection
    )

    query_embedding = await embedding_service.embed(cache_key)
    
    # Check cache
    cache_result = await semantic_cache.check(session.session_id, query_embedding)

    if cache_result and cache_result.hit:
        # Step 6: Cache hit
        logger.info(f"Cache hit! Tier: {cache_result.tier}")
        
        # We need to construct the response token stats. We can estimate.
        # But for now, returning 0/0. In Phase 6, we refine token stats.
        return NexusResponse(
            request_id=payload.request_id,
            session_id=session.session_id,
            response_text=cache_result.response,
            tool_calls=None,
            is_final=True,
            cache_hit=True,
            cache_tier=cache_result.tier,
            memory_nodes_retrieved=0,
            pre_compression_tokens=0,
            post_compression_tokens=0,
            compression_ratio=0.0,
            usage=UsageStats(prompt_tokens=0, completion_tokens=0, total_tokens=0),
            model_used=payload.model_alias,
            cost_estimate_usd=0.0,
            error=None,
        )

    # Extract Phase 6 services
    memory_graph = request.app.state._state.get("memory_graph")
    context_optimizer = request.app.state._state.get("context_optimizer")
    prompt_assembler = request.app.state._state.get("prompt_assembler")
    compressor = request.app.state._state.get("prompt_compressor")
    router_service = request.app.state._state.get("llm_router")
    response_processor = request.app.state._state.get("response_processor")

    # Step 4: TOOL LOOP CHECK
    if payload.tool_results:
        session_tl = session.active_tool_loop
        # We check config via session_manager's config reference
        max_iter = getattr(session_manager.config.tool_loop, 'max_iterations', 3) if hasattr(session_manager.config, 'tool_loop') else 3
        
        if session_tl and session_tl.iteration_count < max_iter:
            # Append tool results
            for res in payload.tool_results:
                session_tl.messages.append({"role": "tool", "content": str(res)})
                
            # Dispatch directly
            from ..models.context import CompressedPrompt
            # In a real implementation we might re-compress, but for now we dispatch the tool loop as a compressed prompt stub
            # Since the tools need the history, we just pass the active tool loop messages
            # Note: A real implementation requires constructing the exact litellm message format.
            # We'll construct a direct TaggedPrompt
            
            tagged_prompt = await prompt_assembler.assemble(
                "", session.conversation_summary, 
                ctx.filtered_context, payload.user_message, 
                session_tl.messages
            )
            # Skip compression for tool loop continuation for simplicity and reliability
            compressed = CompressedPrompt(
                messages=tagged_prompt.messages,
                post_compression_tokens=tagged_prompt.pre_token_count,
                compression_ratio=0.0
            )
            
            llm_result = await router_service.dispatch(compressed, payload.model_alias, api_key, False, prompt_assembler.tool_definitions)
            response = await response_processor.process(llm_result, ctx, session_manager, memory_graph, semantic_cache)
            return response
        else:
            return NexusResponse(
                request_id=payload.request_id,
                session_id=session.session_id,
                response_text="",
                tool_calls=None,
                is_final=True,
                error={"code": "TOOL_LOOP_LIMIT", "message": "Max tool loop iterations exceeded."}
            )

    # 7a. Retrieve memory
    intent = payload.user_message 
    memory_bundle = await memory_graph.retrieve(
        query_text=payload.user_message,
        language_id=lang_id,
        session_id=session.session_id,
        request_intent=intent
    )
    ctx.memory_context = memory_bundle.text
    ctx.memory_nodes_count = memory_bundle.node_count

    # 7b. Optimize Context
    filtered_context = await context_optimizer.optimize(
        context_bundle=payload.context_bundle,
        query_text=payload.user_message,
        query_embedding=query_embedding
    )
    ctx.filtered_context = filtered_context.model_dump()

    # 7c. Assemble Prompt
    tool_history = session.active_tool_loop.messages if session.active_tool_loop else None
    
    tagged_prompt = await prompt_assembler.assemble(
        memory_context=ctx.memory_context,
        conversation_summary=session.conversation_summary,
        filtered_context=filtered_context,
        user_message=payload.user_message,
        tool_history=tool_history
    )
    ctx.assembled_prompt = tagged_prompt.messages
    ctx.pre_compression_tokens = tagged_prompt.pre_token_count

    # 8. Compression
    model_context_window = router_service.get_context_window(payload.model_alias)
    heavy_context_flag = payload.context_bundle.get("heavy_context_flag", False)
    
    compressed = await compressor.compress(tagged_prompt, heavy_context_flag, model_context_window)
    ctx.compressed_prompt = compressed
    ctx.post_compression_tokens = compressed.post_compression_tokens

    # 9. LLM dispatch
    tools = prompt_assembler.tool_definitions if not payload.tool_results else None

    if payload.stream:
        # FastAPI handles async generators directly if wrapped in StreamingResponse
        from fastapi.responses import StreamingResponse
        import json
        
        async def stream_generator():
            async for chunk in router_service.dispatch(compressed, payload.model_alias, api_key, True, tools):
                if chunk.is_final:
                    response = await response_processor.process(chunk.llm_result, ctx, session_manager, memory_graph, semantic_cache)
                    yield f"data: {response.model_dump_json()}\n\n"
                    yield "data: [DONE]\n\n"
                else:
                    yield f"data: {json.dumps({'delta': chunk.delta, 'request_id': ctx.payload.request_id})}\n\n"

        return StreamingResponse(stream_generator(), media_type="text/event-stream")
    else:
        llm_result = await router_service.dispatch(compressed, payload.model_alias, api_key, False, tools)
        response = await response_processor.process(llm_result, ctx, session_manager, memory_graph, semantic_cache)
        return response
