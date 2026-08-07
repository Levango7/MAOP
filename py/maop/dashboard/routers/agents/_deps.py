"""Shared dependencies for the agents router subpackage.

Centralizes module-level caches and lazy factory helpers so that all
sub-routers (crud/evolution/memory/routes) share a single source of
truth for registry/scanner/repair/memory/evolution/matcher instances.

Backward-compatibility note:
    Tests historically monkeypatched ``maop.dashboard.routers.agents._get_*``
    symbols.  Those attributes are re-exported from the package ``__init__``
    but sub-routers always call them through ``_deps._get_*()`` so that
    patching ``maop.dashboard.routers.agents._deps._get_*`` takes effect
    regardless of which sub-router the endpoint lives in.
"""

from __future__ import annotations

from typing import Any

from maop.dashboard.routers.state import MAOP_ROOT

# Module-level cache for lazily-constructed singletons (scanner/registry/...).
# Kept here (not in each sub-router) so all sub-routers share one cache.
_instance_cache: dict[str, Any] = {}


def _get_scanner():
    if "scanner" not in _instance_cache:
        from maop.core.agent.lifecycle.agent_scanner import AgentScanner

        root = MAOP_ROOT
        _instance_cache["scanner"] = AgentScanner(root_dir=str(root))
    return _instance_cache["scanner"]


def _get_registry():
    if "registry" not in _instance_cache:
        from maop.core.agent.lifecycle.agent_registry import AgentRegistry

        root = MAOP_ROOT
        _instance_cache["registry"] = AgentRegistry(root_dir=str(root))
    return _instance_cache["registry"]


def _get_matcher():
    if "matcher" not in _instance_cache:
        from maop.core.agent.lifecycle.agent_registry import AgentRegistry
        from maop.core.agent.tools.capability_matcher import CapabilityMatcher

        root = MAOP_ROOT
        registry = AgentRegistry(root_dir=str(root))
        _instance_cache["matcher"] = CapabilityMatcher(registry=registry)
    return _instance_cache["matcher"]


def _get_repair():
    if "repair" not in _instance_cache:
        from maop.core.agent.lifecycle.agent_repair import AgentRepair

        root = MAOP_ROOT
        _instance_cache["repair"] = AgentRepair(root_dir=str(root))
    return _instance_cache["repair"]


def _get_memory():
    if "memory" not in _instance_cache:
        from maop.core.agent.memory_ctx.agent_memory import AgentMemory

        root = MAOP_ROOT
        _instance_cache["memory"] = AgentMemory(root_dir=str(root))
    return _instance_cache["memory"]


def _get_evolution():
    if "evolution" not in _instance_cache:
        from maop.core.agent.evolution.agent_evolution import AgentEvolution

        root = MAOP_ROOT
        _instance_cache["evolution"] = AgentEvolution(root_dir=str(root))
    return _instance_cache["evolution"]


def _get_agent_config(agent_name: str):
    """从 agents.yaml 加载指定 agent 的配置。"""
    try:
        from maop.config.loader import ConfigLoader

        root = MAOP_ROOT
        cfg = ConfigLoader(project_root=str(root)).load()
        return cfg.agents.get(agent_name)
    except Exception:
        return None