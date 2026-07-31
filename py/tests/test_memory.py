"""Tests for MAOP.memory.store — SQLite-backed memory with synonym expansion."""

from __future__ import annotations

import contextlib
from pathlib import Path

import pytest

from maop.memory.store import (
    MemoryStore,
    _is_valid_id,
    _new_id,
    expand_keywords,
)


@pytest.fixture
def mem_store() -> MemoryStore:
    """Create a MemoryStore with a temp root directory."""
    import shutil
    import tempfile
    tmp = tempfile.mkdtemp(prefix="MAOP_mem_")
    (Path(tmp) / "data").mkdir(parents=True, exist_ok=True)
    store = MemoryStore(root_dir=tmp)
    yield store
    with contextlib.suppress(Exception):
        shutil.rmtree(tmp, ignore_errors=True)


class TestHelpers:
    def test_new_id_format(self):
        mid = _new_id()
        # Format: YYYYMMDD-HHMMSS-<rand6>
        parts = mid.split("-")
        assert len(parts) == 3
        assert len(parts[0]) == 8  # date
        assert len(parts[1]) == 6  # time
        assert len(parts[2]) == 6  # random

    def test_valid_id(self):
        assert _is_valid_id("20260712-120000-abcXYZ")
        assert not _is_valid_id("../../etc/passwd")
        assert not _is_valid_id("id with spaces")

    def test_expand_keywords_chinese(self):
        result = expand_keywords("登录失败")
        assert "login" in result
        assert "auth" in result

    def test_expand_keywords_english(self):
        result = expand_keywords("timeout error")
        assert "超时" in result
        assert "错误" in result

    def test_expand_keywords_no_match(self):
        result = expand_keywords("hello world")
        assert result == ["hello world"]


class TestStore:
    def test_store_basic(self, mem_store: MemoryStore):
        entry_id = mem_store.store(
            agent="claude", task="fix bug", content="fixed the null pointer",
            tags="bug,fix", topic="coding",
        )
        assert entry_id is not None
        assert _is_valid_id(entry_id)

    def test_store_creates_db(self, mem_store: MemoryStore):
        """SQLite DB file should exist after store."""
        mem_store.store(agent="claude", task="test task")
        assert mem_store._db_path.exists()

    def test_store_syncs_wiki(self, mem_store: MemoryStore):
        """T3-1: JSON dual-write removed; verify entry persisted in SQLite."""
        entry_id = mem_store.store(
            agent="claude", task="wiki task", content="some content",
        )
        mem_store._flush_json()  # no-op now, kept for backward compat
        rows = mem_store._query(
            "SELECT id, agent, task FROM memory_entries WHERE id = ?",
            (entry_id,),
        )
        assert len(rows) == 1, "Entry should be persisted in SQLite"
        assert rows[0]["id"] == entry_id
        assert rows[0]["agent"] == "claude"

    def test_store_syncs_memory_index(self, mem_store: MemoryStore):
        """T3-1: JSON dual-write removed; verify entry queryable via SQLite."""
        entry_id = mem_store.store(agent="claude", task="index task")
        mem_store._flush_json()
        rows = mem_store._query(
            "SELECT id FROM memory_entries WHERE id = ?",
            (entry_id,),
        )
        assert len(rows) == 1, "Entry should be queryable from SQLite"
        assert rows[0]["id"] == entry_id

    def test_store_tags_as_list(self, mem_store: MemoryStore):
        entry_id = mem_store.store(agent="claude", task="tagged", tags=["a", "b"])
        assert entry_id is not None


class TestSearch:
    def test_search_by_query(self, mem_store: MemoryStore):
        mem_store.store(agent="claude", task="fix login bug", content="login was broken")
        mem_store.store(agent="kimi", task="add feature", content="new feature added")

        results = mem_store.search(query="login", top=5)
        assert len(results) >= 1
        assert results[0].agent == "claude"

    def test_search_by_agent(self, mem_store: MemoryStore):
        mem_store.store(agent="claude", task="task1", content="c1")
        mem_store.store(agent="kimi", task="task2", content="c2")

        results = mem_store.search(agent="claude")
        assert all(r.agent == "claude" for r in results)

    def test_search_by_id(self, mem_store: MemoryStore):
        entry_id = mem_store.store(agent="claude", task="specific task", content="specific content")

        results = mem_store.search(entry_id=entry_id)
        assert len(results) == 1
        assert results[0].id == entry_id

    def test_search_no_results(self, mem_store: MemoryStore):
        results = mem_store.search(query="nonexistent_xyz")
        assert len(results) == 0

    def test_search_recent_entries(self, mem_store: MemoryStore):
        for i in range(5):
            mem_store.store(agent="claude", task=f"task {i}", content=f"content {i}")

        results = mem_store.search(top=3)
        assert len(results) == 3

    def test_search_synonym_expansion(self, mem_store: MemoryStore):
        mem_store.store(agent="claude", task="login issue", content="auth problem")
        # Search with Chinese synonym should find English content
        results = mem_store.search(query="登录", top=5)
        assert len(results) >= 1


class TestTrace:
    def test_create_trace(self, mem_store: MemoryStore):
        trace_id = mem_store.trace(task="test task", agent="claude")
        assert trace_id
        assert len(trace_id) == 32  # UUID hex

    def test_update_trace(self, mem_store: MemoryStore):
        trace_id = mem_store.trace(task="test", agent="claude")
        # Update with same trace_id
        result_id = mem_store.trace(trace_id=trace_id, agent="kimi")
        assert result_id == trace_id

        # Verify agent was added
        traces = mem_store._load_traces()
        trace = next(t for t in traces if t.trace_id == trace_id)
        assert "claude" in trace.agents
        assert "kimi" in trace.agents


class TestTrajectory:
    def test_record_trajectory(self, mem_store: MemoryStore):
        trace_id = "test-trace-123"
        step_id = mem_store.trajectory(
            trace_id=trace_id, agent="claude", task="write code",
            tool_name="editor", tool_output="code written",
        )
        assert step_id

    def test_get_trajectory(self, mem_store: MemoryStore):
        trace_id = "test-trace-456"
        mem_store.trajectory(trace_id=trace_id, agent="claude", task="step1")
        mem_store.trajectory(trace_id=trace_id, agent="kimi", task="step2")

        steps = mem_store.get_trajectory(trace_id)
        assert len(steps) == 2


class TestInject:
    def test_inject_context(self, mem_store: MemoryStore):
        trace_id = "inject-trace"
        mem_store.trajectory(trace_id=trace_id, agent="claude", task="plan", tool_output="plan done")
        mem_store.trajectory(trace_id=trace_id, agent="kimi", task="execute", tool_output="exec done")

        context = mem_store.inject(trace_id)
        assert "[Memory Context]" in context
        assert "claude" in context

    def test_inject_empty(self, mem_store: MemoryStore):
        context = mem_store.inject("nonexistent-trace")
        assert context == ""


class TestStats:
    def test_stats_empty(self, mem_store: MemoryStore):
        stats = mem_store.stats()
        assert stats.total_entries == 0
        assert stats.total_traces == 0

    def test_stats_with_data(self, mem_store: MemoryStore):
        mem_store.store(agent="claude", task="t1", topic="coding")
        mem_store.store(agent="kimi", task="t2", topic="search")
        mem_store.trace(task="test", agent="claude")

        stats = mem_store.stats()
        assert stats.total_entries == 2
        assert stats.total_traces == 1
        assert "claude" in stats.by_agent
        assert "coding" in stats.by_topic


class TestPrune:
    def test_prune_nothing(self, mem_store: MemoryStore):
        mem_store.store(agent="claude", task="recent")
        pruned = mem_store.prune(ttl_days=30)
        assert len(pruned) == 0

    def test_prune_dry_run(self, mem_store: MemoryStore):
        entry_id = mem_store.store(agent="claude", task="old")
        # Backdate the entry in SQLite
        import sqlite3
        conn = sqlite3.connect(str(mem_store._db_path))
        conn.execute(
            "UPDATE memory_entries SET timestamp = ? WHERE id = ?",
            ("2020-01-01T00:00:00+00:00", entry_id),
        )
        conn.commit()
        conn.close()

        pruned = mem_store.prune(ttl_days=30, dry_run=True)
        assert len(pruned) == 1
        # Entry should still exist (dry run)
        results = mem_store.search(entry_id=entry_id)
        assert len(results) == 1

    def test_prune_actual_delete(self, mem_store: MemoryStore):
        entry_id = mem_store.store(agent="claude", task="old")
        # Backdate the entry in SQLite
        import sqlite3
        conn = sqlite3.connect(str(mem_store._db_path))
        conn.execute(
            "UPDATE memory_entries SET timestamp = ? WHERE id = ?",
            ("2020-01-01T00:00:00+00:00", entry_id),
        )
        conn.commit()
        conn.close()

        pruned = mem_store.prune(ttl_days=30)
        assert len(pruned) == 1
        # Entry should be gone
        results = mem_store.search(entry_id=entry_id)
        assert len(results) == 0
