"""MAOP TenantManager — Multi-tenant isolation, quotas, RLS, and audit.

This module hosts the enhanced :class:`TenantManager` that subsumes the
legacy single-file implementation (``maop/core/tenant.py``) and wires in:

* :class:`~maop.core.tenant.rls.TenantRLS` — row-level security scoping.
* :class:`~maop.core.tenant.quota.ResourceQuotaManager` — multi-resource quotas.
* :class:`~maop.core.tenant.audit.AuditLogger` — append-only audit trail.

Backward compatibility: the original API (``create_tenant``, ``get_tenant``,
``check_quota``, ``check_agent_access``, ``check_model_access``) is preserved
verbatim so existing callers and ``maop.core.security.tenant`` keep working.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from maop.core.backends.db_utils import sqlite_connect, validate_identifier
from maop.core.tenant.audit import AuditEntry, AuditLogger
from maop.core.tenant.quota import ResourceQuotaManager, ResourceUsage
from maop.core.tenant.rls import TenantRLS

logger = logging.getLogger(__name__)


class TenantConfig(BaseModel):
    """Per-tenant configuration (backward-compatible with legacy tenant.py)."""

    tenant_id: str
    display_name: str = ""
    enabled: bool = True
    quota_tokens: int = 0
    quota_requests: int = 0
    allowed_agents: list[str] = Field(default_factory=list)
    allowed_models: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: str = ""
    updated_at: str = ""


class TenantManager:
    """Multi-tenant isolation, quota, RLS, and audit management.

    The constructor signature matches the legacy implementation so existing
    callers (``TenantManager(root_dir=...)``) work unchanged.  New keyword
    arguments ``scoped_tables`` and ``audit`` toggle the enhanced subsystems.
    """

    def __init__(
        self,
        root_dir: str | Path | None = None,
        *,
        scoped_tables: list[str] | None = None,
        enable_audit: bool = True,
        enable_quota: bool = True,
    ) -> None:
        self._root = Path(root_dir) if root_dir else Path(".")
        self._db_path = self._root / "data" / "maop.db"
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._ensure_table()
        # Enhanced subsystems (lazy-init to share the same db_path).
        self._rls = TenantRLS(self._db_path, scoped_tables=scoped_tables)
        self._quota = ResourceQuotaManager(self._db_path) if enable_quota else None
        self._audit = AuditLogger(self._db_path) if enable_audit else None

    # ── properties for the enhanced subsystems ──────────────────────

    @property
    def rls(self) -> TenantRLS:
        return self._rls

    @property
    def quota(self) -> ResourceQuotaManager | None:
        return self._quota

    @property
    def audit(self) -> AuditLogger | None:
        return self._audit

    # ── table bootstrap ─────────────────────────────────────────────

    def _ensure_table(self) -> None:
        validate_identifier("tenants", "table")
        with sqlite_connect(self._db_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS tenants (
                    tenant_id TEXT PRIMARY KEY,
                    display_name TEXT NOT NULL DEFAULT '',
                    enabled INTEGER NOT NULL DEFAULT 1,
                    quota_tokens INTEGER NOT NULL DEFAULT 0,
                    quota_requests INTEGER NOT NULL DEFAULT 0,
                    allowed_agents TEXT NOT NULL DEFAULT '[]',
                    allowed_models TEXT NOT NULL DEFAULT '[]',
                    metadata TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL DEFAULT '',
                    updated_at TEXT NOT NULL DEFAULT ''
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS tenant_usage (
                    tenant_id TEXT NOT NULL,
                    date TEXT NOT NULL,
                    tokens_used INTEGER NOT NULL DEFAULT 0,
                    requests_used INTEGER NOT NULL DEFAULT 0,
                    PRIMARY KEY (tenant_id, date)
                )
            """)

    # ── CRUD (legacy-compatible) ────────────────────────────────────

    def create_tenant(self, tenant_id: str, **kwargs: Any) -> TenantConfig:
        now = datetime.now(timezone.utc).isoformat()
        config = TenantConfig(tenant_id=tenant_id, created_at=now, updated_at=now, **kwargs)
        validate_identifier("tenants", "table")
        with sqlite_connect(self._db_path) as conn:
            conn.execute(
                """INSERT OR REPLACE INTO tenants
                   (tenant_id, display_name, enabled, quota_tokens, quota_requests,
                    allowed_agents, allowed_models, metadata, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (config.tenant_id, config.display_name, int(config.enabled),
                 config.quota_tokens, config.quota_requests,
                 json.dumps(config.allowed_agents), json.dumps(config.allowed_models),
                 json.dumps(config.metadata), config.created_at, config.updated_at),
            )
        self._audit_log(tenant_id, "tenant.create", result="ok")
        return config

    def get_tenant(self, tenant_id: str) -> TenantConfig | None:
        validate_identifier("tenants", "table")
        with sqlite_connect(self._db_path) as conn:
            row = conn.execute(
                "SELECT * FROM tenants WHERE tenant_id = ?", (tenant_id,),
            ).fetchone()
            if not row:
                return None
            return self._row_to_config(row)

    def list_tenants(self) -> list[TenantConfig]:
        validate_identifier("tenants", "table")
        with sqlite_connect(self._db_path) as conn:
            rows = conn.execute("SELECT * FROM tenants ORDER BY created_at").fetchall()
        return [self._row_to_config(r) for r in rows]

    def delete_tenant(self, tenant_id: str) -> bool:
        validate_identifier("tenants", "table")
        with sqlite_connect(self._db_path) as conn:
            cursor = conn.execute("DELETE FROM tenants WHERE tenant_id = ?", (tenant_id,))
            deleted = cursor.rowcount > 0
        if deleted:
            self._audit_log(tenant_id, "tenant.delete", result="ok")
        return deleted

    def _row_to_config(self, row: sqlite3.Row) -> TenantConfig:
        return TenantConfig(
            tenant_id=row["tenant_id"],
            display_name=row["display_name"],
            enabled=bool(row["enabled"]),
            quota_tokens=row["quota_tokens"],
            quota_requests=row["quota_requests"],
            allowed_agents=json.loads(row["allowed_agents"]),
            allowed_models=json.loads(row["allowed_models"]),
            metadata=json.loads(row["metadata"]),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    # ── quota (legacy-compatible) ───────────────────────────────────

    def check_quota(
        self, tenant_id: str, *, tokens_used: int = 0, requests_used: int = 0,
    ) -> bool:
        config = self.get_tenant(tenant_id)
        if not config or not config.enabled:
            return False
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        validate_identifier("tenant_usage", "table")
        with sqlite_connect(self._db_path) as conn:
            row = conn.execute(
                "SELECT * FROM tenant_usage WHERE tenant_id = ? AND date = ?",
                (tenant_id, today),
            ).fetchone()
            current_tokens = row["tokens_used"] if row else 0
            current_requests = row["requests_used"] if row else 0
            if config.quota_tokens > 0 and current_tokens + tokens_used > config.quota_tokens:
                self._audit_log(
                    tenant_id, "quota.breach", resource="tokens", result="denied",
                    detail={"used": current_tokens + tokens_used, "limit": config.quota_tokens},
                )
                return False
            if config.quota_requests > 0 and current_requests + requests_used > config.quota_requests:
                self._audit_log(
                    tenant_id, "quota.breach", resource="requests", result="denied",
                    detail={"used": current_requests + requests_used, "limit": config.quota_requests},
                )
                return False
            conn.execute(
                """INSERT INTO tenant_usage (tenant_id, date, tokens_used, requests_used)
                   VALUES (?, ?, ?, ?)
                   ON CONFLICT(tenant_id, date) DO UPDATE SET
                     tokens_used = tokens_used + excluded.tokens_used,
                     requests_used = requests_used + excluded.requests_used""",
                (tenant_id, today, tokens_used, requests_used),
            )
        return True

    async def check_quota_async(
        self, tenant_id: str, *, tokens_used: int = 0, requests_used: int = 0,
    ) -> bool:
        """Async wrapper — offloads the SQLite call to a worker thread."""
        import asyncio
        return await asyncio.to_thread(
            self.check_quota, tenant_id,
            tokens_used=tokens_used, requests_used=requests_used,
        )

    def check_agent_access(self, tenant_id: str, agent: str) -> bool:
        config = self.get_tenant(tenant_id)
        if not config or not config.enabled:
            return False
        if not config.allowed_agents:
            return True
        allowed = agent in config.allowed_agents
        if not allowed:
            self._audit_log(
                tenant_id, "agent.access", resource=agent, result="denied",
            )
        return allowed

    def check_model_access(self, tenant_id: str, model: str) -> bool:
        config = self.get_tenant(tenant_id)
        if not config or not config.enabled:
            return False
        if not config.allowed_models:
            return True
        allowed = model in config.allowed_models
        if not allowed:
            self._audit_log(
                tenant_id, "model.access", resource=model, result="denied",
            )
        return allowed

    # ── enhanced: resource quota convenience ────────────────────────

    def check_resource_quota(
        self, tenant_id: str, resource: str, amount: int = 1, *, consume: bool = False,
    ) -> bool:
        """Delegate to :class:`ResourceQuotaManager` if enabled."""
        if self._quota is None:
            return True
        ok = self._quota.check(tenant_id, resource, amount, consume=consume)
        if not ok:
            self._audit_log(
                tenant_id, "quota.breach", resource=resource, result="denied",
                detail={"amount": amount},
            )
        return ok

    def get_resource_usage(self, tenant_id: str) -> list[ResourceUsage]:
        if self._quota is None:
            return []
        return self._quota.all_usage(tenant_id)

    # ── enhanced: RLS convenience ───────────────────────────────────

    def scoped_select(
        self, tenant_id: str, table: str, **kwargs: Any,
    ) -> tuple[str, tuple[Any, ...]]:
        """Build a tenant-scoped SELECT via :class:`TenantRLS`."""
        return self._rls.scoped_select(tenant_id, table, **kwargs)

    def scoped_insert(
        self, tenant_id: str, table: str, columns: list[str], values: list[Any],
    ) -> tuple[str, tuple[Any, ...]]:
        """Build a tenant-scoped INSERT via :class:`TenantRLS`."""
        return self._rls.scoped_insert(tenant_id, table, columns, values)

    def scoped_execute(
        self, tenant_id: str, sql: str, params: tuple[Any, ...] = (),
    ) -> list[sqlite3.Row]:
        """Execute *sql* and enforce that returned rows belong to *tenant_id*.

        For scoped tables, the caller should already use :meth:`scoped_select`
        to build *sql*.  This method additionally post-checks every row via
        :meth:`TenantRLS.enforce_scope` to catch accidental cross-tenant reads.
        """
        with sqlite_connect(self._db_path) as conn:
            rows = conn.execute(sql, params).fetchall()
        for row in rows:
            try:
                row_dict = dict(row)
            except (TypeError, ValueError):
                continue
            table = self._infer_table(sql)
            if table and self._rls.is_scoped(table):
                self._rls.enforce_scope(tenant_id, table, row_dict)
        self._audit_log(tenant_id, "data.read", resource=self._infer_table(sql) or "")
        return rows

    @staticmethod
    def _infer_table(sql: str) -> str:
        """Best-effort extraction of the table name from a SELECT/INSERT."""
        low = sql.lstrip().lower()
        for kw in ("from", "into", "update"):
            idx = low.find(kw)
            if idx >= 0:
                rest = sql[idx + len(kw):].strip()
                tok = ""
                for ch in rest:
                    if ch.isalnum() or ch == "_":
                        tok += ch
                    else:
                        break
                if tok:
                    return tok
        return ""

    # ── enhanced: audit convenience ────────────────────────────────

    def audit_log(
        self, tenant_id: str, action: str, **kwargs: Any,
    ) -> AuditEntry | None:
        """Public audit-log entry point.  No-op if audit is disabled."""
        return self._audit_log(tenant_id, action, **kwargs)

    def _audit_log(
        self, tenant_id: str, action: str, **kwargs: Any,
    ) -> AuditEntry | None:
        if self._audit is None:
            return None
        try:
            return self._audit.log(tenant_id, action, **kwargs)
        except Exception:
            logger.debug("audit log write failed", exc_info=True)
            return None

    def get_audit_log(
        self, tenant_id: str, *, limit: int = 100, **kwargs: Any,
    ) -> list[AuditEntry]:
        if self._audit is None:
            return []
        return self._audit.query(tenant_id, limit=limit, **kwargs)