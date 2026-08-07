"""Tests for PhaseContext and PhaseResult from core/phases.py."""
from __future__ import annotations

from maop.core.agent.evolution.phases import PhaseContext, PhaseResult


class TestPhaseContext:
    def test_defaults(self):
        ctx = PhaseContext()
        assert ctx.task == ""
        assert ctx.original_task == ""
        assert ctx.agent == ""
        assert ctx.routing_key == ""
        assert ctx.plan is None
        assert ctx.plan_result is None
        assert ctx.execution_result is None
        assert ctx.verify_result is None
        assert ctx.feedback == ""
        assert ctx.trace_id == ""
        assert ctx.streamer is None
        assert ctx.analysis_result is None
        assert ctx.analysis_dict == {}
        assert ctx.fallback_chain == []
        assert ctx.feedback_cycles == 0
        assert ctx.block_reason == ""
        assert ctx.parallel_executed is False
        assert ctx.timeout == 0.0
        assert ctx.extra == {}

    def test_with_values(self):
        ctx = PhaseContext(
            task="fix bug",
            original_task="fix bug",
            agent="claude",
            routing_key="codegen",
            feedback="needs retry",
            trace_id="abc123",
            feedback_cycles=2,
            timeout=30.0,
            extra={"key": "val"},
        )
        assert ctx.task == "fix bug"
        assert ctx.agent == "claude"
        assert ctx.routing_key == "codegen"
        assert ctx.feedback == "needs retry"
        assert ctx.trace_id == "abc123"
        assert ctx.feedback_cycles == 2
        assert ctx.timeout == 30.0
        assert ctx.extra == {"key": "val"}


class TestPhaseResult:
    def test_defaults(self):
        result = PhaseResult()
        assert result.ok is True
        assert result.error == ""
        assert result.data is None
        assert result.skip_remaining is False

    def test_skip_remaining_flag(self):
        result = PhaseResult(ok=True, skip_remaining=True)
        assert result.skip_remaining is True
        assert result.ok is True

    def test_error_result(self):
        result = PhaseResult(ok=False, error="boom")
        assert result.ok is False
        assert result.error == "boom"
