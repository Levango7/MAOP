"""MAOP Dashboard — Plugin Management API endpoints."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Query, Request

from maop.core.security.middleware import require_admin
from maop.dashboard.error_handler import handle_api_errors

router = APIRouter(prefix="/api/plugins", tags=["plugins"])


def _get_plugin_manager():
    from pathlib import Path

    from maop.core.agent.plugins_hooks.plugin import PluginManager
    root = Path(__file__).resolve().parent.parent.parent.parent
    return PluginManager(root_dir=str(root))


@router.get("")
@handle_api_errors
async def list_plugins(state: str = Query("", description="Filter by state")) -> dict[str, Any]:
    from maop.core.agent.plugins_hooks.plugin import PluginState
    mgr = _get_plugin_manager()
    filter_state = PluginState(state) if state else None
    plugins = mgr.list_plugins(state=filter_state)
    return {"plugins": [p.model_dump() for p in plugins]}


@router.get("/{plugin_id}")
@handle_api_errors
async def get_plugin(plugin_id: str) -> dict[str, Any]:
    mgr = _get_plugin_manager()
    info = mgr.get_plugin(plugin_id)
    if info is None:
        return {"error": "Plugin not found"}
    return {"plugin": info.model_dump()}


@router.post("/discover")
@handle_api_errors
async def discover_plugins(request: Request) -> dict[str, Any]:
    require_admin(request)
    mgr = _get_plugin_manager()
    found = mgr.discover()
    return {"discovered": [p.model_dump() for p in found]}


@router.post("/{plugin_id}/load")
@handle_api_errors
async def load_plugin(plugin_id: str, request: Request) -> dict[str, Any]:
    require_admin(request)
    mgr = _get_plugin_manager()
    info = mgr.load(plugin_id)
    return {"plugin": info.model_dump()}


@router.post("/{plugin_id}/start")
@handle_api_errors
async def start_plugin(plugin_id: str, request: Request, body: dict | None = None) -> dict[str, Any]:
    require_admin(request)
    mgr = _get_plugin_manager()
    config = body.get("config") if body else None
    info = mgr.start(plugin_id, config=config)
    return {"plugin": info.model_dump()}


@router.post("/{plugin_id}/stop")
@handle_api_errors
async def stop_plugin(plugin_id: str, request: Request) -> dict[str, Any]:
    require_admin(request)
    mgr = _get_plugin_manager()
    info = mgr.stop(plugin_id)
    return {"plugin": info.model_dump()}


@router.post("/{plugin_id}/reload")
@handle_api_errors
async def reload_plugin(plugin_id: str, request: Request) -> dict[str, Any]:
    require_admin(request)
    mgr = _get_plugin_manager()
    info = mgr.reload(plugin_id)
    return {"plugin": info.model_dump()}


@router.put("/{plugin_id}/config")
@handle_api_errors
async def update_plugin_config(plugin_id: str, request: Request, body: dict) -> dict[str, Any]:
    require_admin(request)
    mgr = _get_plugin_manager()
    info = mgr.update_config(plugin_id, config=body.get("config", {}))
    return {"plugin": info.model_dump()}


@router.post("/load-all")
@handle_api_errors
async def load_all_plugins(request: Request) -> dict[str, Any]:
    require_admin(request)
    mgr = _get_plugin_manager()
    results = mgr.load_all()
    return {"plugins": [p.model_dump() for p in results]}


@router.post("/start-all")
@handle_api_errors
async def start_all_plugins(request: Request) -> dict[str, Any]:
    require_admin(request)
    mgr = _get_plugin_manager()
    results = mgr.start_all()
    return {"plugins": [p.model_dump() for p in results]}


@router.post("/stop-all")
@handle_api_errors
async def stop_all_plugins(request: Request) -> dict[str, Any]:
    require_admin(request)
    mgr = _get_plugin_manager()
    results = mgr.stop_all()
    return {"plugins": [p.model_dump() for p in results]}
