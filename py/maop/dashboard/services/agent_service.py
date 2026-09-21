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
"""

from __future__ import annotations

import logging
import threading
from typing import Any

logger = logging.getLogger(__name__)


# ══════════════════════════════════════════════════════════════════
# §1  Agent Catalog — 注册中心管理
# ══════════════════════════════════════════════════════════════════

_catalog: Any = None
_catalog_lock = threading.Lock()


def _get_catalog() -> Any:
    """惰性初始化全局 AgentCatalog 单例。"""
    global _catalog
    if _catalog is None:
        with _catalog_lock:
            if _catalog is None:
                from maop.core.agent.registry.agent_catalog import AgentCatalog
                _catalog = AgentCatalog.default()
    return _catalog


def _set_catalog(catalog: Any) -> None:
    """供测试注入自定义 catalog（隔离 DB）。"""
    global _catalog
    with _catalog_lock:
        _catalog = catalog


def list_agents(enabled: bool | None) -> dict[str, Any]:
    """列出所有已注册 Agent，可选按 enabled 过滤。

    Parameters
    ----------
    enabled : bool | None
        True 仅返回启用；False 仅返回禁用；None 返回全部。

    Returns
    -------
    dict
        ``{"agents": [...], "count": int}`` — agents 为 model_dump 后的 dict 列表。
    """
    catalog = _get_catalog()
    if enabled is True:
        agents = catalog.list_enabled()
    elif enabled is False:
        agents = [a for a in catalog.list_all() if not a.enabled]
    else:
        agents = catalog.list_all()
    return {
        "agents": [a.model_dump(mode="json") for a in agents],
        "count": len(agents),
    }


def get_agent(name: str) -> dict[str, Any] | None:
    """获取单个 Agent 详情。返回 model_dump 后的 dict 或 None（不存在）。"""
    catalog = _get_catalog()
    desc = catalog.get(name)
    if desc is None:
        return None
    return desc.model_dump(mode="json")


def register_agent(
    *,
    name: str,
    display_name: str,
    vendor: str,
    version: str,
    adapter_type: str,
    capabilities: list[str],
    billing_model: str,
    auth_method: str,
    max_concurrent: int,
    rate_limit_per_min: int,
    timeout_s: float,
    enabled: bool,
) -> dict[str, Any]:
    """注册新 Agent（或更新同名 Agent）。

    将字符串能力/计费/授权转换为枚举，构建 AgentDescriptor 并注册。
    返回注册后的 descriptor model_dump。

    Raises
    ------
    ValueError
        当 capability / billing_model / auth_method 字符串无法映射到枚举。
    """
    from maop.core.agent.registry.agent_catalog import (
        AgentCapability,
        AgentDescriptor,
        AuthMethod,
        BillingModel,
    )

    # 转换枚举 — 无法映射时抛 ValueError，由 router 转为 HTTPException(400)
    caps = [AgentCapability(c) for c in capabilities]
    bm = BillingModel(billing_model)
    am = AuthMethod(auth_method)

    desc = AgentDescriptor(
        name=name,
        display_name=display_name,
        vendor=vendor,
        version=version,
        adapter_type=adapter_type,
        capabilities=caps,
        billing_model=bm,
        auth_method=am,
        max_concurrent=max_concurrent,
        rate_limit_per_min=rate_limit_per_min,
        timeout_s=timeout_s,
        enabled=enabled,
    )
    catalog = _get_catalog()
    catalog.register(desc)
    logger.info("[agent_catalog_service] registered agent: %s", name)
    return desc.model_dump(mode="json")


def delete_agent(name: str) -> bool:
    """删除（注销）一个 Agent。返回是否删除成功。"""
    catalog = _get_catalog()
    return bool(catalog.delete(name))


def update_agent_health(name: str, healthy: bool) -> bool:
    """更新指定 Agent 的健康状态。返回 Agent 是否存在。"""
    catalog = _get_catalog()
    desc = catalog.get(name)
    if desc is None:
        return False
    catalog.update_health(name, healthy)
    return True


def search_agents(capability: list[str]) -> dict[str, Any]:
    """按能力搜索 Agent，返回具备所有指定能力的启用 Agent。

    Parameters
    ----------
    capability : list[str]
        能力过滤列表（可重复）。空列表则返回所有启用 Agent。

    Returns
    -------
    dict
        ``{"agents": [...], "count": int}``。

    Raises
    ------
    ValueError
        当 capability 字符串无法映射到 AgentCapability 枚举。
    """
    from maop.core.agent.registry.agent_catalog import AgentCapability

    catalog = _get_catalog()
    if not capability:
        agents = catalog.list_enabled()
    else:
        caps = [AgentCapability(c) for c in capability]
        # 逐个能力搜索取交集（search 已过滤 enabled=True）
        result_sets = [{a.name for a in catalog.search(c)} for c in caps]
        common = set.intersection(*result_sets) if result_sets else set()
        all_agents = {a.name: a for a in catalog.list_enabled()}
        agents = [all_agents[n] for n in sorted(common)]
    return {
        "agents": [a.model_dump(mode="json") for a in agents],
        "count": len(agents),
    }


def catalog_stats() -> dict[str, Any]:
    """返回 Agent Catalog 统计信息。"""
    catalog = _get_catalog()
    return {
        "count": catalog.count(),
        "healthy_count": catalog.count_healthy(),
    }


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
# §3  Agent Proxy (Bridge) — 适配器代理
# ══════════════════════════════════════════════════════════════════

_agent_proxy: Any = None
_agent_proxy_lock = threading.Lock()


def _get_bridge(maop_root: Any = None) -> Any:
    """惰性初始化全局 AgentProxy 单例。

    Parameters
    ----------
    maop_root : Any
        MAOP 根路径（str 或 Path）。首次初始化时使用；后续调用忽略。
    """
    global _agent_proxy
    if _agent_proxy is None:
        with _agent_proxy_lock:
            if _agent_proxy is None:
                from maop.core.agent.delegation.agent_proxy import AgentProxy
                _agent_proxy = AgentProxy(root_dir=str(maop_root))
    return _agent_proxy


def _set_bridge(bridge: Any) -> None:
    """供测试注入自定义 bridge（隔离）。"""
    global _agent_proxy
    with _agent_proxy_lock:
        _agent_proxy = bridge


def bridge_adapters() -> dict[str, Any]:
    """列出所有 bridge 适配器及其状态。"""
    bridge = _get_bridge()
    names = bridge.list_adapters()
    statuses = []
    for name in names:
        try:
            s = bridge.get_status(name)
            statuses.append(s.model_dump())
        except Exception:
            statuses.append({"name": name, "error": "status unavailable"})
    return {"adapters": statuses, "count": len(statuses)}


def bridge_call(adapter_name: str, task: str, kwargs: dict[str, Any]) -> Any:
    """通过 bridge 适配器代理调用。

    Raises
    ------
    KeyError
        适配器不存在。
    RuntimeError
        调用执行失败。
    """
    bridge = _get_bridge()
    return bridge.call(adapter_name, task, **kwargs)


def bridge_health() -> dict[str, Any]:
    """检查所有 bridge 适配器健康状态。"""
    bridge = _get_bridge()
    return bridge.health_check_all()


def bridge_sync_config(adapter_name: str, config: dict[str, Any]) -> None:
    """同步 bridge 适配器配置。

    Raises
    ------
    KeyError
        适配器不存在。
    """
    bridge = _get_bridge()
    bridge.sync_config(adapter_name, config)


# ══════════════════════════════════════════════════════════════════
# §4  Agent Proxy Gateway — 规则/审计/预算
# ══════════════════════════════════════════════════════════════════

_gateway: Any = None
_gateway_lock = threading.Lock()


def _get_gateway() -> Any:
    """惰性初始化全局 AgentProxyGateway 单例。"""
    global _gateway
    if _gateway is None:
        with _gateway_lock:
            if _gateway is None:
                from maop.core.agent.auth.agent_proxy_gateway import AgentProxyGateway
                _gateway = AgentProxyGateway()
    return _gateway


def _set_gateway(gateway: Any) -> None:
    """供测试注入自定义 gateway（隔离 DB）。"""
    global _gateway
    with _gateway_lock:
        _gateway = gateway


def add_proxy_rule(
    *,
    internal_path: str,
    external_agent: str,
    allowed_departments: list[str],
    enabled: bool,
) -> str:
    """添加代理规则，返回 rule_id。"""
    from maop.core.agent.auth.agent_proxy_gateway import ProxyRule

    rule = ProxyRule(
        internal_path=internal_path,
        external_agent=external_agent,
        allowed_departments=allowed_departments,
        enabled=enabled,
    )
    gateway = _get_gateway()
    gateway.add_proxy_rule(rule)
    logger.info(
        "[agent_proxy_service] 添加规则 id=%s path=%s",
        rule.rule_id, rule.internal_path,
    )
    return rule.rule_id


def list_proxy_rules() -> dict[str, Any]:
    """列出全部代理规则。"""
    gateway = _get_gateway()
    rules = gateway.list_proxy_rules()
    return {
        "rules": [r.model_dump(mode="json") for r in rules],
        "count": len(rules),
    }


def remove_proxy_rule(rule_id: str) -> None:
    """移除代理规则。"""
    gateway = _get_gateway()
    gateway.remove_proxy_rule(rule_id)


def query_audit(*, user: str, agent: str, limit: int) -> dict[str, Any]:
    """查询审计日志（按时间倒序）。"""
    gateway = _get_gateway()
    events = gateway.query_audit(user=user, agent=agent, limit=limit)
    return {
        "events": [e.model_dump(mode="json") for e in events],
        "count": len(events),
    }


def set_department_budget(
    *,
    department: str,
    agent: str,
    monthly_budget: float,
    used: float,
    reset_day: int,
) -> dict[str, str]:
    """设置部门预算，返回 ``{"department": ..., "agent": ...}``。"""
    from maop.core.agent.auth.agent_proxy_gateway import DepartmentBudget

    budget = DepartmentBudget(
        department=department,
        agent=agent,
        monthly_budget=monthly_budget,
        used=used,
        reset_day=reset_day,
    )
    gateway = _get_gateway()
    gateway.set_department_budget(budget)
    logger.info(
        "[agent_proxy_service] 设置预算 dept=%s agent=%s monthly=%.2f",
        department, agent, monthly_budget,
    )
    return {"department": department, "agent": agent}


def get_budget_summary(department: str) -> dict[str, Any]:
    """查询预算汇总。"""
    gateway = _get_gateway()
    budgets = gateway.get_budget_summary(department=department)
    return {
        "budgets": [b.model_dump(mode="json") for b in budgets],
        "count": len(budgets),
    }


def check_budget(department: str, agent: str, cost: float) -> bool:
    """检查部门预算是否足够扣减。"""
    gateway = _get_gateway()
    return bool(gateway.check_budget(department, agent, cost))


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