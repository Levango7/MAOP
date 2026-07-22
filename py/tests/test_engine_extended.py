"""Extended tests for MAOP.engine — topological sort, DAG, conditions, decomposition."""

from __future__ import annotations

import pytest

from maop.engine import (
    StepType, StepStatus, WorkflowStep, Engine, _topological_sort, _resolve_template, _find_step, json_dumps_safe,
)


class TestTopologicalSort:
    def test_single_step(self):
        steps = [WorkflowStep(id="s1", type=StepType.AGENT, task="do something")]
        layers = _topological_sort(steps)
        assert len(layers) == 1
        assert len(layers[0]) == 1

    def test_linear_chain(self):
        steps = [
            WorkflowStep(id="s1", type=StepType.AGENT, task="first"),
            WorkflowStep(id="s2", type=StepType.AGENT, task="second", depends_on=["s1"]),
            WorkflowStep(id="s3", type=StepType.AGENT, task="third", depends_on=["s2"]),
        ]
        layers = _topological_sort(steps)
        assert len(layers) == 3
        assert layers[0][0].id == "s1"
        assert layers[1][0].id == "s2"
        assert layers[2][0].id == "s3"

    def test_parallel_independent(self):
        steps = [
            WorkflowStep(id="s1", type=StepType.AGENT, task="a"),
            WorkflowStep(id="s2", type=StepType.AGENT, task="b"),
            WorkflowStep(id="s3", type=StepType.AGENT, task="c", depends_on=["s1", "s2"]),
        ]
        layers = _topological_sort(steps)
        assert len(layers) == 2
        assert len(layers[0]) == 2
        assert len(layers[1]) == 1

    def test_diamond_dependency(self):
        steps = [
            WorkflowStep(id="s1", type=StepType.AGENT, task="root"),
            WorkflowStep(id="s2", type=StepType.AGENT, task="left", depends_on=["s1"]),
            WorkflowStep(id="s3", type=StepType.AGENT, task="right", depends_on=["s1"]),
            WorkflowStep(id="s4", type=StepType.AGENT, task="merge", depends_on=["s2", "s3"]),
        ]
        layers = _topological_sort(steps)
        assert len(layers) == 3
        assert len(layers[0]) == 1
        assert len(layers[1]) == 2
        assert len(layers[2]) == 1

    def test_empty_steps(self):
        layers = _topological_sort([])
        assert layers == []


class TestResolveTemplate:
    def test_simple_replacement(self):
        result = _resolve_template("Hello {{ name }}", {"name": "World"})
        assert result == "Hello World"

    def test_multiple_replacements(self):
        result = _resolve_template("{{ a }} and {{ b }}", {"a": "X", "b": "Y"})
        assert result == "X and Y"

    def test_no_placeholders(self):
        result = _resolve_template("no placeholders", {"key": "val"})
        assert result == "no placeholders"

    def test_empty_template(self):
        result = _resolve_template("", {"key": "val"})
        assert result == ""

    def test_missing_key_kept_as_is(self):
        result = _resolve_template("{{ missing }}", {"other": "val"})
        assert result == "{{ missing }}"


class TestEngineConditionStep:
    @pytest.mark.asyncio
    async def test_condition_true(self):
        engine = Engine()
        step = WorkflowStep(
            id="c1", type=StepType.CONDITION,
            params={"expr": "x > 5"},
        )
        result = await engine.run(steps=[step], context={"x": 10})
        assert result.steps[0].status == StepStatus.SUCCESS

    @pytest.mark.asyncio
    async def test_condition_false(self):
        engine = Engine()
        step = WorkflowStep(
            id="c1", type=StepType.CONDITION,
            params={"expr": "x > 100"},
        )
        result = await engine.run(steps=[step], context={"x": 10})
        assert result.steps[0].status == StepStatus.SKIPPED

    @pytest.mark.asyncio
    async def test_condition_invalid_expr(self):
        engine = Engine()
        step = WorkflowStep(
            id="c1", type=StepType.CONDITION,
            params={"expr": "invalid!!!syntax"},
        )
        result = await engine.run(steps=[step])
        assert result.steps[0].status == StepStatus.SKIPPED


class TestEngineVerifyStep:
    @pytest.mark.asyncio
    async def test_verify_upstream_success(self):
        engine = Engine()
        steps = [
            WorkflowStep(id="s1", type=StepType.TERMINAL, task="done"),
            WorkflowStep(id="v1", type=StepType.VERIFY, depends_on=["s1"]),
        ]
        result = await engine.run(steps=steps)
        verify_step = next(r for r in result.steps if r.id == "v1")
        assert verify_step.status == StepStatus.SUCCESS

    @pytest.mark.asyncio
    async def test_verify_with_failed_upstream(self):
        engine = Engine()
        steps = [
            WorkflowStep(id="s1", type=StepType.AGENT, task="fail me", on_failure="skip"),
            WorkflowStep(id="v1", type=StepType.VERIFY, depends_on=["s1"]),
        ]
        result = await engine.run(steps=steps)
        verify_step = next(r for r in result.steps if r.id == "v1")
        assert verify_step.status in (StepStatus.SUCCESS, StepStatus.FAILED)


class TestEngineAbortOnFailure:
    @pytest.mark.asyncio
    async def test_abort_skips_remaining(self):
        engine = Engine()
        steps = [
            WorkflowStep(id="s1", type=StepType.TERMINAL, task="done"),
            WorkflowStep(id="s2", type=StepType.AGENT, task="should run", depends_on=["s1"]),
        ]
        result = await engine.run(steps=steps)
        step2 = next((r for r in result.steps if r.id == "s2"), None)
        assert step2 is not None
        assert step2.status in (StepStatus.SUCCESS, StepStatus.SKIPPED)


class TestEngineDecomposition:
    def test_semicolon_decomposition(self):
        engine = Engine()
        step = WorkflowStep(id="p1", type=StepType.PLAN, task="do A; do B; do C")
        substeps = engine._decompose_task("do A; do B; do C", step)
        assert len(substeps) == 3

    def test_bullet_decomposition(self):
        engine = Engine()
        task = "- Write tests\n- Fix bugs\n- Deploy"
        step = WorkflowStep(id="p1", type=StepType.PLAN, task=task)
        substeps = engine._decompose_task(task, step)
        assert len(substeps) == 3

    def test_atomic_task_no_decomposition(self):
        engine = Engine()
        step = WorkflowStep(id="p1", type=StepType.PLAN, task="simple task")
        substeps = engine._decompose_task("simple task", step)
        assert len(substeps) == 0


class TestFindStep:
    def test_find_existing(self):
        steps = [WorkflowStep(id="s1"), WorkflowStep(id="s2")]
        result = _find_step(steps, "s2")
        assert result.id == "s2"

    def test_find_missing_returns_fallback(self):
        steps = [WorkflowStep(id="s1")]
        result = _find_step(steps, "nonexistent")
        assert result.id == "nonexistent"


class TestJsonDumpsSafe:
    def test_normal_dict(self):
        result = json_dumps_safe({"key": "value"})
        assert '"key"' in result

    def test_non_serializable(self):
        result = json_dumps_safe({"func": lambda x: x})
        assert result is not None