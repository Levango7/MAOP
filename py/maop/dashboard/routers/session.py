"""MAOP Dashboard — Session & Conversation API endpoints.

Business logic (session/conversation CRUD, pagination, rerun) lives in
:mod:`maop.dashboard.services.session_service`; this router only does
request parsing, auth, service dispatch, and response formatting.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field

from maop.core.security.middleware import require_admin
from maop.dashboard.error_handler import handle_api_errors
from maop.dashboard.services import session_service

router = APIRouter(prefix="/api/session", tags=["session"])

# P1-3: 任务历史页专用 router (复数 /api/sessions), 与现有单数 router 共存。
# 现有 /api/session/* 端点保持不变, 新增 /api/sessions (列表+分页) 与
# /api/sessions/{id}/rerun (重跑) 走此 router。
tasks_router = APIRouter(prefix="/api/sessions", tags=["sessions"])


# ── Pydantic 请求模型 (P2-#4 fix: 输入校验) ────────────────────────

class CreateSessionRequest(BaseModel):
    agent: str = ""
    workdir: str = ""
    tags: list[str] | None = None
    metadata: dict[str, Any] | None = None
    token_budget: int = 0


class UpdateSessionRequest(BaseModel):
    status: str | None = None
    agent: str | None = None
    workdir: str | None = None
    tags: list[str] | None = None
    metadata: dict[str, Any] | None = None
    token_count: int | None = None
    token_budget: int | None = None
    message_count: int | None = None


class AddMessageRequest(BaseModel):
    role: str = "user"
    content: str = ""
    metadata: dict[str, Any] | None = None
    token_count: int = 0


@router.get("/")
@handle_api_errors
async def list_sessions(
    request: Request,
    status: str = Query("", description="Filter by status"),
    agent: str = Query("", description="Filter by agent"),
    limit: int = Query(50, ge=1, le=200),
) -> dict[str, Any]:
    # P1 fix: 读端点也需要 admin 鉴权，防止会话信息泄露。
    require_admin(request)
    sessions = session_service.list_sessions(status=status, agent=agent, limit=limit)
    return {"status": "ok", "sessions": sessions}


@router.post("/")
@handle_api_errors
async def create_session(body: CreateSessionRequest, request: Request) -> dict[str, Any]:
    require_admin(request)
    session = session_service.create_session(
        agent=body.agent,
        workdir=body.workdir,
        tags=body.tags,
        metadata=body.metadata,
        token_budget=body.token_budget,
    )
    return {"status": "ok", "session": session}


@router.get("/stats")
@handle_api_errors
async def session_stats(request: Request) -> dict[str, Any]:
    # P1 fix: 读端点也需要 admin 鉴权。
    require_admin(request)
    # M-1 fix: 包裹为 {status, data} 统一响应格式。
    return {"status": "ok", "data": session_service.session_stats()}


@router.get("/{session_id}")
@handle_api_errors
async def get_session(session_id: str, request: Request) -> dict[str, Any]:
    # P1 fix: 读端点也需要 admin 鉴权。
    require_admin(request)
    session = session_service.get_session(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found")
    return {"status": "ok", "session": session}


@router.patch("/{session_id}")
@handle_api_errors
async def update_session(session_id: str, body: UpdateSessionRequest, request: Request) -> dict[str, Any]:
    require_admin(request)
    ok = session_service.update_session(
        session_id,
        status=body.status,
        agent=body.agent,
        workdir=body.workdir,
        tags=body.tags,
        metadata=body.metadata,
        token_count=body.token_count,
        token_budget=body.token_budget,
        message_count=body.message_count,
    )
    return {"status": "ok", "updated": ok}


@router.delete("/{session_id}")
@handle_api_errors
async def delete_session(session_id: str, request: Request) -> dict[str, Any]:
    require_admin(request)
    ok = session_service.delete_session(session_id)
    return {"status": "ok", "deleted": ok}



@router.get("/{session_id}/messages")
@handle_api_errors
async def get_messages(
    session_id: str,
    request: Request,
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
) -> dict[str, Any]:
    # P1 fix: 读端点也需要 admin 鉴权。
    require_admin(request)
    messages = session_service.get_messages(session_id, limit=limit, offset=offset)
    return {"status": "ok", "messages": messages}


@router.post("/{session_id}/messages")
@handle_api_errors
async def add_message(session_id: str, body: AddMessageRequest, request: Request) -> dict[str, Any]:
    require_admin(request)
    msg_id = session_service.add_message(
        session_id=session_id,
        role=body.role,
        content=body.content,
        metadata=body.metadata,
        token_count=body.token_count,
    )
    return {"status": "ok", "message_id": msg_id}


@router.get("/{session_id}/context")
@handle_api_errors
async def get_context_window(
    session_id: str,
    request: Request,
    max_tokens: int = Query(4000, ge=100, le=128000),
) -> dict[str, Any]:
    # P1 fix: 读端点也需要 admin 鉴权。
    require_admin(request)
    window = session_service.get_context_window(session_id, max_tokens=max_tokens)
    return {"status": "ok", "context": window}


@router.get("/{session_id}/context/compressed")
@handle_api_errors
async def get_compressed_context(
    session_id: str,
    request: Request,
    max_tokens: int = Query(4000, ge=100, le=128000),
) -> dict[str, Any]:
    # P1 fix: 读端点也需要 admin 鉴权。
    require_admin(request)
    window = session_service.get_compressed_context(session_id, max_tokens=max_tokens)
    return {"status": "ok", "context": window}


@router.delete("/{session_id}/messages")
@handle_api_errors
async def clear_messages(session_id: str, request: Request) -> dict[str, Any]:
    require_admin(request)
    count = session_service.clear_messages(session_id)
    return {"status": "ok", "cleared": count}


# ════════════════════════════════════════════════════════════════════════════
# P1-3: 任务历史页 API — /api/sessions (列表+搜索+过滤+分页+排序)
#                            /api/sessions/{id}/rerun (重跑)
# ════════════════════════════════════════════════════════════════════════════
@tasks_router.get("")
@handle_api_errors
async def list_sessions_paginated(
    request: Request,
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
    # P1 fix: 读端点也需要 admin 鉴权。
    require_admin(request)
    # M-1 fix: 包裹为 {status, data} 统一响应格式。
    return {"status": "ok", "data": session_service.list_sessions_paginated(
        status=status,
        search=search,
        page=page,
        limit=limit,
        sort=sort,
        order=order,
    )}


@tasks_router.post("/{session_id}/rerun")
@handle_api_errors
async def rerun_session(session_id: str, request: Request) -> dict[str, Any]:
    """重跑指定任务/会话。

    复制原会话的 agent / workdir / tags / metadata / token_budget 创建新会话,
    新会话以 ``active`` 状态启动, metadata 中标记 ``rerun_from`` 来源。
    返回新会话对象; 前端可据此跳转到 Run 页面继续执行。
    """
    require_admin(request)
    new_session = session_service.rerun_session(session_id)
    if new_session is None:
        raise HTTPException(status_code=404, detail="Session not found")
    return {"status": "ok", "session": new_session, "rerun_from": session_id}
