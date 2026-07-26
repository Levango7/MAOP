"""MAOP Loop — Master Orchestrator: Plan -> Execute -> Verify cycle.

Unified orchestration loop with DAG support. with unified engine, DAG support, guardrails,
memory injection, feedback loop, and structured logging.

Integrated subsystems:
  - analyzer: Semantic decomposition + DAG + strategy selection
  - worker_pool: Parallel subtask execution via DAG parallel groups
  - load_balancer: Intelligent agent routing
  - cache_guard: Cache protection (SingleFlight, anti-stampede)
  - cache (LRU): Result caching for repeated tasks
  - monitoring: Structured logging + metrics collection
  - evolve: Self-evolution after verify phase
  - timeseries: Time-series metrics recording
  - message_queue: Async inter-module communication
  - hot_reload: Configuration hot-reload
  - vector: Vector semantic search (hybrid with FTS5)
  - bloom_filter: Already in memory.store for dedup

Models are in loop_models.py, execution logic in loop_executor.py,
and simple analysis in loop_analyzer.py.
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

if TYPE_CHECKING:
    from maop.core.llm_provider import LLMProviderFactory

from maop.config.loader import ConfigLoader, MaopConfig
from maop.core.analyzer import ExecutionStrategy
from maop.core.analyzer import analyze as requirement_analyze
from maop.core.cache import SingleFlight
from maop.core.error_schema import MaopResult
from maop.core.event_bus import Event, EventBus, get_event_bus
from maop.core.log_rotate import rotate_logs
from maop.core.monitoring import MetricsCollector, StructuredLogger
from maop.core.otel import get_tracer
from maop.core.otel import setup_provider as otel_setup
from maop.core.otel import span as otel_span
from maop.core.phases import PhaseContext, PhaseResult
from maop.loop_analyzer import simple_analyze
from maop.loop_executor import ExecuteMixin

# Re-export models for backward compatibility (maop_loop.py is the canonical import path)
from maop.loop_models import LoopConfig, LoopResult, RequirementAnalysis
from maop.maop_plan import maop_plan
from maop.maop_verify import VerifyResult

logger = logging.getLogger(__name__)

_otel_tracer = None


def _get_otel_tracer():
    global _otel_tracer
    if _otel_tracer is None:
        otel_setup()
        _otel_tracer = get_tracer("maop.loop")
    return _otel_tracer

# ── MAOP Loop Engine ───────────────────────────────────────────


class MaopLoop(ExecuteMixin):
    """Master orchestrator: Plan -> Execute -> Verify with feedback.

    Integrated subsystems:
      - analyzer: Semantic task decomposition + DAG + strategy
      - worker_pool: Parallel execution of independent subtasks
      - load_balancer: Intelligent agent selection
      - cache_guard: SingleFlight + anti-stampede protection
      - result_cache: LRU cache for repeated task results
      - monitoring: StructuredLogger + MetricsCollector
      - timeseries: Time-series metrics recording
      - evolve: Self-evolution suggestions after verify

    Usage::

        loop = MaopLoop(root_dir="/path/to/MAOP")
        result = await loop.run(task="Add input validation")
        if result.success:
            print("Task completed:", result.execution.stdout)
    """

    def __init__(
        self,
        root_dir: str | Path | None = None,
        config: MaopConfig | None = None,
        loop_config: LoopConfig | None = None,
        event_bus: EventBus | None = None,
    ) -> None:
        if root_dir is None:
            from maop.core.db_utils import find_project_root
            root_dir = find_project_root()
        self._root = Path(root_dir)

        # Load config
        if config is None:
            try:
                loader = ConfigLoader(project_root=self._root)
                config = loader.load()
            except Exception as exc:
                logger.warning("ConfigLoader failed, using defaults: %s", exc)
                config = None
        self._config = config

        self._loop_config = loop_config or LoopConfig()
        self._bus = event_bus or get_event_bus()
        lc = self._loop_config

        # Service Container — lazy-initialized subsystems
        from maop.core.services import ServiceContainer
        self._svc = ServiceContainer(root_dir=self._root)
        self._svc.set("config", config)

        # Core subsystems via container
        self._breaker = self._svc.get("circuit_breaker")
        self._dispatcher = self._svc.get("dispatcher")
        self._guardrail = self._svc.get("guardrail")
        self._verify_engine = self._svc.get("verify_engine")
        self._memory = self._svc.get("memory_store")

        # HookManager
        self._hook_mgr = self._svc.get("hook_manager")
        if self._hook_mgr:
            try:
                self._hook_mgr.bridge_event_bus(self._bus)
            except Exception as exc:
                # H10 fix (Phase R7): 不再静默吞异常，记录警告便于排查
                logger.warning("bridge_event_bus failed: %s", exc, exc_info=True)

        # P0: WorkerPool
        self._worker_pool = self._svc.get("worker_pool", raise_on_failure=False) if lc.enable_parallel else None

        # P0: LoadBalancer
        self._load_balancer = None
        if lc.enable_load_balancer:
            self._load_balancer = self._svc.get("load_balancer")
            if self._load_balancer and config:
                agents = getattr(config, "agents", None)
                if agents and isinstance(agents, dict):
                    for a_name, a in agents.items():
                        weight = max(1, 20 - getattr(a, "timeout_s", 120) // 10)
                        self._load_balancer.register(a_name, weight=weight)

        # Cache
        self._cache_guard = self._svc.get("cache_guard", raise_on_failure=False) if lc.enable_cache_guard else None
        self._single_flight = SingleFlight()
        self._result_cache = self._svc.get("result_cache", raise_on_failure=False) if lc.enable_result_cache else None

        # Monitoring
        self._struct_logger = StructuredLogger("MAOP-loop", log_dir=self._root / "logs")
        self._metrics: MetricsCollector | None = MetricsCollector() if lc.enable_metrics else None
        if self._metrics:
            self._metric_loop_duration = self._metrics.histogram("loop_duration_ms", "Loop cycle duration")
            self._metric_plan_duration = self._metrics.histogram("plan_duration_ms", "Plan phase duration")
            self._metric_exec_duration = self._metrics.histogram("exec_duration_ms", "Execute phase duration")
            self._metric_verify_duration = self._metrics.histogram("verify_duration_ms", "Verify phase duration")
            self._metric_tasks_total = self._metrics.counter("tasks_total", "Total tasks processed")
            self._metric_tasks_success = self._metrics.counter("tasks_success", "Successful tasks")

        # P1/P2 services via container
        self._timeseries = self._svc.get("timeseries", raise_on_failure=False) if lc.enable_timeseries else None
        self._message_queue = self._svc.get("message_queue", raise_on_failure=False)
        self._hot_reload = self._svc.get("hot_reload", raise_on_failure=False)
        self._kv_store = self._svc.get("kv_store", raise_on_failure=False)
        self._prompt_manager = self._svc.get("prompt_manager", raise_on_failure=False)
        self._migration = self._svc.get("migration", raise_on_failure=False)
        self._consolidator = self._svc.get("consolidator", raise_on_failure=False) if lc.enable_dream else None

        self._loop_count = 0

        # Log file
        self._log_file = self._root / "data" / "MAOP-loop.jsonl"

        # G1c (2026-07-22, Phase G): LLM factory for semantic analysis.
        # Lazily instantiated on first access via the llm_factory property
        # (see ADR-013). Kept None until needed so MaopLoop works without
        # any LLM provider configured.
        self._llm_factory: LLMProviderFactory | None = None



    # ── Structured logging (replaces _write_maop_log) ──────────

    def _log(self, phase: str, level: str, message: str, **data: Any) -> None:
        """Write structured log via monitoring.StructuredLogger."""
        self._struct_logger.log(phase=phase, level=level, message=message, **data)

    def _record_metric(self, name: str, value: float, tags: dict[str, str] | None = None) -> None:
        """Record a time-series data point."""
        if self._timeseries:
            try:
                self._timeseries.record(name, value, tags=tags or {})
            except Exception as exc:
                logger.warning("Metric record failed for %s: %s", name, exc)

    @property
    def llm_factory(self) -> LLMProviderFactory | None:
        """Lazily-initialized LLMProviderFactory for semantic analysis.

        G1c (2026-07-22, Phase G): used by Phase 0 analyze to call
        ``simple_analyze`` with an LLM-first semantic extraction path
        when ``LoopConfig.enable_llm_analyze`` is True. The factory is
        only constructed on first access so a MAOP instance without
        any configured LLM provider remains fully functional (rule-based
        fallback path). Returns None if the factory cannot be built
        (config missing / import error); callers must handle None by
        falling back to the rule-based path. See ADR-013.
        """
        if self._llm_factory is None:
            try:
                from maop.core.llm_provider import LLMProviderFactory
                self._llm_factory = LLMProviderFactory(root_dir=self._root)
            except Exception as exc:
                logger.warning("[maop_loop] LLMProviderFactory init failed: %s", exc)
                # Leave _llm_factory as None so subsequent accesses retry.
                # simple_analyze treats llm_factory=None as "fall back to
                # rule-based", so this is a safe degradation path.
        return self._llm_factory

    async def run(
        self,
        task: str,
        *,
        workdir: str = "",
        retry: bool = False,
        skip_verify: bool = False,
        parent_trace_id: str = "",
        agent: str = "",
    ) -> LoopResult:
        """Execute a full Plan -> Execute -> Verify cycle.

        Thin orchestrator that delegates to _phase_* methods.

        F6a (2026-07-22, Phase F): ``agent`` lets the caller pin the
        executing agent. When non-empty, it is stored as
        ``ctx.forced_agent`` and ``ctx.agent`` so that ``_phase_plan``
        skips its own agent selection (plan_result.selected_agent +
        load_balancer) and the explicitly-requested agent is used end
        to end. This closes the gap where A2A ``dispatch_task`` recorded
        ``agent_name`` only as artifact metadata without actually
        routing execution to that agent. When empty (default), the
        existing plan-based selection is preserved. See ADR-013.
        """
        start = time.monotonic()
        trace_id = uuid.uuid4().hex
        lc = self._loop_config

        logger.info("MAOP Loop started | task=%s | trace=%s", task[:80], trace_id)
        self._log("loop", "INFO", "Loop started", trace_id=trace_id, task=task[:80])

        if lc.enable_log_rotation:
            try:
                rotate_logs(
                    max_size_kb=lc.log_rotation_max_kb,
                    retain_count=lc.log_rotation_retain,
                    log_dir=self._root / "logs",
                    data_dir=self._root / "data",
                )
            except Exception as exc:
                logger.warning("[MAOP-loop] Log rotation failed: %s", exc)

        ctx = PhaseContext(task=task, original_task=task, trace_id=trace_id)

        # F6a (2026-07-22, Phase F): honor caller-pinned agent.
        # When non-empty, set both ctx.agent (so other phases see it)
        # and ctx.forced_agent (so _phase_plan knows NOT to override it
        # via plan_result.selected_agent or load_balancer.select).
        if agent:
            ctx.agent = agent
            ctx.forced_agent = agent
            self._log("loop", "INFO", f"Agent pinned by caller: {agent}",
                      trace_id=trace_id, agent=agent)

        self._inject_memory(ctx, parent_trace_id)

        with otel_span(_get_otel_tracer(), "maop.run", trace_id=trace_id,
                       attributes={"task": task[:100]}):
            pr = await self._phase_analyze(ctx, skip_verify)
            if pr.skip_remaining:
                return self._build_loop_result(ctx, start)

            pr = await self._phase_plan(ctx, workdir)
            if pr.skip_remaining:
                return self._build_loop_result(ctx, start)

            pr = await self._phase_execute(ctx, workdir, retry)
            if pr.skip_remaining:
                return self._build_loop_result(ctx, start)

            pr = await self._phase_verify(ctx, workdir, skip_verify)
            if pr.skip_remaining:
                return self._build_loop_result(ctx, start)

            pr = await self._phase_feedback(ctx, workdir, skip_verify)
            if pr.skip_remaining:
                return self._build_loop_result(ctx, start)

            await self._phase_evolve(ctx)

        return self._build_loop_result(ctx, start)

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
        if (skip_verify and lc.skip_analyze) or lc.skip_analyze:
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
            loop.create_task(self._bus.publish(Event(topic="loop.analyze", data={
                "trace_id": ctx.trace_id, "analysis": ctx.analysis_dict,
            })))
        except RuntimeError:
            pass

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

        cache_key = f"{ctx.agent}:{ctx.routing_key}:{ctx.original_task[:200]}"
        # P2-5 fix: skip cache for side-effecting routing keys (write/run/exec/shell)
        # to prevent silent dedup of tasks that modify state
        _side_effect_keys = ("codegen", "review", "verify", "run", "exec", "shell", "deploy", "migrate", "fix", "refactor")
        cache_enabled = self._result_cache and not any(k in ctx.routing_key.lower() for k in _side_effect_keys)
        exec_result = None
        if cache_enabled:
            try:
                cached = self._result_cache.get(cache_key)
                if cached is not None:
                    self._log("execute", "INFO", "Cache hit", trace_id=ctx.trace_id)
                    exec_result = cached
            except Exception as exc:
                # H10 fix (Phase R7): 缓存读取失败不应静默，记录警告
                logger.warning("cache lookup failed: %s", exc, exc_info=True)

        ctx.parallel_executed = False
        if exec_result is None:
            is_parallel = (ctx.analysis_result
                           and ctx.analysis_result.strategy in (ExecutionStrategy.PARALLEL, ExecutionStrategy.HYBRID)
                           and ctx.analysis_result.sub_tasks and len(ctx.analysis_result.sub_tasks) > 1
                           and self._worker_pool)
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
                    self._result_cache.put(cache_key, exec_result)
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
        """Record actual cost after execution (best-effort)."""
        if not exec_result or not exec_result.model:
            return
        try:
            from maop.model.budget import BudgetGuard
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

            bg = BudgetGuard(root_dir=self._root)
            bg.record_actual_cost(
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

        while (verify_result and not verify_result.passed
               and ctx.feedback_cycles < lc.feedback_max_cycles
               and not should_skip):
            v_state = getattr(verify_result, "state", "working")
            if v_state == "blocked":
                ctx.block_reason = getattr(verify_result, "block_reason", "") or "External input required"
                logger.warning("Feedback loop stopped: BLOCKED — %s", ctx.block_reason)
                self._log("verify-feedback", "WARN",
                              f"Blocked: {ctx.block_reason}", trace_id=ctx.trace_id)
                try:
                    from maop.core.human_proxy import HumanProxy
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
        success = exec_result is not None and exec_result.is_success() and (verify_result.passed if verify_result else True)

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
            loop.create_task(self._bus.publish(Event(topic="loop.complete", data={
                "trace_id": ctx.trace_id, "success": success, "duration_ms": total_ms,
            })))
        except RuntimeError:
            pass

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
            return plan.model_dump()
        except Exception as exc:
            logger.warning("Plan phase failed: %s", exc)
            return {
                "phase": "plan", "task": task,
                "selected_agent": "claude", "routing_key": "chat",
                "gates": ["exit_code", "output"],
                "budget": {"timeout_s": self._loop_config.default_timeout_s, "max_retries": 1},
            }

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
            logger.warning("Verify phase exception: %s", exc)
            self._log("verify", "ERROR", f"Verify exception: {exc}", trace_id=trace_id)
            return VerifyResult(passed=False, summary=f"Verify error: {exc}")

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
