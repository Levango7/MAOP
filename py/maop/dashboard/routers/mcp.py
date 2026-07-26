"""MAOP Dashboard — MCP Client API routes."""

from __future__ import annotations

import logging
import threading
from typing import Any

from fastapi import APIRouter, Request
from pydantic import BaseModel

from maop.core.middleware import require_admin
from maop.dashboard.error_handler import handle_api_errors
from maop.dashboard.routers.state import MAOP_ROOT

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
_mcp_hub_lock = threading.Lock()


def _try_init_delta_component(module_path: str, class_name: str) -> Any:
    """Import and construct a δ-3/4/5 component with default args.

    Returns the constructed instance, or ``None`` on any failure (import
    error, DB unavailable, etc.) so a single broken component never
    blocks ``MCPHub`` creation — the hub degrades gracefully to the
    pre-δ behaviour for that dimension.
    """
    try:
        import importlib
        mod = importlib.import_module(module_path)
        cls = getattr(mod, class_name)
        return cls()
    except Exception as exc:
        logger.warning(
            "[mcp_router] init %s.%s failed (degraded to None): %s",
            module_path, class_name, exc,
        )
        return None


def _get_hub() -> Any:
    """Lazy-init singleton MCPHub with the full δ-3/4/5 stack.

    The hub is constructed once (thread-safe via ``_mcp_hub_lock``) and
    reused for every request. δ-3 (permission_checker / audit_logger)
    and δ-5 (cache / concurrency / rate_limiter) components are injected
    with default construction; each is built defensively so a failure
    (e.g. DB unavailable) degrades only that component to ``None``
    rather than blocking hub creation.

    ``user_context_provider`` is ``None`` for now: the permission checker
    still enforces the tool-name dimensions (denied_tools / allowed_tools)
    from each server config; the user/role dimensions are only enforced
    when a server config carries ``allowed_users`` / ``allowed_roles``
    AND a per-call ``user_context`` is supplied.
    """
    global _mcp_hub
    if _mcp_hub is not None:
        return _mcp_hub
    with _mcp_hub_lock:
        if _mcp_hub is not None:  # double-checked locking
            return _mcp_hub
        from maop.core.mcp_hub import MCPHub

        # δ-3: permission gate + audit trail
        permission_checker = _try_init_delta_component(
            "maop.core.mcp_permission", "MCPPermissionChecker",
        )
        audit_logger = _try_init_delta_component(
            "maop.core.mcp_audit", "MCPAuditLogger",
        )
        # δ-5: resilience hooks — cache, per-server concurrency, RPM limiter
        cache = _try_init_delta_component(
            "maop.core.mcp_cache", "MCPCache",
        )
        concurrency = _try_init_delta_component(
            "maop.core.mcp_concurrency", "MCPServerConcurrency",
        )
        rate_limiter = _try_init_delta_component(
            "maop.core.mcp_concurrency", "MCPServerRateLimiter",
        )

        _mcp_hub = MCPHub(
            root_dir=str(MAOP_ROOT),
            permission_checker=permission_checker,
            audit_logger=audit_logger,
            cache=cache,
            concurrency=concurrency,
            rate_limiter=rate_limiter,
        )
        logger.info(
            "[mcp_router] MCPHub singleton initialised (δ-3/4/5 stack: "
            "permission=%s, audit=%s, cache=%s, concurrency=%s, rate_limiter=%s)",
            permission_checker is not None, audit_logger is not None,
            cache is not None, concurrency is not None, rate_limiter is not None,
        )
    return _mcp_hub


@router.post("/connect/{server_name}")
@handle_api_errors
async def connect_server(server_name: str, request: Request) -> dict[str, Any]:
    require_admin(request)
    hub = _get_hub()
    # δ-1: use MCPHub.connect(config) — look up previously registered config by name
    config = hub.get_server_config(server_name)
    if config is None:
        return {"status": "failed", "server": server_name}
    # Clear the old record so connect() inserts a fresh connected record
    hub.remove_server(server_name)
    server_id = await hub.connect(config)
    return {"status": "ok" if server_id else "failed", "server": server_name}


@router.post("/disconnect/{server_name}")
@handle_api_errors
async def disconnect_server(server_name: str, request: Request) -> dict[str, Any]:
    require_admin(request)
    hub = _get_hub()
    # δ-1: use MCPHub.disconnect(server_id) — resolve name to id
    server_id = hub.find_server_id_by_name(server_name)
    if server_id is not None:
        await hub.disconnect(server_id)
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
    result = await hub.call_tool_by_name(
        body.tool, body.arguments, user_context=user_context,
    )
    return result.model_dump() if hasattr(result, "model_dump") else result


@router.get("/health")
@handle_api_errors
async def health_check() -> dict[str, Any]:
    hub = _get_hub()
    health = await hub.health_check_all()
    return {"health": health}
