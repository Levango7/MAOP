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
"""

from __future__ import annotations

import logging
import time as _time
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import PlainTextResponse, Response

from maop.config.edition import FeatureFlag, has_feature
from maop.core.security.middleware import require_admin
from maop.dashboard.error_handler import handle_api_errors

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/audit", tags=["audit"])

_MAOP_ROOT = Path(__file__).resolve().parent.parent.parent.parent


def _get_personal_events(
    *,
    action: str = "",
    actor: str = "",
    target: str = "",
    limit: int = 100,
) -> list[dict[str, Any]]:
    """Read audit events from personal edition AuditLog (logs/audit.jsonl)."""
    try:
        from maop.control.audit import AuditLog
        events = AuditLog(_MAOP_ROOT / "logs" / "audit.jsonl").read_recent(limit=limit * 5)
        result = []
        for e in events:
            if action and e.action != action:
                continue
            if actor and e.actor != actor:
                continue
            if target and e.target != target:
                continue
            result.append(e.model_dump())
        return result[:limit]
    except Exception as exc:
        logger.error("Personal audit events failed: %s", exc)
        return []


def _get_personal_summary() -> dict[str, Any]:
    """Get audit summary from personal edition AuditLog."""
    try:
        from maop.control.audit import AuditLog
        log = AuditLog(_MAOP_ROOT / "logs" / "audit.jsonl")
        events = log.read_recent(limit=500)
        by_action: dict[str, int] = {}
        by_actor: dict[str, int] = {}
        for e in events:
            by_action[e.action] = by_action.get(e.action, 0) + 1
            by_actor[e.actor] = by_actor.get(e.actor, 0) + 1
        return {"total": len(events), "by_action": by_action, "by_actor": by_actor}
    except Exception as exc:
        logger.error("Personal audit summary failed: %s", exc)
        return {"total": 0, "by_action": {}, "by_actor": {}}


# ── Enterprise helpers ────────────────────────────────────────────

_enterprise_logger: Any = None


def _get_enterprise_logger() -> Any:
    global _enterprise_logger
    if _enterprise_logger is None:
        from maop.enterprise.audit import EnterpriseAuditLogger
        _enterprise_logger = EnterpriseAuditLogger()
    return _enterprise_logger


def _filter_enterprise_events(
    mgr: Any,
    *,
    tenant_id: str = "",
    action: str = "",
    severity: str = "",
    hours: int = 24,
    limit: int = 100,
    offset: int = 0,
) -> tuple[list[Any], int]:
    since = _time.time() - hours * 3600
    events = [e for e in mgr._events if e.timestamp >= since]
    if tenant_id:
        events = [e for e in events if e.tenant_id == tenant_id]
    if action:
        events = [e for e in events if e.action.value == action]
    if severity:
        events = [e for e in events if e.severity.value == severity]
    total = len(events)
    return events[offset: offset + limit], total


# ── Alert engine singleton + WebSocket broadcaster ────────────────

_alert_engine: Any = None


def _ws_broadcast_alert(alert: dict[str, Any]) -> Any:
    """Push an alert to all connected dashboard WebSocket clients.

    Looks up the running server's ``_ws_broadcast`` coroutine and schedules
    it on the event loop. Falls back to no-op when the server module is not
    importable (e.g. unit tests that only mount the router).
    """
    try:
        from maop.dashboard import server as _server
        broadcast = getattr(_server, "_ws_broadcast", None)
        if broadcast is None:
            return None
        import asyncio
        loop = asyncio.get_event_loop()
        if loop.is_running():
            asyncio.ensure_future(broadcast({"type": "audit_alert", "alert": alert}))
        else:
            loop.run_until_complete(broadcast({"type": "audit_alert", "alert": alert}))
    except Exception as exc:
        logger.debug("ws_broadcast_alert skipped: %s", exc)
    return None


def _get_alert_engine() -> Any:
    """Lazy-init the singleton AuditAlertEngine (enterprise only)."""
    global _alert_engine
    if _alert_engine is None:
        from maop.enterprise.audit_enhanced import AuditAlertEngine
        _alert_engine = AuditAlertEngine(broadcaster=_ws_broadcast_alert)
    return _alert_engine


def _reset_alert_engine_for_tests() -> None:
    """Reset the singleton — used by unit tests, not by production code."""
    global _alert_engine
    _alert_engine = None


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

    if has_feature(FeatureFlag.AUDIT_LOG):
        mgr = _get_enterprise_logger()
        events, total = _filter_enterprise_events(
            mgr,
            tenant_id=tenant_id,
            action=action,
            severity=severity,
            hours=hours,
            limit=limit,
            offset=offset,
        )
        return {
            "status": "ok",
            "events": [e.model_dump() for e in events],
            "count": len(events),
            "total": total,
            "limit": limit,
            "offset": offset,
        }
    else:
        events = _get_personal_events(action=action, limit=limit)
        return {
            "status": "ok",
            "events": events,
            "count": len(events),
            "total": len(events),
            "limit": limit,
            "offset": offset,
        }


@router.get("/summary")
@handle_api_errors
async def get_summary(
    request: Request,
    tenant_id: str = "",
    hours: int = 24,
) -> dict[str, Any]:
    """Get audit event summary (unified for both editions)."""
    require_admin(request)

    if has_feature(FeatureFlag.AUDIT_LOG):
        mgr = _get_enterprise_logger()
        summary = mgr.summary(tenant_id=tenant_id, hours=hours)
        return {"status": "ok", "summary": summary}
    else:
        return {"status": "ok", "summary": _get_personal_summary()}


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

    if has_feature(FeatureFlag.AUDIT_LOG):
        mgr = _get_enterprise_logger()
        events, total = _filter_enterprise_events(
            mgr,
            action=action,
            hours=24,
            limit=limit,
        )
        return {
            "status": "ok",
            "events": [e.model_dump() for e in events],
            "count": len(events),
            "total": total,
        }
    else:
        events = _get_personal_events(action=action, actor=actor, target=target, limit=limit)
        return {
            "status": "ok",
            "events": events,
            "count": len(events),
            "total": len(events),
        }


# ── Enhancement endpoints (enterprise-only) ──────────────────────
#
# All enhanced endpoints require FeatureFlag.AUDIT_LOG. In personal
# edition they return 404 via the enterprise_api_guard middleware or
# raise HTTPException(404) here for direct router mounts.


def _require_audit_feature() -> None:
    """Raise 404 if the audit_log feature is not available."""
    if not has_feature(FeatureFlag.AUDIT_LOG):
        raise HTTPException(status_code=404, detail="Audit enhancement requires enterprise edition")


def _collect_enterprise_events(
    *,
    tenant_id: str = "",
    hours: int = 24,
    limit: int = 10000,
) -> list[Any]:
    """Return up to ``limit`` AuditEvent objects from the enterprise logger."""
    mgr = _get_enterprise_logger()
    since = _time.time() - hours * 3600
    events = [e for e in mgr._events if e.timestamp >= since]
    if tenant_id:
        events = [e for e in events if e.tenant_id == tenant_id]
    return events[-limit:]


@router.post("/events/advanced")
@handle_api_errors
async def advanced_query(request: Request, body: dict[str, Any]) -> dict[str, Any]:
    """Advanced multi-field filtering with pagination and sort.

    Request body matches ``AuditEventQuery``. Returns events + total count.
    """
    require_admin(request)
    _require_audit_feature()
    from maop.enterprise.audit_enhanced import AuditEventQuery, filter_events as _filter

    query = AuditEventQuery(**body)
    events = _collect_enterprise_events(
        tenant_id=query.tenant_id,
        hours=int(max(1, (_time.time() - query.since) // 3600)) if query.since else 24,
        limit=10000,
    )
    page, total = _filter(events, query)
    return {
        "status": "ok",
        "events": [e.model_dump(mode="json") for e in page],
        "count": len(page),
        "total": total,
        "limit": query.limit,
        "offset": query.offset,
    }


@router.get("/export")
@handle_api_errors
async def export_events(
    request: Request,
    format: str = Query("csv", pattern="^(csv|json)$"),
    tenant_id: str = "",
    hours: int = 24,
    limit: int = 5000,
) -> Response:
    """Export audit events as CSV or JSON.

    Returns a ``text/csv`` or ``application/json`` response body suitable
    for ``Blob`` download in the browser.
    """
    require_admin(request)
    _require_audit_feature()
    from maop.enterprise.audit_enhanced import export_events_csv, export_events_json

    events = _collect_enterprise_events(tenant_id=tenant_id, hours=hours, limit=limit)
    if format == "json":
        return Response(
            content=export_events_json(events),
            media_type="application/json",
            headers={"Content-Disposition": "attachment; filename=audit_events.json"},
        )
    return PlainTextResponse(
        content=export_events_csv(events),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": "attachment; filename=audit_events.csv"},
    )


@router.get("/stats")
@handle_api_errors
async def get_stats(
    request: Request,
    tenant_id: str = "",
    hours: int = 24,
) -> dict[str, Any]:
    """Aggregate statistics: counts by action / severity / risk / category / actor."""
    require_admin(request)
    _require_audit_feature()
    from maop.enterprise.audit_enhanced import compute_stats

    events = _collect_enterprise_events(tenant_id=tenant_id, hours=hours, limit=100000)
    stats = compute_stats(events, hours=hours)
    return {"status": "ok", "stats": stats.model_dump()}


@router.get("/timeline")
@handle_api_errors
async def get_timeline(
    request: Request,
    tenant_id: str = "",
    hours: int = 24,
    bucket_s: int = 3600,
) -> dict[str, Any]:
    """Bucketed time series for charting."""
    require_admin(request)
    _require_audit_feature()
    from maop.enterprise.audit_enhanced import compute_timeline

    events = _collect_enterprise_events(tenant_id=tenant_id, hours=hours, limit=100000)
    now = _time.time()
    since = now - hours * 3600
    points = compute_timeline(events, bucket_s=bucket_s, since=since, until=now)
    return {
        "status": "ok",
        "timeline": [p.model_dump() for p in points],
        "bucket_s": bucket_s,
        "hours": hours,
    }


@router.get("/heatmap")
@handle_api_errors
async def get_heatmap(
    request: Request,
    tenant_id: str = "",
    hours: int = 168,  # default 1 week for day×hour pattern
) -> dict[str, Any]:
    """7×24 day×hour heatmap of event volume."""
    require_admin(request)
    _require_audit_feature()
    from maop.enterprise.audit_enhanced import compute_heatmap

    events = _collect_enterprise_events(tenant_id=tenant_id, hours=hours, limit=100000)
    cells = compute_heatmap(events)
    return {
        "status": "ok",
        "heatmap": [c.model_dump() for c in cells],
        "hours": hours,
    }


# ── Alert rule CRUD ───────────────────────────────────────────────


@router.post("/alert/rules")
@handle_api_errors
async def create_alert_rule(request: Request, body: dict[str, Any]) -> dict[str, Any]:
    """Create a new alert rule."""
    require_admin(request)
    _require_audit_feature()
    from maop.enterprise.audit_enhanced import AuditAlertRuleCreate

    create = AuditAlertRuleCreate(**body)
    engine = _get_alert_engine()
    actor = getattr(request.state, "auth_identity", "") or ""
    rule = engine.create_rule(create, created_by=actor)
    return {"status": "ok", "rule": rule.model_dump(mode="json")}


@router.get("/alert/rules")
@handle_api_errors
async def list_alert_rules(
    request: Request,
    tenant_id: str = "",
    enabled_only: bool = False,
) -> dict[str, Any]:
    """List alert rules (optionally filtered by tenant / enabled)."""
    require_admin(request)
    _require_audit_feature()
    engine = _get_alert_engine()
    rules = engine.list_rules(tenant_id=tenant_id, enabled_only=enabled_only)
    return {
        "status": "ok",
        "rules": [r.model_dump(mode="json") for r in rules],
        "count": len(rules),
    }


@router.get("/alert/rules/{rule_id}")
@handle_api_errors
async def get_alert_rule(request: Request, rule_id: str) -> dict[str, Any]:
    """Get a single alert rule by ID."""
    require_admin(request)
    _require_audit_feature()
    engine = _get_alert_engine()
    rule = engine.get_rule(rule_id)
    if rule is None:
        raise HTTPException(status_code=404, detail=f"Rule {rule_id} not found")
    return {"status": "ok", "rule": rule.model_dump(mode="json")}


@router.put("/alert/rules/{rule_id}")
@handle_api_errors
async def update_alert_rule(request: Request, rule_id: str, body: dict[str, Any]) -> dict[str, Any]:
    """Update an existing alert rule (partial update)."""
    require_admin(request)
    _require_audit_feature()
    from maop.enterprise.audit_enhanced import AuditAlertRuleUpdate

    update = AuditAlertRuleUpdate(**body)
    engine = _get_alert_engine()
    rule = engine.update_rule(rule_id, update)
    if rule is None:
        raise HTTPException(status_code=404, detail=f"Rule {rule_id} not found")
    return {"status": "ok", "rule": rule.model_dump(mode="json")}


@router.delete("/alert/rules/{rule_id}")
@handle_api_errors
async def delete_alert_rule(request: Request, rule_id: str) -> dict[str, Any]:
    """Delete an alert rule."""
    require_admin(request)
    _require_audit_feature()
    engine = _get_alert_engine()
    ok = engine.delete_rule(rule_id)
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
    _require_audit_feature()
    engine = _get_alert_engine()
    alerts = engine.list_alerts(
        rule_id=rule_id,
        tenant_id=tenant_id,
        acknowledged=acknowledged,
        since=since,
        limit=limit,
    )
    return {
        "status": "ok",
        "alerts": [a.model_dump(mode="json") for a in alerts],
        "count": len(alerts),
    }


@router.post("/alert/{alert_id}/acknowledge")
@handle_api_errors
async def acknowledge_alert(request: Request, alert_id: str) -> dict[str, Any]:
    """Acknowledge a triggered alert."""
    require_admin(request)
    _require_audit_feature()
    engine = _get_alert_engine()
    actor = getattr(request.state, "auth_identity", "") or ""
    alert = engine.acknowledge_alert(alert_id, acknowledged_by=actor)
    if alert is None:
        raise HTTPException(status_code=404, detail=f"Alert {alert_id} not found")
    return {"status": "ok", "alert": alert.model_dump(mode="json")}


@router.post("/alert/evaluate")
@handle_api_errors
async def evaluate_alerts(
    request: Request,
    hours: int = 1,
    tenant_id: str = "",
) -> dict[str, Any]:
    """Manually evaluate recent audit events against all enabled rules.

    Returns the list of newly-triggered alerts. Useful for backfilling
    after rule creation.
    """
    require_admin(request)
    _require_audit_feature()
    engine = _get_alert_engine()
    events = _collect_enterprise_events(tenant_id=tenant_id, hours=hours, limit=100000)
    triggered = engine.evaluate_events(events)
    return {
        "status": "ok",
        "triggered": [a.model_dump(mode="json") for a in triggered],
        "count": len(triggered),
        "evaluated_events": len(events),
    }
