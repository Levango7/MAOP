"""Models & Model Gateway 域 Service 层.

框架无关（不导入 FastAPI），函数接收显式参数，返回普通 dict/object，
不抛 HTTPException。可独立单元测试，无需 HTTP 上下文。

本模块从以下两个 router 提取业务逻辑：

- ``model.py``         — 模型管理（registry / list / switch / budget /
  policies / provider CRUD / model CRUD / API key vault / health check）
- ``model_gateway.py`` — 模型授权网关（权限规则 / 访问检查 / 使用量 /
  配置更新）

单例（ModelRegistry / ApiKeyVault / ModelGateway）使用双重检查锁定
保护，与原 router 风格一致；``_set_xxx`` 供测试注入。共享运行时
状态（``MAOP_ROOT``）通过 ``maop.dashboard.routers.state`` 访问，
与 ``system_service.py`` 保持同一模式。
"""

from __future__ import annotations

import asyncio
import logging
import shutil
import threading
from pathlib import Path
from typing import Any

from maop.dashboard.routers import state

logger = logging.getLogger(__name__)


# ══════════════════════════════════════════════════════════════════
# §1  单例访问器 — ModelRegistry / ApiKeyVault / ModelGateway
# ══════════════════════════════════════════════════════════════════

_model_registry: Any = None
_model_registry_lock = threading.Lock()


def _get_model_registry() -> Any:
    """惰性初始化全局 ModelRegistry 单例。"""
    global _model_registry
    if _model_registry is None:
        with _model_registry_lock:
            if _model_registry is None:  # double-checked locking
                from maop.model.registry import ModelRegistry
                _model_registry = ModelRegistry(project_root=str(state.MAOP_ROOT))
    return _model_registry


def _set_model_registry(reg: Any) -> None:
    """供测试注入自定义 registry（隔离配置文件）。"""
    global _model_registry
    with _model_registry_lock:
        _model_registry = reg


_api_key_vault: Any = None
_api_key_vault_lock = threading.Lock()


def _get_api_key_vault() -> Any:
    """惰性初始化全局 ApiKeyVault 单例。"""
    global _api_key_vault
    if _api_key_vault is None:
        with _api_key_vault_lock:
            if _api_key_vault is None:  # double-checked locking
                from maop.core.security.api_key_vault import ApiKeyVault
                _api_key_vault = ApiKeyVault(root_dir=str(state.MAOP_ROOT))
    return _api_key_vault


def _set_api_key_vault(vault: Any) -> None:
    """供测试注入自定义 vault（隔离密钥存储）。"""
    global _api_key_vault
    with _api_key_vault_lock:
        _api_key_vault = vault


_model_gateway: Any = None
_model_gateway_lock = threading.Lock()


def _get_model_gateway() -> Any:
    """惰性初始化全局 ModelGateway 单例。"""
    global _model_gateway
    if _model_gateway is None:
        with _model_gateway_lock:
            if _model_gateway is None:  # double-checked locking
                from maop.core.agent.llm_chat.model_gateway import ModelGateway
                _model_gateway = ModelGateway()
    return _model_gateway


def _set_model_gateway(gw: Any) -> None:
    """供测试注入自定义 gateway（隔离权限/使用量状态）。"""
    global _model_gateway
    with _model_gateway_lock:
        _model_gateway = gw


# ══════════════════════════════════════════════════════════════════
# §2  Model agents / quota / switch
# ══════════════════════════════════════════════════════════════════

def list_model_agents() -> dict[str, Any]:
    """列出所有 agent 及其 CLI 可用性。

    Returns
    -------
    dict
        ``{"agents": [...], "count": int}``。
    """
    from maop.config.loader import ConfigLoader

    cfg = ConfigLoader(project_root=str(state.MAOP_ROOT)).load()
    agents: list[dict[str, Any]] = []
    for name, ad in cfg.agents.items():
        cli_path = shutil.which(ad.cli) if ad.cli else None
        agents.append({
            "name": name, "cli": ad.cli, "driver": ad.driver,
            "model": getattr(ad, "model", ""), "timeout_s": ad.timeout_s,
            "capabilities": ad.capabilities, "description": ad.description,
            "cli_available": cli_path is not None, "cli_path": cli_path or "",
        })
    return {"agents": agents, "count": len(agents)}


def get_model_quota() -> dict[str, Any]:
    """查询各 agent 的 CLI 配额信息（容错：配置加载失败返回空列表）。

    Returns
    -------
    dict
        ``{"agents": [...], "count": int}``。
    """
    agents_cfg: list[dict[str, Any]] = []
    try:
        from maop.config.loader import ConfigLoader

        cfg = ConfigLoader(project_root=str(state.MAOP_ROOT)).load()
        for name, ad in cfg.agents.items():
            cli_path = shutil.which(ad.cli) if ad.cli else None
            agents_cfg.append({
                "agent": name, "cli": ad.cli, "model": getattr(ad, "model", ""),
                "available": cli_path is not None, "cli_path": cli_path or "",
                "driver": ad.driver,
            })
    except Exception:
        logger.debug("Failed to load agent config", exc_info=True)
    return {"agents": agents_cfg, "count": len(agents_cfg)}


async def switch_model(agent_name: str, new_model: str) -> dict[str, Any]:
    """切换指定 agent 使用的模型并持久化到 agents.yaml。

    Parameters
    ----------
    agent_name : str
        目标 agent 名称（须存在于 agents.yaml）。
    new_model : str
        新模型名称（须存在于 models.yaml 的 models 列表，若文件存在）。

    Returns
    -------
    dict
        ``{"agent": str, "model": str}``。

    Raises
    ------
    FileNotFoundError
        agents.yaml 不存在。
    KeyError
        agent_name 不在 agents.yaml 中。
    ValueError
        new_model 不在 models.yaml 的有效模型集合中。
    """
    ypath = state.MAOP_ROOT / "agents.yaml"
    if not ypath.exists():
        ypath = state.MAOP_ROOT / "config" / "agents.yaml"
    if not ypath.exists():
        raise FileNotFoundError("agents.yaml not found")
    import yaml

    _text = await asyncio.to_thread(Path(ypath).read_text, encoding="utf-8")
    data = yaml.safe_load(_text)
    agents = data.get("agents", {})
    if agent_name not in agents:
        raise KeyError(f"Unknown agent: {agent_name}")
    mpath = state.MAOP_ROOT / "models.yaml"
    if not mpath.exists():
        mpath = state.MAOP_ROOT / "config" / "models.yaml"
    if mpath.exists():
        import yaml as _yaml

        _mtext = await asyncio.to_thread(Path(mpath).read_text, encoding="utf-8")
        mdata = _yaml.safe_load(_mtext)
        valid_models = set(mdata.get("models", {}).keys()) if isinstance(mdata, dict) else set()
        if valid_models and new_model not in valid_models:
            raise ValueError(f"Unknown model: {new_model}. Valid: {sorted(valid_models)}")

    agents[agent_name]["model"] = new_model
    _dumped = yaml.dump(data, allow_unicode=True, default_flow_style=False)
    await asyncio.to_thread(Path(ypath).write_text, _dumped, encoding="utf-8")
    return {"agent": agent_name, "model": new_model}


# ══════════════════════════════════════════════════════════════════
# §3  ModelRegistry 查询端点
# ══════════════════════════════════════════════════════════════════

def get_model_registry_stats() -> dict[str, Any]:
    """返回 ModelRegistry 统计信息。"""
    return {"stats": _get_model_registry().stats()}


def list_models() -> dict[str, Any]:
    """列出所有已注册模型（含 provider 健康状态）。

    Returns
    -------
    dict
        ``{"models": [...], "count": int}``。
    """
    reg = _get_model_registry()
    models: list[dict[str, Any]] = []
    for m in reg.list_models(enabled_only=False):
        models.append({
            "name": m.name, "provider": m.provider, "family": m.family,
            "context_window": m.context_window, "max_output": m.max_output,
            "cost_per_1k_input": m.cost_per_1k_input,
            "cost_per_1k_output": m.cost_per_1k_output,
            "capabilities": m.capabilities, "latency_tier": m.latency_tier.value,
            "quality_tier": m.quality_tier.value, "enabled": m.enabled,
            "provider_healthy": reg.providers.is_healthy(m.provider),
        })
    return {"models": models, "count": len(models)}


def list_model_providers() -> dict[str, Any]:
    """列出所有 provider。"""
    return {"providers": _get_model_registry().providers.list_providers()}


def select_model(capability: str, agent_model: str, policy: str) -> dict[str, Any]:
    """按策略选择有效模型。

    Returns
    -------
    dict
        ``{"effective_model": dict}`` — ModelSelector.select().model_dump()。
    """
    from maop.model.selector import ModelSelector

    em = ModelSelector(_get_model_registry()).select(
        capability=capability, agent_model=agent_model, policy_name=policy,
    )
    return {"effective_model": em.model_dump()}


def get_model_budget() -> dict[str, Any]:
    """返回预算守卫统计。"""
    from maop.model.budget import BudgetGuard

    reg = _get_model_registry()
    return {"budget": BudgetGuard(root_dir=str(state.MAOP_ROOT), config=reg.config.budget).stats()}


def get_model_quota_status() -> dict[str, Any]:
    """返回所有模型的配额使用情况。"""
    from maop.model.quota import QuotaEnforcer

    return {"quotas": QuotaEnforcer(_get_model_registry()).usage_all()}


def list_model_policies() -> dict[str, Any]:
    """列出所有模型选择策略。

    Returns
    -------
    dict
        ``{"policies": [...], "count": int}``。
    """
    reg = _get_model_registry()
    policies: list[dict[str, Any]] = []
    for name, p in reg.config.policies.items():
        policies.append({
            "name": name, "strategy": p.strategy.value,
            "max_cost_per_task": p.max_cost_per_task,
            "prefer_low_latency": p.prefer_low_latency,
            "fallback_on_error": p.fallback_on_error,
            "fallback_on_timeout": p.fallback_on_timeout,
        })
    return {"policies": policies, "count": len(policies)}


# ══════════════════════════════════════════════════════════════════
# §4  Provider CRUD
# ══════════════════════════════════════════════════════════════════

def add_provider(name: str, pdef: Any) -> dict[str, Any]:
    """新增 provider 并持久化。

    Parameters
    ----------
    name : str
        provider 名称。
    pdef : ProviderDef
        provider 定义（已排除 name 字段）。

    Returns
    -------
    dict
        ``{"provider": str}``。
    """
    reg = _get_model_registry()
    reg.add_provider(name, pdef)
    reg.save()
    return {"provider": name}


def remove_provider(name: str) -> dict[str, Any]:
    """删除 provider 并持久化。

    Returns
    -------
    dict
        ``{"removed": str}``。

    Raises
    ------
    ValueError
        provider 不存在或被引用，无法删除（由 registry 抛出）。
    """
    reg = _get_model_registry()
    reg.remove_provider(name)
    reg.save()
    return {"removed": name}


# ══════════════════════════════════════════════════════════════════
# §5  Model CRUD
# ══════════════════════════════════════════════════════════════════

def add_model(name: str, mdef: Any) -> dict[str, Any]:
    """新增模型并持久化。

    Returns
    -------
    dict
        ``{"model": str}``。
    """
    reg = _get_model_registry()
    reg.add_model(name, mdef)
    reg.save()
    return {"model": name}


def remove_model(name: str) -> dict[str, Any]:
    """删除模型并持久化。

    Returns
    -------
    dict
        ``{"removed": str}``。

    Raises
    ------
    KeyError
        模型不存在（registry 返回 False）。
    """
    reg = _get_model_registry()
    removed = reg.remove_model(name)
    if not removed:
        raise KeyError(f"Model {name} not found")
    reg.save()
    return {"removed": name}


# ══════════════════════════════════════════════════════════════════
# §6  API Key Vault
# ══════════════════════════════════════════════════════════════════

def store_api_key(provider: str, api_key: str) -> dict[str, Any]:
    """存储 provider 的 API key。

    Returns
    -------
    dict
        ``{"provider": str}``。
    """
    vault = _get_api_key_vault()
    vault.store(provider, api_key)
    return {"provider": provider}


def delete_api_key(provider: str) -> dict[str, Any]:
    """删除 provider 的 API key。

    Returns
    -------
    dict
        ``{"provider": str}``。

    Raises
    ------
    KeyError
        provider 不存在（vault 返回 False）。
    """
    vault = _get_api_key_vault()
    deleted = vault.delete(provider)
    if not deleted:
        raise KeyError(f"Provider {provider} not found")
    return {"provider": provider}


def list_api_key_providers() -> dict[str, Any]:
    """列出所有已配置 API key 的 provider。"""
    vault = _get_api_key_vault()
    return {"providers": vault.list_providers()}


# ══════════════════════════════════════════════════════════════════
# §7  Provider Health Check
# ══════════════════════════════════════════════════════════════════

async def check_provider_health(provider: str) -> dict[str, Any]:
    """检查 provider 健康状态。

    Parameters
    ----------
    provider : str
        provider 名称；空字符串表示检查全部 provider。

    Returns
    -------
    dict
        单 provider：``{"result": dict}``；
        全 provider：``{"results": [dict, ...]}``。
    """
    from maop.core.routing.provider_health import ProviderHealthChecker

    reg = _get_model_registry()
    vault = _get_api_key_vault()
    checker = ProviderHealthChecker(registry=reg, vault=vault)
    if provider:
        result = await checker.check(provider)
        return {"result": result.model_dump()}
    results = await checker.check_all()
    return {"results": [r.model_dump() for r in results]}


# ══════════════════════════════════════════════════════════════════
# §8  ModelGateway — 权限规则管理
# ══════════════════════════════════════════════════════════════════

def list_gateway_permissions() -> dict[str, Any]:
    """列出所有模型授权权限规则。

    Returns
    -------
    dict
        ``{"permissions": [...], "count": int}``。
    """
    gateway = _get_model_gateway()
    perms = gateway.list_permissions()
    return {
        "permissions": [p.model_dump(mode="json") for p in perms],
        "count": len(perms),
    }


def add_gateway_permission(perm: Any) -> dict[str, Any]:
    """添加权限规则。

    Returns
    -------
    dict
        ``{"model_pattern": str}``。
    """
    gateway = _get_model_gateway()
    gateway.add_permission(perm)
    return {"model_pattern": perm.model_pattern}


def update_gateway_permission(model_pattern: str, perm: Any) -> dict[str, Any]:
    """更新权限规则（按原 model_pattern 定位）。

    2026-09-23 新增：供 AgentGateway 的权限编辑使用。

    ``gateway.add_permission`` 本身是 upsert（同 pattern 覆盖），故直接复用；
    但**若本次修改同时改了 model_pattern**，需先删旧规则，否则会残留一条
    指向旧 pattern 的规则（改名场景）。

    Parameters
    ----------
    model_pattern : str
        原始 pattern（URL 路径参数），用于定位待更新规则。
    perm : Any
        新规则（含可能已变更的 ``model_pattern``）。

    Returns
    -------
    dict
        ``{"model_pattern": str, "renamed_from": str}``。
    """
    gateway = _get_model_gateway()
    new_pattern = getattr(perm, "model_pattern", "")
    if model_pattern and new_pattern and new_pattern != model_pattern:
        # 改名：先删旧，再写新（否则旧规则残留）
        gateway.remove_permission(model_pattern)
    gateway.add_permission(perm)
    return {"model_pattern": new_pattern, "renamed_from": model_pattern}


def remove_gateway_permission(model_pattern: str) -> dict[str, Any]:
    """删除权限规则。

    Returns
    -------
    dict
        ``{"model_pattern": str}``。

    Raises
    ------
    KeyError
        权限规则不存在（gateway 返回 False）。
    """
    gateway = _get_model_gateway()
    removed = gateway.remove_permission(model_pattern)
    if not removed:
        raise KeyError("permission rule not found")
    return {"model_pattern": model_pattern}


# ══════════════════════════════════════════════════════════════════
# §9  ModelGateway — 访问检查 / 使用量 / 配置
# ══════════════════════════════════════════════════════════════════

def check_model_access(model: str, agent: str, session_id: str) -> dict[str, Any]:
    """检查模型访问权限。

    Returns
    -------
    dict
        ``{"decision": dict}`` — gateway.check_access().model_dump()。
    """
    gateway = _get_model_gateway()
    decision = gateway.check_access(model, agent=agent, session_id=session_id)
    return {"decision": decision.model_dump(mode="json")}


def record_model_usage(model: str, tokens: int, agent: str, session_id: str) -> dict[str, Any]:
    """记录模型使用量。

    Returns
    -------
    dict
        ``{"model": str, "tokens": int}``。
    """
    gateway = _get_model_gateway()
    gateway.record_usage(model, tokens, agent=agent, session_id=session_id)
    return {"model": model, "tokens": tokens}


def get_model_daily_usage(model: str) -> dict[str, Any]:
    """获取今日模型使用量。

    Returns
    -------
    dict
        ``{"usage": dict, "total_tokens": int}``。
    """
    gateway = _get_model_gateway()
    usage = gateway.get_daily_usage(model=model)
    total = sum(usage.values())
    return {"usage": usage, "total_tokens": total}


def clear_model_daily_usage(model: str = "") -> dict[str, Any]:
    """清空今日模型使用量。

    2026-09-23 新增：供 AgentGateway 的「清空今日用量」使用。

    Returns
    -------
    dict
        ``{"cleared": int, "model": str}``。
    """
    gateway = _get_model_gateway()
    cleared = gateway.clear_daily_usage(model=model)
    return {"cleared": cleared, "model": model}


def update_gateway_config(config: Any) -> dict[str, Any]:
    """更新模型网关配置。

    Returns
    -------
    dict
        空字典（router 包装为 ``{"status": "ok"}``）。
    """
    gateway = _get_model_gateway()
    gateway.update_config(config)
    return {}