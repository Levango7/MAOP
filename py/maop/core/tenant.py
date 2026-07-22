"""MAOP Multi-Tenancy — Tenant isolation and per-tenant quotas.

Provides:
  - TenantManager: CRUD for tenant records
  - Per-tenant API key isolation (via BYOK gateway)
  - Per-tenant quotas (token budget, request rate, agent access)
  - Tenant-aware middleware for Dashboard

Usage::

    from maop.core.tenant import TenantManager

    mgr = TenantManager(root_dir="/path/to/MAOP")
    mgr.create_tenant("acme", display_name="Acme Corp", quota_tokens=100000)
    is_allowed = mgr.check_quota("acme", tokens_used=500)
"""
from __future__ import annotations

import logging
import sqlite3
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from maop.core.db_utils import sqlite_connect, validate_identifier

logger = logging.getLogger(__name__)


class TenantConfig(BaseModel):
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
    """Multi-tenant isolation and quota management."""

    def __init__(self, root_dir: str | Path | None = None) -> None:
        self._root = Path(root_dir) if root_dir else Path(".")
        self._db_path = self._root / "data" / "maop.db"
        self._ensure_table()

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

    def create_tenant(self, tenant_id: str, **kwargs: Any) -> TenantConfig:
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc).isoformat()
        config = TenantConfig(tenant_id=tenant_id, created_at=now, updated_at=now, **kwargs)

        import json
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
        return config

    def get_tenant(self, tenant_id: str) -> TenantConfig | None:
        import json
        validate_identifier("tenants", "table")
        with sqlite_connect(self._db_path) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute("SELECT * FROM tenants WHERE tenant_id = ?", (tenant_id,)).fetchone()
            if not row:
                return None
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

    def list_tenants(self) -> list[TenantConfig]:
        import json
        validate_identifier("tenants", "table")
        with sqlite_connect(self._db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute("SELECT * FROM tenants ORDER BY created_at").fetchall()
        result = []
        for row in rows:
            result.append(TenantConfig(
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
            ))
        return result

    def delete_tenant(self, tenant_id: str) -> bool:
        validate_identifier("tenants", "table")
        with sqlite_connect(self._db_path) as conn:
            cursor = conn.execute("DELETE FROM tenants WHERE tenant_id = ?", (tenant_id,))
            return cursor.rowcount > 0

    def check_quota(self, tenant_id: str, *, tokens_used: int = 0, requests_used: int = 0) -> bool:
        from datetime import datetime, timezone
        config = self.get_tenant(tenant_id)
        if not config or not config.enabled:
            return False

        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        validate_identifier("tenant_usage", "table")
        with sqlite_connect(self._db_path) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT * FROM tenant_usage WHERE tenant_id = ? AND date = ?",
                (tenant_id, today),
            ).fetchone()

            current_tokens = row["tokens_used"] if row else 0
            current_requests = row["requests_used"] if row else 0

            if config.quota_tokens > 0 and current_tokens + tokens_used > config.quota_tokens:
                return False
            if config.quota_requests > 0 and current_requests + requests_used > config.quota_requests:
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

    def check_agent_access(self, tenant_id: str, agent: str) -> bool:
        config = self.get_tenant(tenant_id)
        if not config or not config.enabled:
            return False
        if not config.allowed_agents:
            return True
        return agent in config.allowed_agents

    def check_model_access(self, tenant_id: str, model: str) -> bool:
        config = self.get_tenant(tenant_id)
        if not config or not config.enabled:
            return False
        if not config.allowed_models:
            return True
        return model in config.allowed_models