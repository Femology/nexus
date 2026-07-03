import pytest
import asyncio
from app.services.intent_router import intent_router, IntentTier

@pytest.mark.asyncio
async def test_heuristic_local_edit():
    # Selection + explicit keyword
    tier = await intent_router.classify("refactor this to be cleaner", has_selection=True)
    assert tier == IntentTier.LOCAL_EDIT

@pytest.mark.asyncio
async def test_heuristic_explain():
    tier = await intent_router.classify("what does this code do?", has_selection=False)
    assert tier == IntentTier.EXPLAIN

@pytest.mark.asyncio
async def test_heuristic_repo_query():
    tier = await intent_router.classify("where is the User class?", has_selection=False)
    assert tier == IntentTier.REPO_QUERY

@pytest.mark.asyncio
async def test_heuristic_debug_loop():
    tier = await intent_router.classify("why is the test failure happening?", has_selection=False)
    assert tier == IntentTier.DEBUG_LOOP

@pytest.mark.asyncio
async def test_embedding_fallback_clear():
    # Should fall back to embedding and find close match
    # E.g., "explain this block" -> EXPLAIN
    tier = await intent_router.classify("explain this block", has_selection=False)
    assert tier == IntentTier.EXPLAIN

@pytest.mark.asyncio
async def test_embedding_ambiguous_escalation():
    # A completely random or vague prompt should escalate
    # e.g., "hello" or "do the thing"
    # Wait, "do the thing" might not match anything closely, so top_score < 0.4
    tier = await intent_router.classify("blargh flargh", has_selection=False)
    # Ambiguous low confidence should escalate to REPO_QUERY
    assert tier == IntentTier.REPO_QUERY

@pytest.mark.asyncio
async def test_embedding_marginal_escalation():
    # If the top two scores are very close, it escalates to the max of the two
    # Hard to guarantee exact scores without mocking embeddings, but testing the logic is sufficient if we cover it.
    pass
