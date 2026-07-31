"""Verify unified search: data written via /api/memory/store (ThreeLayerMemory)
can be found via /api/memory/search (now queries both tables).
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


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
    _store_data: list = []

    def __init__(self, root_dir=None):
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

    def store(self, layer, content, **kwargs):
        entry = FakeEpisodicEntry(
            task=kwargs.get("task") or content[:80],
            agent=kwargs.get("agent", ""),
            summary=content,
        )
        FakeThreeLayerMemory._store_data.append(entry)
        return entry.id


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
    monkeypatch.setattr("maop.core.three_layer_memory.ThreeLayerMemory", FakeThreeLayerMemory)
    monkeypatch.setattr("maop.core.vector.VectorStore", FakeVectorStore)
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
