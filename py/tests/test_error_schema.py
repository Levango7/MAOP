"""Tests for MAOP.core.error_schema."""

from maop.core.error_schema import MaopResult, new_result


class TestNewResult:
    def test_success_by_default(self):
        r = new_result(agent="claude", task="codegen")
        assert r.ok is True
        assert r.exit_code == 0
        assert r.error is None
        assert r.is_success() is True

    def test_failure_with_error(self):
        r = new_result(agent="kimi", task="search", exit_code=1, error="timeout")
        assert r.ok is False
        assert r.is_success() is False

    def test_failure_with_nonzero_exit(self):
        r = new_result(agent="claude", task="build", exit_code=2)
        assert r.ok is False
        assert r.is_success() is False

    def test_ok_derived_not_explicit(self):
        """ok should be derived from exit_code + error, not manually set."""
        r = new_result(agent="a", task="t", exit_code=0, error="oops")
        assert r.ok is False  # error present → not ok


class TestFormatError:
    def test_basic_format(self):
        r = new_result(agent="claude", task="codegen", error="fail", duration_ms=123)
        msg = r.format_error()
        assert "[MAOP-0]" in msg
        assert "Agent='claude'" in msg
        assert "Task='codegen'" in msg
        assert "fail" in msg
        assert "123ms" in msg

    def test_with_details(self):
        r = new_result(
            agent="kimi", task="search", exit_code=1,
            error="crash", stderr="err-out", stdout="out-data",
        )
        msg = r.format_error(include_details=True)
        assert "stderr: err-out" in msg
        assert "stdout: out-data" in msg


class TestMaopResultModel:
    def test_serialization_roundtrip(self):
        r = new_result(agent="codex", task="review", trace_id="abc", model="gpt-4")
        data = r.model_dump()
        r2 = MaopResult(**data)
        assert r2.agent == "codex"
        assert r2.trace_id == "abc"
        assert r2.model == "gpt-4"

    def test_start_time_auto_set(self):
        r = new_result(agent="a", task="t")
        assert r.start_time is not None
        assert "T" in r.start_time  # ISO format
