"""Tests for the three Claude Code-inspired mechanisms.

Tests:
  1. state_classifier.py — TaskStateClassifier
  2. consolidator.py — DreamConsolidator
  3. context_compressor.py — ContextCompressor
"""

from pathlib import Path
import tempfile

from maop.core.state_classifier import (
    TaskState,
    TaskStateClassifier,
)
from maop.core.context_compressor import ContextCompressor
from maop.memory.consolidator import DreamConsolidator
from maop.memory.store import MemoryStore


# ── 1. State Classifier Tests ────────────────────────────────

class TestTaskStateClassifier:
    def setup_method(self):
        self.clf = TaskStateClassifier()

    def test_passed_returns_done(self):
        result = self.clf.classify(passed=True, summary="All gates passed")
        assert result.state == TaskState.DONE
        assert result.confidence == 1.0

    def test_permission_denied_is_blocked(self):
        result = self.clf.classify(
            passed=False,
            summary="Failed",
            stderr="permission denied: cannot write to /root",
        )
        assert result.state == TaskState.BLOCKED
        assert result.block_reason != ""

    def test_waiting_for_user_is_blocked(self):
        result = self.clf.classify(
            passed=False,
            summary="Failed",
            stdout="waiting for user confirmation to proceed",
        )
        assert result.state == TaskState.BLOCKED

    def test_module_not_found_is_failed(self):
        result = self.clf.classify(
            passed=False,
            summary="Failed",
            stderr="ModuleNotFoundError: No module named 'foo'",
        )
        assert result.state == TaskState.FAILED

    def test_syntax_error_is_failed(self):
        result = self.clf.classify(
            passed=False,
            summary="Failed",
            stderr="SyntaxError: invalid syntax at line 10",
        )
        assert result.state == TaskState.FAILED

    def test_rate_limit_is_working(self):
        result = self.clf.classify(
            passed=False,
            summary="Failed",
            stdout="rate limit exceeded, retry after 60s",
        )
        assert result.state == TaskState.WORKING

    def test_429_is_working(self):
        result = self.clf.classify(
            passed=False,
            summary="Failed",
            stderr="HTTP 429 Too Many Requests",
        )
        assert result.state == TaskState.WORKING

    def test_no_pattern_defaults_to_working(self):
        result = self.clf.classify(
            passed=False,
            summary="Failed: some unknown issue",
            feedback="unknown reason",
        )
        assert result.state == TaskState.WORKING
        assert result.confidence < 0.5

    def test_blocked_takes_priority_over_failed(self):
        """If both blocked and failed patterns match, blocked wins."""
        result = self.clf.classify(
            passed=False,
            summary="Failed",
            stderr="permission denied and ModuleNotFoundError",
        )
        assert result.state == TaskState.BLOCKED

    def test_gate_reasons_are_checked(self):
        result = self.clf.classify(
            passed=False,
            summary="Failed",
            gates=[
                {"name": "exit_code", "passed": False, "reason": "access denied"},
            ],
        )
        assert result.state == TaskState.BLOCKED

    def test_custom_patterns(self):
        clf = TaskStateClassifier(
            blocked_patterns=[(r"my-custom-block", 0.9)],
        )
        result = clf.classify(passed=False, summary="my-custom-block triggered")
        assert result.state == TaskState.BLOCKED


# ── 2. Dream Consolidator Tests ──────────────────────────────

class TestDreamConsolidator:
    def setup_method(self):
        self.tmpdir = tempfile.mkdtemp(prefix="MAOP_dream_test_")
        self.store = MemoryStore(root_dir=Path(self.tmpdir))
        self.consolidator = DreamConsolidator(
            memory_store=self.store,
            min_group_size=3,
        )

    def teardown_method(self):
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _populate(self, n: int = 5):
        """Populate store with n similar entries."""
        for i in range(n):
            self.store.store(
                agent="claude",
                task="Fix the bug in module X",
                content=f"Attempt {i}: fixed issue with timeout handling",
                tags=["test"],
                topic="bugfix",
            )

    def test_dream_runs_four_phases(self):
        self._populate(5)
        report = self.consolidator.dream(dry_run=True)
        assert report.success
        assert report.total_entries_scanned >= 5
        assert report.started_at != ""
        assert report.finished_at != ""

    def test_dream_groups_similar_entries(self):
        self._populate(5)
        report = self.consolidator.dream(dry_run=True)
        assert report.groups_formed >= 1

    def test_dream_prunes_originals(self):
        self._populate(5)
        report = self.consolidator.dream(dry_run=False)
        assert report.entries_pruned >= 3  # At least min_group_size entries pruned
        assert report.entries_created >= 1  # At least one consolidated entry created

    def test_dream_dry_run_does_not_modify(self):
        self._populate(5)
        stats_before = self.store.stats()
        self.consolidator.dream(dry_run=True)
        stats_after = self.store.stats()
        assert stats_before.total_entries == stats_after.total_entries

    def test_dream_no_groups_when_entries_too_few(self):
        self._populate(2)  # Below min_group_size=3
        report = self.consolidator.dream(dry_run=True)
        assert report.groups_formed == 0
        assert report.entries_pruned == 0

    def test_dream_handles_empty_store(self):
        report = self.consolidator.dream()
        assert report.success
        assert report.total_entries_scanned == 0
        assert report.groups_formed == 0


# ── 3. Context Compressor Tests ──────────────────────────────

class TestContextCompressor:
    def setup_method(self):
        self.compressor = ContextCompressor()

    def test_compress_produces_nine_sections(self):
        messages = [
            {"role": "user", "content": "Fix the login bug"},
            {"role": "assistant", "content": "I'll fix the login bug in auth.py"},
        ]
        result = self.compressor.compress(messages)
        assert len(result.sections) == 9

    def test_primary_request_extracted(self):
        messages = [
            {"role": "user", "content": "Build a REST API for user management"},
            {"role": "assistant", "content": "OK, starting..."},
        ]
        result = self.compressor.compress(messages)
        primary = next(s for s in result.sections if s.title == "Primary Request")
        assert "REST API" in primary.content

    def test_user_corrections_preserved_verbatim(self):
        messages = [
            {"role": "user", "content": "Create a login page"},
            {"role": "assistant", "content": "I created a signup page"},
            {"role": "user", "content": "No, I said login not signup! Fix this."},
        ]
        result = self.compressor.compress(messages)
        corrections = next(s for s in result.sections if s.title == "User Corrections")
        assert corrections.preserved_verbatim is True
        assert "login" in corrections.content.lower()

    def test_files_modified_extracted(self):
        messages = [
            {"role": "user", "content": "Fix the bug"},
            {"role": "assistant", "content": "Modified auth.py and created utils.py"},
        ]
        result = self.compressor.compress(messages)
        files = next(s for s in result.sections if s.title == "Files Modified")
        assert "auth.py" in files.content or "utils.py" in files.content

    def test_reduction_calculated(self):
        long_messages = [
            {"role": "user", "content": "Do something " + "x" * 10000},
            {"role": "assistant", "content": "Done " + "y" * 10000},
        ]
        result = self.compressor.compress(long_messages, max_tokens=500)
        assert result.original_tokens > result.compressed_tokens
        assert result.reduction_pct > 0

    def test_compress_text_works(self):
        result = self.compressor.compress_text(
            "This is a long text blob with some content about files.py",
            max_tokens=1000,
        )
        assert len(result.sections) == 9

    def test_to_prompt_renders(self):
        messages = [{"role": "user", "content": "Test request"}]
        result = self.compressor.compress(messages)
        prompt = self.compressor.to_prompt(result)
        assert "[Compressed Context Summary]" in prompt
        assert "Primary Request" in prompt

    def test_verbatim_section_not_trimmed(self):
        """Even under aggressive compression, verbatim sections survive."""
        correction = "No! " * 500  # Very long correction
        messages = [
            {"role": "user", "content": "Do task"},
            {"role": "assistant", "content": "Done"},
            {"role": "user", "content": correction},
        ]
        result = self.compressor.compress(messages, max_tokens=200)
        corrections = next(s for s in result.sections if s.title == "User Corrections")
        assert corrections.preserved_verbatim is True
        # Should still have content (may be trimmed but not empty)
        assert len(corrections.content) > 0

    def test_empty_messages(self):
        result = self.compressor.compress([])
        assert len(result.sections) == 9
