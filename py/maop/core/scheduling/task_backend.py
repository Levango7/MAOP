"""MAOP Task Backend — Abstract execution backend for workflow steps.

Defines the ``TaskBackend`` protocol that hides the execution substrate:
steps can run locally through the in-process :class:`WorkerPool`
(``LocalTaskBackend``) or be dispatched to a Redis Streams queue for
execution by remote workers (``DistributedTaskBackend``).

The engine selects a backend via :meth:`Engine._get_task_backend`:
- Personal edition (no Redis) → ``LocalTaskBackend`` (default).
- Enterprise / ``--profile redis`` → ``DistributedTaskBackend`` when
  ``distributed=True``.

This module is part of the M0 implementation (L0.1 T0.1.1 + T0.1.2 + T0.1.3).

Usage::

    from maop.core.scheduling.task_backend import TaskBackend

    backend: TaskBackend = LocalTaskBackend(worker_pool)
    result: EngineResult = await backend.run(steps, context, workdir, trace_id)
"""

from __future__ import annotations

import logging
import time
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

from maop.engine_types import EngineResult, StepResult, StepStatus, WorkflowStep

if TYPE_CHECKING:
    from maop.engine import Engine

logger = logging.getLogger(__name__)


# ── Protocol ──────────────────────────────────────────────────


@runtime_checkable
class TaskBackend(Protocol):
    """Execution backend that processes a DAG of workflow steps.

    Implementations MUST be idempotent: calling ``run()`` multiple times
    with different step lists must not interfere. They MUST NOT mutate
    the input ``steps`` or ``context`` dicts.
    """

    @abstractmethod
    async def run(
        self,
        steps: list[WorkflowStep],
        context: dict[str, Any] | None,
        workdir: str,
        trace_id: str,
    ) -> EngineResult:
        """Execute all steps in topological order and return the aggregate result.

        Parameters
        ----------
        steps : list[WorkflowStep]
            Workflow steps to execute.
        context : dict | None
            Initial context for template resolution.
        workdir : str
            Working directory for execution.
        trace_id : str
            Trace ID for observability (already valid; do not regenerate).

        Returns
        -------
        EngineResult
            Aggregate result with one ``StepResult`` per step.
        """
        ...

    @abstractmethod
    async def shutdown(self) -> None:
        """Release resources (close connections, stop background tasks)."""


# ── ABC variant ────────────────────────────────────────────────


class TaskBackendABC(ABC):
    """Abstract base class variant of :class:`TaskBackend`.

    Use this when class-based inheritance is preferred over structural
    typing (e.g. for ``isinstance`` checks in legacy code).
    """

    @abstractmethod
    async def run(
        self,
        steps: list[WorkflowStep],
        context: dict[str, Any] | None,
        workdir: str,
        trace_id: str,
    ) -> EngineResult:
        ...

    @abstractmethod
    async def shutdown(self) -> None:
        ...


# ── Local Backend (in-process via WorkerPool / asyncio) ──────


class LocalTaskBackend:
    """Local execution backend backed by the in-process event loop.

    This implementation reuses the engine's existing single-process
    DAG execution logic (layer-based parallelism within the asyncio
    event loop). It provides the same semantics as
    ``Engine._run_single()`` without a separate code path in the
    engine constructor.

    Personal edition default (no Redis required).
    """

    def __init__(self, engine: Engine) -> None:
        """Bind to an ``Engine`` instance that provides ``_run_single``.

        Parameters
        ----------
        engine : Engine
            The engine whose ``_run_single`` method will be invoked.
            Using the method directly preserves all single-process
            behaviour (pause, abort, spawns, dynamic decomposition).
        """
        self._engine = engine

    async def run(
        self,
        steps: list[WorkflowStep],
        context: dict[str, Any] | None,
        workdir: str,
        trace_id: str,
    ) -> EngineResult:
        """Delegate to the engine's single-process execution."""
        return await self._engine._run_single(
            steps, context, workdir, trace_id,
        )

    async def shutdown(self) -> None:
        """No-op for local backend (lifetime managed by the engine)."""


# ── Distributed Backend (Redis Streams + DAG worker pool) ────


class DistributedTaskBackend:
    """Distributed execution backend backed by Redis Streams.

    Wraps :class:`~maop.core.scheduling.distributed_scheduler.DistributedScheduler`
    and maps its :class:`DistributedResult` back to an
    :class:`EngineResult` so the caller is agnostic to the substrate.

    Redis is required to construct this backend. When Redis is
    unavailable, the engine falls back to :class:`LocalTaskBackend`.
    """

    def __init__(
        self,
        redis_client: Any,
        *,
        registry: Any = None,
        task_stream: str = "maop:sched:tasks",
        results_prefix: str = "maop:sched:results",
        poll_interval: float = 0.1,
        failure_check_interval: float = 1.0,
        max_retries: int = 2,
    ) -> None:
        self._redis = redis_client
        self._task_stream = task_stream
        self._registry = registry
        self._results_prefix = results_prefix
        self._poll_interval = poll_interval
        self._failure_check_interval = failure_check_interval
        self._max_retries = max_retries
        self._scheduler: Any = None  # lazy-initialised

    def _get_scheduler(self) -> Any:
        """Lazily build the DistributedScheduler."""
        if self._scheduler is None:
            from maop.core.scheduling.distributed_scheduler import (
                DistributedScheduler,
            )

            self._scheduler = DistributedScheduler(
                self._redis,
                registry=self._registry,
                task_stream=self._task_stream,
                results_prefix=self._results_prefix,
                poll_interval=self._poll_interval,
                failure_check_interval=self._failure_check_interval,
                max_retries=self._max_retries,
            )
        return self._scheduler

    async def run(
        self,
        steps: list[WorkflowStep],
        context: dict[str, Any] | None,
        workdir: str,
        trace_id: str,
    ) -> EngineResult:
        """Execute steps via the Redis Streams distributed worker pool.

        Builds :class:`_NodeSpec` objects from the workflow steps,
        dispatches them to the scheduler, and aggregates the results
        back into an :class:`EngineResult`.
        """
        from maop.core.scheduling.distributed_scheduler import (
            node_spec_from_step,
        )

        scheduler = self._get_scheduler()
        start = time.monotonic()

        if context is None:
            context = {}
        ctx = dict(context)

        # Build node specs from workflow steps
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
                    "task": step.task,
                    "params": step.params,
                    "workdir": workdir,
                    "trace_id": trace_id,
                },
                timeout=float(step.timeout),
            ))

        # Run on distributed workers
        dist_result = await scheduler.run(nodes, run_id=trace_id)

        # Aggregate distributed results into EngineResult
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

    async def shutdown(self) -> None:
        """No-op for distributed backend (Redis client owned externally)."""
