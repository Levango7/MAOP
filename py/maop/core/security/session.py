"""MAOP Session Manager — Persistent conversation sessions with metadata.

Provides:
  - Session CRUD (create / get / list / delete)
  - Session metadata (agent, workdir, tags, status)
  - SQLite persistence for session state
  - Session resumption: pick up where you left off
  - Token budget tracking per session

Usage::

    from maop.core.security.session import SessionManager

    mgr = SessionManager(root_dir="/path/to/MAOP")
    sid = mgr.create(agent="mavis", workdir="/project")
    mgr.update(sid, status="active")
    session = mgr.get(sid)
"""

from __future__ import annotations

import contextlib
import json
import logging
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, cast

from pydantic import BaseModel, Field

from maop.core.backends.db_utils import get_db_path, sqlite_connect, validate_identifier

logger = logging.getLogger(__name__)


class SessionStatus(str):
    ACTIVE = "active"
    PAUSED = "paused"
    COMPLETED = "completed"
    FAILED = "failed"
    ARCHIVED = "archived"


class Session(BaseModel):
    id: str = ""
    agent: str = ""
    workdir: str = ""
    status: str = SessionStatus.ACTIVE
    tags: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
    token_count: int = 0
    token_budget: int = 0
    message_count: int = 0
    created_at: str = ""
    updated_at: str = ""
    last_active_at: str = ""


class SessionManager:
    """Manage persistent conversation sessions.

    Usage::

        mgr = SessionManager(root_dir="/path/to/MAOP")
        sid = mgr.create(agent="mavis", workdir="/project")
        session = mgr.get(sid)
        mgr.update(sid, status="paused")
    """

    def __init__(self, root_dir: str | Path) -> None:
        self._root = Path(root_dir)
        self._db_path = get_db_path("session")
        self._ensure_db()

    def _ensure_db(self) -> None:
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS sessions (
                    id TEXT PRIMARY KEY,
                    agent TEXT NOT NULL DEFAULT '',
                    workdir TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL DEFAULT 'active',
                    tags TEXT NOT NULL DEFAULT '[]',
                    metadata TEXT NOT NULL DEFAULT '{}',
                    token_count INTEGER NOT NULL DEFAULT 0,
                    token_budget INTEGER NOT NULL DEFAULT 0,
                    message_count INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    last_active_at TEXT NOT NULL
                )
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_sessions_status
                ON sessions(status)
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_sessions_agent
                ON sessions(agent)
            """)

    def _connect(self):
        return sqlite_connect(self._db_path, foreign_keys=False)

    def create(
        self,
        agent: str = "",
        workdir: str = "",
        tags: list[str] | None = None,
        metadata: dict[str, Any] | None = None,
        token_budget: int = 0,
    ) -> str:
        now = datetime.now(timezone.utc).isoformat()
        sid = f"sess-{uuid.uuid4().hex[:12]}"
        session = Session(
            id=sid,
            agent=agent,
            workdir=workdir,
            status=SessionStatus.ACTIVE,
            tags=tags or [],
            metadata=metadata or {},
            token_budget=token_budget,
            created_at=now,
            updated_at=now,
            last_active_at=now,
        )
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO sessions (id, agent, workdir, status, tags, metadata, "
                "token_count, token_budget, message_count, created_at, updated_at, last_active_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (session.id, session.agent, session.workdir, session.status,
                 json.dumps(session.tags), json.dumps(session.metadata),
                 session.token_count, session.token_budget, session.message_count,
                 session.created_at, session.updated_at, session.last_active_at),
            )
        logger.info("[session] Created %s agent=%s", sid, agent)
        return sid

    def get(self, session_id: str) -> Session | None:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM sessions WHERE id=?", (session_id,)).fetchone()
        if row is None:
            return None
        return self._row_to_session(row)

    def list(
        self,
        *,
        status: str = "",
        agent: str = "",
        limit: int = 50,
    ) -> list[Session]:
        with self._connect() as conn:
            clauses = []
            params: list[Any] = []
            if status:
                clauses.append("status=?")
                params.append(status)
            if agent:
                clauses.append("agent=?")
                params.append(agent)
            where = " WHERE " + " AND ".join(clauses) if clauses else ""
            rows = conn.execute(
                f"SELECT * FROM sessions{where} ORDER BY last_active_at DESC LIMIT ?",
                (*params, limit),
            ).fetchall()
        return [self._row_to_session(r) for r in rows]

    # ── 任务历史页: 搜索 / 过滤 / 分页 / 排序 ───────────────────────
    # P1-3: Overview「最近委托」只展示若干条, 用户无法回溯历史。
    # list_paginated 在 SQL 层完成过滤+排序+分页, 避免全表读入内存。
    # 搜索字段: agent / workdir / metadata.task / metadata.description /
    # metadata.prompt — 用 LIKE 拼接, 命中任一即算匹配。
    _SORTABLE_COLUMNS = {  # noqa: RUF012
        "created_at": "created_at",
        "updated_at": "updated_at",
        "last_active_at": "last_active_at",
        "agent": "agent",
        "status": "status",
        "name": "agent",  # 任务名称兜底映射到 agent
    }

    def list_paginated(
        self,
        *,
        status: str = "",
        search: str = "",
        page: int = 1,
        limit: int = 20,
        sort: str = "created_at",
        order: str = "desc",
    ) -> dict[str, Any]:
        """Return a paginated, filtered, searchable slice of sessions.

        返回结构::

            {
              "items": [Session, ...],
              "total": 100,
              "page": 1,
              "limit": 20,
              "total_pages": 5,
            }

        - ``status`` 为 ``"all"`` 或空串时不按状态过滤; 否则按精确匹配。
        - ``search`` 在 agent / workdir / metadata 文本上做 LIKE 模糊匹配。
        - ``sort`` 仅允许白名单列(见 _SORTABLE_COLUMNS), 防止 SQL 注入。
        - ``order`` 仅接受 ``asc`` / ``desc``。
        """
        # 参数归一化与裁剪
        page = max(1, int(page))
        limit = max(1, min(100, int(limit)))
        order = "asc" if str(order).lower() == "asc" else "desc"
        sort_col = self._SORTABLE_COLUMNS.get(sort, "created_at")

        clauses: list[str] = []
        params: list[Any] = []

        # 状态过滤: running → active (会话状态枚举为 active/paused/completed/failed/archived)
        if status and status != "all":
            # 兼容前端语义: running 映射到 active
            mapped = "active" if status == "running" else status
            clauses.append("status=?")
            params.append(mapped)

        # 关键词搜索: agent / workdir / metadata 文本
        if search:
            kw = f"%{search}%"
            clauses.append(
                "(agent LIKE ? OR workdir LIKE ? OR metadata LIKE ? OR tags LIKE ?)"
            )
            params.extend([kw, kw, kw, kw])

        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        offset = (page - 1) * limit

        with self._connect() as conn:
            total_row = conn.execute(
                f"SELECT COUNT(*) AS c FROM sessions{where}", params
            ).fetchone()
            total = int(total_row["c"]) if total_row else 0

            rows = conn.execute(
                f"SELECT * FROM sessions{where} "
                f"ORDER BY {sort_col} {order.upper()} LIMIT ? OFFSET ?",
                (*params, limit, offset),
            ).fetchall()

        items = [self._row_to_session(r).model_dump() for r in rows]
        total_pages = (total + limit - 1) // limit if total else 0
        return {
            "items": items,
            "total": total,
            "page": page,
            "limit": limit,
            "total_pages": total_pages,
        }

    def update(
        self,
        session_id: str,
        *,
        status: str | None = None,
        agent: str | None = None,
        workdir: str | None = None,
        tags: list[str] | None = None,  # type: ignore[valid-type]
        metadata: dict[str, Any] | None = None,
        token_count: int | None = None,
        token_budget: int | None = None,
        message_count: int | None = None,
    ) -> bool:
        session = self.get(session_id)
        if session is None:
            return False
        now = datetime.now(timezone.utc).isoformat()
        updates: dict[str, Any] = {"updated_at": now, "last_active_at": now}
        if status is not None:
            updates["status"] = status
        if agent is not None:
            updates["agent"] = agent
        if workdir is not None:
            updates["workdir"] = workdir
        if tags is not None:
            updates["tags"] = json.dumps(tags)
        if metadata is not None:
            updates["metadata"] = json.dumps(metadata)
        if token_count is not None:
            updates["token_count"] = token_count
        if token_budget is not None:
            updates["token_budget"] = token_budget
        if message_count is not None:
            updates["message_count"] = message_count
        for key in updates:
            validate_identifier(key, "session column")
        set_clause = ", ".join(f"{k}=?" for k in updates)
        values = list(updates.values()) + [session_id]
        with self._connect() as conn:
            conn.execute(f"UPDATE sessions SET {set_clause} WHERE id=?", values)
        return True

    def delete(self, session_id: str) -> bool:
        with self._connect() as conn:
            cursor = conn.execute("DELETE FROM sessions WHERE id=?", (session_id,))
            return cast(bool, cursor.rowcount > 0)

    def touch(self, session_id: str) -> bool:
        now = datetime.now(timezone.utc).isoformat()
        with self._connect() as conn:
            cursor = conn.execute(
                "UPDATE sessions SET last_active_at=?, updated_at=? WHERE id=?",
                (now, now, session_id),
            )
            return cast(bool, cursor.rowcount > 0)

    def add_tokens(self, session_id: str, count: int) -> bool:
        with self._connect() as conn:
            cursor = conn.execute(
                "UPDATE sessions SET token_count=token_count+?, last_active_at=? WHERE id=?",
                (count, datetime.now(timezone.utc).isoformat(), session_id),
            )
            return cast(bool, cursor.rowcount > 0)

    def is_over_budget(self, session_id: str) -> bool:
        session = self.get(session_id)
        if session is None or session.token_budget <= 0:
            return False
        return session.token_count >= session.token_budget

    def stats(self) -> dict[str, Any]:
        with self._connect() as conn:
            total = conn.execute("SELECT COUNT(*) as c FROM sessions").fetchone()["c"]
            active = conn.execute("SELECT COUNT(*) as c FROM sessions WHERE status='active'").fetchone()["c"]
            by_status = conn.execute(
                "SELECT status, COUNT(*) as c FROM sessions GROUP BY status"
            ).fetchall()
        return {
            "total": total,
            "active": active,
            "by_status": {r["status"]: r["c"] for r in by_status},
        }

    def rerun(self, session_id: str) -> Session | None:
        """创建一个新会话作为指定会话的重跑副本。

        复制原会话的 agent / workdir / tags / metadata / token_budget,
        生成新的 session id 并以 ``active`` 状态入库。原会话保持不变。

        返回新建的 Session; 若原会话不存在则返回 None。
        """
        origin = self.get(session_id)
        if origin is None:
            return None
        # 在 metadata 中标记来源, 便于历史页展示「重跑自 xxx」
        new_metadata = dict(origin.metadata)
        new_metadata.setdefault("rerun_from", origin.id)
        new_metadata.setdefault("rerun_at", datetime.now(timezone.utc).isoformat())
        new_sid = self.create(
            agent=origin.agent,
            workdir=origin.workdir,
            tags=list(origin.tags),
            metadata=new_metadata,
            token_budget=origin.token_budget,
        )
        return self.get(new_sid)

    def _row_to_session(self, row: Any) -> Session:
        tags = []
        with contextlib.suppress(json.JSONDecodeError, TypeError):
            tags = json.loads(row["tags"])
        metadata = {}
        with contextlib.suppress(json.JSONDecodeError, TypeError):
            metadata = json.loads(row["metadata"])
        return Session(
            id=row["id"],
            agent=row["agent"],
            workdir=row["workdir"],
            status=row["status"],
            tags=tags,
            metadata=metadata,
            token_count=row["token_count"],
            token_budget=row["token_budget"],
            message_count=row["message_count"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            last_active_at=row["last_active_at"],
        )
