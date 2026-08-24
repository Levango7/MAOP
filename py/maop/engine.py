"""MAOP Unified Engine — Execute workflow steps in topological order.

DAG workflow engine with topological execution: consumes WorkflowStep arrays,
supports plan/agent/dag/verify/condition/terminal step types.

Module split (Phase 3-1)
-----------------------
- ``engine_types`` — enums (``StepType``, ``StepStatus``) and Pydantic models
  (``WorkflowStep``, ``StepResult``, ``EngineResult``).
- ``engine_utils`` — pure helper functions (``safe_eval``, ``_resolve_template``,
  ``_topological_sort``, ``_find_step``, ``json_dumps_safe``, ...).
- ``engine``      — the ``Engine`` class plus re-exports for backward
  compatibility.

Public symbols are re-exported via ``__all__`` so that
``from maop.engine import Engine, EngineResult, WorkflowStep, ...`` continues
to work without any change to callers. Dependency graph is single-directional:
``engine → engine_types + engine_utils`` and ``engine_utils → engine_types``.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
import uuid
from pathlib import Path
from typing import Any

from maop.core.reliability.event_bus import EventBus, get_event_bus
from maop.engine_helpers import decompose_task
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

logger = logging.getLogger(__name__)


# ── M4 修复：pause 检查 ────────────────────────────────────────
# control.py 的 pause() 创建 .maop_pause 标记文件，但原 engine.py 未检查该文件，
# 导致 pause 后系统继续执行。现新增 check_pause() 在任务派发前检查标记文件。
PAUSE_CHECK_INTERVAL_S: float = 1.0  # pause 检查轮询间隔（秒）
PAUSE_FILE_NAME: str = ".maop_pause"  # pause 标记文件名（与 control.py 一致）

# ── 默认步骤超时（M4 修复 3.2）────────────────────────────────
# 原代码在 engine.py 第 361 行硬编码 300 秒，不可配置。现提取为模块级常量，
# 并允许通过环境变量 MAOP_STEP_TIMEOUT_S 覆盖。解析失败时回退到 300 秒。
_DEFAULT_STEP_TIMEOUT_S_FALLBACK = 300


def _resolve_default_step_timeout_s() -> float:
    """从环境变量 MAOP_STEP_TIMEOUT_S 解析默认步骤超时（秒）。

    解析失败或未设置时回退到 ``_DEFAULT_STEP_TIMEOUT_S_FALLBACK``。
    严禁接受非正值——这会让 asyncio.wait_for 立即超时。
    """
    raw = os.environ.get("MAOP_STEP_TIMEOUT_S")
    if raw is None or raw == "":
        return float(_DEFAULT_STEP_TIMEOUT_S_FALLBACK)
    try:
        value = float(raw)
    except ValueError:
        logger.warning(
            "[engine] MAOP_STEP_TIMEOUT_S=%r 不是合法数值，回退到默认 %ss",
            raw, _DEFAULT_STEP_TIMEOUT_S_FALLBACK,
        )
        return float(_DEFAULT_STEP_TIMEOUT_S_FALLBACK)
    if value <= 0:
        logger.warning(
            "[engine] MAOP_STEP_TIMEOUT_S=%r 非正数，回退到默认 %ss",
            raw, _DEFAULT_STEP_TIMEOUT_S_FALLBACK,
        )
        return float(_DEFAULT_STEP_TIMEOUT_S_FALLBACK)
    return value


DEFAULT_STEP_TIMEOUT_S: float = _resolve_default_step_timeout_s()


def _get_pause_file_path() -> Path:
    """获取 pause 标记文件路径。

    文件位于 <root>/logs/.maop_pause，与 control.py 中的路径保持一致。
    使用 get_root_dir() 统一解析根目录（M3 修复）。
    """
    try:
        from maop.config.env import get_root_dir
        root = get_root_dir(default=".")
    except Exception:
        # 回退：使用当前工作目录，避免导入失败导致引擎不可用
        root = Path.cwd()
    return root / "logs" / PAUSE_FILE_NAME


def is_paused() -> bool:
    """检查系统是否处于暂停状态。

    Returns
    -------
    bool
        True 表示系统已暂停（.maop_pause 文件存在）。
    """
    return _get_pause_file_path().exists()


async def check_pause_async() -> None:
    """异步检查 pause 状态，若已暂停则等待直到恢复。

    在任务派发前调用此函数，确保暂停期间不执行新任务。
    使用异步 sleep 避免阻塞事件循环。
    """
    while is_paused():
        logger.info("系统已暂停（.maop_pause 存在），等待恢复...")
        await asyncio.sleep(PAUSE_CHECK_INTERVAL_S)


def check_pause_sync() -> None:
    """同步检查 pause 状态，若已暂停则等待直到恢复。

    用于同步调用路径。使用 time.sleep 阻塞等待。
    """
    while is_paused():
        logger.info("系统已暂停（.maop_pause 存在），等待恢复...")
        time.sleep(PAUSE_CHECK_INTERVAL_S)


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
        # P1 fix: 分布式类型转换保护。worker 返回的 JSON 反序列化后，
        # duration_ms 可能是 float/int/str/None，output/error 可能是
        # None/非 str 类型。直接 int(None) 或 int("abc") 会抛 TypeError/
        # ValueError，导致聚合失败。此处统一做防御性类型转换。
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

            # 类型安全地提取 output（确保为 str）
            raw_output = res.get("output", "")
            if raw_output is None:
                safe_output = ""
            else:
                safe_output = str(raw_output)

            # 类型安全地提取 error（确保为 str，None → ""）
            raw_error = res.get("error", "")
            if raw_error is None:
                safe_error = ""
            else:
                safe_error = str(raw_error)

            # 类型安全地提取 duration_ms（确保为 int）。
            # worker 可能返回 float/str/None：先转 float 再转 int，
            # 任何转换失败回退到 0。
            raw_duration = res.get("duration_ms", 0)
            try:
                safe_duration_ms = int(float(raw_duration)) if raw_duration is not None else 0
            except (TypeError, ValueError):
                logger.warning(
                    "[engine] distributed result for step %s has invalid duration_ms=%r; defaulting to 0",
                    step.id, raw_duration,
                )
                safe_duration_ms = 0

            sr = StepResult(
                id=step.id,
                status=status,
                output=safe_output,
                error=safe_error,
                agent=step.agent,
                duration_ms=safe_duration_ms,
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
            # M4 修复 3.1/3.2：跟踪每个 step 的实际生效超时值。
            # 原代码用 step.timeout（可能为 0/None）构造错误信息，导致
            # "Step timed out after 0s" 这种与实际超时不符的误导信息。
            # 现统一用 effective_timeouts[step.id] 记录真正传给 wait_for 的值。
            effective_timeouts: dict[str, float] = {}
            for step in layer:
                # P0-2 fix: wrap each step with asyncio.wait_for to prevent
                # a single hanging step from blocking the entire engine.
                # M4 修复 3.2：默认超时改为模块级常量 DEFAULT_STEP_TIMEOUT_S，
                # 可通过环境变量 MAOP_STEP_TIMEOUT_S 覆盖，避免硬编码 300。
                effective_timeout = (
                    float(step.timeout) if step.timeout and step.timeout > 0
                    else DEFAULT_STEP_TIMEOUT_S
                )
                effective_timeouts[step.id] = effective_timeout
                tasks.append(asyncio.wait_for(
                    self._execute_step(step, ctx, results, workdir, trace_id),
                    timeout=effective_timeout,
                ))
            layer_results = await asyncio.gather(*tasks, return_exceptions=True)

            for step, lr in zip(layer, layer_results):
                if isinstance(lr, asyncio.TimeoutError):
                    # M4 修复 3.1：使用实际生效的超时值，而非 step.timeout。
                    # 当 step.timeout 为 0/None 时，step.timeout 会显示 "after 0s"
                    # 但实际超时是 DEFAULT_STEP_TIMEOUT_S（或环境变量覆盖值）。
                    eff_timeout = effective_timeouts[step.id]
                    sr = StepResult(
                        id=step.id, status=StepStatus.FAILED,
                        error=f"Step timed out after {eff_timeout}s", agent=step.agent,
                        duration_ms=int(eff_timeout * 1000),
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
        """Execute a single workflow step."""
        start = time.monotonic()

        # ── Supervisor: pre-dispatch checkpoint ──
        # When a Supervisor is configured, ask it whether this agent may
        # be dispatched. Terminated / drained agents are skipped (with
        # optional fallback). When no Supervisor is configured, this
        # branch is a no-op (full backward compatibility).
        supervisor = self._get_supervisor() if step.type == StepType.AGENT and step.agent else None
        if (
            supervisor is not None
            and step.type == StepType.AGENT
            and step.agent
            and hasattr(supervisor, "check_before_dispatch")
        ):
            try:
                decision = supervisor.check_before_dispatch(step.agent)
                if not decision.allow:
                    if decision.fallback_agent:
                        logger.info(
                            "[engine] supervisor blocked agent %s (%s); "
                            "fallback to %s",
                            step.agent, decision.reason,
                            decision.fallback_agent,
                        )
                        step = step.model_copy(
                            update={"agent": decision.fallback_agent},
                        )
                    else:
                        return StepResult(
                            id=step.id,
                            status=StepStatus.SKIPPED,
                            error=f"Supervisor blocked dispatch: {decision.reason}",
                            agent=step.agent,
                            duration_ms=int((time.monotonic() - start) * 1000),
                        )
                elif decision.degraded:
                    logger.info(
                        "[engine] supervisor dispatching %s in degraded mode (%s)",
                        step.agent, decision.reason,
                    )
            except Exception:
                logger.exception(
                    "[engine] supervisor check_before_dispatch failed for %s",
                    step.agent,
                )

        # Check dependencies
        for dep_id in step.depends_on:
            dep_result = results.get(dep_id)
            if dep_result and dep_result.status == StepStatus.FAILED:
                if step.on_failure == "skip" or step.type == StepType.TERMINAL:
                    return StepResult(
                        id=step.id, status=StepStatus.SKIPPED,
                        error=f"Dependency {dep_id} failed",
                        agent=step.agent,
                    )
                else:
                    # P1-4: Dependency failed but step will continue — warn the
                    # user so the default "continue on failure" behavior is not
                    # silent. Backward compatibility is preserved (step still
                    # executes); callers can opt into skip by setting
                    # on_failure='skip'.
                    logger.warning(
                        "[engine] step %s: dependency %s failed but on_failure=%r "
                        "(not 'skip'); step will execute. Set on_failure='skip' to "
                        "skip steps when dependencies fail.",
                        step.id, dep_id, step.on_failure,
                    )

        # Resolve templates in task
        resolved_task = _resolve_template(step.task, context)

        sr: StepResult | None = None
        try:
            if step.type == StepType.TERMINAL:
                # Terminal step: aggregate context
                output = json_dumps_safe({k: v for k, v in context.items()
                                         if not k.startswith("_")})
                sr = StepResult(
                    id=step.id, status=StepStatus.SUCCESS,
                    output=output, agent=step.agent,
                    duration_ms=int((time.monotonic() - start) * 1000),
                )

            elif step.type == StepType.VERIFY:
                # Verify step: check upstream results.
                # P0 修复：缺失依赖必须视为 FAILED，而非被 `if d in results`
                # 静默忽略。否则缺少上游步骤时 upstream_ok 会错误地为 True。
                upstream_ok = all(
                    results.get(
                        d,
                        StepResult(
                            id=d,
                            status=StepStatus.FAILED,
                            error="Dependency not executed",
                        ),
                    ).status
                    == StepStatus.SUCCESS
                    for d in step.depends_on
                )
                if upstream_ok:
                    sr = StepResult(
                        id=step.id, status=StepStatus.SUCCESS,
                        output="All upstream steps passed",
                        agent=step.agent or "verify",
                        duration_ms=int((time.monotonic() - start) * 1000),
                    )
                else:
                    sr = StepResult(
                        id=step.id, status=StepStatus.FAILED,
                        error="Upstream verification failed",
                        agent=step.agent or "verify",
                        duration_ms=int((time.monotonic() - start) * 1000),
                    )

            elif step.type == StepType.CONDITION:
                # Condition step: evaluate params
                condition_expr = step.params.get("expr", "true")
                # Simple boolean evaluation
                try:
                    passed = safe_eval(condition_expr, context)
                except Exception:
                    passed = False
                status = StepStatus.SUCCESS if passed else StepStatus.SKIPPED
                sr = StepResult(
                    id=step.id, status=status,
                    output=str(passed), agent=step.agent,
                    duration_ms=int((time.monotonic() - start) * 1000),
                )

            elif step.type == StepType.PLAN:
                # Plan step: dynamic task decomposition (P1-4)
                substeps = decompose_task(resolved_task, step)
                if substeps:
                    # Execute sub-steps recursively
                    sub_results = []
                    for sub in substeps:
                        sub_sr = await self._execute_step(
                            sub, context, results, workdir, trace_id,
                        )
                        sub_results.append(sub_sr)
                        context[sub.id] = sub_sr.output or sub_sr.error
                    # Aggregate sub-step outputs
                    success = all(r.status == StepStatus.SUCCESS for r in sub_results)
                    output = "\n".join(r.output for r in sub_results if r.output)
                    sr = StepResult(
                        id=step.id,
                        status=StepStatus.SUCCESS if success else StepStatus.FAILED,
                        output=output,
                        error="; ".join(r.error for r in sub_results if r.error) if not success else "",
                        agent=step.agent,
                        duration_ms=int((time.monotonic() - start) * 1000),
                    )
                # No decomposition: fall through to agent execution
                elif self._step_executor is not None:
                    result = await self._step_executor(
                        step=step, context=context, workdir=workdir,
                        trace_id=trace_id,
                    )
                    sr = StepResult(
                        id=step.id, status=StepStatus.SUCCESS,
                        output=result.output if hasattr(result, 'output') else str(result),
                        exit_code=result.exit_code if hasattr(result, 'exit_code') else 0,
                        agent=step.agent,
                        duration_ms=int((time.monotonic() - start) * 1000),
                    )
                else:
                    # No executor configured: this PLAN step has no sub-steps and
                    # no executor to run it as an agent step. Reporting SUCCESS
                    # here would be a false positive (P2-1) — fail fast instead.
                    sr = StepResult(
                        id=step.id, status=StepStatus.FAILED,
                        error=f"No step executor configured for {step.type.value} step '{step.id}'; "
                              f"cannot run plan/agent step. Set engine._step_executor before dispatch.",
                        agent=step.agent,
                        duration_ms=int((time.monotonic() - start) * 1000),
                    )

            elif step.type in (StepType.AGENT, StepType.DAG):
                # Agent/DAG step: use custom executor or mock
                if self._step_executor is not None:
                    max_attempts = 1 + max(0, step.retry)  # P1-6: retry support
                    result_retry: Any = None
                    for attempt in range(max_attempts):
                        result_retry = await self._step_executor(
                            step=step, context=context, workdir=workdir,
                            trace_id=trace_id,
                        )
                        result_error = result_retry.error if hasattr(result_retry, 'error') else None
                        result_exit_code = result_retry.exit_code if hasattr(result_retry, 'exit_code') else 0
                        result_output = result_retry.output if hasattr(result_retry, 'output') else str(result_retry)
                        # P0-1: Check for execution errors — don't blindly report
                        # SUCCESS. If the executor returned a non-empty error or a
                        # non-zero exit_code, the step must be marked FAILED so
                        # callers aren't misled by a false-positive SUCCESS.
                        has_error = bool(result_error) or (result_exit_code != 0)
                        if not has_error or attempt >= max_attempts - 1:
                            break
                        logger.warning(
                            "[engine] step %s attempt %d/%d failed; retrying",
                            step.id, attempt + 1, max_attempts,
                        )

                    # P1-7: fallback_to support — if the step still failed after
                    # all retries and a fallback agent is configured, re-execute
                    # the step with the fallback agent. On fallback success,
                    # promote the fallback result so the step is marked SUCCESS.
                    if has_error and step.fallback_to:
                        logger.info(
                            "[engine] step %s failed; falling back to agent %s",
                            step.id, step.fallback_to,
                        )
                        fallback_step = step.model_copy(
                            update={"agent": step.fallback_to, "fallback_to": ""},
                        )
                        fb_result = await self._step_executor(
                            step=fallback_step, context=context, workdir=workdir,
                            trace_id=trace_id,
                        )
                        fb_error = fb_result.error if hasattr(fb_result, 'error') else None
                        fb_exit = fb_result.exit_code if hasattr(fb_result, 'exit_code') else 0
                        fb_output = fb_result.output if hasattr(fb_result, 'output') else str(fb_result)
                        if not (bool(fb_error) or fb_exit != 0):
                            result_output = fb_output
                            result_exit_code = fb_exit
                            result_error = ""
                            has_error = False

                    sr = StepResult(
                        id=step.id,
                        status=StepStatus.FAILED if has_error else StepStatus.SUCCESS,
                        output=result_output,
                        exit_code=result_exit_code,
                        error=result_error if result_error else "",
                        agent=step.agent,
                        duration_ms=int((time.monotonic() - start) * 1000),
                    )
                else:
                    # No executor configured: cannot actually run agent/DAG step.
                    # Reporting SUCCESS here would be a false positive — fail fast
                    # so callers know an executor must be wired up.
                    sr = StepResult(
                        id=step.id, status=StepStatus.FAILED,
                        error=f"No step executor configured for {step.type.value} step '{step.id}'; "
                              f"cannot run agent/DAG step. Set engine._step_executor before dispatch.",
                        agent=step.agent,
                        duration_ms=int((time.monotonic() - start) * 1000),
                    )

            else:
                sr = StepResult(
                    id=step.id, status=StepStatus.FAILED,
                    error=f"Unknown step type: {step.type}",
                    agent=step.agent,
                    duration_ms=int((time.monotonic() - start) * 1000),
                )

        except Exception as exc:
            sr = StepResult(
                id=step.id, status=StepStatus.FAILED,
                error=str(exc), agent=step.agent,
                duration_ms=int((time.monotonic() - start) * 1000),
            )

        # ── Supervisor: post-dispatch checkpoint ──
        # Record the dispatch outcome so the supervisor's passive layer
        # (sliding window) tracks this agent. No-op when no Supervisor
        # is configured or the step is not an AGENT step.
        if (
            supervisor is not None
            and sr is not None
            and step.type == StepType.AGENT
            and step.agent
            and hasattr(supervisor, "check_after_dispatch")
        ):
            try:
                supervisor.check_after_dispatch(
                    step.agent,
                    success=(sr.status == StepStatus.SUCCESS),
                    latency=(time.monotonic() - start),
                )
            except Exception:
                logger.exception(
                    "[engine] supervisor check_after_dispatch failed for %s",
                    step.agent,
                )

        return sr if sr is not None else StepResult(
            id=step.id, status=StepStatus.FAILED,
            error="unreachable: sr is None after _execute_step",
            agent=step.agent,
            duration_ms=int((time.monotonic() - start) * 1000),
        )


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
]
