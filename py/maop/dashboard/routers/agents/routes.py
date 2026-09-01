"""Routes endpoint for the agents router.

Returns capability → primary/fallback/tertiary routing config merged
with agent info from the registry. Kept as its own module because the
handler is ~117 lines and conceptually distinct from CRUD/evolution/memory.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

from typing import Any

from fastapi import APIRouter

from maop.dashboard.error_handler import handle_api_errors

from . import _deps

router = APIRouter(prefix="/api/agents", tags=["agents"])


@router.get("/routes")
@handle_api_errors
async def get_agent_routes() -> dict[str, Any]:
    """返回路由配置 (capability → primary/fallback/tertiary)，合并 agent 信息。

    数据来源:
      1. registry.list_agents() — 提供 name/provider/enabled/model/capabilities
      2. config/agents.yaml routing: 块 — 提供 capability 路由规则

    每个 route 包含 Agents.vue 期望的 name/provider/enabled 字段。
    若 routing 配置为空，则从 agent 列表构建 routes。
    """
    import yaml as _yaml

    # 1. 从 registry 获取 agent 信息
    agent_map: dict[str, dict] = {}
    try:
        registry = _deps._get_registry()
        for a in registry.list_agents():
            name = getattr(a, "name", "") or ""
            if not name:
                continue
            agent_map[name] = {
                "name": name,
                "provider": getattr(a, "provider", "") or "",
                "enabled": getattr(a, "enabled", True),
                "model": getattr(a, "model", "") or "",
                "capabilities": getattr(a, "capabilities", []) or [],
            }
    except Exception:
        logger.warning('[agents/routes] get_agent_routes：从 registry 读取 agent 映射失败已忽略（agent_map 可能为空）', exc_info=True)

    # 2. 读 agents.yaml routing 配置
    yaml_path = _deps.MAOP_ROOT / "config" / "agents.yaml"
    if not yaml_path.exists():
        yaml_path = _deps.MAOP_ROOT / "agents.yaml"

    agents_cfg: dict = {}
    routing_cfg: dict = {}
    if yaml_path.exists():
        try:
            data = _yaml.safe_load(yaml_path.read_text(encoding="utf-8")) or {}
            agents_cfg = data.get("agents", {}) or {}
            routing_cfg = data.get("routing", {}) or {}
        except Exception:
            logger.warning('[agents/routes] get_agent_routes：读取 agents.yaml 路由配置失败已忽略（agents_cfg 为空），请检查 YAML 格式', exc_info=True)

    def _agent_model(agent_name: str) -> str:
        ad = agents_cfg.get(agent_name, {})
        if isinstance(ad, dict):
            return ad.get("model", "") or ad.get("model_display", "") or ""
        return ""

    def _agent_provider(agent_name: str) -> str:
        info = agent_map.get(agent_name, {})
        if info.get("provider"):
            return info["provider"]
        ad = agents_cfg.get(agent_name, {})
        if isinstance(ad, dict):
            return ad.get("provider", "") or ""
        return ""

    def _agent_enabled(agent_name: str) -> bool:
        info = agent_map.get(agent_name, {})
        if "enabled" in info:
            return info["enabled"]
        ad = agents_cfg.get(agent_name, {})
        if isinstance(ad, dict):
            return ad.get("enabled", True)
        return True

    routes = []

    # 3. 有 routing 配置: 为每个 capability 创建 route
    for capability, rule in routing_cfg.items():
        if not isinstance(rule, dict):
            continue
        primary = rule.get("primary", "")
        fallback = rule.get("fallback", "")
        tertiary = rule.get("tertiary", "")
        keywords = rule.get("keywords", []) or []
        match_pattern = rule.get("match", "")

        routes.append({
            "capability": capability,
            "name": primary,
            "provider": _agent_provider(primary),
            "enabled": _agent_enabled(primary),
            "primary": primary,
            "primary_model": _agent_model(primary),
            "fallback": fallback,
            "fallback_model": _agent_model(fallback),
            "tertiary": tertiary,
            "tertiary_model": _agent_model(tertiary),
            "keywords": keywords,
            "match": match_pattern,
        })

    # 4. 无 routing 配置: 从 agent 列表构建 routes
    if not routes and agent_map:
        for name, info in agent_map.items():
            caps = info.get("capabilities", [])
            cap = caps[0] if caps else "general"
            routes.append({
                "capability": cap,
                "name": name,
                "provider": info.get("provider", ""),
                "enabled": info.get("enabled", True),
                "primary": name,
                "primary_model": info.get("model", ""),
                "fallback": "",
                "fallback_model": "",
                "tertiary": "",
                "tertiary_model": "",
                "keywords": [],
                "match": "",
            })

    return {"routes": routes, "count": len(routes)}