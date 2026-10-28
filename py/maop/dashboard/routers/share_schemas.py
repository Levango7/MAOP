"""Response models for the ``/api/share`` endpoints the dashboard calls.

``share_service`` never returns event payloads: ``create_share`` stores and
``get_share`` reads back ``run_service._summarize`` output only (task, agent,
status, duration, step count, verify verdict, cost). ``ShareSummary`` models
exactly those keys — both routes have an ``error_value`` fallback that is a
plain dict, so it IS validated by ``response_model`` and therefore needs the
``error`` field (with defaults, so the success path stays valid too).
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class ShareSummary(BaseModel):
    """``run_service._summarize`` output — the only thing a share exposes."""

    trace_id: str = ""
    task: str = ""
    agent: str = ""
    routing_key: str = ""
    status: str = ""
    success: bool | None = None
    duration_ms: int = 0
    steps: int = 0
    started_at: str = ""
    updated_at: str = ""
    verify_status: str = ""
    verify_summary: str = ""
    cost: dict[str, Any] = Field(default_factory=dict)


class ShareView(BaseModel):
    """One stored share row (``share_service.get_share``)."""

    token: str
    trace_id: str
    summary: ShareSummary
    created_at: float = 0.0
    expires_at: float = 0.0


class ShareCreateEnvelope(BaseModel):
    """``POST /api/share`` — the newly minted link."""

    status: str = "error"
    token: str = ""
    trace_id: str = ""
    path: str = ""
    created_at: float = 0.0
    expires_at: float = 0.0
    error: str = ""


class ShareEnvelope(BaseModel):
    """``GET /api/share/{token}`` — public read, token is the credential."""

    status: str = "error"
    share: ShareView | None = None
    error: str = ""
