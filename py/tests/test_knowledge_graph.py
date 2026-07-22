"""Tests for KnowledgeGraph — entity-relation graph traversal and inference."""
from __future__ import annotations

import sqlite3

from pathlib import Path

import pytest

from maop.core.knowledge_graph import GraphEdge, GraphNode, KnowledgeGraph, Subgraph


@pytest.fixture
def kg_db(tmp_path: Path) -> KnowledgeGraph:
    from maop.core.db_utils import get_db_path
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
    conn.executemany(
        "INSERT OR IGNORE INTO entities (name, entity_type, confidence) VALUES (?, ?, ?)",
        [
            ("AuthService", "service", 0.95),
            ("UserService", "service", 0.90),
            ("DatabasePool", "infrastructure", 0.85),
            ("CacheLayer", "infrastructure", 0.80),
        ],
    )
    conn.executemany(
        "INSERT OR IGNORE INTO relations (id, source, target, relation_type, confidence) VALUES (?, ?, ?, ?, ?)",
        [
            ("r1", "AuthService", "UserService", "uses", 0.9),
            ("r2", "UserService", "DatabasePool", "depends_on", 0.85),
            ("r3", "AuthService", "CacheLayer", "uses", 0.8),
            ("r4", "DatabasePool", "CacheLayer", "extends", 0.7),
        ],
    )
    conn.executemany(
        "INSERT OR IGNORE INTO facts (id, subject, predicate, object_value, topic, created_at) VALUES (?, ?, ?, ?, ?, ?)",
        [
            ("f1", "AuthService", "authenticates", "users", "auth", "2025-01-01"),
            ("f2", "UserService", "manages", "user records", "user", "2025-01-01"),
        ],
    )
    conn.commit()
    conn.close()
    return KnowledgeGraph(root_dir=tmp_path)


class TestGraphNode:
    def test_defaults(self):
        node = GraphNode()
        assert node.name == ""
        assert node.entity_type == "concept"
        assert node.in_degree == 0
        assert node.out_degree == 0

    def test_with_values(self):
        node = GraphNode(name="Foo", entity_type="class", in_degree=3, out_degree=1)
        assert node.name == "Foo"
        assert node.in_degree == 3


class TestGraphEdge:
    def test_defaults(self):
        edge = GraphEdge()
        assert edge.source == ""
        assert edge.relation_type == "related_to"
        assert edge.confidence == 1.0

    def test_with_values(self):
        edge = GraphEdge(source="A", target="B", relation_type="uses")
        assert edge.source == "A"
        assert edge.target == "B"


class TestSubgraph:
    def test_defaults(self):
        sg = Subgraph()
        assert sg.nodes == []
        assert sg.edges == []
        assert sg.center == ""


class TestKnowledgeGraph:
    def test_get_neighbors_direct(self, kg_db: KnowledgeGraph):
        sg = kg_db.get_neighbors("AuthService", max_depth=1)
        assert sg.center == "AuthService"
        assert len(sg.nodes) >= 1
        assert any(n.name == "UserService" for n in sg.nodes)
        assert any(n.name == "CacheLayer" for n in sg.nodes)

    def test_get_neighbors_depth2(self, kg_db: KnowledgeGraph):
        sg = kg_db.get_neighbors("AuthService", max_depth=2)
        assert any(n.name == "DatabasePool" for n in sg.nodes)

    def test_get_neighbors_outgoing_only(self, kg_db: KnowledgeGraph):
        sg = kg_db.get_neighbors("AuthService", direction="outgoing", max_depth=1)
        for edge in sg.edges:
            assert edge.source == "AuthService"

    def test_get_neighbors_incoming(self, kg_db: KnowledgeGraph):
        sg = kg_db.get_neighbors("DatabasePool", direction="incoming", max_depth=1)
        assert any(e.target == "DatabasePool" for e in sg.edges)

    def test_find_path_exists(self, kg_db: KnowledgeGraph):
        path = kg_db.find_path("AuthService", "DatabasePool")
        assert len(path) >= 1
        assert path[0].source == "AuthService"

    def test_find_path_same_node(self, kg_db: KnowledgeGraph):
        path = kg_db.find_path("AuthService", "AuthService")
        assert path == []

    def test_find_path_no_path(self, kg_db: KnowledgeGraph):
        path = kg_db.find_path("DatabasePool", "AuthService", max_depth=2)
        assert path == []

    def test_build_context(self, kg_db: KnowledgeGraph):
        ctx = kg_db.build_context("AuthService")
        assert "AuthService" in ctx
        assert "uses" in ctx or "depends_on" in ctx

    def test_build_context_empty(self, kg_db: KnowledgeGraph):
        ctx = kg_db.build_context("NonExistent")
        assert ctx == ""

    def test_get_subgraph_by_topic(self, kg_db: KnowledgeGraph):
        sg = kg_db.get_subgraph_by_topic("auth")
        assert isinstance(sg, Subgraph)

    def test_export_for_visualization(self, kg_db: KnowledgeGraph):
        data = kg_db.export_for_visualization()
        assert "nodes" in data
        assert "edges" in data
        assert len(data["nodes"]) >= 1

    def test_infer_transitive(self, kg_db: KnowledgeGraph):
        inferred = kg_db._infer_transitive("AuthService")
        assert isinstance(inferred, list)
        if inferred:
            assert any("uses" in item or "depends_on" in item for item in inferred)

    def test_empty_database(self, tmp_path: Path):
        db_path = tmp_path / "data" / "knowledge.db"
        db_path.parent.mkdir(parents=True, exist_ok=True)
        kg = KnowledgeGraph(root_dir=tmp_path)
        sg = kg.get_neighbors("Anything", max_depth=1)
        assert sg.center == "Anything"
        assert sg.nodes == []
        assert sg.edges == []