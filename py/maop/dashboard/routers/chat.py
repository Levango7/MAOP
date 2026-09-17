"""MAOP Dashboard — Chat API with SSE streaming and three-layer memory.

Endpoints:
  POST /api/chat          — Send a message, get full response
  POST /api/chat/stream   — Send a message, get SSE streaming response
  GET  /api/chat/sessions — List chat sessions
  GET  /api/chat/{id}     — Get chat session messages
  DELETE /api/chat/{id}   — Clear chat session
  POST /api/chat/memory/search — Search across memory layers
  POST /api/chat/consolidate   — Trigger memory consolidation
  GET  /api/chat/memory/stats  — Get memory statistics

Business logic lives in :mod:`maop.dashboard.services.chat_service`; this
module only does request parsing, auth, service dispatch, and response
formatting.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Request, UploadFile
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field

from maop.core.security.middleware import require_admin
from maop.dashboard.error_handler import handle_api_errors
from maop.dashboard.services import chat_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/chat", tags=["chat"])

# P2-24: 统一使用 state.MAOP_ROOT，避免路径计算层数不一致
from .state import MAOP_ROOT  # noqa: E402


def _get_engine():
    """Return a ChatEngine bound to MAOP_ROOT.

    Thin wrapper over :func:`chat_service.make_chat_engine` kept on the
    router module so stability tests can monkeypatch the engine factory
    (see ``tests/stability/test_boundary_inputs.py``).
    """
    return chat_service.make_chat_engine(MAOP_ROOT)


# ── Request/Response Models ──────────────────────────────────────

class ChatRequestBody(BaseModel):
    session_id: str = ""
    message: str
    images: list[str] = []
    agent: str = ""
    model: str = ""
    system_prompt: str = ""
    stream: bool = False
    # M-3 fix: 添加值域约束。
    max_tokens: int = Field(default=4096, ge=1, le=128000)
    temperature: float = Field(default=0.7, ge=0.0, le=2.0)


class MemorySearchRequest(BaseModel):
    query: str
    # M-3 fix: 添加值域约束。
    top: int = Field(default=10, ge=1, le=100)


# ── Chat Endpoints ───────────────────────────────────────────────

@router.post("")
@handle_api_errors("chat")
async def chat(request_body: ChatRequestBody, request: Request) -> dict[str, Any]:
    """Send a chat message and get a full response."""
    require_admin(request)
    engine = _get_engine()
    chat_req = chat_service.build_chat_request(
        session_id=request_body.session_id,
        message=request_body.message,
        images=request_body.images,
        agent=request_body.agent,
        model=request_body.model,
        system_prompt=request_body.system_prompt,
        stream=False,
        max_tokens=request_body.max_tokens,
        temperature=request_body.temperature,
    )
    return await chat_service.send_chat(engine, chat_req)


@router.post("/stream")
@handle_api_errors("chat stream")
async def chat_stream(request_body: ChatRequestBody, request: Request) -> Any:
    """Send a chat message and get an SSE streaming response."""
    require_admin(request)
    engine = _get_engine()
    chat_req = chat_service.build_chat_request(
        session_id=request_body.session_id,
        message=request_body.message,
        images=request_body.images,
        agent=request_body.agent,
        model=request_body.model,
        system_prompt=request_body.system_prompt,
        stream=True,
        max_tokens=request_body.max_tokens,
        temperature=request_body.temperature,
    )

    # StreamingResponse construction stays in the router; the async
    # iterable (generator) is owned by the service.
    return StreamingResponse(
        chat_service.stream_chat_chunks(engine, chat_req),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/models")
@handle_api_errors("list models")
async def list_models(request: Request) -> dict[str, Any]:
    """List available LLM models from models.yaml."""
    require_admin(request)
    data = chat_service.list_llm_models(MAOP_ROOT)
    return {"status": "ok", "data": data}


# ── Session Management ───────────────────────────────────────────

@router.get("/sessions")
@handle_api_errors("list chat sessions")
async def list_sessions(request: Request) -> dict[str, Any]:
    """List all chat sessions."""
    require_admin(request)
    data = chat_service.list_chat_sessions(MAOP_ROOT)
    return {"status": "ok", "data": data["sessions"]}


@router.get("/{session_id}")
@handle_api_errors("get chat session")
async def get_session(request: Request, session_id: str) -> dict[str, Any]:
    """Get messages for a chat session."""
    require_admin(request)
    engine = _get_engine()
    data = chat_service.get_session_messages(engine, session_id)
    return {"status": "ok", "data": data}


@router.delete("/{session_id}")
@handle_api_errors("clear chat session")
async def clear_session(session_id: str, request: Request) -> dict[str, Any]:
    """Clear all messages in a chat session."""
    require_admin(request)
    engine = _get_engine()
    count = chat_service.clear_session(engine, session_id)
    return {"status": "ok", "cleared": count}


# ── Memory Endpoints ─────────────────────────────────────────────

@router.post("/memory/search")
@handle_api_errors("memory search")
async def memory_search(request_body: MemorySearchRequest, request: Request) -> dict[str, Any]:
    """Search across all memory layers."""
    require_admin(request)
    engine = _get_engine()
    results = chat_service.search_memory(engine, request_body.query, request_body.top)
    return {"status": "ok", "data": results}


@router.post("/memory/consolidate")
@handle_api_errors("memory consolidate")
async def memory_consolidate(request: Request) -> dict[str, Any]:
    """Trigger L2 → L3 memory consolidation."""
    require_admin(request)
    engine = _get_engine()
    report = chat_service.consolidate_memory(engine)
    return {"status": "ok", "data": report}


@router.get("/memory/stats")
@handle_api_errors("memory stats")
async def memory_stats(request: Request) -> dict[str, Any]:
    """Get memory statistics."""
    require_admin(request)
    engine = _get_engine()
    stats = chat_service.get_memory_stats(engine)
    return {"status": "ok", "data": stats}


# ── Image Upload Endpoints ───────────────────────────────────────

@router.post("/upload")
@handle_api_errors("image upload")
async def upload_image(
    request: Request,
    session_id: str = "",
    file: UploadFile | None = None,
) -> dict[str, Any]:
    """Upload an image for multimodal chat."""
    require_admin(request)

    if file is None:
        return JSONResponse(
            status_code=400,
            content={"status": "error", "error": "No file provided"},
        )

    content = await file.read()
    img_id = chat_service.save_image(
        MAOP_ROOT,
        session_id=session_id,
        filename=file.filename or "upload.png",
        data=content,
        content_type=file.content_type or "",
    )
    return {"status": "ok", "data": {"image_id": img_id, "filename": file.filename}}


@router.get("/images/{session_id}")
@handle_api_errors("list session images")
async def list_session_images(request: Request, session_id: str) -> dict[str, Any]:
    """List all images for a chat session."""
    require_admin(request)
    images = chat_service.list_session_images(MAOP_ROOT, session_id)
    return {"status": "ok", "data": images}


@router.delete("/images/{image_id}")
@handle_api_errors("delete image")
async def delete_image(image_id: str, request: Request) -> dict[str, Any]:
    """Delete an uploaded image."""
    require_admin(request)
    deleted = chat_service.delete_image(MAOP_ROOT, image_id)
    if not deleted:
        return JSONResponse(
            status_code=404,
            content={"status": "error", "error": "Image not found"},
        )
    return {"status": "ok", "data": {"deleted": True}}
