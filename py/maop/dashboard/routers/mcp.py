"""MAOP Dashboard — MCP Client API routes."""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Request
from pydantic import BaseModel

from maop.dashboard.error_handler import handle_api_errors
from maop.dashboard.routers.state import MAOP_ROOT
from maop.core.middleware import require_admin

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


_mcp_hub = None
_mcp_registry = None


def _get_hub() -> Any:
    global _mcp_hub
    if _mcp_hub is None:
        from maop.core.mcp_hub import MCPHub
        _mcp_hub = MCPHub(root_dir=str(MAOP_ROOT))
    return _mcp_hub


def _get_registry() -> Any:
    global _mcp_registry
    if _mcp_registry is None:
        from maop.core.mcp_registry import MCPRegistry
        _mcp_registry = MCPRegistry(root_dir=str(MAOP_ROOT))
    return _mcp_registry


@router.post("/connect/{server_name}")
@handle_api_errors
async def connect_server(server_name: str, request: Request) -> dict[str, Any]:
    require_admin(request)
    hub = _get_hub()
    ok = await hub.connect_server(server_name)
    return {"status": "ok" if ok else "failed", "server": server_name}


@router.post("/disconnect/{server_name}")
@handle_api_errors
async def disconnect_server(server_name: str, request: Request) -> dict[str, Any]:
    require_admin(request)
    hub = _get_hub()
    await hub.disconnect_server(server_name)
    return {"status": "ok", "server": server_name}


@router.get("/servers")
@handle_api_errors
async def list_servers() -> dict[str, Any]:
    hub = _get_hub()
    servers = hub.list_servers()
    return {"servers": servers, "count": len(servers)}


@router.post("/servers")
@handle_api_errors
async def add_server(body: ServerCreate, request: Request) -> dict[str, Any]:
    require_admin(request)
    from maop.core.mcp_hub import MCPServerConfig, TransportType
    hub = _get_hub()
    config = MCPServerConfig(
        name=body.name,
        transport=TransportType(body.transport),
        command=body.command,
        args=body.args,
        url=body.url,
        env=body.env,

    )
    hub.add_server(config)
    return {"status": "ok", "server": body.name}


@router.delete("/servers/{server_name}")
@handle_api_errors
async def remove_server(server_name: str, request: Request) -> dict[str, Any]:
    require_admin(request)
    hub = _get_hub()
    removed = hub.remove_server(server_name)
    return {"status": "ok" if removed else "not_found", "server": server_name}


@router.get("/tools")
@handle_api_errors
async def list_tools() -> dict[str, Any]:
    hub = _get_hub()
    tools = hub.all_tools()
    return {"tools": [t.model_dump() if hasattr(t, "model_dump") else str(t) for t in tools], "count": len(tools)}


@router.post("/call")
@handle_api_errors
async def call_tool(body: ToolCallRequest, request: Request) -> dict[str, Any]:
    require_admin(request)
    hub = _get_hub()
    result = await hub.call_tool(body.tool, body.arguments)
    return result.model_dump() if hasattr(result, "model_dump") else result


@router.get("/health")
@handle_api_errors
async def health_check() -> dict[str, Any]:
    hub = _get_hub()
    health = hub.health_check_all()
    return {"health": health}
