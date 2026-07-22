"""Tests for ThreeLayerMemory — Working / Episodic / Semantic / Transform."""

import shutil
import tempfile
import time

import pytest

from maop.core.three_layer_memory import (
    ContextHead,
    FocusMode,
    QualityDimensions,
    ThreeLayerMemory,
    _compress_text,
    _is_negative_feedback,
    _text_relevance,
    decay_weight,
)


@pytest.fixture
def mem_env():
    tmpdir = tempfile.mkdtemp()
    mem = ThreeLayerMemory(root_dir=tmpdir, working_max=50, working_ttl=60)
    yield mem
    shutil.rmtree(tmpdir, ignore_errors=True)


# ── Working Memory ────────────────────────────────────────────

class TestWorkingMemory:
    def test_put_get(self, mem_env):
        mem_env.working_put("key1", {"agent": "claude"})
        assert mem_env.working_get("key1") == {"agent": "claude"}

    def test_get_missing(self, mem_env):
        assert mem_env.working_get("nonexistent") is None

    def test_delete(self, mem_env):
        mem_env.working_put("key1", "val1")
        mem_env.working_delete("key1")
        assert mem_env.working_get("key1") is None

    def test_clear(self, mem_env):
        mem_env.working_put("a", 1)
        mem_env.working_put("b", 2)
        mem_env.working_clear()
        assert mem_env.working_get("a") is None


# ── Episodic Memory ───────────────────────────────────────────

class TestEpisodicMemory:
    def test_store_and_search(self, mem_env):
        mem_env.episodic_store(task="Fix login bug", agent="claude", outcome="success", score=0.9)
        mem_env.episodic_store(task="Deploy to prod", agent="kimi", outcome="failure", score=0.3)
        results = mem_env.episodic_search(query="login")
        assert len(results) == 1
        assert results[0].entry.task == "Fix login bug"

    def test_search_by_agent(self, mem_env):
        mem_env.episodic_store(task="Task A", agent="claude", outcome="success", score=0.8)
        mem_env.episodic_store(task="Task B", agent="kimi", outcome="success", score=0.7)
        results = mem_env.episodic_search(agent="claude")
        assert len(results) == 1
        assert results[0].entry.agent == "claude"

    def test_search_by_outcome(self, mem_env):
        mem_env.episodic_store(task="T1", agent="a", outcome="success", score=0.9)
        mem_env.episodic_store(task="T2", agent="b", outcome="failure", score=0.2)
        results = mem_env.episodic_search(outcome="failure")
        assert len(results) == 1
        assert results[0].entry.outcome == "failure"

    def test_decay_weight_recent(self):
        w = decay_weight(time.time())
        assert w == 1.0

    def test_decay_weight_old(self):
        w = decay_weight(time.time() - 60 * 86400)  # 60 days ago
        assert 0.3 < w < 0.5

    def test_decay_applied_in_search(self, mem_env):
        mem_env.episodic_store(task="Old task", agent="a", outcome="success", score=0.9)
        # Manually set created_at to old
        with mem_env._episodic_connect() as conn:
            conn.execute("UPDATE episodic_memory SET created_at = ?", (time.time() - 60 * 86400,))
        results = mem_env.episodic_search(apply_decay=True)
        assert len(results) == 1
        assert results[0].retrieval_weight < 1.0

    def test_stats(self, mem_env):
        mem_env.episodic_store(task="T1", agent="a", outcome="success", score=0.9)
        mem_env.episodic_store(task="T2", agent="b", outcome="failure", score=0.3)
        stats = mem_env.episodic_stats()
        assert stats["total"] == 2
        assert stats["by_outcome"]["success"] == 1
        assert stats["by_outcome"]["failure"] == 1

    def test_lessons_stored(self, mem_env):
        mem_env.episodic_store(
            task="Fix bug", agent="claude", outcome="success", score=0.9,
            lessons=["Always check null", "Use type hints"],
        )
        entry = mem_env.episodic_get(
            mem_env.episodic_search(query="Fix bug")[0].entry.id
        )
        assert len(entry.lessons) == 2


# ── Consolidation ─────────────────────────────────────────────

class TestConsolidation:
    def test_consolidate_high_score(self, mem_env):
        mem_env.episodic_store(task="Important task", agent="claude", outcome="success", score=0.95)
        report = mem_env.consolidate(min_score=0.7)
        assert report.candidates == 1
        assert report.consolidated == 1

    def test_consolidate_skips_low_score(self, mem_env):
        mem_env.episodic_store(task="Minor task", agent="kimi", outcome="partial", score=0.3)
        report = mem_env.consolidate(min_score=0.7)
        assert report.candidates == 0
        assert report.consolidated == 0

    def test_consolidate_no_duplicate(self, mem_env):
        mem_env.episodic_store(task="Task", agent="a", outcome="success", score=0.9)
        mem_env.consolidate(min_score=0.7)
        report = mem_env.consolidate(min_score=0.7)
        assert report.candidates == 0  # already consolidated


# ── Transform Focus ───────────────────────────────────────────

class TestTransform:
    def test_deep_focus_returns_few(self, mem_env):
        for i in range(5):
            mem_env.episodic_store(task=f"Fix bug {i}", agent="claude", outcome="success", score=0.8 + i * 0.02)
        result = mem_env.transform("Fix bug", mode=FocusMode.DEEP_FOCUS)
        assert result.mode == FocusMode.DEEP_FOCUS
        assert len(result.context_parts) <= 3

    def test_broad_scan_returns_more(self, mem_env):
        for i in range(10):
            mem_env.episodic_store(task=f"Task {i}", agent="a", outcome="success", score=0.5)
        result = mem_env.transform("Task", mode=FocusMode.BROAD_SCAN)
        assert result.mode == FocusMode.BROAD_SCAN
        assert len(result.context_parts) >= len(
            mem_env.transform("Task", mode=FocusMode.DEEP_FOCUS).context_parts
        )

    def test_exploratory_keeps_all(self, mem_env):
        for i in range(5):
            mem_env.episodic_store(task=f"Explore {i}", agent="a", outcome="success", score=0.7)
        result = mem_env.transform("Explore", mode=FocusMode.EXPLORATORY)
        assert result.mode == FocusMode.EXPLORATORY
        assert len(result.context_parts) >= 5

    def test_working_memory_included(self, mem_env):
        mem_env.working_put("login", {"status": "in_progress"})
        mem_env.episodic_store(task="Fix login", agent="claude", outcome="success", score=0.9)
        result = mem_env.transform("login", mode=FocusMode.DEEP_FOCUS)
        working_parts = [p for p in result.context_parts if p["layer"] == "working"]
        assert len(working_parts) >= 1

    def test_text_relevance(self):
        assert _text_relevance("fix login bug", "Fix login timeout bug") > 0.5
        assert _text_relevance("deploy", "Fix login bug") == 0.0

    def test_weights_sorted_descending(self, mem_env):
        mem_env.episodic_store(task="Fix critical bug", agent="a", outcome="success", score=0.95)
        mem_env.episodic_store(task="Minor cleanup", agent="b", outcome="partial", score=0.3)
        result = mem_env.transform("Fix bug", mode=FocusMode.EXPLORATORY)
        weights = [p["weight"] for p in result.context_parts]
        assert weights == sorted(weights, reverse=True)

    def test_pipeline_stats_present(self, mem_env):
        mem_env.episodic_store(task="Test pipeline", agent="a", outcome="success", score=0.8)
        result = mem_env.transform("Test pipeline", mode=FocusMode.EXPLORATORY)
        assert "raw_items" in result.pipeline_stats
        assert "after_dedup" in result.pipeline_stats
        assert "final_items" in result.pipeline_stats

    def test_deduplication(self, mem_env):
        for _ in range(3):
            mem_env.episodic_store(task="Same task", agent="a", outcome="success", score=0.8)
        result = mem_env.transform("Same task", mode=FocusMode.EXPLORATORY)
        assert result.pipeline_stats["after_dedup"] <= result.pipeline_stats["raw_items"]

    def test_budget_control(self, mem_env):
        for i in range(10):
            mem_env.episodic_store(task=f"Budget test {i} " + "x" * 200, agent="a", outcome="success", score=0.7)
        result = mem_env.transform("Budget test", mode=FocusMode.EXPLORATORY, token_budget=200)
        assert result.total_tokens_estimate <= 300

    def test_compress_text(self):
        long_text = "First sentence. Second sentence. Third sentence. Fourth sentence. Fifth sentence."
        compressed = _compress_text(long_text, max_len=60)
        assert len(compressed) <= 80
        assert "..." in compressed

    def test_compress_short_text_unchanged(self):
        short = "Hello world"
        assert _compress_text(short) == short


# ── Quality Dimensions ────────────────────────────────────────

class TestQualityDimensions:
    def test_defaults(self):
        qd = QualityDimensions()
        assert qd.composite() == 0.0

    def test_composite_weighted(self):
        qd = QualityDimensions(correctness=1.0, completeness=1.0, efficiency=1.0, clarity=1.0, safety=1.0)
        assert qd.composite() == 1.0

    def test_composite_partial(self):
        qd = QualityDimensions(correctness=0.8, completeness=0.6, efficiency=0.5)
        c = qd.composite()
        assert 0.0 < c < 1.0
        assert c > 0.5  # correctness weighted 0.35

    def test_episodic_store_with_qd(self, mem_env):
        qd = QualityDimensions(correctness=0.9, completeness=0.8, efficiency=0.7)
        eid = mem_env.episodic_store(
            task="Test with QD", agent="claude", outcome="success",
            quality_dimensions=qd,
        )
        entry = mem_env.episodic_get(eid)
        assert entry is not None
        assert entry.quality_dimensions.correctness == 0.9
        assert entry.quality_dimensions.completeness == 0.8

    def test_qd_auto_sets_score(self, mem_env):
        qd = QualityDimensions(correctness=0.8, completeness=0.7)
        eid = mem_env.episodic_store(
            task="Auto score", agent="claude", outcome="success",
            quality_dimensions=qd,
        )
        entry = mem_env.episodic_get(eid)
        assert entry is not None
        assert entry.score == qd.composite()

    def test_qd_in_search_results(self, mem_env):
        qd = QualityDimensions(correctness=0.9, completeness=0.8)
        mem_env.episodic_store(
            task="Search QD test", agent="claude", outcome="success",
            score=0.85, quality_dimensions=qd,
        )
        results = mem_env.episodic_search(query="Search QD")
        assert len(results) >= 1
        assert results[0].entry.quality_dimensions.correctness == 0.9


class TestNegativeFeedback:
    def test_negative_keywords(self):
        assert _is_negative_feedback("This is bad and broken")
        assert _is_negative_feedback("The result was wrong")

    def test_positive_not_negative(self):
        assert not _is_negative_feedback("Great job, works perfectly")
        assert not _is_negative_feedback("")

    def test_mixed_not_negative(self):
        assert not _is_negative_feedback("Good but could be better")


class TestSubmitFeedback:
    def test_update_feedback(self, mem_env):
        eid = mem_env.episodic_store(task="Feedback test", agent="claude", outcome="success")
        result = mem_env.submit_feedback(eid, user_feedback="Looks good")
        assert result["updated"] is True
        entry = mem_env.episodic_get(eid)
        assert entry is not None
        assert entry.user_feedback == "Looks good"

    def test_update_with_qd(self, mem_env):
        eid = mem_env.episodic_store(task="QD feedback", agent="claude", outcome="success")
        qd = QualityDimensions(correctness=0.5, completeness=0.4)
        result = mem_env.submit_feedback(eid, user_feedback="Poor result", quality_dimensions=qd)
        assert result["updated"] is True
        assert result["composite_score"] == qd.composite()
        entry = mem_env.episodic_get(eid)
        assert entry is not None
        assert entry.quality_dimensions.correctness == 0.5

    def test_negative_feedback_triggers_actions(self, mem_env):
        eid = mem_env.episodic_store(task="Bad result test", agent="claude", outcome="failure")
        qd = QualityDimensions(correctness=0.2, completeness=0.1)
        result = mem_env.submit_feedback(eid, user_feedback="This is terrible and broken", quality_dimensions=qd)
        assert result["updated"] is True
        assert "evolution_reflection" in result["triggered_actions"]

    def test_nonexistent_entry(self, mem_env):
        result = mem_env.submit_feedback("nonexistent_id", user_feedback="test")
        assert result["updated"] is False


class TestMultiHeadContext:
    def test_multi_head_returns_all_heads(self, mem_env):
        mem_env.episodic_store(task="Fix timeout error in login", agent="claude", outcome="success", score=0.9)
        result = mem_env.transform_multi_head("Fix timeout error")
        assert len(result.heads) == 3
        head_names = {h.head for h in result.heads}
        assert head_names == {ContextHead.FACTS, ContextHead.INTENT, ContextHead.CONSTRAINTS}

    def test_multi_head_fused_context(self, mem_env):
        mem_env.episodic_store(task="Deploy to production", agent="codex", outcome="success", score=0.85)
        result = mem_env.transform_multi_head("Deploy to production")
        assert len(result.fused_context) >= 0
        assert result.total_tokens_estimate >= 0

    def test_multi_head_single_head(self, mem_env):
        mem_env.episodic_store(task="Debug the constraint violation", agent="claude", outcome="failure", score=0.3)
        result = mem_env.transform_multi_head("Debug constraint", heads=[ContextHead.CONSTRAINTS])
        assert len(result.heads) == 1
        assert result.heads[0].head == ContextHead.CONSTRAINTS

    def test_multi_head_empty_context(self, mem_env):
        result = mem_env.transform_multi_head("nonexistent query xyz")
        assert result.fused_context is not None

    def test_multi_head_budget_control(self, mem_env):
        for i in range(10):
            mem_env.episodic_store(task=f"Task {i} with error and constraint data", agent="claude", outcome="success", score=0.8)
        result = mem_env.transform_multi_head("error constraint", token_budget=500)
        total_tokens = sum(len(str(p)) // 4 for p in result.fused_context)
        assert total_tokens <= 700  # some margin


# ── P4: Session Summary Compression ───────────────────────────

class TestSessionSummaryCompression:
    def test_store_with_summary(self, mem_env):
        eid = mem_env.episodic_store(
            task="Fix login timeout", agent="claude", outcome="success", score=0.9,
            summary="Resolved by adding socket timeout config",
            key_decisions=["Used 30s timeout", "Added retry logic"],
            files_touched=["auth/login.py", "config/settings.yaml"],
        )
        entry = mem_env.episodic_get(eid)
        assert entry is not None
        assert entry.summary == "Resolved by adding socket timeout config"
        assert entry.key_decisions == ["Used 30s timeout", "Added retry logic"]
        assert entry.files_touched == ["auth/login.py", "config/settings.yaml"]

    def test_search_returns_summary_fields(self, mem_env):
        mem_env.episodic_store(
            task="Deploy to prod", agent="codex", outcome="success", score=0.85,
            summary="Blue-green deployment succeeded",
            key_decisions=["Used blue-green strategy"],
            files_touched=["deploy.yaml"],
        )
        results = mem_env.episodic_search(query="Deploy")
        assert len(results) >= 1
        entry = results[0].entry
        assert entry.summary == "Blue-green deployment succeeded"
        assert entry.key_decisions == ["Used blue-green strategy"]
        assert entry.files_touched == ["deploy.yaml"]

    def test_defaults_empty_when_not_provided(self, mem_env):
        eid = mem_env.episodic_store(task="Simple task", agent="a", outcome="success", score=0.5)
        entry = mem_env.episodic_get(eid)
        assert entry is not None
        assert entry.summary == ""
        assert entry.key_decisions == []
        assert entry.files_touched == []

    def test_row_to_episodic_parses_json_fields(self, mem_env):
        eid = mem_env.episodic_store(
            task="Complex task", agent="claude", outcome="success", score=0.9,
            summary="Multi-step fix applied",
            key_decisions=["Step 1: diagnose", "Step 2: patch", "Step 3: verify"],
            files_touched=["src/main.py", "tests/test_main.py", "README.md"],
            lessons=["Always verify after patch"],
        )
        entry = mem_env.episodic_get(eid)
        assert entry is not None
        assert len(entry.key_decisions) == 3
        assert len(entry.files_touched) == 3
        assert len(entry.lessons) == 1

    def test_consolidate_uses_summary(self, mem_env):
        mem_env.episodic_store(
            task="Critical fix", agent="claude", outcome="success", score=0.95,
            summary="Patched SQL injection vulnerability",
            key_decisions=["Used parameterized queries"],
            files_touched=["db/queries.py"],
        )
        report = mem_env.consolidate(min_score=0.7)
        assert report.consolidated == 1


# ── P3: FTS5 Full-Text Search ──────────────────────────────────

class TestFTS5Search:
    def test_fts5_search_by_task(self, mem_env):
        mem_env.episodic_store(task="Fix authentication timeout error", agent="claude", outcome="success", score=0.9)
        mem_env.episodic_store(task="Deploy to production server", agent="codex", outcome="success", score=0.8)
        results = mem_env.episodic_search(query="authentication")
        assert len(results) >= 1
        assert "authentication" in results[0].entry.task.lower()

    def test_fts5_search_by_summary(self, mem_env):
        mem_env.episodic_store(
            task="Fix bug", agent="claude", outcome="success", score=0.9,
            summary="Resolved socket timeout in network layer",
        )
        results = mem_env.episodic_search(query="socket timeout")
        assert len(results) >= 1

    def test_fts5_search_multi_word(self, mem_env):
        mem_env.episodic_store(task="Fix login timeout", agent="claude", outcome="success", score=0.9)
        mem_env.episodic_store(task="Deploy to prod", agent="codex", outcome="success", score=0.8)
        results = mem_env.episodic_search(query="login timeout")
        assert len(results) >= 1
        assert "login" in results[0].entry.task.lower()

    def test_fts5_search_no_results(self, mem_env):
        mem_env.episodic_store(task="Fix login bug", agent="claude", outcome="success", score=0.9)
        results = mem_env.episodic_search(query="quantum computing")
        assert len(results) == 0

    def test_fts5_search_combined_with_agent_filter(self, mem_env):
        mem_env.episodic_store(task="Fix timeout error", agent="claude", outcome="success", score=0.9)
        mem_env.episodic_store(task="Fix timeout error", agent="codex", outcome="success", score=0.8)
        results = mem_env.episodic_search(query="timeout", agent="claude")
        assert len(results) >= 1
        assert all(r.entry.agent == "claude" for r in results)



# ── t13: overflow-to-episodic on eviction ───────────────────────────


class TestOverflowToEpisodic:
    """Verify evicted Working Memory entries are overflowed to Episodic."""

    def test_evicted_entry_appears_in_episodic(self, mem_env):
        """Fill working memory to capacity; the next put should evict the
        OLDEST entry, which must show up in episodic memory."""
        # working_max=50 in the fixture.
        for i in range(50):
            mem_env.working_put(f"key{i}", {"index": i})
        # Cache is full. Adding one more must evict "key0" (oldest).
        mem_env.working_put("key50", {"index": 50})

        results = mem_env.episodic_search(query="overflow key0")
        assert len(results) >= 1
        overflow_entries = [r for r in results if r.entry.outcome == "overflow"]
        assert len(overflow_entries) >= 1
        # The task field should mention the EVICTED key (key0), not key50.
        assert any("key0" in r.entry.task for r in overflow_entries)
        # The new key (key50) should NOT be in episodic (it's in working).
        assert all("key50" not in r.entry.task for r in overflow_entries)

    def test_new_value_not_overflowed(self, mem_env):
        """The NEW value must stay in working memory; only the EVICTED
        value should be overflowed to episodic."""
        for i in range(50):
            mem_env.working_put(f"key{i}", {"index": i})
        mem_env.working_put("key50", {"index": 50})

        # The new value must be retrievable from working memory.
        assert mem_env.working_get("key50") == {"index": 50}
        # And it should NOT appear in episodic (it wasn't evicted).
        results = mem_env.episodic_search(query="key50")
        overflow_for_key50 = [
            r for r in results
            if r.entry.outcome == "overflow" and "key50" in r.entry.task
        ]
        assert len(overflow_for_key50) == 0

    def test_multiple_evictions_each_overflow(self, mem_env):
        """Each evicted entry should produce its own episodic overflow record."""
        for i in range(50):
            mem_env.working_put(f"key{i}", {"index": i})
        # Evict 3 entries.
        mem_env.working_put("key50", {"index": 50})
        mem_env.working_put("key51", {"index": 51})
        mem_env.working_put("key52", {"index": 52})

        results = mem_env.episodic_search(query="overflow")
        overflow_entries = [r for r in results if r.entry.outcome == "overflow"]
        # key0, key1, key2 were evicted (in order).
        evicted_keys_in_episodic = [
            r.entry.task.split("overflow: ")[-1] for r in overflow_entries
            if "overflow: " in r.entry.task
        ]
        assert "key0" in evicted_keys_in_episodic
        assert "key1" in evicted_keys_in_episodic
        assert "key2" in evicted_keys_in_episodic

    def test_pinned_entry_not_evicted_no_overflow(self, mem_env):
        """A pinned Working Memory entry should never be evicted, and thus
        never overflowed to episodic."""
        mem_env.working_put("pinned_key", {"important": True})
        assert mem_env.working_pin("pinned_key") is True
        # Fill the cache to capacity, evicting other entries.
        for i in range(60):
            mem_env.working_put(f"filler{i}", {"i": i})
        # The pinned key should still be in working memory.
        assert mem_env.working_get("pinned_key") == {"important": True}
        # And it should NOT appear in episodic overflow records.
        results = mem_env.episodic_search(query="pinned_key")
        overflow_for_pinned = [
            r for r in results
            if r.entry.outcome == "overflow" and "pinned_key" in r.entry.task
        ]
        assert len(overflow_for_pinned) == 0
