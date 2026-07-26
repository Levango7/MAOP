"""Tests for regression and simulation testing framework."""
from __future__ import annotations

import pytest

from maop.core.regression import (
    PersonaConfig,
    PersonaSimulator,
    RegressionReport,
    RegressionTestRunner,
    SimulationResult,
    TestCase,
    TestResult,
)


class TestTestCase:
    def test_defaults(self):
        tc = TestCase(prompt="fix bug")
        assert tc.prompt == "fix bug"
        assert tc.expected_keywords == []
        assert tc.expected_exit_code == 0

    def test_with_values(self):
        tc = TestCase(
            name="code-review", prompt="review code",
            expected_keywords=["suggestion", "issue"],
            expected_exit_code=0, agent="reviewer",
        )
        assert tc.agent == "reviewer"
        assert len(tc.expected_keywords) == 2


class TestTestResult:
    def test_defaults(self):
        r = TestResult()
        assert r.passed is False
        assert r.keyword_matches == []

    def test_passed(self):
        r = TestResult(passed=True, actual_exit_code=0, keyword_matches=["ok"])
        assert r.passed is True


class TestRegressionReport:
    def test_defaults(self):
        r = RegressionReport()
        assert r.total_tests == 0
        assert r.regressions == []
        assert r.improvements == []


class TestPersonaConfig:
    def test_defaults(self):
        p = PersonaConfig(name="tester")
        assert p.expertise == "intermediate"
        assert p.goals == []


class TestPersonaSimulator:
    def test_junior_dev_persona(self):
        sim = PersonaSimulator("junior_dev", goal="fix null pointer")
        assert sim.persona.name == "junior_dev"
        assert sim.persona.expertise == "beginner"

    def test_senior_dev_persona(self):
        sim = PersonaSimulator("senior_dev")
        assert sim.persona.expertise == "expert"

    def test_pm_persona(self):
        sim = PersonaSimulator("pm")
        assert sim.persona.role == "Product manager"

    def test_qa_persona(self):
        sim = PersonaSimulator("qa_engineer")
        assert sim.persona.expertise == "testing"

    def test_custom_persona(self):
        p = PersonaConfig(name="custom", expertise="advanced", goals=["automate"])
        sim = PersonaSimulator(persona=p)
        assert sim.persona.name == "custom"

    def test_generate_input_first_turn(self):
        sim = PersonaSimulator("junior_dev", goal="fix the bug")
        inp = sim.generate_input(1)
        assert "fix the bug" in inp

    def test_generate_input_followup_beginner(self):
        sim = PersonaSimulator("junior_dev")
        inp = sim.generate_input(2, "Here is the solution")
        assert "explain" in inp.lower() or "understand" in inp.lower()

    def test_generate_input_followup_expert(self):
        sim = PersonaSimulator("senior_dev")
        inp = sim.generate_input(2, "Done")
        assert "edge case" in inp.lower()

    def test_evaluate_satisfaction_high(self):
        sim = PersonaSimulator()
        score = sim.evaluate_satisfaction("The task is complete and success! Done!")
        assert score >= 0.7

    def test_evaluate_satisfaction_low(self):
        sim = PersonaSimulator()
        score = sim.evaluate_satisfaction("error: fail")
        assert score < 0.6

    def test_evaluate_satisfaction_medium(self):
        sim = PersonaSimulator()
        score = sim.evaluate_satisfaction("Here's how to do it step by step")
        assert 0.5 <= score <= 1.0

    @pytest.mark.asyncio
    async def test_run_turns_no_dispatcher(self):
        sim = PersonaSimulator("junior_dev", goal="test goal")
        result = await sim.run_turns(max_turns=3)
        assert isinstance(result, SimulationResult)
        assert len(result.turns) == 3
        assert result.total_duration_ms >= 0
        assert result.summary != ""


class TestRegressionTestRunner:
    def test_init(self, tmp_path):
        runner = RegressionTestRunner(root_dir=tmp_path)
        assert runner._results_dir.exists()
