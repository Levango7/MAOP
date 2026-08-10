"""MAOP Tenant Resource Quotas — Fine-grained per-tenant resource limits.

Extends the simple token/request quota in :class:`TenantConfig` with a
multi-resource quota engine: storage (MB), agents, users, concurrent tasks,
API calls/day, tokens/day.  Each resource has an independent limit and a
persistent usage counter.

Usage is tracked in the ``tenant_resource_usage`` table with one row per
``(tenant_id, resource, period)``.  ``period`` is an arbitrary string —
typically a date (``2026-08-10``) for daily limits or ``"total"`` for
all-time/cumulative limits (storage, agents).
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel

from maop.core.backends.db_utils import sqlite_connect, validate_identifier

logger = logging.getLogger(__name__)

#: Well-known resource identifiers.  Callers may use arbitrary strings too,
#: but these are the ones the platform enforces by default.
RESOURCE_STORAGE_MB = "storage_mb"
RESOURCE_AGENTS = "agents"
RESOURCE_USERS = "users"
RESOURCE_CONCURRENT_TASKS = "concurrent_tasks"
RESOURCE_API_CALLS = "api_calls"
RESOURCE_TOKENS = "tokens"


class ResourceQuota(BaseModel):
    """Per-tenant quota for a single resource.

    Attributes
    ----------
    resource : str
        Resource identifier (e.g. ``"storage_mb"``).
    limit : int
        Maximum allowed usage.  ``0`` means unlimited.
    period : str
        ``"daily"`` resets at UTC midnight; ``"total"`` is cumulative.
    """

    resource: str
    limit: int = 0
    period: str = "total"


class ResourceUsage(BaseModel):
    """Current usage snapshot for a tenant."""

    tenant_id: str
    resource: str
    period: str
    used: int = 0
    limit: int = 0

    @property
    def remaining(self) -> int:
        if self.limit <= 0:
            return -1  # unlimited
        return max(0, self.limit - self.used)

    @property
    def exceeded(self) -> bool:
        return self.limit > 0 and self.used >= self.limit


class QuotaError(Exception):
    """Raised when a quota check fails (optionally, in strict mode)."""


class ResourceQuotaManager:
    """Multi-resource quota engine for one tenant database.

    Parameters
    ----------
    db_path : str | Path
        Shared SQLite database path.
    strict : bool
        If True, :meth:`check` raises :class:`QuotaError` on violation;
        if False (default) it returns False and the caller decides.
    """

    def __init__(self, db_path: Any, *, strict: bool = False) -> None:
        self._db_path = db_path
        self._strict = strict
        self._ensure_tables()

    def _ensure_tables(self) -> None:
        with sqlite_connect(self._db_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS tenant_resource_quota (
                    tenant_id TEXT NOT NULL,
                    resource TEXT NOT NULL,
                    limit_val INTEGER NOT NULL DEFAULT 0,
                    period TEXT NOT NULL DEFAULT 'total',
                    PRIMARY KEY (tenant_id, resource)
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS tenant_resource_usage (
                    tenant_id TEXT NOT NULL,
                    resource TEXT NOT NULL,
                    period_key TEXT NOT NULL,
                    used INTEGER NOT NULL DEFAULT 0,
                    PRIMARY KEY (tenant_id, resource, period_key)
                )
            """)

    # ── quota CRUD ──────────────────────────────────────────────────

    def set_quota(
        self, tenant_id: str, resource: str, limit: int, *, period: str = "total",
    ) -> ResourceQuota:
        """Set or update the quota for a tenant/resource."""
        with sqlite_connect(self._db_path) as conn:
            conn.execute(
                """INSERT OR REPLACE INTO tenant_resource_quota
                   (tenant_id, resource, limit_val, period)
                   VALUES (?, ?, ?, ?)""",
                (tenant_id, resource, limit, period),
            )
        return ResourceQuota(resource=resource, limit=limit, period=period)

    def get_quota(self, tenant_id: str, resource: str) -> ResourceQuota | None:
        with sqlite_connect(self._db_path) as conn:
            row = conn.execute(
                "SELECT resource, limit_val, period FROM tenant_resource_quota "
                "WHERE tenant_id = ? AND resource = ?",
                (tenant_id, resource),
            ).fetchone()
            if not row:
                return None
            return ResourceQuota(resource=row[0], limit=row[1], period=row[2])

    def list_quotas(self, tenant_id: str) -> list[ResourceQuota]:
        with sqlite_connect(self._db_path) as conn:
            rows = conn.execute(
                "SELECT resource, limit_val, period FROM tenant_resource_quota "
                "WHERE tenant_id = ? ORDER BY resource",
                (tenant_id,),
            ).fetchall()
        return [ResourceQuota(resource=r[0], limit=r[1], period=r[2]) for r in rows]

    def remove_quota(self, tenant_id: str, resource: str) -> bool:
        with sqlite_connect(self._db_path) as conn:
            cur = conn.execute(
                "DELETE FROM tenant_resource_quota WHERE tenant_id = ? AND resource = ?",
                (tenant_id, resource),
            )
            return cur.rowcount > 0

    # ── usage & checking ────────────────────────────────────────────

    def _period_key(self, period: str) -> str:
        if period == "daily":
            return datetime.now(timezone.utc).strftime("%Y-%m-%d")
        return "total"

    def get_usage(self, tenant_id: str, resource: str) -> ResourceUsage:
        quota = self.get_quota(tenant_id, resource)
        period = quota.period if quota else "total"
        key = self._period_key(period)
        with sqlite_connect(self._db_path) as conn:
            row = conn.execute(
                "SELECT used FROM tenant_resource_usage "
                "WHERE tenant_id = ? AND resource = ? AND period_key = ?",
                (tenant_id, resource, key),
            ).fetchone()
        used = row[0] if row else 0
        limit = quota.limit if quota else 0
        return ResourceUsage(
            tenant_id=tenant_id, resource=resource, period=period, used=used, limit=limit,
        )

    def check(
        self, tenant_id: str, resource: str, amount: int = 1, *, consume: bool = False,
    ) -> bool:
        """Return True if *amount* more usage is allowed for *resource*.

        If *consume* is True and the check passes, the usage is recorded
        atomically.  In strict mode a :class:`QuotaError` is raised on denial.
        """
        usage = self.get_usage(tenant_id, resource)
        if usage.exceeded or (usage.limit > 0 and usage.used + amount > usage.limit):
            if self._strict:
                raise QuotaError(
                    f"quota exceeded: tenant={tenant_id!r} resource={resource!r} "
                    f"used={usage.used} limit={usage.limit} requested={amount}"
                )
            return False
        if consume:
            self._record(tenant_id, resource, usage.period, amount)
        return True

    def consume(self, tenant_id: str, resource: str, amount: int = 1) -> bool:
        """Convenience: check + record in one call."""
        return self.check(tenant_id, resource, amount, consume=True)

    def _record(self, tenant_id: str, resource: str, period: str, amount: int) -> None:
        key = self._period_key(period)
        validate_identifier("tenant_resource_usage", "table")
        with sqlite_connect(self._db_path) as conn:
            conn.execute(
                """INSERT INTO tenant_resource_usage
                   (tenant_id, resource, period_key, used)
                   VALUES (?, ?, ?, ?)
                   ON CONFLICT(tenant_id, resource, period_key) DO UPDATE SET
                     used = used + excluded.used""",
                (tenant_id, resource, key, amount),
            )

    def reset_usage(self, tenant_id: str, resource: str | None = None) -> int:
        """Reset usage counters for a tenant.  Returns rows deleted.

        If *resource* is None, all resources for the tenant are reset.
        """
        with sqlite_connect(self._db_path) as conn:
            if resource is None:
                cur = conn.execute(
                    "DELETE FROM tenant_resource_usage WHERE tenant_id = ?",
                    (tenant_id,),
                )
            else:
                cur = conn.execute(
                    "DELETE FROM tenant_resource_usage "
                    "WHERE tenant_id = ? AND resource = ?",
                    (tenant_id, resource),
                )
            return cur.rowcount

    def all_usage(self, tenant_id: str) -> list[ResourceUsage]:
        """Return usage for every resource that has a quota set."""
        quotas = self.list_quotas(tenant_id)
        return [self.get_usage(tenant_id, q.resource) for q in quotas]