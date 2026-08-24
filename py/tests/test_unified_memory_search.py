"""Verify unified search: data written via /api/memory/store (ThreeLayerMemory)
can be found via /api/memory/search (now queries both tables).
"""
from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from maop.memory.manager import (
    ConsolidationTrigger,
    MemoryManager,
    MemoryManagerConfig,
)
from maop.memory.store import MemoryStore


class FakeEpisodicEntry:
    def __init__(self, task, agent="claude", outcome="success", score=0.9,
                 summary="", lessons=None, metadata=None, created_at=1000000.0):
        self.id = f"ep-{task[:8]}"
        self.task = task
        self.agent = agent
        self.outcome = outcome
        self.score = score
        self.lessons = lessons or []
        self.summary = summary or task
        self.metadata = metadata or {}
        self.created_at = created_at


class FakeEpisodicResult:
    def __init__(self, entry, weight=1.0):
        self.entry = entry
        self.retrieval_weight = weight


class FakeThreeLayerMemory:
    _store_data: list = []  # noqa: RUF012

    def __init__(self, root_dir=None, working_max=None, working_ttl=None, **kwargs):
        pass

    def episodic_search(self, query="", agent="", outcome="", min_score=0.0,
                        top=10, apply_decay=True):
        results = []
        for entry in FakeThreeLayerMemory._store_data:
            if agent and entry.agent != agent:
                continue
            if query and query.lower() not in entry.task.lower():
                continue
            results.append(FakeEpisodicResult(entry))
        return results[:top]

    def short_term_search(self, query="", top=10, agent=""):
        """T3: facade.short_term_search 转发到该别名（与真实底层输出形态一致：dict 列表）。"""
        results = self.episodic_search(query=query, agent=agent, top=top)
        return [
            {
                "id": r.entry.id,
                "agent": r.entry.agent,
                "task": r.entry.task,
                "metadata": r.entry.metadata or {},
                "outcome": r.entry.outcome,
                "score": r.entry.score,
                "created_at": getattr(r.entry, "created_at", None),
            }
            for r in results
        ]

    def store(self, layer, content, **kwargs):
        entry = FakeEpisodicEntry(
            task=kwargs.get("task") or content[:80],
            agent=kwargs.get("agent", ""),
            summary=content,
        )
        FakeThreeLayerMemory._store_data.append(entry)
        return entry.id

    def episodic_stats(self):
        total = len(FakeThreeLayerMemory._store_data)
        by_outcome: dict = {}
        score_sum = 0.0
        for e in FakeThreeLayerMemory._store_data:
            by_outcome[e.outcome] = by_outcome.get(e.outcome, 0) + 1
            score_sum += getattr(e, "score", 0.0) or 0.0
        return {
            "total": total,
            "by_outcome": by_outcome,
            "avg_score": round(score_sum / total, 3) if total else 0.0,
            "consolidated": 0,
            "unconsolidated": total,
        }

    def short_term_stats(self):
        """T3: facade.short_term_stats 转发到该别名（与 episodic_stats 输出一致）。"""
        return self.episodic_stats()


class FakeMemoryStore:
    def __init__(self, root_dir=None):
        pass

    def stats(self):
        return MagicMock(total_entries=0, model_dump=lambda: {"total_entries": 0})

    def search(self, query="", top=10, agent="", **kwargs):
        return []


class FakeVectorStore:
    def __init__(self, db_path=None):
        pass

    def count(self):
        return 0


@pytest.fixture
def setup(tmp_path, monkeypatch):
    FakeThreeLayerMemory._store_data = []
    monkeypatch.setattr("maop.dashboard.routers.state.MAOP_ROOT", tmp_path)
    monkeypatch.setattr("maop.dashboard.routers.memory.MAOP_ROOT", tmp_path)
    monkeypatch.setattr("maop.memory.store.MemoryStore", FakeMemoryStore)
    monkeypatch.setattr("maop.core.memory.three_layer_memory.ThreeLayerMemory", FakeThreeLayerMemory)
    monkeypatch.setattr("maop.core.memory.vector.VectorStore", FakeVectorStore)
    app = FastAPI()

    @app.middleware("http")
    async def _inject_admin(request, call_next):
        request.state.auth_roles = ["admin"]
        request.state.tenant_id = ""
        return await call_next(request)

    from maop.dashboard.routers.memory import router
    app.include_router(router)
    return TestClient(app)


class TestUnifiedSearch:
    def test_store_then_search_finds_it(self, setup):
        """Write via /api/memory/store, search via /api/memory/search."""
        c = setup
        resp = c.post("/api/memory/store", json={
            "layer": "episodic",
            "content": "Fix authentication timeout bug",
            "agent": "claude",
            "topic": "bugfix",
        })
        assert resp.status_code == 200, resp.text
        assert resp.json()["status"] == "ok", resp.text
        print("STORE OK, data:", FakeThreeLayerMemory._store_data)

        resp = c.get("/api/memory/search", params={"q": "authentication"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert data["count"] >= 1
        found = any("authentication" in r.get("snippet", "").lower()
                     or "authentication" in r.get("task", "").lower()
                     for r in data["results"])
        assert found, "Stored content not found in search results"

    def test_trace_includes_episodic(self, setup):
        """Trace endpoint should include episodic entries."""
        c = setup
        c.post("/api/memory/store", json={
            "layer": "episodic",
            "content": "Deploy to production",
            "agent": "gemini",
        })
        data = c.get("/api/memory/trace").json()
        agents = [t["agent"] for t in data["traces"]]
        assert "gemini" in agents

    def test_deep_includes_episodic_count(self, setup):
        """Deep stats should include episodic_count."""
        c = setup
        c.post("/api/memory/store", json={
            "layer": "episodic",
            "content": "Test entry",
            "agent": "claude",
        })
        data = c.get("/api/memory/deep").json()
        assert data["stats"]["episodic_count"] >= 1


# --- Merged from test_memory_manager_search_coverage3.py ---
# Coverage tests (round 3) for maop.memory.manager and maop.memory.search.
#
# Targets missing lines in:
# - manager.py: consolidate, _maybe_consolidate, knowledge_extractor/graph/
#   vector_search properties, extract_knowledge, query_knowledge,
#   semantic_search, query_episodic
# - search.py: FTS5 branches, regex fallback, vector supplement

# ── MemoryManager.consolidate ───────────────────────────────────────


class TestConsolidate:
    def test_consolidate_init_failure(self, tmp_path):
        """Cover DreamConsolidator init failure (304-306)."""
        mgr = MemoryManager(root_dir=str(tmp_path))
        with patch(
            "maop.memory.consolidator.DreamConsolidator",
            side_effect=ImportError("no consolidator"),
        ):
            result = mgr.consolidate()
        assert result is None

    def test_consolidate_success(self, tmp_path):
        """Cover successful consolidation (295-321)."""
        mgr = MemoryManager(root_dir=str(tmp_path))

        # Mock the consolidator
        mock_report = MagicMock()
        mock_report.started_at = "2024-01-01T00:00:00"
        mock_report.finished_at = "2024-01-01T00:01:00"
        mock_report.total_entries_scanned = 10
        mock_report.entries_pruned = 2
        mock_report.success = True
        mock_report.model_dump.return_value = {"success": True}

        mock_consolidator = MagicMock()
        mock_consolidator.dream.return_value = mock_report

        with patch(
            "maop.memory.consolidator.DreamConsolidator",
            return_value=mock_consolidator,
        ):
            result = mgr.consolidate()
        assert result is not None
        assert result["success"] is True

    def test_consolidate_dry_run(self, tmp_path):
        """Cover dry_run consolidation."""
        mgr = MemoryManager(root_dir=str(tmp_path))

        mock_report = MagicMock()
        mock_report.started_at = "2024-01-01T00:00:00"
        mock_report.finished_at = "2024-01-01T00:01:00"
        mock_report.total_entries_scanned = 5
        mock_report.entries_pruned = 0
        mock_report.success = True
        mock_report.model_dump.return_value = {"dry_run": True}

        mock_consolidator = MagicMock()
        mock_consolidator.dream.return_value = mock_report

        with patch(
            "maop.memory.consolidator.DreamConsolidator",
            return_value=mock_consolidator,
        ):
            result = mgr.consolidate(dry_run=True)
        # Dry run returns a report dict; verify it indicates dry_run mode
        assert result is not None
        assert result["dry_run"] is True
        mock_consolidator.dream.assert_called_once_with(dry_run=True)


# ── MemoryManager._maybe_consolidate ────────────────────────────────


class TestMaybeConsolidate:
    def test_below_threshold(self, tmp_path):
        """Cover case where entries < threshold (343-344)."""
        config = MemoryManagerConfig(
            consolidation=ConsolidationTrigger(entry_threshold=100)
        )
        mgr = MemoryManager(root_dir=str(tmp_path), config=config)
        # No entries → below threshold → should return without consolidating
        mgr._maybe_consolidate()

    def test_above_threshold_no_last_consolidation(self, tmp_path):
        """Cover case where entries >= threshold and no last consolidation (355-358)."""
        config = MemoryManagerConfig(
            consolidation=ConsolidationTrigger(entry_threshold=1)
        )
        mgr = MemoryManager(root_dir=str(tmp_path), config=config)

        # Add an entry to exceed threshold
        mgr._memory.store(
            agent="test", task="test task", content="test content",
        )

        # Mock consolidate to avoid actual consolidation
        with patch.object(mgr, "consolidate") as mock_cons:
            mgr._maybe_consolidate()
            mock_cons.assert_called_once()

    def test_above_threshold_recent_consolidation(self, tmp_path):
        """Cover case where last consolidation is recent (346-353)."""
        config = MemoryManagerConfig(
            consolidation=ConsolidationTrigger(
                entry_threshold=1, days_since_last=7
            )
        )
        mgr = MemoryManager(root_dir=str(tmp_path), config=config)

        # Add an entry to exceed threshold
        mgr._memory.store(
            agent="test", task="test task", content="test content",
        )

        # Set recent consolidation date
        from datetime import datetime, timezone
        mgr._last_consolidation = datetime.now(timezone.utc).isoformat()

        # Should not consolidate because recent
        with patch.object(mgr, "consolidate") as mock_cons:
            mgr._maybe_consolidate()
            mock_cons.assert_not_called()

    def test_above_threshold_old_consolidation(self, tmp_path):
        """Cover case where last consolidation is old (355-358)."""
        config = MemoryManagerConfig(
            consolidation=ConsolidationTrigger(
                entry_threshold=1, days_since_last=0
            )
        )
        mgr = MemoryManager(root_dir=str(tmp_path), config=config)

        # Add an entry to exceed threshold
        mgr._memory.store(
            agent="test", task="test task", content="test content",
        )

        # Set old consolidation date (10 days ago)
        from datetime import datetime, timedelta, timezone
        old_date = (datetime.now(timezone.utc) - timedelta(days=10)).isoformat()
        mgr._last_consolidation = old_date

        with patch.object(mgr, "consolidate") as mock_cons:
            mgr._maybe_consolidate()
            mock_cons.assert_called_once()

    def test_consolidate_exception(self, tmp_path):
        """Cover consolidation exception (357-358)."""
        config = MemoryManagerConfig(
            consolidation=ConsolidationTrigger(entry_threshold=1)
        )
        mgr = MemoryManager(root_dir=str(tmp_path), config=config)

        mgr._memory.store(
            agent="test", task="test task", content="test content",
        )

        with patch.object(mgr, "consolidate", side_effect=RuntimeError("boom")):
            # Should not raise
            mgr._maybe_consolidate()

    def test_invalid_last_consolidation_date(self, tmp_path):
        """Cover invalid last consolidation date (352-353)."""
        config = MemoryManagerConfig(
            consolidation=ConsolidationTrigger(
                entry_threshold=1, days_since_last=7
            )
        )
        mgr = MemoryManager(root_dir=str(tmp_path), config=config)

        mgr._memory.store(
            agent="test", task="test task", content="test content",
        )

        # Set invalid date
        mgr._last_consolidation = "not-a-date"

        with patch.object(mgr, "consolidate") as mock_cons:
            mgr._maybe_consolidate()
            mock_cons.assert_called_once()


# ── MemoryManager properties (knowledge_extractor, knowledge_graph, vector_search) ──


class TestLazyProperties:
    def test_knowledge_extractor_init_failure(self, tmp_path):
        """Cover KnowledgeExtractor init failure (410-411)."""
        mgr = MemoryManager(root_dir=str(tmp_path))
        with patch(
            "maop.core.memory.knowledge_extractor.KnowledgeExtractor",
            side_effect=ImportError("no extractor"),
        ):
            result = mgr.knowledge_extractor
        assert result is None

    def test_knowledge_graph_init_failure(self, tmp_path):
        """Cover KnowledgeGraph init failure (420-421)."""
        mgr = MemoryManager(root_dir=str(tmp_path))
        with patch(
            "maop.core.memory.knowledge_graph.KnowledgeGraph",
            side_effect=ImportError("no graph"),
        ):
            result = mgr.knowledge_graph
        assert result is None

    def test_vector_search_init_failure(self, tmp_path):
        """Cover VectorSearch init failure (430-431)."""
        mgr = MemoryManager(root_dir=str(tmp_path))
        with patch(
            "maop.memory.vector_search.VectorSearch",
            side_effect=ImportError("no vector search"),
        ):
            result = mgr.vector_search
        assert result is None

    def test_knowledge_extractor_cached(self, tmp_path):
        """Cover cached knowledge_extractor."""
        mgr = MemoryManager(root_dir=str(tmp_path))
        mock_extractor = MagicMock()
        mgr._knowledge_extractor = mock_extractor
        assert mgr.knowledge_extractor is mock_extractor

    def test_knowledge_graph_cached(self, tmp_path):
        """Cover cached knowledge_graph."""
        mgr = MemoryManager(root_dir=str(tmp_path))
        mock_graph = MagicMock()
        mgr._knowledge_graph = mock_graph
        assert mgr.knowledge_graph is mock_graph

    def test_vector_search_cached(self, tmp_path):
        """Cover cached vector_search."""
        mgr = MemoryManager(root_dir=str(tmp_path))
        mock_vs = MagicMock()
        mgr._vector_search = mock_vs
        assert mgr.vector_search is mock_vs


# ── MemoryManager.extract_knowledge ─────────────────────────────────


class TestExtractKnowledge:
    def test_no_extractor(self, tmp_path):
        """Cover case where knowledge_extractor is None (442-443)."""
        mgr = MemoryManager(root_dir=str(tmp_path))
        mgr._knowledge_extractor = None
        with patch.object(
            type(mgr), "knowledge_extractor", None
        ):
            result = mgr.extract_knowledge("user", "assistant")
        assert result is None

    def test_extract_success(self, tmp_path):
        """Cover successful knowledge extraction (444-448)."""
        mgr = MemoryManager(root_dir=str(tmp_path))
        mock_extractor = MagicMock()
        mock_extractor.extract_from_exchange.return_value = {"entities": ["e1"]}
        mock_extractor.store_extraction.return_value = {"stored": 1}
        mgr._knowledge_extractor = mock_extractor

        result = mgr.extract_knowledge("user msg", "assistant msg", topic="test")
        assert result == {"stored": 1}

    def test_extract_exception(self, tmp_path):
        """Cover extraction exception (449-451)."""
        mgr = MemoryManager(root_dir=str(tmp_path))
        mock_extractor = MagicMock()
        mock_extractor.extract_from_exchange.side_effect = RuntimeError("boom")
        mgr._knowledge_extractor = mock_extractor

        result = mgr.extract_knowledge("user", "assistant")
        assert result is None


# ── MemoryManager.query_knowledge ───────────────────────────────────


class TestQueryKnowledge:
    def test_no_graph(self, tmp_path):
        """Cover case where knowledge_graph is None (460-461)."""
        mgr = MemoryManager(root_dir=str(tmp_path))
        mgr._knowledge_graph = None
        with patch.object(
            type(mgr), "knowledge_graph", None
        ):
            result = mgr.query_knowledge("entity")
        assert result == ""

    def test_query_success(self, tmp_path):
        """Cover successful knowledge query (462-465)."""
        mgr = MemoryManager(root_dir=str(tmp_path))
        mock_graph = MagicMock()
        mock_graph.build_context.return_value = "context text"
        mgr._knowledge_graph = mock_graph

        result = mgr.query_knowledge("entity", max_depth=3)
        assert result == "context text"

    def test_query_exception(self, tmp_path):
        """Cover knowledge query exception (466-468)."""
        mgr = MemoryManager(root_dir=str(tmp_path))
        mock_graph = MagicMock()
        mock_graph.build_context.side_effect = RuntimeError("boom")
        mgr._knowledge_graph = mock_graph

        result = mgr.query_knowledge("entity")
        assert result == ""


# ── MemoryManager.semantic_search ───────────────────────────────────


class TestSemanticSearch:
    def test_no_vector_search(self, tmp_path):
        """Cover case where vector_search is None (472-473)."""
        mgr = MemoryManager(root_dir=str(tmp_path))
        mgr._vector_search = None
        with patch.object(
            type(mgr), "vector_search", None
        ):
            result = mgr.semantic_search("query")
        assert result == []

    def test_search_success(self, tmp_path):
        """Cover successful semantic search (474-476)."""
        mgr = MemoryManager(root_dir=str(tmp_path))
        mock_vs = MagicMock()
        mock_result = MagicMock()
        mock_result.model_dump.return_value = {"id": "1", "score": 0.9}
        mock_vs.search.return_value = [mock_result]
        mgr._vector_search = mock_vs

        result = mgr.semantic_search("query", top=5)
        assert len(result) == 1
        assert result[0]["id"] == "1"

    def test_search_exception(self, tmp_path):
        """Cover semantic search exception (477-479)."""
        mgr = MemoryManager(root_dir=str(tmp_path))
        mock_vs = MagicMock()
        mock_vs.search.side_effect = RuntimeError("boom")
        mgr._vector_search = mock_vs

        result = mgr.semantic_search("query")
        assert result == []


# ── MemoryManager.query_episodic ────────────────────────────────────


class TestQueryEpisodic:
    def test_query_episodic_empty(self, tmp_path):
        """Cover query_episodic with no data (505-512)."""
        mgr = MemoryManager(root_dir=str(tmp_path))
        result = mgr.query_episodic()
        assert result == []

    def test_query_episodic_with_data(self, tmp_path):
        """Cover query_episodic with data and JSON deserialization (514-521)."""
        mgr = MemoryManager(root_dir=str(tmp_path))

        # Insert test data into episodic_memory table
        from maop.core.backends.db_utils import sqlite_connect
        from maop.memory.shared_db import get_memory_db_path

        db_path = get_memory_db_path()
        db_path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite_connect(db_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS episodic_memory (
                    id TEXT PRIMARY KEY,
                    task TEXT,
                    agent TEXT,
                    outcome TEXT,
                    score REAL,
                    summary TEXT,
                    user_feedback TEXT,
                    quality_dimensions TEXT,
                    lessons TEXT,
                    key_decisions TEXT,
                    files_touched TEXT,
                    metadata TEXT,
                    created_at TEXT
                )
            """)
            conn.execute("""
                INSERT INTO episodic_memory (
                    id, task, agent, outcome, score, summary, user_feedback,
                    quality_dimensions, lessons, key_decisions, files_touched,
                    metadata, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                "e1", "test task", "test agent", "success", 0.9, "summary",
                "feedback", json.dumps({"correctness": 0.9}),
                json.dumps(["lesson1"]), json.dumps(["dec1"]),
                json.dumps(["file1.py"]), json.dumps({"key": "val"}),
                "2024-01-01T00:00:00"
            ))

        result = mgr.query_episodic()
        assert len(result) == 1
        assert result[0]["task"] == "test task"
        assert result[0]["lessons"] == ["lesson1"]
        assert result[0]["quality_dimensions"] == {"correctness": 0.9}

    def test_query_episodic_with_query(self, tmp_path):
        """Cover query_episodic with search query."""
        mgr = MemoryManager(root_dir=str(tmp_path))

        from maop.core.backends.db_utils import sqlite_connect
        from maop.memory.shared_db import get_memory_db_path

        db_path = get_memory_db_path()
        db_path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite_connect(db_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS episodic_memory (
                    id TEXT PRIMARY KEY,
                    task TEXT,
                    agent TEXT,
                    outcome TEXT,
                    score REAL,
                    summary TEXT,
                    user_feedback TEXT,
                    quality_dimensions TEXT,
                    lessons TEXT,
                    key_decisions TEXT,
                    files_touched TEXT,
                    metadata TEXT,
                    created_at TEXT
                )
            """)
            conn.execute("""
                INSERT INTO episodic_memory (
                    id, task, agent, outcome, score, summary, user_feedback,
                    quality_dimensions, lessons, key_decisions, files_touched,
                    metadata, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                "e1", "auth bug fix", "agent", "success", 1.0, "fixed auth",
                "", "{}", "[]", "[]", "[]", "{}", "2024-01-01T00:00:00"
            ))

        result = mgr.query_episodic("auth")
        assert len(result) == 1
        assert "auth" in result[0]["task"]


# ── MemoryManager.prune_expired and stats ───────────────────────────


class TestPruneAndStats:
    def test_prune_expired(self, tmp_path):
        """Cover prune_expired method."""
        mgr = MemoryManager(root_dir=str(tmp_path))
        result = mgr.prune_expired()
        assert isinstance(result, int)

    def test_stats(self, tmp_path):
        """Cover stats method."""
        mgr = MemoryManager(root_dir=str(tmp_path))
        stats = mgr.stats()
        assert "short_term_entries" in stats
        assert "short_term_traces" in stats
        assert "by_agent" in stats
        assert "by_topic" in stats
        assert "last_consolidation" in stats


# ── MemorySearch coverage ───────────────────────────────────────────


class TestMemorySearchCoverage:
    def _create_search(self, tmp_path):
        """Helper to create a MemoryStore instance (inherits SearchMixin) with data."""
        store = MemoryStore(root_dir=str(tmp_path))
        # Store some entries
        store.store(
            agent="agent1", task="fix auth bug",
            content="Fixed authentication bug in login.py",
            tags=["bug", "auth"], topic="debugging",
        )
        store.store(
            agent="agent2", task="deploy to prod",
            content="Deployed version 1.0 to production",
            tags=["deploy"], topic="deployment",
        )
        return store

    def test_search_no_query_with_filters(self, tmp_path):
        """Cover search with no query but with agent/trace_id/since/until filters (107-134)."""
        search = self._create_search(tmp_path)
        results = search.search(query="", agent="agent1")
        assert len(results) >= 1
        assert all(r.agent == "agent1" for r in results)

    def test_search_no_query_all_filters(self, tmp_path):
        """Cover search with all filters set."""
        search = self._create_search(tmp_path)
        results = search.search(
            query="", agent="agent1", trace_id="t1",
            since="2020-01-01", until="2030-01-01",
        )
        # trace_id won't match but should not raise
        assert isinstance(results, list)

    def test_search_fts5_with_agent_filter(self, tmp_path):
        """Cover FTS5 search with agent filter (155-156)."""
        search = self._create_search(tmp_path)
        results = search.search(query="auth", agent="agent1")
        assert len(results) >= 1

    def test_search_fts5_with_trace_filter(self, tmp_path):
        """Cover FTS5 search with trace_id filter (158-159)."""
        search = self._create_search(tmp_path)
        results = search.search(query="auth", trace_id="nonexistent")
        assert isinstance(results, list)

    def test_search_fts5_with_date_filters(self, tmp_path):
        """Cover FTS5 search with since/until filters (161-165)."""
        search = self._create_search(tmp_path)
        results = search.search(
            query="auth", since="2020-01-01", until="2030-01-01",
        )
        assert len(results) >= 1

    def test_search_fts5_no_highlight(self, tmp_path):
        """Cover FTS5 search with highlight=False (185)."""
        search = self._create_search(tmp_path)
        results = search._search_fts5(query="auth", highlight=False)
        assert len(results) >= 1

    def test_search_regex_with_filters(self, tmp_path):
        """Cover regex fallback with filters (232-242)."""
        search = self._create_search(tmp_path)
        results = search._search_regex(
            query="auth", agent="agent1",
            trace_id="t1", since="2020-01-01", until="2030-01-01",
        )
        assert isinstance(results, list)

    def test_search_fts5_fallback_to_regex(self, tmp_path):
        """Cover FTS5 failure → regex fallback (213-215)."""
        search = self._create_search(tmp_path)
        # Mock _query to raise to trigger FTS5 fallback
        original_query = search._query
        call_count = [0]
        def failing_query(sql, params=None):
            call_count[0] += 1
            if "memory_fts" in sql:
                raise RuntimeError("FTS5 broken")
            return original_query(sql, params)
        search._query = failing_query
        results = search.search(query="auth")
        # Should fall back to regex and still return results
        assert isinstance(results, list)
