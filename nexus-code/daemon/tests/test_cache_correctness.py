import pytest
import numpy as np
import faiss
from datetime import datetime
from app.services.cache import SemanticCache, compute_context_hash

@pytest.mark.asyncio
async def test_cache_hit_on_identical_hash():
    cache = SemanticCache()
    session_id = "test_session_1"
    
    # 1. Setup mock data
    user_message = "What does this code do?"
    context_bundle = {"active_file": {"content": "def hello(): pass"}}
    
    content_hash = compute_context_hash(user_message, context_bundle)
    embedding = np.random.rand(1, 384).astype('float32')
    faiss.normalize_L2(embedding) if 'faiss' in globals() else None  # Ensure normalization if needed by the test environment
    
    # 2. Store in cache
    await cache.store(
        session_id=session_id,
        query_embedding=embedding,
        content_hash=content_hash,
        response_text="It defines a hello function.",
        model_used="test-model",
        metadata={}
    )
    
    # 3. Lookup with exact same hash -> Hit
    result = await cache.check(session_id, embedding, content_hash)
    assert result is not None
    assert result.hit is True
    assert result.response == "It defines a hello function."


@pytest.mark.asyncio
async def test_cache_miss_on_different_hash():
    cache = SemanticCache()
    session_id = "test_session_2"
    
    user_message = "What does this code do?"
    context_bundle_1 = {"active_file": {"content": "def hello(): pass"}}
    context_bundle_2 = {"active_file": {"content": "def hello(): print('hi')"}}
    
    content_hash_1 = compute_context_hash(user_message, context_bundle_1)
    content_hash_2 = compute_context_hash(user_message, context_bundle_2)
    
    assert content_hash_1 != content_hash_2
    
    embedding = np.random.rand(1, 384).astype('float32')
    
    # Store bundle 1
    await cache.store(
        session_id=session_id,
        query_embedding=embedding,
        content_hash=content_hash_1,
        response_text="It defines a hello function.",
        model_used="test-model",
        metadata={}
    )
    
    # Lookup with exact same embedding but different context hash -> Miss
    result = await cache.check(session_id, embedding, content_hash_2)
    assert result is None
