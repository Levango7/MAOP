"""Tests for MAOP Phase γ-3 — true preemption.

Covers:
  - :meth:`WorkerPool.cancel` — plain vs preemptive cancellation,
    ``TaskPreemptedError`` resolution, no-op on unknown/finished
    tasks.
  - Checkpoint wiring: every executed task records a durable
    run (start/complete/fail); a *preempted* task leaves its
    step in ``running`` status (neither complete nor fail).
  - :meth:`PreemptableWorkerPool._maybe_preempt` — victim
    selection (lowest priority, FIFO/anti-thrash tie-breaks),
    no-victim and thrash-protection cases, cancel-race undo.
  - The completion watcher's preemption branch: token stays
    pending, bookkeeping cleaned, no error recorded.
  - Dispatch-loop integration: with ``true_preemption=True`` a
    queued higher-priority task actually cancels a lower-priority
    running task and is admitted in its slot.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from maop.core.monitoring.monitoring import MAOP_TASK_PREEMPTION_TOTAL
from maop.core.reliability.preemptable_worker_pool import PreemptableWorkerPool
from maop.core.reliability.priority_queue import PriorityTask
from maop.core.reliability.worker_pool import (
    PoolStats,
    TaskPreemptedError,
    WorkerPool,
)

# ── Test doubles ─────────────────────────────────────────

class FakeCheckpoint:
    """Duck-typed PipelineCheckpoint recording every call."""

    def __init__(self) -> None:
        self.calls: list[tuple] = []

    def start_run(self, workflow_name, steps, variables=None):
        self.calls.append(("start_run", workflow_name, tuple(steps)))
        return "run-fake"

    def start_step(self, run_id, step_name):
        self.calls.append(("start_step", run_id, step_name))
        return True

    def complete_step(self, run_id, step_name, output="", metadata=None):
        self.calls.append(("complete_step", run_id, step_name))
        return True

    def fail_step(self, run_id, step_name, error="", metadata=None):
        self.calls.append(("fail_step", run_id, step_name, error))
        return True


async def _never_run(task, workdir="", skip_verify=False, agent=""):
    """Stands in for a long MaopLoop.run: never resolves."""
    await asyncio.Event().wait()


# ── WorkerPool.cancel ────────────────────────────────────

class TestWorkerPoolCancel:
    @pytest.mark.asyncio
    async def test_cancel_preempt_resolves_with_preempted_error(self):
        pool = WorkerPool(max_workers=1, root_dir="")
        await pool.start()
        try:
            with patch("maop.maop_loop.MaopLoop") as mock_loop_cls:
                mock_loop = MagicMock()
                mock_loop.run = AsyncMock(side_effect=_never_run)
                mock_loop_cls.return_value = mock_loop
                task_id = await pool.submit("long task")
                await asyncio.sleep(0.05)  # let it start running
                wt = pool.get_task(task_id)
                assert wt is not None
                assert wt.status == "running"

                assert pool.cancel(task_id, preempt=True) is True
                with pytest.raises(TaskPreemptedError):
                    await pool.wait(task_id, timeout=5)
                wt = pool.get_task(task_id)
                assert wt is not None
                assert wt.status == "preempted"
        finally:
            await pool.stop()

    @pytest.mark.asyncio
    async def test_cancel_plain_marks_cancelled(self):
        pool = WorkerPool(max_workers=1, root_dir="")
        await pool.start()
        try:
            with patch("maop.maop_loop.MaopLoop") as mock_loop_cls:
                mock_loop = MagicMock()
                mock_loop.run = AsyncMock(side_effect=_never_run)
                mock_loop_cls.return_value = mock_loop
                task_id = await pool.submit("long task")
                await asyncio.sleep(0.05)

                assert pool.cancel(task_id) is True
                with pytest.raises(asyncio.CancelledError):
                    await pool.wait(task_id, timeout=5)
                wt = pool.get_task(task_id)
                assert wt is not None
                assert wt.status == "cancelled"
        finally:
            await pool.stop()

    @pytest.mark.asyncio
    async def test_cancel_unknown_or_finished_returns_false(self):
        pool = WorkerPool(max_workers=1, root_dir="")
        await pool.start()
        try:
            # Unknown id.
            assert pool.cancel("does-not-exist", preempt=True) is False

            with patch("maop.maop_loop.MaopLoop") as mock_loop_cls:
                mock_loop = MagicMock()
                mock_loop.run = AsyncMock(return_value="done")
                mock_loop_cls.return_value = mock_loop
                task_id = await pool.submit("quick task")
                await pool.wait(task_id, timeout=5)
                # Already finished — cancel is a no-op.
                assert pool.cancel(task_id, preempt=True) is False
                wt = pool.get_task(task_id)
                assert wt is not None
                assert wt.status == "success"
        finally:
            await pool.stop()

    @pytest.mark.asyncio
    async def test_cancel_while_waiting_for_slot_resolves_future(self):
        """Cancellation landing on the semaphore wait must not hang.

        Regression guard for the γ-3 restructure: the old code let
        CancelledError escape _run_task uncaught when it landed
        before the inner try, leaving the result future (and the
        completion watcher) unresolved forever.
        """
        pool = WorkerPool(max_workers=1, root_dir="")
        await pool.start()
        try:
            with patch("maop.maop_loop.MaopLoop") as mock_loop_cls:
                mock_loop = MagicMock()
                mock_loop.run = AsyncMock(side_effect=_never_run)
                mock_loop_cls.return_value = mock_loop

                # Occupy the only slot.
                blocker_id = await pool.submit("blocker")
                await asyncio.sleep(0.05)
                # Queued behind it (still waiting on the semaphore).
                queued_id = await pool.submit("queued")
                await asyncio.sleep(0.05)
                wt = pool.get_task(queued_id)
                assert wt is not None
                assert wt.status == "pending"

                # Preempt-cancel the queued task before it starts.
                assert pool.cancel(queued_id, preempt=True) is True
                with pytest.raises(TaskPreemptedError):
                    await pool.wait(queued_id, timeout=5)
                wt = pool.get_task(queued_id)
                assert wt is not None
                assert wt.status == "preempted"

                # Unblock the blocker so stop() is clean.
                pool.cancel(blocker_id, preempt=True)
                with pytest.raises(TaskPreemptedError):
                    await pool.wait(blocker_id, timeout=5)
        finally:
            await pool.stop()


# ── Checkpoint wiring ────────────────────────────────────

class TestCheckpointWiring:
    @pytest.mark.asyncio
    async def test_successful_task_records_run(self):
        ckpt = FakeCheckpoint()
        pool = WorkerPool(max_workers=1, root_dir="", checkpoint=ckpt)
        await pool.start()
        try:
            with patch("maop.maop_loop.MaopLoop") as mock_loop_cls:
                mock_loop = MagicMock()
                mock_loop.run = AsyncMock(return_value="ok")
                mock_loop_cls.return_value = mock_loop
                task_id = await pool.submit("write the docs")
                await pool.wait(task_id, timeout=5)
        finally:
            await pool.stop()

        assert ("start_run", "worker_pool", ("write the docs",)) in ckpt.calls
        assert ("start_step", "run-fake", "write the docs") in ckpt.calls
        assert ("complete_step", "run-fake", "write the docs") in ckpt.calls
        assert not any(c[0] == "fail_step" for c in ckpt.calls)

    @pytest.mark.asyncio
    async def test_failed_task_records_fail_step(self):
        ckpt = FakeCheckpoint()
        pool = WorkerPool(max_workers=1, root_dir="", checkpoint=ckpt)
        await pool.start()
        try:
            with patch("maop.maop_loop.MaopLoop") as mock_loop_cls:
                mock_loop = MagicMock()
                mock_loop.run = AsyncMock(side_effect=RuntimeError("boom"))
                mock_loop_cls.return_value = mock_loop
                task_id = await pool.submit("doomed task")
                with pytest.raises(RuntimeError):
                    await pool.wait(task_id, timeout=5)
        finally:
            await pool.stop()

        assert ("start_step", "run-fake", "doomed task") in ckpt.calls
        assert ("fail_step", "run-fake", "doomed task", "boom") in ckpt.calls
        assert not any(c[0] == "complete_step" for c in ckpt.calls)

    @pytest.mark.asyncio
    async def test_preempted_task_leaves_step_running(self):
        """The durable record of a preempted task stays 'running'.

        pending_steps() reports stuck-'running' steps for retry —
        that is the resumable state true preemption relies on.
        """
        ckpt = FakeCheckpoint()
        pool = WorkerPool(max_workers=1, root_dir="", checkpoint=ckpt)
        await pool.start()
        try:
            with patch("maop.maop_loop.MaopLoop") as mock_loop_cls:
                mock_loop = MagicMock()
                mock_loop.run = AsyncMock(side_effect=_never_run)
                mock_loop_cls.return_value = mock_loop
                task_id = await pool.submit("half-done task")
                await asyncio.sleep(0.05)
                pool.cancel(task_id, preempt=True)
                with pytest.raises(TaskPreemptedError):
                    await pool.wait(task_id, timeout=5)
        finally:
            await pool.stop()

        assert ("start_step", "run-fake", "half-done task") in ckpt.calls
        assert not any(
            c[0] in ("complete_step", "fail_step") for c in ckpt.calls
        )

    @pytest.mark.asyncio
    async def test_without_checkpoint_no_records_no_crash(self):
        """Default (no checkpoint) keeps the pre-γ-3 behaviour."""
        pool = WorkerPool(max_workers=1, root_dir="")
        await pool.start()
        try:
            with patch("maop.maop_loop.MaopLoop") as mock_loop_cls:
                mock_loop = MagicMock()
                mock_loop.run = AsyncMock(return_value="fine")
                mock_loop_cls.return_value = mock_loop
                task_id = await pool.submit("plain task")
                assert await pool.wait(task_id, timeout=5) == "fine"
        finally:
            await pool.stop()


# ── PreemptableWorkerPool._maybe_preempt ─────────────────

def _make_preempt_pool(true_preemption: bool = True, max_preemptions: int = 2):
    pool = PreemptableWorkerPool(
        max_workers=1,
        true_preemption=true_preemption,
        max_preemptions=max_preemptions,
        checkpoint=FakeCheckpoint(),
    )
    return pool


class TestMaybePreempt:
    def test_selects_lowest_priority_victim(self):
        pool = _make_preempt_pool()
        head = PriorityTask(payload={"task": "urgent"}, priority=1)
        pool._queue.push(head)
        victim_a = PriorityTask(payload={"task": "a"}, priority=5)
        victim_b = PriorityTask(payload={"task": "b"}, priority=3)
        pool._running = {"wp-a": victim_a, "wp-b": victim_b}
        cancelled: list[tuple] = []

        def _cancel(wp_id: str, *, preempt: bool = False) -> bool:
            cancelled.append((wp_id, preempt))
            return True

        pool._pool.cancel = _cancel  # type: ignore[method-assign]

        assert pool._maybe_preempt() is True
        # Lowest priority (5) wins over 3.
        assert cancelled == [("wp-a", True)]
        # Victim re-enqueued under its original token, marked once.
        assert len(pool._queue) == 2  # head + victim
        assert victim_a.preempted_count == 1
        # Marked for the completion watcher's preemption branch.
        assert "wp-a" in pool._preempted_wp_ids

    def test_no_victim_when_no_strictly_lower_priority_running(self):
        pool = _make_preempt_pool()
        head = PriorityTask(payload={"task": "normal"}, priority=3)
        pool._queue.push(head)
        same = PriorityTask(payload={"task": "same"}, priority=3)
        higher = PriorityTask(payload={"task": "higher"}, priority=2)
        pool._running = {"wp-1": same, "wp-2": higher}
        assert pool._maybe_preempt() is False
        assert len(pool._queue) == 1

    def test_empty_queue_no_preemption(self):
        pool = _make_preempt_pool()
        pool._running = {"wp-1": PriorityTask(payload={}, priority=5)}
        assert pool._maybe_preempt() is False

    def test_thrash_protection_excludes_exhausted_victim(self):
        pool = _make_preempt_pool(max_preemptions=2)
        head = PriorityTask(payload={"task": "urgent"}, priority=1)
        pool._queue.push(head)
        tired = PriorityTask(payload={"task": "tired"}, priority=5)
        tired.preempted_count = 2  # at the limit
        fresh = PriorityTask(payload={"task": "fresh"}, priority=4)
        pool._running = {"wp-tired": tired, "wp-fresh": fresh}
        cancelled: list[str] = []

        def _cancel(wp_id: str, *, preempt: bool = False) -> bool:
            cancelled.append(wp_id)
            return True

        pool._pool.cancel = _cancel  # type: ignore[method-assign]

        assert pool._maybe_preempt() is True
        # The exhausted victim is skipped; the fresh p4 task is chosen.
        assert cancelled == ["wp-fresh"]
        assert fresh.preempted_count == 1
        assert tired.preempted_count == 2

    def test_cancel_race_undoes_marks(self):
        """If the victim completed before cancel landed, undo everything."""
        pool = _make_preempt_pool()
        head = PriorityTask(payload={"task": "urgent"}, priority=1)
        pool._queue.push(head)
        victim = PriorityTask(payload={"task": "victim"}, priority=5)
        pool._running = {"wp-victim": victim}

        def _raced_cancel(wp_id: str, *, preempt: bool = False) -> bool:
            return False  # raced with completion

        pool._pool.cancel = _raced_cancel  # type: ignore[method-assign]

        assert pool._maybe_preempt() is False
        assert victim.preempted_count == 0
        assert "wp-victim" not in pool._preempted_wp_ids
        assert len(pool._queue) == 1  # victim NOT re-enqueued

    def test_metric_incremented_on_true_preemption(self):
        pool = _make_preempt_pool()
        head = PriorityTask(payload={"task": "urgent"}, priority=1)
        pool._queue.push(head)
        victim = PriorityTask(payload={"task": "victim"}, priority=5)
        pool._running = {"wp-victim": victim}
        pool._pool.cancel = (  # type: ignore[method-assign]
            lambda wp_id, *, preempt=False: True
        )

        before = MAOP_TASK_PREEMPTION_TOTAL.get()
        assert pool._maybe_preempt() is True
        after = MAOP_TASK_PREEMPTION_TOTAL.get()
        assert after == before + 1

    def test_soft_preemption_pool_never_cancels(self):
        """true_preemption=False (default): _maybe_preempt is never
        consulted by the dispatch loop, and directly it is inert."""
        pool = _make_preempt_pool(true_preemption=False)
        head = PriorityTask(payload={"task": "urgent"}, priority=1)
        pool._queue.push(head)
        victim = PriorityTask(payload={"task": "victim"}, priority=5)
        pool._running = {"wp-victim": victim}
        cancelled: list[str] = []

        def _cancel(wp_id: str, *, preempt: bool = False) -> bool:
            cancelled.append(wp_id)
            return True

        pool._pool.cancel = _cancel  # type: ignore[method-assign]
        # Even called directly, the soft pool must not cancel.
        assert pool._maybe_preempt() is False
        assert cancelled == []


# ── Completion watcher: preemption branch ────────────────

class TestWatcherPreemptionBranch:
    @pytest.mark.asyncio
    async def test_preempted_task_keeps_token_pending(self):
        pool = _make_preempt_pool()
        pt = PriorityTask(payload={"task": "victim"}, priority=5)
        token = f"pt-{pt.enqueue_order}"
        pool._token_events[token] = asyncio.Event()
        pool._running = {"wp-victim": pt}
        pool._preempted_wp_ids = {"wp-victim"}

        async def fake_wait(wp_id):
            raise TaskPreemptedError(f"task {wp_id} preempted")

        pool._pool.wait = fake_wait  # type: ignore[method-assign]
        await pool._watch_completion("wp-victim", pt)

        # Bookkeeping cleaned…
        assert "wp-victim" not in pool._running
        assert "wp-victim" not in pool._preempted_wp_ids
        # …but the token stays pending: no result, no error, no event.
        assert token not in pool._token_results
        assert token not in pool._token_errors
        assert not pool._token_events[token].is_set()


# ── Dispatch loop integration ────────────────────────────

class TestDispatchLoopIntegration:
    @pytest.mark.asyncio
    async def test_loop_preempts_and_admits_high_priority_head(self):
        pool = _make_preempt_pool()
        inner = pool._pool

        # Victim already running (as the dispatch loop would have
        # registered it), queue head waiting with strictly higher
        # priority.
        victim = PriorityTask(payload={"task": "low-priority"}, priority=5)
        pool._running = {"wp-victim": victim}
        head = PriorityTask(payload={"task": "urgent"}, priority=1)
        pool._queue.push(head)

        # Tick 1: no idle slot -> preemption happens.
        # Tick 2: one slot -> the urgent head is admitted.
        # Tick 3+: no slot again -> stable (the re-enqueued
        # victim, p5, is not a candidate against the running
        # p1 head, so no further preemption).
        stats_values = [
            PoolStats(idle_workers=0),
            PoolStats(idle_workers=1),
            PoolStats(idle_workers=0),
        ]
        stats_calls = {"n": 0}

        def fake_stats() -> PoolStats:
            idx = min(stats_calls["n"], len(stats_values) - 1)
            stats_calls["n"] += 1
            return stats_values[idx]

        submitted: list[str] = []
        cancelled: list[tuple] = []

        inner.stats = fake_stats  # type: ignore[method-assign]

        async def fake_submit(task, *, workdir="", skip_verify=False,
                              agent_name=""):
            submitted.append(task)
            return "wp-new"

        async def fake_wait(wp_id):
            await asyncio.Event().wait()  # never resolves; stop() cancels

        def _cancel(wp_id: str, *, preempt: bool = False) -> bool:
            cancelled.append((wp_id, preempt))
            return True

        inner.submit = fake_submit  # type: ignore[method-assign]
        inner.wait = fake_wait  # type: ignore[method-assign]
        inner.cancel = _cancel  # type: ignore[method-assign]

        pool._stop_event = asyncio.Event()
        dispatch = asyncio.ensure_future(pool._dispatch_loop())
        try:
            await asyncio.sleep(0.15)  # a few dispatch ticks
        finally:
            pool._stop_event.set()
            await dispatch  # loop exits on the stop event
            # Cancel the watchers still awaiting the never-
            # resolving fake_wait (the stop() path).
            await pool.stop()

        # The victim was truly cancelled (not just recorded)…
        assert ("wp-victim", True) in cancelled
        # …re-enqueued (queue still holds it under its token)…
        queued_priorities = [t.priority for t in pool._queue.snapshot()]
        assert 5 in queued_priorities
        assert victim.preempted_count == 1
        # …and the urgent head was admitted in the freed slot.
        assert submitted == ["urgent"]
        assert pool._token_to_wp_id[f"pt-{head.enqueue_order}"] == "wp-new"
