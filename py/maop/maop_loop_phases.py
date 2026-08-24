"""MAOP Loop phases — Plan/Execute/Verify/Feedback/Evolve phase methods.

Extracted from maop_loop.py for single-responsibility separation. Provides a
``PhasesMixin`` that ``MaopLoop`` inherits to gain the per-phase methods
(``_phase_analyze``, ``_phase_plan``, ``_phase_execute``, ``_phase_verify``,
``_phase_feedback``, ``_phase_evolve``) plus their helpers
(``_inject_memory``, ``_budget_reconciliation``, ``_build_loop_result``,
``_simple_analyze``, ``_plan``, ``_verify``, ``_build_fallback_chain``).

The host ``MaopLoop`` class supplies all subsystem attributes set up in
``__init__`` (``self._loop_config``, ``self._memory``, ``self._bus``, ...).
The mixin only declares them for type-checkers; runtime behavior is
identical to the original monolithic class.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import time
from typing import Any, cast

from maop.core.agent.analyzer import ExecutionStrategy
from maop.core.agent.analyzer import analyze as requirement_analyze
from maop.core.agent.evolution.phases import PhaseContext, PhaseResult
from maop.core.monitoring.otel import get_tracer
from maop.core.monitoring.otel import setup_provider as otel_setup
from maop.core.reliability.error_schema import MaopResult
from maop.core.reliability.event_bus import Event
from maop.loop_analyzer import simple_analyze
from maop.loop_models import LoopConfig, LoopResult, RequirementAnalysis
from maop.maop_plan import maop_plan
from maop.maop_verify import VerifyResult

logger = logging.getLogger(__name__)

_otel_tracer = None

# Hold strong references to background asyncio tasks so they are not
# garbage-collected before completion ("Task was destroyed but it is pending").
_bg_tasks: set[asyncio.Task[Any]] = set()


def _get_otel_tracer():
    global _otel_tracer
    if _otel_tracer is None:
        otel_setup()
        _otel_tracer = get_tracer("maop.loop")
    return _otel_tracer


class PhasesMixin:
    """Mixin providing the per-phase orchestration methods for MaopLoop.

    Requires the host class to have (all set in MaopLoop.__init__):
      - self._root, self._config, self._loop_config, self._bus
      - self._memory, self._load_balancer, self._worker_pool
      - self._result_cache, self._verify_engine, self._consolidator
      - self._metrics + self._metric_* histogram/counter handles
      - self._loop_count
      - self._log(phase, level, message, **data)
      - self._record_metric(name, value, tags=None)
      - self.llm_factory  (property)
      - self._execute_with_strategy(...) / self._execute_with_retry(...)
        (provided by ExecuteMixin)
    """

    # ── Host attribute declarations (set by MaopLoop.__init__) ───
    _root: Any
    _config: Any
    _loop_config: LoopConfig
    _bus: Any
    _memory: Any
    _load_balancer: Any
    _worker_pool: Any
    _result_cache: Any
    _verify_engine: Any
    _consolidator: Any
    _metrics: Any
    _metric_plan_duration: Any
    _metric_exec_duration: Any
    _metric_verify_duration: Any
    _metric_loop_duration: Any
    _metric_tasks_total: Any
    _metric_tasks_success: Any
    _loop_count: int
    _log: Any
    _record_metric: Any
    llm_factory: Any

    @staticmethod
    def _should_execute_parallel(analysis, worker_pool):
        """NOTE: mirrors LoopExecutor._should_execute_parallel in loop_executor.py.

        P3 fix: 提取并行判定条件为单一辅助方法，消除与
        ``ExecuteMixin._execute_with_strategy`` 中重复的 4 项合取条件，
        避免日后修改一处漏改另一处导致行为分叉。
        """
        return (analysis is not None
                and analysis.strategy in (ExecutionStrategy.PARALLEL, ExecutionStrategy.HYBRID)
                and analysis.sub_tasks and len(analysis.sub_tasks) > 1
                and worker_pool is not None)

    def _inject_memory(self, ctx: PhaseContext, parent_trace_id: str) -> None:
        """Inject memory context and record trace."""
        lc = self._loop_config
        if lc.enable_memory_inject and self._memory is not None:
            try:
                context = self._memory.inject(ctx.trace_id)
                if not context:
                    results = self._memory.search(query=ctx.original_task, top=3)
                    if results:
                        lines = ["[Memory Context]"]
                        for r in results:
                            lines.append(f"  {r.agent}: {r.task} >> {r.snippet[:100]}")
                        context = "\n".join(lines)
                if context:
                    ctx.task = f"{ctx.task}\n\n{context}"
                    logger.info("[inject] Memory context appended to task")
            except Exception as exc:
                logger.warning("[MAOP-loop] Memory injection failed: %s", exc)
        try:
            if self._memory is not None:
                self._memory.trace(
                    trace_id=ctx.trace_id, parent_trace_id=parent_trace_id,
                    task=ctx.original_task, agent="MAOP-loop",
                )
        except Exception as exc:
            logger.debug("[MAOP-loop] Trace recording failed: %s", exc)

    async def _phase_analyze(self, ctx: PhaseContext, skip_verify: bool) -> PhaseResult:
        """Phase 0: Analyze — semantic decomposition + guardrail + routing.

        G1c (2026-07-22, Phase G): the simple-analyze branch now ``await``s
        ``simple_analyze`` and forwards LLM-related kwargs so that, when
        ``LoopConfig.enable_llm_analyze`` is True, the LLM-first semantic
        extraction path is invoked (ADR-013 dual-path). When the flag is
        False (default), behavior is identical to the prior synchronous
        rule-based call — ``simple_analyze`` returns immediately without
        touching the LLM factory.
        """
        _otel = _get_otel_tracer()

        lc = self._loop_config
        # P3 fix: 原表达式 (skip_verify and lc.skip_analyze) or lc.skip_analyze
        # 按布尔吸收律 A∧B ∨ A ≡ A，化简为 lc.skip_analyze。
        if lc.skip_analyze:
            return PhaseResult(ok=True)

        self._log("analyze", "INFO", "Analyze phase started", trace_id=ctx.trace_id)

        if lc.enable_semantic_analyze:
            ctx.analysis_result = await requirement_analyze(
                task=ctx.original_task,
                config=self._config,
                max_subtasks=lc.max_subtasks,
                llm_factory=self.llm_factory,
                model_name=lc.llm_analyze_model,
                enable_llm=lc.enable_llm_analyze,
            )
            ctx.analysis_dict = ctx.analysis_result.model_dump()
            self._log("analyze", "INFO",
                       f"Semantic analysis: {len(ctx.analysis_result.sub_tasks)} subtasks, "
                       f"complexity={ctx.analysis_result.complexity_level.value}, "
                       f"strategy={ctx.analysis_result.strategy.value}, "
                       f"score={ctx.analysis_result.complexity_score}",
                       trace_id=ctx.trace_id)
            self._record_metric("analysis_complexity", ctx.analysis_result.complexity_score,
                                {"task_hash": ctx.analysis_result.task_hash})
        else:
            simple_result = await simple_analyze(
                ctx.original_task,
                llm_factory=self.llm_factory,
                model_name=lc.llm_analyze_model,
                enable_llm=lc.enable_llm_analyze,
            )
            ctx.analysis_dict = simple_result.model_dump()

        try:
            loop = asyncio.get_running_loop()
            _t = loop.create_task(self._bus.publish(Event(topic="loop.analyze", data={
                "trace_id": ctx.trace_id, "analysis": ctx.analysis_dict,
            })))
            _bg_tasks.add(_t)
            _t.add_done_callback(_bg_tasks.discard)
        except RuntimeError as exc:
            logger.debug("maop_loop: no running loop for loop.analyze publish: %s", exc)

        return PhaseResult(ok=True)

    async def _phase_plan(self, ctx: PhaseContext, workdir: str) -> PhaseResult:
        """Phase 1: Plan — plan generation + load balancer + fallback chain.

        F6a (2026-07-22, Phase F): when the caller pinned an agent via
        ``MaopLoop.run(agent=...)`` (recorded as ``ctx.forced_agent``),
        skip the plan-based ``selected_agent`` lookup and the
        load_balancer override. This makes A2A-dispatched tasks actually
        execute with the requested agent. See ADR-013.
        """
        lc = self._loop_config
        plan_start = time.monotonic()
        self._log("plan", "INFO", "Plan phase started", trace_id=ctx.trace_id)

        ctx.plan_result = await self._plan(ctx.task, workdir)
        # F6a: respect caller-pinned agent — only select from plan_result
        # when no explicit agent was provided to run().
        if not ctx.forced_agent:
            ctx.agent = ctx.plan_result.get("selected_agent", "claude")
        ctx.routing_key = ctx.plan_result.get("routing_key", "chat")
        ctx.timeout = ctx.plan_result.get("budget", {}).get("timeout_s", lc.default_timeout_s)

        # F6a: load_balancer may refine the selection, but only when the
        # caller did not explicitly pin an agent.
        if not ctx.forced_agent and self._load_balancer and ctx.analysis_result and ctx.analysis_result.suggested_agents:
            try:
                lb_agent = self._load_balancer.select(
                    candidates=ctx.analysis_result.suggested_agents,
                    routing_key=ctx.routing_key,
                )
                if lb_agent:
                    ctx.agent = lb_agent
                    self._log("plan", "INFO", f"LoadBalancer selected: {lb_agent}",
                              trace_id=ctx.trace_id)
            except Exception as exc:
                logger.warning("LoadBalancer selection failed: %s", exc)

        ctx.fallback_chain = self._build_fallback_chain(ctx.agent, ctx.routing_key)

        plan_duration = int((time.monotonic() - plan_start) * 1000)
        if self._metrics:
            self._metric_plan_duration.observe(plan_duration)
        self._record_metric("plan_duration_ms", plan_duration)

        await self._bus.publish(Event(topic="loop.plan", data={
            "trace_id": ctx.trace_id, "agent": ctx.agent,
            "routing_key": ctx.routing_key, "fallback": ctx.fallback_chain,
        }))

        return PhaseResult(ok=True)

    async def _phase_execute(self, ctx: PhaseContext, workdir: str, retry: bool) -> PhaseResult:
        """Phase 2: Execute — run task with strategy, caching, and budget."""
        exec_start = time.monotonic()
        self._log("execute", "INFO", "Execute phase started", trace_id=ctx.trace_id)

        # P2 fix: 用 SHA256 哈希替代 original_task[:200] 截断，避免前 200 字符
        # 相同但后续不同的任务发生缓存 key 碰撞导致结果串用。
        cache_key = f"{ctx.agent}:{ctx.routing_key}:{hashlib.sha256(ctx.original_task.encode('utf-8', errors='replace')).hexdigest()[:16]}"
        # P2-5 fix: skip cache for side-effecting routing keys (write/run/exec/shell)
        # to prevent silent dedup of tasks that modify state
        _side_effect_keys = ("codegen", "review", "verify", "run", "exec", "shell", "deploy", "migrate", "fix", "refactor")
        cache_enabled = self._result_cache and not any(k in ctx.routing_key.lower() for k in _side_effect_keys)
        exec_result = None
        if cache_enabled:
            try:
                cached = self._result_cache.get(cache_key)  # type: ignore
                if cached is not None:
                    self._log("execute", "INFO", "Cache hit", trace_id=ctx.trace_id)
                    exec_result = cached
            except Exception as exc:
                # H10 fix (Phase R7): 缓存读取失败不应静默，记录警告
                logger.warning("cache lookup failed: %s", exc, exc_info=True)

        ctx.parallel_executed = False
        if exec_result is None:
            is_parallel = self._should_execute_parallel(ctx.analysis_result, self._worker_pool)
            exec_result = await self._execute_with_strategy(
                task=ctx.task, analysis=ctx.analysis_result,
                fallback_chain=ctx.fallback_chain, routing_key=ctx.routing_key,
                workdir=workdir, timeout=int(ctx.timeout), retry=retry,
                trace_id=ctx.trace_id,
            )
            if is_parallel:
                ctx.parallel_executed = True

            if cache_enabled and exec_result and exec_result.is_success():
                try:
                    self._result_cache.put(cache_key, exec_result)  # type: ignore
                except Exception as exc:
                    logger.debug("[MAOP-loop] Cache put failed: %s", exc)

            self._budget_reconciliation(exec_result, ctx.trace_id)

        ctx.execution_result = exec_result

        exec_duration = int((time.monotonic() - exec_start) * 1000)
        if self._metrics:
            self._metric_exec_duration.observe(exec_duration)
        self._record_metric("exec_duration_ms", exec_duration)

        await self._bus.publish(Event(topic="loop.execute", data={
            "trace_id": ctx.trace_id, "agent": exec_result.agent if exec_result else "none",
            "exit_code": exec_result.exit_code if exec_result else -1,
            "duration_ms": exec_duration,
        }))

        return PhaseResult(ok=True)

    def _budget_reconciliation(self, exec_result: Any, trace_id: str) -> None:
        """Record actual cost after execution (best-effort).

        P2-1 成本双写统一: writes to ``CostTracker`` (SQLite ``maop.db``)
        directly instead of the legacy JSON ``budget_ledger.json`` via
        ``model.budget.BudgetGuard``, eliminating the dual-write split.
        """
        if not exec_result or not exec_result.model:
            return
        try:
            from maop.core.cost_tracker import CostTracker
            from maop.model.registry import ModelRegistry

            model_name = exec_result.model or ""
            usage = getattr(exec_result, "usage", None) or {}
            if isinstance(usage, dict):
                est_tokens_in = usage.get("prompt_tokens", 0)
                est_tokens_out = usage.get("completion_tokens", 0)
            else:
                est_tokens_in = 0
                est_tokens_out = 0
            if not est_tokens_in:
                logger.warning("[maop_loop] No prompt_tokens reported by provider — using heuristic estimate, cost accuracy is UNKNOWN")
                cjk = sum(1 for ch in exec_result.task if '\u4e00' <= ch <= '\u9fff')
                est_tokens_in = cjk * 2 + (len(exec_result.task) - cjk) // 4
            if not est_tokens_out:
                logger.warning("[maop_loop] No completion_tokens reported by provider — using heuristic estimate, cost accuracy is UNKNOWN")
                cjk = sum(1 for ch in exec_result.stdout if '\u4e00' <= ch <= '\u9fff')
                est_tokens_out = cjk * 2 + (len(exec_result.stdout) - cjk) // 4

            registry = ModelRegistry(project_root=self._root)
            model_def = registry.get_model(model_name)
            provider = model_def.provider if model_def else ""
            estimated_cost = 0.0
            if model_def:
                estimated_cost = (
                    model_def.cost_per_1k_input * est_tokens_in / 1000
                    + model_def.cost_per_1k_output * est_tokens_out / 1000
                )

            tracker = CostTracker(root_dir=self._root)
            tracker.record_actual_cost(
                trace_id=trace_id, model=model_name, provider=provider,
                actual_tokens_in=est_tokens_in, actual_tokens_out=est_tokens_out,
                estimated_cost=estimated_cost,
            )
        except Exception as exc:
            logger.warning("[MAOP-loop] Budget reconciliation failed: %s", exc)

    async def _phase_verify(self, ctx: PhaseContext, workdir: str, skip_verify: bool) -> PhaseResult:
        """Phase 3: Verify — check execution results against plan."""
        verify_start = time.monotonic()
        self._log("verify", "INFO", "Verify phase started", trace_id=ctx.trace_id)
        should_skip = skip_verify or self._loop_config.skip_verify
        ctx.verify_result = await self._verify(
            ctx.plan_result, ctx.execution_result, workdir, should_skip, ctx.trace_id,
        )

        verify_duration = int((time.monotonic() - verify_start) * 1000)
        if self._metrics:
            self._metric_verify_duration.observe(verify_duration)
        self._record_metric("verify_duration_ms", verify_duration)

        return PhaseResult(ok=True)

    async def _phase_feedback(self, ctx: PhaseContext, workdir: str, skip_verify: bool) -> PhaseResult:
        """Phase 4: Feedback loop — state-aware retry on verify failure."""
        lc = self._loop_config
        should_skip = skip_verify or lc.skip_verify
        verify_result = ctx.verify_result
        exec_result = ctx.execution_result

        while (verify_result and not verify_result.errored and not verify_result.passed
               and ctx.feedback_cycles < lc.feedback_max_cycles
               and not should_skip):
            v_state = getattr(verify_result, "state", "working")
            if v_state == "blocked":
                ctx.block_reason = getattr(verify_result, "block_reason", "") or "External input required"
                logger.warning("Feedback loop stopped: BLOCKED — %s", ctx.block_reason)
                self._log("verify-feedback", "WARN",
                              f"Blocked: {ctx.block_reason}", trace_id=ctx.trace_id)
                try:
                    from maop.core.agent.delegation.human_proxy import HumanProxy
                    hp = HumanProxy(root_dir=str(self._root))
                    hp.request(
                        task=ctx.original_task,
                        agent=exec_result.agent if exec_result else "",
                        priority="high", reason=f"Verify blocked: {ctx.block_reason}",
                        metadata={"trace_id": ctx.trace_id, "verify_summary": getattr(verify_result, "summary", "")},
                    )
                    logger.info("[loop] HumanProxy approval request created for blocked state")
                except Exception as hp_exc:
                    logger.debug("[loop] HumanProxy request skipped: %s", hp_exc)
                break
            if v_state == "failed":
                logger.warning("Feedback loop stopped: FAILED (structural) — %s",
                               getattr(verify_result, "summary", ""))
                self._log("verify-feedback", "ERROR",
                              f"Structural failure: {verify_result.summary}",
                              trace_id=ctx.trace_id)
                break

            ctx.feedback_cycles += 1
            logger.info("Feedback loop %d/%d: Re-planning (state=working)...",
                        ctx.feedback_cycles, lc.feedback_max_cycles)
            self._log("verify-feedback", "WARN",
                           f"Verify failed (working), feedback loop {ctx.feedback_cycles}",
                           trace_id=ctx.trace_id)

            feedback_task = f"Retry: {verify_result.summary} | original: {ctx.original_task}"
            feedback_chain = self._build_fallback_chain(ctx.agent, ctx.routing_key)

            exec_result = await self._execute_with_retry(
                task=feedback_task, fallback_chain=feedback_chain,
                routing_key=ctx.routing_key, workdir=workdir,
                timeout=int(ctx.timeout), retry=True, trace_id=ctx.trace_id,
            )
            verify_result = await self._verify(
                ctx.plan_result, exec_result, workdir, False, ctx.trace_id,
            )

        ctx.execution_result = exec_result
        ctx.verify_result = verify_result
        return PhaseResult(ok=True)

    async def _phase_evolve(self, ctx: PhaseContext) -> PhaseResult:
        """Phase 5: Evolve + Dream scheduling."""
        lc = self._loop_config

        if lc.enable_evolve and ctx.verify_result:
            try:
                from maop.evolve import EvolveEngine
                evolve_engine = EvolveEngine(root_dir=self._root)
                evolve_result = evolve_engine.analyze()
                if evolve_result and evolve_result.suggestions:
                    self._log("evolve", "INFO",
                              f"Evolve: {len(evolve_result.suggestions)} suggestions",
                              trace_id=ctx.trace_id)
                    await self._bus.publish(Event(topic="loop.evolve", data={
                        "trace_id": ctx.trace_id,
                        "suggestions": [s.model_dump() for s in evolve_result.suggestions[:5]],
                    }))
            except Exception as exc:
                logger.warning("Evolve phase failed: %s", exc)

        self._loop_count += 1
        if (self._consolidator and lc.enable_dream
                and self._loop_count % lc.dream_interval_cycles == 0):
            try:
                dream_report = self._consolidator.dream()
                if dream_report.success and dream_report.entries_pruned > 0:
                    self._log("dream", "INFO",
                              f"Dream: pruned {dream_report.entries_pruned} entries, "
                              f"created {dream_report.entries_created} consolidated, "
                              f"reduction {dream_report.reduction_pct:.1f}%",
                              trace_id=ctx.trace_id)
                    await self._bus.publish(Event(topic="loop.dream", data={
                        "trace_id": ctx.trace_id,
                        "pruned": dream_report.entries_pruned,
                        "created": dream_report.entries_created,
                        "reduction_pct": dream_report.reduction_pct,
                    }))
                else:
                    logger.debug("[dream] No consolidation needed this cycle")
            except Exception as exc:
                logger.debug("Dream consolidation skipped: %s", exc)

        return PhaseResult(ok=True)

    def _build_loop_result(self, ctx: PhaseContext, start: float) -> LoopResult:
        """Build final LoopResult from phase context."""
        total_ms = int((time.monotonic() - start) * 1000)
        exec_result = ctx.execution_result
        verify_result = ctx.verify_result
        verify_ok = True
        if verify_result and not verify_result.errored:
            verify_ok = verify_result.passed
        success = exec_result is not None and exec_result.is_success() and verify_ok

        try:
            if self._memory is not None:
                self._memory.store(
                    agent=exec_result.agent if exec_result else "none",
                    task=ctx.original_task,
                    content=exec_result.stdout[:500] if exec_result and exec_result.stdout else "",
                    tags=["MAOP-loop", ctx.routing_key],
                    trace_id=ctx.trace_id,
                )
        except Exception as exc:
            logger.warning("[MAOP-loop] Memory store failed: %s", exc)

        if self._metrics:
            self._metric_loop_duration.observe(total_ms)
            self._metric_tasks_total.inc()
            if success:
                self._metric_tasks_success.inc()
        self._record_metric("loop_duration_ms", total_ms, {"success": str(success)})

        self._log("loop", "INFO" if success else "ERROR",
                       f"Loop {'succeeded' if success else 'failed'}",
                       trace_id=ctx.trace_id, duration_ms=total_ms, feedback_cycles=ctx.feedback_cycles)

        try:
            loop = asyncio.get_running_loop()
            _t = loop.create_task(self._bus.publish(Event(topic="loop.complete", data={
                "trace_id": ctx.trace_id, "success": success, "duration_ms": total_ms,
            })))
            _bg_tasks.add(_t)
            _t.add_done_callback(_bg_tasks.discard)
        except RuntimeError as exc:
            logger.debug("maop_loop: no running loop for loop.complete publish: %s", exc)

        return LoopResult(
            task=ctx.original_task, trace_id=ctx.trace_id,
            selected_agent=ctx.agent, routing_key=ctx.routing_key,
            plan=ctx.plan_result, execution=exec_result,
            verify=verify_result, feedback_cycles=ctx.feedback_cycles,
            total_duration_ms=total_ms, success=success,
            analysis=ctx.analysis_dict,
            parallel_executed=ctx.parallel_executed,
            block_reason=ctx.block_reason,
        )

    # ── Phase implementations ─────────────────────────────

    async def _simple_analyze(self, task: str) -> RequirementAnalysis:
        """Fallback simple text parsing — delegates to loop_analyzer.simple_analyze.

        G1c (2026-07-22, Phase G): now ``async`` and forwards the LLM
        kwargs from ``LoopConfig`` so callers that invoke this wrapper
        directly (rather than going through ``_phase_analyze``) also
        benefit from the LLM-first semantic path when enabled. When
        ``enable_llm_analyze`` is False (default), behavior is identical
        to the prior synchronous call. See ADR-013.
        """
        lc = self._loop_config
        return await simple_analyze(
            task,
            llm_factory=self.llm_factory,
            model_name=lc.llm_analyze_model,
            enable_llm=lc.enable_llm_analyze,
        )

    async def _plan(self, task: str, workdir: str) -> dict[str, Any]:
        """Execute Plan phase — route task to agent."""
        try:
            plan = maop_plan(task=task, workdir=workdir, config=self._config)
            return plan.model_dump()  # type: ignore
        except Exception as exc:
            # C2 fix: do NOT silently degrade to a hardcoded default route,
            # which would mask a real misconfiguration as a successful plan.
            # Fail loud so the caller marks the task as failed/errored.
            logger.error("Plan phase failed: %s", exc)
            raise

    async def _verify(
        self,
        plan: dict[str, Any],
        result: MaopResult | None,
        workdir: str,
        skip: bool,
        trace_id: str,
    ) -> VerifyResult | None:
        """Execute Verify phase."""
        if skip:
            logger.warning("[MAOP-loop] Verify phase SKIPPED")
            self._log("verify", "WARN", "Verify skipped", trace_id=trace_id)
            # Return None — finalize treats None as "no verification performed",
            # trusting the execution result instead of marking success=False.
            return None

        try:
            if self._verify_engine is not None:
                return cast(VerifyResult | None, self._verify_engine.verify(plan=plan, result=result, workdir=workdir))
            return None
        except Exception as exc:
            # C3 fix: distinguish an engine error from a real verification
            # failure. Mark `errored=True` so finalize does NOT count this as
            # the task failing verification (which would falsely fail the task).
            logger.error("Verify phase exception (engine error, not a task failure): %s", exc)
            self._log("verify", "ERROR", f"Verify exception: {exc}", trace_id=trace_id)
            return VerifyResult(passed=False, errored=True, summary=f"Verify engine error: {exc}")

    # ── Fallback chain ─────────────────────────────────────

    def _build_fallback_chain(self, selected_agent: str, routing_key: str) -> list[str]:
        """Build fallback chain from routing table."""
        chain = [selected_agent]

        if self._config is None:
            return chain

        # Look up routing entry
        for rk, route in self._config.routing.items():
            if rk == routing_key:
                for level in (route.primary, route.fallback, route.tertiary):
                    if level and level != selected_agent and level not in chain:
                        chain.append(level)
                break

        return chain