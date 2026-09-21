"""MAOP Dashboard — Plugin Management API endpoints.

业务逻辑已提取至 ``maop.dashboard.services.plugin_service``（§1 Plugin）。
本 router 仅保留：路由定义 / 请求参数解析 / 权限检查 /
service 调用 / 响应格式化 / 错误处理。
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel

from maop.core.security.middleware import require_admin
from maop.dashboard.error_handler import handle_api_errors
from maop.dashboard.services import plugin_service

from .state import MAOP_ROOT  # noqa: F401

router = APIRouter(prefix="/api/plugins", tags=["plugins"])


class PluginStartRequest(BaseModel):
    """启动插件请求体."""

    config: dict[str, Any] | None = None


class PluginConfigUpdateRequest(BaseModel):
    """更新插件配置请求体."""

    config: dict[str, Any] = {}


# ── 单例转发（供测试注入，转发到 service 层）──────────────────────
def _get_plugin_manager() -> Any:
    return plugin_service._get_plugin_manager()


def _set_plugin_manager(mgr: Any) -> None:
    plugin_service._set_plugin_manager(mgr)


@router.get("")
@handle_api_errors
async def list_plugins(request: Request, state: str = Query("", description="Filter by state")) -> dict[str, Any]:
    require_admin(request)
    result = plugin_service.list_plugins(state)
    return {"status": "ok", **result}


@router.get("/{plugin_id}")
@handle_api_errors
async def get_plugin(request: Request, plugin_id: str) -> dict[str, Any]:
    require_admin(request)
    info = plugin_service.get_plugin(plugin_id)
    if info is None:
        raise HTTPException(status_code=404, detail="Plugin not found")
    return {"status": "ok", "plugin": info}


@router.post("/discover")
@handle_api_errors
async def discover_plugins(request: Request) -> dict[str, Any]:
    require_admin(request)
    result = plugin_service.discover_plugins()
    return {"status": "ok", **result}


@router.post("/{plugin_id}/load")
@handle_api_errors
async def load_plugin(plugin_id: str, request: Request) -> dict[str, Any]:
    require_admin(request)
    info = plugin_service.load_plugin(plugin_id)
    return {"status": "ok", "plugin": info}


@router.post("/{plugin_id}/start")
@handle_api_errors
async def start_plugin(plugin_id: str, request: Request, body: PluginStartRequest | None = None) -> dict[str, Any]:
    require_admin(request)
    config = body.config if body else None
    info = plugin_service.start_plugin(plugin_id, config=config)
    return {"status": "ok", "plugin": info}


@router.post("/{plugin_id}/stop")
@handle_api_errors
async def stop_plugin(plugin_id: str, request: Request) -> dict[str, Any]:
    require_admin(request)
    info = plugin_service.stop_plugin(plugin_id)
    return {"status": "ok", "plugin": info}


@router.post("/{plugin_id}/reload")
@handle_api_errors
async def reload_plugin(plugin_id: str, request: Request) -> dict[str, Any]:
    require_admin(request)
    info = plugin_service.reload_plugin(plugin_id)
    return {"status": "ok", "plugin": info}


@router.put("/{plugin_id}/config")
@handle_api_errors
async def update_plugin_config(plugin_id: str, request: Request, body: PluginConfigUpdateRequest) -> dict[str, Any]:
    require_admin(request)
    info = plugin_service.update_plugin_config(plugin_id, config=body.config)
    return {"status": "ok", "plugin": info}


@router.post("/load-all")
@handle_api_errors
async def load_all_plugins(request: Request) -> dict[str, Any]:
    require_admin(request)
    result = plugin_service.load_all_plugins()
    return {"status": "ok", **result}


@router.post("/start-all")
@handle_api_errors
async def start_all_plugins(request: Request) -> dict[str, Any]:
    require_admin(request)
    result = plugin_service.start_all_plugins()
    return {"status": "ok", **result}


@router.post("/stop-all")
@handle_api_errors
async def stop_all_plugins(request: Request) -> dict[str, Any]:
    require_admin(request)
    result = plugin_service.stop_all_plugins()
    return {"status": "ok", **result}
