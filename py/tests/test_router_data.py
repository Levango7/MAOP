"""Unit tests for MAOP.dashboard.routers.data module.

Refactor note (v4.0.0): data router consolidation moved all sub-endpoints
(overview/graph/knowledge/tools/system) into a single data module.
The previous sub-module imports (data_overview/data_graph/...) were removed;
fixtures now monkeypatch the consolidated data module directly.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from maop.dashboard.routers import data as data_mod
from maop.dashboard.routers import state as state_mod


def _make_app() -> FastAPI:
    app = FastAPI()
    @app.middleware("http")
    async def _inject_admin(request, call_next):
        request.state.auth_roles = ["admin"]
        return await call_next(request)
    app.include_router(data_mod.router)
    return app


def _make_bridge_mock():
    """Create an AsyncMock bridge with all methods used by data endpoints."""
    bridge = AsyncMock()
    bridge.report.return_value = {"summary": "ok", "hours": 48}
    bridge.agent_stats.return_value = {"agents": [], "total": 0}
    bridge.timeseries.return_value = {"points": []}
    bridge.live.return_value = {"live": True}
    bridge.failures.return_value = {"failures": []}
    bridge.chain.return_value = {"chain": []}
    bridge.graph_nodes.return_value = [{"id": "n1"}, {"id": "n2"}]
    bridge.graph_edges.return_value = [{"source": "n1", "target": "n2"}]
    bridge.memory_stats.return_value = {"total": 10}
    bridge.tools_stats.return_value = {"tools": 5}
    bridge.guardrail_report.return_value = {"guardrails": []}
    bridge.sandbox_list.return_value = {"sandboxes": []}
    bridge.human_pending.return_value = {"pending": []}
    bridge.prompts_list.return_value = {"prompts": [{"name": "p1"}]}
    bridge.coordination_report.return_value = {"teams": []}
    bridge.skills_list.return_value = {"skills": [{"name": "s1"}]}
    bridge.providers_report.return_value = {"providers": []}
    bridge.mcp_servers.return_value = [{"name": "srv1"}]
    bridge.mcp_tools.return_value = [{"name": "tool1"}]
    bridge.logs_get.return_value = {"content": "log data"}
    bridge.versions_check.return_value = {"MAOP": "3.0"}
    return bridge


@pytest.fixture
def temp_maop_root(tmp_path, monkeypatch):
    # Consolidated: data module re-exports MAOP_ROOT from .state
    monkeypatch.setattr(data_mod, "MAOP_ROOT", tmp_path)
    monkeypatch.setattr(state_mod, "MAOP_ROOT", tmp_path)
    return tmp_path


@pytest.fixture
def mock_bridge(monkeypatch):
    bridge = _make_bridge_mock()
    # Consolidated: data module re-exports get_bridge from .state
    monkeypatch.setattr(data_mod, "get_bridge", lambda: bridge)
    monkeypatch.setattr(state_mod, "get_bridge", lambda: bridge)
    return bridge


@pytest.fixture
def client():
    return TestClient(_make_app())


# ── Basic data endpoints ──────────────────────────────────────────
class TestBasicEndpoints:
    def test_report(self, client, mock_bridge):
        resp = client.get("/api/report")
        assert resp.status_code == 200
        assert resp.json()["summary"] == "ok"
        mock_bridge.report.assert_awaited_once()

    def test_agents(self, client, mock_bridge):
        resp = client.get("/api/agents")
        assert resp.status_code == 200
        assert "agents" in resp.json()

    def test_timeseries(self, client, mock_bridge):
        resp = client.get("/api/timeseries")
        assert resp.status_code == 200
        assert "points" in resp.json()

    def test_live(self, client, mock_bridge):
        resp = client.get("/api/live")
        assert resp.status_code == 200
        assert resp.json()["live"] is True

    def test_failures(self, client, mock_bridge):
        resp = client.get("/api/failures")
        assert resp.status_code == 200
        assert "failures" in resp.json()

    def test_chain(self, client, mock_bridge):
        resp = client.get("/api/chain")
        assert resp.status_code == 200
        assert "chain" in resp.json()


# ── Graph endpoints ───────────────────────────────────────────────
class TestGraphEndpoints:
    def test_graph_stats(self, client, mock_bridge):
        resp = client.get("/api/graph/stats")
        assert resp.status_code == 200
        data = resp.json()
        assert data["nodes"] == 2
        assert data["edges"] == 1
        assert "avg_degree" in data

    def test_graph_nodes(self, client, mock_bridge):
        resp = client.get("/api/graph/nodes")
        assert resp.status_code == 200
        assert len(resp.json()) == 2

    def test_graph_edges(self, client, mock_bridge):
        resp = client.get("/api/graph/edges")
        assert resp.status_code == 200
        assert len(resp.json()) == 1

    def test_graph_neighbors(self, client, mock_bridge):
        resp = client.get("/api/graph/neighbors?node=n1")
        assert resp.status_code == 200
        data = resp.json()
        assert data["node"] == "n1"
        assert data["count"] == 1

    def test_graph_neighbors_not_found(self, client, mock_bridge):
        resp = client.get("/api/graph/neighbors?node=unknown")
        data = resp.json()
        assert data["count"] == 0


# ── Vector / memory endpoints ─────────────────────────────────────
class TestVectorEndpoints:
    def test_vector_stats(self, client, mock_bridge):
        resp = client.get("/api/vector/stats")
        assert resp.status_code == 200
        assert resp.json()["total"] == 10

    def test_vector_list_error(self, client, temp_maop_root, mock_bridge):
        resp = client.get("/api/vector/list")
        assert resp.status_code == 200
        data = resp.json()
        assert "vectors" in data
        assert "count" in data

    def test_vector_search_error(self, client, temp_maop_root, mock_bridge):
        resp = client.get("/api/vector/search?q=test")
        assert resp.status_code == 200
        data = resp.json()
        assert data["query"] == "test"
        assert "results" in data


# ── Tools / guardrails / sandbox ──────────────────────────────────
class TestMiscEndpoints:
    def test_tools_stats(self, client, mock_bridge):
        resp = client.get("/api/tools/stats")
        assert resp.status_code == 200
        assert resp.json()["tools"] == 5

    def test_guardrails(self, client, mock_bridge):
        resp = client.get("/api/guardrails")
        assert resp.status_code == 200
        assert "guardrails" in resp.json()

    def test_sandbox_list(self, client, mock_bridge):
        resp = client.get("/api/sandbox/list")
        assert resp.status_code == 200
        assert "sandboxes" in resp.json()

    def test_human_pending(self, client, mock_bridge):
        resp = client.get("/api/human/pending")
        assert resp.status_code == 200
        assert "pending" in resp.json()


# ── Prompts ───────────────────────────────────────────────────────
class TestPrompts:
    def test_prompts_from_bridge(self, client, mock_bridge):
        resp = client.get("/api/prompts")
        assert resp.status_code == 200
        data = resp.json()
        assert "prompts" in data
        assert len(data["prompts"]) >= 1

    def test_prompts_error_fallback(self, client, temp_maop_root, monkeypatch):
        # Consolidated: prompts endpoint lives in data module now
        def bad_bridge():
            b = AsyncMock()
            b.prompts_list.side_effect = Exception("boom")
            return b
        monkeypatch.setattr(data_mod, "get_bridge", bad_bridge)
        resp = client.get("/api/prompts")
        assert resp.status_code == 200
        data = resp.json()
        assert "prompts" in data


# ── Coordination / teams / skills ─────────────────────────────────
class TestCoordination:
    def test_coordination(self, client, mock_bridge):
        resp = client.get("/api/coordination")
        assert resp.status_code == 200
        assert "teams" in resp.json()

    def test_teams_fallback(self, client, temp_maop_root, mock_bridge):
        resp = client.get("/api/teams")
        assert resp.status_code == 200
        # Returns a list
        assert isinstance(resp.json(), list)

    def test_skills(self, client, mock_bridge):
        resp = client.get("/api/skills")
        assert resp.status_code == 200
        data = resp.json()
        assert "skills" in data
        assert "count" in data


# ── Wiki / versions / providers ───────────────────────────────────
class TestInfoEndpoints:
    def test_wiki_stats(self, client, temp_maop_root, mock_bridge):
        resp = client.get("/api/wiki/stats")
        assert resp.status_code == 200
        data = resp.json()
        assert "total" in data
        assert "vector_count" in data

    def test_versions(self, client, mock_bridge):
        resp = client.get("/api/versions")
        assert resp.status_code == 200
        data = resp.json()
        assert "MAOP_VERSION" in data
        assert "python" in data

    def test_providers(self, client, mock_bridge):
        resp = client.get("/api/providers")
        assert resp.status_code == 200
        assert "providers" in resp.json()


# ── MCP ───────────────────────────────────────────────────────────
class TestMcp:
    def test_mcp_servers(self, client, mock_bridge):
        resp = client.get("/api/mcp/servers")
        assert resp.status_code == 200
        assert len(resp.json()) == 1

    def test_mcp_tools(self, client, mock_bridge):
        resp = client.get("/api/mcp/tools")
        assert resp.status_code == 200
        assert len(resp.json()) == 1

    def test_mcp_combined(self, client, mock_bridge):
        resp = client.get("/api/mcp")
        assert resp.status_code == 200
        data = resp.json()
        assert data["server_count"] == 1
        assert data["tool_count"] == 1


# ── Optimizer ─────────────────────────────────────────────────────
class TestOptimizer:
    def test_optimizer(self, client, mock_bridge):
        resp = client.get("/api/optimizer")
        assert resp.status_code == 200
        data = resp.json()
        assert "report" in data
        assert "recommendations" in data


# ── Metrics ───────────────────────────────────────────────────────
class TestMetrics:
    def test_metrics_returns_dict(self, client, temp_maop_root, mock_bridge):
        resp = client.get("/api/metrics")
        assert resp.status_code == 200
        data = resp.json()
        assert "load_balancer" in data
        assert "timeseries" in data
        assert "circuit_breaker" in data
        assert "cache" in data


# ── Logs ──────────────────────────────────────────────────────────
class TestLogs:
    def test_logs_default(self, client, temp_maop_root, mock_bridge):
        resp = client.get("/api/logs")
        assert resp.status_code == 200

    def test_logs_delegations(self, client, mock_bridge):
        resp = client.get("/api/logs/delegations")
        assert resp.status_code == 200
        mock_bridge.logs_get.assert_awaited()

    def test_logs_checker(self, client, mock_bridge):
        resp = client.get("/api/logs/checker")
        assert resp.status_code == 200

    def test_logs_analysis(self, client, mock_bridge):
        resp = client.get("/api/logs/analysis")
        assert resp.status_code == 200
        data = resp.json()
        assert "total" in data

    def test_logs_analysis_empty(self, client, monkeypatch):
        # Consolidated: logs endpoint lives in data module now
        bridge = AsyncMock()
        bridge.logs_get.return_value = []
        monkeypatch.setattr(data_mod, "get_bridge", lambda: bridge)
        app = TestClient(_make_app())
        resp = app.get("/api/logs/analysis")
        data = resp.json()
        assert data["total"] == 0


# ── Batch ─────────────────────────────────────────────────────────
class TestBatch:
    def test_batch_empty_keys(self, client, mock_bridge):
        resp = client.get("/api/batch")
        assert resp.status_code == 200
        assert resp.json() == {}

    def test_batch_multiple_keys(self, client, mock_bridge):
        resp = client.get("/api/batch?keys=report,live,failures")
        assert resp.status_code == 200
        data = resp.json()
        assert "report" in data
        assert "live" in data
        assert "failures" in data

    def test_batch_unknown_key_ignored(self, client, mock_bridge):
        resp = client.get("/api/batch?keys=report,unknown_key")
        assert resp.status_code == 200
        data = resp.json()
        assert "report" in data
        assert "unknown_key" not in data