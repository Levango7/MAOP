"""Tests for MAOP.engine — Unified workflow engine."""

from __future__ import annotations

import pytest

from maop.engine import (
    safe_eval,
    StepType,
    StepStatus,
    WorkflowStep,
    StepResult,
    EngineResult,
    Engine,
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
