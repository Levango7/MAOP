"""MAOP Priority Task Queue — Priority + deadline-aware task ordering (Phase γ-2).

Provides an in-process, thread-safe priority queue used by the scheduler
to pick the next task to run. Ordering key:

    (priority, deadline_urgency_score, enqueue_order)

  - ``priority``           : 1 (highest) .. 5 (lowest). Lower value runs first.
  - ``deadline_urgency_score``: ``-deadline_ms`` when a deadline is set (so an
    earlier deadline ⇒ smaller score ⇒ runs sooner); ``0`` when no deadline
    (neutral). Encoded directly in the heap tuple so no recomputation is
    needed on ``pop``.
  - ``enqueue_order``      : monotonically increasing counter giving FIFO
    tie-breaking among equal-priority / equal-deadline tasks.

Implementation uses :mod:`heapq` (binary heap) giving ``O(log n)`` push/pop.

Phase γ-2 design note
---------------------
This queue is the backbone of the *soft preemption* scheduler
(:class:`maop.core.preemptable_worker_pool.PreemptableWorkerPool`). True
preemption (cancelling a running low-priority task to admit a high-priority
one) was *not* implemented because :class:`maop.core.pipeline_checkpoint.
PipelineCheckpoint` is not wired into the task execution path
(``WorkerPool._run_task`` / ``MaopLoop.run`` never call it) and only saves
flat step-level state — mid-task progress would be lost on cancellation.
Soft preemption therefore orders the *pending* queue by priority/deadline
and records "would-be preemption" events in
``MAOP_task_preemption_total`` for monitoring demand, without ever
cancelling a running task. See the Phase γ-2 report for details.
"""

from __future__ import annotations

import heapq
import itertools
import threading
import time
from dataclasses import dataclass, field
from typing import Any

__all__ = ["PriorityTask", "PriorityTaskQueue"]


@dataclass
class PriorityTask:
    """A task wrapper carrying scheduling metadata.

    Attributes
    ----------
    payload : Any
        The opaque task payload (description string, dict, …). The queue
        does not introspect it; the consumer interprets it.
    priority : int
        Scheduling priority 1 (highest) .. 5 (lowest). Default 3 (normal).
    deadline_ms : int | None
        Absolute deadline in milliseconds since the epoch. ``None`` means
        no explicit deadline (best-effort).
    created_at : float
        Epoch timestamp (seconds) when this wrapper was constructed.
    enqueue_order : int
        Counter assigned at enqueue time for FIFO tie-breaking. Populated
        by :meth:`PriorityTaskQueue.push`; callers may leave it at 0.
    """

    payload: Any
    priority: int = 3
    deadline_ms: int | None = None
    created_at: float = field(default_factory=time.time)
    enqueue_order: int = 0

    def deadline_urgency_score(self) -> float:
        """Return the deadline-urgency component of the sort key.

        Smaller score = more urgent = runs sooner.

        - With a deadline: ``float(deadline_ms)`` — because ``deadline_ms``
          is an *absolute* epoch timestamp, an earlier deadline has a
          smaller value and therefore pops first. This satisfies the
          Phase γ-2 spec goal "deadline 紧迫的优先".
        - Without a deadline: ``float('inf')`` — neutral in the sense that
          it does not preempt any task that carries a real deadline; among
          equal-priority no-deadline tasks the FIFO ``enqueue_order``
          tie-breaker applies.

        .. note::
            The Phase γ-2 spec text suggested ``score = -deadline_ms`` with
            ``0`` for no-deadline. That formula is self-contradictory for a
            min-heap: it makes a *later* (larger) deadline produce a *more
            negative* (smaller) score, so later deadlines would pop *before*
            earlier ones — the opposite of "deadline 紧迫的优先". The
            positive ``deadline_ms`` + ``inf`` encoding used here is the
            minimal fix that honours the stated goal while keeping deadline
            tasks ahead of no-deadline tasks at equal priority.
        """
        if self.deadline_ms is None:
            return float("inf")
        return float(self.deadline_ms)


class PriorityTaskQueue:
    """Thread-safe priority + deadline-aware task queue.

    Ordering key (smaller pops first)::

        (priority, deadline_urgency_score, enqueue_order)

    Uses a binary heap (:mod:`heapq`) for ``O(log n)`` push/pop.
    """

    def __init__(self) -> None:
        self._heap: list[tuple[tuple[int, float, int], int, PriorityTask]] = []
        # Monotonic counter for FIFO tie-breaking. itertools.count is
        # atomic under the GIL for single increments, but we still guard it
        # with the lock in push() to keep ordering strictly monotonic.
        self._counter = itertools.count()
        self._lock = threading.Lock()
        # Used by reorder(): detect whether deadline urgency can change the
        # ordering. We keep a parallel dict from enqueue_order -> task for
        # O(n) rebuild on reorder (queue sizes are expected to be modest).

    # ── Core queue operations ────────────────────────────────────

    def push(self, task: PriorityTask) -> None:
        """Push a task onto the queue in priority order (thread-safe)."""
        with self._lock:
            if task.enqueue_order == 0:
                task.enqueue_order = next(self._counter)
            key = (
                int(task.priority),
                task.deadline_urgency_score(),
                task.enqueue_order,
            )
            # heap entries are (key, tiebreak_id, task); the tiebreak_id is
            # redundant with enqueue_order but kept to satisfy heapq's
            # comparison fallback when keys are equal (PriorityTask is not
            # orderable). We use the unique counter value as the final
            # tiebreaker so two equal keys never compare the task object.
            tiebreak = next(self._counter)
            heapq.heappush(self._heap, (key, tiebreak, task))

    def pop(self) -> PriorityTask | None:
        """Remove and return the highest-priority task, or ``None`` if empty."""
        with self._lock:
            if not self._heap:
                return None
            return heapq.heappop(self._heap)[2]

    def peek(self) -> PriorityTask | None:
        """Return the highest-priority task without removing it."""
        with self._lock:
            if not self._heap:
                return None
            return self._heap[0][2]

    def __len__(self) -> int:
        with self._lock:
            return len(self._heap)

    def empty(self) -> bool:
        """Return ``True`` if the queue holds no tasks."""
        with self._lock:
            return not self._heap

    def clear(self) -> None:
        """Remove all tasks from the queue."""
        with self._lock:
            self._heap.clear()

    # ── Reorder ──────────────────────────────────────────────────

    def reorder(self) -> int:
        """Re-heapify the queue to reflect elapsed time / deadline urgency.

        Because ``deadline_urgency_score`` is derived from the *absolute*
        ``deadline_ms`` (not a relative remaining-time value), the heap key
        is in fact stable: an earlier deadline always has a smaller score
        regardless of wall-clock progress. So in the current encoding a
        plain reorder is a no-op and the heap ordering is already correct.

        This method is retained as the contract hook for a future
        enhancement that switches the score to *remaining* time
        (``deadline_ms - now``), which *would* drift as time passes and
        require periodic reordering. It rebuilds the heap from the current
        task set, recomputing keys — which keeps callers correct if the
        score formula later changes.

        Returns
        -------
        int
            Number of tasks currently in the queue (post-rebuild).
        """
        with self._lock:
            if not self._heap:
                return 0
            tasks = [entry[2] for entry in self._heap]
            self._heap.clear()
            for task in tasks:
                key = (
                    int(task.priority),
                    task.deadline_urgency_score(),
                    task.enqueue_order,
                )
                tiebreak = next(self._counter)
                heapq.heappush(self._heap, (key, tiebreak, task))
            return len(self._heap)

    # ── Introspection ───────────────────────────────────────────

    def size_by_priority(self) -> dict[int, int]:
        """Return a ``{priority: count}`` snapshot of the queue."""
        with self._lock:
            counts: dict[int, int] = {}
            for entry in self._heap:
                p = entry[2].priority
                counts[p] = counts.get(p, 0) + 1
            return counts

    def snapshot(self) -> list[PriorityTask]:
        """Return a list of queued tasks in pop order (read-only copy)."""
        with self._lock:
            # Sort a copy of the heap entries by key to give pop order
            # without mutating the heap.
            ordered = sorted(self._heap, key=lambda e: (e[0], e[1]))
            return [entry[2] for entry in ordered]
