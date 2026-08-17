"""Unit tests for MAOP.dashboard.routers.evolve_insights module."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from maop.dashboard.routers import evolve_insights as ev
from maop.dashboard.routers import state as st


def _make_app() -> FastAPI:
    app = FastAPI()
    @app.middleware("http")
    async def _inject_admin(request, call_next):
        request.state.auth_roles = ["admin"]
        return await call_next(request)
    app.include_router(ev.router)
    return app


@pytest.fixture
def temp_maop_root(tmp_path, monkeypatch):
    monkeypatch.setattr(ev, "MAOP_ROOT", tmp_path)
    return tmp_path


@pytest.fixture
def client():
    return TestClient(_make_app())


def _make_evolve_engine_mock():
    """Create a mock EvolveEngine instance."""
    eng = MagicMock()
    eng.status.return_value = {"stats": {"by_agent": [{"agent": "a1", "count": 5}]}}
    eng.analyze.return_value = {"suggestions": [], "count": 0}
    eng.suggest.return_value = {"action": "suggest", "stats": {"by_agent": []}}
    eng.apply.return_value = {"applied": True}
    return eng


# ── GET /api/evolve/status ────────────────────────────────────────
class TestEvolveStatus:
    def test_status_ok(self, client, temp_maop_root):
        eng = _make_evolve_engine_mock()
        with patch("maop.evolve.EvolveEngine", return_value=eng):
            resp = client.get("/api/evolve/status")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert "data" in data

    def test_status_error(self, client, temp_maop_root):
        with patch("maop.evolve.EvolveEngine", side_effect=Exception("no evolve")):
            resp = client.get("/api/evolve/status")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "error"

    def test_status_with_model_dump(self, client, temp_maop_root):
        """When status() returns a pydantic-like object with model_dump."""
        eng = MagicMock()
        model_obj = MagicMock()
        model_obj.model_dump.return_value = {"stats": {"by_agent": []}}
        eng.status.return_value = model_obj
        with patch("maop.evolve.EvolveEngine", return_value=eng):
            resp = client.get("/api/evolve/status")
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"


# ── POST /api/evolve/analyze ──────────────────────────────────────
class TestEvolveAnalyze:
    def test_analyze_default(self, client, temp_maop_root):
        eng = _make_evolve_engine_mock()
        with patch("maop.evolve.EvolveEngine", return_value=eng):
            resp = client.post("/api/evolve/analyze", json={})
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert data["action"] == "analyze"

    def test_analyze_with_action_apply(self, client, temp_maop_root):
        eng = _make_evolve_engine_mock()
        with patch("maop.evolve.EvolveEngine", return_value=eng):
            resp = client.post("/api/evolve/analyze", json={"action": "apply"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["action"] == "apply"

    def test_analyze_with_action_reset(self, client, temp_maop_root):
        eng = _make_evolve_engine_mock()
        eng._suggestions_file = None
        with patch("maop.evolve.EvolveEngine", return_value=eng):
            resp = client.post("/api/evolve/analyze", json={"action": "reset"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["action"] == "reset"
        assert "cleared" in data["msg"].lower()

    def test_analyze_no_body(self, client, temp_maop_root):
        """POST without JSON body should not crash."""
        eng = _make_evolve_engine_mock()
        with patch("maop.evolve.EvolveEngine", return_value=eng):
            resp = client.post("/api/evolve/analyze")
        assert resp.status_code == 200

    def test_analyze_error(self, client, temp_maop_root):
        with patch("maop.evolve.EvolveEngine", side_effect=Exception("fail")):
            resp = client.post("/api/evolve/analyze", json={})
        assert resp.status_code == 200
        assert resp.json()["status"] == "error"


# ── GET /api/evolve/suggestions ───────────────────────────────────
class TestEvolveSuggestions:
    def test_suggestions_ok(self, client, temp_maop_root):
        eng = _make_evolve_engine_mock()
        with patch("maop.evolve.EvolveEngine", return_value=eng):
            resp = client.get("/api/evolve/suggestions")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert "suggestions" in data

    def test_suggestions_error(self, client, temp_maop_root):
        with patch("maop.evolve.EvolveEngine", side_effect=Exception("nope")):
            resp = client.get("/api/evolve/suggestions")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "error"
        assert "suggestions" in data

    def test_suggestions_no_suggest_method(self, client, temp_maop_root):
        """EvolveEngine without suggest() method."""
        eng = MagicMock()
        eng.status.return_value = {"stats": {"by_agent": []}}
        del eng.suggest  # remove suggest attr
        with patch("maop.evolve.EvolveEngine", return_value=eng):
            resp = client.get("/api/evolve/suggestions")
        assert resp.status_code == 200


# ── GET /api/evolve/report ────────────────────────────────────────
class TestEvolveReport:
    def test_report_with_agents_dict(self, client, temp_maop_root, monkeypatch):
        bridge = AsyncMock()
        bridge.agent_stats.return_value = {
            "agents": [
                {"name": "a1", "success_rate": 0.9, "total_delegations": 10, "successes": 9,
                 "avg_latency_ms": 100, "tags": ["t1", "t2"]},
                {"name": "a2", "success_rate": 0.5, "total_delegations": 4, "successes": 2,
                 "avg_latency_ms": 200, "tags": []},
            ]
        }
        monkeypatch.setattr(st, "get_bridge", lambda: bridge)
        resp = client.get("/api/evolve/report")
        assert resp.status_code == 200
        data = resp.json()
        assert "performance" in data
        assert len(data["performance"]) == 2
        assert data["performance"][0]["agent"] == "a1"
        assert data["performance"][0]["success_rate"] == 90.0  # 0.9 * 100
        assert data["performance"][0]["fail_count"] == 1
        assert data["performance"][0]["tags"] == "t1,t2"

    def test_report_with_agents_list(self, client, temp_maop_root, monkeypatch):
        bridge = AsyncMock()
        bridge.agent_stats.return_value = [
            {"name": "a1", "success_rate": 80, "total": 5, "success": 4},
        ]
        monkeypatch.setattr(st, "get_bridge", lambda: bridge)
        resp = client.get("/api/evolve/report")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["performance"]) == 1
        # success_rate > 1 should not be multiplied
        assert data["performance"][0]["success_rate"] == 80

    def test_report_empty_agents(self, client, temp_maop_root, monkeypatch):
        bridge = AsyncMock()
        bridge.agent_stats.return_value = {"agents": []}
        monkeypatch.setattr(st, "get_bridge", lambda: bridge)
        resp = client.get("/api/evolve/report")
        assert resp.status_code == 200
        assert resp.json()["performance"] == []

    def test_report_error(self, client, temp_maop_root, monkeypatch):
        def bad_bridge():
            b = AsyncMock()
            b.agent_stats.side_effect = Exception("bridge fail")
            return b
        monkeypatch.setattr(st, "get_bridge", bad_bridge)
        resp = client.get("/api/evolve/report")
        assert resp.status_code == 200
        data = resp.json()
        assert data["performance"] == []
        assert "error" in data
