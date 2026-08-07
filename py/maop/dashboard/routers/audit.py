"""Audit router — unified endpoint for both enterprise and personal editions.

Enterprise edition: uses EnterpriseAuditLogger (maop.enterprise.audit) with
  tenant filtering, severity levels, and pagination.

Personal edition: uses AuditLog (maop.control.audit) reading from
  logs/audit.jsonl with basic filtering.

Both editions expose the same API surface: /api/audit/events, /api/audit/summary,
  /api/audit/filter.
"""

from __future__ import annotations

import logging
import time as _time
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Request

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


# ── Endpoints ─────────────────────────────────────────────────────


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
