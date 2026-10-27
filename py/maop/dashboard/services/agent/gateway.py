"""Agent Proxy Gateway 子域 — 适配器代理 + 规则/审计/预算.

从 ``agent_service`` 拆分（file-size ratchet）。包含：

- §3 Agent Proxy (Bridge)：``bridge_adapters`` / ``bridge_call`` /
  ``bridge_health`` / ``bridge_sync_config`` 等
- §4 Agent Proxy Gateway：``add_proxy_rule`` / ``list_proxy_rules`` /
  ``query_audit`` / ``set_department_budget`` / ``get_budget_summary`` /
  ``check_budget`` 等

``agent_service`` forwards every public name below, so callers and
tests keep importing from the parent module.
"""

from __future__ import annotations

import logging
import threading
from typing import Any

logger = logging.getLogger(__name__)


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


def _register_adapter_from_config(bridge: Any, adapter_name: str) -> bool:
    """把 agents.yaml 里配置的 agent 按需构建成 bridge 适配器并注册。

    为什么需要它（2026-10-05）：`AgentProxy` 的注册表**从来没有人往里放过东西** ——
    `register()` 在全仓只有定义处和测试调用。于是 `/api/bridge/adapters` 永远返回空表、
    `/api/bridge/call` 对任何名字都回 404（那个 404 是"适配器未找到"的正常语义，不是
    端点坏了；坏的是**从来没人注册**）。路由、鉴权、请求模型、错误脱敏都写好了，
    缺的只是这一步。

    刻意做成**按需**（调用时才建）而不是启动时批量注册：agents.yaml 有 30+ 条配置，
    启动即构造全部适配器既慢又可能踩到重量级适配器的构造副作用。

    Returns
    -------
    bool
        是否成功注册（配置里没有这个 agent 或构建失败时为 False）。
    """
    from maop.config.loader import ConfigLoader
    from maop.core.agent.adapters.adapter_factory import build_adapter
    from maop.delegate.agent_resolver import AgentResolver

    try:
        config = ConfigLoader(project_root=getattr(bridge, "_root", None)).load()
        agent_cfg = AgentResolver(config).resolve(adapter_name)
        if agent_cfg is None:
            return False
        bridge.register(adapter_name, build_adapter(agent_cfg))
        return True
    except Exception as exc:
        logger.warning("[bridge] 按需注册适配器 %s 失败：%s", adapter_name, exc)
        return False


def bridge_call(adapter_name: str, task: str, kwargs: dict[str, Any]) -> Any:
    """通过 bridge 适配器代理调用。

    若该名字还没注册但 agents.yaml 里有同名 agent，则先按需构建并注册 —— 否则这个
    端点对开箱配置也只会回 404（注册表本来是空的）。

    Raises
    ------
    KeyError
        适配器不存在（既没注册、配置里也没有）。
    RuntimeError
        调用执行失败。
    """
    bridge = _get_bridge()
    if bridge.get(adapter_name) is None:
        _register_adapter_from_config(bridge, adapter_name)
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


__all__ = [
    "_get_bridge",
    "_get_gateway",
    "_register_adapter_from_config",
    "_set_bridge",
    "_set_gateway",
    "add_proxy_rule",
    "bridge_adapters",
    "bridge_call",
    "bridge_health",
    "bridge_sync_config",
    "check_budget",
    "get_budget_summary",
    "list_proxy_rules",
    "query_audit",
    "remove_proxy_rule",
    "set_department_budget",
]
