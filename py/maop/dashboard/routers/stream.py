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


def _check_sse_token(request: Request) -> None:
    """EventSource cannot set Authorization header, so SSE clients pass
    JWT via ?token= query param. Extract and validate it here, setting
    request.state.auth_roles for require_admin to work."""
    from maop.dashboard.routers.auth import get_auth_mgr
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
        pass  # invalid token → require_admin will reject


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
    import asyncio
    import json
    import time

    async def generate():
        while True:
            try:
                # Gather live system state
                state = {"ts": time.time()}
                try:
                    from maop.dashboard.data_bridge import get_bridge
                    bridge = get_bridge()
                    # F-P0-1 fix: call async snapshot() properly
                    import asyncio as _aio
                    snap = await bridge.snapshot() if bridge else {}
                    if snap:
                        state.update({
                            "agents": snap.get("agents_count", 0),
                            "healthy_agents": snap.get("healthy_agents", 0),
                            "total_agents": snap.get("total_agents", 0),
                            "memory_usage_pct": snap.get("memory_usage_pct", 0),
                            "cpu_pct": snap.get("cpu_pct", 0),
                            "queue_health_pct": snap.get("queue_health_pct", 0),
                            "active_streams": snap.get("active_streams", 0),
                            "success_rate": snap.get("success_rate", 0),
                            "delegations": snap.get("delegations", []),
                        })
                except Exception as exc:
                    logger.warning("[stream] snapshot failed: %s", exc)
                yield f"event: state\ndata: {json.dumps(state)}\n\n"
            except Exception:
                pass
            await asyncio.sleep(2)

    return StreamingResponse(generate(), media_type="text/event-stream")


@router.get("/active")
@handle_api_errors
async def list_active_streams(request: Request) -> dict[str, Any]:
    """List all currently active streaming executions."""
    require_admin(request)
    from maop.core.streaming import get_stream_registry
    registry = get_stream_registry()
    active = registry.active()
    return {"active": active, "count": len(active)}

@router.get("/{trace_id}")
@handle_api_errors
async def stream_trace(trace_id: str, request: Request) -> Any:
    """SSE endpoint: subscribe to real-time output for a running execution."""
    _check_sse_token(request)
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
