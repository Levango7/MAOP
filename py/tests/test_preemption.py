"""Tests for MAOP Phase γ-2 — Priority queue + soft preemption scheduling.

Covers:
  - :class:`maop.core.priority_queue.PriorityTaskQueue` — ordering, deadline
    urgency, FIFO tie-break, thread safety, reorder, introspection.
  - Soft preemption behaviour (``MAOP_task_preemption_total`` recording) via
    a stubbed :class:`PreemptableWorkerPool` so the test does not spin up a
    real ``MaopLoop``.
  - Dispatcher priority integration: :meth:`Dispatcher.dispatch_priority`
    enqueues, :meth:`Dispatcher.drain_pending` executes in priority order,
    and the backward-compatible fallback (no queue) runs ``dispatch`` directly.
"""

from __future__ import annotations

import asyncio
import threading
import time

import pytest

from maop.core.monitoring import (
    MAOP_PRIORITY_QUEUE_SIZE,
    MAOP_TASK_PREEMPTION_TOTAL,
    get_priority_wait_histogram,
)
from maop.core.priority_queue import PriorityTask, PriorityTaskQueue


# ── PriorityTaskQueue ──────────────────────────────────────────


class TestPriorityTaskQueue:
    def test_push_pop_basic(self):
        q = PriorityTaskQueue()
        assert q.empty()
        assert len(q) == 0
        q.push(PriorityTask(payload="a", priority=3))
        assert not q.empty()
        assert len(q) == 1
        popped = q.pop()
        assert popped is not None
        assert popped.payload == "a"
        assert q.empty()

    def test_pop_empty_returns_none(self):
        q = PriorityTaskQueue()
        assert q.pop() is None

    def test_peek_does_not_remove(self):
        q = PriorityTaskQueue()
        q.push(PriorityTask(payload="x", priority=2))
        peeked = q.peek()
        assert peeked is not None
        assert peeked.payload == "x"
        assert len(q) == 1
        # pop still returns the same item
        popped = q.pop()
        assert popped.payload == "x"

    def test_priority_ordering_lower_runs_first(self):
        """priority 1 (highest) is popped before priority 5 (lowest)."""
        q = PriorityTaskQueue()
        q.push(PriorityTask(payload="low", priority=5))
        q.push(PriorityTask(payload="high", priority=1))
        q.push(PriorityTask(payload="mid", priority=3))
        assert q.pop().payload == "high"
        assert q.pop().payload == "mid"
        assert q.pop().payload == "low"

    def test_fifo_tiebreak_same_priority(self):
        """Among equal-priority tasks, earlier push pops first."""
        q = PriorityTaskQueue()
        q.push(PriorityTask(payload="first", priority=3))
        q.push(PriorityTask(payload="second", priority=3))
        q.push(PriorityTask(payload="third", priority=3))
        assert q.pop().payload == "first"
        assert q.pop().payload == "second"
        assert q.pop().payload == "third"

    def test_deadline_urgency_score(self):
        """Earlier deadline => smaller score => more urgent.

        score = deadline_ms (positive, absolute timestamp) so an earlier
        deadline yields a smaller score and pops first. No-deadline tasks
        get ``inf`` so they sort after every deadline-bearing task.
        """
        no_deadline = PriorityTask(payload="nd", priority=3)
        assert no_deadline.deadline_urgency_score() == float("inf")
        early = PriorityTask(payload="early", priority=3, deadline_ms=1000)
        late = PriorityTask(payload="late", priority=3, deadline_ms=5000)
        assert early.deadline_urgency_score() < late.deadline_urgency_score()
        assert early.deadline_urgency_score() == 1000.0
        assert late.deadline_urgency_score() == 5000.0

    def test_deadline_affects_ordering_same_priority(self):
        """At equal priority, the earlier-deadline task pops first."""
        q = PriorityTaskQueue()
        q.push(PriorityTask(payload="late-dl", priority=3, deadline_ms=10_000))
        q.push(PriorityTask(payload="early-dl", priority=3, deadline_ms=1_000))
        q.push(PriorityTask(payload="no-dl", priority=3))
        # early (1000) < late (10000) < no-deadline (inf)
        assert q.pop().payload == "early-dl"
        assert q.pop().payload == "late-dl"
        assert q.pop().payload == "no-dl"

    def test_priority_beats_deadline(self):
        """Priority dominates: a high-prio task with a far deadline still wins."""
        q = PriorityTaskQueue()
        q.push(PriorityTask(payload="p5-urgent-dl", priority=5, deadline_ms=1))
        q.push(PriorityTask(payload="p1-far-dl", priority=1, deadline_ms=1_000_000))
        assert q.pop().payload == "p1-far-dl"
        assert q.pop().payload == "p5-urgent-dl"

    def test_clear(self):
        q = PriorityTaskQueue()
        for i in range(5):
            q.push(PriorityTask(payload=i, priority=3))
        assert len(q) == 5
        q.clear()
        assert q.empty()
        assert len(q) == 0

    def test_snapshot_pop_order(self):
        q = PriorityTaskQueue()
        q.push(PriorityTask(payload="c", priority=3))
        q.push(PriorityTask(payload="a", priority=1))
        q.push(PriorityTask(payload="b", priority=2))
        snap = q.snapshot()
        assert [t.payload for t in snap] == ["a", "b", "c"]
        # snapshot is read-only
        assert len(q) == 3

    def test_size_by_priority(self):
        q = PriorityTaskQueue()
        q.push(PriorityTask(payload="x", priority=1))
        q.push(PriorityTask(payload="y", priority=1))
        q.push(PriorityTask(payload="z", priority=3))
        counts = q.size_by_priority()
        assert counts == {1: 2, 3: 1}

    def test_reorder_preserves_order(self):
        """reorder() should keep the heap valid and ordering intact."""
        q = PriorityTaskQueue()
        q.push(PriorityTask(payload="b", priority=2))
        q.push(PriorityTask(payload="a", priority=1))
        q.push(PriorityTask(payload="c", priority=3))
        n = q.reorder()
        assert n == 3
        assert q.pop().payload == "a"
        assert q.pop().payload == "b"
        assert q.pop().payload == "c"

    def test_reorder_empty_returns_zero(self):
        q = PriorityTaskQueue()
        assert q.reorder() == 0

    def test_explicit_enqueue_order_respected(self):
        """If a caller sets enqueue_order explicitly, it is honoured."""
        q = PriorityTaskQueue()
        # Force a later-created task to have a smaller enqueue_order so it
        # pops first among equal priority/deadline.
        q.push(PriorityTask(payload="natural", priority=3, enqueue_order=100))
        q.push(PriorityTask(payload="forced-first", priority=3, enqueue_order=1))
        assert q.pop().payload == "forced-first"


class TestPriorityTaskQueueConcurrency:
    def test_concurrent_push_pop(self):
        """Concurrent producers + consumers must not corrupt the heap.

        Verifies thread safety: every pushed item is popped exactly once,
        and pop order respects priority across threads.
        """
        q = PriorityTaskQueue()
        n_per_producer = 200
        producers = 4
        pushed: list[PriorityTask] = []
        push_lock = threading.Lock()
        popped: list[PriorityTask] = []
        pop_lock = threading.Lock()

        def producer(pid: int) -> None:
            for i in range(n_per_producer):
                # Mix priorities 1..3 across producers.
                prio = (pid % 3) + 1
                pt = PriorityTask(payload=(pid, i), priority=prio)
                with push_lock:
                    pushed.append(pt)
                q.push(pt)

        def consumer() -> None:
            while True:
                pt = q.pop()
                if pt is None:
                    return
                with pop_lock:
                    popped.append(pt)

        threads = [threading.Thread(target=producer, args=(p,)) for p in range(producers)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # All items should now be queued.
        total = producers * n_per_producer
        assert len(q) == total

        # Run consumers until the queue drains.
        cons = [threading.Thread(target=consumer) for _ in range(4)]
        for t in cons:
            t.start()
        for t in cons:
            t.join()

        assert len(popped) == total
        assert len(q) == 0

        # Verify popped order respects priority: all priority-1 items should
        # appear before any priority-3 item.
        first_p3_idx = next(
            (i for i, pt in enumerate(popped) if pt.priority == 3), None
        )
        if first_p3_idx is not None:
            for i in range(first_p3_idx):
                assert popped[i].priority <= 3
            # No priority-1 or priority-2 task after the first priority-3
            for pt in popped[first_p3_idx:]:
                assert pt.priority >= 3


# ── Metrics registration ───────────────────────────────────────


class TestPreemptionMetrics:
    def test_preemption_counter_exists_and_increments(self):
        before = MAOP_TASK_PREEMPTION_TOTAL.get()
        MAOP_TASK_PREEMPTION_TOTAL.inc()
        after = MAOP_TASK_PREEMPTION_TOTAL.get()
        assert after == before + 1.0

    def test_priority_queue_size_gauge_per_priority(self):
        MAOP_PRIORITY_QUEUE_SIZE.set(5.0, labels={"priority": "1"})
        MAOP_PRIORITY_QUEUE_SIZE.set(2.0, labels={"priority": "3"})
        assert MAOP_PRIORITY_QUEUE_SIZE.get(labels={"priority": "1"}) == 5.0
        assert MAOP_PRIORITY_QUEUE_SIZE.get(labels={"priority": "3"}) == 2.0

    def test_priority_wait_histogram_per_priority(self):
        h1 = get_priority_wait_histogram(1)
        before = h1._total
        h1.observe(0.05)
        assert h1._total == before + 1
        # Different priority gets its own histogram
        h3 = get_priority_wait_histogram(3)
        assert h3 is not h1


# ── Soft preemption (PreemptableWorkerPool) ────────────────────


class TestSoftPreemption:
    """Soft-preemption detection logic without spinning up MaopLoop.

    We instantiate :class:`PreemptableWorkerPool` but do NOT call
    ``start()`` (which would launch the dispatch loop + MaopLoop). Instead
    we drive ``_maybe_record_soft_preemption`` directly with a stubbed
    underlying pool so we can assert the counter increments correctly.
    """

    def _make_pool(self):
        from maop.core.preemptable_worker_pool import PreemptableWorkerPool
        pool = PreemptableWorkerPool(max_workers=1)
        return pool

    def _stub_idle(self, pool, idle: int):
        """Replace the underlying WorkerPool.stats() to report a fixed idle count."""
        from maop.core.worker_pool import PoolStats

        def fake_stats() -> PoolStats:
            return PoolStats(
                total_workers=1,
                active_workers=1 - idle,
                idle_workers=idle,
            )
        pool._pool.stats = fake_stats  # type: ignore[method-assign]

    def test_no_preemption_when_worker_idle(self):
        pool = self._make_pool()
        self._stub_idle(pool, idle=1)  # a free slot exists
        before = MAOP_TASK_PREEMPTION_TOTAL.get()
        incoming = PriorityTask(payload="high", priority=1)
        pool._maybe_record_soft_preemption(incoming)
        after = MAOP_TASK_PREEMPTION_TOTAL.get()
        assert after == before  # no event recorded

    def test_no_preemption_when_no_running_tasks(self):
        pool = self._make_pool()
        self._stub_idle(pool, idle=0)  # all busy
        pool._running.clear()  # nothing running
        before = MAOP_TASK_PREEMPTION_TOTAL.get()
        pool._maybe_record_soft_preemption(PriorityTask(payload="high", priority=1))
        after = MAOP_TASK_PREEMPTION_TOTAL.get()
        assert after == before  # nothing to preempt

    def test_no_preemption_when_incoming_not_higher_priority(self):
        pool = self._make_pool()
        self._stub_idle(pool, idle=0)
        pool._running["t1"] = PriorityTask(payload="low", priority=1)
        before = MAOP_TASK_PREEMPTION_TOTAL.get()
        # incoming priority 3 is LOWER (worse) than running priority 1
        pool._maybe_record_soft_preemption(PriorityTask(payload="incoming", priority=3))
        after = MAOP_TASK_PREEMPTION_TOTAL.get()
        assert after == before

    def test_soft_preemption_recorded_when_higher_priority_arrives(self):
        pool = self._make_pool()
        self._stub_idle(pool, idle=0)
        pool._running["t1"] = PriorityTask(payload="low", priority=5)
        pool._running["t2"] = PriorityTask(payload="mid", priority=3)
        before = MAOP_TASK_PREEMPTION_TOTAL.get()
        # incoming priority 1 is HIGHER (better) than the lowest running (5)
        pool._maybe_record_soft_preemption(PriorityTask(payload="high", priority=1))
        after = MAOP_TASK_PREEMPTION_TOTAL.get()
        assert after == before + 1.0

    def test_soft_preemption_only_records_once_per_call(self):
        pool = self._make_pool()
        self._stub_idle(pool, idle=0)
        pool._running["t1"] = PriorityTask(payload="low", priority=5)
        before = MAOP_TASK_PREEMPTION_TOTAL.get()
        pool._maybe_record_soft_preemption(PriorityTask(payload="high", priority=1))
        after = MAOP_TASK_PREEMPTION_TOTAL.get()
        assert after == before + 1.0
        # Calling again increments again (each arrival is one event)
        pool._maybe_record_soft_preemption(PriorityTask(payload="high2", priority=1))
        after2 = MAOP_TASK_PREEMPTION_TOTAL.get()
        assert after2 == after + 1.0

    def test_running_task_not_cancelled(self):
        """Soft preemption must not remove the running task from _running."""
        pool = self._make_pool()
        self._stub_idle(pool, idle=0)
        running = PriorityTask(payload="low", priority=5)
        pool._running["t1"] = running
        pool._maybe_record_soft_preemption(PriorityTask(payload="high", priority=1))
        # The running task is still there — soft preemption does not cancel.
        assert "t1" in pool._running
        assert pool._running["t1"] is running

    @pytest.mark.asyncio
    async def test_submit_enqueues_and_records_size(self):
        """submit() pushes to the queue and bumps the per-priority size gauge."""
        pool = self._make_pool()
        # Don't start the dispatch loop; just verify enqueue side-effects.
        before = MAOP_PRIORITY_QUEUE_SIZE.get(labels={"priority": "1"})
        await pool.submit("task-A", priority=1)
        after = MAOP_PRIORITY_QUEUE_SIZE.get(labels={"priority": "1"})
        assert after == before + 1.0
        assert len(pool.queue) == 1
        peeked = pool.queue.peek()
        assert peeked is not None
        assert peeked.priority == 1


# ── Dispatcher priority integration ────────────────────────────


class TestDispatcherPriorityIntegration:
    def _make_dispatcher(self, queue=None):
        from maop.delegate.dispatcher import Dispatcher
        return Dispatcher(priority_queue=queue)

    def test_set_and_get_priority_queue(self):
        d = self._make_dispatcher()
        assert d.priority_queue is None
        q = PriorityTaskQueue()
        d.set_priority_queue(q)
        assert d.priority_queue is q
        d.set_priority_queue(None)
        assert d.priority_queue is None

    @pytest.mark.asyncio
    async def test_dispatch_priority_fallback_without_queue(self):
        """Without a queue, dispatch_priority delegates to dispatch directly."""
        d = self._make_dispatcher(queue=None)
        calls = []

        async def fake_dispatch(*args, **kwargs):
            calls.append((args, kwargs))
            return "dispatch-result"

        d.dispatch = fake_dispatch  # type: ignore[method-assign]
        result = await d.dispatch_priority(
            "claude", "do thing", priority=2, deadline_ms=12345,
        )
        assert result == "dispatch-result"
        assert len(calls) == 1
        assert calls[0][1]["priority"] == 2
        assert calls[0][1]["deadline_ms"] == 12345

    async def _enqueue(self, d, *args, **kw):
        """Schedule dispatch_priority as a background task and yield once so
        its synchronous push-to-queue runs before we return."""
        task = asyncio.ensure_future(d.dispatch_priority(*args, **kw))
        # Let the coroutine run up to its `await fut` so the push happens.
        await asyncio.sleep(0)
        return task

    @pytest.mark.asyncio
    async def test_dispatch_priority_enqueues_with_queue(self):
        """With a queue, dispatch_priority enqueues and awaits drain."""
        q = PriorityTaskQueue()
        d = self._make_dispatcher(queue=q)

        dispatched_order: list[str] = []

        async def fake_dispatch(agent, task, *, priority=3, deadline_ms=None, **kw):
            dispatched_order.append(f"p{priority}:{task}")
            return f"ok-{task}"

        d.dispatch = fake_dispatch  # type: ignore[method-assign]

        # Enqueue three tasks out of priority order (as background tasks so
        # their synchronous push runs without blocking on the result future).
        f_low = await self._enqueue(d, "claude", "low-task", priority=5)
        f_high = await self._enqueue(d, "claude", "high-task", priority=1)
        f_mid = await self._enqueue(d, "claude", "mid-task", priority=3)

        # All three are now queued; nothing dispatched yet.
        assert len(q) == 3
        assert dispatched_order == []

        # Drain all three; they should execute in priority order.
        n = await d.drain_pending(limit=3)
        assert n == 3
        assert len(q) == 0

        # Verify execution order: high(1) -> mid(3) -> low(5)
        assert dispatched_order == ["p1:high-task", "p3:mid-task", "p5:low-task"]

        # Each future should resolve with its result.
        assert await f_high == "ok-high-task"
        assert await f_mid == "ok-mid-task"
        assert await f_low == "ok-low-task"

    @pytest.mark.asyncio
    async def test_drain_pending_respects_deadline_at_equal_priority(self):
        """At equal priority, the earlier-deadline task drains first."""
        q = PriorityTaskQueue()
        d = self._make_dispatcher(queue=q)

        order: list[str] = []

        async def fake_dispatch(agent, task, *, priority=3, deadline_ms=None, **kw):
            order.append(task)
            return task

        d.dispatch = fake_dispatch  # type: ignore[method-assign]

        await self._enqueue(d, "claude", "far-dl", priority=3, deadline_ms=10_000)
        await self._enqueue(d, "claude", "near-dl", priority=3, deadline_ms=1_000)

        await d.drain_pending(limit=2)
        # near-dl (deadline 1000) is more urgent than far-dl (deadline 10000)
        assert order == ["near-dl", "far-dl"]

    @pytest.mark.asyncio
    async def test_drain_pending_partial_when_queue_small(self):
        """drain_pending(limit) returns actual count when queue is smaller."""
        q = PriorityTaskQueue()
        d = self._make_dispatcher(queue=q)

        async def fake_dispatch(agent, task, *, priority=3, deadline_ms=None, **kw):
            return task

        d.dispatch = fake_dispatch  # type: ignore[method-assign]
        await self._enqueue(d, "claude", "only-one", priority=3)
        n = await d.drain_pending(limit=5)
        assert n == 1

    @pytest.mark.asyncio
    async def test_drain_pending_no_queue_returns_zero(self):
        d = self._make_dispatcher(queue=None)
        n = await d.drain_pending(limit=5)
        assert n == 0

    @pytest.mark.asyncio
    async def test_drain_pending_propagates_exception_to_future(self):
        """If dispatch raises, the enqueued future receives the exception."""
        q = PriorityTaskQueue()
        d = self._make_dispatcher(queue=q)

        async def fake_dispatch(agent, task, *, priority=3, deadline_ms=None, **kw):
            raise RuntimeError("boom")

        d.dispatch = fake_dispatch  # type: ignore[method-assign]

        fut = await self._enqueue(d, "claude", "fail-task", priority=1)
        await d.drain_pending(limit=1)
        with pytest.raises(RuntimeError, match="boom"):
            await fut

    def test_record_soft_preemption_helper(self):
        from maop.delegate.dispatcher import Dispatcher
        d = Dispatcher()
        before = MAOP_TASK_PREEMPTION_TOTAL.get()
        # incoming priority 1 vs running [5] => should record
        d._record_soft_preemption_for_dispatch(1, [5])
        after = MAOP_TASK_PREEMPTION_TOTAL.get()
        assert after == before + 1.0

        # incoming priority 5 vs running [1] => no record
        before2 = MAOP_TASK_PREEMPTION_TOTAL.get()
        d._record_soft_preemption_for_dispatch(5, [1])
        after2 = MAOP_TASK_PREEMPTION_TOTAL.get()
        assert after2 == before2

        # empty running list => no record
        before3 = MAOP_TASK_PREEMPTION_TOTAL.get()
        d._record_soft_preemption_for_dispatch(1, [])
        after3 = MAOP_TASK_PREEMPTION_TOTAL.get()
        assert after3 == before3


# ── Checkpoint completeness sanity check (documents the decision) ──


class TestCheckpointCompleteness:
    """Documents the Phase γ-2 risk assessment that drove the soft-preemption
    decision. These tests assert the structural facts about
    PipelineCheckpoint so that a future change to wire it into the execution
    path is detectable here (and can flip the scheduler to true preemption).
    """

    def test_step_checkpoint_has_required_fields(self):
        from maop.core.pipeline_checkpoint import StepCheckpoint
        sc = StepCheckpoint()
        # The 4 fields the project_memory.md constraint requires.
        assert hasattr(sc, "status")
        assert hasattr(sc, "output")   # serves as "result"
        assert hasattr(sc, "attempts")
        assert hasattr(sc, "error")

    def test_worker_pool_does_not_use_checkpoint(self):
        """WorkerPool._run_task must not reference PipelineCheckpoint.

        This is the structural reason soft preemption was chosen: cancelling
        a running task loses its in-progress work because no checkpoint is
        written. If this test starts failing (checkpoint is wired in), the
        scheduler can be upgraded to true preemption.
        """
        import inspect
        from maop.core.worker_pool import WorkerPool
        src = inspect.getsource(WorkerPool)
        assert "PipelineCheckpoint" not in src
        assert "pipeline_checkpoint" not in src
