"""Pydantic v2 models for the NexusPayload request contract.

Every field mirrors the shared JSON Schema at shared/nexus_schema.json.
Custom validators enforce cross-field business rules.
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field, field_validator, model_validator

# ---------------------------------------------------------------------------
# Nested sub-models
# ---------------------------------------------------------------------------

_UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
    re.IGNORECASE,
)


class CursorPosition(BaseModel):
    """Line/column position inside a text document."""

    line: int = Field(ge=0)
    column: int = Field(ge=0)


class ActiveFile(BaseModel):
    """Full snapshot of the currently focused editor tab."""

    path: str = Field(min_length=1)
    language_id: str = Field(min_length=1)
    content: str
    cursor_position: CursorPosition


class Selection(BaseModel):
    """Highlighted text range, if present."""

    text: str
    start_line: int = Field(ge=0)
    end_line: int = Field(ge=0)
    start_column: int = Field(ge=0)
    end_column: int = Field(ge=0)


class OpenTab(BaseModel):
    """Snapshot of a single open editor tab (excluding the active file)."""

    path: str = Field(min_length=1)
    language_id: str = Field(min_length=1)
    content: str


class DiagnosticRange(BaseModel):
    """Start/end positions for a diagnostic annotation."""

    start: CursorPosition
    end: CursorPosition


class Diagnostic(BaseModel):
    """A single VS Code diagnostic entry for the active file."""

    message: str
    severity: Literal["error", "warning", "information", "hint"]
    range: DiagnosticRange
    source: str


class ContextBundle(BaseModel):
    """All editor state captured at the moment the user sent their message."""

    active_file: ActiveFile
    selection: Selection | None
    open_tabs: list[OpenTab] = Field(max_length=10)
    workspace_structure: dict
    git_diff: str
    diagnostics: list[Diagnostic] = Field(max_length=50)
    terminal_snapshot: str | None
    pre_compression_token_estimate: int = Field(ge=0)
    heavy_context_flag: bool


class ToolResult(BaseModel):
    """Result of a single tool execution returned by the Extension Host."""

    tool_call_id: str = Field(min_length=1)
    tool_name: str = Field(min_length=1)
    output: str
    is_error: bool


# ---------------------------------------------------------------------------
# Top-level payload
# ---------------------------------------------------------------------------


class NexusPayload(BaseModel):
    """Master request contract sent from the VS Code Extension Host to the daemon.

    This is the single standardized structure that crosses the localhost:8000
    boundary on every user interaction.
    """

    session_id: str
    request_id: str
    timestamp: str
    model_alias: str = Field(min_length=1)
    stream: bool
    user_message: str
    provider_key_alias: str = Field(min_length=1)
    context_bundle: ContextBundle
    history_ref: str
    tool_results: list[ToolResult] | None

    # -- Validators ----------------------------------------------------------

    @field_validator("session_id", "request_id", "history_ref")
    @classmethod
    def _validate_uuid(cls, value: str) -> str:
        """Ensure the value is a well-formed UUID string."""
        try:
            UUID(value, version=4)
        except ValueError:
            if not _UUID_RE.match(value):
                raise ValueError(f"'{value}' is not a valid UUID")
        return value

    @field_validator("timestamp")
    @classmethod
    def _validate_iso8601(cls, value: str) -> str:
        """Ensure the timestamp is a valid ISO 8601 datetime string."""
        try:
            datetime.fromisoformat(value.replace("Z", "+00:00"))
        except (ValueError, TypeError) as exc:
            raise ValueError(f"'{value}' is not a valid ISO 8601 timestamp") from exc
        return value

    @model_validator(mode="after")
    def _validate_tool_result_message_rule(self) -> "NexusPayload":
        """If tool_results is present, user_message must be an empty string."""
        if self.tool_results is not None and self.user_message != "":
            raise ValueError(
                "user_message must be an empty string when tool_results is provided"
            )
        return self
