"""MAOP Preemptable Worker Pool — Priority-aware preemption scheduler.

Wraps :class:`maop.core.worker_pool.WorkerPool` with a
:class:`maop.core.priority_queue.PriorityTaskQueue` so that pending tasks
are admitted in priority + deadline order instead of submission order.

Phase γ-2: SOFT preemption (default)
------------------------------------
1. Orders the pending queue by ``(priority, deadline_urgency_score,
   enqueue_order)`` via :class:`PriorityTaskQueue`.
2. Admits the next queued task whenever a worker slot frees up.
3. When a higher-priority task arrives while all workers are busy *and*
   at least one running task has a strictly lower priority, records a
   "would-be preemption" event in ``MAOP_task_preemption_total`` so the
   demand for true preemption is observable — but does **not** cancel the
   running task. The high-priority task is placed at the front of the
   queue and runs as soon as a slot is free.

Phase γ-3: TRUE preemption (opt-in via ``true_preemption=True``)
--------------------------------------------------------------
When no worker slot is free and the queue head has strictly higher
priority than a running task, the lowest-priority running task is
**cancelled** (:meth:`WorkerPool.cancel(preempt=True)`) and **re-enqueued
under its original token** — its ``wait()``er is resolved by the
re-admitted copy, so from the submitter's perspective the task simply
takes longer, never vanishes.

This is safe because the execution path now writes durable state:
:meth:`WorkerPool._run_task` records every executed task in a
:class:`~maop.core.reliability.pipeline_checkpoint.PipelineCheckpoint` run
(``start_step``/``complete_step``/``fail_step``). A preempted task
leaves its checkpoint step in ``running`` status, which
``pending_steps()`` reports for retry, and the step's ``attempts``
counter makes repeated preemption-retry cycles visible. What is *not*
preserved is mid-task progress *inside* ``MaopLoop.run`` — the
re-admitted copy re-executes the task in full; phase-level resume is a
separate, future enhancement.

Thrash protection: a task that has already been preempted
``max_preemptions`` times (default 2) is no longer eligible as a
victim, so a burst of high-priority work cannot starve one unlucky
task indefinitely.

Both soft and true events increment the same
``MAOP_task_preemption_total`` counter (per the monitoring contract);
the log lines distinguish them (``soft preemption:`` vs
``TRUE preemption:``).

Backward compatibility
----------------------
:class:`PreemptableWorkerPool` is API-compatible with :class:`WorkerPool`
for the common methods (``start``/``stop``/``submit``/``wait``/``stats``).
``submit`` gains optional ``priority`` / ``deadline_ms`` keyword args with
defaults (priority=3, no deadline) that preserve the original behaviour
when omitted. ``true_preemption`` defaults to ``False`` — existing
deployments keep soft-preemption semantics until they opt in.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from typing import TYPE_CHECKING, Any

from maop.core.monitoring.monitoring import (
    MAOP_PRIORITY_QUEUE_SIZE,
    MAOP_TASK_PREEMPTION_TOTAL,
    get_priority_wait_histogram,
)
from maop.core.reliability.priority_queue import PriorityTask, PriorityTaskQueue
from maop.core.reliability.worker_pool import (
    PoolStats,
    TaskPreemptedError,
    WorkerPool,
)

if TYPE_CHECKING:
    from maop.core.reliability.pipeline_checkpoint import (
        PipelineCheckpoint,
    )

logger = logging.getLogger(__name__)

__all__ = ["PreemptableWorkerPool"]


class PreemptableWorkerPool:
    """Priority-aware worker pool with preemption.

    Parameters
    ----------
    max_workers : int
        Maximum concurrent IO-bound tasks (forwarded to ``WorkerPool``).
    max_cpu_workers : int
        Maximum CPU-bound worker processes (forwarded to ``WorkerPool``).
    root_dir : str | None
        MAOP project root (forwarded to ``WorkerPool``).
    true_preemption : bool
        Phase γ-3 switch. When ``True``, a queued task with strictly
        higher priority than a running task *cancels* that running
        task and re-enqueues it (default ``False`` = soft preemption,
        i.e. the γ-2 behaviour).
    max_preemptions : int
        Thrash protection: a task already preempted this many times
        (default 2) is no longer eligible as a preemption victim.
    checkpoint : PipelineCheckpoint | None
        Durable execution records (forwarded to ``WorkerPool``).
        When ``true_preemption`` is on and none is supplied, one is
        created under ``root_dir``. Pass an instance to control the
        location (or a test double).
    """

    def __init__(
        self,
        max_workers: int = 4,
        max_cpu_workers: int = 0,
        root_dir: str | None = None,
        true_preemption: bool = False,
        max_preemptions: int = 2,
        checkpoint: PipelineCheckpoint | None = None,
    ) -> None:
        if checkpoint is None and true_preemption:
            # The execution-path wiring that makes true
            # preemption safe (durable per-task records).
            # Best-effort: if the store cannot be created,
            # preemption still works — the pool simply runs
            # without durable records. Fail LOUDLY though:
            # the checkpoint import was silently broken for
            # a release (wrong module path) precisely
            # because the old contextlib.suppress(Exception)
            # hid it — an operator had no way to notice
            # that true preemption had degraded (ADR-018).
            try:
                from maop.core.reliability.pipeline_checkpoint import (
                    PipelineCheckpoint,
                )
                checkpoint = PipelineCheckpoint(root_dir=root_dir or ".")
            except Exception as exc:
                logger.error(
                    "[preemptable-pool] true_preemption enabled but "
                    "the durable checkpoint store is unavailable "
                    "(%s: %s); running WITHOUT durable "
                    "preemption records",
                    type(exc).__name__, exc,
                )
        self._pool = WorkerPool(
            max_workers=max_workers,
            max_cpu_workers=max_cpu_workers,
            root_dir=root_dir,
            checkpoint=checkpoint,
        )
        self._queue: PriorityTaskQueue = PriorityTaskQueue()
        # Track currently-running task priorities keyed by WorkerPool task id,
        # so we can detect "would-be preemption" scenarios.
        self._running: dict[str, PriorityTask] = {}
        self._running_lock = asyncio.Lock()
        # Background dispatch loop is started lazily and stopped with the pool.
        self._dispatch_task: asyncio.Task | None = None
        self._stop_event: asyncio.Event | None = None
        # Track watcher tasks so they can be cancelled on stop().
        self._watchers: set[asyncio.Task] = set()
        # Map enqueue token -> WorkerPool task id, for wait() support.
        self._token_to_wp_id: dict[str, str] = {}
        # Map token -> asyncio.Event, signalled on completion.
        self._token_events: dict[str, asyncio.Event] = {}
        # Map token -> result/exception, for wait() to return/raise.
        self._token_results: dict[str, Any] = {}
        self._token_errors: dict[str, BaseException] = {}
        # Phase γ-3: true-preemption switch + thrash protection.
        self._true_preemption = true_preemption
        self._max_preemptions = max(0, max_preemptions)
        # WorkerPool task ids cancelled for preemption whose
        # re-enqueue is pending — the completion watcher uses this
        # to take the preemption branch instead of recording an
        # error on the (original) token.
        self._preempted_wp_ids: set[str] = set()

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
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self._dispatch_task
            self._dispatch_task = None
        # Cancel any outstanding watchers so they don't linger after stop().
        for watcher in list(self._watchers):
            watcher.cancel()
        if self._watchers:
            await asyncio.gather(*self._watchers, return_exceptions=True)
            self._watchers.clear()
        await self._pool.stop()
        # Flush queue gauges
        with contextlib.suppress(Exception):
            MAOP_PRIORITY_QUEUE_SIZE.set(0.0, labels={"priority": "all"})

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
        token = f"pt-{pt.enqueue_order}"
        # Pre-register the completion event so wait() can be called
        # before the dispatch loop has admitted the task.
        self._token_events[token] = asyncio.Event()
        return token

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
                    # No slot available. Under true preemption
                    # (γ-3) a queued task with strictly higher
                    # priority than a running one may cancel that
                    # running task and take its slot; otherwise we
                    # do NOT cancel running tasks (soft preemption).
                    if self._true_preemption:
                        self._maybe_preempt()
                    # Wait briefly before retrying — also lets a
                    # just-issued cancellation land, avoiding
                    # double-preemption of the same victim.
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
                token = f"pt-{pt.enqueue_order}"
                self._token_to_wp_id[token] = wp_id
                async with self._running_lock:
                    self._running[wp_id] = pt

                # Spawn a watcher to clean up the running map + record
                # completion. We don't block the dispatch loop on it.
                watcher = asyncio.ensure_future(self._watch_completion(wp_id, pt))
                self._watchers.add(watcher)
                watcher.add_done_callback(self._watchers.discard)
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.warning("[preempt-pool] dispatch loop error: %s", exc)
                await asyncio.sleep(0.05)

    async def _watch_completion(self, wp_id: str, pt: PriorityTask) -> None:
        """Wait for a WorkerPool task to finish, then clean up bookkeeping.

        Records the result/exception so :meth:`wait` can return/raise,
        and signals the token's completion event. Exceptions are logged
        (not silently swallowed) so failures are observable.

        Phase γ-3: a :class:`TaskPreemptedError` means the task was
        cancelled by true preemption and has already been re-enqueued
        under its original token by :meth:`_maybe_preempt`. The token
        must stay pending — the re-admitted copy spawns a new watcher
        that will resolve it — so no result, error, or event is
        recorded here.
        """
        token = f"pt-{pt.enqueue_order}"
        try:
            result = await self._pool.wait(wp_id)
            self._token_results[token] = result
        except TaskPreemptedError:
            # The victim was re-enqueued under this token by
            # _maybe_preempt; discard the preemption mark.
            # NOTE: no event signalling on this path — the
            # code after the try/finally below is only reached
            # on completion/failure, and a `return` here leaves
            # the token pending for the re-admitted copy.
            self._preempted_wp_ids.discard(wp_id)
            logger.info(
                "[preempt-pool] task %s (wp_id=%s) preempted; "
                "re-enqueued under token %s",
                token, wp_id, token,
            )
            return
        except BaseException as exc:
            self._token_errors[token] = exc
            # CancelledError is expected during stop(); don't warn for it.
            if not isinstance(exc, (KeyError, asyncio.CancelledError)):
                logger.warning(
                    "[preempt-pool] task %s (wp_id=%s) failed: %s",
                    token, wp_id, exc,
                    exc_info=not isinstance(exc, asyncio.TimeoutError),
                )
        finally:
            async with self._running_lock:
                self._running.pop(wp_id, None)
        # Signal waiters regardless of success/failure (but NOT
        # preemption — see the note above).
        event = self._token_events.get(token)
        if event is not None:
            event.set()

    # ── True preemption (Phase γ-3) ───────────────────────

    def _maybe_preempt(self) -> bool:
        """Attempt true preemption of a running task.

        Conditions (all required):
          - the queue is non-empty (there is a waiting head), AND
          - some running task has a strictly lower priority (larger
            number) than the queue head, AND
          - that task has been preempted fewer than
            ``max_preemptions`` times (thrash protection).

        On success the victim is cancelled via
        :meth:`WorkerPool.cancel(preempt=True)`, marked in
        ``_preempted_wp_ids`` (so its completion watcher takes the
        re-enqueue branch), and **re-pushed onto the priority queue
        under its original enqueue token** — its submitter's
        ``wait()`` is resolved by the re-admitted copy.

        Returns ``True`` if a preemption was performed.
        """
        # Guard: true preemption is opt-in. The dispatch loop
        # checks the same flag before calling, but the method
        # stays inert on its own so it can never cancel a
        # running task on a soft-preemption pool.
        if not self._true_preemption:
            return False
        head = self._queue.peek()
        if head is None:
            return False
        # Snapshot without the asyncio lock (same discipline as
        # _maybe_record_soft_preemption: a stale entry at worst
        # causes a no-op cancel(), which returns False).
        candidates = [
            (pt, wp_id)
            for wp_id, pt in list(self._running.items())
            if pt.priority > head.priority
            and pt.preempted_count < self._max_preemptions
        ]
        if not candidates:
            return False
        # Victim: lowest priority (largest number) first; tie-break
        # by fewest prior preemptions, then oldest enqueue order
        # (FIFO fairness).
        victim_pt, victim_wp_id = min(
            candidates,
            key=lambda item: (
                -item[0].priority,
                item[0].preempted_count,
                item[0].enqueue_order,
            ),
        )
        # Mark BEFORE cancelling so the completion watcher takes the
        # preemption branch instead of recording an error.
        self._preempted_wp_ids.add(victim_wp_id)
        victim_pt.preempted_count += 1
        if not self._pool.cancel(victim_wp_id, preempt=True):
            # Raced with the victim's normal completion — undo the
            # marks; the task finished on its own.
            self._preempted_wp_ids.discard(victim_wp_id)
            victim_pt.preempted_count -= 1
            return False
        # Same counter as soft preemption per the monitoring
        # contract ("this same counter will record actual
        # cancellations"); log lines distinguish the two.
        MAOP_TASK_PREEMPTION_TOTAL.inc()
        logger.info(
            "[preempt-pool] TRUE preemption: queued priority=%d "
            "(token pt-%d) cancelled running priority=%d "
            "(wp_id=%s, preemptions=%d/%d) — victim re-enqueued",
            head.priority, head.enqueue_order, victim_pt.priority,
            victim_wp_id, victim_pt.preempted_count,
            self._max_preemptions,
        )
        # Re-enqueue the victim under its original token:
        # enqueue_order (hence the token) is preserved, and the
        # queue-size gauge balance is restored (it was
        # decremented at admission).
        self._record_queue_size_inc(victim_pt.priority)
        self._queue.push(victim_pt)
        return True

    async def wait(self, token: str, timeout: float = 0) -> Any:
        """Wait for a submitted task to complete and return its result.

        Parameters
        ----------
        token : str
            The task id returned by :meth:`submit`.
        timeout : float
            Maximum seconds to wait. ``0`` means wait indefinitely.

        Raises
        ------
        KeyError
            If ``token`` is unknown (never submitted or already cleaned up).
        BaseException
            Re-raises any exception the underlying task raised.
        """
        if token not in self._token_events:
            # Maybe already completed and cleaned up; check results/errors.
            if token in self._token_errors:
                raise self._token_errors[token]
            if token in self._token_results:
                return self._token_results[token]
            raise KeyError(f"Task token {token!r} not found")
        event = self._token_events[token]
        if timeout > 0:
            await asyncio.wait_for(event.wait(), timeout=timeout)
        else:
            await event.wait()
        if token in self._token_errors:
            raise self._token_errors[token]
        return self._token_results.get(token)

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
        with contextlib.suppress(Exception):
            MAOP_PRIORITY_QUEUE_SIZE.inc(labels={"priority": str(priority)})

    def _record_queue_size_dec(self, priority: int) -> None:
        with contextlib.suppress(Exception):
            MAOP_PRIORITY_QUEUE_SIZE.dec(labels={"priority": str(priority)})

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
