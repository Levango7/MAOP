"""HTTP contracts for ``GET /api/graph/stats`` and ``GET /api/knowledge-graph``.

Graph stats: the route converts every service failure into a 500 ErrorSchema,
so the success payload is exactly the four keys ``get_graph_stats()`` emits —
declaring ``status``/``error`` on the model would pad two keys the producer
never sends. The fake bridge only replaces the external graph backend; the
degree arithmetic runs for real.

Knowledge graph: rows are seeded into the per-test isolated SQLite file and the
response is compared against ``query_knowledge_graph_v2()`` itself. Row types
reuse ``GraphNodeV2``/``GraphEdgeV2`` from the producer.
"""

from __future__ import annotations

import json
import sqlite3
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from maop.core.backends.db_utils import get_db_path
from maop.core.memory.knowledge_extractor import KnowledgeExtractor
from maop.dashboard.routers import data as data_router
from maop.dashboard.routers import knowledge as knowledge_router
from maop.dashboard.routers.data_schemas import GraphStatsPayload
from maop.dashboard.routers.knowledge_schemas import KnowledgeGraphPayload
from maop.dashboard.services import data_service, knowledge_service

ERROR_KEYS = {"status", "error", "code", "detail", "request_id"}
NODE_KEYS = {"id", "type", "label", "timestamp", "properties", "confidence"}
EDGE_KEYS = {"id", "source", "target", "type", "timestamp", "properties", "confidence"}


class _FakeBridge:
    def __init__(self, nodes: list[dict[str, Any]], edges: list[dict[str, Any]]) -> None:
        self._nodes = nodes
        self._edges = edges

    async def graph_nodes(self) -> list[dict[str, Any]]:
        return self._nodes

    async def graph_edges(self) -> list[dict[str, Any]]:
        return self._edges


class _BrokenBridge:
    async def graph_nodes(self) -> list[dict[str, Any]]:
        raise RuntimeError("bridge offline")

    async def graph_edges(self) -> list[dict[str, Any]]:
        raise RuntimeError("bridge offline")


def _make_app(roles: list[str] | None = None) -> TestClient:
    app = FastAPI()

    @app.middleware("http")
    async def _inject_auth(request, call_next):
        request.state.auth_roles = roles if roles is not None else ["admin"]
        request.state.auth_identity = "tester"
        request.state.tenant_id = ""
        return await call_next(request)

    app.include_router(data_router.router)
    app.include_router(knowledge_router.kg_router)
    return TestClient(app)


@pytest.fixture
def client() -> TestClient:
    return _make_app()


def _install_bridge(monkeypatch, bridge: Any) -> None:
    monkeypatch.setattr(data_service.state, "get_bridge", lambda: bridge)


# ── graph stats ───────────────────────────────────────────────────


def test_graph_stats_is_exactly_the_four_producer_keys(client, monkeypatch) -> None:
    _install_bridge(
        monkeypatch,
        _FakeBridge(
            nodes=[{"id": "a"}, {"id": "b"}, {"id": "c"}],
            edges=[
                {"source": "a", "target": "b"},
                {"source": "a", "target": "c"},
            ],
        ),
    )
    resp = client.get("/api/graph/stats")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert set(body) == {"nodes", "edges", "avg_degree", "max_degree"}, body
    assert body == {"nodes": 3, "edges": 2, "avg_degree": 1.33, "max_degree": 2}


def test_empty_graph_reports_zero_degrees(client, monkeypatch) -> None:
    _install_bridge(monkeypatch, _FakeBridge(nodes=[], edges=[]))
    body = client.get("/api/graph/stats").json()
    assert body == {"nodes": 0, "edges": 0, "avg_degree": 0.0, "max_degree": 0}


def test_graph_stats_failure_is_a_500_error_schema(client, monkeypatch) -> None:
    """The route's ``except`` turns service failures into ``HTTPException``;
    the decorator renders those, so the old ``error_value`` was unreachable."""
    _install_bridge(monkeypatch, _BrokenBridge())
    resp = client.get("/api/graph/stats")
    assert resp.status_code == 500, resp.text
    body = resp.json()
    assert set(body) == ERROR_KEYS, body
    assert body["status"] == "error"
    assert body["code"] == "HTTP_500"


def test_graph_stats_requires_admin() -> None:
    reader = _make_app(roles=["read"])
    resp = reader.get("/api/graph/stats")
    assert resp.status_code == 403, resp.text
    assert set(resp.json()) == ERROR_KEYS


def test_graph_stats_openapi_matches_the_model(client) -> None:
    schema = client.app.openapi()
    op = schema["paths"]["/api/graph/stats"]["get"]
    ref = op["responses"]["200"]["content"]["application/json"]["schema"]["$ref"]
    props = schema["components"]["schemas"][ref.rsplit("/", 1)[-1]]["properties"]
    assert set(props) == set(GraphStatsPayload.model_fields) == {
        "nodes", "edges", "avg_degree", "max_degree",
    }


# ── knowledge graph ───────────────────────────────────────────────


def _seed_graph() -> None:
    KnowledgeExtractor(root_dir=".")  # creates tables in the isolated data dir
    with sqlite3.connect(get_db_path("knowledge_graph")) as conn:
        conn.executemany(
            "INSERT INTO entities (name, entity_type, attributes, confidence) VALUES (?,?,?,?)",
            [
                ("agent-a", "agent", json.dumps({"k": "v"}), 0.9),
                ("agent-b", "task", "{}", 0.5),
            ],
        )
        conn.execute(
            "INSERT INTO relations (id, source, target, relation_type, context, confidence)"
            " VALUES (?,?,?,?,?,?)",
            ("e1", "agent-a", "agent-b", "delegates", "", 0.8),
        )
        conn.execute(
            "INSERT INTO facts (id, subject, predicate, object_value, created_at)"
            " VALUES (?,?,?,?,?)",
            ("f1", "agent-a", "does", "x", "2026-01-01T00:00:00Z"),
        )


def test_the_graph_payload_equals_the_producer(client) -> None:
    _seed_graph()
    resp = client.get("/api/knowledge-graph")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert set(body) == {"status", "data"}
    assert body["status"] == "ok"
    assert body["data"] == knowledge_service.query_knowledge_graph_v2(limit=500)


def test_rows_carry_the_producer_fields(client) -> None:
    _seed_graph()
    data = client.get("/api/knowledge-graph").json()["data"]
    assert [n["id"] for n in data["nodes"]] == ["agent-a", "agent-b"]
    assert all(set(n) == NODE_KEYS for n in data["nodes"]), data["nodes"]
    assert all(set(e) == EDGE_KEYS for e in data["edges"]), data["edges"]
    assert data["nodes"][0]["properties"] == {"k": "v"}
    assert data["nodes"][0]["timestamp"] == "2026-01-01T00:00:00Z"
    assert data["stats"] == {"node_count": 2, "edge_count": 1}


def test_the_type_filter_drops_edges_whose_endpoint_is_filtered_out(client) -> None:
    _seed_graph()
    data = client.get("/api/knowledge-graph", params={"type": "agent"}).json()["data"]
    assert [n["id"] for n in data["nodes"]] == ["agent-a"]
    assert data["edges"] == []
    assert data["stats"] == {"node_count": 1, "edge_count": 0}


def test_an_empty_graph_is_empty_not_missing(client) -> None:
    KnowledgeExtractor(root_dir=".")
    body = client.get("/api/knowledge-graph").json()
    assert body["data"] == {"nodes": [], "edges": [], "stats": {"node_count": 0, "edge_count": 0}}


def test_invalid_time_range_is_400(client) -> None:
    resp = client.get("/api/knowledge-graph", params={"time_range": "2026-01-01"})
    assert resp.status_code == 400, resp.text
    assert set(resp.json()) == ERROR_KEYS


def test_limit_bounds_are_validated(client) -> None:
    assert client.get("/api/knowledge-graph", params={"limit": 0}).status_code == 422
    assert client.get("/api/knowledge-graph", params={"limit": 1001}).status_code == 422


def test_knowledge_graph_requires_admin() -> None:
    reader = _make_app(roles=["read"])
    resp = reader.get("/api/knowledge-graph")
    assert resp.status_code == 403, resp.text


def test_knowledge_graph_openapi_matches_the_model(client) -> None:
    schema = client.app.openapi()
    components = schema["components"]["schemas"]
    op = schema["paths"]["/api/knowledge-graph"]["get"]
    ref = op["responses"]["200"]["content"]["application/json"]["schema"]["$ref"]
    payload = components[ref.rsplit("/", 1)[-1]]
    assert set(payload["properties"]) == set(KnowledgeGraphPayload.model_fields) == {
        "status", "data",
    }
    data_ref = payload["properties"]["data"]["$ref"]
    data_props = components[data_ref.rsplit("/", 1)[-1]]["properties"]
    assert set(data_props) == {"nodes", "edges", "stats"}
