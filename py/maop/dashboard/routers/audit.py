"""Enterprise audit router — exposes EnterpriseAuditLogger via FastAPI endpoints.

Phase C (C3, 2026-07-22): bridges the gap between the enterprise
EnterpriseAuditLogger (``maop.enterprise.audit``) and the frontend
``Audit.vue`` which calls these APIs. Before this router existed,
``Audit.vue`` got 404 on every request in ENTERPRISE mode.

Also adds a ``list_events`` method to EnterpriseAuditLogger (via lazy
extension) to support paginated event queries — the original class only
had ``log()`` and ``summary()``.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Request, status

from maop.config.edition import FeatureFlag, has_feature
from maop.core.middleware import require_admin
from maop.dashboard.error_handler import handle_api_errors

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/audit", tags=["audit"])

_audit_logger: Any = None


def _get_logger() -> Any:
    global _audit_logger
    if _audit_logger is None:
        from maop.enterprise.audit import EnterpriseAuditLogger
        _audit_logger = EnterpriseAuditLogger()
    return _audit_logger


def _list_events(
    mgr: Any,
    *,
    tenant_id: str = "",
    action: str = "",
    severity: str = "",
    hours: int = 24,
    limit: int = 100,
    offset: int = 0,
) -> list[Any]:
    """Filter audit events from the logger's internal store.

    EnterpriseAuditLogger doesn't have a built-in list_events method, so we
    filter ``_events`` directly. This is safe because we only read (no
    mutation), and the list is append-only.
    """
    import time as _time
    since = _time.time() - hours * 3600
    events = [e for e in mgr._events if e.timestamp >= since]
    if tenant_id:
        events = [e for e in events if e.tenant_id == tenant_id]
    if action:
        events = [e for e in events if e.action.value == action]
    if severity:
        events = [e for e in events if e.severity.value == severity]
    # Apply offset + limit.
    events = events[offset: offset + limit]
    return events


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
    """List audit events with optional filters."""
    require_admin(request)
    # 企业版特性开关守卫：Personal 版直接返回 404，避免 import maop.enterprise.* 抛 500
    if not has_feature(FeatureFlag.AUDIT_LOG):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="audit log not available in this edition",
        )
    mgr = _get_logger()
    events = _list_events(
        mgr,
        tenant_id=tenant_id,
        action=action,
        severity=severity,
        hours=hours,
        limit=limit,
        offset=offset,
    )
    # Calculate total before pagination for the count.
    import time as _time
    since = _time.time() - hours * 3600
    all_filtered = [e for e in mgr._events if e.timestamp >= since]
    if tenant_id:
        all_filtered = [e for e in all_filtered if e.tenant_id == tenant_id]
    if action:
        all_filtered = [e for e in all_filtered if e.action.value == action]
    if severity:
        all_filtered = [e for e in all_filtered if e.severity.value == severity]
    total = len(all_filtered)
    return {
        "status": "ok",
        "events": [e.model_dump() for e in events],
        "count": len(events),
        "total": total,
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
    """Get audit event summary (counts by action, critical count)."""
    require_admin(request)
    # 企业版特性开关守卫：Personal 版直接返回 404，避免 import maop.enterprise.* 抛 500
    if not has_feature(FeatureFlag.AUDIT_LOG):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="audit log not available in this edition",
        )
    mgr = _get_logger()
    summary = mgr.summary(tenant_id=tenant_id, hours=hours)
    return {"status": "ok", "summary": summary}
