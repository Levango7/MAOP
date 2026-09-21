"""Session & long-running-task service layer.

Encapsulates business logic for session/conversation management
(session.py) and long-running task management (long_running_task.py).
Extracted from the router layer so routers only do parameter parsing +
auth + service call + response formatting.

The service is intentionally framework-agnostic: it does not import
FastAPI (except for ``StreamingResponse`` / ``HTTPException`` in the
SSE helper where an HTTP response is intrinsic to the API contract) and
can be unit-tested or invoked without an HTTP context.
"""

from __future__ import annotations

import asyncio
import json
import logging
import threading
from typing import Any

from maop.core.agent.router.long_running_task import (
    LongRunningTask,
    LongRunningTaskManager,
)
from maop.core.backends.db_utils import get_db_path

logger = logging.getLogger(__name__)


# ════════════════════════════════════════════════════════════════════════════
# Session & conversation managers
# ════════════════════════════════════════════════════════════════════════════

def get_session_mgr() -> Any:
    """Create a :class:`SessionManager` bound to ``MAOP_ROOT``."""
    from maop.core.security.session import SessionManager

    # P2-24: 统一使用 state.MAOP_ROOT
    from maop.dashboard.routers.state import MAOP_ROOT
    return SessionManager(root_dir=str(MAOP_ROOT))


def get_conversation_mgr() -> Any:
    """Create a :class:`ConversationManager` bound to ``MAOP_ROOT``."""
    from maop.core.agent.llm_chat.conversation import ConversationManager

    # P2-24: 统一使用 state.MAOP_ROOT
    from maop.dashboard.routers.state import MAOP_ROOT
    return ConversationManager(root_dir=str(MAOP_ROOT))


# ── Session CRUD ──────────────────────────────────────────────────

def list_sessions(status: str = "", agent: str = "", limit: int = 50) -> list[dict[str, Any]]:
    """List sessions filtered by status/agent. Returns model_dump list."""
    mgr = get_session_mgr()
    sessions = mgr.list(status=status, agent=agent, limit=limit)
    return [s.model_dump() for s in sessions]


def create_session(
    agent: str = "",
    workdir: str = "",
    tags: list[str] | None = None,
    metadata: dict[str, Any] | None = None,
    token_budget: int = 0,
) -> dict[str, Any] | None:
    """Create a new session. Returns the session model_dump (or None)."""
    mgr = get_session_mgr()
    sid = mgr.create(
        agent=agent, workdir=workdir, tags=tags,
        metadata=metadata, token_budget=token_budget,
    )
    session = mgr.get(sid)
    return session.model_dump() if session else None


def session_stats() -> Any:
    """Return session aggregate stats."""
    mgr = get_session_mgr()
    return mgr.stats()


def get_session(session_id: str) -> dict[str, Any] | None:
    """Get a single session by ID. Returns model_dump or None."""
    mgr = get_session_mgr()
    session = mgr.get(session_id)
    return session.model_dump() if session else None


def update_session(
    session_id: str,
    status: str | None = None,
    agent: str | None = None,
    workdir: str | None = None,
    tags: list[str] | None = None,
    metadata: dict[str, Any] | None = None,
    token_count: int | None = None,
    token_budget: int | None = None,
    message_count: int | None = None,
) -> bool:
    """Update session fields. Returns ``ok`` flag from manager."""
    mgr = get_session_mgr()
    return mgr.update(
        session_id, status=status, agent=agent, workdir=workdir, tags=tags,
        metadata=metadata, token_count=token_count,
        token_budget=token_budget, message_count=message_count,
    )


def delete_session(session_id: str) -> bool:
    """Delete a session. Returns ``ok`` flag from manager."""
    mgr = get_session_mgr()
    return mgr.delete(session_id)


# ── Messages ──────────────────────────────────────────────────────

def get_messages(session_id: str, limit: int = 100, offset: int = 0) -> list[dict[str, Any]]:
    """List messages in a session. Returns model_dump list."""
    cmgr = get_conversation_mgr()
    messages = cmgr.get_history(session_id, limit=limit, offset=offset)
    return [m.model_dump() for m in messages]


def add_message(
    session_id: str,
    role: str = "user",
    content: str = "",
    metadata: dict[str, Any] | None = None,
    token_count: int = 0,
) -> str:
    """Add a message to a session and touch the session. Returns message_id."""
    cmgr = get_conversation_mgr()
    msg_id = cmgr.add_message(
        session_id=session_id, role=role, content=content,
        metadata=metadata, token_count=token_count,
    )
    smgr = get_session_mgr()
    smgr.touch(session_id)
    return msg_id


def get_context_window(session_id: str, max_tokens: int = 4000) -> dict[str, Any]:
    """Get the context window for a session. Returns model_dump."""
    cmgr = get_conversation_mgr()
    window = cmgr.get_context_window(session_id, max_tokens=max_tokens)
    return window.model_dump()


def get_compressed_context(session_id: str, max_tokens: int = 4000) -> dict[str, Any]:
    """Get the compressed context window. Returns model_dump."""
    cmgr = get_conversation_mgr()
    window = cmgr.get_compressed_context(session_id, max_tokens=max_tokens)
    return window.model_dump()


def clear_messages(session_id: str) -> int:
    """Clear all messages in a session. Returns cleared count."""
    cmgr = get_conversation_mgr()
    return cmgr.clear_session(session_id)


# ── Task history (paginated list + rerun) ─────────────────────────

def list_sessions_paginated(
    status: str = "all",
    search: str = "",
    page: int = 1,
    limit: int = 20,
    sort: str = "created_at",
    order: str = "desc",
) -> Any:
    """Paginated list of sessions/tasks with search/filter/sort."""
    mgr = get_session_mgr()
    return mgr.list_paginated(
        status=status, search=search, page=page,
        limit=limit, sort=sort, order=order,
    )


def rerun_session(session_id: str) -> dict[str, Any] | None:
    """Rerun a session: clone config into a new ``active`` session.

    Returns the new session model_dump, or None if the original does
    not exist.
    """
    mgr = get_session_mgr()
    new_session = mgr.rerun(session_id)
    return new_session.model_dump() if new_session else None


# ════════════════════════════════════════════════════════════════════════════
# Long-running task manager (singleton)
# ════════════════════════════════════════════════════════════════════════════

_manager_lock = threading.Lock()
_manager: LongRunningTaskManager | None = None


def get_long_running_task_manager() -> LongRunningTaskManager:
    """获取全局长任务管理器单例（线程安全懒初始化）."""
    global _manager
    if _manager is not None:
        return _manager
    with _manager_lock:
        if _manager is None:
            _manager = LongRunningTaskManager(db_path=get_db_path("long_running_task"))
    return _manager


def set_long_running_task_manager_for_tests(
    manager: LongRunningTaskManager | None,
) -> None:
    """注入/重置管理器实例 — 仅供单元测试隔离使用."""
    global _manager
    with _manager_lock:
        _manager = manager


# ── Long-running task operations ──────────────────────────────────

def submit_long_running_task(
    agent_name: str,
    task: str,
    timeout_s: float = 3600.0,
    metadata: dict[str, Any] | None = None,
) -> str:
    """Submit a long-running task. Returns ``task_id``."""
    mgr = get_long_running_task_manager()
    return mgr.submit(
        agent_name=agent_name, task=task,
        timeout_s=timeout_s, metadata=metadata or {},
    )


def list_active_long_running_tasks() -> list[dict[str, Any]]:
    """List all active (pending/running) long-running tasks."""
    mgr = get_long_running_task_manager()
    tasks = mgr.list_active()
    return [t.model_dump() for t in tasks]


def get_long_running_task_status(task_id: str) -> dict[str, Any]:
    """Get a single task's status. Raises ``KeyError`` if not found."""
    mgr = get_long_running_task_manager()
    task = mgr.get_status(task_id)
    return task.model_dump()


def cancel_long_running_task(task_id: str) -> bool:
    """Cancel a long-running task. Returns ``True`` if cancelled."""
    mgr = get_long_running_task_manager()
    return mgr.cancel(task_id)


def get_long_running_task_raw(task_id: str) -> LongRunningTask:
    """Get the raw task object (for SSE streaming). Raises ``KeyError``."""
    mgr = get_long_running_task_manager()
    return mgr.get_status(task_id)


# ── SSE progress stream ───────────────────────────────────────────

# Terminal statuses that close the SSE stream.
TERMINAL_STATUSES = {"completed", "failed", "cancelled", "timeout"}


async def long_running_task_progress_generator(task_id: str) -> Any:
    """Async generator yielding SSE-formatted progress events.

    Emits ``progress`` events on change and a final ``done`` (or
    ``error``) event before closing. Polls the manager every 1 second.
    """
    mgr = get_long_running_task_manager()
    initial = mgr.get_status(task_id)  # raises KeyError if absent

    last_progress: float = -1.0
    last_message: str = ""

    # 若初始已是终态，直接推送最终事件
    if initial.status in TERMINAL_STATUSES:
        event_data = {
            "task_id": task_id,
            "status": initial.status,
            "progress": initial.progress,
            "result": initial.result,
            "error": initial.error,
        }
        yield f"event: done\ndata: {json.dumps(event_data)}\n\n"
        return

    while True:
        try:
            task = mgr.get_status(task_id)
        except KeyError:
            # 任务被删除（理论上不会发生，task_id 是 PK）
            err_data = {"task_id": task_id, "error": "任务不存在"}
            yield f"event: error\ndata: {json.dumps(err_data)}\n\n"
            return

        # 仅在进度或消息变化时推送，减少无效事件
        if task.progress != last_progress or task.progress_message != last_message:
            progress_data = {
                "task_id": task_id,
                "progress": task.progress,
                "message": task.progress_message,
                "status": task.status,
            }
            yield f"event: progress\ndata: {json.dumps(progress_data)}\n\n"
            last_progress = task.progress
            last_message = task.progress_message

        # 终态：推送 done 事件并关闭流
        if task.status in TERMINAL_STATUSES:
            done_data = {
                "task_id": task_id,
                "status": task.status,
                "progress": task.progress,
                "result": task.result,
                "error": task.error,
            }
            yield f"event: done\ndata: {json.dumps(done_data)}\n\n"
            return

        # 轮询间隔：1 秒
        await asyncio.sleep(1.0)