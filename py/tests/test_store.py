"""Tests for MAOP.memory.store — SQLite-backed persistent memory store with FTS5."""

from __future__ import annotations

from pathlib import Path

import pytest

from maop.memory.store import (
    FacetResult,
    MemoryEntry,
    MemoryStats,
    MemoryStore,
    SearchResult,
    TraceEntry,
    TrajectoryStep,
    _is_valid_id,
    _new_id,
    expand_keywords,
)

# ── Fixtures ──────────────────────────────────────────────────────

@pytest.fixture
def store(tmp_path: Path) -> MemoryStore:
    """A MemoryStore rooted in a temp directory."""
    return MemoryStore(root_dir=tmp_path)


@pytest.fixture
def populated_store(store: MemoryStore) -> MemoryStore:
    """A MemoryStore with a few entries for search/facet tests."""
    store.store(agent="claude", task="fix login timeout bug", content="login hangs after 30s", tags="bug,auth", topic="bug")
    store.store(agent="kimi", task="deploy new config system", content="deployed to prod", tags="deploy", topic="ops")
    store.store(agent="claude", task="write unit tests", content="added pytest tests", tags="test", topic="test")
    return store


# ── Model tests ───────────────────────────────────────────────────

class TestMemoryEntry:
    def test_default_factory_id(self):
        entry = MemoryEntry(agent="a", task="t")
        assert entry.id  # non-empty
        assert entry.topic == "general"
        assert entry.tags == []
        assert entry.exit_code == 0

    def test_timestamp_iso_format(self):
        entry = MemoryEntry(agent="a")
        # ISO format contains 'T'
        assert "T" in entry.timestamp


class TestTraceEntry:
    def test_defaults(self):
        t = TraceEntry()
        assert t.trace_id  # uuid hex
        assert t.status == "active"
        assert t.agents == []

    def test_with_values(self):
        t = TraceEntry(session_id="s1", task="do thing", agents=["a", "b"])
        assert t.session_id == "s1"
        assert t.agents == ["a", "b"]


class TestTrajectoryStep:
    def test_defaults(self):
        s = TrajectoryStep(trace_id="t1")
        assert s.trace_id == "t1"
        assert s.tool_name == ""
        assert s.duration_ms == 0


class TestSearchResult:
    def test_required_fields(self):
        r = SearchResult(id="x", agent="a", task="t")
        assert r.score == 0.0
        assert r.snippet == ""
        assert r.highlighted == ""


class TestFacetResult:
    def test_construction(self):
        f = FacetResult(facet="topic", value="bug", count=3)
        assert f.count == 3


# ── Helper function tests ─────────────────────────────────────────

class TestNewId:
    def test_format(self):
        eid = _new_id()
        # YYYYMMDD-HHmmss-<rand6>
        parts = eid.split("-")
        assert len(parts) == 3
        assert len(parts[0]) == 8  # date
        assert len(parts[1]) == 6  # time
        assert len(parts[2]) == 6  # rand

    def test_unique(self):
        ids = {_new_id() for _ in range(100)}
        assert len(ids) == 100


class TestIsValidId:
    def test_valid_alphanumeric(self):
        assert _is_valid_id("abc123")
        assert _is_valid_id("20260717-120000-abcdef")
        assert _is_valid_id("foo_bar-baz")

    def test_rejects_path_traversal(self):
        assert not _is_valid_id("../etc/passwd")
        assert not _is_valid_id("a/b")
        assert not _is_valid_id("a;b")
        assert not _is_valid_id("")

    def test_rejects_spaces(self):
        assert not _is_valid_id("has space")


class TestExpandKeywords:
    def test_returns_original_first(self):
        result = expand_keywords("登录问题")
        assert result[0] == "登录问题"

    def test_synonym_expansion(self):
        result = expand_keywords("登录")
        # "登录" should expand to include login, signin, etc.
        assert "login" in result
        assert "signin" in result

    def test_reverse_synonym(self):
        # "error" should pull in "错误"
        result = expand_keywords("error")
        assert "错误" in result

    def test_dedup_preserves_order(self):
        result = expand_keywords("error")
        # No duplicates
        assert len(result) == len(set(result))

    def test_no_synonym_match(self):
        result = expand_keywords("xyzrandomword")
        assert result == ["xyzrandomword"]


# ── MemoryStore initialization ────────────────────────────────────

class TestMemoryStoreInit:
    def test_init_creates_data_dir(self, tmp_path: Path):
        MemoryStore(root_dir=tmp_path)
        assert (tmp_path / "data").exists()
        # Unified DB mode (ADR-011): memory shares maop.db
        assert (tmp_path / "data" / "maop.db").exists()

    def test_fts5_flag_set(self, store: MemoryStore):
        # FTS5 is built into Python's sqlite3 on most platforms
        assert store._fts5_available in (True, False)
        assert store._initialized is True

    def test_find_root_fallback(self, tmp_path: Path, monkeypatch):
        # When no config/agents.yaml exists up the tree, find_project_root returns cwd
        from maop.core.db_utils import find_project_root
        monkeypatch.chdir(tmp_path)
        root = find_project_root()
        assert isinstance(root, Path)


# ── Store operation ───────────────────────────────────────────────

class TestStoreOperation:
    def test_store_returns_id(self, store: MemoryStore):
        eid = store.store(agent="claude", task="fix bug", content="content here")
        assert eid is not None
        assert isinstance(eid, str)

    def test_store_with_tags_string(self, store: MemoryStore):
        eid = store.store(agent="a", task="t", tags="bug,auth,urgent")
        assert eid is not None
        # Verify tags persisted (search by id)
        results = store.search(entry_id=eid)
        assert len(results) == 1
        assert "bug" in results[0].tags

    def test_store_with_tags_list(self, store: MemoryStore):
        eid = store.store(agent="a", task="t", tags=["bug", "auth"])
        results = store.search(entry_id=eid)
        assert len(results) == 1
        assert "auth" in results[0].tags

    def test_store_empty_tags_string(self, store: MemoryStore):
        eid = store.store(agent="a", task="t", tags="")
        assert eid is not None
        results = store.search(entry_id=eid)
        assert results[0].tags == ""

    def test_store_topic_override(self, store: MemoryStore):
        eid = store.store(agent="a", task="t", topic="custom_topic")
        results = store.search(entry_id=eid)
        assert results[0].topic == "custom_topic"

    def test_store_writes_wiki_json(self, store: MemoryStore):
        """Verify entry persists to SQLite (legacy wiki.json removed in v4 ADR-011)."""
        eid = store.store(agent="a", task="my task", content="my content")
        # Verify entry is persisted in SQLite via search
        results = store.search(entry_id=eid)
        assert len(results) == 1
        assert results[0].id == eid

    def test_store_writes_memory_json(self, store: MemoryStore):
        """Verify entry persists to SQLite (legacy memory.json removed in v4 ADR-011)."""
        eid = store.store(agent="a", task="my task")
        # Verify entry is persisted in SQLite via search
        results = store.search(entry_id=eid)
        assert len(results) == 1
        assert results[0].id == eid


# ── Search ────────────────────────────────────────────────────────

class TestSearch:
    def test_search_by_entry_id(self, populated_store: MemoryStore):
        # Store a known entry and search by ID
        eid = populated_store.store(agent="x", task="find me", content="unique content")
        results = populated_store.search(entry_id=eid)
        assert len(results) == 1
        assert results[0].id == eid
        assert results[0].agent == "x"

    def test_search_by_entry_id_not_found(self, store: MemoryStore):
        results = store.search(entry_id="nonexistent-id")
        assert results == []

    def test_search_no_query_returns_recent(self, populated_store: MemoryStore):
        results = populated_store.search(top=10)
        assert len(results) >= 3
        # All should be SearchResult instances
        assert all(isinstance(r, SearchResult) for r in results)

    def test_search_with_query(self, populated_store: MemoryStore):
        results = populated_store.search(query="login", top=5)
        assert len(results) >= 1
        # The login bug entry should be in results
        assert any("login" in r.task or "login" in r.snippet for r in results)

    def test_search_filter_by_agent(self, populated_store: MemoryStore):
        results = populated_store.search(agent="claude", top=10)
        assert all(r.agent == "claude" for r in results)
        assert len(results) >= 2

    def test_search_top_limit(self, populated_store: MemoryStore):
        results = populated_store.search(top=1)
        assert len(results) <= 1

    def test_search_highlight_flag(self, populated_store: MemoryStore):
        results = populated_store.search(query="login", highlight=True, top=5)
        # Should not raise; results may contain highlighted field
        assert isinstance(results, list)


# ── Facets ────────────────────────────────────────────────────────

class TestFacets:
    def test_facets_by_topic(self, populated_store: MemoryStore):
        facets = populated_store.facets(field="topic")
        assert len(facets) >= 1
        assert all(isinstance(f, FacetResult) for f in facets)
        # Counts should be positive
        assert all(f.count > 0 for f in facets)

    def test_facets_by_agent(self, populated_store: MemoryStore):
        facets = populated_store.facets(field="agent")
        agents = {f.value for f in facets}
        assert "claude" in agents
        assert "kimi" in agents

    def test_facets_invalid_field_defaults_to_topic(self, populated_store: MemoryStore):
        facets = populated_store.facets(field="invalid_field")
        assert all(f.facet == "topic" for f in facets)

    def test_facets_sorted_by_count_desc(self, populated_store: MemoryStore):
        facets = populated_store.facets(field="agent")
        counts = [f.count for f in facets]
        assert counts == sorted(counts, reverse=True)

    def test_facets_top_limit(self, populated_store: MemoryStore):
        facets = populated_store.facets(field="topic", top=1)
        assert len(facets) <= 1


# ── JSON search ───────────────────────────────────────────────────

class TestSearchJson:
    def test_search_json_match(self, store: MemoryStore):
        store.store(agent="a", task="t", content='{"type": "bug_report", "msg": "crash"}')
        results = store.search_json("$.type", "bug_report")
        assert len(results) >= 1

    def test_search_json_no_match(self, store: MemoryStore):
        store.store(agent="a", task="t", content='{"type": "feature"}')
        results = store.search_json("$.type", "bug_report")
        assert len(results) == 0

    def test_search_json_invalid_content(self, store: MemoryStore):
        store.store(agent="a", task="t", content="not json at all")
        results = store.search_json("$.type", "bug_report")
        assert results == []


# ── Trace ─────────────────────────────────────────────────────────

class TestTrace:
    def test_trace_create_new(self, store: MemoryStore):
        tid = store.trace(task="my task", agent="claude")
        assert tid  # non-empty

    def test_trace_with_explicit_id(self, store: MemoryStore):
        tid = store.trace(trace_id="fixed-trace-id", task="t", agent="a")
        assert tid == "fixed-trace-id"

    def test_trace_update_existing(self, store: MemoryStore):
        tid = store.trace(trace_id="t1", task="t", agent="a1")
        store.trace(trace_id=tid, agent="a2")
        traces = store._load_traces()
        t = next(tr for tr in traces if tr.trace_id == "t1")
        assert "a1" in t.agents
        assert "a2" in t.agents

    def test_load_traces_empty(self, store: MemoryStore):
        assert store._load_traces() == []


# ── Trajectory ────────────────────────────────────────────────────

class TestTrajectory:
    def test_trajectory_record(self, store: MemoryStore):
        sid = store.trajectory(trace_id="t1", agent="claude", task="step1", tool_name="bash", tool_output="ok")
        assert sid

    def test_get_trajectory(self, store: MemoryStore):
        store.trajectory(trace_id="t1", agent="a", task="s1", tool_name="t1", tool_output="o1")
        store.trajectory(trace_id="t1", agent="a", task="s2", tool_name="t2", tool_output="o2")
        steps = store.get_trajectory("t1")
        assert len(steps) == 2
        assert all(isinstance(s, TrajectoryStep) for s in steps)

    def test_get_trajectory_empty(self, store: MemoryStore):
        steps = store.get_trajectory("nonexistent")
        assert steps == []


# ── Inject ────────────────────────────────────────────────────────

class TestInject:
    def test_inject_with_steps(self, store: MemoryStore):
        store.trajectory(trace_id="t1", agent="claude", task="do thing", tool_output="result here")
        injected = store.inject("t1")
        assert "[Memory Context]" in injected
        assert "claude" in injected

    def test_inject_no_steps(self, store: MemoryStore):
        injected = store.inject("nonexistent")
        assert injected == ""

    def test_inject_top_limit(self, store: MemoryStore):
        for i in range(10):
            store.trajectory(trace_id="t1", agent="a", task=f"step{i}", tool_output=f"out{i}")
        injected = store.inject("t1", top=3)
        # Should only include last 3 steps
        assert "step9" in injected
        assert "step0" not in injected


# ── Stats ─────────────────────────────────────────────────────────

class TestStats:
    def test_stats_empty(self, store: MemoryStore):
        stats = store.stats()
        assert isinstance(stats, MemoryStats)
        assert stats.total_entries == 0
        assert stats.total_traces == 0

    def test_stats_with_data(self, populated_store: MemoryStore):
        stats = populated_store.stats()
        assert stats.total_entries >= 3
        assert "claude" in stats.by_agent
        assert stats.by_agent["claude"] >= 2

    def test_stats_timestamps(self, populated_store: MemoryStore):
        stats = populated_store.stats()
        assert stats.oldest <= stats.newest
        assert stats.oldest != ""


# ── Prune ─────────────────────────────────────────────────────────

class TestPrune:
    def test_prune_zero_ttl(self, populated_store: MemoryStore):
        pruned = populated_store.prune(ttl_days=0)
        assert pruned == []

    def test_prune_dry_run(self, populated_store: MemoryStore):
        pruned = populated_store.prune(ttl_days=30, dry_run=True)
        # Recent entries shouldn't be pruned
        assert isinstance(pruned, list)
        # Stats should still show all entries
        assert populated_store.stats().total_entries >= 3

    def test_prune_negative_ttl(self, populated_store: MemoryStore):
        pruned = populated_store.prune(ttl_days=-1)
        assert pruned == []

    def test_prune_old_entries(self, store: MemoryStore, tmp_path: Path):
        # Insert an entry, then manually backdate its timestamp
        eid = store.store(agent="a", task="old", content="old content")
        # Update timestamp to 100 days ago
        from datetime import datetime, timedelta, timezone
        old_ts = (datetime.now(timezone.utc) - timedelta(days=100)).isoformat()
        with store._connect() as conn:
            conn.execute("UPDATE memory_entries SET timestamp = ? WHERE id = ?", (old_ts, eid))
        pruned = store.prune(ttl_days=30, dry_run=False)
        assert eid in pruned
        # Entry should be gone
        assert store.search(entry_id=eid) == []
