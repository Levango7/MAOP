"""MAOP Agent Resolver — Agent config resolution, caching, and matching.

Extracted from dispatcher.py (N2 refactor) to reduce Dispatcher complexity
from ~1167 lines to ~600 lines.

Responsibilities:
  - Resolve agent configs from YAML / AgentRegistry / wildcard match
  - Version-based cache invalidation on config reload
  - Subagent parent/child resolution
  - Capability-based agent matching via CapabilityMatcher
"""

from __future__ import annotations

import logging
from typing import Any, cast

from maop.delegate.models import AgentConfig

logger = logging.getLogger(__name__)


def _wildcard_match(pattern: str, name: str) -> bool:
    """Simple wildcard match using fnmatch-style * and ?.

    pattern: the agent name being searched for (e.g. "codex-mini")
    name: the config agent name which may contain wildcards (e.g. "codex*")
    """
    import fnmatch
    return fnmatch.fnmatch(pattern, name)


def _get_agent_registry(root_dir=None):
    """Lazy import AgentRegistry."""
    try:
        from maop.core.agent.lifecycle.agent_registry import AgentRegistry
        return AgentRegistry(root_dir=root_dir or "data")
    except ImportError:
        return None
    except Exception:
        logger.exception("Failed to load AgentRegistry")
        return None


def _get_capability_matcher(root_dir=None):
    """Lazy import CapabilityMatcher with AgentRegistry."""
    try:
        from maop.core.agent.lifecycle.agent_registry import AgentRegistry
        from maop.core.agent.tools.capability_matcher import CapabilityMatcher
        registry = AgentRegistry(root_dir=root_dir or "data")
        return CapabilityMatcher(registry=registry)
    except ImportError:
        return None
    except Exception:
        logger.exception("Failed to load CapabilityMatcher")
        return None


class AgentResolver:
    """Resolves agent configurations from YAML config, registry, or wildcard match.

    Usage::

        resolver = AgentResolver(config, root_dir="/path/to/maop")
        config = await resolver.resolve("claude")
        best = await resolver.match_agent("write a function", ["codegen"])
    """

    def __init__(
        self,
        config: Any | None = None,
        root_dir: str | None = None,
        *,
        registry: Any | None = None,
        capability_matcher: Any | None = None,
    ) -> None:
        self._config = config
        self._root_dir = root_dir
        self._registry = registry
        self._matcher = capability_matcher
        self._cache: dict[str, AgentConfig] = {}
        self._cache_versions: dict[str, int] = {}
        self._agents_index: dict[str, Any] | None = None
        self._workflows_index: dict[str, Any] | None = None

    def clear_cache(self) -> None:
        """Clear the agent config cache (call after config reload)."""
        self._cache.clear()
        self._cache_versions.clear()
        self._agents_index = None
        self._workflows_index = None

    def resolve(self, agent_name: str) -> AgentConfig | None:
        """Resolve an agent config by name.

        Resolution order:
          1. Cache (version-checked)
          2. YAML config agents dict/list
          3. YAML workflows dict/list
          4. Wildcard match against config agents
          5. AgentRegistry auto-discovery

        Supports ``parent/child`` format for subagents.
        """
        # 1. Check cache with version validation
        cached = self._cache.get(agent_name)
        if cached is not None:
            current_version = getattr(self._config, '_version', 0) if self._config else 0
            cached_version = self._cache_versions.get(agent_name, 0)
            if cached_version == current_version:
                return cached.model_copy()  # type: ignore[no-any-return]
            else:
                del self._cache[agent_name]
                self._cache_versions.pop(agent_name, None)

        # No config? Try registry directly.
        if self._config is None:
            return self._resolve_from_registry(agent_name)

        # 2. Subagent resolution: parent/child
        if "/" in agent_name:
            return self._resolve_subagent(agent_name)

        # 3. Regular agent from agents section
        agents = getattr(self._config, "agents", None)
        if agents:
            cfg = self._resolve_from_agents(agents, agent_name)
            if cfg is not None:
                return cfg

        # 4. Workflows section
        workflows = getattr(self._config, "workflows", None)
        if workflows:
            cfg = self._resolve_from_workflows(workflows, agent_name)
            if cfg is not None:
                return cfg

        # 5. Wildcard match
        if agents:
            cfg = self._wildcard_resolve(agents, agent_name)
            if cfg is not None:
                return cfg

        # 6. Fallback: AgentRegistry
        return self._resolve_from_registry(agent_name)

    def _resolve_subagent(self, agent_name: str) -> AgentConfig | None:
        """Resolve a subagent in 'parent/child' format."""
        parent_name, child_name = agent_name.split("/", 1)
        parent_def = self._find_agent_def(parent_name)
        if parent_def is None:
            return None
        subagents = getattr(parent_def, "subagents", None) or {}
        child_def = subagents.get(child_name)
        if child_def is None:
            logger.warning(
                "Subagent '%s' not found under parent '%s'", child_name, parent_name,
            )
            return None
        child_provider = getattr(child_def, 'provider', '') or getattr(parent_def, 'provider', '')
        cfg = AgentConfig(
            name=agent_name,
            cli=parent_def.cli,
            driver=parent_def.driver,
            cli_args=child_def.cli_args,
            capabilities=child_def.capabilities or parent_def.capabilities,
            timeout_s=parent_def.timeout_s,
            model=parent_def.model,
            provider=child_provider,
            wrapper=parent_def.wrapper,
        )
        self._cache[agent_name] = cfg
        self._cache_versions[agent_name] = getattr(self._config, '_version', 0) if self._config else 0
        return cfg

    def _resolve_from_agents(self, agents: Any, agent_name: str) -> AgentConfig | None:
        """Try to resolve from the agents section (dict or list)."""
        if isinstance(agents, dict):
            a = agents.get(agent_name)
            if a is not None:
                if getattr(a, 'enabled', True) is False:
                    logger.warning("Agent '%s' is disabled (enabled: false)", agent_name)
                    return None
                return self._make_config(agent_name, a)
        else:
            idx = self._build_agents_index(agents)
            a = idx.get(agent_name)
            if a is not None:
                if getattr(a, 'enabled', True) is False:
                    logger.warning("Agent '%s' is disabled (enabled: false)", agent_name)
                    return None
                return self._make_config(agent_name, a)
        return None

    def _resolve_from_workflows(self, workflows: Any, agent_name: str) -> AgentConfig | None:
        """Try to resolve from the workflows section."""
        if isinstance(workflows, dict):
            w = workflows.get(agent_name)
            if w is not None:
                return self._make_config(agent_name, w)
        else:
            idx = self._build_workflows_index(workflows)
            w = idx.get(agent_name)
            if w is not None:
                return self._make_config(agent_name, w)
        return None

    def _wildcard_resolve(self, agents: Any, agent_name: str) -> AgentConfig | None:
        """Wildcard match against agent names."""
        if isinstance(agents, dict):
            for a_name, a in agents.items():
                if agent_name != a_name and _wildcard_match(agent_name, a_name):
                    return self._make_config(a_name, a)
        else:
            for a in agents:
                if agent_name != a.name and _wildcard_match(agent_name, a.name):
                    return self._make_config(a.name, a)
        return None

    def _make_config(self, name: str, defn: Any) -> AgentConfig:
        """Build an AgentConfig from a definition dict/object and cache it."""
        cfg = AgentConfig(
            name=name,
            cli=defn.cli,
            driver=defn.driver,
            cli_args=getattr(defn, 'cli_args', ''),
            capabilities=cast(list[str], getattr(defn, 'capabilities', None) or []),
            timeout_s=defn.timeout_s,
            model=getattr(defn, 'model', ''),
            provider=getattr(defn, 'provider', ''),
            wrapper=cast(str, getattr(defn, 'wrapper', None) or ""),
            command=getattr(defn, 'command', ''),
        )
        self._cache[name] = cfg
        self._cache_versions[name] = getattr(self._config, '_version', 0) if self._config else 0
        return cfg

    def _find_agent_def(self, name: str):
        """Look up an AgentDef by name from the config (dict form only)."""
        agents = getattr(self._config, "agents", None)
        if agents and isinstance(agents, dict):
            return agents.get(name)
        if agents:
            for a in agents:
                if a.name == name:
                    return a
        return None

    def _build_agents_index(self, agents: list) -> dict[str, Any]:
        if self._agents_index is None:
            self._agents_index = {a.name: a for a in agents}
        return self._agents_index

    def _build_workflows_index(self, workflows: list) -> dict[str, Any]:
        if self._workflows_index is None:
            self._workflows_index = {w.name: w for w in workflows}
        return self._workflows_index

    def _resolve_from_registry(self, agent_name: str) -> AgentConfig | None:
        """Try to resolve an agent from the AgentRegistry by name."""
        registry = self._registry or _get_agent_registry(self._root_dir)
        if registry is None:
            return None
        agent = registry.get_agent(agent_name)
        if agent is None or not agent.enabled:
            return None
        cfg = AgentConfig(
            name=agent.name,
            cli=agent.cli_path,
            driver=agent.driver or "cli",
            cli_args=agent.cli_args,
            capabilities=agent.capabilities,
            timeout_s=agent.timeout_s,
            model=agent.model,
            provider=getattr(agent, 'provider', ''),
        )
        self._cache[agent_name] = cfg
        self._cache_versions[agent_name] = getattr(self._config, '_version', 0) if self._config else 0
        logger.info("[resolver] Resolved '%s' from AgentRegistry", agent_name)
        return cfg

    def match_agent(self, task: str, requirements: list[str] | None = None) -> AgentConfig | None:
        """Use CapabilityMatcher to find the best agent for a task.

        Returns the highest-scoring agent as an AgentConfig, or None.
        """
        matcher = self._matcher or _get_capability_matcher(self._root_dir)
        if matcher is None:
            return None
        scores = matcher.match(task=task, requirements=requirements, top_k=1)
        if not scores or scores[0].total_score <= 0:
            return None
        best = scores[0]
        return self.resolve(best.agent_name)
