"""MAOP Unified Engine — Execute workflow steps in topological order.

DAG workflow engine with topological execution: consumes WorkflowStep arrays,
supports plan/agent/dag/verify/condition/terminal step types.

Module split (Phase 3-1)
-----------------------
- ``engine_types`` — enums (``StepType``, ``StepStatus``) and Pydantic models
  (``WorkflowStep``, ``StepResult``, ``EngineResult``).
- ``engine_utils`` — pure helper functions (``safe_eval``, ``_resolve_template``,
  ``_topological_sort``, ``_find_step``, ``json_dumps_safe``, ...).
- ``engine_pause`` — pause-checking helpers (``is_paused``,
  ``check_pause_async``, ``_get_pause_file_path``) and pause constants.
- ``engine_executor`` — single-step execution helper
  (``execute_step_helper``) extracted from ``Engine._execute_step``.
- ``engine``      — the ``Engine`` class plus re-exports for backward
  compatibility.

Public symbols are re-exported via ``__all__`` so that
``from maop.engine import Engine, EngineResult, WorkflowStep, ...`` continues
to work without any change to callers. Dependency graph is single-directional:
``engine → engine_pause + engine_executor + engine_types + engine_utils``,
``engine_executor → engine_types + engine_utils``, and
``engine_pause`` is standalone.
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
import uuid
from typing import Any

from maop.core.reliability.event_bus import EventBus, get_event_bus
from maop.engine_types import (
    EngineResult,
    StepResult,
    StepStatus,
    StepType,
    WorkflowStep,
)
from maop.engine_utils import (
    _find_step,
    _resolve_template,
    _topological_sort,
    json_dumps_safe,
    safe_eval,
)
# Re-export pause helpers for backward compatibility
# (``from maop.engine import is_paused, check_pause_async`` must still work).
from maop.engine_pause import (
    MAX_PAUSE_SECONDS,
    PAUSE_CHECK_INTERVAL_S,
    PAUSE_FILE_NAME,
    _get_pause_file_path,
    check_pause_async,
    is_paused,
)
# Re-export the plan depth cap and the step execution helper.
from maop.engine_executor import (
    _MAX_PLAN_DEPTH,
    execute_step_helper,
)

logger = logging.getLogger(__name__)


# ── Engine ────────────────────────────────────────────────────

class Engine:
    """Unified workflow engine that executes steps in topological order.

    Usage::

        engine = Engine()
        steps = [
            WorkflowStep(id="s1", type=StepType.AGENT, agent="claude", task="Write code"),
            WorkflowStep(id="s2", type=StepType.VERIFY, depends_on=["s1"]),
            WorkflowStep(id="s3", type=StepType.TERMINAL, depends_on=["s2"]),
        ]
        result = await engine.run(steps, context={"task": "refactor"})
    """

    def __init__(
        self,
        event_bus: EventBus | None = None,
        step_executor: Any = None,
        redis_client: Any = None,
    ) -> None:
        self._bus = event_bus or get_event_bus()
        self._step_executor = step_executor  # Optional custom executor
        if step_executor is None:
            # P2-1 (Breaking): without an executor, AGENT/DAG/PLAN steps cannot
            # actually run and will fail fast (see _execute_step). Surfacing this
            # at construction time helps callers wire one up before dispatch.
            logger.warning(
                "Engine constructed without step_executor: AGENT/DAG/PLAN steps "
                "will fail fast (StepStatus.FAILED) because no executor is wired "
                "to actually run them. Set step_executor=<callable> to enable."
            )
        # F1-01 分布式执行: Redis client for DistributedScheduler.
        # When None (Personal edition / no Redis), run() uses single-process
        # execution. When provided, run(distributed=True) dispatches DAG
        # nodes to the Redis Streams task queue.
        self._redis_client = redis_client
        self._distributed_scheduler: Any = None  # lazy init

    def _get_distributed_scheduler(self) -> Any:
        """Lazily build a DistributedScheduler bound to the Redis client.

        Returns ``None`` when no Redis client is configured (Personal
        edition fallback to single-process execution).
        """
        if self._redis_client is None:
            return None
        if self._distributed_scheduler is None:
            try:
                from maop.core.scheduling.distributed_scheduler import (
                    DistributedScheduler,
                )
                self._distributed_scheduler = DistributedScheduler(
                    self._redis_client,
                )
            except Exception:
                # Redis unavailable → degrade to single-process (Personal).
                logger.warning(
                    "[engine] DistributedScheduler init failed; "
                    "falling back to single-process execution",
                )
                self._distributed_scheduler = None
        return self._distributed_scheduler

    def _get_supervisor(self) -> Any:
        """Lazily return the process-wide Supervisor singleton.

        Returns ``None`` when no Supervisor has been configured
        (passive-only mode). The Engine integration points all take
        the ``None`` branch and behave exactly as before — full
        backward compatibility.
        """
        try:
            from maop.core.scheduling.failure_detector import get_supervisor

            return get_supervisor()
        except Exception:
            return None

    async def run(
        self,
        steps: list[WorkflowStep],
        context: dict[str, Any] | None = None,
        workdir: str = "",
        trace_id: str = "",
        *,
        distributed: bool = False,
    ) -> EngineResult:
        """Execute all steps in topological order.

        Parameters
        ----------
        steps : list[WorkflowStep]
            Workflow steps to execute.
        context : dict | None
            Initial context with variables for template resolution.
        workdir : str
            Working directory.
        trace_id : str
            Trace ID for observability.
        distributed : bool
            F1-01 分布式执行: when True and a Redis client is configured,
            dispatch DAG nodes to the Redis Streams task queue for
            execution by distributed workers. When the Redis client is
            unavailable (Personal edition), automatically falls back to
            single-process execution without error. Default False
            (single-process, backward-compatible).
        """
        # F1-01: attempt distributed execution when requested and Redis
        # is available. On any failure (no Redis, scheduler init error,
        # dispatch error), fall back to single-process execution so the
        # Personal edition and Redis-outage scenarios never break.
        if distributed:
            scheduler = self._get_distributed_scheduler()
            if scheduler is not None:
                try:
                    return await self._run_distributed(
                        scheduler, steps, context, workdir, trace_id,
                    )
                except Exception as exc:
                    logger.warning(
                        "[engine] distributed execution failed (%s); "
                        "falling back to single-process",
                        exc,
                    )
        return await self._run_single(steps, context, workdir, trace_id)

    async def _run_distributed(
        self,
        scheduler: Any,
        steps: list[WorkflowStep],
        context: dict[str, Any] | None,
        workdir: str,
        trace_id: str,
    ) -> EngineResult:
        """Execute steps via the DistributedScheduler (Redis Streams).

        Builds :class:`_NodeSpec` objects from the workflow steps,
        dispatches them to the scheduler, and aggregates the results
        back into an :class:`EngineResult`.
        """
        from maop.core.scheduling.distributed_scheduler import (
            node_spec_from_step,
        )

        start = time.monotonic()
        if not trace_id:
            trace_id = uuid.uuid4().hex
        if context is None:
            context = {}
        ctx = dict(context)

        # Build node specs from workflow steps. Each step's payload
        # carries its serialised description so workers can execute it.
        # M4 修复：分布式执行前检查 pause 状态
        await check_pause_async()

        nodes = []
        for step in steps:
            affinity = step.params.get("affinity") if step.params else None
            priority = int(step.params.get("priority", 3)) if step.params else 3
            nodes.append(node_spec_from_step(
                step.id,
                depends_on=list(step.depends_on),
                affinity=affinity,
                priority=priority,
                payload={
                    "type": step.type.value,
                    "agent": step.agent,
                    "task": _resolve_template(step.task, ctx),
                    "params": step.params,
                    "workdir": workdir,
                    "trace_id": trace_id,
                },
                timeout=float(step.timeout),
            ))

        dist_result = await scheduler.run(nodes, run_id=trace_id)

        # Aggregate distributed results into EngineResult.
        all_results: list[StepResult] = []
        for step in steps:
            res = dist_result.results.get(step.id, {})
            status_map = {
                "success": StepStatus.SUCCESS,
                "failed": StepStatus.FAILED,
                "skipped": StepStatus.SKIPPED,
                "pending": StepStatus.PENDING,
            }
            status = status_map.get(res.get("status", "pending"), StepStatus.PENDING)
            sr = StepResult(
                id=step.id,
                status=status,
                output=str(res.get("output", "")) if res.get("output") is not None else "",
                error=res.get("error", ""),
                agent=step.agent,
                duration_ms=int(res.get("duration_ms", 0)),
            )
            all_results.append(sr)
            ctx[step.id] = sr.output or sr.error

        total_ms = int((time.monotonic() - start) * 1000)
        success = all(
            r.status in (StepStatus.SUCCESS, StepStatus.SKIPPED)
            for r in all_results
        )
        return EngineResult(
            trace_id=trace_id,
            steps=all_results,
            success=success,
            total_duration_ms=total_ms,
            context=ctx,
        )

    async def _run_single(
        self,
        steps: list[WorkflowStep],
        context: dict[str, Any] | None,
        workdir: str,
        trace_id: str,
    ) -> EngineResult:
        """Single-process execution (original run() logic, backward-compatible)."""
        start = time.monotonic()
        if not trace_id:
            trace_id = uuid.uuid4().hex
        if context is None:
            context = {}

        ctx = dict(context)
        results: dict[str, StepResult] = {}
        layers = _topological_sort(steps)

        for _layer_idx, layer in enumerate(layers):
            # M4 修复：在每层任务派发前检查 pause 状态，暂停期间不执行新任务
            await check_pause_async()

            # Check if any previous step requested abort
            aborted = any(
                results.get(s.id, StepResult(id=s.id)).status == StepStatus.FAILED
                and _find_step(steps, s.id).on_failure == "abort"
                for s in steps if s.id in results
            )
            if aborted:
                for step in layer:
                    results[step.id] = StepResult(
                        id=step.id, status=StepStatus.SKIPPED,
                        error="Aborted due to upstream failure",
                    )
                continue

            # Execute layer steps in parallel
            tasks = []
            for step in layer:
                # P0-2 fix: wrap each step with asyncio.wait_for to prevent
                # a single hanging step from blocking the entire engine.
                tasks.append(asyncio.wait_for(
                    self._execute_step(step, ctx, results, workdir, trace_id),
                    timeout=float(step.timeout) if step.timeout > 0 else 300,
                ))
            layer_results = await asyncio.gather(*tasks, return_exceptions=True)

            for step, lr in zip(layer, layer_results):
                if isinstance(lr, asyncio.TimeoutError):
                    # P1-3 fix: 原代码 duration_ms=step.timeout*1000 在 step.timeout<=0
                    # （走 300s 默认分支）时算出 0，与实际超时值不符。改为使用
                    # 实际生效的超时值（step.timeout>0 时用 step.timeout，否则 300）。
                    effective_timeout = float(step.timeout) if step.timeout > 0 else 300
                    sr = StepResult(
                        id=step.id, status=StepStatus.FAILED,
                        error=f"Step timed out after {effective_timeout}s", agent=step.agent,
                        duration_ms=int(effective_timeout * 1000),
                    )
                elif isinstance(lr, Exception):
                    sr = StepResult(
                        id=step.id, status=StepStatus.FAILED,
                        error=str(lr), agent=step.agent,
                    )
                else:
                    sr = lr  # type: ignore
                results[step.id] = sr

                # Update context with step output
                ctx[step.id] = sr.output or sr.error

        total_ms = int((time.monotonic() - start) * 1000)
        all_results = [results[s.id] for s in steps if s.id in results]
        success = all(
            r.status in (StepStatus.SUCCESS, StepStatus.SKIPPED)
            for r in all_results
        )

        return EngineResult(
            trace_id=trace_id,
            steps=all_results,
            success=success,
            total_duration_ms=total_ms,
            context=ctx,
        )

    async def _execute_step(
        self,
        step: WorkflowStep,
        context: dict[str, Any],
        results: dict[str, StepResult],
        workdir: str,
        trace_id: str,
    ) -> StepResult:
        """Execute a single workflow step.

        Thin wrapper that delegates to :func:`maop.engine_executor.execute_step_helper`
        so the heavy logic lives in ``engine_executor.py`` (Phase 3-1 split).
        Behaviour is unchanged — this is a pure physical extraction.
        """
        return await execute_step_helper(
            self, step, context, results, workdir, trace_id,
        )

    # ── Dynamic task decomposition (P1-4) ──────────────────

    def _decompose_task(
        self,
        task: str,
        step: WorkflowStep,
    ) -> list[WorkflowStep]:
        """Decompose a complex task into sub-steps.

        Uses heuristics to detect compound tasks and split them:
        - Semicolons: "do A; do B" → 2 steps
        - Numbered lists: "1. A 2. B" → 2 steps
        - "and" conjunctions: "implement X and test Y" → 2 steps
        - Bullet lists: "- A\\n- B" → 2 steps

        Returns empty list if task is atomic (no decomposition needed).
        """
        substeps: list[WorkflowStep] = []

        # Strategy 1: Semicolon-separated tasks
        # P3-5 fix: 原代码 task.split(";") 会错误分割字符串字面量内的分号
        # （如 "print('a;b')" → ["print('a", "b')"]）。
        # 改用正则排除引号内的分号：匹配不在单/双引号内的分号。
        # 正则解释：分号前没有未闭合的引号（用 negative lookbehind 简化处理
        # 常见场景——完整的引号状态机过于复杂，此处用正则处理单层引号嵌套）。
        if ";" in task:
            # 按不在引号内的分号分割
            parts = re.split(r''';(?=(?:[^'"]*['"][^'"]*['"])*[^'"]*$)''', task)
            parts = [p.strip() for p in parts if p.strip()]
            if len(parts) > 1:
                for i, part in enumerate(parts):
                    substeps.append(WorkflowStep(
                        id=f"{step.id}_sub{i}",
                        type=StepType.AGENT,
                        agent=step.agent,
                        task=part,
                        depends_on=[f"{step.id}_sub{i-1}"] if i > 0 else [],
                    ))
                return substeps

        # Strategy 2: Numbered list "1. A 2. B"
        numbered = re.findall(r'\d+\.\s+(.+?)(?=\d+\.|$)', task, re.DOTALL)
        if len(numbered) > 1:
            for i, part in enumerate(numbered):
                substeps.append(WorkflowStep(
                    id=f"{step.id}_sub{i}",
                    type=StepType.AGENT,
                    agent=step.agent,
                    task=part.strip(),
                    depends_on=[],
                ))
            return substeps

        # Strategy 3: Bullet list "- A\n- B"
        bullets = re.findall(r'^[-*]\s+(.+)$', task, re.MULTILINE)
        if len(bullets) > 1:
            for i, part in enumerate(bullets):
                substeps.append(WorkflowStep(
                    id=f"{step.id}_sub{i}",
                    type=StepType.AGENT,
                    agent=step.agent,
                    task=part.strip(),
                    depends_on=[],
                ))
            return substeps

        # Strategy 4: "and" conjunction (conservative: only split on clear "and")
        and_parts = re.split(r'\s+and\s+', task, maxsplit=2)
        if len(and_parts) > 1 and all(len(p) > 10 for p in and_parts):
            for i, part in enumerate(and_parts):
                substeps.append(WorkflowStep(
                    id=f"{step.id}_sub{i}",
                    type=StepType.AGENT,
                    agent=step.agent,
                    task=part.strip(),
                    depends_on=[],
                ))
            return substeps

        # Task is atomic — no decomposition
        return []


# ── Re-exports for backward compatibility ─────────────────────
# All public symbols previously defined in this module are re-exported so
# that `from maop.engine import X` continues to work unchanged. Listing
# them in __all__ also documents the public API and keeps `import *`
# deterministic. Ruff treats names in __all__ as used (no F401).

__all__ = [
    "Engine",
    "EngineResult",
    "StepResult",
    "StepStatus",
    "StepType",
    "WorkflowStep",
    "_find_step",
    "_resolve_template",
    "_topological_sort",
    "json_dumps_safe",
    "safe_eval",
    # Pause helpers (re-exported from engine_pause for backward compat)
    "is_paused",
    "check_pause_async",
]
