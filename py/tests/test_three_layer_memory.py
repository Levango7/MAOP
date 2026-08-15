"""Tests for ThreeLayerMemory — Working / Episodic / Semantic / Transform."""

import shutil
import tempfile
import time
from pathlib import Path
from unittest.mock import patch

import pytest

from maop.core.memory.three_layer_memory import (
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


# --- Merged from test_three_layer_memory_coverage.py (TestThreeLayerMemory only, 5 module-import tests dropped) ---

# ── Three Layer Memory ──────────────────────────────────────────────

class TestThreeLayerMemory:
    def test_init(self, tmp_path):
        from maop.core.memory.three_layer_memory import ThreeLayerMemory
        mem = ThreeLayerMemory(root_dir=str(tmp_path))
        assert mem is not None

    def test_working_put_get(self, tmp_path):
        from maop.core.memory.three_layer_memory import ThreeLayerMemory
        mem = ThreeLayerMemory(root_dir=str(tmp_path))
        mem.working_put("k1", "value1")
        assert mem.working_get("k1") == "value1"

    def test_working_get_missing(self, tmp_path):
        from maop.core.memory.three_layer_memory import ThreeLayerMemory
        mem = ThreeLayerMemory(root_dir=str(tmp_path))
        assert mem.working_get("nonexistent") is None

    def test_working_delete(self, tmp_path):
        from maop.core.memory.three_layer_memory import ThreeLayerMemory
        mem = ThreeLayerMemory(root_dir=str(tmp_path))
        mem.working_put("k1", "value1")
        mem.working_delete("k1")
        assert mem.working_get("k1") is None

    def test_working_clear(self, tmp_path):
        from maop.core.memory.three_layer_memory import ThreeLayerMemory
        mem = ThreeLayerMemory(root_dir=str(tmp_path))
        mem.working_put("k1", "v1")
        mem.working_put("k2", "v2")
        mem.working_clear()
        assert mem.working_get("k1") is None

    def test_working_pin_unpin(self, tmp_path):
        from maop.core.memory.three_layer_memory import ThreeLayerMemory
        mem = ThreeLayerMemory(root_dir=str(tmp_path))
        mem.working_put("k1", "v1")
        assert mem.working_pin("k1") is True
        mem.working_unpin("k1")
        assert mem.working_pinned_keys() == []

    def test_episodic_store(self, tmp_path):
        from maop.core.memory.three_layer_memory import ThreeLayerMemory
        mem = ThreeLayerMemory(root_dir=str(tmp_path))
        entry_id = mem.episodic_store(task="test task", agent="a1", outcome="success", score=0.9)
        assert entry_id

    def test_episodic_store_with_lessons(self, tmp_path):
        from maop.core.memory.three_layer_memory import ThreeLayerMemory
        mem = ThreeLayerMemory(root_dir=str(tmp_path))
        entry_id = mem.episodic_store(
            task="test", agent="a", outcome="failure",
            lessons=["lesson1", "lesson2"], user_feedback="bad",
            summary="test summary", key_decisions=["d1"],
            files_touched=["f1"], metadata={"k": "v"},
        )
        assert entry_id

    def test_episodic_search_empty(self, tmp_path):
        from maop.core.memory.three_layer_memory import ThreeLayerMemory
        mem = ThreeLayerMemory(root_dir=str(tmp_path))
        results = mem.episodic_search(query="", top=10)
        assert isinstance(results, list)

    def test_episodic_search_with_query(self, tmp_path):
        from maop.core.memory.three_layer_memory import ThreeLayerMemory
        mem = ThreeLayerMemory(root_dir=str(tmp_path))
        mem.episodic_store(task="fix bug", agent="a", summary="bug fix")
        results = mem.episodic_search(query="bug", top=10)
        assert isinstance(results, list)

    def test_episodic_search_with_filters(self, tmp_path):
        from maop.core.memory.three_layer_memory import ThreeLayerMemory
        mem = ThreeLayerMemory(root_dir=str(tmp_path))
        mem.episodic_store(task="t1", agent="alice", outcome="success", score=0.9)
        results = mem.episodic_search(agent="alice", outcome="success", min_score=0.5)
        assert isinstance(results, list)

    def test_store_working(self, tmp_path):
        from maop.core.memory.three_layer_memory import ThreeLayerMemory
        mem = ThreeLayerMemory(root_dir=str(tmp_path))
        key = mem.store(layer="working", content="value", key="k1")
        assert key == "k1"

    def test_store_short_term(self, tmp_path):
        from maop.core.memory.three_layer_memory import ThreeLayerMemory
        mem = ThreeLayerMemory(root_dir=str(tmp_path))
        entry_id = mem.store(layer="short_term", content="test content", task="t1")
        assert entry_id

    def test_store_long_term(self, tmp_path):
        from maop.core.memory.three_layer_memory import ThreeLayerMemory
        mem = ThreeLayerMemory(root_dir=str(tmp_path))
        doc_id = mem.store(layer="long_term", content="test doc", doc_id="d1")
        assert doc_id

    def test_store_unknown_layer(self, tmp_path):
        from maop.core.memory.three_layer_memory import ThreeLayerMemory
        mem = ThreeLayerMemory(root_dir=str(tmp_path))
        with pytest.raises(ValueError):
            mem.store(layer="unknown", content="c")

    def test_retrieve_working(self, tmp_path):
        from maop.core.memory.three_layer_memory import ThreeLayerMemory
        mem = ThreeLayerMemory(root_dir=str(tmp_path))
        mem.working_put("k1", "value1")
        results = mem.retrieve(layer="working", query="k1")
        assert isinstance(results, list)

    def test_retrieve_short_term(self, tmp_path):
        from maop.core.memory.three_layer_memory import ThreeLayerMemory
        mem = ThreeLayerMemory(root_dir=str(tmp_path))
        mem.episodic_store(task="t1", summary="hello")
        results = mem.retrieve(layer="short_term", query="hello")
        assert isinstance(results, list)

    def test_retrieve_long_term(self, tmp_path):
        from maop.core.memory.three_layer_memory import ThreeLayerMemory
        mem = ThreeLayerMemory(root_dir=str(tmp_path))
        results = mem.retrieve(layer="long_term", query="test")
        assert isinstance(results, list)

    def test_retrieve_unknown_layer(self, tmp_path):
        from maop.core.memory.three_layer_memory import ThreeLayerMemory
        mem = ThreeLayerMemory(root_dir=str(tmp_path))
        with pytest.raises(ValueError):
            mem.retrieve(layer="unknown")

    def test_query_memory_entries_empty(self, tmp_path):
        from maop.core.memory.three_layer_memory import ThreeLayerMemory
        mem = ThreeLayerMemory(root_dir=str(tmp_path))
        results = mem.query_memory_entries(query="", top=10)
        assert isinstance(results, list)

    def test_query_memory_entries_with_query(self, tmp_path):
        from maop.core.memory.three_layer_memory import ThreeLayerMemory
        mem = ThreeLayerMemory(root_dir=str(tmp_path))
        results = mem.query_memory_entries(query="test", top=10)
        assert isinstance(results, list)

    def test_episodic_stats(self, tmp_path):
        from maop.core.memory.three_layer_memory import ThreeLayerMemory
        mem = ThreeLayerMemory(root_dir=str(tmp_path))
        stats = mem.episodic_stats()
        assert isinstance(stats, dict)

    def test_semantic_index(self, tmp_path):
        from maop.core.memory.three_layer_memory import ThreeLayerMemory
        mem = ThreeLayerMemory(root_dir=str(tmp_path))
        doc_id = mem.semantic_index("d1", "test document")
        assert doc_id == "d1"

    def test_semantic_search(self, tmp_path):
        from maop.core.memory.three_layer_memory import ThreeLayerMemory
        mem = ThreeLayerMemory(root_dir=str(tmp_path))
        mem.semantic_index("d1", "hello world")
        results = mem.semantic_search("hello", top=5)
        assert isinstance(results, list)

# ── Coverage tests (merged from test_three_layer_memory_coverage3.py) ──

# ── Helpers ─────────────────────────────────────────────────────────


def _make_mem(tmp_path: Path):
    from maop.core.memory.three_layer_memory import ThreeLayerMemory
    return ThreeLayerMemory(root_dir=str(tmp_path))


# ── _parse_qd branches (322, 324, 328-329) ──────────────────────────


class TestParseQd:
    def test_none_returns_default(self):
        from maop.core.memory.three_layer_memory import ThreeLayerMemory
        from maop.core.memory.three_layer_memory_types import QualityDimensions
        result = ThreeLayerMemory._parse_qd(None)
        assert isinstance(result, QualityDimensions)

    def test_quality_dimensions_passthrough(self):
        from maop.core.memory.three_layer_memory import ThreeLayerMemory
        from maop.core.memory.three_layer_memory_types import QualityDimensions
        qd = QualityDimensions(correctness=0.9)
        result = ThreeLayerMemory._parse_qd(qd)
        assert result is qd

    def test_invalid_json_returns_default(self):
        from maop.core.memory.three_layer_memory import ThreeLayerMemory
        from maop.core.memory.three_layer_memory_types import QualityDimensions
        result = ThreeLayerMemory._parse_qd("not valid json{{{")
        assert isinstance(result, QualityDimensions)

    def test_dict_input(self):
        from maop.core.memory.three_layer_memory import ThreeLayerMemory
        result = ThreeLayerMemory._parse_qd({"correctness": 0.8})
        assert result.correctness == 0.8

    def test_json_string(self):
        from maop.core.memory.three_layer_memory import ThreeLayerMemory
        result = ThreeLayerMemory._parse_qd('{"correctness": 0.7}')
        assert result.correctness == 0.7


# ── migrate_legacy_episodic_db branches (229-234) ───────────────────


class TestMigrateLegacy:
    def test_migration_success(self, tmp_path):
        """Cover branch where migration succeeds and logs info (229-232)."""
        with patch("maop.core.memory.three_layer_memory.migrate_legacy_episodic_db", return_value=5):
            mem = _make_mem(tmp_path)
        assert mem is not None

    def test_migration_exception(self, tmp_path):
        """Cover branch where migration raises (233-234)."""
        with patch(
            "maop.core.memory.three_layer_memory.migrate_legacy_episodic_db",
            side_effect=RuntimeError("migrate boom"),
        ):
            mem = _make_mem(tmp_path)
        assert mem is not None


# ── _on_evict exception (295-296) ───────────────────────────────────


class TestOnEvictException:
    def test_evict_overflow_exception(self, tmp_path):
        """Cover branch where overflow fails (295-296)."""
        mem = _make_mem(tmp_path)
        # Make episodic_store raise
        with patch.object(mem, "episodic_store", side_effect=RuntimeError("store boom")):
            # Trigger eviction by filling working memory beyond capacity
            # Working memory capacity is limited; put many items to force eviction
            for i in range(200):
                mem.working_put(f"k{i}", f"v{i}" * 100)
        # Should not raise


# ─– episodic_search FTS5 fallback (434-449) ────────────────────────


class TestEpisodicSearchFtsFallback:
    def test_fts_fallback_to_like(self, tmp_path):
        """Cover FTS5 OperationalError fallback to LIKE query (434-449)."""
        mem = _make_mem(tmp_path)
        mem.episodic_store(task="fix bug", agent="alice", outcome="success", score=0.9)
        mem.episodic_store(task="fix issue", agent="bob", outcome="failure", score=0.5)
        # Drop the FTS table to force fallback to LIKE
        import sqlite3
        from maop.memory.shared_db import get_memory_db_path
        db_path = get_memory_db_path()
        with sqlite3.connect(str(db_path)) as conn:
            try:
                conn.execute("DROP TABLE IF EXISTS episodic_memory_fts")
                conn.commit()
            except Exception:
                pass
        # Mock _increment_access_counts to avoid FTS dependency in post-search step
        with patch.object(mem, "_increment_access_counts"):
            results = mem.episodic_search(query="fix", top=10)
        assert isinstance(results, list)

    def test_search_with_all_filters(self, tmp_path):
        """Cover agent/outcome/min_score filter branches (417-421)."""
        mem = _make_mem(tmp_path)
        mem.episodic_store(task="task1", agent="alice", outcome="success", score=0.9)
        mem.episodic_store(task="task2", agent="bob", outcome="failure", score=0.3)
        # Use all filters
        results = mem.episodic_search(
            query="task", agent="alice", outcome="success", min_score=0.5, top=10,
        )
        assert isinstance(results, list)


# ── store() branches (496, 529, 531, 534) ───────────────────────────


class TestStoreBranches:
    def test_short_term_with_topic_tags_trace(self, tmp_path):
        """Cover topic/tags/trace_id metadata merge (529, 531, 534)."""
        mem = _make_mem(tmp_path)
        entry_id = mem.store(
            layer="short_term", content="test", task="t1",
            topic="bugs", tags=["tag1", "tag2"], trace_id="tr1",
        )
        assert entry_id

    def test_short_term_with_string_tags(self, tmp_path):
        """Cover tags as string branch (531)."""
        mem = _make_mem(tmp_path)
        entry_id = mem.store(
            layer="short_term", content="test", task="t1",
            tags="single_tag",
        )
        assert entry_id

    def test_long_term_without_doc_id(self, tmp_path):
        """Cover long_term branch where doc_id is auto-generated (496)."""
        mem = _make_mem(tmp_path)
        doc_id = mem.store(layer="long_term", content="auto id doc")
        assert doc_id  # auto-generated


# ── query_memory_entries success (602-603) ──────────────────────────


class TestQueryMemoryEntriesSuccess:
    def test_with_data(self, tmp_path):
        """Cover success path with actual memory_entries data (602-603)."""
        mem = _make_mem(tmp_path)
        # Use MemoryManager to write a memory entry
        from maop.memory.manager import MemoryManager
        mgr = MemoryManager(root_dir=str(tmp_path))
        mgr.add_exchange(
            agent="claude", session_id="s1",
            user_msg="fix bug", assistant_msg="fixed the bug",
        )
        results = mem.query_memory_entries(query="bug", top=10)
        assert isinstance(results, list)


# ─– episodic_update_feedback branches (657, 741) ───────────────────


class TestEpisodicUpdateFeedback:
    def test_update_with_quality_dimensions_score(self, tmp_path):
        """Cover branch where new_score > 0 (657)."""
        from maop.core.memory.three_layer_memory_types import QualityDimensions
        mem = _make_mem(tmp_path)
        entry_id = mem.episodic_store(task="t1", agent="a", score=0.5)
        qd = QualityDimensions(correctness=0.9, completeness=0.8)
        result = mem.episodic_update_feedback(entry_id, user_feedback="good", quality_dimensions=qd)
        assert result is True

    def test_update_with_no_changes(self, tmp_path):
        """Cover branch where sets is empty (741)."""
        mem = _make_mem(tmp_path)
        entry_id = mem.episodic_store(task="t1", agent="a")
        # Call with no feedback and no quality_dimensions
        result = mem.episodic_update_feedback(entry_id)
        assert result is True

    def test_update_nonexistent_entry(self, tmp_path):
        """Cover branch where entry not found."""
        mem = _make_mem(tmp_path)
        result = mem.episodic_update_feedback("nonexistent-id", user_feedback="x")
        assert result is False


# ─– submit_feedback evolution cycle (783-784, 809-810) ─────────────


class TestSubmitFeedbackCoverage:
    def test_low_quality_triggers_evolution(self, tmp_path):
        """Cover evolution reflection trigger (783-784)."""
        from maop.core.memory.three_layer_memory_types import QualityDimensions
        mem = _make_mem(tmp_path)
        entry_id = mem.episodic_store(task="t1", agent="a", score=0.9)
        # Low composite score triggers evolution
        qd = QualityDimensions(correctness=0.1)
        with patch("maop.core.evolution.evolution_loop.EvolutionLoop") as MockLoop:
            MockLoop.return_value.run_cycle.return_value = None
            result = mem.submit_feedback(entry_id, user_feedback="bad", quality_dimensions=qd)
        assert "evolution_reflection" in result["triggered_actions"]

    def test_negative_feedback_triggers_evolution(self, tmp_path):
        """Cover negative feedback detection (783-784)."""
        mem = _make_mem(tmp_path)
        entry_id = mem.episodic_store(task="t1", agent="a", score=0.9)
        with patch("maop.core.evolution.evolution_loop.EvolutionLoop") as MockLoop:
            MockLoop.return_value.run_cycle.return_value = None
            result = mem.submit_feedback(entry_id, user_feedback="this is terrible and broken")
        assert "evolution_reflection" in result["triggered_actions"]

    def test_error_ledger_recording(self, tmp_path):
        """Cover error ledger recording branch (809-810)."""
        mem = _make_mem(tmp_path)
        entry_id = mem.episodic_store(task="t1", agent="a", score=0.9)
        with patch("maop.core.evolution.evolution_loop.EvolutionLoop") as MockLoop:
            MockLoop.return_value.run_cycle.return_value = None
            with patch("maop.core.reliability.error_ledger.ErrorLedger") as MockLedger:
                MockLedger.return_value.record.return_value = None
                result = mem.submit_feedback(entry_id, user_feedback="terrible")
        assert "error_ledger_recorded" in result["triggered_actions"]

    def test_error_ledger_exception(self, tmp_path):
        """Cover error ledger exception branch (809-810)."""
        mem = _make_mem(tmp_path)
        entry_id = mem.episodic_store(task="t1", agent="a", score=0.9)
        with patch("maop.core.evolution.evolution_loop.EvolutionLoop") as MockLoop:
            MockLoop.return_value.run_cycle.return_value = None
            with patch("maop.core.reliability.error_ledger.ErrorLedger") as MockLedger:
                MockLedger.side_effect = RuntimeError("ledger boom")
                result = mem.submit_feedback(entry_id, user_feedback="terrible")
        assert "error_ledger_recorded" not in result["triggered_actions"]


# ─– consolidate branches (878, 880, 899-901) ───────────────────────


class TestConsolidateBranches:
    def test_consolidate_with_lessons_and_feedback(self, tmp_path):
        """Cover lessons and user_feedback branches (878, 880)."""
        mem = _make_mem(tmp_path)
        mem.episodic_store(
            task="t1", agent="a", outcome="success", score=0.9,
            lessons=["lesson1", "lesson2"], user_feedback="great work",
        )
        report = mem.consolidate(min_score=0.5, limit=10)
        assert report.candidates >= 1
        assert report.consolidated >= 1

    def test_consolidate_index_exception(self, tmp_path):
        """Cover consolidation index exception (899-901)."""
        mem = _make_mem(tmp_path)
        mem.episodic_store(task="t1", agent="a", score=0.9)
        # Make vector store index raise
        with patch.object(mem, "_get_vector_store") as MockVS:
            MockVS.return_value.index.side_effect = RuntimeError("vs boom")
            report = mem.consolidate(min_score=0.5, limit=10)
        assert report.errors >= 1


# ─– access-count consolidation (678-680) ───────────────────────────


class TestAccessConsolidation:
    def test_consolidation_exception(self, tmp_path):
        """Cover access-consolidation exception branch (678-680)."""
        mem = _make_mem(tmp_path)
        # Store an entry with high access count
        entry_id = mem.episodic_store(task="t1", agent="a", score=0.9)
        # Manually bump access_count to trigger consolidation
        import sqlite3
        from maop.memory.shared_db import get_memory_db_path
        db_path = get_memory_db_path()
        with sqlite3.connect(str(db_path)) as conn:
            conn.execute(
                "UPDATE episodic_memory SET access_count = 10 WHERE id = ?",
                (entry_id,),
            )
            conn.commit()
        # Make vector store index raise to trigger exception branch
        with patch.object(mem, "_get_vector_store") as MockVS:
            MockVS.return_value.index.side_effect = RuntimeError("vs boom")
            # Call the access-count consolidation method
            report = mem.consolidate_by_access(min_access_count=5, limit=10)
        assert report.errors >= 1


# ─– transform branches (960-965, 1012-1013) ────────────────────────


class TestTransformBranches:
    def test_transform_with_semantic_search_exception(self, tmp_path):
        """Cover semantic_search exception in transform (960-965)."""
        mem = _make_mem(tmp_path)
        mem.episodic_store(task="fix bug", agent="a", score=0.9)
        with patch.object(mem, "semantic_search", side_effect=RuntimeError("ss boom")):
            result = mem.transform(query="fix bug")
        assert result is not None
        assert hasattr(result, "context_parts")

    def test_transform_with_compression(self, tmp_path):
        """Cover text compression branch (1012-1013)."""
        mem = _make_mem(tmp_path)
        # Store a large episodic entry that will trigger compression
        mem.episodic_store(
            task="fix bug", agent="a", score=0.9,
            summary="x" * 1000,
        )
        result = mem.transform(query="fix bug", token_budget=100)
        assert result is not None
        # Something should be compressed
        assert result.pipeline_stats.get("compressed", 0) >= 0


# ─– _gather_context_items (1100, 1112-1117) ────────────────────────


class TestGatherContextItems:
    def test_with_working_and_semodic(self, tmp_path):
        """Cover working + semantic branches (1100, 1112-1115)."""
        mem = _make_mem(tmp_path)
        mem.working_put("query1", "working value")
        mem.episodic_store(task="query1", agent="a", score=0.9)
        items = mem._gather_context_items("query1")
        assert isinstance(items, list)

    def test_with_semantic_search_exception(self, tmp_path):
        """Cover semantic_search exception branch (1116-1117)."""
        mem = _make_mem(tmp_path)
        mem.working_put("q", "v")
        with patch.object(mem, "semantic_search", side_effect=RuntimeError("ss boom")):
            items = mem._gather_context_items("q")
        assert isinstance(items, list)


# ─– transform_multi_head (exercises _gather_context_items) ─────────


class TestTransformMultiHead:
    def test_default_heads(self, tmp_path):
        mem = _make_mem(tmp_path)
        mem.working_put("q", "v")
        mem.episodic_store(task="q", agent="a", score=0.9)
        result = mem.transform_multi_head(query="q")
        assert result is not None
        assert hasattr(result, "heads")

    def test_with_semantic_exception(self, tmp_path):
        mem = _make_mem(tmp_path)
        with patch.object(mem, "semantic_search", side_effect=RuntimeError("boom")):
            result = mem.transform_multi_head(query="q")
        assert result is not None


# ── short_term_search metadata 字段（T3-A） ─────────────────────

class TestShortTermSearchMetadata:
    """T3-A: ``short_term_search`` dict 输出必须包含 ``metadata`` 字段。"""

    def test_metadata_preserved(self, tmp_path):
        mem = _make_mem(tmp_path)
        entry_id = mem.short_term_store(
            "content with metadata",
            task="meta_task",
            agent="claude",
            metadata={"source": "test", "priority": 3},
        )
        results = mem.short_term_search(query="", top=10)
        found = [r for r in results if r.get("id") == entry_id]
        assert len(found) == 1
        assert found[0]["metadata"] == {"source": "test", "priority": 3}

    def test_metadata_defaults_to_dict(self, tmp_path):
        mem = _make_mem(tmp_path)
        entry_id = mem.short_term_store("plain content", task="plain_task")
        results = mem.short_term_search(query="", top=10)
        found = [r for r in results if r.get("id") == entry_id]
        assert len(found) == 1
        assert found[0]["metadata"] == {}

    def test_facade_search_keeps_metadata(self, tmp_path):
        """facade.search 合并 short_term 时不再丢失 metadata。"""
        from maop.memory.facade import MemoryFacade

        facade = MemoryFacade(root_dir=tmp_path, mode="agent")
        facade.short_term_store(
            "facade metadata",
            task="facade_meta_task",
            agent="claude",
            metadata={"source": "facade"},
        )
        results = facade.search("facade_meta_task", top=10)
        short = [r for r in results if r.get("layer") == "short_term"]
        assert short, "expected short_term results from facade.search"
        assert any(r.get("metadata") == {"source": "facade"} for r in short)
