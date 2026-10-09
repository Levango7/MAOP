"""Tests for dynamic orchestration — runtime fan-out (spawn directives).

A step executor signals dynamic orchestration by returning a
result object carrying a ``spawn`` attribute (a
:class:`~maop.engine_types.SpawnDirective`). After the step
succeeds, the engine merges the directive's steps into the
run graph — they execute in the same run, after their
spawner (``depends_on`` defaults to the spawner's id).

Covers:
  - Basic fan-out: spawned steps run in the same run,
    after the spawner, and surface in EngineResult.steps.
  - Ordering: explicit ``depends_on`` is honoured.
  - Validation: duplicate ids and unknown dependencies are
    dropped (run continues); the per-run spawn cap holds.
  - Chained fan-out: a spawned step may itself fan out.
  - Failure discipline: a failed step's directive is
    discarded; a failing spawned step fails the run.
  - Backward compatibility: executors without a ``spawn``
    attribute behave exactly as before.
  - Concurrent runs: spawn queues are isolated per run
    (keyed by trace_id).
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

import pytest

from maop.engine import _MAX_SPAWNED_STEPS, Engine
from maop.engine_types import (
    SpawnDirective,
    StepType,
    WorkflowStep,
)

# ── Test doubles ─────────────────────────────────────────


def _fan_executor(
    call_order: list[str],
    spawn_map: dict[tuple[str, str] | str, SpawnDirective],
    fail_map: dict[str, str] | None = None,
):
    """Executor that records call order and optional spawns.

    ``spawn_map`` keys are ``(trace_id, step_id)`` tuples or
    plain step ids; ``fail_map`` maps step ids to error
    strings (the step fails instead of succeeding).
    """

    async def executor(
        step: WorkflowStep,
        context: dict[str, Any],
        workdir: str,
        trace_id: str,
    ) -> Any:
        call_order.append(step.id)
        error = (fail_map or {}).get(step.id)
        result: Any = SimpleNamespace(
            output="" if error else f"out:{step.id}",
            exit_code=1 if error else 0,
        )
        if error:
            result.error = error
        directive = spawn_map.get(
            (trace_id, step.id), spawn_map.get(step.id),
        )
        if directive is not None:
            result.spawn = directive
        return result

    return executor


def _step(step_id: str, depends_on: list[str] | None = None) -> WorkflowStep:
    return WorkflowStep(
        id=step_id,
        type=StepType.AGENT,
        agent="test-agent",
        task=f"task-{step_id}",
        depends_on=depends_on or [],
    )


# ── Fan-out basics ───────────────────────────────────────


class TestFanOut:
    @pytest.mark.asyncio
    async def test_spawned_steps_run_in_same_run(self) -> None:
        """Spawned steps execute after the spawner, in-run."""
        call_order: list[str] = []
        directive = SpawnDirective(
            steps=[_step("fan_0"), _step("fan_1")],
        )
        engine = Engine(
            step_executor=_fan_executor(
                call_order, {"gen": directive},
            ),
        )
        result = await engine.run([_step("gen")])
        assert result.success is True
        assert call_order == ["gen", "fan_0", "fan_1"]
        assert [r.id for r in result.steps] == ["gen", "fan_0", "fan_1"]
        # Spawned step outputs land in the run context.
        assert result.context["fan_0"] == "out:fan_0"

    @pytest.mark.asyncio
    async def test_spawn_depends_on_defaults_to_spawner(
        self,
    ) -> None:
        """Without explicit depends_on, spawned steps wait for
        the spawner — they never join the spawner's layer."""
        call_order: list[str] = []
        # The spawner and a second step run in parallel; the
        # spawned step must come after the spawner only.
        directive = SpawnDirective(steps=[_step("fan")])
        engine = Engine(
            step_executor=_fan_executor(
                call_order, {"gen": directive},
            ),
        )
        result = await engine.run(
            [_step("gen"), _step("peer")],
        )
        assert result.success is True
        # gen and peer are one layer (any order); fan is next.
        assert set(call_order[:2]) == {"gen", "peer"}
        assert call_order[2] == "fan"

    @pytest.mark.asyncio
    async def test_explicit_depends_on_is_honoured(self) -> None:
        """A directive may target any step that exists in the
        run — here the spawned step waits for a later step."""
        call_order: list[str] = []
        directive = SpawnDirective(
            steps=[_step("fan")],
            depends_on=["b"],
        )
        engine = Engine(
            step_executor=_fan_executor(
                call_order, {"a": directive},
            ),
        )
        result = await engine.run(
            [_step("a"), _step("b", depends_on=["a"])],
        )
        assert result.success is True
        assert call_order == ["a", "b", "fan"]

    @pytest.mark.asyncio
    async def test_chained_fan_out(self) -> None:
        """A spawned step may itself fan out further steps."""
        call_order: list[str] = []
        spawn_map = {
            "gen": SpawnDirective(steps=[_step("mid")]),
            "mid": SpawnDirective(steps=[_step("leaf")]),
        }
        engine = Engine(
            step_executor=_fan_executor(call_order, spawn_map),
        )
        result = await engine.run([_step("gen")])
        assert result.success is True
        assert call_order == ["gen", "mid", "leaf"]


# ── Validation ───────────────────────────────────────────


class TestSpawnValidation:
    @pytest.mark.asyncio
    async def test_duplicate_step_id_dropped(self) -> None:
        """A spawned step whose id already exists is dropped
        with an error log; the run continues."""
        call_order: list[str] = []
        directive = SpawnDirective(steps=[_step("gen")])
        engine = Engine(
            step_executor=_fan_executor(
                call_order, {"gen": directive},
            ),
        )
        result = await engine.run([_step("gen")])
        assert result.success is True
        assert [r.id for r in result.steps] == ["gen"]

    @pytest.mark.asyncio
    async def test_unknown_dependency_dropped(self) -> None:
        """A directive referencing a non-existent step is
        dropped; the run continues."""
        call_order: list[str] = []
        directive = SpawnDirective(
            steps=[_step("fan")],
            depends_on=["ghost"],
        )
        engine = Engine(
            step_executor=_fan_executor(
                call_order, {"gen": directive},
            ),
        )
        result = await engine.run([_step("gen")])
        assert result.success is True
        assert [r.id for r in result.steps] == ["gen"]

    @pytest.mark.asyncio
    async def test_spawn_cap_enforced(self) -> None:
        """The per-run spawned-step cap holds; excess steps
        are dropped."""
        call_order: list[str] = []
        many = [_step(f"fan_{i}") for i in range(_MAX_SPAWNED_STEPS + 10)]
        directive = SpawnDirective(steps=many)
        engine = Engine(
            step_executor=_fan_executor(
                call_order, {"gen": directive},
            ),
        )
        result = await engine.run([_step("gen")])
        assert result.success is True
        assert len(result.steps) == 1 + _MAX_SPAWNED_STEPS


# ── Failure discipline ───────────────────────────────────


class TestFailureDiscipline:
    @pytest.mark.asyncio
    async def test_failed_step_discards_directive(self) -> None:
        """A failed step's spawn directive is not delivered."""
        call_order: list[str] = []
        directive = SpawnDirective(steps=[_step("fan")])
        engine = Engine(
            step_executor=_fan_executor(
                call_order,
                {"gen": directive},
                fail_map={"gen": "boom"},
            ),
        )
        result = await engine.run([_step("gen")])
        assert result.success is False
        assert [r.id for r in result.steps] == ["gen"]

    @pytest.mark.asyncio
    async def test_failing_spawned_step_fails_run(self) -> None:
        """A spawned step that fails marks the run failed."""
        call_order: list[str] = []
        directive = SpawnDirective(steps=[_step("fan")])
        engine = Engine(
            step_executor=_fan_executor(
                call_order,
                {"gen": directive},
                fail_map={"fan": "spawn failed"},
            ),
        )
        result = await engine.run([_step("gen")])
        assert result.success is False
        assert [r.id for r in result.steps] == ["gen", "fan"]
        fan = next(r for r in result.steps if r.id == "fan")
        assert fan.status.value == "failed"


# ── Backward compatibility & concurrency ─────────────────


class TestBackwardCompat:
    @pytest.mark.asyncio
    async def test_executor_without_spawn_attribute(self) -> None:
        """Executors that never set ``spawn`` behave as before."""
        call_order: list[str] = []
        engine = Engine(
            step_executor=_fan_executor(call_order, {}),
        )
        steps = [
            _step("s1"),
            _step("s2", depends_on=["s1"]),
            _step("s3", depends_on=["s1"]),
        ]
        result = await engine.run(steps)
        assert result.success is True
        assert call_order == ["s1", "s2", "s3"]
        assert [r.id for r in result.steps] == ["s1", "s2", "s3"]

    @pytest.mark.asyncio
    async def test_cyclic_graph_still_raises(self) -> None:
        """The cycle error from _topological_sort is preserved."""
        engine = Engine(step_executor=_fan_executor([], {}))
        with pytest.raises(ValueError, match="Cycle detected"):
            await engine.run(
                [
                    _step("x", depends_on=["y"]),
                    _step("y", depends_on=["x"]),
                ],
            )

    @pytest.mark.asyncio
    async def test_concurrent_runs_are_isolated(self) -> None:
        """Spawn queues are keyed by trace_id — concurrent runs
        only see their own directives."""
        call_order: list[str] = []
        spawn_map: dict[tuple[str, str], SpawnDirective] = {}

        async def run_one(tag: str) -> list[str]:
            trace_id = f"trace-{tag}"
            spawn_map[(trace_id, "gen")] = SpawnDirective(
                steps=[_step(f"fan-{tag}")],
            )
            engine = Engine(
                step_executor=_fan_executor(call_order, spawn_map),
            )
            result = await engine.run([_step("gen")], trace_id=trace_id)
            assert result.success is True
            return [r.id for r in result.steps]

        ids_a, ids_b = await asyncio.gather(
            run_one("a"), run_one("b"),
        )
        assert ids_a == ["gen", "fan-a"]
        assert ids_b == ["gen", "fan-b"]
