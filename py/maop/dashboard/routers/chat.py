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
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Request, UploadFile
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from maop.core.middleware import require_admin

from .error_handler import handle_api_errors

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/chat", tags=["chat"])

MAOP_ROOT = Path(__file__).resolve().parent.parent.parent.parent.parent


def _get_engine():
    from maop.core.chat_engine import ChatEngine
    return ChatEngine(root_dir=str(MAOP_ROOT))


# ── Request/Response Models ──────────────────────────────────────

class ChatRequestBody(BaseModel):
    session_id: str = ""
    message: str
    images: list[str] = []
    agent: str = ""
    model: str = ""
    system_prompt: str = ""
    stream: bool = False
    max_tokens: int = 4096
    temperature: float = 0.7


class MemorySearchRequest(BaseModel):
    query: str
    top: int = 10


# ── Chat Endpoints ───────────────────────────────────────────────

@router.post("")
@handle_api_errors("chat")
async def chat(request_body: ChatRequestBody, request: Request) -> dict[str, Any]:
    """Send a chat message and get a full response."""
    require_admin(request)
    from maop.core.chat_engine import ChatRequest
    engine = _get_engine()
    chat_req = ChatRequest(
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
    response = await engine.chat(chat_req)
    return {"status": "ok", "data": response.model_dump()}


@router.post("/stream")
@handle_api_errors("chat stream")
async def chat_stream(request_body: ChatRequestBody, request: Request) -> Any:
    """Send a chat message and get an SSE streaming response."""
    require_admin(request)
    from maop.core.chat_engine import ChatRequest
    engine = _get_engine()
    chat_req = ChatRequest(
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

    async def event_generator():
        async for chunk in engine.chat_stream(chat_req):
            yield chunk

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/models")
@handle_api_errors("list models")
async def list_models() -> dict[str, Any]:
    """List available LLM models from models.yaml."""
    from maop.core.llm_provider import LLMProviderFactory
    factory = LLMProviderFactory(root_dir=str(MAOP_ROOT))
    models = factory.list_models(enabled_only=True)
    providers = factory.list_providers(enabled_only=True)
    return {
        "status": "ok",
        "data": {
            "models": [m.model_dump() for m in models],
            "providers": [p.model_dump() for p in providers],
        },
    }


# ── Session Management ───────────────────────────────────────────

@router.get("/sessions")
@handle_api_errors("list chat sessions")
async def list_sessions() -> dict[str, Any]:
    """List all chat sessions."""
    from maop.core.session import SessionManager
    mgr = SessionManager(root_dir=str(MAOP_ROOT))
    sessions = mgr.list()
    return {"status": "ok", "data": [s.model_dump() for s in sessions]}


@router.get("/{session_id}")
@handle_api_errors("get chat session")
async def get_session(session_id: str) -> dict[str, Any]:
    """Get messages for a chat session."""
    engine = _get_engine()
    history = engine.memory.conversation.get_history(session_id)
    return {
        "status": "ok",
        "data": {
            "session_id": session_id,
            "messages": [m.model_dump() for m in history],
            "message_count": len(history),
        },
    }


@router.delete("/{session_id}")
@handle_api_errors("clear chat session")
async def clear_session(session_id: str, request: Request) -> dict[str, Any]:
    """Clear all messages in a chat session."""
    require_admin(request)
    engine = _get_engine()
    count = engine.memory.conversation.clear_session(session_id)
    return {"status": "ok", "cleared": count}


# ── Memory Endpoints ─────────────────────────────────────────────

@router.post("/memory/search")
@handle_api_errors("memory search")
async def memory_search(request_body: MemorySearchRequest, request: Request) -> dict[str, Any]:
    """Search across all memory layers."""
    require_admin(request)
    engine = _get_engine()
    results = engine.memory.search_all_layers(query=request_body.query, top=request_body.top)
    return {"status": "ok", "data": results}


@router.post("/memory/consolidate")
@handle_api_errors("memory consolidate")
async def memory_consolidate(request: Request) -> dict[str, Any]:
    """Trigger L2 → L3 memory consolidation."""
    require_admin(request)
    engine = _get_engine()
    report = engine.memory.consolidate()
    return {"status": "ok", "data": report}


@router.get("/memory/stats")
@handle_api_errors("memory stats")
async def memory_stats() -> dict[str, Any]:
    """Get memory statistics."""
    engine = _get_engine()
    stats = engine.memory.stats()
    return {"status": "ok", "data": stats}


# ── Image Upload Endpoints ───────────────────────────────────────

@router.post("/upload")
@handle_api_errors("image upload")
async def upload_image(
    session_id: str = "",
    file: UploadFile | None = None,
    request: Request = None,  # type: ignore[assignment]
) -> dict[str, Any]:
    """Upload an image for multimodal chat."""
    require_admin(request)
    from maop.core.image_store import ImageStore

    if file is None:
        return {"status": "error", "error": "No file provided"}

    content = await file.read()
    store = ImageStore(root_dir=str(MAOP_ROOT))
    img_id = store.save(
        session_id=session_id or "default",
        filename=file.filename or "upload.png",
        data=content,
        content_type=file.content_type or "",
    )
    return {"status": "ok", "data": {"image_id": img_id, "filename": file.filename}}


@router.get("/images/{session_id}")
@handle_api_errors("list session images")
async def list_session_images(session_id: str) -> dict[str, Any]:
    """List all images for a chat session."""
    from maop.core.image_store import ImageStore
    store = ImageStore(root_dir=str(MAOP_ROOT))
    images = store.list_session_images(session_id)
    return {"status": "ok", "data": [img.model_dump() for img in images]}


@router.delete("/images/{image_id}")
@handle_api_errors("delete image")
async def delete_image(image_id: str, request: Request) -> dict[str, Any]:
    """Delete an uploaded image."""
    require_admin(request)
    from maop.core.image_store import ImageStore
    store = ImageStore(root_dir=str(MAOP_ROOT))
    deleted = store.delete(image_id)
    return {"status": "ok" if deleted else "not_found", "deleted": deleted}
