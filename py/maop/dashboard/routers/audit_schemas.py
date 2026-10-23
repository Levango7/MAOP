"""Response models for ``/api/audit/*`` — the 7 endpoints the dashboard calls.

Two things shape these envelopes:

* **Editions.** ``observability_service`` forks every read on
  ``has_feature(FeatureFlag.AUDIT_LOG)``, and the two producers spelled the
  same facts differently (personal ``level``/``target`` vs enterprise
  ``severity``/``resource``, personal summary ``total`` vs enterprise
  ``total_events``). The service now emits one vocabulary and marks what the
  personal log cannot record as ``None`` — "not recorded", which is not the
  same claim as ``""``. ``Audit.vue`` used to paper over the split with
  ``e.level || e.severity`` chains, and that is exactly what let a
  wrong-vocabulary mock pass.
* **The error path bypasses serialization.** ``audit.py`` uses bare
  ``@handle_api_errors``: failures become ``ErrorSchema`` ``JSONResponse``s
  (``error_handler.error_response``), so no fallback dict ever reaches these
  models and ``status`` has no default.

The alert-rule and alert-history models mirror
``maop.enterprise.audit_enhanced`` (``AuditAlertRule`` / ``AuditAlert``); the
import stays inside the test that checks them, because MAOP must import this
module without the enterprise overlay.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class AuditEventOut(BaseModel):
    """One event row, unified across editions.

    ``detail`` is a ``str`` in the enterprise model and a ``dict`` in the
    personal one, so it stays ``Any`` rather than being narrowed to a lie.
    """

    event_id: str
    timestamp: float
    action: str
    severity: str
    actor: str
    resource: str
    detail: Any = None
    trace_id: str | None = None
    tenant_id: str | None = None
    result: str | None = None
    ip_address: str | None = None
    user_agent: str | None = None
    metadata: dict[str, Any] | None = None
    risk_level: str | None = None
    category: str | None = None
    tags: list[str] | None = None


class EventsEnvelope(BaseModel):
    status: str
    events: list[AuditEventOut] = []
    count: int = 0
    total: int = 0
    limit: int = 100
    offset: int = 0


class AuditSummaryOut(BaseModel):
    """``None`` means the edition does not compute that aggregation."""

    total_events: int
    by_action: dict[str, int] = Field(default_factory=dict)
    by_actor: dict[str, int] | None = None
    by_risk_level: dict[str, int] | None = None
    by_category: dict[str, int] | None = None
    critical_count: int | None = None
    hours: int | None = None


class SummaryEnvelope(BaseModel):
    status: str
    summary: AuditSummaryOut


class AlertRuleOut(BaseModel):
    """``AuditAlertRule.model_dump(mode="json")`` — 13 keys, all always present."""

    rule_id: str
    name: str
    description: str = ""
    enabled: bool = True
    condition_type: str
    condition: dict[str, Any] = Field(default_factory=dict)
    action: str
    action_config: dict[str, Any] = Field(default_factory=dict)
    severity: str
    tenant_id: str = ""
    created_at: float = 0.0
    updated_at: float = 0.0
    created_by: str = ""


class AlertRulesEnvelope(BaseModel):
    status: str
    rules: list[AlertRuleOut] = []
    count: int = 0


class AlertRuleEnvelope(BaseModel):
    """POST / PUT / GET-by-id all answer with the single rule."""

    status: str
    rule: AlertRuleOut


class AlertDeletedEnvelope(BaseModel):
    status: str
    deleted: str


class AuditAlertOut(BaseModel):
    """``AuditAlert`` — a triggered alert.

    ``rule_name`` / ``event_actor`` exist only inside ``metadata``, and only for
    engine-built alerts, so callers must not expect them as top-level keys.
    """

    alert_id: str
    rule_id: str
    triggered_at: float
    event_id: str = ""
    severity: str = "warning"
    message: str = ""
    acknowledged: bool = False
    acknowledged_by: str = ""
    acknowledged_at: float | None = None
    tenant_id: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)


class AlertsEnvelope(BaseModel):
    status: str
    alerts: list[AuditAlertOut] = []
    count: int = 0
