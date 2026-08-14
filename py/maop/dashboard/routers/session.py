"""MAOP Dashboard — Session & Conversation API endpoints."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import APIRouter, Query, Request

from maop.core.security.middleware import require_admin
from maop.dashboard.error_handler import handle_api_errors

router = APIRouter(prefix="/api/session", tags=["session"])

# P1-3: 任务历史页专用 router (复数 /api/sessions), 与现有单数 router 共存。
# 现有 /api/session/* 端点保持不变, 新增 /api/sessions (列表+分页) 与
# /api/sessions/{id}/rerun (重跑) 走此 router。
tasks_router = APIRouter(prefix="/api/sessions", tags=["sessions"])


def _get_session_mgr():
    from maop.core.security.session import SessionManager
    root = Path(__file__).resolve().parent.parent.parent.parent
    return SessionManager(root_dir=str(root))


def _get_conversation_mgr():
    from maop.core.agent.llm_chat.conversation import ConversationManager
    root = Path(__file__).resolve().parent.parent.parent.parent
    return ConversationManager(root_dir=str(root))


@router.get("/")
@handle_api_errors
async def list_sessions(
    status: str = Query("", description="Filter by status"),
    agent: str = Query("", description="Filter by agent"),
    limit: int = Query(50, ge=1, le=200),
) -> dict[str, Any]:
    mgr = _get_session_mgr()
    sessions = mgr.list(status=status, agent=agent, limit=limit)
    return {"sessions": [s.model_dump() for s in sessions]}


@router.post("/")
@handle_api_errors
async def create_session(body: dict, request: Request) -> dict[str, Any]:
    require_admin(request)
    mgr = _get_session_mgr()
    sid = mgr.create(
        agent=body.get("agent", ""),
        workdir=body.get("workdir", ""),
        tags=body.get("tags"),
        metadata=body.get("metadata"),
        token_budget=body.get("token_budget", 0),
    )
    session = mgr.get(sid)
    return {"session": session.model_dump() if session else None}


@router.get("/stats")
@handle_api_errors
async def session_stats() -> dict[str, Any]:
    mgr = _get_session_mgr()
    return mgr.stats()

@router.get("/{session_id}")
@handle_api_errors
async def get_session(session_id: str) -> dict[str, Any]:
    mgr = _get_session_mgr()
    session = mgr.get(session_id)
    if session is None:
        return {"error": "Session not found"}
    return {"session": session.model_dump()}


@router.patch("/{session_id}")
@handle_api_errors
async def update_session(session_id: str, body: dict, request: Request) -> dict[str, Any]:
    require_admin(request)
    mgr = _get_session_mgr()
    ok = mgr.update(
        session_id,
        status=body.get("status"),
        agent=body.get("agent"),
        workdir=body.get("workdir"),
        tags=body.get("tags"),
        metadata=body.get("metadata"),
        token_count=body.get("token_count"),
        token_budget=body.get("token_budget"),
        message_count=body.get("message_count"),
    )
    return {"updated": ok}


@router.delete("/{session_id}")
@handle_api_errors
async def delete_session(session_id: str, request: Request) -> dict[str, Any]:
    require_admin(request)
    mgr = _get_session_mgr()
    ok = mgr.delete(session_id)
    return {"deleted": ok}



@router.get("/{session_id}/messages")
@handle_api_errors
async def get_messages(
    session_id: str,
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
) -> dict[str, Any]:
    cmgr = _get_conversation_mgr()
    messages = cmgr.get_history(session_id, limit=limit, offset=offset)
    return {"messages": [m.model_dump() for m in messages]}


@router.post("/{session_id}/messages")
@handle_api_errors
async def add_message(session_id: str, body: dict, request: Request) -> dict[str, Any]:
    require_admin(request)
    cmgr = _get_conversation_mgr()
    msg_id = cmgr.add_message(
        session_id=session_id,
        role=body.get("role", "user"),
        content=body.get("content", ""),
        metadata=body.get("metadata"),
        token_count=body.get("token_count", 0),
    )
    smgr = _get_session_mgr()
    smgr.touch(session_id)
    return {"message_id": msg_id}


@router.get("/{session_id}/context")
@handle_api_errors
async def get_context_window(
    session_id: str,
    max_tokens: int = Query(4000, ge=100, le=128000),
) -> dict[str, Any]:
    cmgr = _get_conversation_mgr()
    window = cmgr.get_context_window(session_id, max_tokens=max_tokens)
    return {"context": window.model_dump()}


@router.get("/{session_id}/context/compressed")
@handle_api_errors
async def get_compressed_context(
    session_id: str,
    max_tokens: int = Query(4000, ge=100, le=128000),
) -> dict[str, Any]:
    cmgr = _get_conversation_mgr()
    window = cmgr.get_compressed_context(session_id, max_tokens=max_tokens)
    return {"context": window.model_dump()}


@router.delete("/{session_id}/messages")
@handle_api_errors
async def clear_messages(session_id: str, request: Request) -> dict[str, Any]:
    require_admin(request)
    cmgr = _get_conversation_mgr()
    count = cmgr.clear_session(session_id)
    return {"cleared": count}


# ════════════════════════════════════════════════════════════════════════════
# P1-3: 任务历史页 API — /api/sessions (列表+搜索+过滤+分页+排序)
#                            /api/sessions/{id}/rerun (重跑)
# ════════════════════════════════════════════════════════════════════════════
@tasks_router.get("")
@handle_api_errors
async def list_sessions_paginated(
    status: str = Query("all", description="Filter by status: running/completed/failed/all"),
    search: str = Query("", description="Search keyword (matches agent/workdir/metadata)"),
    page: int = Query(1, ge=1, description="Page number (1-indexed)"),
    limit: int = Query(20, ge=1, le=100, description="Items per page (max 100)"),
    sort: str = Query("created_at", description="Sort field: created_at/updated_at/name/status"),
    order: str = Query("desc", description="Sort order: asc/desc"),
) -> dict[str, Any]:
    """列出所有任务/会话, 支持搜索、状态过滤、分页与排序。

    返回::

        {
          "items": [...],
          "total": 100,
          "page": 1,
          "limit": 20,
          "total_pages": 5
        }
    """
    mgr = _get_session_mgr()
    return mgr.list_paginated(
        status=status,
        search=search,
        page=page,
        limit=limit,
        sort=sort,
        order=order,
    )


@tasks_router.post("/{session_id}/rerun")
@handle_api_errors
async def rerun_session(session_id: str, request: Request) -> dict[str, Any]:
    """重跑指定任务/会话。

    复制原会话的 agent / workdir / tags / metadata / token_budget 创建新会话,
    新会话以 ``active`` 状态启动, metadata 中标记 ``rerun_from`` 来源。
    返回新会话对象; 前端可据此跳转到 Run 页面继续执行。
    """
    require_admin(request)
    mgr = _get_session_mgr()
    new_session = mgr.rerun(session_id)
    if new_session is None:
        return {"error": "Session not found", "session": None}
    return {"session": new_session.model_dump(), "rerun_from": session_id}
