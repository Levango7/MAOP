"""Coverage tests for mcp + evolve + memory routers — POST endpoints + complex GETs.

Uses isolated MAOP_ROOT + admin role injection + mocked managers.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


def _make_app(*routers) -> TestClient:
    app = FastAPI()

    @app.middleware("http")
    async def _inject_admin(request, call_next):
        request.state.auth_roles = ["admin"]
        request.state.auth_identity = "admin"
        return await call_next(request)

    for r in routers:
        app.include_router(r)
    return TestClient(app)


# ── MCP ──────────────────────────────────────────────────────────────

@pytest.fixture
def mcp_env(tmp_path, monkeypatch):
    monkeypatch.setattr("maop.dashboard.routers.mcp.MAOP_ROOT", tmp_path)
    import maop.dashboard.routers.mcp as mcp_mod
    mcp_mod._mcp_hub = None

    mock_hub = MagicMock()
    mock_hub.get_server_config = MagicMock(return_value=SimpleNamespace(
        name="test-server", transport="stdio", command="test", args=[], url="", env={},
    ))
    mock_hub.remove_server = MagicMock(return_value=True)
    mock_hub.connect = AsyncMock(return_value="server-1")
    mock_hub.find_server_id_by_name = MagicMock(return_value="server-1")
    mock_hub.disconnect = AsyncMock(return_value=None)
    mock_hub.list_servers = MagicMock(return_value=[{"name": "s1", "connected": True}])
    mock_hub.add_server = MagicMock(return_value=None)
    mock_hub.all_tools = MagicMock(return_value=[
        SimpleNamespace(model_dump=lambda: {"name": "tool1"})
    ])
    mock_hub.call_tool_by_name = AsyncMock(return_value=SimpleNamespace(
        model_dump=lambda: {"result": "ok"}
    ))
    mock_hub.health_check_all = AsyncMock(return_value={"s1": {"healthy": True}})

    monkeypatch.setattr("maop.dashboard.routers.mcp._get_hub", lambda: mock_hub)
    return mock_hub


@pytest.fixture
def mcp_client(mcp_env):
    from maop.dashboard.routers.mcp import router
    return _make_app(router)


class TestMcpConnect:
    def test_happy(self, mcp_client):
        resp = mcp_client.post("/api/mcp/connect/test-server")
        assert resp.status_code == 200

    def test_no_config(self, mcp_env, mcp_client):
        mcp_env.get_server_config.return_value = None
        resp = mcp_client.post("/api/mcp/connect/nonexistent")
        assert resp.status_code == 200
        assert resp.json()["status"] == "failed"


class TestMcpDisconnect:
    def test_happy(self, mcp_client):
        resp = mcp_client.post("/api/mcp/disconnect/test-server")
        assert resp.status_code == 200

    def test_no_server(self, mcp_env, mcp_client):
        mcp_env.find_server_id_by_name.return_value = None
        resp = mcp_client.post("/api/mcp/disconnect/nonexistent")
        assert resp.status_code == 200


class TestMcpServers:
    def test_list(self, mcp_client):
        resp = mcp_client.get("/api/mcp/servers")
        assert resp.status_code == 200
        assert resp.json()["count"] >= 1

    def test_add(self, mcp_client):
        resp = mcp_client.post(
            "/api/mcp/servers",
            json={"name": "new-server", "transport": "stdio", "command": "test"},
        )
        assert resp.status_code == 200

    def test_remove(self, mcp_client):
        resp = mcp_client.delete("/api/mcp/servers/test-server")
        assert resp.status_code == 200

    def test_remove_not_found(self, mcp_env, mcp_client):
        mcp_env.remove_server.return_value = False
        resp = mcp_client.delete("/api/mcp/servers/nonexistent")
        assert resp.status_code == 200
        assert resp.json()["status"] == "not_found"


class TestMcpTools:
    def test_list(self, mcp_client):
        resp = mcp_client.get("/api/mcp/tools")
        assert resp.status_code == 200
        assert resp.json()["count"] >= 1


class TestMcpCall:
    def test_happy(self, mcp_client):
        resp = mcp_client.post(
            "/api/mcp/call",
            json={"tool": "tool1", "arguments": {"x": 1}},
        )
        assert resp.status_code == 200


class TestMcpHealth:
    def test_health(self, mcp_client):
        resp = mcp_client.get("/api/mcp/health")
        assert resp.status_code == 200


# ── Evolve ───────────────────────────────────────────────────────────

@pytest.fixture
def evolve_env(tmp_path, monkeypatch):
    monkeypatch.setattr("maop.dashboard.routers.evolve_insights.MAOP_ROOT", tmp_path)
    return tmp_path


@pytest.fixture
def evolve_client(evolve_env, monkeypatch):
    """Mock EvolveEngine to avoid real evolution logic."""
    mock_eng = MagicMock()
    mock_eng.status = MagicMock(return_value={"stats": {"by_agent": []}})
    mock_eng.analyze = MagicMock(return_value={"suggestions": []})
    mock_eng.suggest = MagicMock(return_value={"stats": {"by_agent": []}})
    mock_eng.apply = MagicMock(return_value=SimpleNamespace(
        model_dump=lambda: {"applied": True}
    ))
    mock_eng._load_suggestions = MagicMock(return_value=[])
    mock_eng._suggestions_file = None

    # Patch EvolveEngine constructor
    monkeypatch.setattr("maop.evolve.EvolveEngine", lambda **kw: mock_eng)

    # Mock get_bridge for evolve report
    from unittest.mock import AsyncMock
    mock_bridge = MagicMock()
    mock_bridge.agent_stats = AsyncMock(return_value={"agents": []})
    monkeypatch.setattr("maop.dashboard.routers.state.get_bridge", lambda: mock_bridge)

    from maop.dashboard.routers.evolve_insights import router
    return _make_app(router)


class TestEvolveStatus:
    def test_status(self, evolve_client):
        resp = evolve_client.get("/api/evolve/status")
        assert resp.status_code == 200


class TestEvolveAnalyze:
    def test_default(self, evolve_client):
        resp = evolve_client.post("/api/evolve/analyze", json={})
        assert resp.status_code == 200

    def test_apply_action(self, evolve_client):
        resp = evolve_client.post(
            "/api/evolve/analyze",
            json={"action": "apply", "suggestion_id": "s1"},
        )
        assert resp.status_code == 200

    def test_reset_action(self, evolve_client):
        resp = evolve_client.post(
            "/api/evolve/analyze",
            json={"action": "reset"},
        )
        assert resp.status_code == 200

    def test_auto_evolve_action(self, evolve_client):
        resp = evolve_client.post(
            "/api/evolve/analyze",
            json={"action": "auto_evolve", "hours": 24},
        )
        assert resp.status_code == 200


class TestEvolveSuggestions:
    def test_suggestions(self, evolve_client):
        resp = evolve_client.get("/api/evolve/suggestions")
        assert resp.status_code == 200


class TestEvolveReport:
    def test_report(self, evolve_client):
        resp = evolve_client.get("/api/evolve/report")
        assert resp.status_code == 200


class TestEvolveStrategies:
    def test_strategies(self, evolve_client):
        resp = evolve_client.get("/api/evolve/strategies")
        assert resp.status_code == 200


class TestEvolveHistory:
    def test_history(self, evolve_client):
        resp = evolve_client.get("/api/evolve/history")
        assert resp.status_code == 200


class TestEvolveSuggestionsList:
    def test_list(self, evolve_client):
        resp = evolve_client.get("/api/evolve/suggestions-list")
        assert resp.status_code == 200


class TestEvolveApplySuggestion:
    def test_happy(self, evolve_client):
        resp = evolve_client.post(
            "/api/evolve/apply-suggestion",
            json={"suggestion_id": "s1"},
        )
        assert resp.status_code == 200


# ── Memory ───────────────────────────────────────────────────────────

@pytest.fixture
def memory_env(tmp_path, monkeypatch):
    monkeypatch.setattr("maop.dashboard.routers.memory.MAOP_ROOT", tmp_path)
    monkeypatch.setattr("maop.dashboard.routers.state.MAOP_ROOT", tmp_path)
    return tmp_path


@pytest.fixture
def memory_client(memory_env, monkeypatch):
    """Mock MemoryStore + ThreeLayerMemory + get_bridge."""
    # Mock MemoryStore
    mock_store = MagicMock()
    mock_store.stats = MagicMock(return_value=SimpleNamespace(
        model_dump=lambda: {"total_entries": 0},
        total_entries=0,
    ))
    mock_store.search = MagicMock(return_value=[])
    monkeypatch.setattr("maop.memory.store.MemoryStore", lambda **kw: mock_store)

    # Mock ThreeLayerMemory
    mock_tlm = MagicMock()
    mock_tlm.episodic_search = MagicMock(return_value=[])
    mock_tlm.episodic_stats = MagicMock(return_value={"total": 0, "by_outcome": {}, "avg_score": 0, "consolidated": 0})
    mock_tlm.store = MagicMock(return_value="entry-1")
    monkeypatch.setattr("maop.core.memory.three_layer_memory.ThreeLayerMemory", lambda **kw: mock_tlm)

    # Mock get_bridge
    mock_bridge = MagicMock()
    mock_bridge.memory_stats = AsyncMock(return_value={"status": "ok"})
    monkeypatch.setattr("maop.dashboard.routers.state.get_bridge", lambda: mock_bridge)

    from maop.dashboard.routers.memory import router
    return _make_app(router)


class TestMemoryDeep:
    def test_deep(self, memory_client):
        resp = memory_client.get("/api/memory/deep")
        assert resp.status_code == 200


class TestMemorySearch:
    def test_search_no_query(self, memory_client):
        resp = memory_client.get("/api/memory/search")
        assert resp.status_code == 200

    def test_search_with_query(self, memory_client):
        resp = memory_client.get("/api/memory/search?q=test")
        assert resp.status_code == 200


class TestMemoryTrace:
    def test_trace_no_agent(self, memory_client):
        resp = memory_client.get("/api/memory/trace")
        assert resp.status_code == 200

    def test_trace_with_agent(self, memory_client):
        resp = memory_client.get("/api/memory/trace?agent=claude")
        assert resp.status_code == 200


class TestMemoryStats:
    def test_stats(self, memory_client):
        resp = memory_client.get("/api/memory/stats")
        assert resp.status_code == 200


class TestNeuralStatus:
    def test_status(self, memory_client):
        resp = memory_client.get("/api/neural/status")
        assert resp.status_code == 200


class TestNeuralAttentionPost:
    def test_missing_query(self, memory_client):
        resp = memory_client.post("/api/neural/attention", json={})
        assert resp.status_code == 400

    def test_happy(self, memory_client):
        resp = memory_client.post(
            "/api/neural/attention",
            json={"query": "test", "top_k": 5},
        )
        assert resp.status_code == 200


class TestNeuralAttentionGet:
    def test_no_query(self, memory_client):
        resp = memory_client.get("/api/neural/attention")
        assert resp.status_code == 200

    def test_with_query(self, memory_client):
        resp = memory_client.get("/api/neural/attention?q=test")
        assert resp.status_code == 200


class TestMemoryStore:
    def test_missing_content(self, memory_client):
        resp = memory_client.post("/api/memory/store", json={})
        assert resp.status_code == 400

    def test_happy(self, memory_client):
        resp = memory_client.post(
            "/api/memory/store",
            json={"content": "test memory", "layer": "episodic"},
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"

    def test_with_tags_string(self, memory_client):
        resp = memory_client.post(
            "/api/memory/store",
            json={"content": "test", "tags": "a,b,c"},
        )
        assert resp.status_code == 200

    def test_with_tags_list(self, memory_client):
        resp = memory_client.post(
            "/api/memory/store",
            json={"content": "test", "tags": ["a", "b"]},
        )
        assert resp.status_code == 200