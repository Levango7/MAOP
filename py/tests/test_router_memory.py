"""Unit tests for MAOP.dashboard.routers.memory module.

Tests memory and neural mechanism endpoints:
  - /api/memory/deep, /api/memory/search, /api/memory/trace, /api/memory/stats
  - /api/neural/status, /api/neural/attention (GET + POST)
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


# ── Fakes ───────────────────────────────────────────────────────────

class FakeStats:
    """Simulate a Pydantic-like stats object."""

    def __init__(self, total_entries: int = 5) -> None:
        self.total_entries = total_entries

    def model_dump(self) -> dict:
        return {"total_entries": self.total_entries, "db_size": 1024}


class FakeMemoryStore:
    """Minimal MemoryStore replacement for testing."""

    def __init__(self, root_dir: str | None = None) -> None:
        self.root_dir = root_dir

    def stats(self) -> FakeStats:
        return FakeStats()

    def search(self, query: str = "", top: int = 10) -> list[dict]:
        if not query:
            return []
        return [
            {
                "agent": "claude",
                "content": "hello world",
                "score": 0.95,
                "topic": "general",
                "timestamp": "2024-01-01T00:00:00Z",
                "tags": "test",
                "trace_id": "abc123",
                "snippet": "hello world",
            },
            {
                "agent": "gemini",
                "content": "goodbye world",
                "score": 0.80,
                "topic": "general",
                "timestamp": "2024-01-02T00:00:00Z",
                "tags": "test",
                "trace_id": "def456",
                "snippet": "goodbye world",
            },
        ]


class FakeVectorStore:
    """Minimal VectorStore replacement."""

    def __init__(self, db_path: str | None = None) -> None:
        self.db_path = db_path

    def count(self) -> int:
        return 0


# ── Fixtures ────────────────────────────────────────────────────────

@pytest.fixture
def tmp_root(tmp_path, monkeypatch):
    """Point MAOP_ROOT to a temp dir in both state and memory modules."""
    monkeypatch.setattr("maop.dashboard.routers.state.MAOP_ROOT", tmp_path)
    monkeypatch.setattr("maop.dashboard.routers.memory.MAOP_ROOT", tmp_path)
    return tmp_path


@pytest.fixture
def client(tmp_root, monkeypatch):
    """TestClient with mocked MemoryStore and VectorStore."""
    monkeypatch.setattr("maop.memory.store.MemoryStore", FakeMemoryStore)
    monkeypatch.setattr("maop.core.vector.VectorStore", FakeVectorStore)
    # BloomFilter import is optional; let it succeed or fail naturally.
    app = FastAPI()
    @app.middleware("http")
    async def _inject_admin(request, call_next):
        request.state.auth_roles = ["admin"]
        return await call_next(request)
    from maop.dashboard.routers.memory import router
    app.include_router(router)
    return TestClient(app)


# ── /api/memory/deep ────────────────────────────────────────────────

class TestMemoryDeep:
    def test_returns_ok_status(self, client):
        resp = client.get("/api/memory/deep")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"

    def test_stats_has_bloom_filter_key(self, client):
        data = client.get("/api/memory/deep").json()
        assert "bloom_filter" in data["stats"]

    def test_stats_has_vector_index_key(self, client):
        data = client.get("/api/memory/deep").json()
        assert "vector_index" in data["stats"]

    def test_stats_has_recent_entries(self, client):
        data = client.get("/api/memory/deep").json()
        assert "recent_entries" in data["stats"]
        assert isinstance(data["stats"]["recent_entries"], list)

    def test_stats_has_total_entries(self, client):
        data = client.get("/api/memory/deep").json()
        assert data["stats"]["total_entries"] == 5

    def test_error_returns_error_status(self, tmp_root, monkeypatch):
        """When MemoryStore raises, endpoint returns error dict with generic message."""
        monkeypatch.setattr("maop.memory.store.MemoryStore",
                            MagicMock(side_effect=RuntimeError("boom")))
        app = FastAPI()
        from maop.dashboard.routers.memory import router
        app.include_router(router)
        c = TestClient(app)
        data = c.get("/api/memory/deep").json()
        assert data["status"] == "error"
        assert "boom" not in data["error"]  # internal details must not leak


# ── /api/memory/search ──────────────────────────────────────────────

class TestMemorySearch:
    def test_empty_query_returns_empty_results(self, client):
        resp = client.get("/api/memory/search")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert data["results"] == []
        assert data["count"] == 0

    def test_query_returns_results(self, client):
        data = client.get("/api/memory/search", params={"q": "hello"}).json()
        assert data["status"] == "ok"
        assert data["count"] == 2
        assert data["query"] == "hello"

    def test_query_with_topk_param(self, client):
        data = client.get("/api/memory/search",
                          params={"q": "hello", "topk": 1}).json()
        assert data["status"] == "ok"

    def test_results_have_score(self, client):
        data = client.get("/api/memory/search", params={"q": "test"}).json()
        for r in data["results"]:
            assert "score" in r


# ── /api/memory/trace ───────────────────────────────────────────────

class TestMemoryTrace:
    def test_no_agent_filter_returns_all(self, client):
        resp = client.get("/api/memory/trace")
        assert resp.status_code == 200
        data = resp.json()
        assert "traces" in data
        assert data["count"] == len(data["traces"])
        assert data["agent"] == "all"

    def test_agent_filter(self, client):
        data = client.get("/api/memory/trace",
                          params={"agent": "claude"}).json()
        assert data["agent"] == "claude"
        for t in data["traces"]:
            assert t["agent"] == "claude"

    def test_trace_structure(self, client):
        data = client.get("/api/memory/trace").json()
        for t in data["traces"]:
            assert "agent" in t
            assert "topic" in t
            assert "timestamp" in t
            assert "content" in t
            assert "tags" in t
            assert "trace_id" in t
            assert "score" in t


# ── /api/memory/stats ───────────────────────────────────────────────

class TestMemoryStats:
    def test_stats_via_bridge(self, client, monkeypatch):
        """Mock get_bridge to return async memory_stats."""
        async_bridge = AsyncMock()
        async_bridge.memory_stats = AsyncMock(
            return_value={"total": 10, "agents": 3})
        monkeypatch.setattr("maop.dashboard.routers.state.get_bridge",
                            lambda: async_bridge)
        data = client.get("/api/memory/stats").json()
        assert data == {"total": 10, "agents": 3}

    def test_stats_bridge_error(self, client, monkeypatch):
        async_bridge = AsyncMock()
        async_bridge.memory_stats = AsyncMock(
            side_effect=RuntimeError("bridge down"))
        monkeypatch.setattr("maop.dashboard.routers.state.get_bridge",
                            lambda: async_bridge)
        data = client.get("/api/memory/stats").json()
        assert "error" in data


# ── /api/neural/status ──────────────────────────────────────────────

class TestNeuralStatus:
    def test_returns_ok(self, client):
        resp = client.get("/api/neural/status")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"

    def test_mechanisms_dict(self, client):
        data = client.get("/api/neural/status").json()
        mech = data["mechanisms"]
        assert isinstance(mech, dict)
        assert "attention" in mech
        assert "transform" in mech
        assert "embedding" in mech
        assert "vector_store" in mech

    def test_vector_store_enabled(self, client):
        data = client.get("/api/neural/status").json()
        vs = data["mechanisms"]["vector_store"]
        assert vs["enabled"] is True
        assert "count" in vs

    def test_attention_has_mechanism(self, client):
        data = client.get("/api/neural/status").json()
        att = data["mechanisms"]["attention"]
        assert att["enabled"] is True
        assert "mechanism" in att


# ── /api/neural/attention POST ──────────────────────────────────────

class TestNeuralAttentionPost:
    def test_post_with_query(self, client):
        resp = client.post("/api/neural/attention",
                           json={"query": "hello", "top_k": 5})
        assert resp.status_code == 200
        data = resp.json()
        assert data["query"] == "hello"
        assert "attention_weights" in data
        assert isinstance(data["attention_weights"], list)
        assert data["count"] == 2

    def test_post_missing_query_returns_400(self, client):
        resp = client.post("/api/neural/attention", json={"top_k": 5})
        assert resp.status_code == 400

    def test_post_attention_weights_sum_to_one(self, client):
        data = client.post("/api/neural/attention",
                           json={"query": "hello"}).json()
        if data["attention_weights"]:
            assert abs(sum(data["attention_weights"]) - 1.0) < 1e-6

    def test_post_empty_query_string_returns_400(self, client):
        resp = client.post("/api/neural/attention", json={"query": ""})
        assert resp.status_code == 400


# ── /api/neural/attention GET ───────────────────────────────────────

class TestNeuralAttentionGet:
    def test_get_with_q_param(self, client):
        resp = client.get("/api/neural/attention", params={"q": "hello"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["query"] == "hello"
        assert "attention_weights" in data
        assert isinstance(data["attention_weights"], list)

    def test_get_without_q_returns_empty(self, client):
        data = client.get("/api/neural/attention").json()
        assert data["query"] == ""
        assert data["results"] == []
        assert data["attention_weights"] == []
        assert data["count"] == 0

    def test_get_weights_sum_to_one(self, client):
        data = client.get("/api/neural/attention",
                          params={"q": "hello"}).json()
        if data["attention_weights"]:
            assert abs(sum(data["attention_weights"]) - 1.0) < 1e-4
