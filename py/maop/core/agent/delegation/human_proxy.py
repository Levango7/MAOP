"""MAOP Human Proxy - Approval queue for human-in-the-loop decisions.

Human-in-the-loop approval queue. to pure Python with SQLite-backed persistence.
Actions: request, approve, reject, list, pending, resolve, notify, config.
"""

from __future__ import annotations

import contextlib
import json
import logging
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, cast

from pydantic import BaseModel, Field

from maop.core.backends.db_utils import get_db_path, sqlite_connect

logger = logging.getLogger(__name__)

# ADR-011: human_queue.db (SQLite) is the single source of truth for human
# approval requests. The legacy human-queue.json mirror has been fully removed;
# all access goes through SQLite. Included in DEFAULT_DATABASES for backup.


# ── Models ──────────────────────────────────────────────────────

class ApprovalRequest(BaseModel):
    """A single approval request."""
    id: str = ""
    task: str = ""
    agent: str = ""
    requester: str = "system"
    priority: str = "medium"  # low | medium | high | critical
    reason: str = ""
    status: str = "pending"  # pending | approved | rejected | expired
    created: str = ""
    resolved: str | None = None
    resolved_by: str = ""  # 审批决议的操作者（此前 resolver 参数未持久化，审计链不可归属）
    metadata: dict[str, Any] = Field(default_factory=dict)


class HumanProxyConfig(BaseModel):
    """Human proxy configuration."""
    auto_expire_hours: int = 24
    notify_on_request: bool = True
    max_pending: int = 100


# ── HumanProxy ────────────────────────────────────────────────

class HumanProxy:
    """Human-in-the-loop approval queue.

    Usage::

        proxy = HumanProxy(root_dir="/path/to/MAOP")
        req_id = proxy.request(task="Deploy to prod", agent="claude", reason="Production deployment")
        pending = proxy.pending()
        proxy.approve(req_id)
    """

    def __init__(self, root_dir: str | Path, config: HumanProxyConfig | None = None) -> None:
        self._root = Path(root_dir)
        self._db_path = get_db_path("human_proxy")
        self._config = config or HumanProxyConfig()
        self._ensure_db()

    def _ensure_db(self) -> None:
        """Create table if not exists."""
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS approval_requests (
                    id TEXT PRIMARY KEY,
                    task TEXT NOT NULL,
                    agent TEXT DEFAULT '',
                    requester TEXT DEFAULT 'system',
                    priority TEXT DEFAULT 'medium',
                    reason TEXT DEFAULT '',
                    status TEXT DEFAULT 'pending',
                    created TEXT NOT NULL,
                    resolved TEXT,
                    resolved_by TEXT DEFAULT '',
                    metadata TEXT DEFAULT '{}'
                )
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_approval_status
                ON approval_requests(status, priority)
            """)
            # 存量库补列：resolved_by（审批人）。CREATE TABLE IF NOT EXISTS 不会给旧表加列
            cols = {r[1] for r in conn.execute("PRAGMA table_info(approval_requests)").fetchall()}
            if "resolved_by" not in cols:
                conn.execute("ALTER TABLE approval_requests ADD COLUMN resolved_by TEXT DEFAULT ''")

    def _connect(self):
        return sqlite_connect(self._db_path, foreign_keys=False)

    def _new_id(self) -> str:
        ts = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        return f"hr-{ts}-{uuid.uuid4().hex[:8]}"

    # ── Actions ──────────────────────────────────────────────

    def request(
        self,
        task: str,
        agent: str = "",
        requester: str = "system",
        priority: str = "medium",
        reason: str = "",
        metadata: dict[str, Any] | None = None,
        request_id: str = "",
    ) -> str:
        """Submit a new approval request. Returns request ID."""
        req_id = request_id or self._new_id()
        now = datetime.now(timezone.utc).isoformat()
        meta_json = json.dumps(metadata or {}, ensure_ascii=False)

        with self._connect() as conn:
            conn.execute(
                "INSERT INTO approval_requests (id, task, agent, requester, priority, reason, status, created, metadata) "
                "VALUES (?, ?, ?, ?, ?, ?, 'pending', ?, ?)",
                (req_id, task, agent, requester, priority, reason, now, meta_json),
            )

        logger.info("Approval requested: %s (%s priority)", req_id, priority)
        return req_id

    def approve(self, request_id: str, resolver: str = "system") -> bool:
        """Approve a pending request."""
        now = datetime.now(timezone.utc).isoformat()
        with self._connect() as conn:
            cursor = conn.execute(
                "UPDATE approval_requests SET status='approved', resolved=?, resolved_by=? WHERE id=? AND status='pending'",
                (now, resolver, request_id),
            )
            if cursor.rowcount == 0:
                logger.warning("Approve failed: %s not found or not pending", request_id)
                return False
        logger.info("Approved: %s", request_id)
        return True

    def reject(self, request_id: str, resolver: str = "system", reason: str = "") -> bool:
        """Reject a pending request."""
        now = datetime.now(timezone.utc).isoformat()
        with self._connect() as conn:
            cursor = conn.execute(
                "UPDATE approval_requests SET status='rejected', resolved=?, resolved_by=?, reason=? WHERE id=? AND status='pending'",
                (now, resolver, reason or "rejected", request_id),
            )
            if cursor.rowcount == 0:
                return False
        logger.info("Rejected: %s", request_id)
        return True

    def pending(self, limit: int = 50) -> list[ApprovalRequest]:
        """List all pending requests, sorted by priority."""
        with self._connect() as conn:
            # SQL 内直接按优先级+时间排序（修复：先按时间截断再内存排序会让
            # 积压超过 limit*2 时较旧的 critical 请求被挤出候选集而饿死）。
            rows = conn.execute(
                "SELECT * FROM approval_requests WHERE status='pending' "
                "ORDER BY CASE priority WHEN 'critical' THEN 0 WHEN 'high' THEN 1 "
                "WHEN 'medium' THEN 2 WHEN 'low' THEN 3 ELSE 4 END, created DESC LIMIT ?",
                (limit,),
            ).fetchall()

        results = [self._row_to_request(r) for r in rows]
        return results

    def list_all(self, status: str = "", limit: int = 100) -> list[ApprovalRequest]:
        """List requests, optionally filtered by status."""
        with self._connect() as conn:
            if status:
                rows = conn.execute(
                    "SELECT * FROM approval_requests WHERE status=? ORDER BY created DESC LIMIT ?",
                    (status, limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM approval_requests ORDER BY created DESC LIMIT ?",
                    (limit,),
                ).fetchall()
        return [self._row_to_request(r) for r in rows]

    def resolve(self, request_id: str, decision: str, resolver: str = "system") -> bool:
        """Resolve a request (approve or reject)."""
        if decision == "approve":
            return self.approve(request_id, resolver)
        elif decision == "reject":
            return self.reject(request_id, resolver)
        logger.error("Unknown decision: %s", decision)
        return False

    def get(self, request_id: str) -> ApprovalRequest | None:
        """Get a specific request by ID."""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM approval_requests WHERE id=?", (request_id,)
            ).fetchone()
        if row is None:
            return None
        return self._row_to_request(row)

    def expire_old(self, hours: int = 0) -> int:
        """Mark old pending requests as expired."""
        expire_hours = hours or self._config.auto_expire_hours
        cutoff = datetime.now(timezone.utc).isoformat()
        with self._connect() as conn:
            cursor = conn.execute(
                # julianday 归一化比较：created 是 ISO「T+时区」格式，此前直接与
                # datetime() 的空格分隔文本比较，同一日历日内 'T'(0x54) > ' '(0x20)
                # 导致 created < cutoff 恒 false（当天永不 expire）。
                "UPDATE approval_requests SET status='expired', resolved=? "
                "WHERE status='pending' AND julianday(created) < julianday(?, ?)",
                (cutoff, "now", f"-{expire_hours} hours"),
            )
            if cursor.rowcount > 0:
                pass  # expired entries updated in SQLite
            return cast(int, cursor.rowcount)

    def stats(self) -> dict[str, int]:
        """Get queue statistics."""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT status, COUNT(*) as cnt FROM approval_requests GROUP BY status"
            ).fetchall()
        return {r["status"]: r["cnt"] for r in rows}

    # ── Internal ─────────────────────────────────────────────

    def _row_to_request(self, row: sqlite3.Row) -> ApprovalRequest:
        meta = {}
        with contextlib.suppress(json.JSONDecodeError, ValueError):
            meta = json.loads(row["metadata"] or "{}")
        return ApprovalRequest(
            id=row["id"],
            task=row["task"],
            agent=row["agent"],
            requester=row["requester"],
            priority=row["priority"],
            reason=row["reason"],
            status=row["status"],
            created=row["created"],
            resolved=row["resolved"],
            resolved_by=row["resolved_by"],
            metadata=meta,
        )
