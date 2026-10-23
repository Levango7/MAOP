"""Response models for ``/api/model-gateway/*`` — the 8 endpoints the dashboard calls.

The domain models (``ModelPermission``, ``ModelGatewayConfig``,
``ModelAccessDecision``) are reused verbatim instead of being re-declared: they
are already Pydantic models, and ``model_dump()`` of a domain model is exactly
what the service puts on the wire, so a second hand-written copy would drift.

Note on the ``status`` defaults: every success path is built as
``{"status": "ok", **service_result}`` while all
``@handle_api_errors(error_value=...)`` fallbacks here carry
``{"status": "error", "error": "..."}`` — so ``"error"`` is the truthful default
for a branch that omits the key entirely.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from maop.core.agent.llm_chat.model_gateway import (
    ModelAccessDecision,
    ModelGatewayConfig,
    ModelPermission,
)


class PermissionListEnvelope(BaseModel):
    status: str = "error"
    permissions: list[ModelPermission] = []
    count: int = 0
    error: str = ""


class PermissionWriteEnvelope(BaseModel):
    """POST / DELETE ``/api/model-gateway/permissions`` — echo of the pattern."""

    status: str = "error"
    model_pattern: str = ""
    error: str = ""


class PermissionUpdateEnvelope(BaseModel):
    #: ``renamed_from`` only exists on the update path (the service deletes the
    #: old rule when the body's pattern differs from the URL's).
    status: str = "error"
    model_pattern: str = ""
    renamed_from: str = ""
    error: str = ""


class CheckEnvelope(BaseModel):
    """``decision`` is a ``ModelAccessDecision`` dump — ``None`` on the error branch.

    The UI must not read ``allowed`` off the envelope: the 2026-10 batch found
    ``AgentGateway.vue`` doing exactly that, so the panel rendered "denied" for
    every check because the envelope only carries ``status``.
    """

    status: str = "error"
    decision: ModelAccessDecision | None = None
    error: str = ""


class UsageRecordEnvelope(BaseModel):
    status: str = "error"
    model: str = ""
    tokens: int = 0
    error: str = ""


class UsageRow(BaseModel):
    """One ``(model, agent)`` pair of today's usage.

    ``agent`` is stored per row in SQLite (``model_gateway_usage`` has a
    ``(date, model, agent)`` primary key), but ``get_daily_usage()`` collapses it
    into a ``{model: tokens}`` map — which left the dashboard's "Agent" column
    permanently blank. ``ModelGateway.get_daily_usage_rows()`` keeps the
    breakdown so the column reads a real value.
    """

    model: str = ""
    agent: str = ""
    tokens: int = 0


class UsageGetEnvelope(BaseModel):
    status: str = "error"
    usage: dict[str, int] = Field(default_factory=dict)
    total_tokens: int = 0
    rows: list[UsageRow] = []
    error: str = ""


class UsageClearEnvelope(BaseModel):
    status: str = "error"
    cleared: int = 0
    model: str = ""
    error: str = ""


class GatewayConfigEnvelope(BaseModel):
    """Shared by ``GET`` and ``PUT /api/model-gateway/config``.

    PUT now returns the effective config because it is a *merge*: the client
    cannot know which fields it left untouched, and the previous
    ``{"status": "ok"}`` reply forced the form to stay uninited.
    """

    status: str = "error"
    config: ModelGatewayConfig | None = None
    error: str = ""
