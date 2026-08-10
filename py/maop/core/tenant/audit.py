"""MAOP Tenant Audit Log — Tamper-evident audit trail for tenant operations.

Records every significant tenant-scoped operation (data access, config change,
quota breach, login, …) with an append-only log.  Each entry carries a
monotonic sequence number per tenant and a SHA-256 chain hash linking it to
the previous entry, so deletion or re-ordering is detectable.

Storage: ``tenant_audit_log`` table in the shared SQLite database.
"""

from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, Field

from maop.core.backends.db_utils import sqlite_connect

logger = logging.getLogger(__name__)

#: Well-known audit actions.
ACTION_DATA_READ = "data.read"
ACTION_DATA_WRITE = "data.write"
ACTION_DATA_DELETE = "data.delete"
ACTION_CONFIG_CHANGE = "config.change"
ACTION_QUOTA_BREACH = "quota.breach"
ACTION_LOGIN = "auth.login"
ACTION_LOGOUT = "auth.logout"
ACTION_AGENT_INVOKE = "agent.invoke"
ACTION_TOOL_CALL = "tool.call"


class AuditEntry(BaseModel):
    """One audit log record."""

    id: int = 0
    tenant_id: str
    timestamp: str
    action: str
    resource: str = ""
    resource_id: str = ""
    actor: str = ""           # user/service that performed the action
    result: str = "ok"        # "ok" | "denied" | "error"
    detail: dict[str, Any] = Field(default_factory=dict)
    seq: int = 0              # per-tenant monotonic sequence
    prev_hash: str = ""
    hash: str = ""            # SHA-256 of (prev_hash + canonical(entry fields))


class AuditLogger:
    """Append-only audit log with per-tenant sequence + hash chain.

    Parameters
    ----------
    db_path : str | Path
        Shared SQLite database path.
    max_detail_bytes : int
        Truncate ``detail`` JSON to this many bytes to prevent unbounded rows.
    """

    def __init__(self, db_path: Any, *, max_detail_bytes: int = 4096) -> None:
        self._db_path = db_path
        self._max_detail = max_detail_bytes
        self._ensure_table()

    def _ensure_table(self) -> None:
        with sqlite_connect(self._db_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS tenant_audit_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    tenant_id TEXT NOT NULL,
                    timestamp TEXT NOT NULL,
                    action TEXT NOT NULL,
                    resource TEXT NOT NULL DEFAULT '',
                    resource_id TEXT NOT NULL DEFAULT '',
                    actor TEXT NOT NULL DEFAULT '',
                    result TEXT NOT NULL DEFAULT 'ok',
                    detail TEXT NOT NULL DEFAULT '{}',
                    seq INTEGER NOT NULL DEFAULT 0,
                    prev_hash TEXT NOT NULL DEFAULT '',
                    hash TEXT NOT NULL DEFAULT ''
                )
            """)
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_audit_tenant_ts "
                "ON tenant_audit_log (tenant_id, timestamp)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_audit_tenant_seq "
                "ON tenant_audit_log (tenant_id, seq)"
            )

    # ── write ───────────────────────────────────────────────────────

    def log(
        self,
        tenant_id: str,
        action: str,
        *,
        resource: str = "",
        resource_id: str = "",
        actor: str = "",
        result: str = "ok",
        detail: dict[str, Any] | None = None,
    ) -> AuditEntry:
        """Append one audit entry.  Returns the stored :class:`AuditEntry`."""
        detail = detail or {}
        detail_json = self._truncate_detail(detail)
        ts = datetime.now(timezone.utc).isoformat()
        with sqlite_connect(self._db_path) as conn:
            prev = conn.execute(
                "SELECT seq, hash FROM tenant_audit_log "
                "WHERE tenant_id = ? ORDER BY seq DESC LIMIT 1",
                (tenant_id,),
            ).fetchone()
            prev_seq = prev[0] if prev else 0
            prev_hash = prev[1] if prev else ""
            seq = prev_seq + 1
            entry_hash = self._compute_hash(
                prev_hash, tenant_id, ts, action, resource, resource_id,
                actor, result, detail_json, seq,
            )
            cur = conn.execute(
                """INSERT INTO tenant_audit_log
                   (tenant_id, timestamp, action, resource, resource_id, actor,
                    result, detail, seq, prev_hash, hash)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (tenant_id, ts, action, resource, resource_id, actor,
                 result, detail_json, seq, prev_hash, entry_hash),
            )
            row_id = cur.lastrowid
        stored_detail = json.loads(detail_json)
        return AuditEntry(
            id=row_id if row_id is not None else 0,
            tenant_id=tenant_id,
            timestamp=ts,
            action=action,
            resource=resource,
            resource_id=resource_id,
            actor=actor,
            result=result,
            detail=stored_detail,
            seq=seq,
            prev_hash=prev_hash,
            hash=entry_hash,
        )

    def _truncate_detail(self, detail: dict[str, Any]) -> str:
        s = json.dumps(detail, sort_keys=True, default=str)
        if len(s.encode("utf-8")) > self._max_detail:
            return json.dumps({"_truncated": True, "_len": len(s)})
        return s

    @staticmethod
    def _compute_hash(
        prev_hash: str, tenant_id: str, ts: str, action: str,
        resource: str, resource_id: str, actor: str, result: str,
        detail_json: str, seq: int,
    ) -> str:
        canonical = "|".join([
            prev_hash, tenant_id, ts, action, resource, resource_id,
            actor, result, detail_json, str(seq),
        ])
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    # ── read ────────────────────────────────────────────────────────

    def query(
        self,
        tenant_id: str,
        *,
        action: str | None = None,
        resource: str | None = None,
        actor: str | None = None,
        since: str | None = None,
        until: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[AuditEntry]:
        """Query the audit log for a tenant with optional filters."""
        clauses = ["tenant_id = ?"]
        params: list[Any] = [tenant_id]
        if action is not None:
            clauses.append("action = ?")
            params.append(action)
        if resource is not None:
            clauses.append("resource = ?")
            params.append(resource)
        if actor is not None:
            clauses.append("actor = ?")
            params.append(actor)
        if since is not None:
            clauses.append("timestamp >= ?")
            params.append(since)
        if until is not None:
            clauses.append("timestamp <= ?")
            params.append(until)
        where = " AND ".join(clauses)
        sql = (
            f"SELECT id, tenant_id, timestamp, action, resource, resource_id, "
            f"actor, result, detail, seq, prev_hash, hash "
            f"FROM tenant_audit_log WHERE {where} "
            f"ORDER BY seq DESC LIMIT ? OFFSET ?"
        )
        params.extend([limit, offset])
        with sqlite_connect(self._db_path) as conn:
            rows = conn.execute(sql, tuple(params)).fetchall()
        return [self._row_to_entry(r) for r in rows]

    def count(self, tenant_id: str, *, action: str | None = None) -> int:
        clauses = ["tenant_id = ?"]
        params: list[Any] = [tenant_id]
        if action is not None:
            clauses.append("action = ?")
            params.append(action)
        with sqlite_connect(self._db_path) as conn:
            row = conn.execute(
                f"SELECT COUNT(*) FROM tenant_audit_log WHERE {' AND '.join(clauses)}",
                tuple(params),
            ).fetchone()
        return row[0] if row else 0

    def get_entry(self, tenant_id: str, seq: int) -> AuditEntry | None:
        with sqlite_connect(self._db_path) as conn:
            row = conn.execute(
                "SELECT id, tenant_id, timestamp, action, resource, resource_id, "
                "actor, result, detail, seq, prev_hash, hash "
                "FROM tenant_audit_log WHERE tenant_id = ? AND seq = ?",
                (tenant_id, seq),
            ).fetchone()
        return self._row_to_entry(row) if row else None

    # ── integrity ───────────────────────────────────────────────────

    def verify_chain(self, tenant_id: str) -> bool:
        """Recompute the hash chain and return True if it is intact."""
        with sqlite_connect(self._db_path) as conn:
            rows = conn.execute(
                "SELECT seq, timestamp, action, resource, resource_id, actor, "
                "result, detail, prev_hash, hash "
                "FROM tenant_audit_log WHERE tenant_id = ? ORDER BY seq",
                (tenant_id,),
            ).fetchall()
        expected_prev = ""
        for r in rows:
            seq, ts, action, resource, resource_id, actor, result, detail, prev_hash, h = r
            if prev_hash != expected_prev:
                return False
            recomputed = self._compute_hash(
                prev_hash, tenant_id, ts, action, resource, resource_id,
                actor, result, detail, seq,
            )
            if recomputed != h:
                return False
            expected_prev = h
        return True

    def _row_to_entry(self, row: Any) -> AuditEntry:
        try:
            detail = json.loads(row[8])
        except (json.JSONDecodeError, TypeError):
            detail = {}
        return AuditEntry(
            id=row[0], tenant_id=row[1], timestamp=row[2], action=row[3],
            resource=row[4], resource_id=row[5], actor=row[6], result=row[7],
            detail=detail, seq=row[9], prev_hash=row[10], hash=row[11],
        )