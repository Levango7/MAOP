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
)
from maop.core.otel import get_tracer
from maop.core.otel import span as otel_span
from maop.core.routing_decision import (
    RoutingDecisionRecord,
    get_active_span_context,
    record_decision_safe,
)
from maop.delegate.agent_resolver import AgentResolver
from maop.delegate.drivers import DRIVERS as _DRIVERS

# Re-export for backward compatibility — callers importing from
# MAOP.delegate.dispatcher still get these symbols.
from maop.delegate.models import (  # noqa: F401
    AgentConfig,
    DispatchResult,
    _escape_for_cmd,
    _escape_for_ps_command,
)
from maop.delegate.sla_monitor import SLAMonitor

logger = logging.getLogger(__name__)


# ── Retry helpers (P2 fix: exponential backoff) ─────────────────

async def _retry_with_backoff(
    coro_factory,
    *,
    max_retries: int = 3,
    base_delay_ms: int = 500,
    retryable_exceptions: tuple = (Exception,),
) -> Any:
    """Execute an async operation with exponential backoff retry.

    Args:
        coro_factory: A callable that returns a coroutine to execute.
        max_retries: Maximum number of retry attempts.
        base_delay_ms: Base delay in milliseconds (doubles each retry).
        retryable_exceptions: Tuple of exception types that trigger retry.

    Returns:
        The result of the coroutine.

    Raises:
        The last exception if all retries fail.
    """
    last_exc = None
    for attempt in range(max_retries + 1):
        try:
            return await coro_factory()
        except retryable_exceptions as exc:
            last_exc = exc
            if attempt < max_retries:
                delay_s = (base_delay_ms * (2 ** attempt)) / 1000.0
                logger.warning(
                    "[dispatch] Attempt %d/%d failed: %s. Retrying in %.1fs...",
                    attempt + 1, max_retries + 1, exc, delay_s,
                )
                await asyncio.sleep(delay_s)
            else:
                logger.error(
                    "[dispatch] All %d attempts failed. Last error: %s",
                    max_retries + 1, exc,
                )
    raise last_exc  # type: ignore[misc]


# ── Optional subsystems (lazy import to avoid hard deps) ──────

def _get_load_balancer():
    """Lazy import LoadBalancer."""
    try:
        from maop.core.load_balancer import get_load_balancer
        return get_load_balancer()
    except ImportError:
        return None
    except Exception:
        # P2-6 fix: upgrade to error — runtime init failures should be visible
        logger.exception("Failed to load driver LoadBalancer")
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
    except Exception:
        logger.exception("Failed to load driver Runtime")
        return None

def _get_sandbox_manager(root_dir=None):
    """Lazy import SandboxManager."""
    try:
        from maop.core.sandbox import SandboxManager
        return SandboxManager(root_dir=root_dir)
    except ImportError:
        return None
    except Exception:
        logger.exception("Failed to load driver SandboxManager")
        return None

def _get_subagent_manager(root_dir=None):
    """Lazy import SubagentManager."""
    try:
        from maop.core.subagent_delegation import SubagentManager
        return SubagentManager(root_dir=root_dir)
    except ImportError:
        return None
    except Exception:
        logger.exception("Failed to load driver SubagentManager")
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
        self._model_selector = model_selector
        self._effective_model: Any | None = None
        self._root_dir = root_dir
        self._subagent_mgr = None
        self._registry = registry
        self._matcher = capability_matcher
        # Delegated subsystems (N2 refactor)
        self._resolver = AgentResolver(
            MAOP_config, root_dir,
            registry=registry, capability_matcher=capability_matcher,
        )
        self._sla = SLAMonitor()
        # Phase γ-2: optional priority queue for priority-aware dispatch.
        # When None (default), dispatch() executes synchronously as before.
        # When set, dispatch_priority() enqueues and drain_pending() pops in
        # priority order. Kept optional to preserve backward compatibility.
        self._priority_queue = priority_queue
        # P2 fix: global concurrency limiter to prevent overwhelming downstream LLM APIs.
        # Uses settings.dispatch_concurrency (env: MAOP_DISPATCH_CONCURRENCY, default: 10).
        from maop.config.settings import get_settings
        _concurrency = get_settings().dispatch_concurrency
        self._semaphore = asyncio.Semaphore(_concurrency)

    @property
    def effective_model(self) -> Any | None:
        """Return the last resolved EffectiveModel (for audit/logging)."""
        return self._effective_model

    def clear_agent_cache(self) -> None:
        """Clear the agent config cache (call after config reload)."""
        self._resolver.clear_cache()

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

        Delegates to :class:`SLAMonitor` (N2 refactor). Exposed as a helper
        so that callers managing their own worker pool can signal "a
        higher-priority dispatch arrived while lower-priority dispatches
        are in flight". Under soft preemption the running dispatches are
        not interrupted; the counter records demand only.
        """
        self._sla.record_preemption(incoming_priority, running_priorities)

    def _resolve_agent(self, agent_name: str) -> AgentConfig | None:
        """Resolve agent config by name (delegates to AgentResolver)."""
        return self._resolver.resolve(agent_name)


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
        """Record SLA metrics at task dispatch start (delegates to SLAMonitor)."""
        self._sla.record_start(priority, sla_tier)

    def _record_sla_dispatch_end(
        self,
        priority: int,
        sla_tier: str,
        *,
        deadline_ms: int | None,
    ) -> None:
        """Record SLA metrics at task dispatch completion (delegates to SLAMonitor)."""
        self._sla.record_end(priority, sla_tier, deadline_ms=deadline_ms)



    def match_agent(self, task: str, requirements: list[str] | None = None) -> AgentConfig | None:
        """Use CapabilityMatcher to find the best agent for a task (delegates to AgentResolver)."""
        return self._resolver.match_agent(task, requirements)

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
        sla_tier = self._sla.tier_from_priority(priority)
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
        sla_tier = self._sla.tier_from_priority(priority)
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
            # P2 fix: acquire semaphore to limit concurrent dispatches
            async with self._semaphore:
                result = await driver_fn(config, task, timeout, workdir, trace_id, streamer=streamer)
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
