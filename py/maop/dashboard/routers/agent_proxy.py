"""MAOP Dashboard — Agent Bridge API endpoints."""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Request

from maop.core.security.middleware import require_admin
from maop.dashboard.error_handler import handle_api_errors

from .state import MAOP_ROOT

logger = logging.getLogger(__name__)

router = APIRouter()

_agent_proxy = None

def _get_bridge() -> Any:
    global _agent_proxy
    if _agent_proxy is None:
        from maop.core.agent.delegation.agent_proxy import AgentProxy
        _agent_proxy = AgentProxy(root_dir=str(MAOP_ROOT))
    return _agent_proxy


@router.get("/api/bridge/adapters")
@handle_api_errors("Bridge adapters", error_value={"adapters": [], "count": 0, "error": "List failed"})
async def api_bridge_adapters() -> dict[str, Any]:
    """List available bridge adapters."""
    bridge = _get_bridge()
    names = bridge.list_adapters()
    statuses = []
    for name in names:
        try:
            s = bridge.get_status(name)
            statuses.append(s.model_dump())
        except Exception:
            statuses.append({"name": name, "error": "status unavailable"})
    return {"adapters": statuses, "count": len(statuses)}


@router.post("/api/bridge/call")
@handle_api_errors("Bridge call", error_value={"status": "error", "error": "Call failed"})
async def api_bridge_call(request: Request) -> dict[str, Any]:
    """Proxy a call through a bridge adapter."""
    require_admin(request)
    body = await request.json()
    adapter_name = body.get("adapter", "")
    task = body.get("task", "")
    if not adapter_name or not task:
        raise HTTPException(400, "missing adapter or task")
    bridge = _get_bridge()
    try:
        result = bridge.call(adapter_name, task, **body.get("kwargs", {}))
        return {"status": "ok", "result": result}
    except KeyError as exc:
        raise HTTPException(404, str(exc))
    except RuntimeError as exc:
        raise HTTPException(500, str(exc))


@router.get("/api/bridge/health")
@handle_api_errors("Bridge health", error_value={"health": {}, "error": "Health check failed"})
async def api_bridge_health() -> dict[str, Any]:
    """Check bridge adapter health status."""
    bridge = _get_bridge()
    health = bridge.health_check_all()
    return {"health": health}


@router.post("/api/bridge/sync-config")
@handle_api_errors("Bridge sync-config", error_value={"status": "error", "error": "Sync failed"})
async def api_bridge_sync_config(request: Request) -> dict[str, Any]:
    """Sync bridge adapter configuration."""
    require_admin(request)
    body = await request.json()
    adapter_name = body.get("adapter", "")
    config = body.get("config", {})
    if not adapter_name:
        raise HTTPException(400, "missing adapter")
    bridge = _get_bridge()
    try:
        bridge.sync_config(adapter_name, config)
        return {"status": "ok"}
    except KeyError as exc:
        raise HTTPException(404, str(exc))
