"""MAOP A2A Protocol — Google Agent-to-Agent standard protocol adapter.

Implements the A2A specification for inter-agent communication:
  - AgentCard: Standardized agent capability advertisement
  - A2AMessage: JSON-RPC 2.0 based message format
  - A2AServer: HTTP endpoint for receiving A2A messages
  - A2AClient: Client for sending A2A messages to remote agents

Reference: https://github.com/google/A2A

The A2A protocol enables MAOP agents to communicate with any A2A-compliant
agent system (Google ADK, LangGraph, CrewAI, etc.) using a standard format.

Usage::

    from maop.core.agent.delegation.a2a import A2ACard, A2AServer, A2AClient

    # Advertise an agent
    card = A2ACard(name="code-reviewer", capabilities=["review", "suggest"])

    # Start A2A server
    server = A2AServer(root_dir="/path/to/MAOP")
    await server.start(host="0.0.0.0", port=8080)

    # Send message to remote agent
    client = A2AClient()
    result = await client.send("http://remote:8080", task="Review this code")
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

_A2A_DDL = """
CREATE TABLE IF NOT EXISTS a2a_cards (
    name TEXT PRIMARY KEY,
    card_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS a2a_tasks (
    task_id TEXT PRIMARY KEY,
    task_json TEXT NOT NULL
);
"""


class A2ACard(BaseModel):
    name: str
    description: str = ""
    version: str = "1.0.0"
    capabilities: list[str] = Field(default_factory=list)
    endpoint: str = ""
    provider: str = "maop"
    metadata: dict[str, Any] = Field(default_factory=dict)


class A2AMessage(BaseModel):
    jsonrpc: str = "2.0"
    method: str = "tasks/send"
    id: str = Field(default_factory=lambda: uuid.uuid4().hex)
    params: dict[str, Any] = Field(default_factory=dict)


class A2AResponse(BaseModel):
    jsonrpc: str = "2.0"
    id: str = ""
    result: dict[str, Any] = Field(default_factory=dict)
    error: dict[str, Any] | None = None


class A2ATaskState(BaseModel):
    task_id: str = Field(default_factory=lambda: uuid.uuid4().hex)
    status: str = "submitted"
    artifacts: list[dict[str, Any]] = Field(default_factory=list)
    history: list[dict[str, Any]] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class A2AManager:
    """Manages A2A agent cards, task states, and message routing.

    Bridges A2A protocol to MAOP's internal dispatcher and protocol registry.
    Cards and tasks are persisted to SQLite so they survive restarts.
    """

    def __init__(self, root_dir: str | None = None, worker_pool: Any = None) -> None:
        self._cards: dict[str, A2ACard] = {}
        self._tasks: dict[str, A2ATaskState] = {}
        self._root_dir = root_dir
        self._db_path: str | None = None
        # t11: optional WorkerPool for real task execution.
        # When None, tasks/send only persists the task (legacy behavior).
        self._worker_pool: Any = worker_pool
        self._init_db()

    def set_worker_pool(self, pool: Any) -> None:
        """Inject a WorkerPool (or any object exposing `async submit(task, workdir=...)`).

        Once set, every ``tasks/send`` will dispatch the message to the pool
        asynchronously after the task record is created.
        """
        self._worker_pool = pool
        logger.info("[a2a] WorkerPool injected: %s", type(pool).__name__)

    def _init_db(self) -> None:
        try:
            from maop.core.backends.db_utils import get_db_path, sqlite_connect

            self._db_path = str(get_db_path("a2a"))
            with sqlite_connect(self._db_path) as conn:
                conn.executescript(_A2A_DDL)
                conn.commit()
            self._load_from_db()
        except Exception as exc:
            logger.warning("[a2a] DB init failed, using in-memory only: %s", exc)
            self._db_path = None

    def _load_from_db(self) -> None:
        if not self._db_path:
            return
        try:
            from maop.core.backends.db_utils import sqlite_connect

            with sqlite_connect(self._db_path) as conn:
                for row in conn.execute("SELECT name, card_json FROM a2a_cards"):
                    self._cards[row[0]] = A2ACard(**json.loads(row[1]))
                for row in conn.execute("SELECT task_id, task_json FROM a2a_tasks"):
                    self._tasks[row[0]] = A2ATaskState(**json.loads(row[1]))
        except Exception as exc:
            logger.warning("[a2a] DB load failed: %s", exc)

    def _persist_card(self, card: A2ACard) -> None:
        if not self._db_path:
            return
        try:
            from maop.core.backends.db_utils import sqlite_connect

            with sqlite_connect(self._db_path) as conn:
                conn.execute(
                    "INSERT OR REPLACE INTO a2a_cards (name, card_json) VALUES (?, ?)",
                    (card.name, card.model_dump_json()),
                )
                conn.commit()
        except Exception as exc:
            logger.warning("[a2a] Card persist failed: %s", exc)

    def _persist_task(self, task: A2ATaskState) -> None:
        if not self._db_path:
            return
        try:
            from maop.core.backends.db_utils import sqlite_connect

            with sqlite_connect(self._db_path) as conn:
                conn.execute(
                    "INSERT OR REPLACE INTO a2a_tasks (task_id, task_json) VALUES (?, ?)",
                    (task.task_id, task.model_dump_json()),
                )
                conn.commit()
        except Exception as exc:
            logger.warning("[a2a] Task persist failed: %s", exc)

    def register_card(self, card: A2ACard) -> None:
        self._cards[card.name] = card
        self._persist_card(card)
        logger.info("[a2a] Registered agent card: %s (caps=%s)", card.name, card.capabilities)

    def get_card(self, name: str) -> A2ACard | None:
        return self._cards.get(name)

    def list_cards(self) -> list[A2ACard]:
        return list(self._cards.values())

    def create_task(self, agent_name: str, message: str) -> A2ATaskState:
        task = A2ATaskState(
            status="submitted",
            metadata={"agent": agent_name, "created_at": datetime.now(timezone.utc).isoformat()},
        )
        task.history.append(
            {
                "role": "user",
                "content": message,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
        )
        self._tasks[task.task_id] = task
        self._persist_task(task)
        return task

    def update_task(
        self, task_id: str, status: str, artifact: dict[str, Any] | None = None
    ) -> A2ATaskState | None:
        task = self._tasks.get(task_id)
        if not task:
            return None
        task.status = status
        if artifact:
            task.artifacts.append(artifact)
        task.history.append(
            {
                "role": "agent",
                "status": status,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
        )
        self._persist_task(task)
        return task

    def get_task(self, task_id: str) -> A2ATaskState | None:
        return self._tasks.get(task_id)

    async def dispatch_task(self, task_id: str, agent_name: str, message: str) -> None:
        """Submit a task to the configured WorkerPool and update task state.

        Lifecycle:
          submitted -> working -> completed | failed

        On success, the worker's result is appended as an artifact.
        On failure, the exception message is captured in the artifact.

        F6a (2026-07-22, Phase F): ``agent_name`` is now forwarded to
        ``WorkerPool.submit(agent_name=...)`` which propagates it to
        ``MaopLoop.run(agent=...)``. This makes the A2A-dispatched task
        actually execute with the agent that the A2A caller requested,
        rather than being silently ignored (previous behavior only
        stored ``agent_name`` as artifact metadata). When the worker pool
        is a mock (test) or doesn't accept ``agent_name``, the call
        still succeeds because the kwarg is optional with default "".
        See ADR-013.

        This method is intended to be awaited (or scheduled via
        ``_spawn_dispatch`` from sync ``handle_message``).
        """
        if self._worker_pool is None:
            logger.warning("[a2a] dispatch_task called without a worker pool")
            return
        self.update_task(task_id, "working")
        try:
            workdir = self._root_dir or ""
            result = await self._worker_pool.submit(
                message,
                workdir=workdir,
                agent_name=agent_name,
            )
            artifact = {
                "role": "agent",
                "agent": agent_name,
                "result": result.model_dump() if hasattr(result, "model_dump") else result,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
            self.update_task(task_id, "completed", artifact=artifact)
            logger.info("[a2a] task %s completed by '%s'", task_id, agent_name)
        except Exception as exc:
            artifact = {
                "role": "agent",
                "agent": agent_name,
                "error": str(exc),
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
            self.update_task(task_id, "failed", artifact=artifact)
            logger.warning("[a2a] task %s failed: %s", task_id, exc)

    def _spawn_dispatch(self, task_id: str, agent_name: str, message: str) -> None:
        """Fire-and-forget dispatch from sync ``handle_message``.

        If an asyncio event loop is running, schedule the coroutine with
        ``asyncio.ensure_future``. Otherwise fall back to a daemon thread
        that runs a fresh loop (for non-async callers like CLI tools).
        """
        try:
            import asyncio

            loop = asyncio.get_event_loop()
            if loop.is_running():
                asyncio.ensure_future(self.dispatch_task(task_id, agent_name, message))
                return
            # Loop exists but not running — run in thread.
        except RuntimeError:
            pass  # No current event loop.
        import threading

        def _bg() -> None:
            import asyncio

            asyncio.run(self.dispatch_task(task_id, agent_name, message))

        threading.Thread(target=_bg, daemon=True, name=f"a2a-dispatch-{task_id}").start()

    def handle_message(self, message: A2AMessage) -> A2AResponse:
        method = message.method
        params = message.params

        if method == "agent/card":
            name = params.get("name", "")
            card = self.get_card(name)
            if card:
                return A2AResponse(id=message.id, result=card.model_dump())
            return A2AResponse(
                id=message.id, error={"code": -32601, "message": f"Agent '{name}' not found"}
            )

        if method == "tasks/send":
            agent_name = params.get("agent", "")
            task_msg = params.get("message", "")
            if not agent_name or not task_msg:
                return A2AResponse(
                    id=message.id, error={"code": -32602, "message": "Missing 'agent' or 'message'"}
                )
            task = self.create_task(agent_name, task_msg)
            # t11: if a WorkerPool is configured, dispatch the task for real execution.
            if self._worker_pool is not None:
                self._spawn_dispatch(task.task_id, agent_name, task_msg)
            # A2A 协议语义：tasks/send 提交即返回 submitted（fire-and-forget）。
            # 不能读 task.status —— _spawn_dispatch 的 daemon 线程可能已抢先执行
            # dispatch_task 的 update_task("working")，导致响应反映执行中状态
            # （CI xdist 下偶发 AssertionError: 'working' != 'submitted'）。
            # 状态流转由客户端通过 tasks/get 查询。
            return A2AResponse(
                id=message.id,
                result={"task_id": task.task_id, "status": "submitted"},
            )

        if method == "tasks/get":
            task_id = params.get("task_id", "")
            found = self.get_task(task_id)
            if found is not None:
                return A2AResponse(id=message.id, result=found.model_dump())
            return A2AResponse(
                id=message.id, error={"code": -32601, "message": f"Task '{task_id}' not found"}
            )

        if method == "tasks/cancel":
            task_id = params.get("task_id", "")
            updated = self.update_task(task_id, "canceled")
            if updated is not None:
                return A2AResponse(
                    id=message.id, result={"task_id": updated.task_id, "status": "canceled"}
                )
            return A2AResponse(
                id=message.id, error={"code": -32601, "message": f"Task '{task_id}' not found"}
            )

        return A2AResponse(
            id=message.id, error={"code": -32601, "message": f"Method '{method}' not supported"}
        )


class A2AClient:
    """Client for sending A2A messages to remote agents via HTTP."""

    async def send(
        self, endpoint: str, agent_name: str, message: str, *, timeout: float = 30.0
    ) -> A2AResponse:
        msg = A2AMessage(method="tasks/send", params={"agent": agent_name, "message": message})
        try:
            import httpx

            async with httpx.AsyncClient(timeout=timeout) as client:
                resp = await client.post(
                    endpoint.rstrip("/") + "/a2a",
                    json=msg.model_dump(),
                    headers={"Content-Type": "application/json"},
                )
                return A2AResponse(**resp.json())
        except ImportError:
            logger.warning("[a2a] httpx not installed, cannot send A2A messages")
            return A2AResponse(error={"code": -32000, "message": "httpx not installed"})
        except Exception as exc:
            logger.error("[a2a] Failed to send message: %s", exc)
            return A2AResponse(error={"code": -32000, "message": str(exc)})

    async def get_card(
        self, endpoint: str, agent_name: str, *, timeout: float = 10.0
    ) -> A2ACard | None:
        msg = A2AMessage(method="agent/card", params={"name": agent_name})
        try:
            import httpx

            async with httpx.AsyncClient(timeout=timeout) as client:
                resp = await client.post(
                    endpoint.rstrip("/") + "/a2a",
                    json=msg.model_dump(),
                    headers={"Content-Type": "application/json"},
                )
                data = resp.json()
                if data.get("error"):
                    return None
                return A2ACard(**data.get("result", {}))
        except Exception as exc:
            logger.warning(
                "[a2a] Failed to fetch agent card for name=%s from %s, "
                "returning None (caller will skip this peer in delegation): %s",
                agent_name, endpoint, exc, exc_info=True,
            )
            return None


def create_a2a_router(manager: A2AManager) -> Any:
    """Create a FastAPI router for A2A protocol endpoint."""
    router = APIRouter(prefix="/a2a", tags=["a2a"])

    @router.post("")
    async def a2a_endpoint(request: Request) -> JSONResponse:
        body = await request.json()
        message = A2AMessage(**body)
        response = manager.handle_message(message)
        return JSONResponse(content=response.model_dump(exclude_none=True))

    @router.get("/cards")
    async def list_cards() -> list[dict[str, Any]]:
        return [c.model_dump() for c in manager.list_cards()]

    @router.get("/tasks/{task_id}")
    async def get_task(task_id: str) -> dict[str, Any]:
        task = manager.get_task(task_id)
        if task:
            return task.model_dump()  # type: ignore
        return {"error": "not found"}

    return router
