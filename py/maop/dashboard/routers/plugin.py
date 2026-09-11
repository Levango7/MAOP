"""MAOP Dashboard — Plugin Management API endpoints."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel

from maop.core.security.middleware import require_admin
from maop.dashboard.error_handler import handle_api_errors

from .state import MAOP_ROOT

router = APIRouter(prefix="/api/plugins", tags=["plugins"])


class PluginStartRequest(BaseModel):
    """启动插件请求体."""

    config: dict[str, Any] | None = None


class PluginConfigUpdateRequest(BaseModel):
    """更新插件配置请求体."""

    config: dict[str, Any] = {}


def _get_plugin_manager():
    from maop.core.agent.plugins_hooks.plugin import PluginManager
    return PluginManager(root_dir=str(MAOP_ROOT))


@router.get("")
@handle_api_errors
async def list_plugins(request: Request, state: str = Query("", description="Filter by state")) -> dict[str, Any]:
    require_admin(request)
    from maop.core.agent.plugins_hooks.plugin import PluginState
    mgr = _get_plugin_manager()
    filter_state = PluginState(state) if state else None
    plugins = mgr.list_plugins(state=filter_state)
    return {"status": "ok", "plugins": [p.model_dump() for p in plugins]}


@router.get("/{plugin_id}")
@handle_api_errors
async def get_plugin(request: Request, plugin_id: str) -> dict[str, Any]:
    require_admin(request)
    mgr = _get_plugin_manager()
    info = mgr.get_plugin(plugin_id)
    if info is None:
        raise HTTPException(status_code=404, detail="Plugin not found")
    return {"status": "ok", "plugin": info.model_dump()}


@router.post("/discover")
@handle_api_errors
async def discover_plugins(request: Request) -> dict[str, Any]:
    require_admin(request)
    mgr = _get_plugin_manager()
    found = mgr.discover()
    return {"status": "ok", "discovered": [p.model_dump() for p in found]}


@router.post("/{plugin_id}/load")
@handle_api_errors
async def load_plugin(plugin_id: str, request: Request) -> dict[str, Any]:
    require_admin(request)
    mgr = _get_plugin_manager()
    info = mgr.load(plugin_id)
    return {"status": "ok", "plugin": info.model_dump()}


@router.post("/{plugin_id}/start")
@handle_api_errors
async def start_plugin(plugin_id: str, request: Request, body: PluginStartRequest | None = None) -> dict[str, Any]:
    require_admin(request)
    mgr = _get_plugin_manager()
    config = body.config if body else None
    info = mgr.start(plugin_id, config=config)
    return {"status": "ok", "plugin": info.model_dump()}


@router.post("/{plugin_id}/stop")
@handle_api_errors
async def stop_plugin(plugin_id: str, request: Request) -> dict[str, Any]:
    require_admin(request)
    mgr = _get_plugin_manager()
    info = mgr.stop(plugin_id)
    return {"status": "ok", "plugin": info.model_dump()}


@router.post("/{plugin_id}/reload")
@handle_api_errors
async def reload_plugin(plugin_id: str, request: Request) -> dict[str, Any]:
    require_admin(request)
    mgr = _get_plugin_manager()
    info = mgr.reload(plugin_id)
    return {"status": "ok", "plugin": info.model_dump()}


@router.put("/{plugin_id}/config")
@handle_api_errors
async def update_plugin_config(plugin_id: str, request: Request, body: PluginConfigUpdateRequest) -> dict[str, Any]:
    require_admin(request)
    mgr = _get_plugin_manager()
    info = mgr.update_config(plugin_id, config=body.config)
    return {"status": "ok", "plugin": info.model_dump()}


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
