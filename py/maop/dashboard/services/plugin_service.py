"""Plugins & Integrations 域 Service 层 — Plugin + MCP 子域.

框架无关（不导入 FastAPI），函数接收显式参数，返回普通 dict/object，
不抛 HTTPException。可独立单元测试，无需 HTTP 上下文。

本模块从以下 2 个 router 提取业务逻辑：

- ``plugin``  — Plugin Manager 插件生命周期管理 (load/start/stop/reload/config)
- ``mcp``     — MCP Hub 客户端管理 + Marketplace 安装

每个子域对应一组 ``_get_xxx`` 单例访问器 + 业务函数。单例使用
双重检查锁定保护，与原 router 风格一致；``_set_xxx`` 供测试注入。

``MAOP_ROOT`` 从 ``maop.dashboard.routers.state`` 显式导入，保持与
原 router 同一的路径来源；service 不依赖 router 模块属性。
"""

from __future__ import annotations

import logging
import threading
from typing import Any

logger = logging.getLogger(__name__)


# ══════════════════════════════════════════════════════════════════
# §1  Plugin Manager — 插件生命周期管理
# ══════════════════════════════════════════════════════════════════

_plugin_manager: Any = None
_plugin_manager_lock = threading.Lock()


def _get_plugin_manager() -> Any:
    """惰性初始化全局 PluginManager 单例。"""
    global _plugin_manager
    if _plugin_manager is None:
        with _plugin_manager_lock:
            if _plugin_manager is None:
                from maop.core.agent.plugins_hooks.plugin import PluginManager
                from maop.dashboard.routers.state import MAOP_ROOT
                _plugin_manager = PluginManager(root_dir=str(MAOP_ROOT))
    return _plugin_manager


def _set_plugin_manager(mgr: Any) -> None:
    """供测试注入自定义 PluginManager（隔离文件系统）。"""
    global _plugin_manager
    with _plugin_manager_lock:
        _plugin_manager = mgr


def list_plugins(state: str = "") -> dict[str, Any]:
    """列出所有插件，可选按状态过滤。

    Parameters
    ----------
    state : str
        状态过滤值（空字符串表示不过滤）。

    Returns
    -------
    dict
        ``{"plugins": [...]}`` — plugins 为 model_dump 后的 dict 列表。
    """
    from maop.core.agent.plugins_hooks.plugin import PluginState
    mgr = _get_plugin_manager()
    filter_state = PluginState(state) if state else None
    plugins = mgr.list_plugins(state=filter_state)
    return {"plugins": [p.model_dump() for p in plugins]}


def get_plugin(plugin_id: str) -> dict[str, Any] | None:
    """获取单个插件详情。返回 model_dump 后的 dict 或 None（不存在）。"""
    mgr = _get_plugin_manager()
    info = mgr.get_plugin(plugin_id)
    if info is None:
        return None
    return info.model_dump()


def discover_plugins() -> dict[str, Any]:
    """发现可用插件。返回 ``{"discovered": [...]}``。"""
    mgr = _get_plugin_manager()
    found = mgr.discover()
    return {"discovered": [p.model_dump() for p in found]}


def load_plugin(plugin_id: str) -> dict[str, Any]:
    """加载指定插件。返回 model_dump 后的插件 dict。"""
    mgr = _get_plugin_manager()
    info = mgr.load(plugin_id)
    return info.model_dump()


def start_plugin(plugin_id: str, config: dict[str, Any] | None = None) -> dict[str, Any]:
    """启动指定插件。返回 model_dump 后的插件 dict。"""
    mgr = _get_plugin_manager()
    info = mgr.start(plugin_id, config=config)
    return info.model_dump()


def stop_plugin(plugin_id: str) -> dict[str, Any]:
    """停止指定插件。返回 model_dump 后的插件 dict。"""
    mgr = _get_plugin_manager()
    info = mgr.stop(plugin_id)
    return info.model_dump()


def reload_plugin(plugin_id: str) -> dict[str, Any]:
    """重载指定插件。返回 model_dump 后的插件 dict。"""
    mgr = _get_plugin_manager()
    info = mgr.reload(plugin_id)
    return info.model_dump()


def update_plugin_config(plugin_id: str, config: dict[str, Any]) -> dict[str, Any]:
    """更新插件配置。返回 model_dump 后的插件 dict。"""
    mgr = _get_plugin_manager()
    info = mgr.update_config(plugin_id, config=config)
    return info.model_dump()


def load_all_plugins() -> dict[str, Any]:
    """加载所有插件。返回 ``{"plugins": [...]}``。"""
    mgr = _get_plugin_manager()
    results = mgr.load_all()
    return {"plugins": [p.model_dump() for p in results]}


def start_all_plugins() -> dict[str, Any]:
    """启动所有插件。返回 ``{"plugins": [...]}``。"""
    mgr = _get_plugin_manager()
    results = mgr.start_all()
    return {"plugins": [p.model_dump() for p in results]}


def stop_all_plugins() -> dict[str, Any]:
    """停止所有插件。返回 ``{"plugins": [...]}``。"""
    mgr = _get_plugin_manager()
    results = mgr.stop_all()
    return {"plugins": [p.model_dump() for p in results]}


# ══════════════════════════════════════════════════════════════════
# §2  MCP Hub — MCP 客户端管理 + Marketplace
# ══════════════════════════════════════════════════════════════════

_mcp_hub: Any = None
_mcp_hub_lock = threading.Lock()

_mcp_marketplace: Any = None
_mcp_marketplace_lock = threading.Lock()


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
            "[mcp_service] init %s.%s failed (degraded to None): %s",
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
    """
    global _mcp_hub
    if _mcp_hub is not None:
        return _mcp_hub
    with _mcp_hub_lock:
        if _mcp_hub is not None:  # double-checked locking
            return _mcp_hub
        from maop.core.mcp.mcp_hub import MCPHub
        from maop.dashboard.routers.state import MAOP_ROOT

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
            "[mcp_service] MCPHub singleton initialised (δ-3/4/5 stack: "
            "permission=%s, audit=%s, cache=%s, concurrency=%s, rate_limiter=%s)",
            permission_checker is not None, audit_logger is not None,
            cache is not None, concurrency is not None, rate_limiter is not None,
        )
    return _mcp_hub


def _set_hub(hub: Any) -> None:
    """供测试注入自定义 MCPHub（隔离 δ 组件）。"""
    global _mcp_hub
    with _mcp_hub_lock:
        _mcp_hub = hub


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
        logger.info("[mcp_service] MCPMarketplace singleton initialised")
    return _mcp_marketplace


def _set_marketplace(mp: Any) -> None:
    """供测试注入自定义 MCPMarketplace（隔离网络）。"""
    global _mcp_marketplace
    with _mcp_marketplace_lock:
        _mcp_marketplace = mp


async def connect_server(server_name: str) -> dict[str, Any] | None:
    """连接指定 MCP server。

    Returns
    -------
    dict | None
        ``{"status": ..., "server": server_name}``；若 server 配置不存在返回 None。
    """
    hub = _get_hub()
    # δ-1: use MCPHub.connect(config) — look up previously registered config by name
    config = hub.get_server_config(server_name)
    if config is None:
        return None
    # Clear the old record so connect() inserts a fresh connected record
    hub.remove_server(server_name)
    server_id = await hub.connect(config)
    return {"status": "ok" if server_id else "failed", "server": server_name}


async def disconnect_server(server_name: str) -> dict[str, Any]:
    """断开指定 MCP server。返回 ``{"status": "ok", "server": server_name}``。"""
    hub = _get_hub()
    # δ-1: use MCPHub.disconnect(server_id) — resolve name to id
    server_id = hub.find_server_id_by_name(server_name)
    if server_id is not None:
        await hub.disconnect(server_id)
    return {"status": "ok", "server": server_name}


def list_servers() -> dict[str, Any]:
    """列出所有 MCP server。返回 ``{"servers": ..., "count": int}``。"""
    hub = _get_hub()
    servers = hub.list_servers()
    return {"servers": servers, "count": len(servers)}


def add_server(
    *,
    name: str,
    transport: str,
    command: str = "",
    args: list[str] | None = None,
    url: str = "",
    env: dict[str, str] | None = None,
) -> dict[str, Any]:
    """添加 MCP server 配置。返回 ``{"status": "ok", "server": name}``。"""
    from maop.core.mcp.mcp_hub import MCPServerConfig, TransportType
    hub = _get_hub()
    config = MCPServerConfig(
        name=name,
        transport=TransportType(transport),
        command=command,
        args=args or [],
        url=url,
        env=env or {},
    )
    hub.add_server(config)
    return {"status": "ok", "server": name}


def update_server(
    server_id: str,
    *,
    name: str,
    transport: str,
    command: str = "",
    args: list[str] | None = None,
    url: str = "",
    env: dict[str, str] | None = None,
) -> dict[str, Any] | None:
    """按 id 更新 MCP server 配置。返回 ``None`` 表示该 id 不存在。

    2026-09-23 新增：前端 McpManager 编辑服务器时调用
    ``PUT /api/mcp/servers/{id}``，但 service 层只有 add/remove/list
    （hub 亦只有 ``add_server``，且它会重新生成 id）。
    """
    from maop.core.mcp.mcp_hub import MCPServerConfig, TransportType
    hub = _get_hub()
    config = MCPServerConfig(
        name=name,
        transport=TransportType(transport),
        command=command,
        args=args or [],
        url=url,
        env=env or {},
    )
    ok = hub.update_server(server_id, config)
    if not ok:
        return None
    return {"status": "ok", "server": name, "id": server_id}


def topology() -> dict[str, Any]:
    """MCP 拓扑图数据（servers ↔ tools，外加 Agent 节点）。

    2026-09-23 新增：供 Tools 页面的「MCP 拓扑」标签页使用。前端期望
    ``{"servers": [], "tools": [], "agents": [], "edges": []}``。

    **边只输出真实存在的关系**。目前后端能确定的只有
    ``tool.server_name`` → server→tool 一条。
    Agent 与 MCP server 之间**没有任何存储层面的关联**
    （``AgentDescriptor.adapter_config`` 里没有 server 字段），
    所以不生成 agent 边 —— 宁可图上少一条线，也不要画一条编造的线。
    （前端对 ``type === 'server-agent'`` 的边有特殊配色，该类型当前
    不会出现；这样至少不会出现指向错误的关系。）

    Returns
    -------
    dict
        ``{"servers": [...], "tools": [...], "agents": [...], "edges": [...]}``
    """
    hub = _get_hub()

    raw_servers = hub.list_servers()
    servers = [
        s.model_dump(mode="json") if hasattr(s, "model_dump") else dict(s)
        for s in raw_servers
    ]

    raw_tools = hub.all_tools()
    tools = []
    for t in raw_tools:
        d = t.model_dump(mode="json") if hasattr(t, "model_dump") else dict(t)
        name = d.get("name", "")
        server_name = d.get("server_name", "")
        # 前端按 tl.id 建节点；MCPTool 无 id 字段，用 server:name 合成，
        # 保证同一 server 下重名的工具也不会撞 id。
        d.setdefault("id", f"{server_name}:{name}" if server_name else name)
        tools.append(d)

    edges = []
    for t in tools:
        if t.get("server_name"):
            edges.append({
                "id": f"{t['server_name']}->{t.get('id', '')}",
                "source": t["server_name"],
                "target": t.get("id", ""),
                "type": "server-tool",
            })

    # Agent 节点：来自注册表（本地服务失败不影响拓扑其余部分）
    agents: list[dict[str, Any]] = []
    try:
        from maop.dashboard.services import agent_service

        listed = agent_service.list_agents(None).get("agents", [])
        for a in listed:
            if not isinstance(a, dict):
                a = a.model_dump(mode="json") if hasattr(a, "model_dump") else {}
            agents.append({
                "name": a.get("name", ""),
                # 前端读作 a.provider；本模型字段是 vendor，做一次映射
                "provider": a.get("provider") or a.get("vendor") or "",
                "enabled": bool(a.get("enabled", False)),
            })
    except Exception as exc:  # 注册表不可用时降级为无 agent 节点
        logger.warning("[plugin_service] topology: agent list unavailable: %s", exc)

    return {
        "servers": servers,
        "tools": tools,
        "agents": agents,
        "edges": edges,
    }


def remove_server(server_name: str) -> bool:
    """移除 MCP server 配置。返回是否移除成功。"""
    hub = _get_hub()
    return hub.remove_server(server_name)


def list_tools() -> dict[str, Any]:
    """列出所有 MCP 工具。返回 ``{"tools": ..., "count": int}``。

    2026-09-23：为每个工具补 ``id`` 与 ``concurrency_limit``。

    ``MCPTool`` 模型只有 name/description/input_schema/server_name ——
    **既无 id 也无并发上限字段**。而前端 McpManager 需要：
    - ``id``：``v-for :key`` 与 ``PUT /api/mcp/tools/{id}`` 的定位依据
    - ``concurrency_limit``：并发上限输入框的初值（0 = 不限）

    故在此合成 id（``{server_name}:{name}``，与 hub 的 limit 主键同构），
    并从 ``mcp_tool_limits`` 表读取上限。若不补，前端拿到的 ``tool.id`` 为
    undefined，PUT 根本打不出去。
    """
    hub = _get_hub()
    tools = hub.all_tools()
    try:
        limits = hub.list_tool_concurrency_limits()
    except Exception:  # 旧库/替身 hub 无此方法时降级为"全部不限"
        limits = {}

    out: list[dict[str, Any]] = []
    for t in tools:
        d = t.model_dump() if hasattr(t, "model_dump") else dict(t)
        key = f"{d.get('server_name', '')}:{d.get('name', '')}"
        d["id"] = key
        d["concurrency_limit"] = limits.get(key, 0)
        out.append(d)
    return {"tools": out, "count": len(out)}


def update_tool_concurrency(tool_id: str, concurrency_limit: int) -> dict[str, Any] | None:
    """设置工具的并发上限（0 = 不限）。返回 ``None`` 表示该工具不存在。

    2026-09-23 新增：前端 McpManager 的「并发限制」输入框调用
    ``PUT /api/mcp/tools/{id}``，此前后端无实现。

    ``tool_id`` 即 ``{server_name}:{tool_name}`` —— 直接作为 ``mcp_tool_limits``
    的主键，无需拆分（拆分会在工具名含 ``:`` 时出错）。
    """
    hub = _get_hub()
    # 校验工具存在，避免为拼错的 id 建出无意义的限流记录
    known = {
        f"{t.get('server_name', '')}:{t.get('name', '')}"
        for t in list_tools()["tools"]
    }
    if tool_id not in known:
        return None
    limit = hub.set_tool_concurrency_limit(tool_id, concurrency_limit)
    return {"status": "ok", "tool": tool_id, "concurrency_limit": limit}


def mcp_call_stats(hours: int = 24) -> dict[str, Any]:
    """MCP 调用统计（供 ``GET /api/mcp/stats``）。

    2026-09-23 新增：前端 McpManager 的统计面板调用本端点，此前后端无实现。
    数据来自 ``MCPHub._call_stats``（按小时分桶的内存计数器）。
    """
    hub = _get_hub()
    return hub.call_stats(hours=hours)


async def call_tool(
    tool: str,
    arguments: dict[str, Any],
    *,
    user_context: dict[str, Any] | None = None,
) -> Any:
    """调用 MCP 工具。返回工具结果（model_dump 后的 dict 或原始值）。

    Parameters
    ----------
    tool : str
        工具名称。
    arguments : dict
        工具参数。
    user_context : dict | None
        调用方身份信息 ``{"user_id": ..., "roles": [...]}``，供 δ-3
        权限校验器按 allowed_users / allowed_roles 维度鉴权。
    """
    hub = _get_hub()
    result = await hub.call_tool_by_name(
        tool, arguments, user_context=user_context,
    )
    return result.model_dump() if hasattr(result, "model_dump") else result


async def health_check() -> dict[str, Any]:
    """对所有 MCP server 执行健康检查。返回 ``{"health": ...}``。"""
    hub = _get_hub()
    health = await hub.health_check_all()
    return {"health": health}


def marketplace_tools() -> dict[str, Any]:
    """列出 Marketplace 可安装工具.

    聚合所有已启用 registry 的 catalog, 并标注每个工具是否已安装
    (查 ``mcp_installed.yaml``). 网络不可达或 registry 配置缺失时
    返回空列表而非抛异常, 调用方降级为 EmptyState.
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


def marketplace_install(tool_id: str) -> dict[str, Any]:
    """安装 Marketplace 工具.

    完整安装流程:
      1. ``MCPMarketplace.install`` — 在已启用 registry 中查找工具,
         下载并校验 SHA-256 (若提供), 写入 ``mcp_installed.yaml``
      2. ``MCPHub.add_server`` — 将安装后的 ``MCPServerConfig`` 注册
         到本地 hub (状态 DISCONNECTED, 不自动连接)
      3. 返回安装结果 (server name / transport / 已注册标志)

    Raises
    ------
    ValueError
        工具未找到 (消息含 "not found") 或安装失败 (下载/checksum/未受信 registry)。
        由 router 转换为 404 / 400。

    安全约束:
      - 未受信 registry + 无 checksum + 未显式 opt-in → 拒绝安装
        (``MCPMarketplace`` 内部抛 ``ValueError``)
      - 网络下载失败 / checksum 校验失败 → ValueError
      - 工具未找到 → ValueError (消息含 "not found")
    """
    mp = _get_marketplace()
    # 1. marketplace 安装 (下载 + 校验 + 写 mcp_installed.yaml)
    config = mp.install(tool_id)
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
        "server": config.name,
        "transport": config.transport.value if hasattr(config.transport, "value") else str(config.transport),
        "registered": registered,
    }