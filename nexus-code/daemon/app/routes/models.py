"""GET /v1/models — Available model listing.

Returns the list of configured model aliases from ``daemon_config.yaml``
merged with any locally-running Ollama models discovered via the Ollama
REST API on ``localhost:11434``.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx
from fastapi import APIRouter
from pydantic import BaseModel, Field

from app.config import DaemonConfig

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1")

# ---------------------------------------------------------------------------
# Will be set during lifespan startup
# ---------------------------------------------------------------------------

_config: DaemonConfig | None = None


def set_config(config: DaemonConfig) -> None:
    """Inject the loaded configuration at startup."""
    global _config
    _config = config


# ---------------------------------------------------------------------------
# Response models
# ---------------------------------------------------------------------------


class ModelInfo(BaseModel):
    alias: str
    provider: str
    model_id: str
    context_window: int = Field(ge=1)
    pricing: dict[str, float] = Field(default_factory=lambda: {"input_per_million": 0.0, "output_per_million": 0.0})


class ModelsResponse(BaseModel):
    models: list[ModelInfo]


# ---------------------------------------------------------------------------
# Ollama discovery
# ---------------------------------------------------------------------------

_OLLAMA_URL = "http://localhost:11434/api/tags"
_OLLAMA_TIMEOUT = 2.0  # seconds


async def _discover_ollama_models() -> list[ModelInfo]:
    """Attempt to discover locally-running Ollama models.

    Returns an empty list if Ollama is not reachable or returns
    unexpected data.  Never raises.
    """
    try:
        async with httpx.AsyncClient(timeout=_OLLAMA_TIMEOUT) as client:
            resp = await client.get(_OLLAMA_URL)
            resp.raise_for_status()
            data: dict[str, Any] = resp.json()

        models: list[ModelInfo] = []
        for entry in data.get("models", []):
            name: str = entry.get("name", "")
            if not name:
                continue
            # Strip the `:latest` tag if present for a cleaner alias
            clean_name = name.split(":")[0] if ":" in name else name
            models.append(
                ModelInfo(
                    alias=f"ollama/{clean_name}",
                    provider="ollama",
                    model_id=f"ollama/{clean_name}",
                    context_window=entry.get("details", {}).get(
                        "parameter_size", 8192
                    )
                    if isinstance(entry.get("details"), dict)
                    else 8192,
                )
            )
        return models

    except (httpx.HTTPError, httpx.TimeoutException, Exception) as exc:
        logger.debug("Ollama discovery skipped: %s", exc)
        return []


# ---------------------------------------------------------------------------
# Route
# ---------------------------------------------------------------------------


@router.get("/models", response_model=ModelsResponse)
async def list_models() -> ModelsResponse:
    """Return all available models (configured + discovered Ollama)."""
    assert _config is not None, "Configuration not loaded"

    configured = []
    for m in _config.models:
        pricing_info = _config.pricing.get(m.model_id) or _config.pricing.get(m.alias)
        pricing_dict = {
            "input_per_million": pricing_info.input_per_million_tokens if pricing_info else 0.0,
            "output_per_million": pricing_info.output_per_million_tokens if pricing_info else 0.0
        }
        configured.append(
            ModelInfo(
                alias=m.alias,
                provider=m.provider,
                model_id=m.model_id,
                context_window=m.context_window,
                pricing=pricing_dict
            )
        )

    ollama_models = await _discover_ollama_models()

    # Deduplicate: configured models take priority over discovered ones
    seen_aliases = {m.alias for m in configured}
    for om in ollama_models:
        if om.alias not in seen_aliases:
            configured.append(om)
            seen_aliases.add(om.alias)

    return ModelsResponse(models=configured)
