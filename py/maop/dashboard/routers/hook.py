"""MAOP Dashboard — Hook management API endpoints."""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Request

from maop.core.security.middleware import require_admin

from .error_handler import handle_api_errors
from .state import MAOP_ROOT

logger = logging.getLogger(__name__)

router = APIRouter()

_hook_mgr = None

def _get_hook_mgr() -> Any:
    global _hook_mgr
    if _hook_mgr is None:
        from maop.core.agent.plugins_hooks.hook_manager import HookManager
        _hook_mgr = HookManager(root_dir=str(MAOP_ROOT))
    return _hook_mgr


@router.post("/api/hook/register")
@handle_api_errors("Hook register", error_value={"status": "error", "error": "Register failed"})
async def api_hook_register(request: Request) -> dict[str, Any]:
    require_admin(request)
    body = await request.json()
    event = body.get("event", "")
    url = body.get("url", "")
    callback = body.get("callback", "")
    priority = body.get("priority", 0)
    description = body.get("description", "")
    if not event:
        raise HTTPException(400, "missing event")
    mgr = _get_hook_mgr()
    if url:
        hdef = mgr.register(event=event, url=url, priority=priority, description=description)
    elif callback:
        hdef = mgr.register(event=event, callback=lambda e, d: None, priority=priority, description=description)
    else:
        raise HTTPException(400, "must provide url or callback")
    return {"status": "ok", "hook": hdef.model_dump()}


@router.post("/api/hook/unregister")
@handle_api_errors("Hook unregister", error_value={"status": "error", "error": "Unregister failed"})
async def api_hook_unregister(request: Request) -> dict[str, Any]:
    require_admin(request)
    body = await request.json()
    hook_id = body.get("id", "")
    if not hook_id:
        raise HTTPException(400, "missing id")
    mgr = _get_hook_mgr()
    removed = mgr.unregister(hook_id)
    return {"status": "ok" if removed else "not_found", "removed": removed}


@router.post("/api/hook/enable")
@handle_api_errors("Hook enable", error_value={"status": "error", "error": "Enable failed"})
async def api_hook_enable(request: Request) -> dict[str, Any]:
    require_admin(request)
    body = await request.json()
    hook_id = body.get("id", "")
    if not hook_id:
        raise HTTPException(400, "missing id")
    mgr = _get_hook_mgr()
    result = mgr.enable(hook_id)
    return {"status": "ok" if result else "not_found"}


@router.post("/api/hook/disable")
@handle_api_errors("Hook disable", error_value={"status": "error", "error": "Disable failed"})
async def api_hook_disable(request: Request) -> dict[str, Any]:
    require_admin(request)
    body = await request.json()
    hook_id = body.get("id", "")
    if not hook_id:
        raise HTTPException(400, "missing id")
    mgr = _get_hook_mgr()
    result = mgr.disable(hook_id)
    return {"status": "ok" if result else "not_found"}


@router.get("/api/hook/list")
@handle_api_errors("Hook list", error_value={"hooks": [], "count": 0, "error": "List failed"})
async def api_hook_list(event: str = "") -> dict[str, Any]:
    mgr = _get_hook_mgr()
    hooks = mgr.list_hooks(event=event or "")
    return {"hooks": [h.model_dump() for h in hooks], "count": len(hooks)}


@router.get("/api/hook/get")
@handle_api_errors("Hook get", error_value={"status": "error", "error": "Get failed"})
async def api_hook_get(hook_id: str = "") -> dict[str, Any]:
    if not hook_id:
        raise HTTPException(400, "missing hook_id")
    mgr = _get_hook_mgr()
    hdef = mgr.get_hook(hook_id)
    if hdef is None:
        raise HTTPException(404, f"Hook {hook_id} not found")
    return {"status": "ok", "hook": hdef.model_dump()}


@router.post("/api/hook/trigger")
@handle_api_errors("Hook trigger", error_value={"status": "error", "error": "Trigger failed"})
async def api_hook_trigger(request: Request) -> dict[str, Any]:
    require_admin(request)
    body = await request.json()
    event = body.get("event", "")
    data = body.get("data", {})
    if not event:
        raise HTTPException(400, "missing event")
    mgr = _get_hook_mgr()
    results = await mgr.trigger(event, data)
    return {"status": "ok", "results": [r.model_dump() for r in results], "count": len(results)}


@router.get("/api/hook/logs")
@handle_api_errors("Hook logs", error_value={"logs": [], "error": "Logs failed"})
async def api_hook_logs(event: str = "", limit: int = 100) -> dict[str, Any]:
    mgr = _get_hook_mgr()
    logs = mgr.get_logs(event=event or "", limit=limit)
    return {"logs": logs, "count": len(logs)}


@router.get("/api/hook/events")
@handle_api_errors("Hook events", error_value={"events": [], "error": "Events failed"})
async def api_hook_events() -> dict[str, Any]:
    from maop.core.agent.plugins_hooks.hook_manager import LifecycleEvent
    events = [{"name": e.value, "phase": e.value.split(".")[-1], "domain": e.value.split(".")[0]} for e in LifecycleEvent]
    return {"events": events, "count": len(events)}
