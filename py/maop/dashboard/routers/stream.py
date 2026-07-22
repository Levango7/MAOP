"""MAOP Dashboard — Streaming API routes.

SSE endpoints for real-time agent output streaming.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse

from maop.dashboard.error_handler import handle_api_errors
from maop.core.middleware import require_admin

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/stream", tags=["stream"])


@router.get("/{trace_id}")
@handle_api_errors
async def stream_trace(trace_id: str, request: Request) -> Any:
    """SSE endpoint: subscribe to real-time output for a running execution."""
    require_admin(request)
    from maop.core.streaming import get_stream_registry

    registry = get_stream_registry()
    streamer = registry.get(trace_id)

    if streamer is None:
        return {"status": "not_found", "trace_id": trace_id, "message": "No active stream for this trace_id"}

    async def generate():
        async for chunk in streamer.sse.stream():
            yield chunk

    return StreamingResponse(generate(), media_type="text/event-stream")


@router.get("/")
@handle_api_errors
async def list_active_streams(request: Request) -> Any:
    """List all currently active streaming executions."""
    require_admin(request)
    from maop.core.streaming import get_stream_registry

    registry = get_stream_registry()
    return {"active": registry.active(), "count": len(registry.active())}
