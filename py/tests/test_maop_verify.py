"""Tests for maop_verify.py — Verification gates and engine."""

from __future__ import annotations

import pytest

from maop.core.reliability.error_schema import new_result
from maop.maop_verify import (
    GateResult,
    VerifyEngine,
    _gate_content_safety,
    _gate_dry_run,
    _gate_exit_code,
    _gate_lint,
    _gate_output,
    _gate_syntax_check,
)

# ── Gate function tests ──────────────────────────────────────

class TestGateExitCode:
    def test_zero_passes(self):
        r = new_result(agent="a", task="t", exit_code=0, stdout="ok")
        gr = _gate_exit_code({}, r)
        assert gr.passed and gr.name == "exit_code"

    def test_nonzero_fails(self):
        r = new_result(agent="a", task="t", exit_code=1, stdout="")
        gr = _gate_exit_code({}, r)
        assert not gr.passed
        assert "exit_code=1" in gr.reason

    def test_none_result(self):
        gr = _gate_exit_code({}, None)
        assert not gr.passed


class TestGateOutput:
    def test_non_empty_passes(self):
        r = new_result(agent="a", task="t", exit_code=0, stdout="result here")
        gr = _gate_output({}, r)
        assert gr.passed

    def test_empty_fails(self):
        r = new_result(agent="a", task="t", exit_code=0, stdout="")
        gr = _gate_output({}, r)
        assert not gr.passed

    def test_whitespace_only_fails(self):
        r = new_result(agent="a", task="t", exit_code=0, stdout="   \n  ")
        gr = _gate_output({}, r)
        assert not gr.passed

    def test_none_result(self):
        gr = _gate_output({}, None)
        assert not gr.passed


class TestGateContentSafety:
    def test_clean_output_passes(self):
        r = new_result(agent="a", task="t", exit_code=0, stdout="all good here")
        assert _gate_content_safety({}, r).passed

    def test_api_key_detected(self):
        r = new_result(agent="a", task="t", exit_code=0,
                        stdout="api_key=sk-abcdefghijklmnopqrstuvwxyz123456")
        gr = _gate_content_safety({}, r)
        assert not gr.passed
        assert "secret" in gr.reason.lower()

    def test_openai_key_detected(self):
        r = new_result(agent="a", task="t", exit_code=0,
                        stdout="key: sk-abcdefghijklmnopqrstuvwxyz1234567890")
        assert not _gate_content_safety({}, r).passed

    def test_private_key_detected(self):
        r = new_result(agent="a", task="t", exit_code=0,
                        stdout="-----BEGIN RSA PRIVATE KEY-----")
        assert not _gate_content_safety({}, r).passed

    def test_github_pat_detected(self):
        r = new_result(agent="a", task="t", exit_code=0,
                        stdout="token: ghp_abcdefghijklmnopqrstuvwxyz0123456789")
        assert not _gate_content_safety({}, r).passed

    def test_empty_output_passes(self):
        r = new_result(agent="a", task="t", exit_code=0, stdout="")
        assert _gate_content_safety({}, r).passed


class TestGateSyntaxCheck:
    def test_clean_output(self):
        r = new_result(agent="a", task="t", exit_code=0, stdout="def foo(): pass")
        assert _gate_syntax_check({}, r).passed

    def test_syntax_error_detected(self):
        r = new_result(agent="a", task="t", exit_code=0, stdout="SyntaxError: invalid syntax")
        assert not _gate_syntax_check({}, r).passed

    def test_indentation_error_detected(self):
        r = new_result(agent="a", task="t", exit_code=0, stdout="IndentationError: unexpected indent")
        assert not _gate_syntax_check({}, r).passed


class TestGateLint:
    def test_clean_output(self):
        r = new_result(agent="a", task="t", exit_code=0, stdout="all good")
        assert _gate_lint({}, r).passed

    def test_pycodestyle_error(self):
        r = new_result(agent="a", task="t", exit_code=0, stdout="E501 line too long")
        assert not _gate_lint({}, r).passed

    def test_pyflakes_error(self):
        r = new_result(agent="a", task="t", exit_code=0, stdout="F841 local variable assigned but never used")
        assert not _gate_lint({}, r).passed


class TestGateDryRun:
    def test_always_passes(self):
        assert _gate_dry_run({}, None).passed
        r = new_result(agent="a", task="t", exit_code=0, stdout="ok")
        assert _gate_dry_run({}, r).passed


# ── t18: semantic dry-run tests ─────────────────────────────

class TestGateDryRunSemantic:
    """t18 (2026-07-21) — _gate_dry_run now actually checks for dry-run
    signals when the plan declares dry_run=True. These tests cover the
    new behavior; TestGateDryRun above preserves the backward-compat
    case (no dry_run declaration -> always passes)."""

    def test_passes_when_plan_does_not_declare_dry_run(self):
        """Backward compat: plan without dry_run key -> always pass."""
        gr = _gate_dry_run({}, None)
        assert gr.passed
        assert gr.name == "dry-run"

    def test_fails_when_dry_run_requested_but_result_is_none(self):
        gr = _gate_dry_run({"dry_run": True}, None)
        assert not gr.passed
        assert "no execution result" in gr.reason

    def test_fails_when_dry_run_requested_but_no_signal(self):
        r = new_result(agent="a", task="t", exit_code=0, stdout="ok")
        gr = _gate_dry_run({"dry_run": True}, r)
        assert not gr.passed
        assert "no dry-run signal" in gr.reason

    def test_passes_with_stdout_signal_dry_run_marker(self):
        r = new_result(agent="a", task="t", exit_code=0, stdout="DRY-RUN: would do X")
        gr = _gate_dry_run({"dry_run": True}, r)
        assert gr.passed

    def test_passes_with_stdout_signal_no_changes_applied(self):
        r = new_result(agent="a", task="t", exit_code=0, stdout="No changes applied")
        gr = _gate_dry_run({"dry_run": True}, r)
        assert gr.passed

    def test_passes_with_structured_output_dry_run_flag(self):
        r = new_result(
            agent="a", task="t", exit_code=0,
            stdout="",
            structured_output={"dry_run": True, "plan": ["step1", "step2"]},
        )
        gr = _gate_dry_run({"dry_run": True}, r)
        assert gr.passed

    def test_passes_with_structured_output_dry_run_artifacts(self):
        r = new_result(
            agent="a", task="t", exit_code=0,
            stdout="",
            structured_output={"dry_run_artifacts": ["file1.py", "config.yaml"]},
        )
        gr = _gate_dry_run({"dry_run": True}, r)
        assert gr.passed

    def test_fails_when_expected_artifacts_missing(self):
        r = new_result(
            agent="a", task="t", exit_code=0,
            stdout="DRY-RUN: simulated",
            structured_output={"dry_run_artifacts": ["file1.py"]},
        )
        gr = _gate_dry_run(
            {"dry_run": True, "expected_dry_run_artifacts": ["file1.py", "file2.py"]},
            r,
        )
        assert not gr.passed
        assert "missing expected artifacts" in gr.reason
        assert "file2.py" in gr.reason

    def test_passes_when_all_expected_artifacts_present(self):
        r = new_result(
            agent="a", task="t", exit_code=0,
            stdout="",
            structured_output={"dry_run_artifacts": ["a.txt", "b.txt", "c.txt"]},
        )
        gr = _gate_dry_run(
            {"dry_run": True, "expected_dry_run_artifacts": ["a.txt", "b.txt"]},
            r,
        )
        assert gr.passed

    def test_dry_run_false_in_plan_treated_as_not_requested(self):
        """Plan with dry_run=False should behave like no declaration -> pass."""
        r = new_result(agent="a", task="t", exit_code=0, stdout="ok")
        gr = _gate_dry_run({"dry_run": False}, r)
        assert gr.passed


# ── VerifyEngine tests ───────────────────────────────────────

class TestVerifyEngine:
    @pytest.fixture
    def engine(self):
        return VerifyEngine()

    @pytest.fixture
    def success_result(self):
        return new_result(agent="a", task="t", exit_code=0, stdout="success output")

    @pytest.fixture
    def failure_result(self):
        return new_result(agent="a", task="t", exit_code=1, stdout="")

    def test_all_gates_pass(self, engine, success_result):
        plan = {"gates": ["exit_code", "output"]}
        vr = engine.verify(plan, success_result)
        assert vr.passed
        assert "All gates passed" in vr.summary
        assert len(vr.gates) == 2

    def test_exit_code_fails(self, engine, failure_result):
        plan = {"gates": ["exit_code", "output"]}
        vr = engine.verify(plan, failure_result)
        assert not vr.passed
        assert "exit_code" in vr.summary

    def test_unknown_gate(self, engine, success_result):
        plan = {"gates": ["nonexistent_gate"]}
        vr = engine.verify(plan, success_result)
        assert not vr.passed
        assert "Unknown gate" in vr.gates[0].reason

    def test_default_gates_when_empty(self, engine, success_result):
        plan = {"gates": []}
        vr = engine.verify(plan, success_result)
        assert vr.passed
        assert len(vr.gates) == 2  # exit_code + output

    def test_default_gates_when_missing(self, engine, success_result):
        plan = {}
        vr = engine.verify(plan, success_result)
        assert vr.passed

    def test_content_safety_gate(self, engine):
        r = new_result(agent="a", task="t", exit_code=0,
                        stdout="api_key=sk-abcdefghijklmnopqrstuvwxyz123456")
        plan = {"gates": ["exit_code", "output", "content-safety"]}
        vr = engine.verify(plan, r)
        assert not vr.passed
        assert any(g.name == "content-safety" and not g.passed for g in vr.gates)

    def test_custom_gate(self, success_result):
        custom = {"custom-check": lambda p, r: GateResult(name="custom-check", passed=True)}
        engine = VerifyEngine(custom_gates=custom)
        plan = {"gates": ["exit_code", "custom-check"]}
        vr = engine.verify(plan, success_result)
        assert vr.passed
        assert any(g.name == "custom-check" for g in vr.gates)

    def test_state_classification(self, engine, success_result):
        plan = {"gates": ["exit_code", "output"]}
        vr = engine.verify(plan, success_result)
        assert vr.state in ("done", "blocked", "working", "failed")
        assert vr.classification is not None

    def test_feedback_on_failure(self, engine, failure_result):
        plan = {"gates": ["exit_code", "output"]}
        vr = engine.verify(plan, failure_result)
        assert vr.feedback != ""
