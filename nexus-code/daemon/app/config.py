"""Daemon configuration loader.

Reads daemon_config.yaml and exposes every value as a typed, validated
Pydantic model. Loaded once at startup and shared as a singleton.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field, field_validator

# ---------------------------------------------------------------------------
# Resolve config directory relative to this file
# ---------------------------------------------------------------------------

_CONFIG_DIR = Path(__file__).resolve().parent.parent / "config"


# ---------------------------------------------------------------------------
# Section models
# ---------------------------------------------------------------------------


class CacheConfig(BaseModel):
    l1_similarity_threshold: float = Field(ge=0.0, le=1.0, default=0.95)
    l2_similarity_threshold: float = Field(ge=0.0, le=1.0, default=0.88)
    l1_max_entries_per_session: int = Field(ge=1, default=500)
    l2_max_entries_per_workspace: int = Field(ge=1, default=5000)
    l2_ttl_days: int = Field(ge=1, default=7)


class MemoryConfig(BaseModel):
    max_nodes_per_partition: int = Field(ge=1, default=500)
    retrieval_seed_k: int = Field(ge=1, default=15)
    retrieval_top_n: int = Field(ge=1, default=8)
    graph_traversal_depth: int = Field(ge=1, default=2)


class CompressionConfig(BaseModel):
    system_prompt_rate: float = Field(ge=0.0, le=1.0, default=0.15)
    memory_rate: float = Field(ge=0.0, le=1.0, default=0.60)
    code_context_rate: float = Field(ge=0.0, le=1.0, default=0.40)
    code_context_heavy_rate: float = Field(ge=0.0, le=1.0, default=0.60)
    meta_context_rate: float = Field(ge=0.0, le=1.0, default=0.50)
    summary_rate: float = Field(ge=0.0, le=1.0, default=0.35)


class ContextOptimizerConfig(BaseModel):
    tab_relevance_min: float = Field(ge=0.0, le=1.0, default=0.30)
    active_file_window_lines_before: int = Field(ge=0, default=100)
    active_file_window_lines_after: int = Field(ge=0, default=150)


class ToolLoopConfig(BaseModel):
    max_iterations: int = Field(ge=1, default=10)


class SessionConfig(BaseModel):
    ttl_hours: int = Field(ge=1, default=24)
    eviction_check_interval_minutes: int = Field(ge=1, default=60)
    sqlite_flush_interval_seconds: int = Field(ge=1, default=60)


class LiteLLMConfig(BaseModel):
    retry_max_attempts: int = Field(ge=0, default=3)
    retry_initial_delay_seconds: float = Field(ge=0.0, default=1.0)
    fallback_chain: list[str] = Field(default_factory=lambda: ["gpt-4o-mini", "claude-haiku-3-5"])


class ModelEntry(BaseModel):
    alias: str = Field(min_length=1)
    provider: str = Field(min_length=1)
    model_id: str = Field(min_length=1)
    context_window: int = Field(ge=1)

    @field_validator("alias")
    @classmethod
    def _no_whitespace_in_alias(cls, value: str) -> str:
        if " " in value:
            raise ValueError("Model alias must not contain whitespace")
        return value


class ModelPricing(BaseModel):
    input_per_million_tokens: float = Field(ge=0.0)
    output_per_million_tokens: float = Field(ge=0.0)


# ---------------------------------------------------------------------------
# Top-level config
# ---------------------------------------------------------------------------


class DaemonConfig(BaseModel):
    """Root configuration object for the Nexus-Code daemon."""

    cache: CacheConfig = Field(default_factory=CacheConfig)
    memory: MemoryConfig = Field(default_factory=MemoryConfig)
    compression: CompressionConfig = Field(default_factory=CompressionConfig)
    context_optimizer: ContextOptimizerConfig = Field(default_factory=ContextOptimizerConfig)
    tool_loop: ToolLoopConfig = Field(default_factory=ToolLoopConfig)
    session: SessionConfig = Field(default_factory=SessionConfig)
    litellm: LiteLLMConfig = Field(default_factory=LiteLLMConfig)
    models: list[ModelEntry] = Field(default_factory=list)
    pricing: dict[str, ModelPricing] = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# Loader
# ---------------------------------------------------------------------------


def load_config(config_dir: Path | None = None) -> DaemonConfig:
    """Load and validate daemon configuration from YAML + JSON files.

    Parameters
    ----------
    config_dir:
        Directory containing ``daemon_config.yaml`` and ``pricing.json``.
        Defaults to the ``config/`` directory adjacent to the ``app/`` package.

    Returns
    -------
    DaemonConfig
        Fully validated configuration singleton.
    """
    cfg_dir = config_dir or _CONFIG_DIR

    # -- Load main YAML config -----------------------------------------------
    yaml_path = cfg_dir / "daemon_config.yaml"
    raw: dict[str, Any] = {}
    if yaml_path.is_file():
        with open(yaml_path, "r", encoding="utf-8") as fh:
            raw = yaml.safe_load(fh) or {}

    # -- Load pricing JSON ----------------------------------------------------
    pricing_path = cfg_dir / "pricing.json"
    pricing_raw: dict[str, Any] = {}
    if pricing_path.is_file():
        try:
            with open(pricing_path, "r", encoding="utf-8") as fh:
                pricing_data = json.load(fh)
                pricing_raw = pricing_data.get("models", pricing_data)
        except json.JSONDecodeError as e:
            import logging
            logging.getLogger(__name__).warning(f"pricing.json is malformed: {e}. Using default pricing.")

    raw["pricing"] = pricing_raw

    return DaemonConfig.model_validate(raw)


def load_system_prompt(config_dir: Path | None = None) -> str:
    """Load and concatenate all system prompt sections from YAML.

    Returns a single string suitable for use as the LLM system message.
    """
    cfg_dir = config_dir or _CONFIG_DIR
    prompt_path = cfg_dir / "system_prompt.yaml"

    if not prompt_path.is_file():
        raise FileNotFoundError(
            f"System prompt file not found: {prompt_path}. "
            "This file is required for daemon operation."
        )

    with open(prompt_path, "r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}

    sections = [
        data.get("identity", ""),
        data.get("capabilities", ""),
        data.get("instructions", ""),
        data.get("tool_usage", ""),
        data.get("output_format", ""),
    ]

    return "\n".join(section.strip() for section in sections if section.strip())


def load_tool_definitions(config_dir: Path | None = None) -> list[dict[str, Any]]:
    """Load tool definitions from the tools YAML file.

    Returns the list of tool definition dicts ready for prompt injection
    or serving via the ``/v1/tools`` endpoint.
    """
    cfg_dir = config_dir or _CONFIG_DIR
    tools_path = cfg_dir / "tools.yaml"

    if not tools_path.is_file():
        raise FileNotFoundError(
            f"Tools definition file not found: {tools_path}. "
            "This file is required for daemon operation."
        )

    with open(tools_path, "r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}

    return data.get("tools", [])
