"""Agent Catalog 子域 — 注册中心管理.

从 ``agent_service`` 拆分（file-size ratchet）。包含 Agent 的
list / get / register / update / delete / health / search / stats
全套 CRUD，以及全局 ``AgentCatalog`` 单例的惰性初始化。

``agent_service`` forwards every public name below, so callers and
tests keep importing from the parent module.
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
    retry_count: int = 3,
    fallback_agents: list[str] | None = None,
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
        retry_count=retry_count,
        fallback_agents=fallback_agents or [],
        enabled=enabled,
    )
    catalog = _get_catalog()
    catalog.register(desc)
    logger.info("[agent_catalog_service] registered agent: %s", name)
    return desc.model_dump(mode="json")


def update_agent(name: str, changes: dict[str, Any]) -> dict[str, Any] | None:
    """部分更新已注册 Agent；返回更新后的 descriptor，Agent 不存在时返回 None。

    未出现在 ``changes`` 中的字段保留原值。字段校验由 ``AgentDescriptor``
    完成（例如降级链自引用会抛 ``ValueError``）。
    """
    from maop.core.agent.registry.agent_catalog import AgentDescriptor

    catalog = _get_catalog()
    existing = catalog.get(name)
    if existing is None:
        return None

    merged = existing.model_dump(mode="json")
    merged.update(changes)
    merged["name"] = name
    desc = AgentDescriptor(**merged)
    catalog.register(desc)
    logger.info("[agent_catalog_service] updated agent: %s", name)
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
    """返回 Agent Catalog 统计信息。

    四个计数取自同一次 ``list_all()`` 快照，避免多次加锁期间数据变动导致
    ``count`` 与 ``healthy_count`` 互相矛盾。
    """
    catalog = _get_catalog()
    agents = catalog.list_all()
    return {
        "count": len(agents),
        "healthy_count": sum(1 for a in agents if a.healthy),
        "enabled_count": sum(1 for a in agents if a.enabled),
        "capability_count": len({c.value for a in agents for c in a.capabilities}),
    }


__all__ = [
    "_get_catalog",
    "_set_catalog",
    "catalog_stats",
    "delete_agent",
    "get_agent",
    "list_agents",
    "register_agent",
    "search_agents",
    "update_agent",
    "update_agent_health",
]
