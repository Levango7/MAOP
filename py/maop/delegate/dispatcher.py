"""MAOP Delegate Dispatcher — Config-driven agent execution.

Agent dispatch and driver resolution.: resolves agent config from YAML,
dispatches to the appropriate driver (cli/wrapper/powershell/cmd),
with circuit-breaker check, timeout, and unified MaopResult.

Architecture (split for maintainability):
  - models.py:  AgentConfig, DispatchResult, security helpers
  - drivers.py: 5 async driver implementations + DRIVERS table
  - dispatcher.py (this file): Dispatcher class + lazy subsystem imports
"""

from __future__ import annotations

import logging
from typing import Any

from maop.core.circuit_breaker import CircuitBreaker
from maop.core.error_schema import new_result
from maop.core.otel import get_tracer, span as otel_span

# Re-export for backward compatibility — callers importing from
# MAOP.delegate.dispatcher still get these symbols.
from maop.delegate.models import (  # noqa: F401
    AgentConfig,
    DispatchResult,
    _escape_for_cmd,
    _escape_for_ps_command,
)
from maop.delegate.drivers import DRIVERS as _DRIVERS

logger = logging.getLogger(__name__)

# ── Optional subsystems (lazy import to avoid hard deps) ──────

def _get_load_balancer():
    """Lazy import LoadBalancer."""
    try:
        from maop.core.load_balancer import get_load_balancer
        return get_load_balancer()
    except ImportError:
        return None
    except Exception as exc:
        # P2-6 fix: upgrade to error — runtime init failures should be visible
        logger.error("Failed to load driver LoadBalancer: %s", exc, exc_info=True)
        return None

def _get_runtime(config=None):
    """Lazy import Runtime."""
    try:
        from maop.core.runtime import create_runtime, RuntimeConfig, RuntimeType
        if config:
            return create_runtime(config)
        return create_runtime(RuntimeConfig(type=RuntimeType.LOCAL))
    except ImportError:
        return None
    except Exception as exc:
        logger.error("Failed to load driver Runtime: %s", exc, exc_info=True)
        return None

def _get_sandbox_manager(root_dir=None):
    """Lazy import SandboxManager."""
    try:
        from maop.core.sandbox import SandboxManager
        return SandboxManager(root_dir=root_dir)
    except ImportError:
        return None
    except Exception as exc:
        logger.error("Failed to load driver SandboxManager: %s", exc, exc_info=True)
        return None

def _get_subagent_manager(root_dir=None):
    """Lazy import SubagentManager."""
    try:
        from maop.core.subagent_delegation import SubagentManager
        return SubagentManager(root_dir=root_dir)
    except ImportError:
        return None
    except Exception as exc:
        logger.error("Failed to load driver SubagentManager: %s", exc, exc_info=True)
        return None

def _get_agent_registry(root_dir=None):
    """Lazy import AgentRegistry."""
    try:
        from maop.core.agent_registry import AgentRegistry
        return AgentRegistry(root_dir=root_dir or "data")
    except ImportError:
        return None
    except Exception as exc:
        logger.error("Failed to load driver AgentRegistry: %s", exc, exc_info=True)
        return None

def _get_capability_matcher(root_dir=None):
    """Lazy import CapabilityMatcher with AgentRegistry."""
    try:
        from maop.core.agent_registry import AgentRegistry
        from maop.core.capability_matcher import CapabilityMatcher
        registry = AgentRegistry(root_dir=root_dir or "data")
        return CapabilityMatcher(registry=registry)
    except ImportError:
        return None
    except Exception as exc:
        logger.error("Failed to load driver CapabilityMatcher: %s", exc, exc_info=True)
        return None


# ── Dispatcher ────────────────────────────────────────────────

class Dispatcher:
    """Config-driven agent dispatcher with circuit-breaker protection.

    Resolution order:
      1. YAML config (agents.yaml / workflows)
      2. AgentRegistry + CapabilityMatcher (auto-discovered agents)

    Usage::

        from maop.delegate import Dispatcher
        from maop.config.loader import ConfigLoader

        loader = ConfigLoader()
        config = loader.load()
        dispatcher = Dispatcher(config)

        result = await dispatcher.dispatch(
            agent="claude", task="write a function",
            routing_key="codegen", trace_id="abc123",
        )
    """

    def __init__(
        self,
        MAOP_config: Any | None = None,
        breaker: CircuitBreaker | None = None,
        model_selector: Any | None = None,
        root_dir: str | None = None,
        *,
        registry: Any | None = None,
        capability_matcher: Any | None = None,
    ) -> None:
        self._config = MAOP_config
        self._breaker = breaker or CircuitBreaker()
        self._agent_cache: dict[str, AgentConfig] = {}
        self._cache_versions: dict[str, int] = {}
        self._model_selector = model_selector
        self._effective_model: Any | None = None
        self._root_dir = root_dir
        self._subagent_mgr = None
        self._registry = registry
        self._matcher = capability_matcher
        self._agents_index: dict[str, Any] | None = None
        self._workflows_index: dict[str, Any] | None = None

    @property
    def effective_model(self) -> Any | None:
        """Return the last resolved EffectiveModel (for audit/logging)."""
        return self._effective_model

    def clear_agent_cache(self) -> None:
        """Clear the agent config cache (call after config reload)."""
        self._agent_cache.clear()
        self._cache_versions.clear()

    def _resolve_agent(self, agent_name: str) -> AgentConfig | None:
        """Resolve agent config from the loaded MAOP config.

        Supports ``parent/child`` format for subagents (e.g. ``mavis/verifier``).
        The parent's *cli* is combined with the child's *cli_args* to build
        the final AgentConfig.

        P1-19 fix: cache entries now store config_version for invalidation
        when ConfigLoader.reload() is called. Also re-checks enabled flag
        on cache hit to respect runtime enable/disable changes.

        Returns a copy of the cached config to prevent callers from
        mutating the shared cache (e.g., dispatch() overrides model).
        """
        cached = self._agent_cache.get(agent_name)
        if cached is not None:
            # P1-19 fix: version-based cache invalidation
            current_version = getattr(self._config, '_version', 0) if self._config else 0
            cached_version = self._cache_versions.get(agent_name, 0)
            if cached_version == current_version:
                return cached.model_copy()
            else:
                # Config changed, invalidate cache entry
                del self._agent_cache[agent_name]
                self._cache_versions.pop(agent_name, None)

        # Fallback: AgentRegistry lookup (works even without YAML config)
        if self._config is None:
            reg_cfg = self._resolve_from_registry(agent_name)
            return reg_cfg

        # ── Subagent resolution: parent/child ────────────────────
        if "/" in agent_name:
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
            # F2c (2026-07-22, Phase F): propagate `provider` field through
            # all 8 AgentConfig(...) construction sites (ADR-013). Child
            # subagent may override provider; otherwise inherit parent's.
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
            self._agent_cache[agent_name] = cfg
            self._cache_versions[agent_name] = getattr(self._config, '_version', 0) if self._config else 0
            return cfg

        # ── Regular agent resolution ─────────────────────────────
        agents = getattr(self._config, "agents", None)
        if agents:
            if isinstance(agents, dict):
                a = agents.get(agent_name)
                if a is not None:
                    # B-P0-4 fix: respect enabled: false (was silently ignored)
                    if getattr(a, 'enabled', True) is False:
                        logger.warning("Agent '%s' is disabled (enabled: false)", agent_name)
                        return None
                    cfg = AgentConfig(
                        name=agent_name, cli=a.cli, driver=a.driver,
                        cli_args=getattr(a, 'cli_args', ''),
                        capabilities=a.capabilities,
                        timeout_s=a.timeout_s, model=getattr(a, 'model', ''),
                        provider=getattr(a, 'provider', ''),
                        wrapper=a.wrapper, command=getattr(a, 'command', ''),
                    )
                    self._agent_cache[agent_name] = cfg
                    self._cache_versions[agent_name] = getattr(self._config, '_version', 0) if self._config else 0
                    return cfg
            else:
                agents_by_name = self._build_agents_index(agents)
                a = agents_by_name.get(agent_name)
                if a is not None:
                    # B-P0-4 fix: respect enabled: false
                    if getattr(a, 'enabled', True) is False:
                        logger.warning("Agent '%s' is disabled (enabled: false)", agent_name)
                        return None
                    cfg = AgentConfig(
                        name=a.name, cli=a.cli, driver=a.driver,
                        cli_args=a.cli_args, capabilities=a.capabilities,
                        timeout_s=a.timeout_s, model=a.model,
                        provider=getattr(a, 'provider', ''),
                        wrapper=a.wrapper, command=a.command,
                    )
                    self._agent_cache[agent_name] = cfg
                    self._cache_versions[agent_name] = getattr(self._config, '_version', 0) if self._config else 0
                    return cfg

        # Try workflows section — supports both dict and list
        workflows = getattr(self._config, "workflows", None)
        if workflows:
            if isinstance(workflows, dict):
                w = workflows.get(agent_name)
                if w is not None:
                    cfg = AgentConfig(
                        name=agent_name, cli=w.cli, driver=w.driver,
                        timeout_s=w.timeout_s, model=getattr(w, 'model', ''),
                        provider=getattr(w, 'provider', ''),
                        wrapper=w.wrapper, command=getattr(w, 'command', ''),
                    )
                    self._agent_cache[agent_name] = cfg
                    self._cache_versions[agent_name] = getattr(self._config, '_version', 0) if self._config else 0
                    return cfg
            else:
                wf_by_name = self._build_workflows_index(workflows)
                w = wf_by_name.get(agent_name)
                if w is not None:
                    cfg = AgentConfig(
                        name=w.name, cli=w.cli, driver=w.driver,
                        timeout_s=w.timeout_s, model=w.model,
                        provider=getattr(w, 'provider', ''),
                        wrapper=w.wrapper, command=w.command,
                    )
                    self._agent_cache[agent_name] = cfg
                    self._cache_versions[agent_name] = getattr(self._config, '_version', 0) if self._config else 0
                    return cfg

        # Wildcard match
        if agents:
            if isinstance(agents, dict):
                for a_name, a in agents.items():
                    if agent_name != a_name and _wildcard_match(agent_name, a_name):
                        cfg = AgentConfig(
                            name=a_name, cli=a.cli, driver=a.driver,
                            cli_args=getattr(a, 'cli_args', ''),
                            capabilities=a.capabilities,
                            timeout_s=a.timeout_s, model=getattr(a, 'model', ''),
                            provider=getattr(a, 'provider', ''),
                            wrapper=a.wrapper, command=getattr(a, 'command', ''),
                        )
                        self._agent_cache[agent_name] = cfg
                        self._cache_versions[agent_name] = getattr(self._config, '_version', 0) if self._config else 0
                        return cfg
            else:
                for a in agents:
                    if agent_name != a.name and _wildcard_match(agent_name, a.name):
                        cfg = AgentConfig(
                            name=a.name, cli=a.cli, driver=a.driver,
                            cli_args=a.cli_args, capabilities=a.capabilities,
                            timeout_s=a.timeout_s, model=a.model,
                            provider=getattr(a, 'provider', ''),
                            wrapper=a.wrapper, command=a.command,
                        )
                        self._agent_cache[agent_name] = cfg
                        self._cache_versions[agent_name] = getattr(self._config, '_version', 0) if self._config else 0
                        return cfg

        # Fallback: AgentRegistry lookup
        reg_cfg = self._resolve_from_registry(agent_name)
        if reg_cfg is not None:
            return reg_cfg

        return None

    def _build_agents_index(self, agents: list) -> dict[str, Any]:
        if self._agents_index is None:
            self._agents_index = {a.name: a for a in agents}
        return self._agents_index

    def _build_workflows_index(self, workflows: list) -> dict[str, Any]:
        if self._workflows_index is None:
            self._workflows_index = {w.name: w for w in workflows}
        return self._workflows_index

    def _notify_route_scorer(self, agent: str, *, success: bool) -> None:
        """Notify RouteScorer of agent success/failure for cooldown tracking.

        P0-3 fix: RouteScorer.cooldown was never populated because
        mark_agent_failed/mark_agent_success had no callers. Now invoked
        alongside circuit-breaker recording in _dispatch_impl.
        """
        try:
            from maop.core.route_scorer import get_route_scorer
            scorer = get_route_scorer(self._config)
            if success:
                scorer.mark_agent_success(agent)
            else:
                scorer.mark_agent_failed(agent)
        except Exception as exc:
            # P2-7 fix: upgrade to warning — cooldown mechanism failure
            # affects routing quality and should be visible in logs
            logger.warning("[dispatch] RouteScorer notify failed: %s", exc)

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

    def _resolve_from_registry(self, agent_name: str) -> AgentConfig | None:
        """Try to resolve an agent from the AgentRegistry by name."""
        registry = self._registry or _get_agent_registry(self._root_dir)
        if registry is None:
            return None

        agent = registry.get_agent(agent_name)
        if agent is None or not agent.enabled:
            return None

        # F2c (2026-07-22, Phase F): propagate `provider` from registry
        # agent if available (ADR-013 dual-path). Use getattr for safety
        # in case older registry entries lack the field.
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
        self._agent_cache[agent_name] = cfg
        self._cache_versions[agent_name] = getattr(self._config, '_version', 0) if self._config else 0
        logger.info("[dispatcher] Resolved '%s' from AgentRegistry", agent_name)
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
        return self._resolve_agent(best.agent_name)

    async def dispatch(
        self,
        agent: str,
        task: str,
        *,
        routing_key: str = "",
        workdir: str = "",
        timeout_seconds: int | None = None,
        trace_id: str = "",
        streamer: Any | None = None,
    ) -> DispatchResult:
        """Dispatch a task to the specified agent.

        Steps
        -----
        1. Resolve agent config from YAML.
        2. Check circuit-breaker — reject if open.
        3. Execute via the appropriate driver.
        4. Record result in circuit-breaker.
        5. Return DispatchResult envelope.
        """
        tracer = get_tracer("maop.dispatch")
        with otel_span(tracer, f"dispatch.{agent}", trace_id=trace_id,
                       attributes={"agent": agent, "task": task[:80], "routing_key": routing_key}):
            return await self._dispatch_impl(agent, task, routing_key=routing_key,
                                             workdir=workdir, timeout_seconds=timeout_seconds,
                                             trace_id=trace_id, streamer=streamer)

    async def _dispatch_impl(
        self,
        agent: str,
        task: str,
        *,
        routing_key: str = "",
        workdir: str = "",
        timeout_seconds: int | None = None,
        trace_id: str = "",
        streamer: Any | None = None,
    ) -> DispatchResult:
        # 1. Resolve agent config
        config = self._resolve_agent(agent)

        # 1.1. Fallback: capability-based matching when agent not in config
        if config is None:
            matched = self.match_agent(task)
            if matched is not None:
                logger.info(
                    "[dispatch] Agent '%s' not in config, matched '%s' via capability scoring",
                    agent, matched.name,
                )
                config = matched
                agent = matched.name

        if config is None:
            result = new_result(
                agent=agent, task=task,
                exit_code=-2, error=f"Agent '{agent}' not found in config or registry",
                trace_id=trace_id, routing_key=routing_key,
            )
            return DispatchResult(result=result, breaker_tripped=False)

        # 1.1. Guardrail check — reject if input violates safety rules
        try:
            from maop.core.guardrail import Guardrail
            gr = Guardrail()
            check = gr.check(content=task, agent=agent, task=routing_key)
            if not check.passed:
                blocked = [v.message for v in check.violations if v.action == "block"]
                result = new_result(
                    agent=agent, task=task,
                    exit_code=-4, error=f"Guardrail BLOCKED: {'; '.join(blocked)}",
                    trace_id=trace_id, routing_key=routing_key,
                )
                return DispatchResult(result=result, breaker_tripped=False)
        except (ValueError, KeyError, OSError) as exc:
            # P2-15 fix: narrow exception scope — let TypeError/AttributeError
            # (programming bugs) propagate, only catch expected data errors
            logger.warning("[dispatch] Guardrail check failed (fail-closed): %s", exc)
            result = new_result(
                agent=agent, task=task,
                exit_code=-4, error=f"Guardrail check error (fail-closed): {exc}",
                trace_id=trace_id, routing_key=routing_key,
            )
            return DispatchResult(result=result, breaker_tripped=False)

        # 1.5. Resolve effective model via ModelSelector (mandatory when configured)
        model_resolved = True
        if self._model_selector is not None:
            try:
                em = self._model_selector.select_for_routing_key(
                    routing_key=routing_key or (config.capabilities[0] if config.capabilities else ""),
                    agent_model=config.model or "",
                    policy_name=routing_key or "",
                )
                self._effective_model = em
                # Inject resolved model name into config (strong contract)
                config.model = em.model_name
            except Exception as exc:
                # ModelSelector configured but resolution failed — contract violation
                model_resolved = False
                logger.warning(
                    "ModelSelector resolution FAILED for agent=%s routing_key=%s: %s. "
                    "Falling back to agent-config model=%s.",
                    agent, routing_key, exc, config.model,
                )
                self._effective_model = None

        # 2. Circuit-breaker check
        if not await self._breaker.ais_available(agent):
            # Attempt failover to a fallback agent before giving up.
            try:
                failover = self._breaker.resolve_failover(agent)
            except Exception as exc:
                logger.warning("[dispatch] resolve_failover raised for '%s': %s", agent, exc)
                failover = None
            if failover is not None and failover.agent:
                logger.info(
                    "[dispatch] Circuit OPEN for '%s', failing over to '%s' (degraded=%s)",
                    agent, failover.agent, failover.degraded,
                )
                return await self._dispatch_impl(
                    failover.agent, task,
                    routing_key=routing_key, workdir=workdir,
                    timeout_seconds=timeout_seconds, trace_id=trace_id,
                    streamer=streamer,
                )
            result = new_result(
                agent=agent, task=task,
                exit_code=-3, error=f"Circuit breaker OPEN for '{agent}'",
                trace_id=trace_id, routing_key=routing_key,
            )
            return DispatchResult(result=result, breaker_tripped=True)

        # 2.5. Budget check - reject if daily/monthly budget already exceeded.
        # Non-blocking: any failure in BudgetGuard itself must NOT prevent
        # normal dispatch (only log a warning).
        try:
            from maop.model.budget import BudgetGuard
            _bg_config = getattr(self._config, "budget", None)
            _budget_guard = BudgetGuard(root_dir=self._root_dir, config=_bg_config)
            if not _budget_guard.can_spend(estimated_cost=0.0):
                logger.warning(
                    "[dispatch] Budget EXCEEDED for agent='%s' - rejecting task (trace_id=%s)",
                    agent, trace_id,
                )
                result = new_result(
                    agent=agent, task=task,
                    exit_code=-6, error="Budget EXCEEDED - daily or monthly limit reached",
                    trace_id=trace_id, routing_key=routing_key,
                )
                return DispatchResult(result=result, breaker_tripped=False)
        except Exception as exc:
            # Conservative: budget check failure must not block dispatch.
            logger.warning("[dispatch] BudgetGuard check unavailable (non-blocking): %s", exc)

        # 3. Determine timeout
        timeout = timeout_seconds or config.timeout_s

        # 4. Execute via driver
        driver_fn = _DRIVERS.get(config.driver)
        if driver_fn is None:
            result = new_result(
                agent=agent, task=task,
                exit_code=-2, error=f"Unknown driver: {config.driver}",
                trace_id=trace_id, routing_key=routing_key,
            )
            await self._breaker.arecord_failure(agent)
            self._notify_route_scorer(agent, success=False)
            return DispatchResult(result=result, breaker_tripped=False)

        # 4. Execute via driver — wrap in try/except to ensure failures are recorded
        # in circuit-breaker and route scorer cooldown (P0-3 + P1-7 fix)
        import time as _time
        _dispatch_start = _time.monotonic()
        # P2-7 fix: record task start to LoadBalancer for adaptive scoring
        try:
            lb = _get_load_balancer()
            if lb:
                lb.record_start(agent, trace_id or task[:32])
        except Exception as exc:
            logger.debug("LoadBalancer record failed: %s", exc)
        try:
            result = await driver_fn(config, task, timeout, workdir, trace_id, streamer=streamer)  # type: ignore[call-arg]
            result.routing_key = routing_key
        except Exception as exc:
            result = new_result(
                agent=agent, task=task,
                exit_code=-5, error=f"Driver exception: {exc}",
                trace_id=trace_id, routing_key=routing_key,
            )
            logger.error("[dispatch] Driver '%s' raised: %s", config.driver, exc)

        # P2-7 fix: record task finish to LoadBalancer
        try:
            lb = _get_load_balancer()
            if lb:
                lb.record_finish(
                    agent, trace_id or task[:32],
                    duration_ms=(_time.monotonic() - _dispatch_start) * 1000,
                    success=result.is_success(),
                )
        except Exception as exc:
            logger.debug("LoadBalancer record failed: %s", exc)

        # 5. Record in circuit-breaker and route scorer cooldown
        if result.is_success():
            # P1-16 fix: use async breaker methods to avoid blocking event loop
            await self._breaker.arecord_success(agent)
            self._notify_route_scorer(agent, success=True)
        else:
            await self._breaker.arecord_failure(agent)
            self._notify_route_scorer(agent, success=False)

        return DispatchResult(
            result=result,
            driver_used=config.driver,
            breaker_tripped=False,
            model_resolved=model_resolved,
        )

    async def delegate_to_subagent(
        self,
        parent: str,
        agent: str,
        task: str,
        *,
        routing_key: str = "",
        trace_id: str = "",
        max_depth: int = 5,
    ) -> DispatchResult:
        """Spawn a sub-agent and dispatch the task through it.

        This enables recursive delegation: agent A -> agent B -> agent C,
        with depth tracking to prevent infinite recursion.
        """
        if self._subagent_mgr is None:
            self._subagent_mgr = _get_subagent_manager(self._root_dir)
        if self._subagent_mgr is None:
            result = new_result(
                agent=agent, task=task,
                exit_code=-2, error="SubagentManager not available",
                trace_id=trace_id, routing_key=routing_key,
            )
            return DispatchResult(result=result, breaker_tripped=False)

        sa_info = self._subagent_mgr.spawn(
            parent=parent, agent=agent, task=task, max_depth=max_depth,
        )

        dispatch_result = await self.dispatch(
            agent=agent, task=task,
            routing_key=routing_key, trace_id=trace_id,
        )

        exit_code = dispatch_result.result.exit_code if dispatch_result.result else -1
        self._subagent_mgr.terminate(sa_info.id, exit_code=exit_code)

        return dispatch_result


def _wildcard_match(pattern: str, name: str) -> bool:
    """Simple wildcard match using fnmatch-style * and ?.

    pattern: the agent name being searched for (e.g. "codex-mini")
    name: the config agent name which may contain wildcards (e.g. "codex*")
    """
    import fnmatch
    return fnmatch.fnmatch(pattern, name)
