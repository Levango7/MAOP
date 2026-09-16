"""Core dispatch implementation mixin for :class:`Dispatcher`.

Split out from ``dispatch_core.py`` for maintainability (mixin pattern).
Houses the dispatcher initialisation, configuration accessors, the SLA
wrapping :meth:`_dispatch_impl`, sub-agent delegation, and the
:func:`_record_dispatcher_decision` helper.

- :meth:`__init__` — config-driven initialisation
- :meth:`effective_model` / :meth:`clear_agent_cache` — accessors
- :meth:`set_priority_queue` / :meth:`priority_queue` — queue integration
- :meth:`match_agent` — capability-based agent matching
- :meth:`_dispatch_impl` — SLA-wrapping dispatch entry
- :meth:`delegate_to_subagent` — recursive sub-agent delegation
- :func:`_record_dispatcher_decision` — routing decision record helper

The logger name is pinned to ``maop.delegate.dispatcher`` to preserve log
output exactly as before the split.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from maop.core.monitoring.monitoring import (
    MAOP_ROUTING_DECISION_DURATION_MS,
    MAOP_ROUTING_DECISION_TOTAL,
)
from maop.core.reliability.circuit_breaker import CircuitBreaker
from maop.core.reliability.error_schema import new_result
from maop.core.routing.routing_decision import (
    RoutingDecisionRecord,
    get_active_span_context,
    record_decision_safe,
)
from maop.delegate.agent_resolver import AgentResolver
from maop.delegate.models import (
    AgentConfig,
    DispatchResult,
)
from maop.delegate.sla_monitor import SLAMonitor

# Pinned to the original module name so log records are identical to the
# pre-split behaviour (tests / dashboards key on this logger name).
logger = logging.getLogger("maop.delegate.dispatcher")

# P3-fix: 常量提升——将魔法数字/字符串提为模块级常量，便于统一维护与调优。
_DEFAULT_DISPATCH_CONCURRENCY: int = 10  # 默认并发限制（settings 加载失败时降级使用）
_DEFAULT_MAX_SUBAGENT_DEPTH: int = 5     # 子代理递归委派最大深度


class DispatchImplMixin:
    """Core dispatch implementation & configuration helpers (mixin).

    Provides ``__init__`` and the configuration / SLA-wrapping helpers.
    Expects the host class to provide (via other mixins or itself):
        - ``self._dispatch_impl_inner`` — inner dispatch coroutine
        - ``self.dispatch`` — synchronous dispatch coroutine
    """

    def __init__(
        self,
        maop_config: Any | None = None,
        breaker: CircuitBreaker | None = None,
        model_selector: Any | None = None,
        root_dir: str | None = None,
        *,
        registry: Any | None = None,
        capability_matcher: Any | None = None,
        priority_queue: Any | None = None,
        # P3-4 fix: 保留 MAOP_config 作为向后兼容的别名（PEP8 违规参数名）。
        MAOP_config: Any | None = None,
    ) -> None:
        # P3-4 fix: 优先使用 PEP8 合规的 maop_config，回退到旧 MAOP_config 别名。
        config = maop_config if maop_config is not None else MAOP_config
        self._config = config
        self._breaker = breaker or CircuitBreaker()
        self._model_selector = model_selector
        self._effective_model: Any | None = None
        self._root_dir = root_dir
        self._subagent_mgr = None
        self._registry = registry
        self._matcher = capability_matcher
        # Delegated subsystems (N2 refactor)
        self._resolver = AgentResolver(
            config, root_dir,
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
        # P1-fix: get_settings 异常保护——配置文件损坏/加载失败时降级到默认并发值，
        # 而非让原始异常向上传播导致整个 dispatch 崩溃。
        try:
            from maop.config.settings import get_settings
            _concurrency = get_settings().dispatch_concurrency
        except Exception as exc:
            logger.warning(
                "[dispatch] get_settings 失败，降级到默认并发值 %d: %s",
                _DEFAULT_DISPATCH_CONCURRENCY, exc,
            )
            _concurrency = _DEFAULT_DISPATCH_CONCURRENCY
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

    def match_agent(self, task: str, requirements: list[str] | None = None) -> AgentConfig | None:
        """Use CapabilityMatcher to find the best agent for a task (delegates to AgentResolver)."""
        return self._resolver.match_agent(task, requirements)

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
        _failover_depth: int = 0,
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
                _failover_depth=_failover_depth,
            )
        finally:
            self._record_sla_dispatch_end(priority, sla_tier, deadline_ms=deadline_ms)

    async def delegate_to_subagent(
        self,
        parent: str,
        agent: str,
        task: str,
        *,
        routing_key: str = "",
        trace_id: str = "",
        max_depth: int = _DEFAULT_MAX_SUBAGENT_DEPTH,  # P3-fix: 使用模块级常量
    ) -> DispatchResult:
        """Spawn a sub-agent and dispatch the task through it.

        This enables recursive delegation: agent A -> agent B -> agent C,
        with depth tracking to prevent infinite recursion.
        """
        # Resolve lazy-subsystem helper through the dispatcher namespace so
        # that ``patch("maop.delegate.dispatcher._get_subagent_manager")`` in
        # tests takes effect (call syntax unchanged from pre-split code).
        from maop.delegate.dispatcher import _get_subagent_manager

        if self._subagent_mgr is None:
            self._subagent_mgr = _get_subagent_manager(self._root_dir)
        if self._subagent_mgr is None:
            result = new_result(
                agent=agent, task=task,
                exit_code=-2, error="SubAgentManager not available",
                trace_id=trace_id, routing_key=routing_key,
            )
            return DispatchResult(result=result, breaker_tripped=False)

        sa_info = self._subagent_mgr.spawn_child(
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