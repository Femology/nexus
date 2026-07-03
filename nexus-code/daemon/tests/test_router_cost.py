import pytest
import os
import aiosqlite
from app.services.router import LLMRouter, LLMUsage, IntentTier

@pytest.fixture
def mock_config():
    class RouterConfig:
        cheap_model = "gemini-1.5-flash"
        mid_model = "gemini-1.5-pro"
        high_model = "gemini-1.5-pro"

    class LitellmConfig:
        retry_initial_delay_seconds = 0
        retry_max_attempts = 1
        fallback_chain = []

    class Config:
        router = RouterConfig()
        litellm = LitellmConfig()
        models = []

    return Config()

@pytest.fixture
def router(mock_config):
    router = LLMRouter(config=mock_config)
    # Use an in-memory DB or temporary file for tests if needed, but for now we just test the mapping logic
    return router

def test_select_tier_cheap(router):
    # LOCAL_EDIT -> cheap
    assert router.select_tier(IntentTier.LOCAL_EDIT, 1000, False) == "cheap"
    
    # EXPLAIN short -> cheap
    assert router.select_tier(IntentTier.EXPLAIN, 1500, False) == "cheap"

def test_select_tier_mid(router):
    # EXPLAIN long -> mid
    assert router.select_tier(IntentTier.EXPLAIN, 3000, False) == "mid"
    
    # DEBUG_LOOP -> mid
    assert router.select_tier(IntentTier.DEBUG_LOOP, 5000, False) == "mid"
    
    # REPO_QUERY short -> mid
    assert router.select_tier(IntentTier.REPO_QUERY, 5000, False) == "mid"

def test_select_tier_high(router):
    # REPO_QUERY long -> high
    assert router.select_tier(IntentTier.REPO_QUERY, 15000, False) == "high"

def test_select_tier_override(router):
    # Override always yields high
    assert router.select_tier(IntentTier.LOCAL_EDIT, 10, True) == "high"

@pytest.mark.asyncio
async def test_ledger_persistence(router):
    # Route db to a test path
    router.ledger_db_path = "test_ledger.db"
    if os.path.exists(router.ledger_db_path):
        os.remove(router.ledger_db_path)
        
    await router._init_ledger()
    usage = LLMUsage(prompt_tokens=100, completion_tokens=50, total_tokens=150)
    await router._log_usage("LOCAL_EDIT", "gemini-1.5-flash", usage)
    
    async with aiosqlite.connect(router.ledger_db_path) as db:
        async with db.execute("SELECT * FROM cost_ledger") as cursor:
            rows = await cursor.fetchall()
            assert len(rows) == 1
            row = rows[0]
            # row: id, timestamp, intent_tier, model_used, tokens_in, tokens_out, cost_usd
            assert row[2] == "LOCAL_EDIT"
            assert row[3] == "gemini-1.5-flash"
            assert row[4] == 100
            assert row[5] == 50
            
    if os.path.exists(router.ledger_db_path):
        os.remove(router.ledger_db_path)
