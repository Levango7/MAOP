"""Response models for ``/api/agent-versions/*`` — the endpoints the dashboard calls.

The producer is :mod:`maop.dashboard.services.agent_versions_service`: version
rows go through ``_version_row_to_dict`` and canary rows through
``_canary_row_to_dict``, both of which only decode the JSON columns of the
column lists pinned in ``agent_versions_helpers`` (``_AGENT_VERSION_COLUMNS`` /
``_CANARY_COLUMNS``). The models below mirror those key sets one-for-one: a
FastAPI ``response_model`` **filters** unknown keys, so a field missed here
would silently disappear from the wire.

``activated_at`` / ``retired_at`` / ``duration_seconds`` / ``ended_at`` are
nullable in SQLite and therefore optional here.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class AgentVersionView(BaseModel):
    """One ``agent_versions`` row (``_version_row_to_dict``)."""

    version_id: str
    agent_id: str
    version_number: str
    config_snapshot: dict[str, Any] = Field(default_factory=dict)
    changelog: str = ""
    status: str = ""
    created_by: str = ""
    tenant_id: str = ""
    created_at: float = 0.0
    updated_at: float = 0.0
    activated_at: float | None = None
    retired_at: float | None = None


class CanaryView(BaseModel):
    """One ``agent_version_canaries`` row (``_canary_row_to_dict``)."""

    canary_id: str
    version_id: str
    percentage: int = 0
    target_user_groups: list[str] = Field(default_factory=list)
    strategy: str = ""
    status: str = ""
    duration_seconds: int | None = None
    started_at: float = 0.0
    ended_at: float | None = None
    created_by: str = ""


class VersionListMeta(BaseModel):
    """Pagination metadata of ``list_versions``."""

    total: int
    page: int
    page_size: int


class AgentVersionListEnvelope(BaseModel):
    """``GET /api/agent-versions`` — ``{"status": "ok", **result}``."""

    status: str = "ok"
    data: list[AgentVersionView]
    meta: VersionListMeta


class AgentVersionEnvelope(BaseModel):
    """``POST /{version_id}/activate`` — the activated version."""

    status: str = "ok"
    data: AgentVersionView


class CanaryEnvelope(BaseModel):
    """``POST /{version_id}/canary`` — the created canary config."""

    status: str = "ok"
    data: CanaryView


class RollbackMeta(BaseModel):
    """Who was rolled back to what, and why."""

    rolled_back_from: str
    rolled_back_to: str
    reason: str = ""


class RollbackEnvelope(BaseModel):
    """``POST /{version_id}/rollback`` — target version + rollback metadata."""

    status: str = "ok"
    data: AgentVersionView
    meta: RollbackMeta
