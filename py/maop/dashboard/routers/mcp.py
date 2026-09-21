"""MAOP Dashboard — MCP Client API routes.

业务逻辑已提取至 ``maop.dashboard.services.plugin_service``（§2 MCP）。
本 router 仅保留：路由定义 / 请求参数解析 / 权限检查 /
service 调用 / 响应格式化 / 错误处理。
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from maop.core.security.middleware import require_admin
from maop.dashboard.error_handler import handle_api_errors
from maop.dashboard.routers.state import MAOP_ROOT  # noqa: F401
from maop.dashboard.services import plugin_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/mcp", tags=["mcp"])


class ServerCreate(BaseModel):
    name: str
    transport: str = "stdio"
    command: str = ""
    args: list[str] = []
    url: str = ""
    env: dict[str, str] = {}
    enabled: bool = True
    auto_connect: bool = False
    timeout: float = 30.0


class ToolCallRequest(BaseModel):
    tool: str
    arguments: dict[str, Any] = {}


# ── 单例转发（供测试注入，转发到 service 层）──────────────────────
# 保留模块级 ``_mcp_hub`` / ``_mcp_marketplace`` 作为兼容属性：
# 实际单例由 service 持有，此处仅转发访问器。
_mcp_hub: Any = None
_mcp_marketplace: Any = None


def _get_hub() -> Any:
    return plugin_service._get_hub()


def _set_hub(hub: Any) -> None:
    plugin_service._set_hub(hub)


def _get_marketplace() -> Any:
    return plugin_service._get_marketplace()


def _set_marketplace(mp: Any) -> None:
    plugin_service._set_marketplace(mp)


@router.post("/connect/{server_name}")
@handle_api_errors
async def connect_server(server_name: str, request: Request) -> dict[str, Any]:
    require_admin(request)
    result = await plugin_service.connect_server(server_name)
    if result is None:
        raise HTTPException(status_code=404, detail=f"Server {server_name} not found")
    return result


@router.post("/disconnect/{server_name}")
@handle_api_errors
async def disconnect_server(server_name: str, request: Request) -> dict[str, Any]:
    require_admin(request)
    return await plugin_service.disconnect_server(server_name)


@router.get("/servers")
@handle_api_errors
async def list_servers(request: Request) -> dict[str, Any]:
    require_admin(request)
    result = plugin_service.list_servers()
    return {"status": "ok", **result}


@router.post("/servers")
@handle_api_errors
async def add_server(body: ServerCreate, request: Request) -> dict[str, Any]:
    require_admin(request)
    return plugin_service.add_server(
        name=body.name,
        transport=body.transport,
        command=body.command,
        args=body.args,
        url=body.url,
        env=body.env,
    )


@router.delete("/servers/{server_name}")
@handle_api_errors
async def remove_server(server_name: str, request: Request) -> dict[str, Any]:
    require_admin(request)
    removed = plugin_service.remove_server(server_name)
    if not removed:
        raise HTTPException(status_code=404, detail=f"Server {server_name} not found")
    return {"status": "ok", "server": server_name}


@router.get("/tools")
@handle_api_errors
async def list_tools(request: Request) -> dict[str, Any]:
    require_admin(request)
    result = plugin_service.list_tools()
    return {"status": "ok", **result}


@router.post("/call")
@handle_api_errors
async def call_tool(body: ToolCallRequest, request: Request) -> dict[str, Any]:
    require_admin(request)
    # C-4 fix: forward the authenticated caller's identity and roles to
    # MCPHub.call_tool_by_name so the δ-3 permission checker can enforce
    # per-user (allowed_users) and per-role (allowed_roles) scope on each
    # server config. Without this, the checker silently falls back to
    # default-allow for the user/role dimensions even when a server
    # carries an allowed_users / allowed_roles whitelist — a permission
    # bypass.
    user_context = {
        "user_id": getattr(request.state, "auth_identity", "anonymous"),
        "roles": getattr(request.state, "auth_roles", []) or [],
    }
    return await plugin_service.call_tool(
        body.tool, body.arguments, user_context=user_context,
    )


@router.get("/health")
@handle_api_errors
async def health_check(request: Request) -> dict[str, Any]:
    require_admin(request)
    result = await plugin_service.health_check()
    return {"status": "ok", **result}


# ── Marketplace ─────────────────────────────────────────────────
# P1 闭环: 后端 marketplace 数据源已接入 ``MCPMarketplace``，以下端点
# 对齐 SkillMarket.vue 契约（list/install），不再返回空壳或 501。

@router.get("/marketplace/tools")
@handle_api_errors
async def marketplace_tools(request: Request) -> dict[str, Any]:
    """列出 Marketplace 可安装工具.

    聚合所有已启用 registry 的 catalog, 并标注每个工具是否已安装
    (查 ``mcp_installed.yaml``). 网络不可达或 registry 配置缺失时
    返回空列表而非 500, 前端 ``SkillMarket.vue`` 降级为 EmptyState.
    """
    require_admin(request)
    result = plugin_service.marketplace_tools()
    return {"status": "ok", **result}


@router.post("/marketplace/tools/{tool_id}/install")
@handle_api_errors
async def marketplace_install(tool_id: str, request: Request) -> dict[str, Any]:
    """安装 Marketplace 工具.

    完整安装流程:
      1. 权限校验 (``require_admin``)
      2. 输入校验 (``tool_id`` 非空)
      3. ``MCPMarketplace.install`` — 在已启用 registry 中查找工具,
         下载并校验 SHA-256 (若提供), 写入 ``mcp_installed.yaml``
      4. ``MCPHub.add_server`` — 将安装后的 ``MCPServerConfig`` 注册
         到本地 hub (状态 DISCONNECTED, 不自动连接)
      5. 返回安装结果 (server name / transport / 已注册标志)

    安全约束:
      - 未受信 registry + 无 checksum + 未显式 opt-in → 拒绝安装
        (``MCPMarketplace`` 内部抛 ``ValueError``, 这里转 400)
      - 网络下载失败 / checksum 校验失败 → 400
      - 工具未找到 → 404
    """
    require_admin(request)
    if not tool_id or not tool_id.strip():
        raise HTTPException(status_code=400, detail="tool_id must not be empty")
    try:
        result = plugin_service.marketplace_install(tool_id)
    except ValueError as exc:
        # 批次3A: 脱敏——安装错误细节不暴露给客户端，仅日志记录。
        msg = str(exc)
        logger.warning("[mcp] Install failed for tool %s: %s", tool_id, exc)
        if "not found" in msg.lower():
            raise HTTPException(status_code=404, detail="Tool not found") from exc
        raise HTTPException(status_code=400, detail="Tool installation failed") from exc
    return {"status": "ok", **result}
