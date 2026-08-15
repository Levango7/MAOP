"""Tests for F1-01 Distributed Execution — DistributedScheduler + Worker pool.

Uses ``fakeredis`` as a mock Redis server (no real Redis dependency).
Covers:
  - WorkerRegistry: register / heartbeat / failure detection / affinity
  - DistributedScheduler: DAG layering, dispatch, result aggregation
  - DistributedWorker: consume tasks, execute, post results
  - Engine integration: distributed=True with Redis, fallback to single-process
  - CLI: ``maop worker start`` argument parsing
"""

from __future__ import annotations

import asyncio
import json
import time
from typing import Any
from unittest.mock import patch

import fakeredis
import pytest

from maop.core.scheduling import (
    DistributedScheduler,
    NodeAffinity,
    SchedulingError,
    TaskAssignment,
    WorkerInfo,
    WorkerRegistry,
    WorkerStatus,
)
from maop.core.scheduling.distributed_scheduler import (
    _NodeSpec,
    node_spec_from_step,
)
from maop.engine import Engine, EngineResult, StepType, WorkflowStep
from maop.worker.distributed_worker import (
    DistributedWorker,
    TaskResult,
    WorkerConfig,
    default_executor,
)

# ── Fixtures ──────────────────────────────────────────────────────

@pytest.fixture
def fake_redis() -> Any:
    """Provide a fresh fakeredis server for each test."""
    return fakeredis.FakeRedis()


@pytest.fixture
def registry(fake_redis: Any) -> WorkerRegistry:
    """Provide a WorkerRegistry backed by fakeredis."""
    return WorkerRegistry(fake_redis, heartbeat_timeout=2.0)


@pytest.fixture
def scheduler(fake_redis: Any) -> DistributedScheduler:
    """Provide a DistributedScheduler backed by fakeredis."""
    return DistributedScheduler(fake_redis, poll_interval=0.05)


# ── WorkerRegistry tests ──────────────────────────────────────────

class TestWorkerRegistry:
    """Worker registration, heartbeat, and failure detection."""

    def test_register_returns_id(self, registry: WorkerRegistry) -> None:
        wid = registry.register(host="host1", concurrency=4, capabilities={"gpu"})
        assert isinstance(wid, str)
        assert len(wid) > 0

    def test_register_explicit_id(self, registry: WorkerRegistry) -> None:
        wid = registry.register(worker_id="worker-abc", host="host1")
        assert wid == "worker-abc"

    def test_list_workers(self, registry: WorkerRegistry) -> None:
        registry.register(worker_id="w1")
        registry.register(worker_id="w2")
        assert set(registry.list_workers()) == {"w1", "w2"}

    def test_get_worker_info(self, registry: WorkerRegistry) -> None:
        registry.register(
            worker_id="w1", host="host1", concurrency=8, capabilities={"gpu", "linux"},
        )
        info = registry.get_worker("w1")
        assert info is not None
        assert info.worker_id == "w1"
        assert info.host == "host1"
        assert info.concurrency == 8
        assert info.capabilities == {"gpu", "linux"}
        assert info.status == WorkerStatus.ACTIVE

    def test_get_worker_unknown(self, registry: WorkerRegistry) -> None:
        assert registry.get_worker("nonexistent") is None

    def test_heartbeat_refreshes(self, registry: WorkerRegistry) -> None:
        wid = registry.register(worker_id="w1")
        assert registry.heartbeat(wid) is True

    def test_heartbeat_unknown_worker(self, registry: WorkerRegistry) -> None:
        assert registry.heartbeat("nonexistent") is False

    def test_unregister(self, registry: WorkerRegistry) -> None:
        wid = registry.register(worker_id="w1")
        assert registry.unregister(wid) is True
        assert wid not in registry.list_workers()
        # Second unregister returns False.
        assert registry.unregister(wid) is False

    def test_detect_failures_none(self, registry: WorkerRegistry) -> None:
        registry.register(worker_id="w1")
        registry.heartbeat("w1")
        failed = registry.detect_failures()
        assert failed == []

    def test_detect_failures_expired(self, registry: WorkerRegistry) -> None:
        """Worker with expired heartbeat is detected as failed."""
        wid = registry.register(worker_id="w1")
        # Simulate heartbeat expiry by deleting the heartbeat key.
        registry._redis.delete(registry._heartbeat_key(wid))  # type: ignore[attr-defined]
        failed = registry.detect_failures()
        assert len(failed) == 1
        assert failed[0][0] == wid
        # Worker is pruned from registry.
        assert wid not in registry.list_workers()

    def test_capable_workers_no_requirement(self, registry: WorkerRegistry) -> None:
        registry.register(worker_id="w1", capabilities={"gpu"})
        registry.register(worker_id="w2", capabilities=set())
        # No requirement → all workers qualify.
        capable = registry.capable_workers(None)
        assert set(capable) == {"w1", "w2"}

    def test_capable_workers_with_requirement(self, registry: WorkerRegistry) -> None:
        registry.register(worker_id="w1", capabilities={"gpu", "linux"})
        registry.register(worker_id="w2", capabilities={"cpu"})
        registry.register(worker_id="w3", capabilities={"gpu"})
        capable = registry.capable_workers("gpu")
        assert set(capable) == {"w1", "w3"}
        capable = registry.capable_workers({"gpu", "linux"})
        assert set(capable) == {"w1"}

    def test_in_flight_tracking(self, registry: WorkerRegistry) -> None:
        wid = registry.register(worker_id="w1")
        registry.assign_task(wid, "task-1")
        registry.assign_task(wid, "task-2")
        assert registry.in_flight(wid) == {"task-1", "task-2"}
        registry.complete_task(wid, "task-1")
        assert registry.in_flight(wid) == {"task-2"}

    def test_active_count(self, registry: WorkerRegistry) -> None:
        registry.register(worker_id="w1")
        registry.register(worker_id="w2")
        assert registry.active_count() == 2
        # Expire one heartbeat.
        registry._redis.delete(registry._heartbeat_key("w1"))  # type: ignore[attr-defined]
        assert registry.active_count() == 1

    def test_repr(self, registry: WorkerRegistry) -> None:
        r = repr(registry)
        assert "WorkerRegistry" in r


# ── NodeAffinity tests ────────────────────────────────────────────

class TestNodeAffinity:
    """Node affinity parsing and matching."""

    def test_parse_none(self) -> None:
        aff = NodeAffinity.parse(None)
        assert aff.required == set()

    def test_parse_str(self) -> None:
        aff = NodeAffinity.parse("gpu")
        assert aff.required == {"gpu"}

    def test_parse_set(self) -> None:
        aff = NodeAffinity.parse({"gpu", "linux"})
        assert aff.required == {"gpu", "linux"}

    def test_parse_affinity(self) -> None:
        original = NodeAffinity(required={"gpu"}, prefer={"fast"})
        aff = NodeAffinity.parse(original)
        assert aff is original

    def test_parse_empty_str(self) -> None:
        aff = NodeAffinity.parse("")
        assert aff.required == set()

    def test_parse_unsupported_type(self) -> None:
        with pytest.raises(TypeError):
            NodeAffinity.parse(123)  # type: ignore[arg-type]


# ── DistributedScheduler tests ────────────────────────────────────

class TestDistributedScheduler:
    """DAG layering, dispatch, and result aggregation."""

    def test_compute_layers_simple(self, scheduler: DistributedScheduler) -> None:
        nodes = [
            _NodeSpec(id="a"),
            _NodeSpec(id="b", depends_on=["a"]),
            _NodeSpec(id="c", depends_on=["a"]),
            _NodeSpec(id="d", depends_on=["b", "c"]),
        ]
        layers = scheduler._compute_layers(nodes)
        assert layers[0] == ["a"]
        assert set(layers[1]) == {"b", "c"}
        assert layers[2] == ["d"]

    def test_compute_layers_cycle(self, scheduler: DistributedScheduler) -> None:
        nodes = [
            _NodeSpec(id="a", depends_on=["b"]),
            _NodeSpec(id="b", depends_on=["a"]),
        ]
        with pytest.raises(SchedulingError, match="cycle"):
            scheduler._compute_layers(nodes)

    def test_compute_layers_unknown_dep(self, scheduler: DistributedScheduler) -> None:
        nodes = [_NodeSpec(id="a", depends_on=["nonexistent"])]
        with pytest.raises(SchedulingError, match="unknown node"):
            scheduler._compute_layers(nodes)

    @pytest.mark.asyncio
    async def test_run_empty(self, scheduler: DistributedScheduler) -> None:
        result = await scheduler.run([])
        assert result.success is True
        assert result.results == {}

    @pytest.mark.asyncio
    async def test_run_single_node(self, scheduler: DistributedScheduler) -> None:
        """A single node is dispatched and its result collected.

        We simulate a worker by reading from the task stream and posting
        a result to the results stream.
        """
        nodes = [_NodeSpec(id="n1", payload={"task": "hello"})]
        # Dispatch happens in scheduler.run(); we need a worker to
        # consume and post results concurrently.
        async def simulate_worker() -> None:
            # Wait for the task to appear in the stream (non-blocking read).
            for _ in range(100):
                entries = scheduler._redis.xreadgroup(
                    "maop_workers", "test-consumer",
                    {scheduler._task_stream: ">"}, count=1,
                )
                if entries:
                    break
                await asyncio.sleep(0.05)
            else:
                return
            for _stream, msgs in entries:
                for msg_id, fields in msgs:
                    node_id = fields[b"node_id"].decode()
                    run_id = fields[b"run_id"].decode()
                    scheduler.post_result(
                        run_id, node_id, status="success",
                        output={"result": "done"}, worker_id="test-worker",
                    )
                    scheduler._redis.xack(scheduler._task_stream, "maop_workers", msg_id)

        worker_task = asyncio.ensure_future(simulate_worker())
        result = await scheduler.run(nodes)
        await worker_task
        assert result.success is True
        assert result.results["n1"]["status"] == "success"

    @pytest.mark.asyncio
    async def test_run_dag_with_deps(
        self, scheduler: DistributedScheduler,
    ) -> None:
        """A 3-node DAG (a → b → c) executes in order."""
        nodes = [
            _NodeSpec(id="a", payload={"step": 1}),
            _NodeSpec(id="b", depends_on=["a"], payload={"step": 2}),
            _NodeSpec(id="c", depends_on=["b"], payload={"step": 3}),
        ]

        async def simulate_worker() -> None:
            completed = 0
            while completed < 3:
                entries = scheduler._redis.xreadgroup(
                    "maop_workers", "test-consumer",
                    {scheduler._task_stream: ">"}, count=3,
                )
                if not entries:
                    await asyncio.sleep(0.05)
                    continue
                for _stream, msgs in entries:
                    for msg_id, fields in msgs:
                        node_id = fields[b"node_id"].decode()
                        run_id = fields[b"run_id"].decode()
                        scheduler.post_result(
                            run_id, node_id, status="success",
                            output={"node": node_id}, worker_id="w1",
                        )
                        scheduler._redis.xack(
                            scheduler._task_stream, "maop_workers", msg_id,
                        )
                        completed += 1

        worker_task = asyncio.ensure_future(simulate_worker())
        result = await scheduler.run(nodes)
        await worker_task
        assert result.success is True
        assert all(
            result.results[nid]["status"] == "success" for nid in ("a", "b", "c")
        )

    @pytest.mark.asyncio
    async def test_run_upstream_failure_skips_downstream(
        self, scheduler: DistributedScheduler,
    ) -> None:
        """When node 'a' fails, node 'b' (depends_on a) is skipped."""
        nodes = [
            _NodeSpec(id="a", payload={"fail": True}),
            _NodeSpec(id="b", depends_on=["a"]),
        ]

        async def simulate_worker() -> None:
            completed = 0
            while completed < 1:
                entries = scheduler._redis.xreadgroup(
                    "maop_workers", "test-consumer",
                    {scheduler._task_stream: ">"}, count=2,
                )
                if not entries:
                    await asyncio.sleep(0.05)
                    continue
                for _stream, msgs in entries:
                    for msg_id, fields in msgs:
                        node_id = fields[b"node_id"].decode()
                        run_id = fields[b"run_id"].decode()
                        payload = json.loads(fields[b"payload"].decode())
                        if payload.get("fail"):
                            status = "failed"
                            output = None
                            error = "intentional failure"
                        else:
                            status = "success"
                            output = {"ok": True}
                            error = ""
                        scheduler.post_result(
                            run_id, node_id, status=status,
                            output=output, error=error, worker_id="w1",
                        )
                        scheduler._redis.xack(
                            scheduler._task_stream, "maop_workers", msg_id,
                        )
                        completed += 1

        worker_task = asyncio.ensure_future(simulate_worker())
        result = await scheduler.run(nodes)
        await worker_task
        assert result.success is False
        assert result.results["a"]["status"] == "failed"
        assert result.results["b"]["status"] == "skipped"

    def test_post_result(self, scheduler: DistributedScheduler) -> None:
        msg_id = scheduler.post_result(
            "run-1", "node-1", status="success",
            output={"data": 42}, worker_id="w1", duration_ms=100,
        )
        assert isinstance(msg_id, str)
        # Verify the result is in the results stream.
        stream = scheduler._results_stream("run-1")
        entries = scheduler._redis.xread({stream: "0"}, count=10)
        assert len(entries) == 1

    def test_repr(self, scheduler: DistributedScheduler) -> None:
        r = repr(scheduler)
        assert "DistributedScheduler" in r


# ── node_spec_from_step helper tests ──────────────────────────────

class TestNodeSpecFromStep:
    """Engine-step to _NodeSpec conversion."""

    def test_basic(self) -> None:
        spec = node_spec_from_step("s1", depends_on=["s0"], priority=2)
        assert spec.id == "s1"
        assert spec.depends_on == ["s0"]
        assert spec.priority == 2

    def test_with_affinity(self) -> None:
        spec = node_spec_from_step("s1", affinity="gpu")
        assert spec.affinity.required == {"gpu"}

    def test_with_payload(self) -> None:
        spec = node_spec_from_step("s1", payload={"task": "write code"})
        assert spec.payload == {"task": "write code"}


# ── DistributedWorker tests ───────────────────────────────────────

class TestDistributedWorker:
    """Worker lifecycle, heartbeat, and task execution."""

    @pytest.mark.asyncio
    async def test_start_stop(self, fake_redis: Any) -> None:
        worker = DistributedWorker(
            fake_redis, config=WorkerConfig(concurrency=2),
        )
        wid = await worker.start()
        assert worker.is_running
        assert isinstance(wid, str)
        await worker.stop()
        assert not worker.is_running

    @pytest.mark.asyncio
    async def test_registers_in_registry(self, fake_redis: Any) -> None:
        worker = DistributedWorker(
            fake_redis,
            config=WorkerConfig(concurrency=4, capabilities={"gpu"}, worker_id="w-test"),
        )
        await worker.start()
        info = worker._registry.get_worker("w-test")  # type: ignore[union-attr]
        assert info is not None
        assert info.capabilities == {"gpu"}
        assert info.concurrency == 4
        await worker.stop()

    @pytest.mark.asyncio
    async def test_heartbeat_refreshes(
        self, fake_redis: Any,
    ) -> None:
        worker = DistributedWorker(
            fake_redis,
            config=WorkerConfig(worker_id="w-hb", heartbeat_interval=0.1),
        )
        await worker.start()
        # Let a couple heartbeats fire.
        await asyncio.sleep(0.25)
        info = worker._registry.get_worker("w-hb")  # type: ignore[union-attr]
        assert info is not None
        # Heartbeat should be recent.
        assert time.time() - info.last_heartbeat < 1.0
        await worker.stop()

    @pytest.mark.asyncio
    async def test_default_executor(self) -> None:
        result = await default_executor("n1", {"task": "hello"}, set())
        assert result.status == "success"
        assert result.output == {"echo": {"task": "hello"}, "node_id": "n1"}

    @pytest.mark.asyncio
    async def test_worker_executes_task(
        self, fake_redis: Any,
    ) -> None:
        """Worker consumes a task from the stream and posts a result."""
        scheduler = DistributedScheduler(fake_redis, poll_interval=0.05)
        worker = DistributedWorker(
            fake_redis, scheduler=scheduler,
            config=WorkerConfig(worker_id="w-exec", concurrency=1),
        )
        await worker.start()
        try:
            # Dispatch a single task.
            nodes = [_NodeSpec(id="task-1", payload={"action": "compute"})]
            result = await scheduler.run(nodes)
            assert result.success is True
            assert result.results["task-1"]["status"] == "success"
            # The default executor echoes the payload.
            output = result.results["task-1"]["output"]
            assert output is not None
        finally:
            await worker.stop()

    @pytest.mark.asyncio
    async def test_worker_with_custom_executor(
        self, fake_redis: Any,
    ) -> None:
        """Worker uses a custom executor to run tasks."""

        async def custom_executor(
            node_id: str, payload: dict[str, Any], affinity: set[str],
        ) -> TaskResult:
            return TaskResult(
                node_id=node_id, status="success",
                output={"processed": payload.get("action", "unknown")},
            )

        scheduler = DistributedScheduler(fake_redis, poll_interval=0.05)
        worker = DistributedWorker(
            fake_redis, scheduler=scheduler, executor=custom_executor,
            config=WorkerConfig(worker_id="w-custom", concurrency=1),
        )
        await worker.start()
        try:
            nodes = [_NodeSpec(id="x1", payload={"action": "transform"})]
            result = await scheduler.run(nodes)
            assert result.success is True
            assert result.results["x1"]["output"] == {"processed": "transform"}
        finally:
            await worker.stop()

    @pytest.mark.asyncio
    async def test_worker_repr(self, fake_redis: Any) -> None:
        worker = DistributedWorker(
            fake_redis, config=WorkerConfig(worker_id="w-repr"),
        )
        await worker.start()
        try:
            r = repr(worker)
            assert "DistributedWorker" in r
            assert "w-repr" in r
        finally:
            await worker.stop()


# ── Engine integration tests ──────────────────────────────────────

class TestEngineDistributedIntegration:
    """Engine.run(distributed=True) with Redis and Personal fallback."""

    @pytest.mark.asyncio
    async def test_single_process_default(self) -> None:
        """Without distributed=True, engine uses single-process (backward-compat)."""
        engine = Engine()
        steps = [
            WorkflowStep(id="s1", type=StepType.AGENT, agent="claude", task="hello"),
            WorkflowStep(id="s2", type=StepType.TERMINAL, depends_on=["s1"]),
        ]
        result = await engine.run(steps)
        assert isinstance(result, EngineResult)
        assert len(result.steps) == 2

    @pytest.mark.asyncio
    async def test_distributed_without_redis_falls_back(self) -> None:
        """distributed=True with no Redis client → single-process fallback."""
        engine = Engine()  # no redis_client
        steps = [
            WorkflowStep(id="s1", type=StepType.AGENT, agent="claude", task="hello"),
        ]
        result = await engine.run(steps, distributed=True)
        assert isinstance(result, EngineResult)
        # Should have executed via single-process (fallback).
        assert len(result.steps) == 1

    @pytest.mark.asyncio
    async def test_distributed_with_redis(
        self, fake_redis: Any,
    ) -> None:
        """distributed=True with Redis → dispatches to DistributedScheduler."""
        # Start a worker to consume tasks.
        scheduler = DistributedScheduler(fake_redis, poll_interval=0.05)
        worker = DistributedWorker(
            fake_redis, scheduler=scheduler,
            config=WorkerConfig(worker_id="w-engine", concurrency=2),
        )
        await worker.start()
        try:
            engine = Engine(redis_client=fake_redis)
            steps = [
                WorkflowStep(id="s1", type=StepType.AGENT, agent="claude", task="hello"),
                WorkflowStep(id="s2", type=StepType.TERMINAL, depends_on=["s1"]),
            ]
            result = await engine.run(steps, distributed=True)
            assert isinstance(result, EngineResult)
            assert len(result.steps) == 2
        finally:
            await worker.stop()

    @pytest.mark.asyncio
    async def test_distributed_fallback_on_error(
        self, fake_redis: Any,
    ) -> None:
        """When DistributedScheduler raises, engine falls back to single-process."""

        # Patch _get_distributed_scheduler to return a scheduler that raises.
        engine = Engine(redis_client=fake_redis)

        class FailingScheduler:
            async def run(self, nodes: Any, **kw: Any) -> Any:
                raise RuntimeError("simulated dispatch failure")

        with patch.object(engine, "_get_distributed_scheduler", return_value=FailingScheduler()):
            steps = [
                WorkflowStep(id="s1", type=StepType.AGENT, agent="claude", task="hello"),
            ]
            result = await engine.run(steps, distributed=True)
            # Should fall back to single-process.
            assert isinstance(result, EngineResult)
            assert len(result.steps) == 1


# ── CLI tests ─────────────────────────────────────────────────────

class TestWorkerCLI:
    """``maop worker start`` CLI argument parsing."""

    def test_cmd_worker_no_args_exits(self) -> None:
        """``maop worker`` with no subcommand exits with code 1."""
        from maop.cli import cmd_worker
        with pytest.raises(SystemExit) as exc_info:
            cmd_worker([])
        assert exc_info.value.code == 1

    def test_cmd_worker_unknown_subcommand(self) -> None:
        """``maop worker foo`` exits with code 1."""
        from maop.cli import cmd_worker
        with pytest.raises(SystemExit) as exc_info:
            cmd_worker(["foo"])
        assert exc_info.value.code == 1

    def test_cmd_worker_start_parses_args(self) -> None:
        """``maop worker start --redis-url=... --concurrency=4`` parses correctly."""
        from maop.cli import cmd_worker
        # Patch run_worker to capture args without actually starting.
        captured: dict[str, Any] = {}

        def mock_run_worker(
            redis_url: str = "",
            concurrency: int = 0,
            capabilities: Any = None,
            heartbeat_interval: float = 0.0,
        ) -> None:
            captured["redis_url"] = redis_url
            captured["concurrency"] = concurrency
            captured["capabilities"] = capabilities
            captured["heartbeat_interval"] = heartbeat_interval

        with patch("maop.worker.distributed_worker.run_worker", mock_run_worker):
            cmd_worker([
                "start",
                "--redis-url", "redis://example:6380/1",
                "--concurrency", "8",
                "--capabilities", "gpu,linux",
                "--heartbeat-interval", "3.0",
            ])
        assert captured["redis_url"] == "redis://example:6380/1"
        assert captured["concurrency"] == 8
        assert captured["capabilities"] == {"gpu", "linux"}
        assert captured["heartbeat_interval"] == 3.0

    def test_main_dispatches_worker(self) -> None:
        """``maop worker start`` is dispatched by main()."""
        from maop.cli import main
        with patch("sys.argv", ["maop", "worker", "start", "--concurrency", "2"]):  # noqa: SIM117
            with patch("maop.worker.distributed_worker.run_worker") as mock_rw:
                main()
                mock_rw.assert_called_once()
                call_kwargs = mock_rw.call_args
                assert call_kwargs.kwargs["concurrency"] == 2


# ── TaskAssignment dataclass tests ────────────────────────────────

class TestTaskAssignment:
    """TaskAssignment record."""

    def test_creation(self) -> None:
        assignment = TaskAssignment(
            node_id="n1", worker_id="w1", stream_msg_id="123-0",
        )
        assert assignment.node_id == "n1"
        assert assignment.worker_id == "w1"
        assert assignment.stream_msg_id == "123-0"
        assert assignment.dispatched_at > 0


# ── WorkerInfo dataclass tests ────────────────────────────────────

class TestWorkerInfo:
    """WorkerInfo snapshot."""

    def test_defaults(self) -> None:
        info = WorkerInfo(worker_id="w1")
        assert info.worker_id == "w1"
        assert info.concurrency == 4
        assert info.capabilities == set()
        assert info.status == WorkerStatus.ACTIVE
        assert info.in_flight == set()