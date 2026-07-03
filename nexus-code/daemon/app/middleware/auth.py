"""Request-scoped API key extraction middleware.

Extracts the raw API key from the Authorization Bearer header and stores
it in a ``contextvars.ContextVar`` scoped to the current request.  The key
is automatically cleared when the request completes.

The daemon never logs, persists, or retains the key beyond the request.
"""

from __future__ import annotations

import contextvars
from typing import Final

from fastapi import HTTPException, Request
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.responses import Response

# ---------------------------------------------------------------------------
# Context variable holding the API key for the current request only
# ---------------------------------------------------------------------------

import os
import secrets

_api_key_var: Final[contextvars.ContextVar[str | None]] = contextvars.ContextVar(
    "nexus_api_key", default=None
)

def _get_shared_secret() -> str:
    secret = os.environ.get("NEXUS_DAEMON_SECRET")
    if not secret:
        raise RuntimeError("NEXUS_DAEMON_SECRET environment variable is not set. Daemon lifecycle is broken.")
    return secret

def get_api_key() -> str | None:
    """Retrieve the API key for the current request scope."""
    return _api_key_var.get()

def require_api_key() -> str:
    """Retrieve the API key, raising 401 if absent."""
    key = _api_key_var.get()
    if key is None:
        raise HTTPException(
            status_code=401,
            detail="Missing or invalid Authorization header. "
            "Provide a Bearer token with your API key.",
        )
    return key


# ---------------------------------------------------------------------------
# Middleware
# ---------------------------------------------------------------------------


class APIKeyMiddleware(BaseHTTPMiddleware):
    """Extract ``Authorization: Bearer <key>`` into a request-scoped variable."""

    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        
        # Daemon Security Validation
        # Exclude health check from secret validation? Actually, the prompt says
        # "Fail loudly on invalid secret/foreign process conditions."
        # We enforce X-Nexus-Secret on all requests.
        provided_secret = request.headers.get("x-nexus-secret")
        expected_secret = _get_shared_secret()
        
        if not provided_secret or not secrets.compare_digest(provided_secret, expected_secret):
            return Response(content="Unauthorized: Invalid or missing X-Nexus-Secret", status_code=401)

        auth_header: str | None = request.headers.get("authorization")
        token: str | None = None

        if auth_header and auth_header.lower().startswith("bearer "):
            token = auth_header[7:].strip()

        reset_token = _api_key_var.set(token)
        try:
            response = await call_next(request)
        finally:
            _api_key_var.reset(reset_token)

        return response
