"""Pydantic v2 models for the NexusResponse response contract.

Every field mirrors the shared JSON Schema at shared/nexus_schema.json.
Custom validators enforce response-level business rules.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator


# ---------------------------------------------------------------------------
# Nested sub-models
# ---------------------------------------------------------------------------


class ToolCall(BaseModel):
    """A single tool invocation requested by the LLM."""

    id: str = Field(min_length=1)
    tool_name: str = Field(min_length=1)
    arguments: dict[str, Any]


class UsageStats(BaseModel):
    """Token usage as reported by the LLM provider."""

    prompt_tokens: int = Field(ge=0)
    completion_tokens: int = Field(ge=0)
    total_tokens: int = Field(ge=0)


class ResponseError(BaseModel):
    """Structured error from any layer of the daemon pipeline."""

    code: str = Field(min_length=1)
    message: str
    layer: Literal["cache", "memory", "compression", "router", "provider", "tool_loop"]
    is_retryable: bool


# ---------------------------------------------------------------------------
# Top-level response
# ---------------------------------------------------------------------------


class NexusResponse(BaseModel):
    """Master response contract returned from the daemon to the Extension Host.

    For streaming responses, the final SSE chunk carries this full schema.
    For non-streaming, the entire HTTP body is this schema.
    """

    request_id: str
    session_id: str
    response_text: str | None
    tool_calls: list[ToolCall] | None
    is_final: bool
    cache_hit: bool
    cache_tier: Literal["L1", "L2"] | None
    memory_nodes_retrieved: int = Field(ge=0)
    pre_compression_tokens: int = Field(ge=0)
    post_compression_tokens: int = Field(ge=0)
    compression_ratio: float = Field(ge=0.0, le=1.0)
    usage: UsageStats
    model_used: str
    cost_estimate_usd: float = Field(ge=0.0)
    error: ResponseError | None

    # -- Validators ----------------------------------------------------------

    @model_validator(mode="after")
    def _validate_final_response_has_text(self) -> "NexusResponse":
        """When is_final is True the response must carry either text or an error."""
        if self.is_final and self.response_text is None and self.error is None:
            raise ValueError(
                "response_text must be non-null when is_final is True "
                "and no error is present"
            )
        return self

    @model_validator(mode="after")
    def _validate_cache_tier_consistency(self) -> "NexusResponse":
        """cache_tier must be set when cache_hit is True and null otherwise."""
        if self.cache_hit and self.cache_tier is None:
            raise ValueError("cache_tier must be 'L1' or 'L2' when cache_hit is True")
        if not self.cache_hit and self.cache_tier is not None:
            raise ValueError("cache_tier must be null when cache_hit is False")
        return self
