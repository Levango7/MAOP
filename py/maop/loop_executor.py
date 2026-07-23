"""MAOP Loop Executor — Execution strategies for the MAOP Loop.

Extracted from maop_loop.py for single-responsibility separation.
Provides a Mixin that MaopLoop inherits to gain execution methods.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, cast

from maop.core.analyzer import AnalysisResult, ExecutionStrategy
from maop.core.error_schema import MaopResult, new_result

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
        if (analysis and analysis.strategy in (ExecutionStrategy.PARALLEL, ExecutionStrategy.HYBRID)
                and analysis.sub_tasks and len(analysis.sub_tasks) > 1
                and self._worker_pool):

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
                    result = await self._execute_with_retry(
                        task=st.description, fallback_chain=[st_agent],
                        routing_key=routing_key, workdir=workdir,
                        timeout=timeout, retry=False, trace_id=trace_id,
                    )
                    subtask_results[st_id] = result  # type: ignore[assignment]
            else:
                # Multiple independent subtasks — execute in parallel with concurrency limit
                async def _run_subtask(st_id: str, st_desc: str, st_agent: str) -> tuple[str, MaopResult]:
                    async with sem:
                        r = await self._execute_with_retry(
                            task=st_desc, fallback_chain=[st_agent],
                            routing_key=routing_key, workdir=workdir,
                            timeout=timeout, retry=False, trace_id=trace_id,
                        )
                    return st_id, r  # type: ignore[return-value]

                coros = []
                for st_id in group:
                    st = next((s for s in analysis.sub_tasks if s.id == st_id), None)
                    if st:
                        st_agent = st.assigned_agent or agent
                        coros.append(_run_subtask(st_id, st.description, st_agent))

                # Run in parallel
                group_results = await asyncio.gather(*coros, return_exceptions=True)
                for gr in group_results:
                    if isinstance(gr, Exception):
                        # B-P0-3 fix: continue on exception, was using undefined
                        # st_id/result from previous iteration causing NameError
                        # or wrong data
                        logger.warning("Parallel subtask failed: %s", gr)
                        continue
                    st_id, result = gr  # type: ignore[misc]
                    subtask_results[st_id] = result  # type: ignore[assignment]

            self._log("execute", "INFO",
                      f"Group {group_idx+1}/{len(groups)} completed: {len(group)} subtasks",
                      trace_id=trace_id)

        # Aggregate results
        all_success = all(r.is_success() for r in subtask_results.values()) if subtask_results else False
        combined_stdout = "\n".join(
            f"[{st_id}] {r.stdout[:200]}" for st_id, r in subtask_results.items() if r.stdout
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
        """
        lc = self._loop_config
        agents = fallback_chain  # Always use full fallback chain (P1-10 fix)
        result = None

        for attempt, agent in enumerate(agents):
            if attempt > 0:
                await asyncio.sleep(lc.retry_backoff_ms / 1000)

            # B-P0-2 fix: respect retry parameter — when False (parallel
            # execution), only try once per agent, no iterative retry
            max_iterations = lc.iterative_max_attempts if retry else 1
            for iteration in range(max_iterations):
                if iteration > 0:
                    await asyncio.sleep(lc.iterative_backoff_ms / 1000)

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
                    logger.warning("Agent %s threw exception: %s", agent, exc)

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
