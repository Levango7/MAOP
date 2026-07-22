"""MAOP Enterprise Audit — Comprehensive Audit Logging.

Extends core audit with:
  - Structured audit events (who/what/when/where/result)
  - Tenant-scoped audit trails
  - Compliance-ready immutable log
  - Query/filter API for dashboard
"""

from __future__ import annotations


import logging
import time
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field

from maop.config.edition import require_feature, FeatureFlag

logger = logging.getLogger(__name__)


class AuditAction(str, Enum):
    LOGIN = "login"
    LOGOUT = "logout"
    API_CALL = "api_call"
    AGENT_EXECUTE = "agent_execute"
    CONFIG_CHANGE = "config_change"
    PERMISSION_CHANGE = "permission_change"
    TENANT_CREATE = "tenant_create"
    TENANT_UPDATE = "tenant_update"
    TENANT_DELETE = "tenant_delete"
    DATA_EXPORT = "data_export"
    DATA_ACCESS = "data_access"
    SECRET_ACCESS = "secret_access"
    SYSTEM_ADMIN = "system_admin"


class AuditSeverity(str, Enum):
    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"


class AuditEvent(BaseModel):
    event_id: str = ""
    timestamp: float = 0.0
    action: AuditAction = AuditAction.API_CALL
    severity: AuditSeverity = AuditSeverity.INFO
    actor: str = ""
    tenant_id: str = ""
    resource: str = ""
    detail: str = ""
    result: str = ""
    ip_address: str = ""
    user_agent: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)


class EnterpriseAuditLogger:
    """Enterprise audit trail logger with tenant scoping and optional PG persistence."""

    def __init__(self) -> None:
        require_feature(FeatureFlag.AUDIT_LOG)
        self._events: list[AuditEvent] = []
        self._max_events: int = 100000
        self._pg: PgAuditStore | None = None
        try:
            from maop.enterprise.pg_persist import PgAuditStore
            pg = PgAuditStore()
            if pg.available:
                self._pg = pg
        except Exception:
            pass

    def log(
        self,
        action: AuditAction,
        actor: str = "",
        *,
        tenant_id: str = "",
        resource: str = "",
        detail: str = "",
        result: str = "success",
        severity: AuditSeverity = AuditSeverity.INFO,
        ip_address: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> AuditEvent:
        now = time.time()
        event = AuditEvent(
            event_id=f"aud_{int(now * 1000)}_{len(self._events)}",
            timestamp=now, action=action, severity=severity,
            actor=actor, tenant_id=tenant_id, resource=resource,
            detail=detail, result=result, ip_address=ip_address,
            metadata=metadata or {},
        )
        self._events.append(event)
        if len(self._events) > self._max_events:
            self._events = self._events[-self._max_events:]
        if self._pg:
            self._pg.save_event(event.model_dump())
        if severity == AuditSeverity.CRITICAL:
            logger.critical("[audit] %s actor=%s tenant=%s resource=%s result=%s",
                            action.value, actor, tenant_id, resource, result)
        else:
            logger.info("[audit] %s actor=%s tenant=%s resource=%s", action.value, actor, tenant_id, resource)
        return event

    def query(
        self,
        *,
        actor: str = "",
        tenant_id: str = "",
        action: AuditAction | None = None,
        severity: AuditSeverity | None = None,
        since: float = 0.0,
        limit: int = 100,
    ) -> list[AuditEvent]:
        if self._pg and self._pg.available:
            rows = self._pg.query_events(
                actor=actor, tenant_id=tenant_id,
                action=action.value if action else "",
                severity=severity.value if severity else "",
                since=since, limit=limit,
            )
            return [AuditEvent(**r) for r in rows]
        result = self._events
        if actor:
            result = [e for e in result if e.actor == actor]
        if tenant_id:
            result = [e for e in result if e.tenant_id == tenant_id]
        if action:
            result = [e for e in result if e.action == action]
        if severity:
            result = [e for e in result if e.severity == severity]
        if since:
            result = [e for e in result if e.timestamp >= since]
        return result[-limit:]

    def summary(self, tenant_id: str = "", hours: int = 24) -> dict[str, Any]:
        if self._pg and self._pg.available:
            return self._pg.summary(tenant_id=tenant_id, hours=hours)
        since = time.time() - hours * 3600
        events = [e for e in self._events if e.timestamp >= since]
        if tenant_id:
            events = [e for e in events if e.tenant_id == tenant_id]
        action_counts: dict[str, int] = {}
        for e in events:
            action_counts[e.action.value] = action_counts.get(e.action.value, 0) + 1
        return {
            "total_events": len(events),
            "by_action": action_counts,
            "critical_count": sum(1 for e in events if e.severity == AuditSeverity.CRITICAL),
            "hours": hours,
        }