"""MAOP Subagent System — Hierarchical agent delegation with lifecycle management.

Provides:
  - AgentTree: parent/child relationship tracking
  - SubagentManager: spawn/terminate sub-agents, message passing
  - Recursive delegation: agent A → agent B → agent C
  - Inter-agent communication channel (message queue based)

Usage::

    from maop.core.subagent import SubagentManager

    mgr = SubagentManager(root_dir="/path/to/MAOP")
    child = mgr.spawn(parent="orchestrator", agent="coder", task="fix bug")
    mgr.send(parent="orchestrator", child_id=child.id, message={"type": "context", "data": ...})
    result = mgr.collect(child_id=child.id)
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from maop.core.db_utils import sqlite_connect
from maop.core.subagent_db import get_subagent_db_path, migrate_legacy_subagent_db

logger = logging.getLogger(__name__)


# ── Models ──────────────────────────────────────────────────────

class SubagentInfo(BaseModel):
    id: str
    parent_agent: str
    child_agent: str
    task: str = ""
    status: str = "spawned"
    created_at: str = ""
    finished_at: str = ""
    exit_code: int | None = None
    depth: int = 0


class AgentMessage(BaseModel):
    id: str = ""
    sender: str
    recipient: str
    msg_type: str = "info"
    payload: dict[str, Any] = Field(default_factory=dict)
    created_at: str = ""


class AgentTreeNode(BaseModel):
    agent_name: str
    depth: int = 0
    parent: str | None = None
    children: list[str] = Field(default_factory=list)


# ── SubagentManager ─────────────────────────────────────────────

# ── Parallel Implementation Note ──────────────────────────────
# NOTE: SubagentManager is one of two parallel subagent implementations.
# The other is SubAgentManager in maop/core/subagent_lifecycle.py.
# Both have production callers:
#   - SubagentManager (this class): used by delegate/dispatcher.py (main dispatch)
#   - SubAgentManager: used by dashboard/routers/subagent.py (dashboard API)
# Both share the same ``subagents`` table via maop.core.subagent_db (unified
# schema = field superset of the two implementations) to avoid the previous
# dual-DB / dual-schema conflict where the second manager to initialize would
# find the wrong column set. Future work: consider merging into a single
# canonical implementation.

class SubagentManager:
    """Manage hierarchical agent delegation and inter-agent communication."""

    def __init__(self, root_dir: str | Path = "data") -> None:
        self._root = Path(root_dir)
        self._db_path = get_subagent_db_path()
        self._init_db()

    def _init_db(self) -> None:
        """初始化 subagent DB（共享 schema，自动迁移旧表）。

        使用 maop.core.subagent_db 的统一 schema（两套实现的字段超集），
        并自动迁移旧 DB 文件中可能存在的缺列场景。
        """
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        migrate_legacy_subagent_db()

    def spawn(
        self,
        parent: str,
        agent: str,
        task: str = "",
        max_depth: int = 5,
        *,
        call_chain: list[str] | None = None,
        max_self_ref_depth: int = 3,
    ) -> SubagentInfo:
        """Spawn a child agent under a parent. Enforces max recursion depth.

        Parameters
        ----------
        call_chain : list[str] | None
            The current delegation chain (e.g. ["MAOP", "mavis", "MAOP"]).
            Used for self-referential loop detection. If None, falls back
            to DB-based depth tracking.
        max_self_ref_depth : int
            Maximum times the same agent can appear in a call chain
            (prevents infinite self-delegation).
        """
        if call_chain is not None:
            child_depth = len(call_chain)
            self_refs = sum(1 for a in call_chain if a == agent)
            if self_refs >= max_self_ref_depth:
                raise ValueError(
                    f"Self-reference limit ({max_self_ref_depth}) exceeded for '{agent}' "
                    f"in chain: {' → '.join(call_chain + [agent])}"
                )
            if child_depth >= max_depth:
                raise ValueError(
                    f"Max subagent depth ({max_depth}) exceeded: {' → '.join(call_chain + [agent])}"
                )
        else:
            parent_depth = self._get_depth(parent)
            child_depth = parent_depth + 1
            if child_depth > max_depth:
                raise ValueError(f"Max subagent depth ({max_depth}) exceeded: {parent} → {agent}")

        sa_id = f"sa-{uuid.uuid4().hex[:8]}"
        now = datetime.now(timezone.utc).isoformat()
        info = SubagentInfo(
            id=sa_id, parent_agent=parent, child_agent=agent,
            task=task, status="spawned", created_at=now, depth=child_depth,
        )
        with sqlite_connect(self._db_path) as conn:
            conn.execute(
                "INSERT INTO subagents (id, parent_agent, child_agent, task, status, created_at, depth) VALUES (?,?,?,?,?,?,?)",
                (info.id, info.parent_agent, info.child_agent, info.task, info.status, info.created_at, info.depth),
            )
        logger.info("[subagent] Spawned %s under %s (depth=%d)", sa_id, parent, child_depth)
        return info

    def terminate(self, sa_id: str, exit_code: int = 0) -> SubagentInfo | None:
        """Mark a subagent as completed/failed."""
        info = self.get(sa_id)
        if info is None:
            return None
        now = datetime.now(timezone.utc).isoformat()
        status = "completed" if exit_code == 0 else "failed"
        with sqlite_connect(self._db_path) as conn:
            conn.execute(
                "UPDATE subagents SET status=?, finished_at=?, exit_code=? WHERE id=?",
                (status, now, exit_code, sa_id),
            )
        info.status = status
        info.finished_at = now
        info.exit_code = exit_code
        return info

    def get(self, sa_id: str) -> SubagentInfo | None:
        """Get subagent info by ID."""
        with sqlite_connect(self._db_path) as conn:
            row = conn.execute("SELECT * FROM subagents WHERE id=?", (sa_id,)).fetchone()
        if row is None:
            return None
        return SubagentInfo(**dict(row))

    def list_children(self, parent: str, status: str | None = None) -> list[SubagentInfo]:
        """List all child subagents of a parent agent."""
        with sqlite_connect(self._db_path) as conn:
            if status:
                rows = conn.execute(
                    "SELECT * FROM subagents WHERE parent_agent=? AND status=?",
                    (parent, status),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM subagents WHERE parent_agent=?", (parent,),
                ).fetchall()
        return [SubagentInfo(**dict(r)) for r in rows]

    def get_tree(self, root_agent: str) -> AgentTreeNode:
        """Build an agent tree starting from root_agent."""
        children = self.list_children(root_agent)
        child_names = []
        for c in children:
            child_names.append(c.child_agent)
        depth = self._get_depth(root_agent)
        parent = self._get_parent(root_agent)
        node = AgentTreeNode(agent_name=root_agent, depth=depth, parent=parent, children=child_names)
        return node

    def send(self, sender: str, recipient: str, msg_type: str = "info", payload: dict[str, Any] | None = None) -> AgentMessage:
        """Send a message from one agent to another."""
        msg_id = f"msg-{uuid.uuid4().hex[:8]}"
        now = datetime.now(timezone.utc).isoformat()
        import json as _json
        msg = AgentMessage(
            id=msg_id, sender=sender, recipient=recipient,
            msg_type=msg_type, payload=payload or {}, created_at=now,
        )
        with sqlite_connect(self._db_path) as conn:
            conn.execute(
                "INSERT INTO agent_messages (id, sender, recipient, msg_type, payload, created_at) VALUES (?,?,?,?,?,?)",
                (msg.id, msg.sender, msg.recipient, msg.msg_type, _json.dumps(msg.payload), msg.created_at),
            )
        return msg

    def receive(self, recipient: str, limit: int = 100) -> list[AgentMessage]:
        """Receive all pending messages for an agent."""
        import json as _json
        with sqlite_connect(self._db_path) as conn:
            rows = conn.execute(
                "SELECT * FROM agent_messages WHERE recipient=? ORDER BY created_at LIMIT ?",
                (recipient, limit),
            ).fetchall()
        messages = []
        for r in rows:
            d = dict(r)
            d["payload"] = _json.loads(d.get("payload", "{}"))
            messages.append(AgentMessage(**d))
        return messages

    def purge_messages(self, recipient: str) -> int:
        """Delete all messages for a recipient after processing."""
        with sqlite_connect(self._db_path) as conn:
            cursor = conn.execute("DELETE FROM agent_messages WHERE recipient=?", (recipient,))
        return cursor.rowcount

    def _get_depth(self, agent_name: str) -> int:
        with sqlite_connect(self._db_path) as conn:
            row = conn.execute(
                "SELECT MAX(depth) as max_depth FROM subagents WHERE child_agent=?",
                (agent_name,),
            ).fetchone()
        return row["max_depth"] if row and row["max_depth"] is not None else 0

    def _get_parent(self, agent_name: str) -> str | None:
        with sqlite_connect(self._db_path) as conn:
            row = conn.execute(
                "SELECT parent_agent FROM subagents WHERE child_agent=? ORDER BY created_at DESC LIMIT 1",
                (agent_name,),
            ).fetchone()
        return row["parent_agent"] if row else None
