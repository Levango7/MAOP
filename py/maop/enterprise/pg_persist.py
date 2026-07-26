"""MAOP Enterprise PostgreSQL Persistence Layer.

Provides PostgreSQL-backed storage for enterprise modules:
  - RBAC grants
  - Tenant data + quotas
  - Audit events

Each manager auto-creates its schema on first use.
Uses the shared StorageBackend abstraction so the same code works
with SQLite (personal) or PostgreSQL (enterprise) transparently.

When PostgreSQL is unavailable, falls back to in-memory storage
with a degradation warning (matching the existing behavior).
"""

from __future__ import annotations

import json
import logging
import os
import time
from typing import Any, cast

from maop.config.edition import FeatureFlag, has_feature, record_degradation

logger = logging.getLogger(__name__)


def _get_pg_backend() -> Any | None:
    if not has_feature(FeatureFlag.POSTGRESQL):
        return None
    backend_type = os.getenv("MAOP_STORAGE_BACKEND", "").lower()
    if backend_type != "postgresql":
        return None
    try:
        from maop.core.backends_pg import PostgreSQLStorageBackend
        return PostgreSQLStorageBackend()
    except ImportError:
        record_degradation("storage", "postgresql", "memory")
        return None


class PgRBACStore:
    """PostgreSQL-backed RBAC grant persistence."""

    def __init__(self) -> None:
        self._backend = _get_pg_backend()
        self._ensure_schema()

    def _ensure_schema(self) -> None:
        if not self._backend:
            return
        self._backend.execute("""
            CREATE TABLE IF NOT EXISTS rbac_grants (
                id SERIAL PRIMARY KEY,
                user_id TEXT NOT NULL,
                role TEXT NOT NULL,
                tenant_id TEXT DEFAULT '',
                granted_by TEXT DEFAULT '',
                granted_at DOUBLE PRECISION DEFAULT 0,
                expires_at DOUBLE PRECISION DEFAULT NULL,
                UNIQUE(user_id, role, tenant_id)
            )
        """)
        self._backend.execute("CREATE INDEX IF NOT EXISTS idx_rbac_user ON rbac_grants(user_id)")
        self._backend.execute("CREATE INDEX IF NOT EXISTS idx_rbac_tenant ON rbac_grants(tenant_id)")

    @property
    def available(self) -> bool:
        return self._backend is not None

    def save_grant(self, user_id: str, role: str, tenant_id: str, granted_by: str, granted_at: float, expires_at: float | None) -> None:
        if not self._backend:
            return
        self._backend.execute(
            """INSERT INTO rbac_grants (user_id, role, tenant_id, granted_by, granted_at, expires_at)
               VALUES (%s, %s, %s, %s, %s, %s)
               ON CONFLICT (user_id, role, tenant_id) DO UPDATE SET granted_by=EXCLUDED.granted_by, granted_at=EXCLUDED.granted_at, expires_at=EXCLUDED.expires_at""",
            (user_id, role, tenant_id, granted_by, granted_at, expires_at),
        )

    def delete_grant(self, user_id: str, role: str, tenant_id: str) -> bool:
        if not self._backend:
            return False
        self._backend.execute(
            "DELETE FROM rbac_grants WHERE user_id=%s AND role=%s AND tenant_id=%s",
            (user_id, role, tenant_id),
        )
        return True

    def load_grants(self, user_id: str = "", tenant_id: str = "") -> list[dict[str, Any]]:
        if not self._backend:
            return []
        if user_id and tenant_id:
            return cast(list[dict[str, Any]], self._backend.fetchall(
                "SELECT * FROM rbac_grants WHERE user_id=%s AND (tenant_id=%s OR tenant_id='')",
                (user_id, tenant_id),
            ))
        if user_id:
            return cast(list[dict[str, Any]], self._backend.fetchall("SELECT * FROM rbac_grants WHERE user_id=%s", (user_id,)))
        if tenant_id:
            return cast(list[dict[str, Any]], self._backend.fetchall("SELECT * FROM rbac_grants WHERE tenant_id=%s OR tenant_id=''", (tenant_id,)))
        return cast(list[dict[str, Any]], self._backend.fetchall("SELECT * FROM rbac_grants"))


class PgTenantStore:
    """PostgreSQL-backed tenant persistence."""

    def __init__(self) -> None:
        self._backend = _get_pg_backend()
        self._ensure_schema()

    def _ensure_schema(self) -> None:
        if not self._backend:
            return
        self._backend.execute("""
            CREATE TABLE IF NOT EXISTS tenants (
                tenant_id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                status TEXT DEFAULT 'trial',
                plan TEXT DEFAULT 'starter',
                quota JSONB DEFAULT '{}',
                created_at DOUBLE PRECISION DEFAULT 0,
                updated_at DOUBLE PRECISION DEFAULT 0,
                expires_at DOUBLE PRECISION DEFAULT NULL,
                metadata JSONB DEFAULT '{}'
            )
        """)
        self._backend.execute("""
            CREATE TABLE IF NOT EXISTS tenant_usage (
                tenant_id TEXT PRIMARY KEY,
                api_calls_today INTEGER DEFAULT 0,
                storage_mb REAL DEFAULT 0,
                active_agents INTEGER DEFAULT 0,
                concurrent_tasks INTEGER DEFAULT 0,
                active_users INTEGER DEFAULT 0,
                FOREIGN KEY (tenant_id) REFERENCES tenants(tenant_id) ON DELETE CASCADE
            )
        """)

    @property
    def available(self) -> bool:
        return self._backend is not None

    def save_tenant(self, data: dict[str, Any]) -> None:
        if not self._backend:
            return
        quota = json.dumps(data.get("quota", {}))
        meta = json.dumps(data.get("metadata", {}))
        self._backend.execute(
            """INSERT INTO tenants (tenant_id, name, status, plan, quota, created_at, updated_at, expires_at, metadata)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
               ON CONFLICT (tenant_id) DO UPDATE SET name=EXCLUDED.name, status=EXCLUDED.status, plan=EXCLUDED.plan,
               quota=EXCLUDED.quota, updated_at=EXCLUDED.updated_at, expires_at=EXCLUDED.expires_at, metadata=EXCLUDED.metadata""",
            (data["tenant_id"], data["name"], data.get("status", "trial"), data.get("plan", "starter"),
             quota, data.get("created_at", 0), data.get("updated_at", 0), data.get("expires_at"), meta),
        )

    def delete_tenant(self, tenant_id: str) -> bool:
        if not self._backend:
            return False
        self._backend.execute("DELETE FROM tenant_usage WHERE tenant_id=%s", (tenant_id,))
        self._backend.execute("DELETE FROM tenants WHERE tenant_id=%s", (tenant_id,))
        return True

    def load_tenants(self, status: str = "") -> list[dict[str, Any]]:
        if not self._backend:
            return []
        if status:
            return cast(list[dict[str, Any]], self._backend.fetchall("SELECT * FROM tenants WHERE status=%s", (status,)))
        return cast(list[dict[str, Any]], self._backend.fetchall("SELECT * FROM tenants"))

    def load_tenant(self, tenant_id: str) -> dict[str, Any] | None:
        if not self._backend:
            return None
        return cast(dict[str, Any] | None, self._backend.fetchone("SELECT * FROM tenants WHERE tenant_id=%s", (tenant_id,)))

    def save_usage(self, tenant_id: str, usage: dict[str, Any]) -> None:
        if not self._backend:
            return
        self._backend.execute(
            """INSERT INTO tenant_usage (tenant_id, api_calls_today, storage_mb, active_agents, concurrent_tasks, active_users)
               VALUES (%s, %s, %s, %s, %s, %s)
               ON CONFLICT (tenant_id) DO UPDATE SET api_calls_today=EXCLUDED.api_calls_today, storage_mb=EXCLUDED.storage_mb,
               active_agents=EXCLUDED.active_agents, concurrent_tasks=EXCLUDED.concurrent_tasks, active_users=EXCLUDED.active_users""",
            (tenant_id, usage.get("api_calls_today", 0), usage.get("storage_mb", 0),
             usage.get("active_agents", 0), usage.get("concurrent_tasks", 0), usage.get("active_users", 0)),
        )

    def load_usage(self, tenant_id: str) -> dict[str, Any] | None:
        if not self._backend:
            return None
        return cast(dict[str, Any] | None, self._backend.fetchone("SELECT * FROM tenant_usage WHERE tenant_id=%s", (tenant_id,)))


class PgAuditStore:
    """PostgreSQL-backed audit event persistence."""

    def __init__(self) -> None:
        self._backend = _get_pg_backend()
        self._ensure_schema()

    def _ensure_schema(self) -> None:
        if not self._backend:
            return
        self._backend.execute("""
            CREATE TABLE IF NOT EXISTS audit_events (
                event_id TEXT PRIMARY KEY,
                timestamp DOUBLE PRECISION NOT NULL,
                action TEXT NOT NULL,
                severity TEXT DEFAULT 'info',
                actor TEXT DEFAULT '',
                tenant_id TEXT DEFAULT '',
                resource TEXT DEFAULT '',
                detail TEXT DEFAULT '',
                result TEXT DEFAULT 'success',
                ip_address TEXT DEFAULT '',
                user_agent TEXT DEFAULT '',
                metadata JSONB DEFAULT '{}'
            )
        """)
        self._backend.execute("CREATE INDEX IF NOT EXISTS idx_audit_timestamp ON audit_events(timestamp)")
        self._backend.execute("CREATE INDEX IF NOT EXISTS idx_audit_actor ON audit_events(actor)")
        self._backend.execute("CREATE INDEX IF NOT EXISTS idx_audit_tenant ON audit_events(tenant_id)")
        self._backend.execute("CREATE INDEX IF NOT EXISTS idx_audit_action ON audit_events(action)")

    @property
    def available(self) -> bool:
        return self._backend is not None

    def save_event(self, event: dict[str, Any]) -> None:
        if not self._backend:
            return
        meta = json.dumps(event.get("metadata", {}))
        self._backend.execute(
            """INSERT INTO audit_events (event_id, timestamp, action, severity, actor, tenant_id, resource, detail, result, ip_address, user_agent, metadata)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
            (event.get("event_id", ""), event.get("timestamp", 0), event.get("action", ""),
             event.get("severity", "info"), event.get("actor", ""), event.get("tenant_id", ""),
             event.get("resource", ""), event.get("detail", ""), event.get("result", "success"),
             event.get("ip_address", ""), event.get("user_agent", ""), meta),
        )

    def query_events(
        self,
        *,
        actor: str = "",
        tenant_id: str = "",
        action: str = "",
        severity: str = "",
        since: float = 0.0,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        if not self._backend:
            return []
        clauses = []
        params: list[Any] = []
        if actor:
            clauses.append("actor=%s")
            params.append(actor)
        if tenant_id:
            clauses.append("tenant_id=%s")
            params.append(tenant_id)
        if action:
            clauses.append("action=%s")
            params.append(action)
        if severity:
            clauses.append("severity=%s")
            params.append(severity)
        if since:
            clauses.append("timestamp >= %s")
            params.append(since)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        params.append(limit)
        return cast(list[dict[str, Any]], self._backend.fetchall(
            f"SELECT * FROM audit_events {where} ORDER BY timestamp DESC LIMIT %s",
            tuple(params),
        ))

    def summary(self, tenant_id: str = "", hours: int = 24) -> dict[str, Any]:
        if not self._backend:
            return {"total_events": 0, "by_action": {}, "critical_count": 0, "hours": hours}
        since = time.time() - hours * 3600
        clauses = ["timestamp >= %s"]
        params: list[Any] = [since]
        if tenant_id:
            clauses.append("tenant_id=%s")
            params.append(tenant_id)
        where = f"WHERE {' AND '.join(clauses)}"
        rows = self._backend.fetchall(
            f"SELECT action, severity FROM audit_events {where}",
            tuple(params),
        )
        by_action: dict[str, int] = {}
        critical = 0
        for r in rows:
            a = r.get("action", "")
            by_action[a] = by_action.get(a, 0) + 1
            if r.get("severity") == "critical":
                critical += 1
        return {"total_events": len(rows), "by_action": by_action, "critical_count": critical, "hours": hours}
