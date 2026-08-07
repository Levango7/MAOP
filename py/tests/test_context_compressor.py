"""Tests for MAOP.core.context_compressor — Nine-section context compaction."""

from __future__ import annotations

import pytest

from maop.core.agent.llm_chat.context_compressor import (
    CompressionResult,
    ContextCompressor,
    ContextSection,
)

# ── Fixtures ──────────────────────────────────────────────────────

@pytest.fixture
def compressor() -> ContextCompressor:
    return ContextCompressor()


@pytest.fixture
def conversation() -> list[dict]:
    return [
        {"role": "user", "content": "Please fix the login bug in auth.py"},
        {"role": "assistant", "content": "I'll fix the login bug. Let me check auth.py"},
        {"role": "user", "content": "No, that's wrong. I meant the timeout issue, not the auth logic."},
        {"role": "assistant", "content": "Understood. I'll fix the timeout in auth.py"},
        {"role": "user", "content": "Actually, wait. The file is login.py not auth.py"},
    ]


# ── Model tests ───────────────────────────────────────────────────

class TestContextSection:
    def test_defaults(self):
        s = ContextSection(title="Test", content="")
        assert s.priority == 0
        assert s.preserved_verbatim is False

    def test_with_values(self):
        s = ContextSection(title="T", content="body", priority=5, preserved_verbatim=True)
        assert s.priority == 5
        assert s.preserved_verbatim is True


class TestCompressionResult:
    def test_defaults(self):
        r = CompressionResult()
        assert r.sections == []
        assert r.original_tokens == 0
        assert r.compressed_tokens == 0
        assert r.reduction_pct == 0.0
        assert r.created_at  # non-empty


# ── Initialization ────────────────────────────────────────────────

class TestInit:
    def test_default_max_section_tokens(self):
        c = ContextCompressor()
        assert c._max_section_tokens == 500

    def test_custom_max_section_tokens(self):
        c = ContextCompressor(max_section_tokens=200)
        assert c._max_section_tokens == 200

    def test_section_titles_count(self, compressor: ContextCompressor):
        assert len(compressor.SECTION_TITLES) == 9


# ── Compress ──────────────────────────────────────────────────────

class TestCompress:
    def test_returns_compression_result(self, compressor: ContextCompressor):
        result = compressor.compress([{"role": "user", "content": "hi"}])
        assert isinstance(result, CompressionResult)

    def test_nine_sections(self, compressor: ContextCompressor):
        result = compressor.compress([{"role": "user", "content": "hi"}])
        assert len(result.sections) == 9
        titles = [s.title for s in result.sections]
        assert titles == ContextCompressor.SECTION_TITLES

    def test_primary_request_extracted(self, compressor: ContextCompressor):
        result = compressor.compress([{"role": "user", "content": "build a web app"}])
        primary = next(s for s in result.sections if s.title == "Primary Request")
        assert "build a web app" in primary.content

    def test_primary_request_truncated(self, compressor: ContextCompressor):
        long_msg = "x" * 1000
        result = compressor.compress([{"role": "user", "content": long_msg}])
        primary = next(s for s in result.sections if s.title == "Primary Request")
        assert len(primary.content) <= 500

    def test_no_primary_request(self, compressor: ContextCompressor):
        result = compressor.compress([{"role": "assistant", "content": "hi"}])
        primary = next(s for s in result.sections if s.title == "Primary Request")
        assert "No primary request" in primary.content

    def test_original_tokens_computed(self, compressor: ContextCompressor):
        result = compressor.compress([{"role": "user", "content": "a" * 100}])
        assert result.original_tokens > 0

    def test_compressed_tokens_computed(self, compressor: ContextCompressor):
        result = compressor.compress([{"role": "user", "content": "hello"}])
        assert result.compressed_tokens > 0

    def test_reduction_pct(self, compressor: ContextCompressor):
        result = compressor.compress([{"role": "user", "content": "hello world"}])
        # reduction = (1 - compressed/original) * 100
        expected = (1 - result.compressed_tokens / result.original_tokens) * 100
        assert result.reduction_pct == pytest.approx(expected)

    def test_empty_messages(self, compressor: ContextCompressor):
        result = compressor.compress([])
        assert isinstance(result, CompressionResult)
        assert len(result.sections) == 9


# ── User Corrections (verbatim) ───────────────────────────────────

class TestUserCorrections:
    def test_correction_detected(self, compressor: ContextCompressor):
        msgs = [{"role": "user", "content": "No, that's wrong. I meant the timeout."}]
        result = compressor.compress(msgs)
        corrections = next(s for s in result.sections if s.title == "User Corrections")
        assert "No, that's wrong" in corrections.content
        assert corrections.preserved_verbatim is True

    def test_short_negative_response(self, compressor: ContextCompressor):
        msgs = [{"role": "user", "content": "wrong"}]
        result = compressor.compress(msgs)
        corrections = next(s for s in result.sections if s.title == "User Corrections")
        assert "wrong" in corrections.content

    def test_no_corrections(self, compressor: ContextCompressor):
        msgs = [{"role": "user", "content": "please help me"}]
        result = compressor.compress(msgs)
        corrections = next(s for s in result.sections if s.title == "User Corrections")
        assert "No user corrections" in corrections.content

    def test_correction_priority_highest(self, compressor: ContextCompressor):
        msgs = [{"role": "user", "content": "no, stop doing that"}]
        result = compressor.compress(msgs)
        corrections = next(s for s in result.sections if s.title == "User Corrections")
        assert corrections.priority == 10

    def test_chinese_correction(self, compressor: ContextCompressor):
        msgs = [{"role": "user", "content": "不对"}]
        result = compressor.compress(msgs)
        corrections = next(s for s in result.sections if s.title == "User Corrections")
        assert "不对" in corrections.content

    def test_correction_from_assistant_ignored(self, compressor: ContextCompressor):
        msgs = [{"role": "assistant", "content": "no, that's wrong"}]
        result = compressor.compress(msgs)
        corrections = next(s for s in result.sections if s.title == "User Corrections")
        assert "No user corrections" in corrections.content


# ── Files Modified ────────────────────────────────────────────────

class TestFilesModified:
    def test_file_extraction(self, compressor: ContextCompressor):
        msgs = [{"role": "assistant", "content": "I modified auth.py and updated config.yaml"}]
        result = compressor.compress(msgs)
        files = next(s for s in result.sections if s.title == "Files Modified")
        assert "auth.py" in files.content
        assert "config.yaml" in files.content

    def test_no_files(self, compressor: ContextCompressor):
        result = compressor.compress([{"role": "user", "content": "hello"}])
        files = next(s for s in result.sections if s.title == "Files Modified")
        assert "No files modified" in files.content


# ── Error History ─────────────────────────────────────────────────

class TestErrorHistory:
    def test_error_extracted(self, compressor: ContextCompressor):
        msgs = [{"role": "assistant", "content": "error: something went wrong"}]
        result = compressor.compress(msgs)
        errors = next(s for s in result.sections if s.title == "Error History")
        assert "error" in errors.content.lower()

    def test_no_errors(self, compressor: ContextCompressor):
        result = compressor.compress([{"role": "user", "content": "all good"}])
        errors = next(s for s in result.sections if s.title == "Error History")
        assert "No errors" in errors.content


# ── Assumptions ───────────────────────────────────────────────────

class TestAssumptions:
    def test_assumption_extracted(self, compressor: ContextCompressor):
        msgs = [{"role": "assistant", "content": "Assumption: the API is RESTful"}]
        result = compressor.compress(msgs)
        assumptions = next(s for s in result.sections if s.title == "Working Assumptions")
        assert "the API is RESTful" in assumptions.content

    def test_no_assumptions(self, compressor: ContextCompressor):
        result = compressor.compress([{"role": "user", "content": "hi"}])
        assumptions = next(s for s in result.sections if s.title == "Working Assumptions")
        assert "None explicitly stated" in assumptions.content


# ── Pending Actions ───────────────────────────────────────────────

class TestPendingActions:
    def test_pending_extracted(self, compressor: ContextCompressor):
        msgs = [{"role": "assistant", "content": "todo: write tests for auth module"}]
        result = compressor.compress(msgs)
        pending = next(s for s in result.sections if s.title == "Pending Actions")
        assert "write tests" in pending.content

    def test_no_pending(self, compressor: ContextCompressor):
        result = compressor.compress([{"role": "user", "content": "hi"}])
        pending = next(s for s in result.sections if s.title == "Pending Actions")
        assert "No pending actions" in pending.content


# ── Environment Notes ─────────────────────────────────────────────

class TestEnvNotes:
    def test_env_note_extracted(self, compressor: ContextCompressor):
        msgs = [{"role": "assistant", "content": "platform: linux, python: 3.12"}]
        result = compressor.compress(msgs)
        env = next(s for s in result.sections if s.title == "Environment Notes")
        assert "platform" in env.content.lower() or "python" in env.content.lower()

    def test_no_env_notes(self, compressor: ContextCompressor):
        result = compressor.compress([{"role": "user", "content": "hi"}])
        env = next(s for s in result.sections if s.title == "Environment Notes")
        assert "Not specified" in env.content


# ── compress_text ─────────────────────────────────────────────────

class TestCompressText:
    def test_returns_result(self, compressor: ContextCompressor):
        result = compressor.compress_text("some long text blob")
        assert isinstance(result, CompressionResult)

    def test_nine_sections(self, compressor: ContextCompressor):
        result = compressor.compress_text("text")
        assert len(result.sections) == 9


# ── to_prompt ─────────────────────────────────────────────────────

class TestToPrompt:
    def test_prompt_format(self, compressor: ContextCompressor):
        result = compressor.compress([{"role": "user", "content": "hi"}])
        prompt = compressor.to_prompt(result)
        assert "[Compressed Context Summary]" in prompt
        assert "## Primary Request" in prompt
        assert "---" in prompt

    def test_verbatim_marker(self, compressor: ContextCompressor):
        msgs = [{"role": "user", "content": "no, that's wrong"}]
        result = compressor.compress(msgs)
        prompt = compressor.to_prompt(result)
        assert "(verbatim)" in prompt

    def test_token_summary(self, compressor: ContextCompressor):
        result = compressor.compress([{"role": "user", "content": "hello"}])
        prompt = compressor.to_prompt(result)
        assert "tokens" in prompt
        assert "reduction" in prompt


# ── Trim to budget ────────────────────────────────────────────────

class TestTrimToBudget:
    def test_no_trim_when_fits(self, compressor: ContextCompressor):
        result = compressor.compress([{"role": "user", "content": "short"}], max_tokens=10000)
        # All sections should have content
        assert len(result.sections) == 9

    def test_trim_low_priority_first(self, compressor: ContextCompressor):
        # Very small budget forces trimming
        result = compressor.compress(
            [{"role": "user", "content": "x" * 5000}],
            max_tokens=50,
        )
        # Verbatim section should still be present
        corrections = next(s for s in result.sections if s.title == "User Corrections")
        assert corrections.preserved_verbatim is True

    def test_verbatim_never_trimmed(self, compressor: ContextCompressor):
        msgs = [{"role": "user", "content": "no, that's wrong. " + "x" * 500}]
        result = compressor.compress(msgs, max_tokens=10)
        corrections = next(s for s in result.sections if s.title == "User Corrections")
        # Verbatim content should not contain "[... trimmed]"
        assert "[... trimmed]" not in corrections.content


# ── Token estimation ──────────────────────────────────────────────

class TestEstimateTokens:
    def test_empty_string(self):
        assert ContextCompressor._estimate_tokens("") == 0

    def test_short_string(self):
        assert ContextCompressor._estimate_tokens("abcd") == 1

    def test_long_string(self):
        assert ContextCompressor._estimate_tokens("a" * 400) == 100
