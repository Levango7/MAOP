"""MAOP Dashboard — Session & Conversation API endpoints."""

from __future__ import annotations
from typing import Any

from fastapi import APIRouter, Query, Request

from maop.dashboard.error_handler import handle_api_errors
from maop.core.middleware import require_admin

router = APIRouter(prefix="/api/session", tags=["session"])


def _get_session_mgr():
    from maop.core.session import SessionManager
    from pathlib import Path
    root = Path(__file__).resolve().parent.parent.parent.parent
    return SessionManager(root_dir=str(root))


def _get_conversation_mgr():
    from maop.core.conversation import ConversationManager
    from pathlib import Path
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
