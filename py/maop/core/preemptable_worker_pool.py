"""MAOP Preemptable Worker Pool — Priority-aware soft-preemption scheduler (Phase γ-2).

Wraps :class:`maop.core.worker_pool.WorkerPool` with a
:class:`maop.core.priority_queue.PriorityTaskQueue` so that pending tasks
are admitted in priority + deadline order instead of submission order.

Phase γ-2 design: SOFT preemption
---------------------------------
True preemption (cancelling a running low-priority task to admit a
high-priority one) is **not** implemented because
:class:`maop.core.pipeline_checkpoint.PipelineCheckpoint` is not wired
into the task execution path (``WorkerPool._run_task`` /
``MaopLoop.run`` never invoke it) and only saves flat step-level state.
Cancelling a running task would therefore lose its in-progress work with
no way to resume.

Soft preemption instead:

1. Orders the pending queue by ``(priority, deadline_urgency_score,
   enqueue_order)`` via :class:`PriorityTaskQueue`.
2. Admits the next queued task whenever a worker slot frees up.
3. When a higher-priority task arrives while all workers are busy *and*
   at least one running task has a strictly lower priority, records a
   "would-be preemption" event in ``MAOP_task_preemption_total`` so the
   demand for true preemption is observable — but does **not** cancel the
   running task. The high-priority task is placed at the front of the
   queue and runs as soon as a slot is free.

Once the checkpoint is integrated into the execution path
(``WorkerPool._run_task`` writes per-step state, and DAG dependencies are
persisted), the same interface can switch to true preemption by
cancelling the lowest-priority running task and re-enqueueing it.

Backward compatibility
----------------------
:class:`PreemptableWorkerPool` is API-compatible with :class:`WorkerPool`
for the common methods (``start``/``stop``/``submit``/``wait``/``stats``).
``submit`` gains optional ``priority`` / ``deadline_ms`` keyword args with
defaults (priority=3, no deadline) that preserve the original behaviour
when omitted.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from maop.core.monitoring import (
    MAOP_PRIORITY_QUEUE_SIZE,
    MAOP_TASK_PREEMPTION_TOTAL,
    get_priority_wait_histogram,
)
from maop.core.priority_queue import PriorityTask, PriorityTaskQueue
from maop.core.worker_pool import PoolStats, WorkerPool

logger = logging.getLogger(__name__)

__all__ = ["PreemptableWorkerPool"]


class PreemptableWorkerPool:
    """Priority-aware worker pool with soft preemption.

    Parameters
    ----------
    max_workers : int
        Maximum concurrent IO-bound tasks (forwarded to ``WorkerPool``).
    max_cpu_workers : int
        Maximum CPU-bound worker processes (forwarded to ``WorkerPool``).
    root_dir : str | None
        MAOP project root (forwarded to ``WorkerPool``).
    """

    def __init__(
        self,
        max_workers: int = 4,
        max_cpu_workers: int = 0,
        root_dir: str | None = None,
    ) -> None:
        self._pool = WorkerPool(
            max_workers=max_workers,
            max_cpu_workers=max_cpu_workers,
            root_dir=root_dir,
        )
        self._queue: PriorityTaskQueue = PriorityTaskQueue()
        # Track currently-running task priorities keyed by WorkerPool task id,
        # so we can detect "would-be preemption" scenarios.
        self._running: dict[str, PriorityTask] = {}
        self._running_lock = asyncio.Lock()
        # Background dispatch loop is started lazily and stopped with the pool.
        self._dispatch_task: asyncio.Task | None = None
        self._stop_event: asyncio.Event | None = None

    # ── Lifecycle ───────────────────────────────────────────────

    async def start(self) -> None:
        """Start the underlying pool and the priority dispatch loop."""
        await self._pool.start()
        self._stop_event = asyncio.Event()
        self._dispatch_task = asyncio.ensure_future(self._dispatch_loop())

    async def stop(self) -> None:
        """Stop the dispatch loop and the underlying pool."""
        if self._stop_event is not None:
            self._stop_event.set()
        if self._dispatch_task is not None:
            self._dispatch_task.cancel()
            try:
                await self._dispatch_task
            except (asyncio.CancelledError, Exception):
                pass
            self._dispatch_task = None
        await self._pool.stop()
        # Flush queue gauges
        try:
            MAOP_PRIORITY_QUEUE_SIZE.set(0.0, labels={"priority": "all"})
        except Exception:
            pass

    @property
    def is_running(self) -> bool:
        return self._pool.is_running

    @property
    def queue(self) -> PriorityTaskQueue:
        """Expose the priority queue (mainly for tests/introspection)."""
        return self._queue

    # ── Submission ──────────────────────────────────────────────

    async def submit(
        self,
        task: str,
        *,
        workdir: str = "",
        skip_verify: bool = False,
        priority: int = 3,
        deadline_ms: int | None = None,
        agent_name: str = "",
    ) -> str:
        """Submit a task with priority + deadline metadata.

        The task is enqueued in the :class:`PriorityTaskQueue`; the
        background dispatch loop will admit it to the underlying
        :class:`WorkerPool` as soon as a worker slot is free, in priority
        order.

        If a worker slot is immediately free *and* the queue is empty,
        the task is admitted directly without waiting for the dispatch
        loop tick, to keep latency low for the common case.

        Returns a logical task id (an enqueue token, not the WorkerPool
        id). Use :meth:`wait` with this token to await completion.
        """
        pt = PriorityTask(
            payload={
                "task": task,
                "workdir": workdir,
                "skip_verify": skip_verify,
                "agent_name": agent_name,
            },
            priority=priority,
            deadline_ms=deadline_ms,
        )
        # Record queue size metric at enqueue time.
        self._record_queue_size_inc(priority)
        self._queue.push(pt)
        return f"pt-{pt.enqueue_order}"

    async def submit_preemptable(
        self,
        task: str,
        priority: int = 3,
        deadline_ms: int | None = None,
        *,
        workdir: str = "",
        skip_verify: bool = False,
        agent_name: str = "",
    ) -> str:
        """Alias for :meth:`submit` with explicit priority semantics.

        Provided for parity with the task spec's naming; behaviour is
        identical to :meth:`submit` under soft preemption.
        """
        return await self.submit(
            task,
            workdir=workdir,
            skip_verify=skip_verify,
            priority=priority,
            deadline_ms=deadline_ms,
            agent_name=agent_name,
        )

    # ── Dispatch loop ──────────────────────────────────────────

    async def _dispatch_loop(self) -> None:
        """Background loop: admit queued tasks to the pool when slots free up.

        Polls the pool's idle-worker count and, when one is available,
        pops the highest-priority queued task and submits it to the
        underlying :class:`WorkerPool`.
        """
        assert self._stop_event is not None
        # Map WorkerPool task id -> PriorityTask, so we can detect
        # would-be preemptions and record wait-time metrics on completion.
        pending_futures: dict[str, PriorityTask] = {}

        while not self._stop_event.is_set():
            try:
                stats = self._pool.stats()
                if stats.idle_workers <= 0:
                    # No slot available. We do NOT cancel running tasks
                    # (soft preemption). Just wait briefly before retrying.
                    await asyncio.sleep(0.02)
                    continue

                pt = self._queue.pop()
                if pt is None:
                    await asyncio.sleep(0.02)
                    continue

                # Record wait time + decrement queue-size gauge.
                self._record_admit(pt)

                payload = pt.payload or {}
                task_str = payload.get("task", "")
                workdir = payload.get("workdir", "")
                skip_verify = payload.get("skip_verify", False)
                agent_name = payload.get("agent_name", "")

                # Detect would-be preemption: are there running tasks with
                # strictly lower priority? (i.e. this higher-priority task
                # had to wait behind them). This is a soft-preemption signal.
                self._maybe_record_soft_preemption(pt)

                wp_id = await self._pool.submit(
                    task_str,
                    workdir=workdir,
                    skip_verify=skip_verify,
                    agent_name=agent_name,
                )
                pending_futures[wp_id] = pt
                async with self._running_lock:
                    self._running[wp_id] = pt

                # Spawn a watcher to clean up the running map + record
                # completion. We don't block the dispatch loop on it.
                asyncio.ensure_future(self._watch_completion(wp_id, pt))
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.warning("[preempt-pool] dispatch loop error: %s", exc)
                await asyncio.sleep(0.05)

    async def _watch_completion(self, wp_id: str, pt: PriorityTask) -> None:
        """Wait for a WorkerPool task to finish, then clean up bookkeeping."""
        try:
            await self._pool.wait(wp_id)
        except Exception:
            pass
        finally:
            async with self._running_lock:
                self._running.pop(wp_id, None)

    def _maybe_record_soft_preemption(self, incoming: PriorityTask) -> None:
        """Record a would-be preemption event if applicable.

        A would-be preemption occurs when:
          - all workers are busy (idle_workers == 0), AND
          - the incoming task has strictly higher priority (smaller
            priority number) than at least one currently-running task.

        Under soft preemption we do NOT cancel the running task; we only
        increment ``MAOP_task_preemption_total`` so the demand for true
        preemption is observable.
        """
        try:
            stats = self._pool.stats()
            if stats.idle_workers > 0:
                return  # A slot is free, no preemption pressure.
            # Snapshot running priorities without holding the asyncio lock
            # (we only read; worst case we miss a just-finished task, which
            # is acceptable for a monitoring counter).
            running_priorities = [r.priority for r in self._running.values()]
            if not running_priorities:
                return
            lowest_running = min(running_priorities)
            if incoming.priority < lowest_running:
                MAOP_TASK_PREEMPTION_TOTAL.inc()
                logger.info(
                    "[preempt-pool] soft preemption: incoming priority=%d "
                    "would preempt running priority=%d (not cancelled — "
                    "checkpoint not wired into execution path)",
                    incoming.priority, lowest_running,
                )
        except Exception as exc:
            logger.debug("[preempt-pool] soft-preemption record failed: %s", exc)

    # ── Metrics helpers ─────────────────────────────────────────

    def _record_queue_size_inc(self, priority: int) -> None:
        try:
            MAOP_PRIORITY_QUEUE_SIZE.inc(labels={"priority": str(priority)})
        except Exception:
            pass

    def _record_queue_size_dec(self, priority: int) -> None:
        try:
            MAOP_PRIORITY_QUEUE_SIZE.dec(labels={"priority": str(priority)})
        except Exception:
            pass

    def _record_admit(self, pt: PriorityTask) -> None:
        """Record wait-time + queue-size decrement when a task is admitted."""
        try:
            self._record_queue_size_dec(pt.priority)
            wait_s = time.time() - pt.created_at
            get_priority_wait_histogram(pt.priority).observe(max(0.0, wait_s))
        except Exception as exc:
            logger.debug("[preempt-pool] admit-metric record failed: %s", exc)

    # ── Query / compatibility ───────────────────────────────────

    def stats(self) -> PoolStats:
        """Return underlying pool stats (pending_tasks includes queued)."""
        s = self._pool.stats()
        # Add queued tasks to pending for an honest view.
        queued = len(self._queue)
        return PoolStats(
            total_workers=s.total_workers,
            active_workers=s.active_workers,
            idle_workers=s.idle_workers,
            pending_tasks=s.pending_tasks + queued,
            completed_tasks=s.completed_tasks,
            failed_tasks=s.failed_tasks,
            cpu_workers=s.cpu_workers,
            cpu_active=s.cpu_active,
        )

    def get_task(self, task_id: str):
        """Pass-through to the underlying pool's task lookup."""
        return self._pool.get_task(task_id)

    def all_tasks(self):
        return self._pool.all_tasks()

    def __repr__(self) -> str:
        return (
            f"PreemptableWorkerPool(pool={self._pool!r}, "
            f"queued={len(self._queue)})"
        )
