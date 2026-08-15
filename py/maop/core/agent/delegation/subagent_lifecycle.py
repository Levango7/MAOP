"""MAOP SubAgent Manager — Unified sub-agent lifecycle and hierarchical delegation.

合并自 ``subagent_lifecycle.py``（async 生命周期）+ ``subagent_delegation.py``
（同步层级委派）。两套实现共享同一 ``subagents`` 表（通过 ``subagent_db`` 统一
schema），现合并为单一 ``SubAgentManager``，保留两套方法集：

- **生命周期方法**（async）：``spawn`` / ``wait`` / ``cancel`` /
  ``spawn_and_wait_all`` / ``list_agents`` / ``get_live_transcript``
- **委派方法**（sync）：``spawn_child`` / ``terminate`` / ``get`` /
  ``list_children`` / ``get_tree`` / ``send`` / ``receive`` / ``purge_messages``

向后兼容：``SubagentManager`` 作为 ``SubAgentManager`` 的别名保留。

Usage::

    from maop.core.agent.delegation.subagent_lifecycle import (
        SubAgentManager, AgentConfig, SubagentInfo,
    )

    mgr = SubAgentManager(root_dir="/path/to/MAOP")

    # Async lifecycle: spawn a single sub-agent
    agent_id = await mgr.spawn(
        config=AgentConfig(name="code-reviewer", model="deepseek-chat"),
        task="Review the authentication module",
        context={"files": ["auth/login.py"]},
    )
    result = await mgr.wait(agent_id, timeout=300)

    # Sync hierarchical delegation: spawn a child under a parent
    info = mgr.spawn_child(parent="orchestrator", agent="coder", task="fix bug")
    mgr.terminate(info.id, exit_code=0)
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from maop.core.agent.delegation.subagent_db import get_subagent_db_path, migrate_legacy_subagent_db
from maop.core.backends.db_utils import sqlite_connect

logger = logging.getLogger(__name__)


class AgentRole(str, Enum):
    LEAF = "leaf"
    ORCHESTRATOR = "orchestrator"


class AgentStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class AgentConfig(BaseModel):
    """Sub-agent configuration."""
    name: str = ""
    role: AgentRole = AgentRole.LEAF
    model: str = ""
    system_prompt: str = ""
    tools: list[str] = Field(default_factory=list)
    skills: list[str] = Field(default_factory=list)
    max_turns: int = 15
    max_spawn_depth: int = 1
    memory_layers: list[str] = Field(default_factory=lambda: ["working", "episodic", "semantic"])
    temperature: float = 0.7
    context_window: int = 128000


class AgentResult(BaseModel):
    """Result from a completed sub-agent."""
    agent_id: str = ""
    output: str = ""
    tool_calls: list[dict[str, Any]] = Field(default_factory=list)
    tokens_used: int = 0
    duration_ms: int = 0
    error: str = ""
    status: AgentStatus = AgentStatus.COMPLETED


class TranscriptEntry(BaseModel):
    """A single entry in the agent's live transcript."""
    agent_id: str = ""
    timestamp: float = Field(default_factory=time.time)
    event: str = ""
    data: dict[str, Any] = Field(default_factory=dict)


# ── Delegation Models (merged from subagent_delegation.py) ──────


class SubagentInfo(BaseModel):
    """Hierarchical delegation record (parent → child)."""
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
    """Inter-agent message (send/receive channel)."""
    id: str = ""
    sender: str
    recipient: str
    msg_type: str = "info"
    payload: dict[str, Any] = Field(default_factory=dict)
    created_at: str = ""


class AgentTreeNode(BaseModel):
    """Node in the agent delegation tree."""
    agent_name: str
    depth: int = 0
    parent: str | None = None
    children: list[str] = Field(default_factory=list)


# ── Parallel Implementation Note ──────────────────────────────
# NOTE: 本模块原为两套并行 subagent 实现之一（async 生命周期）。
# 另一套是 subagent_delegation.py 的同步层级委派实现。
# P0-2 (2026-08-07): 已将 subagent_delegation.py 的方法合并到本模块的
# SubAgentManager 中（spawn_child/terminate/get/list_children/get_tree/
# send/receive/purge_messages），subagent_delegation.py 现为重定向 shim。
# SubagentManager 作为 SubAgentManager 的别名保留以向后兼容。

class SubAgentManager:
    """Manage sub-agent lifecycle AND hierarchical delegation.

    合并自原 ``SubAgentManager``（async 生命周期）+ 原 ``SubagentManager``
    （同步层级委派）。两套方法集共享同一 ``subagents`` 表（通过
    ``subagent_db`` 统一 schema）。

    生命周期方法（async）::

        agent_id = await mgr.spawn(config, task, context)
        result = await mgr.wait(agent_id, timeout=300)
        mgr.cancel(agent_id)

    委派方法（sync）::

        info = mgr.spawn_child(parent="A", agent="B", task="...")
        mgr.terminate(info.id, exit_code=0)
        children = mgr.list_children("A")
        tree = mgr.get_tree("A")
        mgr.send(sender="A", recipient="B", payload={...})
        msgs = mgr.receive("B")

    Parameters
    ----------
    root_dir : str | Path
        MAOP project root directory.
    """

    def __init__(self, root_dir: str | Path) -> None:
        self._root = Path(root_dir)
        self._data_dir = self._root / "data"
        self._data_dir.mkdir(parents=True, exist_ok=True)
        self._db_path = get_subagent_db_path()
        self._init_db()
        self._running: dict[str, asyncio.Task[Any]] = {}

    def _init_db(self) -> None:
        """初始化 subagent DB（共享 schema，自动迁移旧表）。

        使用 maop.core.subagent_db 的统一 schema（两套实现的字段超集），
        并自动迁移旧 DB 文件中可能存在的缺列场景。
        """
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        migrate_legacy_subagent_db()

    def _connect(self):
        return sqlite_connect(self._db_path, foreign_keys=False)

    async def spawn(
        self,
        config: AgentConfig,
        task: str,
        context: dict[str, Any] | None = None,
    ) -> str:
        """Spawn a sub-agent. Returns the agent_id."""
        agent_id = uuid.uuid4().hex[:16]
        now = time.time()

        with self._connect() as conn:
            conn.execute(
                """INSERT INTO subagents
                   (id, name, role, model, task, context, status, config, created_at, started_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (agent_id, config.name, config.role.value, config.model,
                 task, json.dumps(context or {}), AgentStatus.RUNNING.value,
                 json.dumps(config.model_dump()), str(now), now),
            )

        self._append_transcript(agent_id, "spawned", {
            "task": task, "model": config.model,
        })

        atask = asyncio.create_task(
            self._execute_agent(agent_id, config, task, context or {}),
        )
        self._running[agent_id] = atask

        logger.debug("SubAgent spawned: %s (name=%s model=%s)", agent_id[:8], config.name, config.model)
        return agent_id

    async def wait(self, agent_id: str, timeout: int = 300) -> AgentResult:
        """Wait for a sub-agent to complete. Returns the result."""
        atask = self._running.get(agent_id)

        if atask is not None:
            try:
                result = await asyncio.wait_for(asyncio.shield(atask), timeout=timeout)
                if isinstance(result, AgentResult):
                    return result
            except asyncio.TimeoutError:
                self._update_status(agent_id, AgentStatus.FAILED, error="Timeout")
                return AgentResult(
                    agent_id=agent_id, error="Timeout",
                    status=AgentStatus.FAILED,
                )
            except Exception as exc:
                self._update_status(agent_id, AgentStatus.FAILED, error=str(exc))
                return AgentResult(
                    agent_id=agent_id, error=str(exc),
                    status=AgentStatus.FAILED,
                )

        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM subagents WHERE id = ?", (agent_id,),
            ).fetchone()
            if not row:
                return AgentResult(agent_id=agent_id, error="Not found", status=AgentStatus.FAILED)

            cols = [d[0] for d in conn.execute("SELECT * FROM subagents LIMIT 0").description]
            d = dict(zip(cols, row))

        return AgentResult(
            agent_id=d["id"],
            output=d.get("output", ""),
            tool_calls=json.loads(d.get("tool_calls", "[]")),
            tokens_used=d.get("tokens_used", 0),
            duration_ms=d.get("duration_ms", 0),
            error=d.get("error", ""),
            status=AgentStatus(d.get("status", "completed")),
        )

    async def spawn_and_wait_all(
        self,
        tasks: list[tuple[AgentConfig, str, dict[str, Any]]],
        timeout: int = 300,
    ) -> list[AgentResult]:
        """Spawn multiple sub-agents in parallel and wait for all to complete."""
        agent_ids = []
        for config, task, context in tasks:
            aid = await self.spawn(config, task, context)
            agent_ids.append(aid)

        results = await asyncio.gather(
            *[self.wait(aid, timeout=timeout) for aid in agent_ids],
            return_exceptions=True,
        )

        final: list[AgentResult] = []
        for r in results:
            if isinstance(r, AgentResult):
                final.append(r)
            else:
                final.append(AgentResult(error=str(r), status=AgentStatus.FAILED))
        return final

    def cancel(self, agent_id: str) -> bool:
        """Cancel a running sub-agent."""
        atask = self._running.get(agent_id)
        if atask is not None and not atask.done():
            atask.cancel()
            self._update_status(agent_id, AgentStatus.CANCELLED)
            self._append_transcript(agent_id, "cancelled", {})
            return True
        return False

    def get_live_transcript(self, agent_id: str, limit: int = 100) -> list[TranscriptEntry]:
        """Get the live execution transcript for a sub-agent."""
        with self._connect() as conn:
            cursor = conn.execute(
                """SELECT * FROM subagent_transcripts
                   WHERE agent_id = ? ORDER BY timestamp DESC LIMIT ?""",
                (agent_id, limit),
            )
            cols = [d[0] for d in cursor.description] if cursor.description else []
            rows = cursor.fetchall()

        return [
            TranscriptEntry(
                agent_id=d["agent_id"], timestamp=d["timestamp"],
                event=d.get("event", ""), data=json.loads(d.get("data", "{}")),
            )
            for d in (dict(zip(cols, row)) for row in rows)
        ]

    def list_agents(self, status: AgentStatus | None = None) -> list[dict[str, Any]]:
        """List all sub-agents, optionally filtered by status."""
        with self._connect() as conn:
            sql = "SELECT id, name, role, model, task, status, created_at FROM subagents WHERE 1=1"
            params: list[Any] = []
            if status:
                sql += " AND status = ?"
                params.append(status.value)
            sql += " ORDER BY created_at DESC"
            cursor = conn.execute(sql, params)
            cols = [d[0] for d in cursor.description] if cursor.description else []
            rows = cursor.fetchall()

        return [dict(zip(cols, row)) for row in rows]

    async def _execute_agent(
        self,
        agent_id: str,
        config: AgentConfig,
        task: str,
        context: dict[str, Any],
    ) -> AgentResult:
        """Execute a sub-agent task (placeholder for actual LLM call)."""
        start = time.time()
        self._append_transcript(agent_id, "started", {"task": task})

        try:
            output = await self._invoke_llm(agent_id, config, task, context)
            duration_ms = int((time.time() - start) * 1000)

            with self._connect() as conn:
                conn.execute(
                    """UPDATE subagents
                       SET status = ?, output = ?, duration_ms = ?, finished_at = ?
                       WHERE id = ?""",
                    (AgentStatus.COMPLETED.value, output, duration_ms, time.time(), agent_id),
                )

            self._append_transcript(agent_id, "completed", {"output_len": len(output)})
            return AgentResult(
                agent_id=agent_id, output=output,
                duration_ms=duration_ms, status=AgentStatus.COMPLETED,
            )
        except asyncio.CancelledError:
            self._update_status(agent_id, AgentStatus.CANCELLED)
            return AgentResult(agent_id=agent_id, status=AgentStatus.CANCELLED)
        except Exception as exc:
            self._update_status(agent_id, AgentStatus.FAILED, error=str(exc))
            self._append_transcript(agent_id, "failed", {"error": str(exc)})
            return AgentResult(
                agent_id=agent_id, error=str(exc), status=AgentStatus.FAILED,
            )
        finally:
            self._running.pop(agent_id, None)

    async def _invoke_llm(
        self,
        agent_id: str,
        config: AgentConfig,
        task: str,
        context: dict[str, Any],
    ) -> str:
        """Invoke the LLM for a sub-agent task.

        This is a hook that delegates to the actual LLM provider.
        Can be overridden or patched for testing.
        """
        self._append_transcript(agent_id, "llm_call", {
            "model": config.model, "task": task[:200],
        })

        try:
            from maop.core.agent.llm_chat.llm_provider import LLMProviderFactory
            factory = LLMProviderFactory(root_dir=str(self._root))
            provider = factory.get_provider(config.model)
            if provider is None:
                return f"[No provider for model '{config.model}']"

            messages = []
            if config.system_prompt:
                messages.append({"role": "system", "content": config.system_prompt})
            ctx_str = json.dumps(context, default=str)[:2000] if context else ""
            user_msg = f"Task: {task}"
            if ctx_str:
                user_msg += f"\n\nContext: {ctx_str}"
            messages.append({"role": "user", "content": user_msg})

            # 统一走 chat_with_fallback 以触发 _record_cost 成本记录与 fallback 链
            result = await factory.chat_with_fallback(
                messages, config.model,
                temperature=config.temperature,
                max_tokens=min(config.context_window // 4, 4096),
                agent=agent_id,
            )
            return result.response.content
        except Exception as exc:
            logger.warning("[subagent] LLM invocation failed for %s: %s", agent_id[:8], exc)
            return f"[LLM Error] {exc}"

    def _update_status(
        self,
        agent_id: str,
        status: AgentStatus,
        error: str = "",
    ) -> None:
        with self._connect() as conn:
            sets = ["status = ?"]
            params: list[Any] = [status.value]
            if error:
                sets.append("error = ?")
                params.append(error)
            if status in (AgentStatus.COMPLETED, AgentStatus.FAILED, AgentStatus.CANCELLED):
                sets.append("finished_at = ?")
                params.append(time.time())
            params.append(agent_id)
            conn.execute(
                f"UPDATE subagents SET {', '.join(sets)} WHERE id = ?", params,
            )

    def _append_transcript(
        self,
        agent_id: str,
        event: str,
        data: dict[str, Any],
    ) -> None:
        entry_id = uuid.uuid4().hex[:16]
        with self._connect() as conn:
            conn.execute(
                """INSERT INTO subagent_transcripts
                   (id, agent_id, timestamp, event, data) VALUES (?, ?, ?, ?, ?)""",
                (entry_id, agent_id, time.time(), event, json.dumps(data)),
            )

    # ── Hierarchical delegation methods (merged from subagent_delegation.py) ──
    # 以下方法原属 SubagentManager（同步层级委派），P0-2 合并迁入。
    # spawn 重命名为 spawn_child 以与 async spawn 区分（委派 vs 生命周期语义）。

    def spawn_child(
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
        with self._connect() as conn:
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
        with self._connect() as conn:
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
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM subagents WHERE id=?", (sa_id,)).fetchone()
        if row is None:
            return None
        return SubagentInfo(**dict(row))

    def list_children(self, parent: str, status: str | None = None) -> list[SubagentInfo]:
        """List all child subagents of a parent agent."""
        with self._connect() as conn:
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
        msg = AgentMessage(
            id=msg_id, sender=sender, recipient=recipient,
            msg_type=msg_type, payload=payload or {}, created_at=now,
        )
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO agent_messages (id, sender, recipient, msg_type, payload, created_at) VALUES (?,?,?,?,?,?)",
                (msg.id, msg.sender, msg.recipient, msg.msg_type, json.dumps(msg.payload), msg.created_at),
            )
        return msg

    def receive(self, recipient: str, limit: int = 100) -> list[AgentMessage]:
        """Receive all pending messages for an agent."""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM agent_messages WHERE recipient=? ORDER BY created_at LIMIT ?",
                (recipient, limit),
            ).fetchall()
        messages = []
        for r in rows:
            d = dict(r)
            d["payload"] = json.loads(d.get("payload", "{}"))
            messages.append(AgentMessage(**d))
        return messages

    def purge_messages(self, recipient: str) -> int:
        """Delete all messages for a recipient after processing."""
        with self._connect() as conn:
            cursor = conn.execute("DELETE FROM agent_messages WHERE recipient=?", (recipient,))
        return int(cursor.rowcount)

    def _get_depth(self, agent_name: str) -> int:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT MAX(depth) as max_depth FROM subagents WHERE child_agent=?",
                (agent_name,),
            ).fetchone()
        return row["max_depth"] if row and row["max_depth"] is not None else 0

    def _get_parent(self, agent_name: str) -> str | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT parent_agent FROM subagents WHERE child_agent=? ORDER BY created_at DESC LIMIT 1",
                (agent_name,),
            ).fetchone()
        return row["parent_agent"] if row else None


# ── Backward-compat alias ──────────────────────────────────────
# SubagentManager 作为 SubAgentManager 的别名保留，向后兼容旧调用方
# （如 maop.core.subagent.SubagentManager、delegate.dispatcher 等）。
SubagentManager = SubAgentManager
