"""GET /health — Daemon health-check endpoint.

Reports the status of every sub-system: cache, memory, compressor, router.
Sub-system statuses are updated by the respective services when they detect
degradation.  During Phase 1, all sub-systems report ``healthy`` because
their implementations are placeholder pass-throughs.
"""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Request
from pydantic import BaseModel

router = APIRouter()

# ---------------------------------------------------------------------------
# Health status registry (mutable at runtime by sub-system services)
# ---------------------------------------------------------------------------

SubsystemStatus = Literal["healthy", "degraded", "unavailable"]


class _HealthRegistry:
    """Singleton that tracks each sub-system's operational status."""

    def __init__(self) -> None:
        self.cache: SubsystemStatus = "healthy"
        self.memory: SubsystemStatus = "healthy"
        self.compressor: SubsystemStatus = "healthy"
        self.router: SubsystemStatus = "healthy"

    @property
    def overall(self) -> SubsystemStatus:
        statuses = [self.cache, self.memory, self.compressor, self.router]
        if any(s == "unavailable" for s in statuses):
            return "unavailable"
        if any(s == "degraded" for s in statuses):
            return "degraded"
        return "healthy"


health_registry = _HealthRegistry()


# ---------------------------------------------------------------------------
# Response model
# ---------------------------------------------------------------------------


class SubsystemHealth(BaseModel):
    status: SubsystemStatus


class HealthResponse(BaseModel):
    status: SubsystemStatus
    subsystems: dict[str, SubsystemHealth]


# ---------------------------------------------------------------------------
# Route
# ---------------------------------------------------------------------------


@router.get("/health", response_model=HealthResponse)
async def health_check(request: Request) -> HealthResponse:
    """Return the current operational status of the daemon and all sub-systems."""
    
    # Check real cache status
    semantic_cache = request.app.state._state.get("semantic_cache")
    if semantic_cache and semantic_cache.degraded:
        health_registry.cache = "degraded"
    else:
        health_registry.cache = "healthy"

    return HealthResponse(
        status=health_registry.overall,
        subsystems={
            "cache": SubsystemHealth(status=health_registry.cache),
            "memory": SubsystemHealth(status=health_registry.memory),
            "compressor": SubsystemHealth(status=health_registry.compressor),
            "router": SubsystemHealth(status=health_registry.router),
        },
    )
