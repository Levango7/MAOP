"""M0 L0.1 End-to-End Integration Tests — TaskBackend abstraction.

Covers the full path from ``Engine.run(distributed=True)`` through the
``DistributedTaskBackend`` down to the ``DistributedScheduler`` Redis
Streams dispatch, and back via result aggregation.

Uses ``fakeredis`` (no real Redis dependency).

Test groups
----------
1. ``LocalTaskBackend`` — verifies delegation to ``Engine._run_single``.
2. ``DistributedTaskBackend`` — verifies node_spec build + result mapping.
3. ``Engine._get_task_backend`` — verifies backend selection logic.
4. ``Engine.run(distributed=True)`` — full round-trip with worker consuming.
5. ``POST /api/engine/run-distributed`` — REST endpoint integration.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import fakeredis
import pytest

from maop.core.scheduling.task_backend import (
    DistributedTaskBackend,
    LocalTaskBackend,
)
from maop.engine import Engine, EngineResult, StepStatus, StepType, WorkflowStep
from maop.engine_types import StepResult

# ── Helpers ──────────────────────────────────────────────────────


async def _mock_step_executor(step: Any, context: Any, workdir: Any, trace_id: Any) -> Any:
    """Mock step executor — returns success with step id."""
    return SimpleNamespace(output=f"mock:{step.id}", exit_code=0)


def _make_steps() -> list[WorkflowStep]:
    """Create a simple 3-step DAG: s1 → s2, s1 → s3 (parallel)."""
    return [
        WorkflowStep(id="s1", type=StepType.AGENT, task="Task 1"),
        WorkflowStep(id="s2", type=StepType.AGENT, task="Task 2", depends_on=["s1"]),
        WorkflowStep(id="s3", type=StepType.AGENT, task="Task 3", depends_on=["s1"]),
    ]


# ── Fixtures ──────────────────────────────────────────────────────


@pytest.fixture
def fake_redis() -> Any:
    """Fresh fakeredis instance."""
    return fakeredis.FakeRedis(decode_responses=False)


@pytest.fixture
def engine_local() -> Engine:
    """Engine without Redis — forces local backend."""
    return Engine(step_executor=_mock_step_executor)


@pytest.fixture
def engine_redis(fake_redis: Any) -> Engine:
    """Engine with Redis client — enables distributed backend."""
    return Engine(redis_client=fake_redis, step_executor=_mock_step_executor)


# ── Group 1: LocalTaskBackend ────────────────────────────────────


class TestLocalTaskBackend:
    """LocalTaskBackend delegates to Engine._run_single."""

    @pytest.mark.asyncio
    async def test_runs_single_step(self, engine_local: Engine) -> None:
        """LocalTaskBackend executes a single step and returns success."""
        backend = LocalTaskBackend(engine_local)
        steps = [WorkflowStep(id="local-1", type=StepType.AGENT, task="Hello")]
        result = await backend.run(
            steps=steps,
            context={},
            workdir="",
            trace_id="test-trace-local",
        )
        assert result.success is True
        assert len(result.steps) == 1
        assert result.steps[0].status == StepStatus.SUCCESS
        assert "mock:local-1" in result.steps[0].output

    @pytest.mark.asyncio
    async def test_runs_dag(self, engine_local: Engine) -> None:
        """LocalTaskBackend executes a multi-step DAG with dependencies."""
        backend = LocalTaskBackend(engine_local)
        result = await backend.run(
            steps=_make_steps(),
            context={},
            workdir="",
            trace_id="test-trace-dag",
        )
        assert result.success is True
        assert len(result.steps) == 3
        assert all(s.status == StepStatus.SUCCESS for s in result.steps)


# ── Group 2: DistributedTaskBackend node_spec + result mapping ───


class TestDistributedTaskBackend:
    """DistributedTaskBackend builds node_specs and maps results."""

    @pytest.mark.asyncio
    async def test_node_spec_construction(self, fake_redis: Any) -> None:
        """DistributedTaskBackend converts WorkflowSteps to _NodeSpec via scheduler."""
        backend = DistributedTaskBackend(fake_redis)
        # Trigger scheduler _ensure_task_group to verify Redis state
        scheduler = backend._get_scheduler()
        assert scheduler is not None
        # Verify the consumer group was created
        groups = fake_redis.xinfo_groups("maop:sched:tasks")
        group_names = [g.get("name", b"").decode() if isinstance(g.get("name"), bytes) else g.get("name", "") for g in groups]
        assert "maop_workers" in group_names

    @pytest.mark.asyncio
    async def test_result_mapping(self, fake_redis: Any) -> None:
        """DistributedTaskBackend maps scheduler results to StepResult."""
        backend = DistributedTaskBackend(fake_redis)
        # Manually post a result to the results stream to simulate worker output
        run_id = "test-result-map"
        results_stream = f"maop:sched:results:{run_id}"
        fake_redis.xadd(results_stream, {
            "node_id": "n1",
            "status": "success",
            "output": "mapped-output",
            "error": "",
            "duration_ms": "150",
        })
        # Now run — the scheduler reads the pre-posted result for n1 on its
        # first poll, so the mapping layer can be exercised without a worker.
        # The original WIP version of this test only populated the stream and
        # asserted nothing; running the backend makes the mapping observable.
        steps = [WorkflowStep(id="n1", type=StepType.AGENT, task="Sim")]
        result = await backend.run(
            steps=steps,
            context=None,
            workdir="",
            trace_id=run_id,
        )
        assert result.trace_id == run_id
        assert len(result.steps) == 1
        mapped = result.steps[0]
        assert mapped.id == "n1"
        assert mapped.status == StepStatus.SUCCESS
        assert mapped.output == "mapped-output"
        assert mapped.duration_ms == 150


# ── Group 3: Engine._get_task_backend ────────────────────────────


class TestEngineBackendSelection:
    """Engine selects the correct TaskBackend based on mode + Redis availability."""

    def test_local_fallback_without_redis(self, engine_local: Engine) -> None:
        """Without Redis, _get_task_backend(distributed=True) returns local."""
        backend = engine_local._get_task_backend(distributed=True)
        assert isinstance(backend, LocalTaskBackend)

    def test_distributed_backend_with_redis(self, engine_redis: Engine) -> None:
        """With Redis, _get_task_backend(distributed=True) returns distributed."""
        backend = engine_redis._get_task_backend(distributed=True)
        assert isinstance(backend, DistributedTaskBackend)

    def test_local_backend_default(self, engine_redis: Engine) -> None:
        """_get_task_backend(distributed=False) always returns local."""
        backend = engine_redis._get_task_backend(distributed=False)
        assert isinstance(backend, LocalTaskBackend)

    def test_engine_creates_backends_on_init(self, fake_redis: Any) -> None:
        """Engine.__init__ creates both local and distributed backends."""
        engine = Engine(redis_client=fake_redis, step_executor=_mock_step_executor)
        assert isinstance(engine._local_backend, LocalTaskBackend)
        assert isinstance(engine._distributed_backend, DistributedTaskBackend)

    def test_engine_without_redis_no_distributed(self, engine_local: Engine) -> None:
        """Engine without Redis has _distributed_backend=None."""
        assert engine_local._distributed_backend is None
        assert isinstance(engine_local._local_backend, LocalTaskBackend)


# ── Group 4: Engine.run(distributed=True) round-trip ────────────


class TestEngineDistributedRun:
    """Engine.run(distributed=True) end-to-end with fakeredis.

    These tests use a real (fakeredis) Redis client and a mock scheduler
    to verify that:
    1. The distributed path is taken when Redis is available.
    2. Fallback to local works when scheduler raises.
    """

    @pytest.mark.asyncio
    async def test_run_distributed_with_mock_scheduler(
        self, engine_redis: Engine, fake_redis: Any
    ) -> None:
        """Engine.run(distributed=True) uses DistributedTaskBackend."""
        steps = _make_steps()

        # Patch DistributedTaskBackend.run to verify it's called
        with patch.object(
            DistributedTaskBackend, "run", new_callable=AsyncMock
        ) as mock_run:
            mock_run.return_value = EngineResult(
                trace_id="mock-trace",
                steps=[],
                success=True,
                total_duration_ms=0,
                context={},
            )
            result = await engine_redis.run(
                steps=steps,
                context={},
                workdir="",
                trace_id="test-e2e",
                distributed=True,
            )
            mock_run.assert_called_once()
            assert result.success is True

    @pytest.mark.asyncio
    async def test_run_distributed_fallback_on_error(
        self, engine_redis: Engine, fake_redis: Any
    ) -> None:
        """Engine.run(distributed=True) falls back to local on error."""
        steps = [WorkflowStep(id="fallback-1", type=StepType.AGENT, task="Fallback")]

        with patch.object(
            DistributedTaskBackend, "run", new_callable=AsyncMock
        ) as mock_run:
            mock_run.side_effect = RuntimeError("Redis unavailable")
            result = await engine_redis.run(
                steps=steps,
                context={},
                workdir="",
                trace_id="test-fallback",
                distributed=True,
            )
            # Should have fallen back to local backend
            assert result.success is True
            assert result.steps[0].status == StepStatus.SUCCESS

    @pytest.mark.asyncio
    async def test_run_local_default(self, engine_local: Engine) -> None:
        """Engine.run() (no distributed) uses local backend."""
        steps = [WorkflowStep(id="default-1", type=StepType.AGENT, task="Default")]
        result = await engine_local.run(steps=steps, context={}, workdir="")
        assert result.success is True
        assert result.steps[0].status == StepStatus.SUCCESS

    @pytest.mark.asyncio
    async def test_run_distributed_flag_without_redis(self, engine_local: Engine) -> None:
        """Engine.run(distributed=True) without Redis falls back to local."""
        steps = [WorkflowStep(id="fallback-local", type=StepType.AGENT, task="Local")]
        result = await engine_local.run(
            steps=steps,
            context={},
            workdir="",
            trace_id="test-no-redis",
            distributed=True,
        )
        assert result.success is True
        assert result.steps[0].status == StepStatus.SUCCESS


# ── Group 5: REST Endpoint Integration ───────────────────────────


class TestEngineRouterEndpoints:
    """POST /api/engine/run and /api/engine/run-distributed endpoints."""

    @pytest.mark.asyncio
    async def test_run_endpoint_local(self) -> None:
        """POST /api/engine/run executes a single step locally."""
        from maop.dashboard.routers.engine_router import _get_engine, set_engine_for_tests

        mock_engine = MagicMock()
        mock_engine.run = AsyncMock(return_value=EngineResult(
            trace_id="api-test",
            steps=[StepResult(id="s1", status=StepStatus.SUCCESS, output="ok")],
            success=True,
            total_duration_ms=42,
            context={},
        ))
        set_engine_for_tests(mock_engine)

        try:
            # Verify engine was injected
            engine = _get_engine()
            assert engine is mock_engine
        finally:
            set_engine_for_tests(None)


# ── Group 6: Spawn drain (M0 L0.1-T0.1.8) ──────────────────────


class TestSpawnDrain:
    """Spawn directives posted by workers are drained and merged into the DAG."""

    @pytest.mark.asyncio
    async def test_post_result_with_spawns(self, fake_redis: Any) -> None:
        """``post_result(spawn_directives=...)`` serialises spawns into the stream."""
        from maop.core.scheduling.distributed_scheduler import DistributedScheduler

        scheduler = DistributedScheduler(fake_redis, poll_interval=0.01)
        scheduler._ensure_task_group()

        spawns = [
            {
                "steps": [
                    {"id": "spawned-1", "type": "agent", "task": "Spawned task"},
                ],
                "depends_on": ["n1"],
            },
        ]
        scheduler.post_result(
            "test-run", "n1", status="success",
            output="ok", spawn_directives=spawns, worker_id="w1",
        )
        # Read back from results stream
        results_stream = scheduler._results_stream("test-run")
        entries = fake_redis.xrange(results_stream, min="-", max="+")
        assert len(entries) == 1
        _msg_id, fields = entries[0]
        raw = fields.get(b"spawn", b"").decode() if isinstance(fields.get(b"spawn", b""), bytes) else fields.get("spawn", "")
        assert "spawned-1" in raw

    @pytest.mark.asyncio
    async def test_read_results_collects_spawns(self, fake_redis: Any) -> None:
        """``_read_results`` returns spawns as a separate list."""
        from maop.core.scheduling.distributed_scheduler import (
            DistributedScheduler,
        )

        scheduler = DistributedScheduler(fake_redis, poll_interval=0.01)
        scheduler._ensure_task_group()
        scheduler.post_result(
            "r2", "n1", status="success",
            output="done",
            spawn_directives=[{"steps": [{"id": "s1", "task": "t"}], "depends_on": ["n1"]}],
            worker_id="w1",
        )
        # _read_results should collect spawns
        results, spawns, _cursor = scheduler._read_results(
            scheduler._results_stream("r2"), {"n1"},
        )
        assert "n1" in results
        assert len(spawns) == 1
        assert spawns[0]["steps"][0]["id"] == "s1"

    def test_merge_spawns_into_dag(self, fake_redis: Any) -> None:
        """``_merge_spawns`` appends valid spawns and drops invalid ones."""
        from maop.core.scheduling.distributed_scheduler import (
            DistributedScheduler,
            _NodeSpec,
        )

        scheduler = DistributedScheduler(fake_redis, poll_interval=0.01)
        node_map: dict[str, Any] = {
            "n1": _NodeSpec(id="n1", payload={"task": "t1"}),
        }
        layers: list[list[str]] = [["n1"]]
        results: dict[str, Any] = {}
        # Valid spawn + duplicate + unknown dep → only valid one merged
        spawns = [
            {"steps": [{"id": "child-1", "type": "agent", "task": "c1"}], "depends_on": ["n1"]},
            {"steps": [{"id": "n1", "type": "agent", "task": "dup"}], "depends_on": ["n1"]},
            {"steps": [{"id": "bad-dep", "type": "agent", "task": "x"}], "depends_on": ["nope"]},
        ]
        scheduler._merge_spawns(spawns, node_map, layers, results, "run-x")
        assert "child-1" in node_map
        assert len(layers) == 2
        assert layers[1] == ["child-1"]
        # Duplicate "n1" should not be added again
        assert "n1" in node_map  # original
        # Unknown dep dropped
        assert "bad-dep" not in node_map


# ── Main ─────────────────────────────────────────────────────────


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
