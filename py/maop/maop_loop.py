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

Models are in loop_models.py (re-exported via maop_loop_config.py),
execution logic in loop_executor.py, phase methods in maop_loop_phases.py,
and simple analysis in loop_analyzer.py.
"""

from __future__ import annotations

import logging
import time
import uuid
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from maop.core.agent.llm_chat.llm_provider import LLMProviderFactory

# Re-export models for backward compatibility (maop_loop.py is the canonical
# import path). LoopConfig/LoopResult/RequirementAnalysis come from
# loop_models; PhaseContext/PhaseResult come from core.agent.evolution.phases.
from maop.config.loader import ConfigLoader, MaopConfig
from maop.core.agent.evolution.phases import PhaseContext, PhaseResult  # noqa: F401
from maop.core.monitoring.monitoring import MetricsCollector, StructuredLogger
from maop.core.monitoring.otel import span as otel_span
from maop.core.reliability.cache import SingleFlight
from maop.core.reliability.event_bus import EventBus, get_event_bus
from maop.core.reliability.log_rotate import rotate_logs
from maop.loop_executor import ExecuteMixin
from maop.loop_models import LoopConfig, LoopResult, RequirementAnalysis  # noqa: F401
from maop.maop_loop_phases import PhasesMixin, _get_otel_tracer

logger = logging.getLogger(__name__)

# ── MAOP Loop Engine ───────────────────────────────────────────


class MaopLoop(ExecuteMixin, PhasesMixin):
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
            from maop.core.backends.db_utils import find_project_root
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
        from maop.core.reliability.services import ServiceContainer
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
                from maop.core.agent.llm_chat.llm_provider import LLMProviderFactory
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
