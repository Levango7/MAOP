"""MAOP Tool Audit Log — Records every tool call for traceability and debugging.

Each tool invocation is logged with its inputs, outputs, duration, and result
status. This enables post-hoc analysis of agent behavior, cost tracking, and
debugging of failed tool calls.

Usage::

    from maop.core.tool_audit import ToolAuditLog

    audit = ToolAuditLog(root_dir="/path/to/MAOP")

    entry_id = audit.record(
        tool_name="file_read",
        agent="claude",
        inputs={"path": "/etc/config.yaml"},
        output="yaml_content...",
        duration_ms=45,
        success=True,
    )

    # Query recent tool calls
    entries = audit.query(tool_name="file_read", limit=20)

    # Get statistics
    stats = audit.stats()
"""

from __future__ import annotations

import json
import logging
import time
import uuid
from pathlib import Path
from typing import Any, cast

from pydantic import BaseModel, Field

from maop.core.db_utils import get_db_path, sqlite_connect

logger = logging.getLogger(__name__)


class ToolAuditEntry(BaseModel):
    """A single tool audit log entry."""
    id: str = Field(default_factory=lambda: uuid.uuid4().hex[:16])
    tool_name: str = ""
    agent: str = ""
    inputs: dict[str, Any] = Field(default_factory=dict)
    output: str = ""
    duration_ms: int = 0
    success: bool = True
    error_message: str = ""
    created_at: float = Field(default_factory=time.time)


class ToolAuditStats(BaseModel):
    """Aggregate statistics for tool calls."""
    total_calls: int = 0
    success_calls: int = 0
    failed_calls: int = 0
    avg_duration_ms: float = 0.0
    by_tool: dict[str, int] = Field(default_factory=dict)
    by_agent: dict[str, int] = Field(default_factory=dict)


_TOOL_AUDIT_DDL = """
CREATE TABLE IF NOT EXISTS tool_audit_log (
    id TEXT PRIMARY KEY,
    tool_name TEXT NOT NULL,
    agent TEXT DEFAULT '',
    inputs TEXT DEFAULT '{}',
    output TEXT DEFAULT '',
    duration_ms INTEGER DEFAULT 0,
    success INTEGER DEFAULT 1,
    error_message TEXT DEFAULT '',
    created_at REAL NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_audit_tool ON tool_audit_log(tool_name);
CREATE INDEX IF NOT EXISTS idx_audit_agent ON tool_audit_log(agent);
CREATE INDEX IF NOT EXISTS idx_audit_success ON tool_audit_log(success);
CREATE INDEX IF NOT EXISTS idx_audit_created ON tool_audit_log(created_at DESC);
"""


class ToolAuditLog:
    """Audit log for all tool invocations.

    Parameters
    ----------
    root_dir : str | Path
        MAOP project root directory.
    """

    def __init__(self, root_dir: str | Path) -> None:
        self._root = Path(root_dir)
        self._data_dir = self._root / "data"
        self._data_dir.mkdir(parents=True, exist_ok=True)
        self._db_path = get_db_path("tool_audit")
        self._init_db()

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.executescript(_TOOL_AUDIT_DDL)

    def _connect(self):
        return sqlite_connect(self._db_path, foreign_keys=False)

    def record(
        self,
        tool_name: str,
        agent: str = "",
        inputs: dict[str, Any] | None = None,
        output: str = "",
        duration_ms: int = 0,
        success: bool = True,
        error_message: str = "",
    ) -> str:
        """Record a tool invocation.

        Returns the audit entry ID.
        """
        entry = ToolAuditEntry(
            tool_name=tool_name,
            agent=agent,
            inputs=inputs or {},
            output=output,
            duration_ms=duration_ms,
            success=success,
            error_message=error_message,
        )
        with self._connect() as conn:
            conn.execute(
                """INSERT INTO tool_audit_log
                   (id, tool_name, agent, inputs, output, duration_ms,
                    success, error_message, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (entry.id, entry.tool_name, entry.agent,
                 json.dumps(entry.inputs, default=str),
                 entry.output, entry.duration_ms,
                 1 if entry.success else 0,
                 entry.error_message, entry.created_at),
            )
        logger.debug("Tool audit: %s by %s (%dms, success=%s)",
                      tool_name, agent, duration_ms, success)
        return entry.id

    def query(
        self,
        tool_name: str = "",
        agent: str = "",
        success: bool | None = None,
        limit: int = 50,
        since: float = 0.0,
    ) -> list[ToolAuditEntry]:
        """Query tool audit entries with optional filters."""
        sql = "SELECT * FROM tool_audit_log WHERE 1=1"
        params: list[Any] = []

        if tool_name:
            sql += " AND tool_name = ?"
            params.append(tool_name)
        if agent:
            sql += " AND agent = ?"
            params.append(agent)
        if success is not None:
            sql += " AND success = ?"
            params.append(1 if success else 0)
        if since > 0:
            sql += " AND created_at >= ?"
            params.append(since)

        sql += " ORDER BY created_at DESC LIMIT ?"
        params.append(limit)

        with self._connect() as conn:
            cursor = conn.execute(sql, params)
            cols = [d[0] for d in cursor.description] if cursor.description else []
            rows = cursor.fetchall()

        entries = []
        for row in rows:
            d = dict(zip(cols, row))
            entries.append(ToolAuditEntry(
                id=d["id"],
                tool_name=d["tool_name"],
                agent=d.get("agent", ""),
                inputs=json.loads(d.get("inputs", "{}")),
                output=d.get("output", ""),
                duration_ms=d.get("duration_ms", 0),
                success=bool(d.get("success", 1)),
                error_message=d.get("error_message", ""),
                created_at=d["created_at"],
            ))
        return entries

    def stats(self, since: float = 0.0) -> ToolAuditStats:
        """Get aggregate statistics for tool calls."""
        with self._connect() as conn:
            where = "WHERE created_at >= ?" if since > 0 else ""
            params: list[Any] = [since] if since > 0 else []

            total = conn.execute(
                f"SELECT COUNT(*) FROM tool_audit_log {where}", params
            ).fetchone()[0]

            success_count = conn.execute(
                f"SELECT COUNT(*) FROM tool_audit_log {where} {'AND' if where else 'WHERE'} success = 1",
                params,
            ).fetchone()[0]

            avg_dur = conn.execute(
                f"SELECT AVG(duration_ms) FROM tool_audit_log {where}", params
            ).fetchone()[0] or 0.0

            by_tool = dict(conn.execute(
                f"SELECT tool_name, COUNT(*) FROM tool_audit_log {where} GROUP BY tool_name",
                params,
            ).fetchall())

            by_agent = dict(conn.execute(
                f"SELECT agent, COUNT(*) FROM tool_audit_log {where} GROUP BY agent",
                params,
            ).fetchall())

        return ToolAuditStats(
            total_calls=total,
            success_calls=success_count,
            failed_calls=total - success_count,
            avg_duration_ms=round(avg_dur, 1),
            by_tool=by_tool,
            by_agent=by_agent,
        )

    def cleanup(self, max_age_days: int = 90) -> int:
        """Remove audit entries older than max_age_days. Returns count removed."""
        cutoff = time.time() - (max_age_days * 86400)
        with self._connect() as conn:
            cursor = conn.execute(
                "DELETE FROM tool_audit_log WHERE created_at < ?", (cutoff,)
            )
            removed = cursor.rowcount
        logger.info("[tool_audit] Cleaned up %d entries older than %d days", removed, max_age_days)
        return cast(int, removed)