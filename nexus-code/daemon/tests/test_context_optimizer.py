import pytest
from app.services.context_optimizer import ContextOptimizer
from app.services.intent_router import IntentTier
from app.services.embedding import EmbeddingService

@pytest.fixture
def mock_context_bundle():
    return {
        "active_file": {"content": "def foo():\n    pass\n" * 400, "cursor_position": {"line": 10}},
        "selection": {"start": {"line": 10}, "end": {"line": 10}},
        "open_tabs": [{"content": "tab1"}, {"content": "tab2"}, {"content": "tab3"}],
        "workspace_structure": "src/\n  main.py",
        "git_diff": "+ def new_func(): pass",
        "diagnostics": "Error on line 10",
        "terminal_snapshot": "npm ERR! code 1",
        "memory_graph": "node1 -> node2"
    }

@pytest.fixture
def optimizer():
    # We don't need a real embedding service since we replaced the scoring logic
    return ContextOptimizer(embedding_service=None)

@pytest.mark.asyncio
async def test_local_edit_context(optimizer, mock_context_bundle):
    filtered = await optimizer.optimize(mock_context_bundle, IntentTier.LOCAL_EDIT)
    
    # Included
    assert filtered.active_file is not None
    assert "// ... [lines 161-400 omitted] ..." in filtered.active_file["content"]
    assert filtered.selection == mock_context_bundle["selection"]
    
    # Excluded
    assert not filtered.open_tabs
    assert filtered.workspace_structure is None
    assert filtered.git_diff is None
    assert filtered.diagnostics is None
    assert filtered.terminal_snapshot is None
    assert filtered.memory_graph is None

@pytest.mark.asyncio
async def test_explain_context(optimizer, mock_context_bundle):
    filtered = await optimizer.optimize(mock_context_bundle, IntentTier.EXPLAIN)
    
    # Included
    assert filtered.active_file is not None
    assert filtered.selection is not None
    assert filtered.memory_graph == "node1 -> node2"
    
    # Excluded
    assert not filtered.open_tabs
    assert filtered.workspace_structure is None
    assert filtered.terminal_snapshot is None

@pytest.mark.asyncio
async def test_debug_loop_context(optimizer, mock_context_bundle):
    filtered = await optimizer.optimize(mock_context_bundle, IntentTier.DEBUG_LOOP)
    
    # Included
    assert filtered.active_file is not None
    assert filtered.diagnostics == "Error on line 10"
    assert filtered.terminal_snapshot == "npm ERR! code 1"
    assert filtered.memory_graph == "node1 -> node2"
    
    # Excluded
    assert filtered.workspace_structure is None
    assert not filtered.open_tabs

@pytest.mark.asyncio
async def test_repo_query_context(optimizer, mock_context_bundle):
    filtered = await optimizer.optimize(mock_context_bundle, IntentTier.REPO_QUERY)
    
    # Included
    assert filtered.workspace_structure == "src/\n  main.py"
    assert filtered.memory_graph == "node1 -> node2"
    assert len(filtered.open_tabs) == 2 # Top 2 tabs included
    
    # active_file included ONLY because selection exists in mock
    assert filtered.active_file is not None
    
    # Remove selection and test again
    mock_context_bundle["selection"] = None
    filtered2 = await optimizer.optimize(mock_context_bundle, IntentTier.REPO_QUERY)
    assert filtered2.active_file is None
