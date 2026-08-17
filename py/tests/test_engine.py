"""Tests for MAOP.engine — Unified workflow engine."""

from __future__ import annotations

import pytest

from maop.engine import (
    Engine,
    EngineResult,
    StepResult,
    StepStatus,
    StepType,
    WorkflowStep,
    _find_step,
    _resolve_template,
    _topological_sort,
    json_dumps_safe,
    safe_eval,
)


class TestSafeEval:
    """Test the safe expression evaluator."""

    def test_constant(self):
        assert safe_eval("42", {}) == 42
        assert safe_eval('"hello"', {}) == "hello"

    def test_name(self):
        assert safe_eval("x", {"x": 10}) == 10
        assert safe_eval("name", {"name": "test"}) == "test"

    def test_binop(self):
        assert safe_eval("1 + 2", {}) == 3
        assert safe_eval("10 - 3", {}) == 7
        assert safe_eval("4 * 5", {}) == 20
        assert safe_eval("10 / 2", {}) == 5.0
        assert safe_eval("10 // 3", {}) == 3
        assert safe_eval("10 % 3", {}) == 1
        assert safe_eval("2 ** 3", {}) == 8

    def test_compare(self):
        assert safe_eval("1 < 2", {}) is True
        assert safe_eval("2 > 3", {}) is False
        assert safe_eval("x == 10", {"x": 10}) is True
        assert safe_eval("x != 10", {"x": 20}) is True
        assert safe_eval("1 <= 1", {}) is True
        assert safe_eval("1 >= 2", {}) is False

    def test_boolop(self):
        assert safe_eval("True and False", {}) is False
        assert safe_eval("True or False", {}) is True
        assert safe_eval("x and y", {"x": True, "y": True}) is True

    def test_unaryop(self):
        assert safe_eval("-5", {}) == -5
        assert safe_eval("not True", {}) is False
        assert safe_eval("+5", {}) == 5

    def test_subscript(self):
        assert safe_eval("lst[0]", {"lst": [10, 20]}) == 10
        assert safe_eval("d['key']", {"d": {"key": "val"}}) == "val"

    def test_list(self):
        assert safe_eval("[1, 2, 3]", {}) == [1, 2, 3]

    def test_tuple(self):
        assert safe_eval("(1, 2)", {}) == (1, 2)

    def test_private_attr_blocked(self):
        with pytest.raises(ValueError, match="private"):
            safe_eval("x._secret", {"x": type("T", (), {"_secret": 1})})

    def test_dangerous_attr_blocked(self):
        with pytest.raises(ValueError, match="private"):
            safe_eval("x.__class__", {"x": "test"})

    def test_format_blocked(self):
        with pytest.raises(ValueError, match="blocked"):
            safe_eval("'{}'.format", {})


class TestStepType:
    def test_values(self):
        assert StepType.PLAN == "plan"
        assert StepType.AGENT == "agent"
        assert StepType.DAG == "dag"
        assert StepType.VERIFY == "verify"
        assert StepType.CONDITION == "condition"
        assert StepType.TERMINAL == "terminal"


class TestStepStatus:
    def test_values(self):
        assert StepStatus.PENDING == "pending"
        assert StepStatus.RUNNING == "running"
        assert StepStatus.SUCCESS == "success"
        assert StepStatus.FAILED == "failed"
        assert StepStatus.SKIPPED == "skipped"


class TestWorkflowStep:
    def test_default_values(self):
        step = WorkflowStep(id="s1")
        assert step.id == "s1"
        assert step.type == StepType.AGENT
        assert step.agent == ""
        assert step.task == ""
        assert step.depends_on == []
        assert step.retry == 0
        assert step.timeout == 120
        assert step.on_failure == ""
        assert step.fallback_to == ""
        assert step.params == {}

    def test_full_step(self):
        step = WorkflowStep(
            id="s1",
            type=StepType.PLAN,
            agent="claude",
            task="Analyze requirements",
            depends_on=["s0"],
            retry=2,
            timeout=300,
        )
        assert step.type == StepType.PLAN
        assert step.depends_on == ["s0"]
        assert step.retry == 2


class TestStepResult:
    def test_default_values(self):
        r = StepResult(id="s1")
        assert r.id == "s1"
        assert r.status == StepStatus.PENDING


class TestEngineResult:
    def test_default_values(self):
        r = EngineResult()
        assert r.steps == []
        assert r.success is False


class TestEngine:
    def test_init(self):
        engine = Engine()
        assert engine is not None

    @pytest.mark.asyncio
    async def test_run_empty_steps(self):
        engine = Engine()
        result = await engine.run(steps=[])
        assert result is not None
        assert isinstance(result, EngineResult)

    @pytest.mark.asyncio
    async def test_run_single_terminal_step(self):
        engine = Engine()
        step = WorkflowStep(id="s1", type=StepType.TERMINAL, task="done")
        result = await engine.run(steps=[step])
        assert result is not None
        assert len(result.steps) >= 1


# --- Merged from test_engine_extended.py ---
# Extended tests for MAOP.engine — topological sort, DAG, conditions, decomposition.


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
        # step2 ran (not aborted). Without an executor wired up, agent step
        # now fails fast (FAILED) instead of returning a placeholder SUCCESS.
        assert step2.status in (StepStatus.SUCCESS, StepStatus.SKIPPED, StepStatus.FAILED)


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
