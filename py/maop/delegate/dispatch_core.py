"""MAOP Delegate Dispatcher — Core Dispatcher class & decision record.

Split out from ``dispatcher.py`` for maintainability. This module hosts
the :class:`Dispatcher` class (config-driven agent dispatch with
circuit-breaker protection). The class is composed from three mixins:

- :class:`DispatchPriorityMixin` (dispatch_priority.py) — priority queue
- :class:`DispatchRecordingMixin` (dispatch_recording.py) — SLA & perf
- :class:`DispatchImplMixin` (dispatch_impl.py) — init, config, _dispatch_impl

This module retains :meth:`dispatch` and :meth:`_dispatch_impl_inner` (the
two largest methods) because tests patch ``otel_span`` on this module and
check source for ``CostTracker`` — moving them would break those contracts.

The :func:`_record_dispatcher_decision` helper is re-exported from
``dispatch_impl.py`` so that ``from maop.delegate.dispatch_core import
Dispatcher, _record_dispatcher_decision`` continues to work.

These symbols are re-exported from ``maop.delegate.dispatcher`` so that
existing callers (``from maop.delegate.dispatcher import Dispatcher``)
continue to work without changes.

Implementation is unchanged — only the module location has moved. The
logger name is pinned to ``maop.delegate.dispatcher`` to preserve log
output exactly as before the split.
"""

from __future__ import annotations

import logging
import time
from typing import Any

from maop.core.monitoring.otel import get_tracer
from maop.core.monitoring.otel import span as otel_span
from maop.core.reliability.error_schema import new_result
from maop.delegate.drivers import DRIVERS as _DRIVERS
from maop.delegate.models import DispatchResult
from maop.delegate.dispatch_priority import DispatchPriorityMixin
from maop.delegate.dispatch_recording import DispatchRecordingMixin
from maop.delegate.dispatch_impl import (
    DispatchImplMixin,
    _record_dispatcher_decision,
)

# NOTE: ``_get_load_balancer`` / ``_get_subagent_manager`` are NOT imported
# at module scope here. They are re-exported by ``dispatcher.py`` and tests
# patch them via ``patch("maop.delegate.dispatcher._get_subagent_manager")``.
# For those patches to take effect inside Dispatcher methods, the helpers
# must be resolved through the ``maop.delegate.dispatcher`` namespace at
# call time — hence the in-method lazy imports below (call syntax unchanged).

# Pinned to the original module name so log records are identical to the
# pre-split behaviour (tests / dashboards key on this logger name).
logger = logging.getLogger("maop.delegate.dispatcher")

# P3-fix: _DEFAULT_PRIORITY retained for backward compatibility; the other
# constants now live in dispatch_impl.py next to their consumers.
_DEFAULT_PRIORITY: int = 3               # 默认调度优先级（1=最高, 5=最低）


# ── Dispatcher ────────────────────────────────────────────────

class Dispatcher(DispatchPriorityMixin, DispatchRecordingMixin, DispatchImplMixin):
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
        # H8 修复：记录委派总数指标
        try:
            from maop.core.monitoring.monitoring import MAOP_DELEGATIONS_TOTAL

            MAOP_DELEGATIONS_TOTAL.inc()
        except Exception:
            # 指标更新失败不应影响业务逻辑
            logger.debug("inc MAOP_DELEGATIONS_TOTAL failed", exc_info=True)
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
            # C1 fix: 主路径记录 agent 执行性能数据（此前 record() 从不被调用，
            # 自适应路由/演化分析的 agent_performance 表长期为空）
            self._record_agent_performance(agent, routing_key, result)
            # H8 修复：记录委派耗时指标
            try:
                from maop.core.monitoring.monitoring import (
                    MAOP_DELEGATION_DURATION,
                )

                MAOP_DELEGATION_DURATION.observe(time.monotonic() - _start)
            except Exception:
                # 指标更新失败不应影响业务逻辑
                logger.debug("observe MAOP_DELEGATION_DURATION failed", exc_info=True)
            return result


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
        _failover_depth: int = 0,
    ) -> DispatchResult:
        """Inner dispatch implementation (Phase γ-1).

        Split out from ``_dispatch_impl`` so the outer method can wrap
        the call in a ``try/finally`` for SLA metric cleanup. SLA params
        are in scope for LoadBalancer SLA recording — see ``lb.record_start``
        below.
        """
        # Resolve lazy-subsystem helper through the dispatcher namespace so
        # that ``patch("maop.delegate.dispatcher._get_load_balancer")`` in
        # tests takes effect (call syntax unchanged from pre-split code).
        from maop.delegate.dispatcher import _get_load_balancer

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
        # P2-fix: guardrail 参数名统一为 `guardrail`（与 guardrail.py 模块名一致），
        # 调用 gr.check() 的关键字参数 content/agent/task 与 Guardrail.check 签名完全匹配。
        try:
            from maop.core.security.guardrail import Guardrail
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
            # P1-11 fix: failover 深度限制，防止递归栈溢出/无限循环
            _MAX_FAILOVER_DEPTH = 3
            if _failover_depth >= _MAX_FAILOVER_DEPTH:
                logger.warning(
                    "[dispatch] Failover depth %d >= max %d for '%s' — giving up",
                    _failover_depth, _MAX_FAILOVER_DEPTH, agent,
                )
                result = new_result(
                    agent=agent, task=task,
                    exit_code=-3,
                    error=f"Circuit breaker OPEN for '{agent}' and failover depth limit reached ({_failover_depth})",
                    trace_id=trace_id, routing_key=routing_key,
                )
                return DispatchResult(result=result, breaker_tripped=True)
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
                    _failover_depth=_failover_depth + 1,  # P1-11 fix: 增加深度
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
            # P0-3 止血（remediation-plan-v5.1.0）：原实现读 JSON
            # budget_ledger.json，但该账本已无写入方（maop_loop 迁移至
            # CostTracker SQLite，model/budget.py docstring 自述废弃）→ 花费恒 0
            # → 准入拦截静默失效（对外宣称的预算管控实际永远放行）。
            # 改用 CostTracker 聚合 SQLite cost_entries 当日/当月真实花费对比
            # 限额（与 maop_loop 同一 get_db_path("cost_tracker") 库）。
            # 限额仍取自 self._config.budget（BudgetConfig），与原实现同配置源。
            from maop.core.cost_tracker import CostTracker
            from maop.model.schema import BudgetConfig
            _bg_config = getattr(self._config, "budget", None) or BudgetConfig()
            if _bg_config.hard_stop:
                _tracker = CostTracker(
                    root_dir=self._root_dir,
                    daily_limit_usd=_bg_config.daily_limit,
                    monthly_limit_usd=_bg_config.monthly_limit,
                    alert_threshold=_bg_config.alert_threshold,
                )
                _budget_status = _tracker.budget_status()
                if _budget_status.daily_over_budget or _budget_status.monthly_over_budget:
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
            self._notify_route_scorer(agent, success=True)
            # H8 修复：记录委派成功指标
            try:
                from maop.core.monitoring.monitoring import MAOP_DELEGATIONS_SUCCESS

                MAOP_DELEGATIONS_SUCCESS.inc()
            except Exception:
                # 指标更新失败不应影响业务逻辑
                logger.debug("inc MAOP_DELEGATIONS_SUCCESS failed", exc_info=True)
        else:
            await self._breaker.arecord_failure(agent)
            self._notify_route_scorer(agent, success=False)
            # H8 修复：记录委派失败指标
            try:
                from maop.core.monitoring.monitoring import MAOP_DELEGATIONS_FAILED

                MAOP_DELEGATIONS_FAILED.inc()
            except Exception:
                # 指标更新失败不应影响业务逻辑
                logger.debug("inc MAOP_DELEGATIONS_FAILED failed", exc_info=True)


        return DispatchResult(
            result=result,
            driver_used=config.driver,
            breaker_tripped=False,
            model_resolved=model_resolved,
        )
