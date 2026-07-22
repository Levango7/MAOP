"""Tests for MAOP.core.state_classifier — TaskStateClassifier pattern matching."""

from __future__ import annotations


from maop.core.state_classifier import (
    ClassificationResult,
    TaskState,
    TaskStateClassifier,
)


class TestTaskState:
    def test_values(self):
        assert TaskState.DONE.value == "done"
        assert TaskState.WORKING.value == "working"
        assert TaskState.BLOCKED.value == "blocked"
        assert TaskState.FAILED.value == "failed"

    def test_is_str_enum(self):
        assert isinstance(TaskState.DONE, str)


class TestClassificationResult:
    def test_defaults(self):
        r = ClassificationResult()
        assert r.state == TaskState.WORKING
        assert r.confidence == 0.0
        assert r.reason == ""
        assert r.block_reason == ""
        assert r.matched_pattern == ""


class TestTaskStateClassifierInit:
    def test_default_patterns(self):
        c = TaskStateClassifier()
        assert len(c._blocked) > 0
        assert len(c._failed) > 0
        assert len(c._working) > 0

    def test_custom_patterns(self):
        c = TaskStateClassifier(
            blocked_patterns=[(r"custom_block", 0.9)],
            failed_patterns=[(r"custom_fail", 0.8)],
            working_patterns=[(r"custom_work", 0.7)],
        )
        assert len(c._blocked) == 1
        assert len(c._failed) == 1
        assert len(c._working) == 1


class TestClassifyDone:
    def test_passed_returns_done(self):
        c = TaskStateClassifier()
        result = c.classify(passed=True)
        assert result.state == TaskState.DONE
        assert result.confidence == 1.0

    def test_passed_ignores_text(self):
        c = TaskStateClassifier()
        result = c.classify(passed=True, stderr="permission denied")
        assert result.state == TaskState.DONE


class TestClassifyBlocked:
    def test_permission_denied(self):
        c = TaskStateClassifier()
        result = c.classify(passed=False, stderr="permission denied")
        assert result.state == TaskState.BLOCKED
        assert result.confidence > 0
        assert result.block_reason != ""

    def test_access_denied(self):
        c = TaskStateClassifier()
        result = c.classify(passed=False, stdout="access denied")
        assert result.state == TaskState.BLOCKED

    def test_waiting_for_user_input(self):
        c = TaskStateClassifier()
        result = c.classify(passed=False, feedback="waiting for user input")
        assert result.state == TaskState.BLOCKED

    def test_requires_confirmation(self):
        c = TaskStateClassifier()
        result = c.classify(passed=False, summary="requires confirmation to proceed")
        assert result.state == TaskState.BLOCKED

    def test_block_reason_extracted(self):
        c = TaskStateClassifier()
        result = c.classify(passed=False, stderr="Error: permission denied. Please contact admin.")
        assert result.state == TaskState.BLOCKED
        assert "permission denied" in result.block_reason.lower()

    def test_blocked_from_gate_reason(self):
        c = TaskStateClassifier()
        gates = [{"name": "g1", "passed": False, "reason": "unauthorized access"}]
        result = c.classify(passed=False, gates=gates)
        assert result.state == TaskState.BLOCKED


class TestClassifyFailed:
    def test_module_not_found(self):
        c = TaskStateClassifier()
        result = c.classify(passed=False, stderr="ModuleNotFoundError: No module named 'foo'")
        assert result.state == TaskState.FAILED

    def test_syntax_error(self):
        c = TaskStateClassifier()
        result = c.classify(passed=False, stderr="SyntaxError: invalid syntax")
        assert result.state == TaskState.FAILED

    def test_segmentation_fault(self):
        c = TaskStateClassifier()
        result = c.classify(passed=False, stdout="segmentation fault")
        assert result.state == TaskState.FAILED

    def test_out_of_memory(self):
        c = TaskStateClassifier()
        result = c.classify(passed=False, feedback="out of memory")
        assert result.state == TaskState.FAILED

    def test_disk_full(self):
        c = TaskStateClassifier()
        result = c.classify(passed=False, stderr="No space left on device")
        assert result.state == TaskState.FAILED

    def test_failed_not_blocked_priority(self):
        # "permission denied" (blocked) should take priority over "not found" (failed)
        c = TaskStateClassifier()
        result = c.classify(passed=False, stderr="permission denied: module not found")
        assert result.state == TaskState.BLOCKED


class TestClassifyWorking:
    def test_in_progress(self):
        c = TaskStateClassifier()
        result = c.classify(passed=False, feedback="task in progress")
        assert result.state == TaskState.WORKING

    def test_retry(self):
        c = TaskStateClassifier()
        result = c.classify(passed=False, stdout="retrying operation")
        assert result.state == TaskState.WORKING

    def test_rate_limit(self):
        c = TaskStateClassifier()
        result = c.classify(passed=False, stderr="rate limit exceeded")
        assert result.state == TaskState.WORKING

    def test_http_429(self):
        c = TaskStateClassifier()
        result = c.classify(passed=False, feedback="HTTP 429 Too Many Requests")
        assert result.state == TaskState.WORKING

    def test_default_working_when_no_match(self):
        c = TaskStateClassifier()
        result = c.classify(passed=False, feedback="something unusual happened")
        assert result.state == TaskState.WORKING
        assert result.confidence == 0.40


class TestBestMatch:
    def test_highest_confidence_wins(self):
        c = TaskStateClassifier()
        # Multiple failed patterns could match; should pick highest score
        result = c.classify(passed=False, stderr="segmentation fault: out of memory")
        assert result.state == TaskState.FAILED
        # segfault is 0.95, OOM is 0.90
        assert result.confidence == 0.95

    def test_no_match_returns_none(self):
        c = TaskStateClassifier()
        match = c._best_match("totally benign text", c._blocked)
        assert match is None


class TestExtractBlockReason:
    def test_default_when_no_sentence_match(self):
        c = TaskStateClassifier()
        reason = c._extract_block_reason("no relevant keywords here")
        assert reason == "External input required"

    def test_extracts_sentence(self):
        c = TaskStateClassifier()
        reason = c._extract_block_reason("All good. permission denied. Continue.")
        assert "permission denied" in reason.lower()
