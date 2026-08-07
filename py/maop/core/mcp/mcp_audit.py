"""MAOP MCP Audit Logger — persistent record of MCP tool invocations.

Phase δ-3: The existing :class:`~maop.core.tool_audit.ToolAuditLog`
covers agent-internal tool calls but is keyed on ``tool_name`` /
``agent`` and has no concept of *which MCP server* was hit or *who*
initiated the call. MCP servers are external processes with their own
privilege boundaries (filesystem access, network access, secrets), so
we need a dedicated audit trail that records:

  - which server + tool was invoked
  - who invoked it (user_id)
  - whether the permission layer allowed it (and why)
  - whether the call itself succeeded, how long it took, and any error

To avoid leaking arguments that may contain secrets (API keys, file
contents, prompts), only a SHA-256 hash of the serialised arguments is
stored. The full payload is intentionally *not* persisted.

Persistence uses SQLite via :func:`maop.core.db_utils.get_db_path` so
the table lands in the same ``maop.db`` (or per-module file under
``MAOP_DB_PER_MODULE=1``) as the rest of the system. The schema is
idempotent — ``CREATE TABLE IF NOT EXISTS`` — so the logger can be
constructed repeatedly without migration.

Usage::

    from maop.core.mcp.mcp_audit import MCPAuditLogger, MCPAuditRecord
    import time

    audit = MCPAuditLogger()
    audit.log_call(MCPAuditRecord(
        timestamp=time.time(),
        server_name="filesystem",
        tool_name="read_file",
        user_id="alice",
        arguments_hash=_hash_arguments({"path": "/tmp/x"}),
        allowed=True,
        decision_reason="default allow",
        success=True,
        duration_ms=12.3,
    ))

    denied = audit.query(allowed=False, limit=10)
    removed = audit.prune(older_than_days=30)
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from maop.core.backends.db_utils import get_db_path, sqlite_connect

logger = logging.getLogger(__name__)


@dataclass
class MCPAuditRecord:
    """A single MCP tool call audit entry.

    All fields are JSON-serialisable so the record can be passed through
    queues / shipped to an external SIEM without further transformation.

    Attributes
    ----------
    timestamp:
        Unix epoch seconds (UTC) — same convention as
        :class:`maop.core.tool_audit.ToolAuditEntry`.
    server_name:
        Logical MCP server name (``MCPServerConfig.name``). We key on
        name rather than the opaque server_id so the audit log survives
        hub restarts; server_id is recycled on every reconnect.
    tool_name:
        Name of the invoked tool, as reported by the MCP server.
    user_id:
        Caller identity. Empty string when no user context is available
        (e.g. system-initiated calls).
    arguments_hash:
        SHA-256 hex digest of the JSON-serialised arguments. Stored
        instead of the raw payload to prevent secret leakage; the hash
        is still useful for grouping identical invocations or detecting
        replay.
    allowed:
        Whether the permission checker permitted the call. ``False`` for
        rejected calls (in which case ``success`` is ``False`` and
        ``error`` carries the decision reason).
    decision_reason:
        Rationale produced by :class:`MCPPermissionChecker`. Goes into
        the audit row verbatim so reviewers can filter by matched rule.
    success:
        Whether the underlying MCP call returned without error. ``False``
        for both permission rejections and transport / server errors.
    duration_ms:
        Wall-clock call duration in milliseconds. Zero for permission
        rejections (no call was made).
    error:
        Optional error message. For permission rejections this mirrors
        ``decision_reason``; for runtime failures it is the exception
        message.
    """

    timestamp: float
    server_name: str
    tool_name: str
    user_id: str = ""
    arguments_hash: str = ""
    allowed: bool = True
    decision_reason: str = ""
    success: bool = True
    duration_ms: float = 0.0
    error: str | None = None


_MCP_AUDIT_DDL = """
CREATE TABLE IF NOT EXISTS mcp_audit (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp REAL NOT NULL,
    server_name TEXT NOT NULL,
    tool_name TEXT NOT NULL,
    user_id TEXT,
    arguments_hash TEXT,
    allowed INTEGER NOT NULL,
    decision_reason TEXT,
    success INTEGER NOT NULL,
    duration_ms REAL,
    error TEXT
);
CREATE INDEX IF NOT EXISTS idx_mcp_audit_ts ON mcp_audit(timestamp DESC);
CREATE INDEX IF NOT EXISTS idx_mcp_audit_server ON mcp_audit(server_name);
CREATE INDEX IF NOT EXISTS idx_mcp_audit_user ON mcp_audit(user_id);
CREATE INDEX IF NOT EXISTS idx_mcp_audit_allowed ON mcp_audit(allowed);
"""


def hash_arguments(arguments: dict[str, Any] | None) -> str:
    """Return the SHA-256 hex digest of the JSON-serialised arguments.

    Sorting keys makes the hash stable across dict insertion order so
    that identical invocations group together in the audit log.
    ``default=str`` mirrors ``tool_audit.py`` so non-JSON-native types
    (datetime, Path) do not crash the hash.
    """
    if not arguments:
        return hashlib.sha256(b"{}").hexdigest()
    try:
        payload = json.dumps(arguments, sort_keys=True, default=str, ensure_ascii=False)
    except (TypeError, ValueError):
        # Last-resort fallback: str() the whole thing so we still get
        # *some* hash rather than crashing the audit path.
        payload = repr(arguments)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class MCPAuditLogger:
    """SQLite-backed audit log for MCP tool invocations.

    Parameters
    ----------
    db_path:
        Optional explicit database path. When omitted, the path is
        resolved via :func:`maop.core.db_utils.get_db_path("mcp_audit")`
        so it follows the same unified / per-module routing as the rest
        of the codebase. Tests typically pass an in-memory or temp path
        to isolate themselves from the production DB.
    """

    def __init__(self, db_path: Path | str | None = None) -> None:
        if db_path is None:
            db_path = get_db_path("mcp_audit")
        self._db_path = Path(str(db_path))
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.executescript(_MCP_AUDIT_DDL)

    def _connect(self):
        return sqlite_connect(self._db_path, foreign_keys=False)

    # ── Write path ──────────────────────────────────────────────

    def log_call(self, record: MCPAuditRecord) -> int:
        """Persist a single audit record. Returns the inserted row id."""
        with self._connect() as conn:
            cursor = conn.execute(
                """INSERT INTO mcp_audit
                   (timestamp, server_name, tool_name, user_id, arguments_hash,
                    allowed, decision_reason, success, duration_ms, error)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    record.timestamp,
                    record.server_name,
                    record.tool_name,
                    record.user_id,
                    record.arguments_hash,
                    1 if record.allowed else 0,
                    record.decision_reason,
                    1 if record.success else 0,
                    record.duration_ms,
                    record.error,
                ),
            )
            row_id = cursor.lastrowid or 0
        logger.debug(
            "[mcp_audit] logged server=%s tool=%s user=%s allowed=%s success=%s",
            record.server_name, record.tool_name, record.user_id,
            record.allowed, record.success,
        )
        return row_id

    # ── Read path ───────────────────────────────────────────────

    def query(
        self,
        server_name: str | None = None,
        user_id: str | None = None,
        allowed: bool | None = None,
        since: float | None = None,
        limit: int = 100,
    ) -> list[MCPAuditRecord]:
        """Query audit records. Results are newest-first."""
        sql = "SELECT * FROM mcp_audit WHERE 1=1"
        params: list[Any] = []

        if server_name is not None:
            sql += " AND server_name = ?"
            params.append(server_name)
        if user_id is not None:
            sql += " AND user_id = ?"
            params.append(user_id)
        if allowed is not None:
            sql += " AND allowed = ?"
            params.append(1 if allowed else 0)
        if since is not None:
            sql += " AND timestamp >= ?"
            params.append(since)

        sql += " ORDER BY timestamp DESC LIMIT ?"
        params.append(max(0, int(limit)))

        with self._connect() as conn:
            cursor = conn.execute(sql, params)
            cols = [d[0] for d in cursor.description] if cursor.description else []
            rows = cursor.fetchall()

        records: list[MCPAuditRecord] = []
        for row in rows:
            d = dict(zip(cols, row))
            records.append(MCPAuditRecord(
                timestamp=float(d.get("timestamp", 0.0)),
                server_name=d.get("server_name", ""),
                tool_name=d.get("tool_name", ""),
                user_id=d.get("user_id", "") or "",
                arguments_hash=d.get("arguments_hash", "") or "",
                allowed=bool(d.get("allowed", 1)),
                decision_reason=d.get("decision_reason", "") or "",
                success=bool(d.get("success", 1)),
                duration_ms=float(d.get("duration_ms", 0.0) or 0.0),
                error=d.get("error"),
            ))
        return records

    def count(
        self,
        server_name: str | None = None,
        allowed: bool | None = None,
    ) -> int:
        """Count audit records matching the given filters."""
        sql = "SELECT COUNT(*) FROM mcp_audit WHERE 1=1"
        params: list[Any] = []
        if server_name is not None:
            sql += " AND server_name = ?"
            params.append(server_name)
        if allowed is not None:
            sql += " AND allowed = ?"
            params.append(1 if allowed else 0)

        with self._connect() as conn:
            row = conn.execute(sql, params).fetchone()
        return int(row[0]) if row else 0

    def prune(self, older_than_days: int = 30) -> int:
        """Delete records older than ``older_than_days``. Returns count removed."""
        cutoff = time.time() - (max(0, int(older_than_days)) * 86400)
        with self._connect() as conn:
            cursor = conn.execute(
                "DELETE FROM mcp_audit WHERE timestamp < ?",
                (cutoff,),
            )
            removed = cursor.rowcount
        logger.info(
            "[mcp_audit] pruned %d records older than %d days",
            removed, older_than_days,
        )
        return int(removed)
