"""MAOP Engine — single-step execution helper.

Extracted from ``engine.py`` (Phase 3-1 split). The ``_execute_step``
method body lives here as a standalone helper function
``execute_step_helper`` so that ``engine.py`` stays under the 500-line
budget. The ``Engine._execute_step`` method becomes a thin wrapper that
delegates to this helper.

The helper receives the ``engine`` instance and accesses engine state
(``_step_executor``, ``_get_supervisor()``, ``_decompose_task()``) via
that instance — no behaviour change, pure physical split.
"""

from __future__ import annotations

import logging
import time
from typing import Any

from maop.engine_types import (
    StepResult,
    StepStatus,
    StepType,
    WorkflowStep,
)
from maop.engine_utils import (
    _resolve_template,
    json_dumps_safe,
    safe_eval,
)

logger = logging.getLogger(__name__)

# P1-5 fix: PLAN-step dynamic decomposition recursion cap. Each recursion
# level multiplies the number of steps; without a cap, adversarial/looping
# task text (nested separators) could recurse until stack exhaustion.
_MAX_PLAN_DEPTH: int = 5


async def execute_step_helper(
    engine: Any,
    step: WorkflowStep,
    context: dict[str, Any],
    results: dict[str, StepResult],
    workdir: str,
    trace_id: str,
) -> StepResult:
    """Execute a single workflow step.

    This is the body of :meth:`Engine._execute_step`, extracted as a
    standalone helper. It receives the ``engine`` instance so it can
    reach ``engine._step_executor``, ``engine._get_supervisor()``,
    ``engine._decompose_task()`` and recurse via
    ``engine._execute_step()``.

    Parameters
    ----------
    engine : Engine
        The engine instance owning this step execution.
    step : WorkflowStep
        The step to execute.
    context : dict[str, Any]
        Live context for template resolution (mutated in place for
        PLAN sub-step outputs).
    results : dict[str, StepResult]
        Results of already-executed steps (dependency lookup).
    workdir : str
        Working directory passed to the step executor.
    trace_id : str
        Trace ID for observability.

    Returns
    -------
    StepResult
        Result of executing this step.
    """
    start = time.monotonic()

    # ── Supervisor: pre-dispatch checkpoint ──
    # When a Supervisor is configured, ask it whether this agent may
    # be dispatched. Terminated / drained agents are skipped (with
    # optional fallback). When no Supervisor is configured, this
    # branch is a no-op (full backward compatibility).
    supervisor = engine._get_supervisor() if step.type == StepType.AGENT and step.agent else None
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
            # Verify step: check upstream results
            upstream_ok = all(
                results[d].status == StepStatus.SUCCESS
                for d in step.depends_on
                if d in results
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
            # P1-5 fix: recursion guard. _decompose_task splits compound
            # tasks into sub-steps which are executed via a recursive
            # _execute_step call; without a depth cap a pathological
            # task (nested semicolon/numbered lists) recurses without
            # bound. Depth is carried in params["_plan_depth"]; at the
            # cap the step degrades to atomic execution (executor path)
            # instead of splitting further.
            depth = 0
            if isinstance(step.params, dict):
                try:
                    depth = int(step.params.get("_plan_depth", 0))
                except (TypeError, ValueError):
                    depth = 0
            substeps: list[WorkflowStep] = []
            if depth < _MAX_PLAN_DEPTH:
                substeps = engine._decompose_task(resolved_task, step)
                substeps = [
                    sub.model_copy(update={
                        "params": {**(sub.params or {}), "_plan_depth": depth + 1},
                    })
                    for sub in substeps
                ]
            else:
                logger.info(
                    "[engine] step %s: plan decomposition depth cap %d reached; "
                    "executing as atomic task",
                    step.id, _MAX_PLAN_DEPTH,
                )
            if substeps:
                # P2-fix: PLAN 子步骤依赖检查——防止循环依赖或乱序执行。
                # 1) 检查子步骤ID无重复；2) 检查显式依赖引用的步骤ID存在；
                # 3) 检测循环依赖。发现问题时记录 error 并跳过有问题的子步骤。
                _sub_ids = [s.id for s in substeps]
                _id_set = set()
                _seen_ids: set[str] = set()
                for _sid in _sub_ids:
                    if _sid in _id_set:
                        logger.error(
                            "[engine] step %s: plan 子步骤 ID 重复 '%s'，跳过重复项",
                            step.id, _sid,
                        )
                    else:
                        _id_set.add(_sid)
                        _seen_ids.add(_sid)
                # 过滤掉重复 ID 的子步骤（保留首次出现）
                _deduped: list[WorkflowStep] = []
                for sub in substeps:
                    if sub.id not in _seen_ids:
                        continue  # 重复 ID，已跳过
                    _seen_ids.discard(sub.id)  # 标记已处理
                    # 检查依赖引用是否存在
                    _missing_deps = [
                        dep for dep in (sub.depends_on or [])
                        if dep not in _id_set and dep != step.id
                    ]
                    if _missing_deps:
                        logger.error(
                            "[engine] step %s: 子步骤 '%s' 引用了不存在的依赖 %s，跳过该子步骤",
                            step.id, sub.id, _missing_deps,
                        )
                        continue
                    _deduped.append(sub)
                # 简单循环依赖检测：DFS 检查是否有子步骤通过 depends_on 形成环
                _dep_graph = {s.id: set(s.depends_on or []) & _id_set for s in _deduped}
                _visiting: set[str] = set()
                _visited: set[str] = set()
                _cyclic_ids: set[str] = set()

                def _detect_cycle(_node: str, _path: set[str]) -> None:
                    if _node in _visited:
                        return
                    if _node in _path:
                        _cyclic_ids.update(_path)
                        return
                    _path.add(_node)
                    for _nbr in _dep_graph.get(_node, set()):
                        _detect_cycle(_nbr, _path)
                    _path.discard(_node)
                    _visited.add(_node)

                for _nid in _dep_graph:
                    _detect_cycle(_nid, set())
                if _cyclic_ids:
                    logger.error(
                        "[engine] step %s: 检测到循环依赖 %s，跳过相关子步骤",
                        step.id, sorted(_cyclic_ids),
                    )
                    _deduped = [s for s in _deduped if s.id not in _cyclic_ids]
                substeps = _deduped
            if substeps:
                # Execute sub-steps recursively
                sub_results = []
                for sub in substeps:
                    sub_sr = await engine._execute_step(
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
            elif engine._step_executor is not None:
                result = await engine._step_executor(
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
            if engine._step_executor is not None:
                max_attempts = 1 + max(0, step.retry)  # P1-6: retry support
                result: Any = None  # type: ignore[no-redef]
                for attempt in range(max_attempts):
                    result = await engine._step_executor(
                        step=step, context=context, workdir=workdir,
                        trace_id=trace_id,
                    )
                    result_error = result.error if hasattr(result, 'error') else None
                    result_exit_code = result.exit_code if hasattr(result, 'exit_code') else 0
                    result_output = result.output if hasattr(result, 'output') else str(result)
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
                    fb_result = await engine._step_executor(
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