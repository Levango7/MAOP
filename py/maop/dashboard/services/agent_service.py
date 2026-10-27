"""Agent Management 域 Service 层 — 主模块.

框架无关（不导入 FastAPI），函数接收显式参数，返回普通 dict/object，
不抛 HTTPException。可独立单元测试，无需 HTTP 上下文。

本模块从以下 6 个 router 提取业务逻辑（agent_versions 单独在
``agent_versions_service.py`` 中）：

- ``agent_catalog``        — Agent Catalog 注册中心管理
- ``agent_router``         — Agent Router 路由选择与并发槽位
- ``agent_proxy``          — Agent Bridge 适配器代理
- ``agent_proxy_gateway``  — Agent Proxy Gateway 规则/审计/预算
- ``agent_collaboration``  — Agent Collaboration 协作编排
- ``subagent``             — SubAgent 生命周期管理

每个子域对应一组 ``_get_xxx`` 单例访问器 + 业务函数。单例使用
双重检查锁定保护，与原 router 风格一致；``_set_xxx`` 供测试注入。

子模块拆分（file-size ratchet）：

- ``services/agent/catalog.py`` — §1 Agent Catalog（注册中心管理）
- ``services/agent/gateway.py`` — §3 Bridge + §4 Gateway（代理 + 规则/审计/预算）

两个子模块的 public 名称由本模块转发 import，``__all__`` 不变，
调用方和测试无需改动。
"""

from __future__ import annotations

import logging
import threading
from typing import Any

# ── 转发 import（子模块拆分，__all__ 不变）──────────────────────
# 公共函数由 __all__ 引用（ruff 不报 unused）；
# 内部 accessor 由 router/test 通过 agent_service._get_xxx() 访问，需 noqa F401 抑制。
from maop.dashboard.services.agent.catalog import (  # noqa: F401
    _get_catalog,
    _set_catalog,
    catalog_stats,
    delete_agent,
    get_agent,
    list_agents,
    register_agent,
    search_agents,
    update_agent,
    update_agent_health,
)
from maop.dashboard.services.agent.gateway import (  # noqa: F401
    _get_bridge,
    _get_gateway,
    _register_adapter_from_config,
    _set_bridge,
    _set_gateway,
    add_proxy_rule,
    bridge_adapters,
    bridge_call,
    bridge_health,
    bridge_sync_config,
    check_budget,
    get_budget_summary,
    list_proxy_rules,
    query_audit,
    remove_proxy_rule,
    set_department_budget,
)

logger = logging.getLogger(__name__)


# PEP 562: 代理子模块的模块级变量（单例 + lock），保持向后兼容。
# 子模块拆分后 _agent_proxy / _catalog 等变量定义在子模块中；
# 通过 `__getattr__` 让 agent_service._agent_proxy 等直接访问继续工作。
# 不加缓存：_set_xxx() 会重新赋值子模块变量，缓存会返回旧值。
def __getattr__(name: str) -> Any:
    for mod_name in ("catalog", "gateway"):
        try:
            mod = __import__(
                f"maop.dashboard.services.agent.{mod_name}",
                fromlist=[name],
            )
        except ImportError:  # silent-ok: scan-continue — 子模块可能不存在或不含该属性，继续尝试下一个
            continue
        if hasattr(mod, name):
            return getattr(mod, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


# ══════════════════════════════════════════════════════════════════
# §2  Agent Router — 路由选择与并发槽位
# ══════════════════════════════════════════════════════════════════

_router: Any = None
_router_lock = threading.Lock()


def _get_router() -> Any:
    """惰性初始化全局 AgentRouter 单例。"""
    global _router
    if _router is None:
        with _router_lock:
            if _router is None:
                from maop.core.agent.router.agent_router import AgentRouter
                _router = AgentRouter()
    return _router


def _set_router(router: Any) -> None:
    """供测试注入自定义 router（隔离 DB）。"""
    global _router
    with _router_lock:
        _router = router


def agent_route(
    *,
    required_capabilities: list[str],
    preferred_agent: str,
    excluded_agents: list[str],
    max_cost_tier: str,
    require_healthy: bool,
    session_id: str,
    strategy: str,
) -> dict[str, Any]:
    """执行路由选择，返回主 Agent + 降级链 + 候选。

    Raises
    ------
    ValueError
        当 required_capabilities 或 strategy 字符串无法映射到枚举。
    """
    from maop.core.agent.registry.agent_catalog import AgentCapability
    from maop.core.agent.router.agent_router import (
        RoutingContext,
        RoutingStrategy,
    )

    caps = [AgentCapability(c) for c in required_capabilities]
    strategy_enum = RoutingStrategy(strategy)

    ctx = RoutingContext(
        required_capabilities=caps,
        preferred_agent=preferred_agent,
        excluded_agents=excluded_agents,
        max_cost_tier=max_cost_tier,
        require_healthy=require_healthy,
        session_id=session_id,
    )
    agent_router = _get_router()
    result = agent_router.route(ctx, strategy=strategy_enum)
    return {
        "primary": result.primary.model_dump(mode="json") if result.primary else None,
        "fallbacks": [a.model_dump(mode="json") for a in result.fallbacks],
        "alternatives": [a.model_dump(mode="json") for a in result.alternatives],
        "reason": result.reason,
    }


def list_strategies() -> dict[str, Any]:
    """列出所有可用路由策略。"""
    from maop.core.agent.router.agent_router import RoutingStrategy

    strategies = [{"value": s.value, "name": s.name} for s in RoutingStrategy]
    return {"strategies": strategies, "count": len(strategies)}


def acquire_slot(agent_name: str) -> dict[str, Any] | None:
    """占用一个 Agent 并发槽位。

    Returns
    -------
    dict | None
        成功返回 ``{"agent_name": str, "active_count": int}``；失败（槽位已满）返回 None。
    """
    agent_router = _get_router()
    ok = agent_router.acquire(agent_name)
    if not ok:
        return None
    return {"agent_name": agent_name, "active_count": agent_router.get_active_count(agent_name)}


def release_slot(agent_name: str) -> dict[str, Any] | None:
    """释放一个 Agent 并发槽位。

    Returns
    -------
    dict | None
        成功返回 ``{"agent_name": str, "active_count": int}``；Agent 不存在返回 None。
    """
    agent_router = _get_router()
    # 检查 agent 是否存在
    if agent_router.catalog.get(agent_name) is None:
        return None
    agent_router.release(agent_name)
    return {"agent_name": agent_name, "active_count": agent_router.get_active_count(agent_name)}


def active_counts() -> dict[str, Any]:
    """获取所有 Agent 的当前活跃并发数。"""
    agent_router = _get_router()
    catalog = agent_router.catalog
    all_agents = catalog.list_all()
    active: dict[str, int] = {}
    for a in all_agents:
        active[a.name] = agent_router.get_active_count(a.name)
    return {"active": active, "count": len(active)}


# ══════════════════════════════════════════════════════════════════
# §5  Agent Collaboration — 协作编排
# ══════════════════════════════════════════════════════════════════

_collaboration: Any = None
_collab_lock = threading.Lock()


def _get_collaboration() -> Any:
    """惰性初始化全局 AgentCollaboration 单例.

    复用全局 AgentCatalog 与 AgentRouter 单例。计费引擎暂不注入
    （None），避免引入额外 DB 初始化开销；如需计费，调用方可
    通过 ``_set_collaboration`` 注入自定义实例。
    """
    global _collaboration
    if _collaboration is None:
        with _collab_lock:
            if _collaboration is None:
                from maop.core.agent.registry.agent_catalog import AgentCatalog
                from maop.core.agent.router.agent_collaboration import (
                    AgentCollaboration,
                )
                from maop.core.agent.router.agent_router import AgentRouter

                catalog = AgentCatalog.default()
                agent_router = AgentRouter(catalog=catalog)
                _collaboration = AgentCollaboration(
                    catalog=catalog, router=agent_router,
                )
    return _collaboration


def _set_collaboration(collab: Any) -> None:
    """供测试注入自定义 AgentCollaboration 实例（隔离 DB）。"""
    global _collaboration
    with _collab_lock:
        _collaboration = collab


def get_pattern_by_name(name: str) -> Any:
    """按名称获取预定义协作模式.

    Parameters
    ----------
    name : str
        模式名：init_and_implement / review_and_fix / parallel_and_merge。

    Returns
    -------
    CollaborationPattern

    Raises
    ------
    KeyError
        当模式名不存在（router 负责转为 HTTPException 404）。
    """
    from maop.core.agent.router.agent_collaboration import (
        pattern_init_and_implement,
        pattern_parallel_and_merge,
        pattern_review_and_fix,
    )

    patterns = {
        "init_and_implement": pattern_init_and_implement,
        "review_and_fix": pattern_review_and_fix,
        "parallel_and_merge": pattern_parallel_and_merge,
    }
    factory = patterns.get(name)
    if factory is None:
        raise KeyError(f"Unknown pattern: {name}. Available: {sorted(patterns.keys())}")
    return factory()


def execute_pattern(pattern_name: str, task: str, context: dict[str, Any]) -> dict[str, Any]:
    """执行预定义协作模式。

    Raises
    ------
    KeyError
        模式名不存在。
    """
    pattern = get_pattern_by_name(pattern_name)
    collab = _get_collaboration()
    result = collab.execute_pattern(pattern, task=task, context=context)
    return {
        "success": result.success,
        "final_result": result.final_result,
        "step_results": [r.model_dump(mode="json") for r in result.step_results],
        "total_duration_s": result.total_duration_s,
        "pattern_name": pattern_name,
    }


def execute_custom(steps: list[dict[str, Any]], context: dict[str, Any]) -> dict[str, Any]:
    """执行自定义步骤编排。

    Parameters
    ----------
    steps : list[dict]
        步骤字典列表，每个含 name / agent_name / task_template /
        depends_on / capabilities_required / optional 字段。
    context : dict
        初始上下文。
    """
    from maop.core.agent.router.agent_collaboration import CollaborationStep

    # 转换请求步骤为 CollaborationStep
    collab_steps = [
        CollaborationStep(
            name=s["name"],
            agent_name=s["agent_name"],
            task_template=s["task_template"],
            depends_on=list(s["depends_on"]),
            capabilities_required=list(s["capabilities_required"]),
            optional=s["optional"],
        )
        for s in steps
    ]
    collab = _get_collaboration()
    result = collab.execute_custom(collab_steps, context=context)
    return {
        "success": result.success,
        "final_result": result.final_result,
        "step_results": [r.model_dump(mode="json") for r in result.step_results],
        "total_duration_s": result.total_duration_s,
    }


def list_patterns() -> dict[str, Any]:
    """列出所有预定义协作模式。"""
    from maop.core.agent.router.agent_collaboration import (
        list_predefined_patterns,
    )

    patterns = list_predefined_patterns()
    return {
        "patterns": [
            {
                "name": p.name,
                "description": p.description,
                "steps": [s.model_dump(mode="json") for s in p.steps],
                "step_count": len(p.steps),
            }
            for p in patterns
        ],
        "count": len(patterns),
    }


# ══════════════════════════════════════════════════════════════════
# §6  SubAgent — 生命周期管理
# ══════════════════════════════════════════════════════════════════

_subagent_mgr: Any = None
_subagent_mgr_lock = threading.Lock()


def _get_subagent_mgr(maop_root: Any = None) -> Any:
    """惰性初始化全局 SubAgentManager 单例。

    Parameters
    ----------
    maop_root : Any
        MAOP 根路径（str 或 Path）。首次初始化时使用；后续调用忽略。
    """
    global _subagent_mgr
    if _subagent_mgr is None:
        with _subagent_mgr_lock:
            if _subagent_mgr is None:
                from maop.core.agent.delegation.subagent_lifecycle import SubAgentManager
                _subagent_mgr = SubAgentManager(root_dir=str(maop_root))
    return _subagent_mgr


def _set_subagent_mgr(mgr: Any) -> None:
    """供测试注入自定义 SubAgentManager（隔离）。"""
    global _subagent_mgr
    with _subagent_mgr_lock:
        _subagent_mgr = mgr


async def subagent_spawn(
    mgr: Any, *, agent_name: str, task: str, context: str, model: str
) -> str:
    """生成子代理，返回 agent_id。

    构建 AgentConfig（F4a fix: SubAgentManager.spawn 需要 AgentConfig 而非 dict）。

    Parameters
    ----------
    mgr : SubAgentManager
        由 caller（router）通过 ``_get_subagent_mgr()`` 获取并传入，
        便于测试通过 monkeypatch 注入 mock manager。
    """
    from maop.core.agent.delegation.subagent_lifecycle import AgentConfig

    config = AgentConfig(name=agent_name, model=model)
    agent_id = await mgr.spawn(config=config, task=task, context=context)
    return agent_id


async def subagent_wait(mgr: Any, agent_id: str, timeout: int) -> Any:
    """等待子代理完成，返回结果（或 None 表示未找到/超时）。

    Parameters
    ----------
    mgr : SubAgentManager
        由 caller（router）传入。
    """
    result = await mgr.wait(agent_id, timeout=timeout)
    return result


def subagent_cancel(mgr: Any, agent_id: str) -> bool:
    """取消子代理，返回是否成功。

    注意：SubAgentManager.cancel 是同步方法（F4a fix）。

    Parameters
    ----------
    mgr : SubAgentManager
        由 caller（router）传入。
    """
    return bool(mgr.cancel(agent_id))


def subagent_list(mgr: Any) -> dict[str, Any]:
    """列出所有子代理。

    Parameters
    ----------
    mgr : SubAgentManager
        由 caller（router）传入。
    """
    agents = mgr.list_agents()
    return {"agents": agents, "count": len(agents)}


def subagent_transcript(mgr: Any, agent_id: str) -> Any:
    """获取子代理实时转录。

    Parameters
    ----------
    mgr : SubAgentManager
        由 caller（router）传入。
    """
    return mgr.get_live_transcript(agent_id)


__all__ = [
    "_get_collaboration",
    "_get_router",
    "_get_subagent_mgr",
    "_set_collaboration",
    "_set_router",
    "_set_subagent_mgr",
    "acquire_slot",
    "active_counts",
    "add_proxy_rule",
    "agent_route",
    "bridge_adapters",
    "bridge_call",
    "bridge_health",
    "bridge_sync_config",
    "catalog_stats",
    "check_budget",
    "delete_agent",
    "execute_custom",
    "execute_pattern",
    "get_agent",
    "get_budget_summary",
    "get_pattern_by_name",
    "list_agents",
    "list_patterns",
    "list_proxy_rules",
    "list_strategies",
    "query_audit",
    "register_agent",
    "release_slot",
    "remove_proxy_rule",
    "search_agents",
    "set_department_budget",
    "subagent_cancel",
    "subagent_list",
    "subagent_spawn",
    "subagent_transcript",
    "subagent_wait",
    "update_agent",
    "update_agent_health",
]
