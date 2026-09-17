"""Audit router — unified endpoint for both enterprise and personal editions.

Enterprise edition: uses EnterpriseAuditLogger (maop.enterprise.audit) with
  tenant filtering, severity levels, and pagination.

Personal edition: uses AuditLog (maop.control.audit) reading from
  logs/audit.jsonl with basic filtering.

Both editions expose the same API surface: /api/audit/events, /api/audit/summary,
  /api/audit/filter.

Enhancement (audit-enhancement PRD):
  - /api/audit/events/advanced  — multi-field filtering + pagination + sort
  - /api/audit/export           — CSV / JSON export
  - /api/audit/stats            — aggregate statistics
  - /api/audit/timeline         — bucketed time series
  - /api/audit/heatmap          — 7×24 day×hour heatmap
  - /api/audit/alert/rules      — CRUD for alert rules
  - /api/audit/alert/history    — triggered-alert history
  - /api/audit/alert/{id}/ack   — acknowledge an alert
  - /api/audit/alert/evaluate   — manually evaluate recent events against rules
  - WebSocket push on alert trigger (via dashboard server's _ws_broadcast)

Business logic lives in :mod:`maop.dashboard.services.observability_service`;
this router only does request parsing, auth, service dispatch, and
response formatting.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import PlainTextResponse, Response

from maop.core.security.middleware import require_admin
from maop.dashboard.error_handler import handle_api_errors
from maop.dashboard.services import observability_service

# 修复12: 提前 import Pydantic 模型用于端点参数类型注解，让 FastAPI 自动校验请求体。
# 个人版无 enterprise 模块时回退到宽松 BaseModel，避免 ImportError 阻断 router 加载。
try:
    from maop.enterprise.audit_enhanced import AuditAlertRuleCreate, AuditAlertRuleUpdate, AuditEventQuery
except ImportError:  # pragma: no cover — personal edition
    from pydantic import BaseModel as _BaseModel

    class AuditAlertRuleCreate(_BaseModel):
        model_config = {"extra": "allow"}

    class AuditAlertRuleUpdate(_BaseModel):
        model_config = {"extra": "allow"}

    class AuditEventQuery(_BaseModel):
        # 宽松回退：允许任意字段，由函数内部再校验
        model_config = {"extra": "allow"}

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/audit", tags=["audit"])


# ── Endpoints (legacy) ────────────────────────────────────────────


@router.get("/events")
@handle_api_errors
async def list_events(
    request: Request,
    tenant_id: str = "",
    action: str = "",
    severity: str = "",
    hours: int = 24,
    limit: int = 100,
    offset: int = 0,
) -> dict[str, Any]:
    """List audit events with optional filters (unified for both editions)."""
    require_admin(request)
    return await observability_service.list_audit_events(
        tenant_id=tenant_id, action=action, severity=severity,
        hours=hours, limit=limit, offset=offset,
    )


@router.get("/summary")
@handle_api_errors
async def get_summary(
    request: Request,
    tenant_id: str = "",
    hours: int = 24,
) -> dict[str, Any]:
    """Get audit event summary (unified for both editions)."""
    require_admin(request)
    return await observability_service.get_audit_summary(tenant_id=tenant_id, hours=hours)


@router.get("/filter")
@handle_api_errors
async def filter_events(
    request: Request,
    action: str = "",
    actor: str = "",
    target: str = "",
    limit: int = 100,
) -> dict[str, Any]:
    """Filter audit events by action/actor/target (unified for both editions)."""
    require_admin(request)
    return await observability_service.filter_audit_events(
        action=action, actor=actor, target=target, limit=limit,
    )


# ── Enhancement endpoints (enterprise-only) ──────────────────────
#
# All enhanced endpoints require FeatureFlag.AUDIT_LOG. In personal
# edition they return 404 via the enterprise_api_guard middleware or
# raise HTTPException(404) here for direct router mounts.


@router.post("/events/advanced")
@handle_api_errors
async def advanced_query(request: Request, body: AuditEventQuery) -> dict[str, Any]:
    """Advanced multi-field filtering with pagination and sort."""
    require_admin(request)
    return await observability_service.advanced_query_audit_events(body)


@router.get("/export")
@handle_api_errors
async def export_events(
    request: Request,
    format: str = Query("csv", pattern="^(csv|json)$"),
    tenant_id: str = "",
    hours: int = Query(24, ge=1, le=87600),
    limit: int = Query(5000, ge=1, le=100000),
) -> Response:
    """Export audit events as CSV or JSON."""
    require_admin(request)
    content, media_type, filename = await observability_service.export_audit_events(
        format=format, tenant_id=tenant_id, hours=hours, limit=limit,
    )
    if format == "json":
        return Response(
            content=content,
            media_type=media_type,
            headers={"Content-Disposition": f"attachment; filename={filename}"},
        )
    return PlainTextResponse(
        content=content,
        media_type=media_type,
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


@router.get("/stats")
@handle_api_errors
async def get_stats(
    request: Request,
    tenant_id: str = "",
    hours: int = Query(24, ge=1, le=87600),
) -> dict[str, Any]:
    """Aggregate statistics: counts by action / severity / risk / category / actor."""
    require_admin(request)
    return await observability_service.get_audit_stats(tenant_id=tenant_id, hours=hours)


@router.get("/timeline")
@handle_api_errors
async def get_timeline(
    request: Request,
    tenant_id: str = "",
    hours: int = Query(24, ge=1, le=87600),
    bucket_s: int = Query(3600, ge=1, le=86400),
) -> dict[str, Any]:
    """Bucketed time series for charting."""
    require_admin(request)
    return await observability_service.get_audit_timeline(
        tenant_id=tenant_id, hours=hours, bucket_s=bucket_s,
    )


@router.get("/heatmap")
@handle_api_errors
async def get_heatmap(
    request: Request,
    tenant_id: str = "",
    hours: int = Query(168, ge=1, le=87600),  # default 1 week for day×hour pattern
) -> dict[str, Any]:
    """7×24 day×hour heatmap of event volume."""
    require_admin(request)
    return await observability_service.get_audit_heatmap(tenant_id=tenant_id, hours=hours)


# ── Alert rule CRUD ───────────────────────────────────────────────


@router.post("/alert/rules")
@handle_api_errors
async def create_alert_rule(request: Request, body: AuditAlertRuleCreate) -> dict[str, Any]:
    """Create a new alert rule."""
    require_admin(request)
    actor = getattr(request.state, "auth_identity", "") or ""
    return await observability_service.create_alert_rule(body, actor=actor)


@router.get("/alert/rules")
@handle_api_errors
async def list_alert_rules(
    request: Request,
    tenant_id: str = "",
    enabled_only: bool = False,
) -> dict[str, Any]:
    """List alert rules (optionally filtered by tenant / enabled)."""
    require_admin(request)
    return await observability_service.list_alert_rules(tenant_id=tenant_id, enabled_only=enabled_only)


@router.get("/alert/rules/{rule_id}")
@handle_api_errors
async def get_alert_rule(request: Request, rule_id: str) -> dict[str, Any]:
    """Get a single alert rule by ID."""
    require_admin(request)
    result = await observability_service.get_alert_rule(rule_id)
    if result is None:
        raise HTTPException(status_code=404, detail=f"Rule {rule_id} not found")
    return result


@router.put("/alert/rules/{rule_id}")
@handle_api_errors
async def update_alert_rule(request: Request, rule_id: str, body: AuditAlertRuleUpdate) -> dict[str, Any]:
    """Update an existing alert rule (partial update)."""
    require_admin(request)
    result = await observability_service.update_alert_rule(rule_id, body)
    if result is None:
        raise HTTPException(status_code=404, detail=f"Rule {rule_id} not found")
    return result


@router.delete("/alert/rules/{rule_id}")
@handle_api_errors
async def delete_alert_rule(request: Request, rule_id: str) -> dict[str, Any]:
    """Delete an alert rule."""
    require_admin(request)
    ok = await observability_service.delete_alert_rule(rule_id)
    if not ok:
        raise HTTPException(status_code=404, detail=f"Rule {rule_id} not found")
    return {"status": "ok", "deleted": rule_id}


# ── Alert history + acknowledgement ──────────────────────────────


@router.get("/alert/history")
@handle_api_errors
async def list_alert_history(
    request: Request,
    rule_id: str = "",
    tenant_id: str = "",
    acknowledged: bool | None = None,
    since: float = 0.0,
    limit: int = 100,
) -> dict[str, Any]:
    """List triggered-alert history."""
    require_admin(request)
    return await observability_service.list_alert_history(
        rule_id=rule_id, tenant_id=tenant_id,
        acknowledged=acknowledged, since=since, limit=limit,
    )


@router.post("/alert/{alert_id}/acknowledge")
@handle_api_errors
async def acknowledge_alert(request: Request, alert_id: str) -> dict[str, Any]:
    """Acknowledge a triggered alert."""
    require_admin(request)
    actor = getattr(request.state, "auth_identity", "") or ""
    result = await observability_service.acknowledge_alert(alert_id, actor=actor)
    if result is None:
        raise HTTPException(status_code=404, detail=f"Alert {alert_id} not found")
    return result


@router.post("/alert/evaluate")
@handle_api_errors
async def evaluate_alerts(
    request: Request,
    hours: int = 1,
    tenant_id: str = "",
) -> dict[str, Any]:
    """Manually evaluate recent audit events against all enabled rules."""
    require_admin(request)
    return await observability_service.evaluate_alerts(hours=hours, tenant_id=tenant_id)
