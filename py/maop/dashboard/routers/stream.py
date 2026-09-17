"""MAOP Dashboard — Streaming API routes.

SSE endpoints for real-time agent output streaming.

Business logic (event classification, async SSE generators, stream
registry access) lives in :mod:`maop.dashboard.services.chat_service`;
this module only does request parsing, auth, service dispatch, and
``StreamingResponse`` construction.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse

from maop.core.security.middleware import require_admin
from maop.dashboard.error_handler import handle_api_errors
from maop.dashboard.services import chat_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/stream", tags=["stream"])


def _check_sse_token(request: Request) -> None:
    """EventSource cannot set Authorization header, so SSE clients pass
    JWT via ?token= query param. Extract and validate it here, setting
    request.state.auth_roles for require_admin to work.

    Kept on the router module (not in the service) because this is
    request-layer concern, and tests monkeypatch it directly on the
    stream module (see ``tests/test_dag_sse_endpoint.py``).
    """
    from maop.dashboard.services.auth_service import get_auth_mgr
    token = request.query_params.get("token", "")
    if not token:
        return  # let require_admin handle the missing-auth case
    try:
        mgr = get_auth_mgr()
        result = mgr.jwt_handler.validate_token(token)
        if result and result.authenticated:
            request.state.auth_roles = result.roles
            request.state.auth_identity = result.identity
    except Exception:
        logger.warning('[stream] _check_sse_token：校验查询参数 token 失败已忽略，交由 require_admin 拒绝', exc_info=True)
        # invalid token → require_admin will reject


@router.get("")
@handle_api_errors
async def global_state_stream(request: Request) -> Any:
    """SSE endpoint: push global system state every 2s for Monitor.vue.

    P0-2 fix: Monitor.vue uses useSSE('/api/stream') expecting event="state"
    with system metrics. This endpoint provides that, complementing the
    per-trace /{trace_id} streaming endpoint.

    P1-12 fix: EventSource can't set Authorization header, so check
    token from query param (injected by useSSE.js _buildUrl).
    """
    _check_sse_token(request)
    require_admin(request)

    return StreamingResponse(
        chat_service.stream_global_state_chunks(),
        media_type="text/event-stream",
    )


@router.get("/active")
@handle_api_errors
async def list_active_streams(request: Request) -> dict[str, Any]:
    """List all currently active streaming executions."""
    require_admin(request)
    data = chat_service.list_active_streams()
    return {"status": "ok", "active": data["active"], "count": data["count"]}


@router.get("/dag/{execution_id}")
@handle_api_errors
async def dag_progress_stream(execution_id: str, request: Request) -> Any:
    """SSE endpoint: stream DAG node progress events for an execution.

    Events:
      - event: node-status    (node state changes: pending/running/success/failed/skipped)
      - event: execution-complete (final event, closes the stream)

    Supports Last-Event-ID resumption: events with _id <= Last-Event-ID are skipped.
    """
    _check_sse_token(request)
    require_admin(request)

    # P1-17: Last-Event-ID 异常处理 — 无效值回退到 0
    try:
        last_event_id = int(request.headers.get("Last-Event-ID", "0"))
    except (ValueError, TypeError):
        last_event_id = 0

    return StreamingResponse(
        chat_service.stream_dag_progress_chunks(execution_id, last_event_id),
        media_type="text/event-stream",
    )


@router.get("/agent/{execution_id}")
@handle_api_errors
async def agent_token_stream(execution_id: str, request: Request) -> Any:
    """SSE endpoint: stream Agent execution tokens in real-time.

    v5F.0.0: Token-by-token streaming for agent task execution.
    Distinct from /api/chat/stream (chat LLM streaming) — this endpoint
    subscribes to a running agent execution's output stream.

    Events:
      - event: token    (each token/chunk: {"content": "..."})
      - event: meta     (metadata: {"agent": "...", "model": "...", "tokens": N})
      - event: done     (completion: {"content_length": N, "tokens": N})
      - event: error    (error: {"error": "..."})

    Falls back to event bus subscription if no active streamer exists.
    """
    _check_sse_token(request)
    require_admin(request)

    streamer = chat_service.get_streamer(execution_id)

    if streamer is not None:
        generator = chat_service.stream_from_streamer(streamer)
    else:
        # P1-17: Last-Event-ID 异常处理 — 无效值回退到 0
        try:
            last_event_id = int(request.headers.get("Last-Event-ID", "0"))
        except (ValueError, TypeError):
            last_event_id = 0
        generator = chat_service.stream_from_event_bus(execution_id, last_event_id)

    return StreamingResponse(
        generator,
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/{trace_id}")
@handle_api_errors
async def stream_trace(trace_id: str, request: Request) -> Any:
    """SSE endpoint: subscribe to real-time output for a running execution."""
    _check_sse_token(request)
    require_admin(request)

    streamer = chat_service.get_streamer(trace_id)

    if streamer is None:
        # P2 fix: 404 应返回 404 状态码，而非 200。
        raise HTTPException(status_code=404, detail=f"No active stream for trace_id {trace_id}")

    async def generate():
        async for chunk in streamer.sse.stream():
            yield chunk

    return StreamingResponse(generate(), media_type="text/event-stream")
