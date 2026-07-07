"""Nexus-Code Optimization Daemon — FastAPI application factory.

This is the entry point for the local Python daemon that runs on
``localhost:8000``.  It owns all optimization logic (caching, memory,
compression, routing) and is the only process that calls LLM providers.

Startup sequence:
1. Load configuration from YAML/JSON files
2. Load system prompt and tool definitions
3. Initialize service singletons (placeholders in Phase 1)
4. Register middleware and routes
5. Bind to port and begin serving
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import DaemonConfig, load_config, load_system_prompt, load_tool_definitions
from app.middleware.auth import APIKeyMiddleware
from app.routes import chat, health, models

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("nexus-code")

# ---------------------------------------------------------------------------
# Application state (populated during lifespan)
# ---------------------------------------------------------------------------

_state: dict[str, Any] = {}


def get_config() -> DaemonConfig:
    """Retrieve the loaded daemon configuration singleton."""
    return _state["config"]


def get_system_prompt() -> str:
    """Retrieve the loaded system prompt string."""
    return _state["system_prompt"]


def get_tool_definitions() -> list[dict[str, Any]]:
    """Retrieve the loaded tool definition list."""
    return _state["tool_definitions"]


# ---------------------------------------------------------------------------
# Lifespan
# ---------------------------------------------------------------------------


@asynccontextmanager
async def lifespan(application: FastAPI) -> AsyncGenerator[None, None]:
    """Application lifespan handler — startup and shutdown logic."""

    # -- STARTUP -------------------------------------------------------------
    logger.info("Starting Nexus-Code daemon...")

    import sys
    # 1. Load configuration
    try:
        config = load_config()
        _state["config"] = config
        logger.info(
            "Configuration loaded — %d models configured",
            len(config.models),
        )
    except Exception as e:
        logger.error(f"FATAL: Configuration validation failed: {e}")
        sys.exit(1)

    # 2. Load system prompt
    try:
        system_prompt = load_system_prompt()
        _state["system_prompt"] = system_prompt
        logger.info("System prompt loaded (%d chars)", len(system_prompt))
    except FileNotFoundError as e:
        logger.error(f"FATAL: {e}")
        sys.exit(1)

    # 3. Load tool definitions
    try:
        tool_defs = load_tool_definitions()
        _state["tool_definitions"] = tool_defs
        logger.info("Tool definitions loaded — %d tools", len(tool_defs))
    except FileNotFoundError as e:
        logger.error(f"FATAL: {e}")
        sys.exit(1)

    # 4. Inject config into routes that need it
    models.set_config(config)

    # 5. Initialize service singletons
    #    - Embedding service (optional — requires sentence-transformers)
    from app.services.embedding import embedding_service
    try:
        await asyncio.get_running_loop().run_in_executor(None, embedding_service.load_model)
        _state["embedding_service"] = embedding_service
        logger.info("Embedding service initialized")
    except Exception as e:
        logger.warning(f"Embedding service unavailable (heuristic-only mode): {e}")
        _state["embedding_service"] = embedding_service  # still store it — it's a no-op stub

    #    - Semantic cache (L1+L2)  → Phase 4
    from app.services.cache import SemanticCache
    semantic_cache = SemanticCache(config)
    await semantic_cache.load_l2("sessions.sqlite")
    _state["semantic_cache"] = semantic_cache

    #    - Session manager         → Phase 4
    from app.services.session import SessionManager
    session_manager = SessionManager(semantic_cache, config)
    await session_manager.load_from_sqlite("sessions.sqlite")
    _state["session_manager"] = session_manager

    #    - Memory graph            → Phase 5
    from app.services.memory_graph import MemoryGraph
    memory_graph = MemoryGraph(embedding_service, config)
    await memory_graph.load_from_sqlite("sessions.sqlite")
    _state["memory_graph"] = memory_graph

    #    - Context optimizer       → Phase 5
    from app.services.context_optimizer import ContextOptimizer
    context_optimizer = ContextOptimizer(embedding_service, config)
    _state["context_optimizer"] = context_optimizer

    #    - Prompt assembler        → Phase 5
    from app.services.prompt_assembler import PromptAssembler
    prompt_assembler = PromptAssembler(config)
    _state["prompt_assembler"] = prompt_assembler

    #    - LLMLingua compressor    → Phase 6
    from app.services.compressor import PromptCompressor
    prompt_compressor = PromptCompressor(config)
    _state["prompt_compressor"] = prompt_compressor

    #    - LiteLLM router          → Phase 6
    from app.services.router import LLMRouter
    llm_router = LLMRouter(config)
    _state["llm_router"] = llm_router

    from app.services.summary_updater import SummaryUpdater
    summary_updater = SummaryUpdater()
    _state["summary_updater"] = summary_updater

    from app.services.response_processor import ResponseProcessor
    response_processor = ResponseProcessor(llm_router, summary_updater)
    _state["response_processor"] = response_processor
    
    # Start background tasks
    persist_task = asyncio.create_task(session_manager.persist_to_sqlite_loop())
    evict_task = asyncio.create_task(session_manager.evict_expired_loop())
    _state["bg_tasks"] = [persist_task, evict_task]
    
    logger.info("Service singletons initialized (Phase 5 active)")

    logger.info("Nexus-Code daemon ready")

    yield

    # -- SHUTDOWN ------------------------------------------------------------
    logger.info("Shutting down Nexus-Code daemon...")

    # Persist session state to SQLite            → Phase 4
    await session_manager.persist_to_sqlite()
    # Persist L2 cache to SQLite                 → Phase 4
    await semantic_cache.persist_l2("sessions.sqlite")
    # Persist Memory Graph to SQLite             → Phase 5
    await memory_graph.save_to_sqlite("sessions.sqlite")
    
    # Cancel background tasks                    → Phase 4
    for task in _state.get("bg_tasks", []):
        task.cancel()

    # Unload models                              → Phase 6

    logger.info("Nexus-Code daemon stopped")


# ---------------------------------------------------------------------------
# App factory
# ---------------------------------------------------------------------------


def create_app() -> FastAPI:
    """Create and configure the FastAPI application instance."""
    application = FastAPI(
        title="Nexus-Code Optimization Daemon",
        description=(
            "Local optimization daemon for the Nexus-Code VS Code extension. "
            "Handles semantic caching, memory graph, prompt compression, "
            "and unified LLM routing."
        ),
        version="1.0.0",
        lifespan=lifespan,
    )

    # -- CORS (localhost only) -----------------------------------------------
    application.add_middleware(
        CORSMiddleware,
        allow_origins=[
            "http://localhost",
            "http://localhost:*",
            "http://127.0.0.1",
            "http://127.0.0.1:*",
            "vscode-webview://*",
        ],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # -- API key extraction middleware ----------------------------------------
    application.add_middleware(APIKeyMiddleware)

    # -- Routes ---------------------------------------------------------------
    application.include_router(health.router)
    application.include_router(models.router)
    application.include_router(chat.router)

    # -- Tools endpoint (serves tool definitions) ----------------------------
    @application.get("/v1/tools")
    async def get_tools() -> dict[str, list[dict[str, Any]]]:
        """Return tool definitions for the Extension Host's Tool Executor."""
        return {"tools": get_tool_definitions()}

    return application


# ---------------------------------------------------------------------------
# Module-level app instance (for uvicorn)
# ---------------------------------------------------------------------------

app = create_app()

if __name__ == "__main__":
    import os
    import sys
    import json
    import socket
    import secrets
    import psutil
    from pathlib import Path
    import uvicorn

    lockfile_path = Path.home() / ".nexus-code" / "daemon.lock"

    # Check for existing daemon
    if lockfile_path.exists():
        try:
            with open(lockfile_path, "r") as f:
                data = json.load(f)
            pid = data.get("pid")
            if pid and psutil.pid_exists(pid):
                process = psutil.Process(pid)
                if "python" in process.name().lower() or "uvicorn" in process.name().lower():
                    print(f"Daemon already running on pid {pid}")
                    sys.exit(0)
        except Exception:
            # Stale or corrupted lockfile
            pass

    # Find available port
    selected_port = None
    for port in range(8000, 8011):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind(("127.0.0.1", port))
                selected_port = port
                break
            except OSError:
                continue
    
    if selected_port is None:
        print("No available port found in range 8000-8010", file=sys.stderr)
        sys.exit(1)

    # Generate secret
    secret = secrets.token_hex(32)
    os.environ["NEXUS_DAEMON_SECRET"] = secret

    # Write lockfile
    lockfile_path.parent.mkdir(parents=True, exist_ok=True)
    
    if not lockfile_path.exists():
        lockfile_path.touch(mode=0o600)
    else:
        lockfile_path.chmod(0o600)
        
    with open(lockfile_path, "w") as f:
        json.dump({
            "pid": os.getpid(),
            "port": selected_port,
            "secret": secret
        }, f)

    try:
        # Run uvicorn programmatically
        uvicorn.run("app.main:app", host="127.0.0.1", port=selected_port, workers=1)
    finally:
        if lockfile_path.exists():
            try:
                lockfile_path.unlink()
            except OSError:
                pass
