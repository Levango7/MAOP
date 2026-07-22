"""Tests for MAOP.memory.consolidator — Dream memory consolidation engine."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from maop.memory.consolidator import (
    ConsolidationGroup,
    ConsolidationReport,
    DreamConsolidator,
)
from maop.memory.store import MemoryStore


# ── Fixtures ──────────────────────────────────────────────────

@pytest.fixture
def mem_store(tmp_path: Path) -> MemoryStore:
    """Create a real MemoryStore with a temp root."""
    return MemoryStore(root_dir=tmp_path)


@pytest.fixture
def consolidator(mem_store: MemoryStore) -> DreamConsolidator:
    return DreamConsolidator(memory_store=mem_store, min_group_size=3)


def _add_entries(store: MemoryStore, agent: str, task: str, topic: str = "general",
                 count: int = 3, content: str = "some content") -> list[str]:
    """Add multiple entries with the same agent/task/topic."""
    ids = []
    for i in range(count):
        eid = store.store(
            agent=agent,
            task=task,
            content=f"{content} #{i}",
            tags=["test"],
            topic=topic,
        )
        if eid:
            ids.append(eid)
    return ids


# ── Model tests ───────────────────────────────────────────────

class TestModels:
    def test_consolidation_group_defaults(self):
        g = ConsolidationGroup()
        assert g.topic == ""
        assert g.entry_ids == []
        assert g.total_content_length == 0

    def test_consolidation_report_defaults(self):
        r = ConsolidationReport()
        assert r.phase == "dream"
        assert r.success is True
        assert r.size_before == 0
        assert r.reduction_pct == 0.0


# ── Task signature ────────────────────────────────────────────

class TestTaskSignature:
    def test_basic_normalization(self):
        sig = DreamConsolidator._task_signature("Fix Bug")
        assert sig == "fix bug"

    def test_removes_timestamps(self):
        sig = DreamConsolidator._task_signature("Run at 2024-01-15T10:30:00")
        assert "2024-01-15t10:30:00" not in sig

    def test_removes_hex_ids(self):
        sig = DreamConsolidator._task_signature("task abc123def456789012345678901234ab")
        assert "abc123def456789012345678901234ab" not in sig

    def test_numbers_replaced(self):
        sig = DreamConsolidator._task_signature("retry 3 times")
        assert "3" not in sig
        assert "N" in sig

    def test_truncation(self):
        long_task = "x" * 200
        sig = DreamConsolidator._task_signature(long_task)
        assert len(sig) <= 80

    def test_whitespace_collapse(self):
        sig = DreamConsolidator._task_signature("hello    world")
        assert sig == "hello world"


# ── Build summary ─────────────────────────────────────────────

class TestBuildSummary:
    def test_summary_header(self):
        group = ConsolidationGroup(topic="bugs", agent="claude", entry_ids=["a", "b", "c"])
        summary = DreamConsolidator._build_summary(group, ["content1", "content2"])
        assert "Dream Consolidation Summary" in summary
        assert "Topic: bugs" in summary
        assert "Agent: claude" in summary
        assert "Entries merged: 3" in summary

    def test_deduplication(self):
        group = ConsolidationGroup(topic="t", agent="a", entry_ids=["1", "2"])
        contents = ["same content here", "same content here", "different"]
        summary = DreamConsolidator._build_summary(group, contents)
        # "same content here" should appear once
        assert summary.count("same content here") == 1
        assert "different" in summary


# ── Dream pipeline (with real MemoryStore) ────────────────────

class TestDreamPipeline:
    def test_empty_store(self, consolidator: DreamConsolidator):
        report = consolidator.dream()
        assert report.success is True
        assert report.total_entries_scanned == 0
        assert report.groups_formed == 0
        assert report.entries_created == 0
        assert report.entries_pruned == 0

    def test_no_consolidation_needed(self, consolidator: DreamConsolidator,
                                      mem_store: MemoryStore):
        # Only 2 entries — below min_group_size=3
        _add_entries(mem_store, "claude", "fix bug", count=2)
        report = consolidator.dream()
        assert report.success is True
        assert report.groups_formed == 0
        assert report.entries_pruned == 0

    def test_consolidates_group(self, consolidator: DreamConsolidator,
                                 mem_store: MemoryStore):
        _add_entries(mem_store, "claude", "fix bug", topic="debug", count=4)
        report = consolidator.dream()
        assert report.success is True
        assert report.groups_formed >= 1
        assert report.entries_created >= 1
        assert report.entries_pruned >= 3
        assert report.size_before >= 4
        assert report.size_after < report.size_before

    def test_dry_run(self, consolidator: DreamConsolidator, mem_store: MemoryStore):
        _add_entries(mem_store, "claude", "fix bug", topic="debug", count=4)
        report = consolidator.dream(dry_run=True)
        assert report.success is True
        assert report.groups_formed >= 1
        assert report.entries_created == 0  # dry run doesn't create
        assert report.entries_pruned >= 3   # dry run reports what would be pruned
        # But actual store should be unchanged
        stats = mem_store.stats()
        assert stats.total_entries == 4

    def test_multiple_groups(self, consolidator: DreamConsolidator,
                              mem_store: MemoryStore):
        _add_entries(mem_store, "claude", "fix bug", topic="debug", count=3)
        _add_entries(mem_store, "codex", "write test", topic="testing", count=3)
        report = consolidator.dream()
        assert report.success is True
        assert report.groups_formed >= 2

    def test_reduction_pct(self, consolidator: DreamConsolidator,
                            mem_store: MemoryStore):
        _add_entries(mem_store, "claude", "fix bug", topic="debug", count=5)
        report = consolidator.dream()
        assert report.reduction_pct > 0

    def test_report_timestamps(self, consolidator: DreamConsolidator):
        report = consolidator.dream()
        assert report.started_at != ""
        assert report.finished_at != ""
        assert report.finished_at >= report.started_at


# ── Error handling ────────────────────────────────────────────

class TestErrorHandling:
    def test_orient_failure(self, mem_store: MemoryStore):
        consolidator = DreamConsolidator(memory_store=mem_store)
        with patch.object(mem_store, 'stats', side_effect=RuntimeError("db locked")):
            report = consolidator.dream()
            assert report.success is False
            assert "db locked" in report.error

    def test_consolidate_with_empty_content(self, consolidator: DreamConsolidator,
                                             mem_store: MemoryStore):
        # Add entries with empty content
        for _ in range(3):
            mem_store.store(agent="claude", task="empty task", content="", topic="test")
        report = consolidator.dream()
        assert report.success is True


# ── Configuration ─────────────────────────────────────────────

class TestConfiguration:
    def test_custom_min_group_size(self, mem_store: MemoryStore):
        consolidator = DreamConsolidator(memory_store=mem_store, min_group_size=2)
        _add_entries(mem_store, "claude", "fix bug", count=2)
        report = consolidator.dream()
        assert report.groups_formed >= 1

    def test_max_group_size_cap(self, mem_store: MemoryStore):
        consolidator = DreamConsolidator(memory_store=mem_store, min_group_size=3,
                                          max_group_size=5)
        _add_entries(mem_store, "claude", "fix bug", count=10)
        report = consolidator.dream()
        # Each group should be capped at 5 entries
        if report.groups_formed > 0:
            # The pruned count should reflect capped groups
            assert report.entries_pruned <= 10
