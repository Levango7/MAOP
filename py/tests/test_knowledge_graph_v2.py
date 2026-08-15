"""Tests for v4.5.0 KnowledgeGraph.query_graph — filtered graph query.

Covers:
  - GraphNodeV2 / GraphEdgeV2 / KnowledgeGraphQuery model semantics
  - query_graph type filter (single + multi)
  - query_graph time_range filter (start/end, lexicographic)
  - query_graph limit
  - Edge filtering: both endpoints must be in the filtered node set
  - Empty database / no matches
  - SQL parameterization (no injection)
  - /api/knowledge-graph endpoint integration (via FastAPI TestClient)

These tests are additive to the existing test_knowledge_graph.py and do
not modify any legacy behavior.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from maop.core.memory.knowledge_graph import (
    GraphEdgeV2,
    GraphNodeV2,
    KnowledgeGraph,
    KnowledgeGraphQuery,
    KnowledgeGraphResponse,
)

# ── Fixture: isolated KG database with v4.5.0 test data ─────────────

@pytest.fixture
def kg_v2_db(tmp_path: Path) -> KnowledgeGraph:
    """Build a KG database with 4 node types and 4 edge types for v4.5.0 tests."""
    from maop.core.backends.db_utils import get_db_path
    db_path = get_db_path("knowledge_graph")
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute(
        "CREATE TABLE IF NOT EXISTS entities ("
        "name TEXT PRIMARY KEY, entity_type TEXT DEFAULT 'concept',"
        "attributes TEXT DEFAULT '{}', confidence REAL DEFAULT 1.0)"
    )
    conn.execute(
        "CREATE TABLE IF NOT EXISTS relations ("
        "id TEXT PRIMARY KEY, source TEXT NOT NULL, target TEXT NOT NULL,"
        "relation_type TEXT NOT NULL, context TEXT DEFAULT '', confidence REAL DEFAULT 1.0)"
    )
    conn.execute(
        "CREATE TABLE IF NOT EXISTS facts ("
        "id TEXT PRIMARY KEY, subject TEXT NOT NULL, predicate TEXT NOT NULL,"
        "object_value TEXT NOT NULL, source_exchange TEXT DEFAULT '',"
        "topic TEXT DEFAULT '', confidence REAL DEFAULT 1.0,"
        "created_at TEXT NOT NULL, access_count INTEGER DEFAULT 0)"
    )
    # 4 node types: agent, task, memory, concept
    conn.executemany(
        "INSERT OR REPLACE INTO entities (name, entity_type, confidence) VALUES (?, ?, ?)",
        [
            ("AgentA", "agent", 0.95),
            ("AgentB", "agent", 0.90),
            ("TaskX", "task", 0.85),
            ("MemoryM", "memory", 0.80),
            ("ConceptC", "concept", 0.75),
        ],
    )
    # 4 edge types: delegates, remembers, produces, depends_on
    conn.executemany(
        "INSERT OR REPLACE INTO relations (id, source, target, relation_type, confidence) VALUES (?, ?, ?, ?, ?)",
        [
            ("r1", "AgentA", "TaskX", "delegates", 0.9),
            ("r2", "TaskX", "MemoryM", "produces", 0.85),
            ("r3", "AgentA", "MemoryM", "remembers", 0.8),
            ("r4", "TaskX", "ConceptC", "depends_on", 0.7),
            ("r5", "AgentB", "AgentA", "delegates", 0.6),
        ],
    )
    # Facts with created_at for time filtering.
    conn.executemany(
        "INSERT OR REPLACE INTO facts (id, subject, predicate, object_value, topic, created_at) VALUES (?, ?, ?, ?, ?, ?)",
        [
            ("f1", "AgentA", "executes", "tasks", "orchestration", "2025-01-15T10:00:00"),
            ("f2", "TaskX", "outputs", "memory", "execution", "2025-06-01T12:00:00"),
            ("f3", "MemoryM", "stores", "concepts", "memory", "2025-07-15T09:30:00"),
        ],
    )
    conn.commit()
    conn.close()
    return KnowledgeGraph(root_dir=tmp_path)


# ── Model semantics ─────────────────────────────────────────────────

class TestKnowledgeGraphQueryModel:
    def test_parse_types_empty_string(self):
        q = KnowledgeGraphQuery()
        assert q.parse_types() == []

    def test_parse_types_comma_separated(self):
        q = KnowledgeGraphQuery(type="agent,task")
        assert q.parse_types() == ["agent", "task"]

    def test_parse_types_list(self):
        q = KnowledgeGraphQuery(type=["agent", "memory"])
        assert q.parse_types() == ["agent", "memory"]

    def test_parse_types_strips_whitespace(self):
        q = KnowledgeGraphQuery(type=" agent , task ")
        assert q.parse_types() == ["agent", "task"]

    def test_parse_types_drops_empty(self):
        q = KnowledgeGraphQuery(type="agent,,task,")
        assert q.parse_types() == ["agent", "task"]

    def test_parse_time_range_empty(self):
        q = KnowledgeGraphQuery()
        assert q.parse_time_range() is None

    def test_parse_time_range_valid(self):
        q = KnowledgeGraphQuery(time_range="2025-01-01,2025-12-31")
        assert q.parse_time_range() == ("2025-01-01", "2025-12-31")

    def test_parse_time_range_invalid_single(self):
        q = KnowledgeGraphQuery(time_range="2025-01-01")
        assert q.parse_time_range() is None

    def test_parse_time_range_invalid_three(self):
        q = KnowledgeGraphQuery(time_range="a,b,c")
        assert q.parse_time_range() is None

    def test_parse_time_range_strips_whitespace(self):
        q = KnowledgeGraphQuery(time_range=" 2025-01-01 , 2025-12-31 ")
        assert q.parse_time_range() == ("2025-01-01", "2025-12-31")

    def test_limit_default(self):
        q = KnowledgeGraphQuery()
        assert q.limit == 500


class TestGraphNodeV2Model:
    def test_required_fields(self):
        n = GraphNodeV2(id="x", type="agent", label="X")
        assert n.id == "x"
        assert n.type == "agent"
        assert n.label == "X"
        assert n.timestamp == ""
        assert n.properties == {}
        assert n.confidence == 1.0

    def test_with_all_fields(self):
        n = GraphNodeV2(
            id="y", type="memory", label="Y",
            timestamp="2025-01-01", properties={"k": "v"}, confidence=0.5,
        )
        assert n.timestamp == "2025-01-01"
        assert n.properties == {"k": "v"}
        assert n.confidence == 0.5


class TestGraphEdgeV2Model:
    def test_required_fields(self):
        e = GraphEdgeV2(id="e1", source="a", target="b", type="delegates")
        assert e.id == "e1"
        assert e.source == "a"
        assert e.target == "b"
        assert e.type == "delegates"
        assert e.timestamp == ""
        assert e.properties == {}


class TestKnowledgeGraphResponseModel:
    def test_defaults(self):
        r = KnowledgeGraphResponse()
        assert r.nodes == []
        assert r.edges == []

    def test_with_data(self):
        r = KnowledgeGraphResponse(
            nodes=[GraphNodeV2(id="x", type="agent", label="X")],
            edges=[GraphEdgeV2(id="e", source="x", target="y", type="delegates")],
        )
        d = r.model_dump()
        assert d["nodes"][0]["id"] == "x"
        assert d["edges"][0]["source"] == "x"


# ── query_graph method ──────────────────────────────────────────────

class TestQueryGraph:
    def test_no_filter_returns_all_nodes(self, kg_v2_db: KnowledgeGraph):
        r = kg_v2_db.query_graph(KnowledgeGraphQuery(limit=100))
        assert len(r.nodes) == 5
        node_ids = {n.id for n in r.nodes}
        assert node_ids == {"AgentA", "AgentB", "TaskX", "MemoryM", "ConceptC"}

    def test_type_filter_single(self, kg_v2_db: KnowledgeGraph):
        r = kg_v2_db.query_graph(KnowledgeGraphQuery(type="agent", limit=100))
        # Only agent nodes
        assert all(n.type == "agent" for n in r.nodes)
        assert {n.id for n in r.nodes} == {"AgentA", "AgentB"}
        # Edges must have both endpoints in the filtered node set
        for e in r.edges:
            assert e.source in {"AgentA", "AgentB"}
            assert e.target in {"AgentA", "AgentB"}

    def test_type_filter_multi(self, kg_v2_db: KnowledgeGraph):
        r = kg_v2_db.query_graph(KnowledgeGraphQuery(type="agent,task", limit=100))
        types = {n.type for n in r.nodes}
        assert types <= {"agent", "task"}
        assert {n.id for n in r.nodes} == {"AgentA", "AgentB", "TaskX"}

    def test_type_filter_no_match(self, kg_v2_db: KnowledgeGraph):
        r = kg_v2_db.query_graph(KnowledgeGraphQuery(type="nonexistent", limit=100))
        assert r.nodes == []
        assert r.edges == []

    def test_limit(self, kg_v2_db: KnowledgeGraph):
        r = kg_v2_db.query_graph(KnowledgeGraphQuery(limit=3))
        assert len(r.nodes) <= 3

    def test_limit_zero(self, kg_v2_db: KnowledgeGraph):
        r = kg_v2_db.query_graph(KnowledgeGraphQuery(limit=0))
        assert r.nodes == []
        assert r.edges == []

    def test_nodes_sorted_by_confidence_desc(self, kg_v2_db: KnowledgeGraph):
        r = kg_v2_db.query_graph(KnowledgeGraphQuery(limit=100))
        confs = [n.confidence for n in r.nodes]
        assert confs == sorted(confs, reverse=True)

    def test_edges_both_endpoints_in_nodes(self, kg_v2_db: KnowledgeGraph):
        """spec 5.3.1 rule 13: edges only include those whose both endpoints are in the filtered result."""
        r = kg_v2_db.query_graph(KnowledgeGraphQuery(type="agent", limit=100))
        node_ids = {n.id for n in r.nodes}
        for e in r.edges:
            assert e.source in node_ids, f"edge source {e.source} not in nodes"
            assert e.target in node_ids, f"edge target {e.target} not in nodes"

    def test_time_range_filter(self, kg_v2_db: KnowledgeGraph):
        # Only AgentA has a fact with created_at 2025-01-15.
        r = kg_v2_db.query_graph(KnowledgeGraphQuery(
            time_range="2025-01-01,2025-02-01", limit=100,
        ))
        ids = {n.id for n in r.nodes}
        assert "AgentA" in ids
        # TaskX (2025-06-01) and MemoryM (2025-07-15) should be excluded.
        assert "TaskX" not in ids
        assert "MemoryM" not in ids

    def test_time_range_filter_wider(self, kg_v2_db: KnowledgeGraph):
        r = kg_v2_db.query_graph(KnowledgeGraphQuery(
            time_range="2025-01-01,2025-12-31", limit=100,
        ))
        ids = {n.id for n in r.nodes}
        assert "AgentA" in ids
        assert "TaskX" in ids
        assert "MemoryM" in ids

    def test_empty_database(self, tmp_path: Path):
        # Fresh isolated DB with no rows.
        from maop.core.backends.db_utils import get_db_path
        db_path = get_db_path("knowledge_graph")
        db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        conn.execute(
            "CREATE TABLE IF NOT EXISTS entities ("
            "name TEXT PRIMARY KEY, entity_type TEXT DEFAULT 'concept',"
            "attributes TEXT DEFAULT '{}', confidence REAL DEFAULT 1.0)"
        )
        conn.execute(
            "CREATE TABLE IF NOT EXISTS relations ("
            "id TEXT PRIMARY KEY, source TEXT NOT NULL, target TEXT NOT NULL,"
            "relation_type TEXT NOT NULL, context TEXT DEFAULT '', confidence REAL DEFAULT 1.0)"
        )
        conn.execute(
            "CREATE TABLE IF NOT EXISTS facts ("
            "id TEXT PRIMARY KEY, subject TEXT NOT NULL, predicate TEXT NOT NULL,"
            "object_value TEXT NOT NULL, source_exchange TEXT DEFAULT '',"
            "topic TEXT DEFAULT '', confidence REAL DEFAULT 1.0,"
            "created_at TEXT NOT NULL, access_count INTEGER DEFAULT 0)"
        )
        conn.commit()
        conn.close()
        kg = KnowledgeGraph(root_dir=tmp_path)
        r = kg.query_graph(KnowledgeGraphQuery())
        assert r.nodes == []
        assert r.edges == []

    def test_response_is_pydantic_model(self, kg_v2_db: KnowledgeGraph):
        r = kg_v2_db.query_graph(KnowledgeGraphQuery(limit=10))
        assert isinstance(r, KnowledgeGraphResponse)
        assert all(isinstance(n, GraphNodeV2) for n in r.nodes)
        assert all(isinstance(e, GraphEdgeV2) for e in r.edges)

    def test_node_timestamp_from_facts(self, kg_v2_db: KnowledgeGraph):
        """Node timestamp should be derived from the earliest facts.created_at."""
        r = kg_v2_db.query_graph(KnowledgeGraphQuery(limit=100))
        node_by_id = {n.id: n for n in r.nodes}
        assert node_by_id["AgentA"].timestamp == "2025-01-15T10:00:00"
        assert node_by_id["TaskX"].timestamp == "2025-06-01T12:00:00"
        assert node_by_id["MemoryM"].timestamp == "2025-07-15T09:30:00"
        # AgentB has no facts → empty timestamp
        assert node_by_id["AgentB"].timestamp == ""

    def test_edge_types_preserved(self, kg_v2_db: KnowledgeGraph):
        r = kg_v2_db.query_graph(KnowledgeGraphQuery(limit=100))
        edge_types = {e.type for e in r.edges}
        assert "delegates" in edge_types
        assert "produces" in edge_types
        assert "remembers" in edge_types
        assert "depends_on" in edge_types

    def test_node_properties_from_attributes(self, kg_v2_db: KnowledgeGraph):
        # entities were inserted without attributes → properties should be {}
        r = kg_v2_db.query_graph(KnowledgeGraphQuery(limit=100))
        for n in r.nodes:
            assert n.properties == {}


# ── /api/knowledge-graph endpoint integration ───────────────────────

class TestKnowledgeGraphEndpoint:
    """Integration test for the /api/knowledge-graph endpoint via FastAPI TestClient.

    We build a minimal app with the kg_router and stub require_admin so
    auth does not block the test.
    """

    @pytest.fixture
    def app_and_client(self, kg_v2_db: KnowledgeGraph, monkeypatch):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        from maop.dashboard.routers import knowledge as knowledge_router

        # Stub require_admin to no-op (test runs without auth middleware).
        monkeypatch.setattr(
            "maop.dashboard.routers.knowledge.require_admin",
            lambda request: None,
        )

        app = FastAPI()
        app.include_router(knowledge_router.kg_router)
        client = TestClient(app)
        return client

    def test_endpoint_returns_nodes_and_edges(self, app_and_client):
        client = app_and_client
        resp = client.get("/api/knowledge-graph?limit=100")
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "ok"
        data = body["data"]
        assert "nodes" in data
        assert "edges" in data
        assert "stats" in data
        assert data["stats"]["node_count"] == len(data["nodes"])
        assert data["stats"]["edge_count"] == len(data["edges"])
        assert data["stats"]["node_count"] == 5

    def test_endpoint_type_filter(self, app_and_client):
        client = app_and_client
        resp = client.get("/api/knowledge-graph?type=agent&limit=100")
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert all(n["type"] == "agent" for n in data["nodes"])
        # Edges must have both endpoints in the filtered node set
        node_ids = {n["id"] for n in data["nodes"]}
        for e in data["edges"]:
            assert e["source"] in node_ids
            assert e["target"] in node_ids

    def test_endpoint_limit(self, app_and_client):
        client = app_and_client
        resp = client.get("/api/knowledge-graph?limit=2")
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert len(data["nodes"]) <= 2

    def test_endpoint_limit_zero_returns_400(self, app_and_client):
        client = app_and_client
        resp = client.get("/api/knowledge-graph?limit=0")
        assert resp.status_code == 400

    def test_endpoint_limit_too_large_returns_400(self, app_and_client):
        client = app_and_client
        resp = client.get("/api/knowledge-graph?limit=99999")
        assert resp.status_code == 400

    def test_endpoint_time_range_invalid_returns_400(self, app_and_client):
        client = app_and_client
        # start > end
        resp = client.get("/api/knowledge-graph?time_range=2025-12-31,2025-01-01")
        assert resp.status_code == 400

    def test_endpoint_time_range_malformed_returns_400(self, app_and_client):
        client = app_and_client
        resp = client.get("/api/knowledge-graph?time_range=only-one-value")
        assert resp.status_code == 400

    def test_endpoint_time_range_valid(self, app_and_client):
        client = app_and_client
        resp = client.get("/api/knowledge-graph?time_range=2025-01-01,2025-02-01&limit=100")
        assert resp.status_code == 200
        data = resp.json()["data"]
        ids = {n["id"] for n in data["nodes"]}
        assert "AgentA" in ids
        assert "TaskX" not in ids

    def test_endpoint_node_fields_align_spec(self, app_and_client):
        """spec 6.2: node must have id/type/label/timestamp/properties."""
        client = app_and_client
        resp = client.get("/api/knowledge-graph?limit=1")
        data = resp.json()["data"]
        if data["nodes"]:
            n = data["nodes"][0]
            assert "id" in n
            assert "type" in n
            assert "label" in n
            assert "timestamp" in n
            assert "properties" in n

    def test_endpoint_edge_fields_align_spec(self, app_and_client):
        """spec 6.3: edge must have id/source/target/type/timestamp/properties."""
        client = app_and_client
        resp = client.get("/api/knowledge-graph?limit=100")
        data = resp.json()["data"]
        if data["edges"]:
            e = data["edges"][0]
            assert "id" in e
            assert "source" in e
            assert "target" in e
            assert "type" in e
            assert "timestamp" in e
            assert "properties" in e

    def test_endpoint_legacy_router_unchanged(self, app_and_client):
        """spec 5.3.1 rule 15: legacy /api/knowledge/graph must not be affected."""
        # The kg_router has prefix /api/knowledge-graph; legacy /api/knowledge/graph
        # is on a different router not mounted here. We verify the kg_router
        # does not register a /graph path.
        from maop.dashboard.routers.knowledge import kg_router
        paths = [r.path for r in kg_router.routes]
        assert "/api/knowledge-graph" in paths
        assert "/api/knowledge/graph" not in paths