import pytest
from fastapi.testclient import TestClient
from unittest.mock import patch, AsyncMock, MagicMock
import json

from app.main import create_app
from app.config import DaemonConfig
from app.models.context import NexusPayload, ContextBundle, ActiveFile

@pytest.fixture
def test_app():
    # Mock lifespan to avoid actually initializing FAISS and heavy models
    app = create_app()
    return app

@pytest.fixture
def client(test_app):
    with TestClient(test_app) as client:
        yield client

def test_health(client):
    response = client.get("/health")
    assert response.status_code == 200

@patch("app.main._state", new_callable=dict)
@patch("litellm.acompletion")
def test_full_pipeline_chat(mock_acompletion, mock_state, client):
    # Setup mock state for lifespan services
    config = DaemonConfig()
    mock_state["config"] = config
    mock_state["system_prompt"] = "You are a helpful AI."
    mock_state["tool_definitions"] = []
    
    mock_embedding = AsyncMock()
    mock_embedding.embed_text = AsyncMock(return_value=[0.1] * 384)
    mock_state["embedding_service"] = mock_embedding
    
    mock_cache = AsyncMock()
    mock_cache.check_l1 = AsyncMock(return_value=None)
    mock_cache.check_l2 = AsyncMock(return_value=None)
    mock_cache.add_l1 = AsyncMock()
    mock_cache.add_l2 = AsyncMock()
    mock_state["semantic_cache"] = mock_cache
    
    mock_session = AsyncMock()
    mock_session.get_session = AsyncMock(return_value=MagicMock())
    mock_state["session_manager"] = mock_session
    
    mock_memory = AsyncMock()
    mock_memory.retrieve_relevant_nodes = AsyncMock(return_value=[])
    mock_memory.write_memory = AsyncMock()
    mock_state["memory_graph"] = mock_memory
    
    mock_optimizer = MagicMock()
    mock_optimizer.optimize = MagicMock(return_value="Optimized Context")
    mock_state["context_optimizer"] = mock_optimizer
    
    mock_assembler = MagicMock()
    mock_assembler.assemble = MagicMock(return_value=[{"role": "user", "content": "Hello"}])
    mock_state["prompt_assembler"] = mock_assembler
    
    mock_compressor = AsyncMock()
    mock_compressor.compress = AsyncMock(return_value=MagicMock(messages=[{"role": "user", "content": "Hello"}], final_token_count=10, initial_token_count=20, compression_ratio=0.5))
    mock_state["prompt_compressor"] = mock_compressor
    
    mock_router = AsyncMock()
    mock_router.dispatch = AsyncMock()
    
    class MockLLMResult:
        text = "Hello world!"
        finish_reason = "stop"
        usage = MagicMock(prompt_tokens=10, completion_tokens=5, total_tokens=15)
        model_used = "mock-model"
        tool_calls = None
    
    mock_router.dispatch.return_value = MockLLMResult()
    mock_router.calculate_cost = MagicMock(return_value=0.0)
    mock_state["llm_router"] = mock_router
    
    mock_updater = AsyncMock()
    mock_updater.update_summary = AsyncMock()
    mock_state["summary_updater"] = mock_updater
    
    mock_response_processor = AsyncMock()
    # We will let the chat route call process_response.
    # Actually wait, `chat.py` fetches `response_processor` from state.
    from app.services.response_processor import ResponseProcessor
    real_processor = ResponseProcessor(mock_router, mock_updater)
    mock_state["response_processor"] = real_processor

    payload = {
        "session_id": "test_session",
        "request_id": "req-123",
        "timestamp": "2026-01-01T00:00:00Z",
        "model_alias": "gpt-4o",
        "stream": False,
        "user_message": "Hello!",
        "provider_key_alias": "test_key",
        "context_bundle": {
            "active_file": {
                "path": "test.py",
                "language_id": "python",
                "content": "print('hello')",
                "cursor_position": {"line": 1, "column": 1}
            },
            "selection": None,
            "open_tabs": [],
            "workspace_structure": {},
            "git_diff": "",
            "diagnostics": [],
            "terminal_snapshot": None,
            "pre_compression_token_estimate": 0,
            "heavy_context_flag": False
        },
        "history_ref": "test_session",
        "tool_results": None
    }
    
    response = client.post("/v1/chat", json=payload, headers={"Authorization": "Bearer fake_key"})
    
    assert response.status_code == 200
    data = response.json()
    assert data["response_text"] == "Hello world!"
    assert data["cache_hit"] is False
