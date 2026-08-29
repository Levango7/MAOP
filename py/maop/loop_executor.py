"""MAOP Loop Executor — Execution strategies for the MAOP Loop.

Extracted from maop_loop.py for single-responsibility separation.
Provides a Mixin that MaopLoop inherits to gain execution methods.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, cast

from maop.core.agent.analyzer import AnalysisResult, ExecutionStrategy
from maop.core.reliability.error_schema import MaopResult, new_result

logger = logging.getLogger(__name__)


class ExecuteMixin:
    """Mixin providing execution strategy methods for MaopLoop.

    Requires the host class to have:
      - self._dispatcher (Dispatcher)
      - self._worker_pool (WorkerPool | None)
      - self._loop_config (LoopConfig)
      - self._log(phase, level, message, **data)
    """

    _dispatcher: Any
    _worker_pool: Any
    _loop_config: Any
    _log: Any

    @staticmethod
    def _should_execute_parallel(analysis, worker_pool):
        """NOTE: mirrors PhasesMixin._should_execute_parallel in maop_loop_phases.py.

        P3 fix: 提取并行判定条件为单一辅助方法，消除与
        ``PhasesMixin._phase_execute`` 中重复的 4 项合取条件，
        避免日后修改一处漏改另一处导致行为分叉。
        """
        return (analysis is not None
                and analysis.strategy in (ExecutionStrategy.PARALLEL, ExecutionStrategy.HYBRID)
                and analysis.sub_tasks and len(analysis.sub_tasks) > 1
                and worker_pool is not None)

    def _get_supervisor(self) -> Any:
        """Lazily return the process-wide Supervisor singleton.

        Returns ``None`` when no Supervisor has been configured
        (passive-only mode). The retry path takes the ``None`` branch
        and uses the static ``LoopConfig`` values — full backward
        compatibility.
        """
        try:
            from maop.core.scheduling.failure_detector import get_supervisor

            return get_supervisor()
        except Exception:
            return None

    @staticmethod
    def _get_downstream(node: str, dag: Any) -> list[str]:
        """Return all transitive downstream (successor) nodes of *node* in *dag*.

        Uses BFS over the DAG edges to collect every node reachable from
        *node* via one or more edges.
        """
        adj: dict[str, list[str]] = {}
        for src, dst in dag.edges:
            adj.setdefault(src, []).append(dst)
        visited: set[str] = set()
        queue = list(adj.get(node, []))
        while queue:
            current = queue.pop(0)
            if current in visited:
                continue
            visited.add(current)
            queue.extend(adj.get(current, []))
        return list(visited)

    async def _execute_with_strategy(
        self,
        task: str,
        analysis: AnalysisResult | None,
        fallback_chain: list[str],
        routing_key: str,
        workdir: str,
        timeout: int,
        retry: bool,
        trace_id: str,
    ) -> MaopResult | None:
        """Execute with strategy awareness — parallel or sequential.

        If analyzer produced a DAG with parallel groups and WorkerPool is available,
        execute independent subtasks in parallel. Otherwise, fall back to sequential.
        """
        # Check if we can execute in parallel
        if self._should_execute_parallel(analysis, self._worker_pool):
            # _should_execute_parallel guarantees analysis is not None here,
            # but mypy cannot infer that from the helper's return type.
            assert analysis is not None

            # Execute subtasks via WorkerPool
            return await self._execute_parallel(
                task=task, analysis=analysis,
                fallback_chain=fallback_chain, routing_key=routing_key,
                workdir=workdir, timeout=timeout, trace_id=trace_id,
            )

        # Sequential execution (original behavior)
        return await self._execute_with_retry(
            task=task, fallback_chain=fallback_chain,
            routing_key=routing_key, workdir=workdir,
            timeout=timeout, retry=retry, trace_id=trace_id,
        )

    async def _execute_parallel(
        self,
        task: str,
        analysis: AnalysisResult,
        fallback_chain: list[str],
        routing_key: str,
        workdir: str,
        timeout: int,
        trace_id: str,
    ) -> MaopResult:
        """Execute subtasks in parallel using WorkerPool + DAG parallel groups."""
        groups = analysis.dag.parallel_groups()
        self._log("execute", "INFO",
                  f"Parallel execution: {len(analysis.sub_tasks)} subtasks in {len(groups)} groups",
                  trace_id=trace_id)

        # Use WorkerPool semaphore for concurrency control if available
        sem = None
        if self._worker_pool:
            sem = getattr(self._worker_pool, "_sem", None)
        if sem is None:
            sem = asyncio.Semaphore(self._loop_config.max_workers)

        subtask_results: dict[str, MaopResult] = {}
        agent = fallback_chain[0] if fallback_chain else "claude"

        for group_idx, group in enumerate(groups):
            if len(group) == 1:
                # Single subtask — execute directly
                st_id = group[0]
                st = next((s for s in analysis.sub_tasks if s.id == st_id), None)
                if st:
                    st_agent = st.assigned_agent or agent
                    # P1 fix: wrap in try/except to match parallel branch safety
                    try:
                        result = await self._execute_with_retry(
                            task=st.description, fallback_chain=[st_agent],
                            routing_key=routing_key, workdir=workdir,
                            timeout=timeout, retry=False, trace_id=trace_id,
                        )
                    except Exception as exc:
                        logger.warning("Subtask %s raised exception: %s", st_id, exc)
                        result = new_result(
                            agent=st_agent, task=st.description, exit_code=1,
                            error=f"Exception: {exc}", trace_id=trace_id,
                        )
                    subtask_results[st_id] = result  # type: ignore
            else:
                # Multiple independent subtasks — execute in parallel with concurrency limit
                async def _run_subtask(st_id: str, st_desc: str, st_agent: str) -> tuple[str, MaopResult]:
                    async with sem:
                        try:
                            r = await self._execute_with_retry(
                                task=st_desc, fallback_chain=[st_agent],
                                routing_key=routing_key, workdir=workdir,
                                timeout=timeout, retry=False, trace_id=trace_id,
                            )
                        except Exception as exc:
                            # P1 fix: catch exception internally so it counts as failure
                            logger.warning("Subtask %s raised exception: %s", st_id, exc)
                            r = new_result(
                                agent=st_agent, task=st_desc, exit_code=1,
                                error=f"Exception: {exc}", trace_id=trace_id,
                            )
                    return st_id, r  # type: ignore

                coros = []
                for st_id in group:
                    st = next((s for s in analysis.sub_tasks if s.id == st_id), None)
                    if st:
                        st_agent = st.assigned_agent or agent
                        coros.append(_run_subtask(st_id, st.description, st_agent))

                # Run in parallel
                group_results = await asyncio.gather(*coros, return_exceptions=True)
                for idx, gr in enumerate(group_results):
                    if isinstance(gr, Exception):
                        # P1 fix: construct failure result instead of skipping
                        # (safety net — _run_subtask should catch internally)
                        st_id = group[idx] if idx < len(group) else f"unknown_{idx}"
                        logger.warning("Parallel subtask %s failed: %s", st_id, gr)
                        subtask_results[st_id] = new_result(
                            agent=agent, task="parallel_subtask", exit_code=1,
                            error=f"Exception: {gr}", trace_id=trace_id,
                        )
                        continue
                    st_id, result = gr  # type: ignore
                    subtask_results[st_id] = result

            self._log("execute", "INFO",
                      f"Group {group_idx+1}/{len(groups)} completed: {len(group)} subtasks",
                      trace_id=trace_id)

        # Aggregate results
        all_success = all(r.is_success() for r in subtask_results.values()) if subtask_results else False
        # P1-4 fix: full subtask stdout used to be truncated to 200 chars here,
        # so verify-phase consumers received fragments and could mis-judge
        # multi-subtask results. Pass complete outputs; per-subtask cap only
        # as an anti-bloat guard for extremely large agent outputs, sized
        # well above realistic CLI output and configurable via
        # LoopConfig.parallel_subtask_stdout_cap (0 = unlimited).
        cap = int(getattr(self._loop_config, "parallel_subtask_stdout_cap", 200_000))
        def _fmt_out(r: MaopResult) -> str:
            s = r.stdout or ""
            return s[:cap] if cap > 0 else s
        combined_stdout = "\n".join(
            f"[{st_id}] {_fmt_out(r)}" for st_id, r in subtask_results.items() if r.stdout
        )
        combined_errors = [r for r in subtask_results.values() if not r.is_success()]

        return new_result(
            agent=agent, task=task,
            exit_code=0 if all_success else 1,
            stdout=combined_stdout,
            error="\n".join(r.error for r in combined_errors if r.error) or None,
            duration_ms=max((r.duration_ms for r in subtask_results.values()), default=0),
            trace_id=trace_id, routing_key=routing_key,
        )

    async def _execute_with_retry(
        self,
        task: str,
        fallback_chain: list[str],
        routing_key: str,
        workdir: str,
        timeout: int,
        retry: bool,
        trace_id: str,
    ) -> MaopResult | None:
        """Execute with fallback chain and iterative retry.

        The fallback chain is always used in full — if the primary agent fails,
        we try the next agent in the chain. The ``retry`` parameter controls
        whether iterative retry (re-trying the same agent multiple times with
        backoff) is enabled; it no longer gates the fallback chain itself.

        Supervisor integration: when a Supervisor is configured, the
        per-agent retry strategy is queried dynamically (``max_attempts``
        reduced for degraded agents, ``skip_agent=True`` for terminated
        agents). When no Supervisor is configured, the static
        ``LoopConfig.iterative_max_attempts`` / ``iterative_backoff_ms``
        values are used — full backward compatibility.
        """
        lc = self._loop_config
        supervisor = self._get_supervisor()
        agents = fallback_chain  # Always use full fallback chain (P1-10 fix)
        result = None

        for attempt, agent in enumerate(agents):
            # ── Supervisor: query dynamic retry strategy ──
            if (
                supervisor is not None
                and hasattr(supervisor, "get_retry_strategy")
            ):
                try:
                    strategy = supervisor.get_retry_strategy(
                        agent,
                        default_max_attempts=lc.iterative_max_attempts,
                        default_backoff_ms=lc.iterative_backoff_ms,
                    )
                    if strategy.get("skip_agent"):
                        logger.info(
                            "[loop-executor] supervisor skip agent %s: %s",
                            agent, strategy,
                        )
                        continue
                    # When retry=False (parallel execution), still only
                    # one iteration per agent regardless of strategy.
                    max_iterations = (
                        int(strategy.get("max_attempts", lc.iterative_max_attempts))
                        if retry else 1
                    )
                    backoff_ms = int(
                        strategy.get("backoff_ms", lc.iterative_backoff_ms)
                    )
                except Exception:
                    logger.exception(
                        "[loop-executor] supervisor get_retry_strategy failed for %s",
                        agent,
                    )
                    max_iterations = lc.iterative_max_attempts if retry else 1
                    backoff_ms = lc.iterative_backoff_ms
            else:
                # No supervisor: use static config (original behaviour).
                max_iterations = lc.iterative_max_attempts if retry else 1
                backoff_ms = lc.iterative_backoff_ms

            if attempt > 0:
                await asyncio.sleep(lc.retry_backoff_ms / 1000)

            for iteration in range(max_iterations):
                if iteration > 0:
                    await asyncio.sleep(backoff_ms / 1000)

                try:
                    dispatch_result = await self._dispatcher.dispatch(
                        agent=agent, task=task,
                        routing_key=routing_key, workdir=workdir,
                        timeout_seconds=timeout, trace_id=trace_id,
                    )
                    result = dispatch_result.result

                    if result.is_success():
                        return cast(MaopResult | None, result)

                    # P2-10 fix: if breaker tripped, stop retrying this agent
                    # and move to next in fallback chain immediately.
                    if getattr(dispatch_result, 'breaker_tripped', False):
                        logger.warning(
                            "Agent %s breaker tripped, skipping remaining retries",
                            agent,
                        )
                        break

                    logger.info("Agent %s failed (exit=%d), iter=%d",
                                agent, result.exit_code, iteration + 1)
                except Exception as exc:
                    # P2-9 fix: construct failure result so breaker records it
                    logger.warning("Agent %s threw exception: %s", agent, exc)
                    result = new_result(
                        agent=agent, task=task, exit_code=1,
                        error=f"Exception: {exc}", trace_id=trace_id,
                    )

            if result and result.is_success():
                return cast(MaopResult | None, result)

        # All agents failed
        if result is None:
            result = new_result(
                agent="none", task=task,
                exit_code=-1, error="All agents failed",
                trace_id=trace_id, routing_key=routing_key,
            )
        return result
