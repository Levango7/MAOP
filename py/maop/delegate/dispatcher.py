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

import asyncio
import logging
import time
from typing import Any

from maop.core.circuit_breaker import CircuitBreaker
from maop.core.error_schema import new_result
from maop.core.monitoring import (
    MAOP_ROUTING_DECISION_DURATION_MS,
    MAOP_ROUTING_DECISION_TOTAL,
    MAOP_TASK_DEADLINE_SECONDS,
    MAOP_TASK_PREEMPTION_TOTAL,
    MAOP_TASK_PRIORITY_DISTRIBUTION,
    MAOP_TASK_SLA_TIER_DISTRIBUTION,
    MAOP_TASK_SLA_VIOLATION_TOTAL,
)
from maop.core.otel import get_tracer
from maop.core.otel import span as otel_span
from maop.core.routing_decision import (
    RoutingDecisionRecord,
    get_active_span_context,
    record_decision_safe,
)
from maop.delegate.drivers import DRIVERS as _DRIVERS

# Re-export for backward compatibility — callers importing from
# MAOP.delegate.dispatcher still get these symbols.
from maop.delegate.models import (  # noqa: F401
    AgentConfig,
    DispatchResult,
    _escape_for_cmd,
    _escape_for_ps_command,
)

logger = logging.getLogger(__name__)


# ── SLA helpers (Phase γ-1) ───────────────────────────────────

def _tier_from_priority(priority: int) -> str:
    """Derive a default SLA tier from a priority level.

    Mapping:
      - priority 1       -> ``critical``
      - priority 2..3    -> ``standard``
      - priority 4..5     -> ``best_effort``

    This is a heuristic used only when the caller does not pass an
    explicit ``sla_tier`` to dispatch (the dispatch API exposes
    ``priority`` / ``deadline_ms`` but not ``sla_tier`` per Phase γ-1
    contract; the Plan model carries the authoritative ``sla_tier``).
    """
    if priority <= 1:
        return "critical"
    if priority <= 3:
        return "standard"
    return "best_effort"

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
        from maop.core.runtime import RuntimeConfig, RuntimeType, create_runtime
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
        priority_queue: Any | None = None,
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
        # Phase γ-2: optional priority queue for priority-aware dispatch.
        # When None (default), dispatch() executes synchronously as before.
        # When set, dispatch_priority() enqueues and drain_pending() pops in
        # priority order. Kept optional to preserve backward compatibility.
        self._priority_queue = priority_queue

    @property
    def effective_model(self) -> Any | None:
        """Return the last resolved EffectiveModel (for audit/logging)."""
        return self._effective_model

    def clear_agent_cache(self) -> None:
        """Clear the agent config cache (call after config reload)."""
        self._agent_cache.clear()
        self._cache_versions.clear()

    # ── Phase γ-2: priority queue integration ──────────────────

    def set_priority_queue(self, queue: Any | None) -> None:
        """Attach (or detach with ``None``) a priority task queue.

        When a queue is attached, :meth:`dispatch_priority` will enqueue
        dispatch requests and :meth:`drain_pending` will execute them in
        priority order. The synchronous :meth:`dispatch` is unaffected.
        """
        self._priority_queue = queue

    @property
    def priority_queue(self) -> Any | None:
        """The currently attached priority queue (or ``None``)."""
        return self._priority_queue

    async def dispatch_priority(
        self,
        agent: str,
        task: str,
        *,
        routing_key: str = "",
        workdir: str = "",
        timeout_seconds: int | None = None,
        trace_id: str = "",
        streamer: Any | None = None,
        priority: int = 3,
        deadline_ms: int | None = None,
    ):
        """Enqueue a dispatch request with priority metadata (Phase γ-2).

        If no priority queue is attached (the default), this falls back to
        a direct :meth:`dispatch` call, preserving the original synchronous
        behaviour — so callers can switch to priority dispatch without
        branching on configuration.

        When a queue is attached, the request is wrapped in a
        :class:`~maop.core.priority_queue.PriorityTask` and pushed; the
        returned awaitable resolves to the :class:`DispatchResult` once
        :meth:`drain_pending` (or a worker loop) eventually executes it.

        Returns
        -------
        asyncio.Future | Awaitable[DispatchResult]
            A future that resolves to the DispatchResult.
        """
        if self._priority_queue is None:
            # Backward-compatible fallback: execute directly.
            return await self.dispatch(
                agent, task,
                routing_key=routing_key, workdir=workdir,
                timeout_seconds=timeout_seconds, trace_id=trace_id,
                streamer=streamer,
                priority=priority, deadline_ms=deadline_ms,
            )

        # Lazy import to avoid a hard import cycle in tests that stub
        # the queue with a duck-typed object.
        from maop.core.priority_queue import PriorityTask

        fut = asyncio.get_running_loop().create_future()
        pt = PriorityTask(
            payload={
                "agent": agent,
                "task": task,
                "routing_key": routing_key,
                "workdir": workdir,
                "timeout_seconds": timeout_seconds,
                "trace_id": trace_id,
                "streamer": streamer,
                "future": fut,
            },
            priority=priority,
            deadline_ms=deadline_ms,
        )
        self._priority_queue.push(pt)
        return await fut

    async def drain_pending(self, limit: int = 1) -> int:
        """Execute up to ``limit`` queued dispatch requests in priority order.

        Pops the highest-priority tasks from the attached queue and
        dispatches each via :meth:`dispatch`. The per-task
        :class:`asyncio.Future` stored in the payload is resolved with the
        :class:`DispatchResult` (or the exception on failure).

        Returns the number of tasks actually dispatched.

        No-op (returns 0) when no priority queue is attached.
        """
        if self._priority_queue is None:
            return 0
        dispatched = 0
        for _ in range(max(0, limit)):
            pt = self._priority_queue.pop()
            if pt is None:
                break
            payload = pt.payload or {}
            fut: asyncio.Future | None = payload.get("future")
            try:
                result = await self.dispatch(
                    payload.get("agent", ""),
                    payload.get("task", ""),
                    routing_key=payload.get("routing_key", ""),
                    workdir=payload.get("workdir", ""),
                    timeout_seconds=payload.get("timeout_seconds"),
                    trace_id=payload.get("trace_id", ""),
                    streamer=payload.get("streamer"),
                    priority=pt.priority,
                    deadline_ms=pt.deadline_ms,
                )
                if fut is not None and not fut.done():
                    fut.set_result(result)
            except Exception as exc:
                if fut is not None and not fut.done():
                    fut.set_exception(exc)
            dispatched += 1
        return dispatched

    def _record_soft_preemption_for_dispatch(
        self,
        incoming_priority: int,
        running_priorities: list[int],
    ) -> None:
        """Record a soft-preemption event for dispatcher-driven dispatch.

        Exposed as a helper so that callers managing their own worker pool
        can signal "a higher-priority dispatch arrived while lower-priority
        dispatches are in flight". Under soft preemption the running
        dispatches are not interrupted; the counter records demand only.
        """
        try:
            if not running_priorities:
                return
            if incoming_priority < min(running_priorities):
                MAOP_TASK_PREEMPTION_TOTAL.inc()
        except Exception as exc:
            # H10 fix (Phase R7): metrics 记录失败不应静默
            logger.debug("preemption metric record failed: %s", exc)

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

    def _notify_route_scorer(
        self,
        agent: str,
        *,
        success: bool,
        priority: int | None = None,
        deadline_ms: int | None = None,
    ) -> None:
        """Notify RouteScorer of agent success/failure for cooldown tracking.

        P0-3 fix: RouteScorer.cooldown was never populated because
        mark_agent_failed/mark_agent_success had no callers. Now invoked
        alongside circuit-breaker recording in _dispatch_impl.

        Phase γ-1: ``priority`` / ``deadline_ms`` are accepted to plumb
        the SLA parameter chain through to the RouteScorer call site.
        The current RouteScorer API does not yet consume these kwargs —
        they are reserved for a future SLA-aware cooldown policy
        (e.g. shorter cooldown for critical tasks).
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

    def _record_sla_dispatch_start(self, priority: int, sla_tier: str) -> None:
        """Record SLA metrics at task dispatch start (Phase γ-1).

        Increments the in-flight gauge for the task's priority level and
        SLA tier. Failures are non-blocking — metric recording must never
        prevent dispatch.
        """
        try:
            priority_label = str(priority)
            MAOP_TASK_PRIORITY_DISTRIBUTION.inc(labels={"priority": priority_label})
            MAOP_TASK_SLA_TIER_DISTRIBUTION.inc(labels={"tier": sla_tier})
        except Exception as exc:
            logger.debug("[dispatch] SLA start-metric record failed: %s", exc)

    def _record_sla_dispatch_end(
        self,
        priority: int,
        sla_tier: str,
        *,
        deadline_ms: int | None,
    ) -> None:
        """Record SLA metrics at task dispatch completion (Phase γ-1).

        Decrements the in-flight gauges incremented at start, and — when
        ``deadline_ms`` is set — checks whether the deadline was violated.
        On violation, increments ``MAOP_task_sla_violation_total`` and
        observes the (negative) remaining seconds in
        ``MAOP_task_deadline_seconds``.
        """
        try:
            priority_label = str(priority)
            MAOP_TASK_PRIORITY_DISTRIBUTION.dec(labels={"priority": priority_label})
            MAOP_TASK_SLA_TIER_DISTRIBUTION.dec(labels={"tier": sla_tier})

            if deadline_ms is not None:
                now_ms = int(__import__("time").time() * 1000)
                remaining_s = (deadline_ms - now_ms) / 1000.0
                if now_ms > deadline_ms:
                    MAOP_TASK_SLA_VIOLATION_TOTAL.inc()
                    MAOP_TASK_DEADLINE_SECONDS.observe(remaining_s)
                    logger.warning(
                        "SLA violation: deadline_ms=%d now_ms=%d remaining_s=%.3fs",
                        deadline_ms, now_ms, remaining_s,
                    )
        except Exception as exc:
            logger.debug("[dispatch] SLA end-metric record failed: %s", exc)

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
        priority: int = 3,
        deadline_ms: int | None = None,
    ) -> DispatchResult:
        """Dispatch a task to the specified agent.

        Steps
        -----
        1. Resolve agent config from YAML.
        2. Check circuit-breaker — reject if open.
        3. Execute via the appropriate driver.
        4. Record result in circuit-breaker.
        5. Return DispatchResult envelope.

        Phase γ-1 SLA parameters
        ------------------------
        priority : int
            Scheduling priority 1 (highest) .. 5 (lowest). Default 3 (normal).
        deadline_ms : int | None
            Absolute deadline timestamp in milliseconds since epoch.
            None means no explicit deadline (best-effort).

        Phase γ-4: the dispatch is wrapped in an outer
        ``routing.dispatcher.dispatch`` span (parent span for the
        routing decision chain) plus the existing inner ``dispatch.{agent}``
        span. A :class:`RoutingDecisionRecord` is persisted so the
        dashboard can explain the dispatch decision.
        """
        _start = time.monotonic()
        sla_tier = _tier_from_priority(priority)
        routing_tracer = get_tracer("maop.routing.dispatcher")
        with otel_span(
            routing_tracer, "routing.dispatcher.dispatch", trace_id=trace_id,
            attributes={
                "routing.agent": agent,
                "routing.routing_key": routing_key,
                "sla.priority": priority,
                "sla.tier": sla_tier,
                "sla.deadline_ms": deadline_ms or 0,
            },
        ) as _routing_span:
            tracer = get_tracer("maop.dispatch")
            with otel_span(tracer, f"dispatch.{agent}", trace_id=trace_id,
                           attributes={"agent": agent, "task": task[:80], "routing_key": routing_key,
                                       "sla.priority": priority, "sla.deadline_ms": deadline_ms or 0}):
                result = await self._dispatch_impl(agent, task, routing_key=routing_key,
                                                    workdir=workdir, timeout_seconds=timeout_seconds,
                                                    trace_id=trace_id, streamer=streamer,
                                                    priority=priority, deadline_ms=deadline_ms)

            # Phase γ-4: set span attributes + persist decision record.
            # The dispatcher span is the PARENT of the routing chain —
            # route_scorer / load_balancer / model_selector spans opened
            # inside _dispatch_impl (via ModelSelector.select_for_routing_key)
            # are children of the inner dispatch.{agent} span, which is
            # itself a child of this routing.dispatcher.dispatch span.
            selected_model = ""
            if self._effective_model is not None:
                selected_model = getattr(self._effective_model, "model_name", "") or ""
            try:
                _routing_span.set_attribute("routing.selected_agent", agent)
                _routing_span.set_attribute("routing.selected_model", selected_model)
                _routing_span.set_attribute("routing.sla_tier", sla_tier)
            except Exception as exc:
                # H10 fix (Phase R7): span 属性设置失败不应静默
                logger.debug("routing span attribute set failed: %s", exc)
            _record_dispatcher_decision(
                trace_id=trace_id, agent=agent, routing_key=routing_key,
                priority=priority, sla_tier=sla_tier, deadline_ms=deadline_ms,
                selected_model=selected_model,
                duration_ms=(time.monotonic() - _start) * 1000.0,
            )
            return result

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
        priority: int = 3,
        deadline_ms: int | None = None,
    ) -> DispatchResult:
        # Phase γ-1: derive SLA tier, log SLA context, and record
        # in-flight gauges. The finally block at the end of this method
        # decrements the gauges and checks for deadline violation.
        sla_tier = _tier_from_priority(priority)
        self._record_sla_dispatch_start(priority, sla_tier)
        logger.info(
            "SLA dispatch: agent=%s priority=%d sla_tier=%s deadline_ms=%s trace_id=%s",
            agent, priority, sla_tier, deadline_ms, trace_id,
            extra={
                "sla_priority": priority,
                "sla_deadline_ms": deadline_ms if deadline_ms is not None else 0,
                "sla_tier": sla_tier,
            },
        )

        try:
            return await self._dispatch_impl_inner(
                agent, task,
                routing_key=routing_key, workdir=workdir,
                timeout_seconds=timeout_seconds, trace_id=trace_id,
                streamer=streamer,
                priority=priority, deadline_ms=deadline_ms,
                sla_tier=sla_tier,
            )
        finally:
            self._record_sla_dispatch_end(priority, sla_tier, deadline_ms=deadline_ms)

    async def _dispatch_impl_inner(
        self,
        agent: str,
        task: str,
        *,
        routing_key: str = "",
        workdir: str = "",
        timeout_seconds: int | None = None,
        trace_id: str = "",
        streamer: Any | None = None,
        priority: int = 3,
        deadline_ms: int | None = None,
        sla_tier: str = "standard",
    ) -> DispatchResult:
        """Inner dispatch implementation (Phase γ-1).

        Split out from ``_dispatch_impl`` so the outer method can wrap
        the call in a ``try/finally`` for SLA metric cleanup. SLA params
        are in scope at every call site that touches LoadBalancer /
        RouteScorer — see ``lb.record_start`` below and
        ``_notify_route_scorer`` calls for the plumbed parameter chain.
        """
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
        except Exception as exc:
            # Fail-closed: any guardrail crash (init/check) must NOT propagate;
            # return a blocked result instead of letting dispatch crash.
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
                # Phase γ-1: propagate SLA context to the failover attempt so
                # its in-flight gauges + violation check are recorded too.
                return await self._dispatch_impl(
                    failover.agent, task,
                    routing_key=routing_key, workdir=workdir,
                    timeout_seconds=timeout_seconds, trace_id=trace_id,
                    streamer=streamer,
                    priority=priority, deadline_ms=deadline_ms,
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
            self._notify_route_scorer(
                agent, success=False, priority=priority, deadline_ms=deadline_ms,
            )
            return DispatchResult(result=result, breaker_tripped=False)

        # 4. Execute via driver — wrap in try/except to ensure failures are recorded
        # in circuit-breaker and route scorer cooldown (P0-3 + P1-7 fix)
        _dispatch_start = time.monotonic()
        # P2-7 fix: record task start to LoadBalancer for adaptive scoring.
        # Phase γ-1: SLA context (priority, deadline_ms) is in scope here
        # for a future LoadBalancer enhancement that weights SLA when
        # computing agent load. The current lb.record_start API does not
        # yet consume these kwargs — the parameter chain is plumbed so
        # the LB call site can use them once it accepts them.
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
                    duration_ms=(time.monotonic() - _dispatch_start) * 1000,
                    success=result.is_success(),
                )
        except Exception as exc:
            logger.debug("LoadBalancer record failed: %s", exc)

        # 5. Record in circuit-breaker and route scorer cooldown
        if result.is_success():
            # P1-16 fix: use async breaker methods to avoid blocking event loop
            await self._breaker.arecord_success(agent)
            self._notify_route_scorer(
                agent, success=True, priority=priority, deadline_ms=deadline_ms,
            )
        else:
            await self._breaker.arecord_failure(agent)
            self._notify_route_scorer(
                agent, success=False, priority=priority, deadline_ms=deadline_ms,
            )

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


# ── Phase γ-4: decision-record helper ─────────────────────────


def _record_dispatcher_decision(
    *,
    trace_id: str,
    agent: str,
    routing_key: str,
    priority: int,
    sla_tier: str,
    deadline_ms: int | None,
    selected_model: str,
    duration_ms: float,
) -> None:
    """Persist a :class:`RoutingDecisionRecord` for ``Dispatcher.dispatch``.

    The dispatcher is the PARENT of the routing decision chain — its
    record is the entry point for reconstructing the full Plan → Route
    → LB → ModelSelect trace via ``query_by_trace(trace_id)``.
    """
    otel_trace_id, span_id, parent_span_id = get_active_span_context()
    effective_trace = trace_id or otel_trace_id

    deadline_note = f"deadline={deadline_ms}ms" if deadline_ms else "deadline=none"
    model_note = f", model='{selected_model}'" if selected_model else ""
    explanation = (
        f"Dispatched to agent '{agent}' with priority={priority} "
        f"({sla_tier}), {deadline_note}{model_note}."
    )

    try:
        MAOP_ROUTING_DECISION_TOTAL.inc(labels={"stage": "dispatcher"})
        MAOP_ROUTING_DECISION_DURATION_MS.observe(duration_ms)
    except Exception as exc:
        # H10 fix (Phase R7): routing metric 记录失败不应静默
        logger.debug("routing decision metric record failed: %s", exc)

    record_decision_safe(RoutingDecisionRecord(
        trace_id=effective_trace,
        span_id=span_id,
        parent_span_id=parent_span_id,
        timestamp=time.time(),
        stage="dispatcher",
        input_summary={
            "agent": agent,
            "routing_key": routing_key,
            "priority": priority,
            "sla_tier": sla_tier,
            "deadline_ms": deadline_ms,
        },
        output_summary={
            "selected_agent": agent,
            "selected_model": selected_model,
        },
        explanation=explanation,
        duration_ms=duration_ms,
        attributes={
            "priority": priority,
            "sla_tier": sla_tier,
            "deadline_ms": deadline_ms or 0,
            "selected_agent": agent,
            "selected_model": selected_model,
        },
    ))
