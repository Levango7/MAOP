"""MAOP Dashboard — MCP Client API routes."""

from __future__ import annotations

import logging
import threading
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from maop.core.security.middleware import require_admin
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
        from maop.core.mcp.mcp_hub import MCPHub

        # δ-3: permission gate + audit trail
        permission_checker = _try_init_delta_component(
            "maop.core.mcp.mcp_permission", "MCPPermissionChecker",
        )
        audit_logger = _try_init_delta_component(
            "maop.core.mcp.mcp_audit", "MCPAuditLogger",
        )
        # δ-5: resilience hooks — cache, per-server concurrency, RPM limiter
        cache = _try_init_delta_component(
            "maop.core.mcp.mcp_cache", "MCPCache",
        )
        concurrency = _try_init_delta_component(
            "maop.core.mcp.mcp_concurrency", "MCPServerConcurrency",
        )
        rate_limiter = _try_init_delta_component(
            "maop.core.mcp.mcp_concurrency", "MCPServerRateLimiter",
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
    from maop.core.mcp.mcp_hub import MCPServerConfig, TransportType
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


# ── Marketplace ─────────────────────────────────────────────────
# P1 闭环: 后端 marketplace 数据源已接入 ``MCPMarketplace``，以下端点
# 对齐 SkillMarket.vue 契约（list/install），不再返回空壳或 501。

_mcp_marketplace: Any = None
_mcp_marketplace_lock = threading.Lock()


def _get_marketplace() -> Any:
    """Lazy-init singleton MCPMarketplace.

    与 ``_get_hub`` 同样的双重检查锁模式。``MCPMarketplace`` 构造
    只读取 YAML 配置 + 创建缓存目录，不会发起网络请求，因此可以
    安全地在首次调用时初始化。
    """
    global _mcp_marketplace
    if _mcp_marketplace is not None:
        return _mcp_marketplace
    with _mcp_marketplace_lock:
        if _mcp_marketplace is not None:  # double-checked locking
            return _mcp_marketplace
        from maop.core.mcp.mcp_marketplace import MCPMarketplace
        _mcp_marketplace = MCPMarketplace()
        logger.info("[mcp_router] MCPMarketplace singleton initialised")
    return _mcp_marketplace


@router.get("/marketplace/tools")
@handle_api_errors
async def marketplace_tools() -> dict[str, Any]:
    """列出 Marketplace 可安装工具.

    聚合所有已启用 registry 的 catalog, 并标注每个工具是否已安装
    (查 ``mcp_installed.yaml``). 网络不可达或 registry 配置缺失时
    返回空列表而非 500, 前端 ``SkillMarket.vue`` 降级为 EmptyState.
    """
    try:
        mp = _get_marketplace()
        catalog = mp.fetch_catalog()
        installed = {s.get("name") for s in mp.list_installed()}
    except Exception as exc:
        logger.warning("[mcp.marketplace_tools] fetch failed: %s", exc)
        return {"tools": [], "count": 0}
    tools: list[dict[str, Any]] = []
    for srv in catalog:
        tools.append({
            "id": srv.name,
            "name": srv.name,
            "description": srv.description,
            "category": ", ".join(srv.tags) if srv.tags else "",
            "source": "mcp",
            "version": srv.version,
            "installed": srv.name in installed,
            "author": srv.author,
            "homepage": srv.homepage,
            "transport": srv.transport_type,
            "verified": srv.verified,
            "install_count": srv.install_count,
        })
    return {"tools": tools, "count": len(tools)}


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
    mp = _get_marketplace()
    # 1. marketplace 安装 (下载 + 校验 + 写 mcp_installed.yaml)
    try:
        config = mp.install(tool_id)
    except ValueError as exc:
        msg = str(exc)
        if "not found" in msg.lower():
            raise HTTPException(status_code=404, detail=msg) from exc
        raise HTTPException(status_code=400, detail=msg) from exc
    # 2. 注册到 MCPHub (不自动连接, 保持 DISCONNECTED 状态)
    hub = _get_hub()
    try:
        hub.add_server(config)
        registered = True
    except Exception as exc:
        # 安装已成功 (mcp_installed.yaml 已写入), 仅 hub 注册失败.
        # 不回滚安装 — 用户可手动 ``mcp connect`` 重试. 返回 partial 状态.
        logger.warning(
            "[mcp.marketplace_install] installed '%s' but hub.add_server failed: %s",
            tool_id, exc,
        )
        registered = False
    return {
        "status": "ok",
        "server": config.name,
        "transport": config.transport.value if hasattr(config.transport, "value") else str(config.transport),
        "registered": registered,
    }
