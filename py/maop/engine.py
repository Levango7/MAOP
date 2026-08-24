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
                substeps = self._decompose_task(resolved_task, step)
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

        Notes
        -----
        Strategy 4（"and" 分割）在 M4 修复 3.14 中收紧：仅当任务长度 > 30、
        每段长度 > 10、分割后每段以动词性词开头、且 "X and Y" 不在固定短语
        黑名单（如 "research and development"）中时才分割，避免误拆常见短语。
        """
        substeps: list[WorkflowStep] = []

        # Strategy 1: Semicolon-separated tasks
        if ";" in task:
            parts = [p.strip() for p in task.split(";") if p.strip()]
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
        import re as _re
        numbered = _re.findall(r'\d+\.\s+(.+?)(?=\d+\.|$)', task, _re.DOTALL)
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
        bullets = _re.findall(r'^[-*]\s+(.+)$', task, _re.MULTILINE)
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

        # Strategy 4: "and" conjunction (M4 修复 3.14：收紧分割条件)
        #
        # 原策略仅要求每段长度 > 10，会错误分割 "research and development
        # the new feature" 这类含固定短语的句子，以及 "analyze the data and
        # generate a report" 这种本应分割但前后词性不明确的场景。
        #
        # 收紧后的条件（全部满足才分割）：
        # 1. 任务总长度 > 40（短任务几乎不需要拆分）；
        # 2. 分割点前后的词都是动词性词（启发式：常见动词集合或动词后缀）；
        # 3. 整个 "X and Y" 不在常见固定短语黑名单中（如 "research and
        #    development"、"supply and demand"）；
        # 4. 每段长度 > 10（保留原条件，避免过短碎片）。
        _AND_PHRASE_BLACKLIST = {
            "research and development", "supply and demand", "salt and pepper",
            "black and white", "back and forth", "up and down", "in and out",
            "trial and error", "peace and quiet", "law and order",
            "pots and pans", "bread and butter", "cause and effect",
            "pros and cons", "do's and don'ts", "men and women", "boys and girls",
            "input and output", "read and write", "open and close",
            "start and end", "begin and end", "old and new", "large and small",
            "high and low", "long and short", "thick and thin", "fast and slow",
            "right and wrong", "true and false", "yes and no", "win and lose",
            "pass and fail", "add and remove", "insert and delete",
            "create and destroy", "build and destroy", "lock and unlock",
            "encrypt and decrypt", "encode and decode", "compress and decompress",
            "serialize and deserialize", "connect and disconnect",
            "subscribe and unsubscribe", "load and save", "push and pop",
            "enqueue and dequeue", "grant and revoke", "allow and deny",
            "accept and reject", "send and receive", "request and response",
            "get and set", "fetch and store", "pull and push", "fork and join",
            "spawn and wait", "start and stop", "pause and resume",
            "mount and unmount", "attach and detach",
            "enable and disable", "show and hide", "expand and collapse",
            "zoom in and zoom out", "log in and log out", "sign in and sign out",
        }
        _VERB_SUFFIXES = ("ing", "ize", "ise", "ate", "ify", "ed", "es", "en")
        _COMMON_VERBS = {
            "implement", "test", "build", "deploy", "create", "write", "design",
            "validate", "configure", "monitor", "analyze", "optimise", "optimize",
            "refactor", "migrate", "integrate", "generate", "parse", "fetch",
            "process", "update", "remove", "delete", "add", "fix", "check",
            "run", "execute", "compile", "install", "start", "stop", "restart",
            "train", "evaluate", "predict", "infer", "serialize", "deserialize",
            "encode", "decode", "encrypt", "decrypt", "compress", "decompress",
            "load", "save", "read", "open", "close", "send", "receive",
            "push", "pull", "store", "get", "set", "find", "search",
            "scan", "filter", "sort", "group", "merge", "split", "join", "map",
            "reduce", "transform", "convert", "translate", "render", "display",
            "print", "log", "trace", "debug", "inspect", "audit", "review",
            "approve", "reject", "accept", "cancel", "abort", "retry", "resume",
            "pause", "wait", "notify", "alert", "report", "export", "import",
            "backup", "restore", "archive", "unarchive", "pack", "unpack",
            "extract", "package", "publish", "release", "rollback", "revert",
            "apply", "commit", "checkout", "branch", "tag", "rebase", "clone", "init", "setup", "teardown", "provision",
            "deprovision", "scale", "resize", "rotate", "move", "copy", "paste",
            "cut", "undo", "redo", "select", "deselect", "highlight", "focus",
            "blur", "click", "tap", "swipe", "scroll", "drag", "drop", "hover",
            "type", "input", "submit", "reset", "clear", "fill", "empty",
            "populate", "vacuum", "compact", "defragment", "format", "erase",
            "wipe", "clean", "purge", "evict", "expire", "refresh", "reload",
            "reboot", "shutdown", "power", "wake", "sleep", "hibernate",
        }

        def _is_verb_like(word: str) -> bool:
            """启发式判断一个词是否为动词性词。

            规则（任一满足即返回 True）：
            - 词在常见动词集合中；
            - 词以常见动词后缀结尾且长度 >= 4（避免 "ed"、"es" 等被误判）。
            """
            w = word.lower().strip()
            if not w:
                return False
            if w in _COMMON_VERBS:
                return True
            return len(w) >= 4 and any(w.endswith(suf) for suf in _VERB_SUFFIXES)

        def _is_blacklist_phrase(left: str, right: str) -> bool:
            """检查 "X and Y" 是否为固定短语黑名单中的成员。

            取左右各 1 个词拼接成 "x and y" 后小写匹配黑名单。
            """
            left_words = left.split()
            right_words = right.split()
            if not left_words or not right_words:
                return False
            phrase = f"{left_words[-1]} and {right_words[0]}".lower()
            return phrase in _AND_PHRASE_BLACKLIST

        if len(task) > 30:
            and_parts = _re.split(r'\s+and\s+', task, maxsplit=2)
            if len(and_parts) > 1 and all(len(p) > 10 for p in and_parts):
                # 逐个分割点检查：
                # - "X and Y" 不在固定短语黑名单（取 left 末词 + right 首词匹配）；
                # - 分割后每段以动词性词开头（"implement X and test Y" 中
                #   "implement" 和 "test" 均为动词），避免误拆 "research and
                #   development" 这类名词短语。
                split_ok = True
                for i in range(len(and_parts) - 1):
                    left = and_parts[i]
                    right = and_parts[i + 1]
                    left_words = left.split()
                    right_words = right.split()
                    if not left_words or not right_words:
                        split_ok = False
                        break
                    if _is_blacklist_phrase(left, right):
                        split_ok = False
                        break
                    # 检查每段首词是否动词性词
                    if not (_is_verb_like(left_words[0])
                            and _is_verb_like(right_words[0])):
                        split_ok = False
                        break
                if split_ok:
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
]
