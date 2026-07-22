"""MAOP SubAgent Manager — Spawn, wait, cancel sub-agent execution.

Provides a structured way to dispatch tasks to sub-agents, track their
lifecycle, and collect results — with support for parallel execution.

Usage::

    from maop.core.subagent_manager import SubAgentManager, AgentConfig

    mgr = SubAgentManager(root_dir="/path/to/MAOP")

    # Spawn a single sub-agent
    agent_id = await mgr.spawn(
        config=AgentConfig(name="code-reviewer", model="deepseek-chat"),
        task="Review the authentication module",
        context={"files": ["auth/login.py"]},
    )

    # Wait for completion
    result = await mgr.wait(agent_id, timeout=300)

    # Parallel execution
    results = await mgr.spawn_and_wait_all([
        (config_a, "Task A", {}),
        (config_b, "Task B", {}),
    ])
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from enum import Enum
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from maop.core.db_utils import get_db_path, sqlite_connect

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


_SUBAGENT_DDL = """
CREATE TABLE IF NOT EXISTS subagents (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    role TEXT DEFAULT 'leaf',
    model TEXT DEFAULT '',
    task TEXT DEFAULT '',
    context TEXT DEFAULT '{}',
    status TEXT DEFAULT 'pending',
    output TEXT DEFAULT '',
    tool_calls TEXT DEFAULT '[]',
    tokens_used INTEGER DEFAULT 0,
    duration_ms INTEGER DEFAULT 0,
    error TEXT DEFAULT '',
    config TEXT DEFAULT '{}',
    created_at REAL NOT NULL,
    started_at REAL DEFAULT 0,
    finished_at REAL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_sa_status ON subagents(status);
CREATE INDEX IF NOT EXISTS idx_sa_name ON subagents(name);

CREATE TABLE IF NOT EXISTS subagent_transcripts (
    id TEXT PRIMARY KEY,
    agent_id TEXT NOT NULL,
    timestamp REAL NOT NULL,
    event TEXT DEFAULT '',
    data TEXT DEFAULT '{}'
);

CREATE INDEX IF NOT EXISTS idx_st_agent ON subagent_transcripts(agent_id);
"""


class SubAgentManager:
    """Manage sub-agent lifecycle: spawn, wait, cancel, transcript.

    Parameters
    ----------
    root_dir : str | Path
        MAOP project root directory.
    """

    def __init__(self, root_dir: str | Path) -> None:
        self._root = Path(root_dir)
        self._data_dir = self._root / "data"
        self._data_dir.mkdir(parents=True, exist_ok=True)
        self._db_path = get_db_path("subagent_manager")
        self._init_db()
        self._running: dict[str, asyncio.Task[Any]] = {}

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.executescript(_SUBAGENT_DDL)

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
                 json.dumps(config.model_dump()), now, now),
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
            from maop.core.llm_provider import LLMProviderFactory
            factory = LLMProviderFactory(root_dir=str(self._root))
            provider = factory.get_provider(config.model)
            if provider is None:
                return f"[No provider for model '{config.model}']"

            model_cfg = factory.get_model_config(config.model)
            model_id = model_cfg.model_id if model_cfg else config.model

            messages = []
            if config.system_prompt:
                messages.append({"role": "system", "content": config.system_prompt})
            ctx_str = json.dumps(context, default=str)[:2000] if context else ""
            user_msg = f"Task: {task}"
            if ctx_str:
                user_msg += f"\n\nContext: {ctx_str}"
            messages.append({"role": "user", "content": user_msg})

            resp = await provider.chat(
                messages, model=model_id,
                temperature=config.temperature,
                max_tokens=min(config.context_window // 4, 4096),
            )
            return resp.content
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