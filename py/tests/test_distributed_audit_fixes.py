"""Tests for backend-audit batch A — distributed execution path fixes.

Covers the four defects found in the 2026-10-09 backend audit:

- ``_read_results`` re-read the result stream from the head on
  every poll (``count=100``): a layer with more than 100 posted
  results never surfaced its tail (the run hung forever), and the
  failure detector re-recorded every completed task on every poll.
  Result reading is now incremental behind a stream-id cursor.
- ``_dispatch_node`` computed the registry's capable-worker list
  twice per dispatch (selection + warning).
- ``DistributedWorker._handle_task`` was not cancellation-safe:
  a ``CancelledError`` leaked the in-flight slot, never ACKed the
  task, and never posted a result (the scheduler waited forever).
  Handler tasks are now strongly referenced (the event loop only
  holds weak references) and ``stop()`` force-cancels handlers
  that outlive the drain window.
- ``DistributedScheduler.run`` had no deadline: a worker that is
  alive-but-stuck keeps refreshing its heartbeat, so failure
  detection never fires and the run waited forever.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

import fakeredis
import pytest

from maop.core.scheduling import SchedulingError
from maop.core.scheduling.distributed_scheduler import (
    _F_AFFINITY,
    _F_NODE_ID,
    _F_PAYLOAD,
    _F_RUN_ID,
    DistributedScheduler,
    _NodeSpec,
)
from maop.core.scheduling.failure_detector import FailurePatternDetector
from maop.core.scheduling.worker_pool import WorkerRegistry
from maop.worker.distributed_worker import (
    DistributedWorker,
    TaskResult,
    WorkerConfig,
)

# ── Fixtures ─────────────────────────────────────────────────

@pytest.fixture
def fake_redis() -> Any:
    """Provide a fresh fakeredis server for each test."""
    return fakeredis.FakeRedis()


@pytest.fixture
def registry(fake_redis: Any) -> WorkerRegistry:
    """Provide a WorkerRegistry backed by fakeredis."""
    return WorkerRegistry(fake_redis, heartbeat_timeout=2.0)


class _CountingDetector(FailurePatternDetector):
    """Detector that records every ``record_result`` call."""

    def __init__(self) -> None:
        super().__init__()
        self.recorded: list[tuple[str, bool]] = []

    def record_result(
        self,
        agent_id: str,
        success: bool,
        latency: float = 0.0,
    ) -> None:
        self.recorded.append((agent_id, success))
        super().record_result(agent_id, success, latency)


class TestReadResultsCursor:
    """Incremental result-stream reading."""

    @pytest.mark.asyncio
    async def test_cursor_processes_each_entry_once(
        self, fake_redis: Any, registry: Any,
    ) -> None:
        scheduler = DistributedScheduler(
            fake_redis, poll_interval=0.05, registry=registry,
        )
        stream = scheduler._results_stream("run-cursor")
        for i in range(3):
            scheduler.post_result(
                "run-cursor", f"n{i}", status="success", worker_id="w1",
            )
        out1, cursor1 = scheduler._read_results(stream, {"n0", "n1", "n2"})
        assert set(out1) == {"n0", "n1", "n2"}
        assert cursor1 != (0, 0)
        # A second read from the cursor returns nothing new.
        out2, cursor2 = scheduler._read_results(
            stream, {"n0", "n1", "n2"}, cursor1,
        )
        assert out2 == {}
        assert cursor2 == cursor1

    @pytest.mark.asyncio
    async def test_cursor_advances_over_unexpected_nodes(
        self, fake_redis: Any, registry: Any,
    ) -> None:
        """Entries for other nodes advance the cursor without results."""
        scheduler = DistributedScheduler(
            fake_redis, poll_interval=0.05, registry=registry,
        )
        stream = scheduler._results_stream("run-mixed")
        scheduler.post_result("run-mixed", "other", status="success", worker_id="w1")
        scheduler.post_result("run-mixed", "wanted", status="success", worker_id="w1")
        out, cursor = scheduler._read_results(stream, {"wanted"})
        assert set(out) == {"wanted"}
        # The cursor moved past both entries.
        out2, _ = scheduler._read_results(stream, {"wanted"}, cursor)
        assert out2 == {}


class TestBigLayer:
    """Regression: a layer with >100 nodes used to hang forever."""

    @pytest.mark.asyncio
    async def test_run_completes_with_more_than_100_results(
        self, fake_redis: Any,
    ) -> None:
        scheduler = DistributedScheduler(fake_redis, poll_interval=0.02)
        worker = DistributedWorker(
            fake_redis, scheduler=scheduler,
            config=WorkerConfig(worker_id="w-big", concurrency=50),
        )
        await worker.start()
        try:
            nodes = [_NodeSpec(id=f"n{i:03d}", payload={"i": i}) for i in range(150)]
            result = await scheduler.run(nodes, deadline_s=30)
            assert result.success is True
            assert len(result.results) == 150
            assert all(r["status"] == "success" for r in result.results.values())
        finally:
            await worker.stop()


class TestRunDeadline:
    """The run-level deadline bounds alive-but-stuck workers."""

    @pytest.mark.asyncio
    async def test_deadline_raises_when_no_result_arrives(
        self, fake_redis: Any,
    ) -> None:
        scheduler = DistributedScheduler(fake_redis, poll_interval=0.02)
        # No worker consumes the task stream: the result never
        # arrives and the deadline must fire instead of hanging.
        with pytest.raises(SchedulingError, match="deadline"):
            await scheduler.run([_NodeSpec(id="stuck")], deadline_s=0.1)

    @pytest.mark.asyncio
    async def test_no_deadline_by_default(self, fake_redis: Any) -> None:
        scheduler = DistributedScheduler(fake_redis, poll_interval=0.02)
        # Default is unbounded — a fast run must not be killed.
        worker = DistributedWorker(
            fake_redis, scheduler=scheduler,
            config=WorkerConfig(worker_id="w-nodl", concurrency=2),
        )
        await worker.start()
        try:
            result = await scheduler.run([_NodeSpec(id="quick")])
            assert result.success is True
        finally:
            await worker.stop()


class TestDispatchSelection:
    """Worker selection must not scan the registry twice."""

    @pytest.mark.asyncio
    async def test_dispatch_node_scans_capable_workers_once(
        self, fake_redis: Any, registry: Any,
    ) -> None:
        scheduler = DistributedScheduler(
            fake_redis, poll_interval=0.05, registry=registry,
        )
        registry.register(
            host="h", concurrency=1, capabilities=set(), worker_id="w1",
        )
        calls = 0
        orig = registry.capable_workers

        def counting(required: Any = None) -> list[str]:
            nonlocal calls
            calls += 1
            return orig(required)

        registry.capable_workers = counting  # type: ignore[method-assign]
        try:
            await scheduler._dispatch_node(
                "run-scan", 0, _NodeSpec(id="n1"),
            )
        finally:
            del registry.capable_workers  # type: ignore[method-assign]
        assert calls == 1


class TestFailureDetectorWindow:
    """Each task outcome must be recorded exactly once."""

    @pytest.mark.asyncio
    async def test_detector_records_each_task_once(
        self, fake_redis: Any,
    ) -> None:
        detector = _CountingDetector()
        scheduler = DistributedScheduler(
            fake_redis, poll_interval=0.02, failure_detector=detector,
        )
        worker = DistributedWorker(
            fake_redis, scheduler=scheduler,
            config=WorkerConfig(worker_id="w-det", concurrency=25),
        )
        await worker.start()
        try:
            nodes = [_NodeSpec(id=f"n{i:02d}") for i in range(10)]
            result = await scheduler.run(nodes, deadline_s=30)
            assert result.success is True
            # The old head-re-read recorded every completed task
            # on every poll (polls × tasks samples).
            assert len(detector.recorded) == 10
            assert all(success for _wid, success in detector.recorded)
        finally:
            await worker.stop()


class TestHandleTaskCancellation:
    """_handle_task must be cancellation-safe."""

    @pytest.mark.asyncio
    async def test_cancelled_task_posts_result_and_acks(
        self, fake_redis: Any,
    ) -> None:
        scheduler = DistributedScheduler(fake_redis, poll_interval=0.05)
        posted: list[tuple[str, str]] = []
        orig_post = scheduler.post_result

        def recording_post(run_id: str, node_id: str, **kw: Any) -> str:
            posted.append((node_id, kw.get("status", "")))
            return orig_post(run_id, node_id, **kw)

        scheduler.post_result = recording_post  # type: ignore[method-assign]

        async def hang(node_id: str, payload: Any, affinity: Any) -> TaskResult:
            await asyncio.sleep(60)
            return TaskResult(status="success")

        worker = DistributedWorker(
            fake_redis, scheduler=scheduler, executor=hang,
            config=WorkerConfig(worker_id="w-cancel", concurrency=1),
        )
        # Write the task to the stream and read it back the way
        # the consumer loop does, so the ACK is observable.
        stream = scheduler.task_stream
        fake_redis.xadd(
            stream,
            {
                _F_NODE_ID.encode(): b"n-cancel",
                _F_RUN_ID.encode(): b"run-cancel",
                _F_AFFINITY.encode(): b"",
                _F_PAYLOAD.encode(): b"{}",
            },
        )
        entries = fake_redis.xreadgroup("maop_workers", "c-test", {stream: ">"})
        msg_id, fields = entries[0][1][0]
        task = asyncio.ensure_future(
            worker._handle_task(stream, "maop_workers", msg_id, fields, asyncio.Semaphore(1)),
        )
        await asyncio.sleep(0.05)  # let the handler enter the executor
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        # A terminal result was posted for the node.
        assert ("n-cancel", "cancelled") in posted
        # The task was ACKed: it left the consumer group's
        # pending list (no redelivery).
        pending = fake_redis.xpending_range(stream, "maop_workers", min="-", max="+", count=10)
        assert pending == []
        # The in-flight slot was released.
        assert not worker._in_flight

    @pytest.mark.asyncio
    async def test_stop_cancels_stuck_handlers(
        self, fake_redis: Any,
    ) -> None:
        scheduler = DistributedScheduler(fake_redis, poll_interval=0.02)
        release = asyncio.Event()

        async def hang(node_id: str, payload: Any, affinity: Any) -> TaskResult:
            await release.wait()
            return TaskResult(status="success")

        worker = DistributedWorker(
            fake_redis, scheduler=scheduler, executor=hang,
            config=WorkerConfig(worker_id="w-stuck", concurrency=1),
        )
        await worker.start()
        run_task = asyncio.ensure_future(
            scheduler.run([_NodeSpec(id="n-stuck")], deadline_s=30),
        )
        # Wait until the worker picked the task up.
        deadline = time.monotonic() + 5
        while not worker._in_flight and time.monotonic() < deadline:
            await asyncio.sleep(0.02)
        assert worker._in_flight
        # stop() must return (bounded) even with a stuck executor,
        # and the run must resolve with a terminal outcome.
        await asyncio.wait_for(worker.stop(), timeout=8)
        release.set()
        result = await asyncio.wait_for(run_task, timeout=5)
        assert result.results["n-stuck"]["status"] == "cancelled"
        assert result.success is False
