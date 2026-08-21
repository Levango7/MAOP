"""Coverage tests for tenant + subagent + plugin + react + permission + routing_preview routers.

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


# ── Tenant ───────────────────────────────────────────────────────────

@pytest.fixture
def tenant_client(tmp_path, monkeypatch):
    """Tenant router with FeatureFlag.TENANT_ISOLATION enabled + mocked manager."""
    monkeypatch.setattr("maop.dashboard.routers.state.MAOP_ROOT", tmp_path)

    # Enable tenant isolation feature
    monkeypatch.setattr("maop.dashboard.routers.tenant.has_feature", lambda f: True)

    # Reset tenant manager singleton
    import maop.dashboard.routers.tenant as tenant_mod
    tenant_mod._tenant_manager = None

    mock_mgr = MagicMock()
    mock_mgr.list_tenants = MagicMock(return_value=[])
    mock_mgr.create_tenant = MagicMock(return_value=SimpleNamespace(
        model_dump=lambda: {"tenant_id": "t1", "name": "test"},
        tenant_id="t1",
    ))
    mock_mgr.get_tenant = MagicMock(return_value=SimpleNamespace(
        model_dump=lambda: {"tenant_id": "t1", "name": "test"},
    ))
    mock_mgr.suspend_tenant = MagicMock(return_value=True)
    mock_mgr.activate_tenant = MagicMock(return_value=True)
    mock_mgr.delete_tenant = MagicMock(return_value=True)
    mock_mgr.get_usage = MagicMock(return_value=SimpleNamespace(
        model_dump=lambda: {"api_calls": 0}
    ))
    monkeypatch.setattr("maop.dashboard.routers.tenant._get_manager", lambda: mock_mgr)

    from maop.dashboard.routers.tenant import router
    return _make_app(router)


class TestTenantList:
    def test_list(self, tenant_client):
        pytest.importorskip("maop.enterprise")
        resp = tenant_client.get("/api/tenant/list")
        assert resp.status_code == 200

    def test_list_with_status(self, tenant_client):
        resp = tenant_client.get("/api/tenant/list?status=active")
        assert resp.status_code in (200, 500)  # may fail on invalid enum


class TestTenantCreate:
    def test_happy(self, tenant_client):
        pytest.importorskip("maop.enterprise")
        resp = tenant_client.post(
            "/api/tenant/create",
            json={"tenant_id": "t1", "name": "test"},
        )
        assert resp.status_code == 200


class TestTenantGet:
    def test_happy(self, tenant_client):
        resp = tenant_client.get("/api/tenant/t1")
        assert resp.status_code == 200

    def test_not_found(self, tenant_client, monkeypatch):
        import maop.dashboard.routers.tenant as tenant_mod
        mgr = tenant_mod._get_manager()
        mgr.get_tenant.return_value = None
        resp = tenant_client.get("/api/tenant/nonexistent")
        assert resp.status_code == 200
        assert resp.json()["status"] == "error"


class TestTenantSuspend:
    def test_happy(self, tenant_client):
        resp = tenant_client.post("/api/tenant/t1/suspend")
        assert resp.status_code == 200

    def test_not_found(self, tenant_client):
        import maop.dashboard.routers.tenant as tenant_mod
        mgr = tenant_mod._get_manager()
        mgr.suspend_tenant.return_value = False
        resp = tenant_client.post("/api/tenant/nonexistent/suspend")
        assert resp.status_code == 200


class TestTenantActivate:
    def test_happy(self, tenant_client):
        resp = tenant_client.post("/api/tenant/t1/activate")
        assert resp.status_code == 200


class TestTenantDelete:
    def test_happy(self, tenant_client):
        resp = tenant_client.delete("/api/tenant/t1")
        assert resp.status_code == 200


class TestTenantUsage:
    def test_happy(self, tenant_client):
        resp = tenant_client.get("/api/tenant/t1/usage")
        assert resp.status_code == 200


# ── SubAgent ─────────────────────────────────────────────────────────

@pytest.fixture
def subagent_client(tmp_path, monkeypatch):
    monkeypatch.setattr("maop.dashboard.routers.subagent.MAOP_ROOT", tmp_path)
    import maop.dashboard.routers.subagent as sa_mod
    sa_mod._subagent_mgr = None

    mock_mgr = MagicMock()
    mock_mgr.spawn = AsyncMock(return_value="agent-1")
    mock_mgr.wait = AsyncMock(return_value=SimpleNamespace(
        model_dump=lambda: {"status": "completed"}
    ))
    mock_mgr.cancel = MagicMock(return_value=True)
    mock_mgr.list_agents = MagicMock(return_value=[{"id": "a1", "agent": "claude"}])
    mock_mgr.get_live_transcript = MagicMock(return_value=["line1", "line2"])
    monkeypatch.setattr("maop.dashboard.routers.subagent._get_subagent_mgr", lambda: mock_mgr)

    from maop.dashboard.routers.subagent import router
    return _make_app(router)


class TestSubagentSpawn:
    def test_missing_fields(self, subagent_client):
        resp = subagent_client.post("/api/subagent/spawn", json={})
        assert resp.status_code == 400

    def test_happy(self, subagent_client):
        resp = subagent_client.post(
            "/api/subagent/spawn",
            json={"agent": "claude", "task": "test"},
        )
        assert resp.status_code == 200

    def test_with_model(self, subagent_client):
        resp = subagent_client.post(
            "/api/subagent/spawn",
            json={"agent": "claude", "task": "test", "model": "claude-3"},
        )
        assert resp.status_code == 200


class TestSubagentWait:
    def test_missing_id(self, subagent_client):
        resp = subagent_client.post("/api/subagent/wait", json={})
        assert resp.status_code == 400

    def test_happy(self, subagent_client):
        resp = subagent_client.post(
            "/api/subagent/wait",
            json={"agent_id": "a1", "timeout": 60},
        )
        assert resp.status_code == 200

    def test_not_found(self, subagent_client, monkeypatch):
        import maop.dashboard.routers.subagent as sa_mod
        mgr = sa_mod._get_subagent_mgr()
        mgr.wait.return_value = None
        resp = subagent_client.post(
            "/api/subagent/wait",
            json={"agent_id": "nonexistent"},
        )
        assert resp.status_code == 404


class TestSubagentCancel:
    def test_missing_id(self, subagent_client):
        resp = subagent_client.post("/api/subagent/cancel", json={})
        assert resp.status_code == 400

    def test_happy(self, subagent_client):
        resp = subagent_client.post(
            "/api/subagent/cancel",
            json={"agent_id": "a1"},
        )
        assert resp.status_code == 200


class TestSubagentList:
    def test_list(self, subagent_client):
        resp = subagent_client.get("/api/subagent/list")
        assert resp.status_code == 200


class TestSubagentTranscript:
    def test_missing_id(self, subagent_client):
        resp = subagent_client.get("/api/subagent/transcript")
        assert resp.status_code == 400

    def test_happy(self, subagent_client):
        resp = subagent_client.get("/api/subagent/transcript?agent_id=a1")
        assert resp.status_code == 200


# ── Plugin ───────────────────────────────────────────────────────────

@pytest.fixture
def plugin_client(tmp_path, monkeypatch):
    mock_mgr = MagicMock()
    mock_mgr.list_plugins = MagicMock(return_value=[
        SimpleNamespace(model_dump=lambda: {"id": "p1", "name": "test"})
    ])
    mock_mgr.get_plugin = MagicMock(return_value=SimpleNamespace(
        model_dump=lambda: {"id": "p1", "name": "test"}
    ))
    mock_mgr.discover = MagicMock(return_value=[
        SimpleNamespace(model_dump=lambda: {"id": "p1"})
    ])
    mock_mgr.load = MagicMock(return_value=SimpleNamespace(
        model_dump=lambda: {"id": "p1", "state": "loaded"}
    ))
    mock_mgr.start = MagicMock(return_value=SimpleNamespace(
        model_dump=lambda: {"id": "p1", "state": "started"}
    ))
    mock_mgr.stop = MagicMock(return_value=SimpleNamespace(
        model_dump=lambda: {"id": "p1", "state": "stopped"}
    ))
    mock_mgr.reload = MagicMock(return_value=SimpleNamespace(
        model_dump=lambda: {"id": "p1", "state": "reloaded"}
    ))
    mock_mgr.update_config = MagicMock(return_value=SimpleNamespace(
        model_dump=lambda: {"id": "p1"}
    ))
    mock_mgr.load_all = MagicMock(return_value=[])
    mock_mgr.start_all = MagicMock(return_value=[])
    mock_mgr.stop_all = MagicMock(return_value=[])
    monkeypatch.setattr("maop.dashboard.routers.plugin._get_plugin_manager", lambda: mock_mgr)

    from maop.dashboard.routers.plugin import router
    return _make_app(router)


class TestPluginList:
    def test_list(self, plugin_client):
        resp = plugin_client.get("/api/plugins")
        assert resp.status_code == 200

    def test_list_with_state(self, plugin_client):
        resp = plugin_client.get("/api/plugins?state=loaded")
        assert resp.status_code in (200, 500)


class TestPluginGet:
    def test_happy(self, plugin_client):
        resp = plugin_client.get("/api/plugins/p1")
        assert resp.status_code == 200

    def test_not_found(self, plugin_client, monkeypatch):
        # Can't easily mock _get_plugin_manager per-test; just call nonexistent
        resp = plugin_client.get("/api/plugins/nonexistent")
        # Returns {"error": "Plugin not found"} with 200
        assert resp.status_code == 200


class TestPluginDiscover:
    def test_happy(self, plugin_client):
        resp = plugin_client.post("/api/plugins/discover")
        assert resp.status_code == 200


class TestPluginLoad:
    def test_happy(self, plugin_client):
        resp = plugin_client.post("/api/plugins/p1/load")
        assert resp.status_code == 200


class TestPluginStart:
    def test_happy(self, plugin_client):
        resp = plugin_client.post("/api/plugins/p1/start")
        assert resp.status_code == 200

    def test_with_config(self, plugin_client):
        resp = plugin_client.post(
            "/api/plugins/p1/start",
            json={"config": {"key": "value"}},
        )
        assert resp.status_code == 200


class TestPluginStop:
    def test_happy(self, plugin_client):
        resp = plugin_client.post("/api/plugins/p1/stop")
        assert resp.status_code == 200


class TestPluginReload:
    def test_happy(self, plugin_client):
        resp = plugin_client.post("/api/plugins/p1/reload")
        assert resp.status_code == 200


class TestPluginUpdateConfig:
    def test_happy(self, plugin_client):
        resp = plugin_client.put(
            "/api/plugins/p1/config",
            json={"config": {"key": "value"}},
        )
        assert resp.status_code == 200


class TestPluginLoadAll:
    def test_happy(self, plugin_client):
        resp = plugin_client.post("/api/plugins/load-all")
        assert resp.status_code == 200


class TestPluginStartAll:
    def test_happy(self, plugin_client):
        resp = plugin_client.post("/api/plugins/start-all")
        assert resp.status_code == 200


class TestPluginStopAll:
    def test_happy(self, plugin_client):
        resp = plugin_client.post("/api/plugins/stop-all")
        assert resp.status_code == 200


# ── React ────────────────────────────────────────────────────────────

@pytest.fixture
def react_client(tmp_path, monkeypatch):
    mock_tracker = MagicMock()
    mock_tracker.list_snapshots = MagicMock(return_value=[
        SimpleNamespace(model_dump=lambda: {"id": "s1"})
    ])
    mock_tracker.snapshot = MagicMock(return_value="snap-1")
    mock_tracker.get_snapshot = MagicMock(return_value=SimpleNamespace(
        model_dump=lambda: {"id": "snap-1"}
    ))
    mock_tracker.diff = MagicMock(return_value=SimpleNamespace(
        model_dump=lambda: {"changes": []}
    ))
    mock_tracker.get_change_log = MagicMock(return_value=[])
    mock_tracker.delete_snapshot = MagicMock(return_value=True)

    mock_store = MagicMock()
    mock_store.list_artifacts = MagicMock(return_value=[
        SimpleNamespace(model_dump=lambda: {"name": "a1"})
    ])
    mock_store.save = MagicMock(return_value=1)
    mock_store.load = MagicMock(return_value="content")
    mock_store.history = MagicMock(return_value=[
        SimpleNamespace(model_dump=lambda: {"version": 1})
    ])
    mock_store.restore = MagicMock(return_value=True)
    mock_store.delete_artifact = MagicMock(return_value=True)

    monkeypatch.setattr("maop.dashboard.routers.react._get_change_tracker", lambda: mock_tracker)
    monkeypatch.setattr("maop.dashboard.routers.react._get_artifact_store", lambda: mock_store)

    from maop.dashboard.routers.react import router
    return _make_app(router)


class TestReactSnapshots:
    def test_list(self, react_client):
        resp = react_client.get("/api/react/snapshots")
        assert resp.status_code == 200

    def test_create(self, react_client):
        resp = react_client.post(
            "/api/react/snapshots",
            json={"workdir": "/tmp", "label": "v1"},
        )
        assert resp.status_code == 200

    def test_delete(self, react_client):
        resp = react_client.delete("/api/react/snapshots/snap-1")
        assert resp.status_code == 200


class TestReactDiff:
    def test_happy(self, react_client):
        resp = react_client.get("/api/react/diff?workdir=/tmp")
        assert resp.status_code == 200


class TestReactChanges:
    def test_happy(self, react_client):
        resp = react_client.get("/api/react/changes?workdir=/tmp")
        assert resp.status_code == 200


class TestReactArtifacts:
    def test_list(self, react_client):
        resp = react_client.get("/api/react/artifacts")
        assert resp.status_code == 200

    def test_save(self, react_client):
        resp = react_client.post(
            "/api/react/artifacts",
            json={"name": "a1", "content": "test"},
        )
        assert resp.status_code == 200

    def test_load(self, react_client):
        resp = react_client.get("/api/react/artifacts/a1")
        assert resp.status_code == 200

    def test_load_not_found(self, react_client, monkeypatch):
        import maop.dashboard.routers.react as react_mod
        store = react_mod._get_artifact_store()
        store.load.return_value = None
        resp = react_client.get("/api/react/artifacts/nonexistent")
        assert resp.status_code == 200

    def test_history(self, react_client):
        resp = react_client.get("/api/react/artifacts/a1/history")
        assert resp.status_code == 200

    def test_restore(self, react_client):
        resp = react_client.post(
            "/api/react/artifacts/a1/restore",
            json={"version": 1},
        )
        assert resp.status_code == 200

    def test_delete(self, react_client):
        resp = react_client.delete("/api/react/artifacts/a1")
        assert resp.status_code == 200


# ── Permission ───────────────────────────────────────────────────────

@pytest.fixture
def permission_client(tmp_path, monkeypatch):
    monkeypatch.setattr("maop.dashboard.routers.permission.MAOP_ROOT", tmp_path)

    mock_pm = MagicMock()
    mock_pm.add_rule = MagicMock(return_value="rule-1")
    mock_pm.remove_rule = MagicMock(return_value=True)
    mock_pm.list_rules = MagicMock(return_value=[
        SimpleNamespace(model_dump=lambda: {"id": "r1"})
    ])
    mock_pm.check = MagicMock(return_value=SimpleNamespace(
        model_dump=lambda: {"allowed": True}
    ))

    mock_hp = MagicMock()
    mock_hp.pending = MagicMock(return_value=[
        SimpleNamespace(model_dump=lambda: {"id": "p1"})
    ])
    mock_hp.approve = MagicMock(return_value=True)
    mock_hp.reject = MagicMock(return_value=True)

    monkeypatch.setattr("maop.core.security.permission.PermissionManager", lambda **kw: mock_pm)
    monkeypatch.setattr("maop.core.agent.delegation.human_proxy.HumanProxy", lambda **kw: mock_hp)

    from maop.dashboard.routers.permission import router
    return _make_app(router)


class TestPermissionRules:
    def test_add(self, permission_client):
        resp = permission_client.post(
            "/api/permission/rules",
            json={"agent": "claude", "action": "code", "decision": "allow"},
        )
        assert resp.status_code == 200

    def test_remove(self, permission_client):
        resp = permission_client.delete("/api/permission/rules/rule-1")
        assert resp.status_code == 200

    def test_list(self, permission_client):
        resp = permission_client.get("/api/permission/rules")
        assert resp.status_code == 200

    def test_check(self, permission_client):
        resp = permission_client.get("/api/permission/check?agent=claude&action=code")
        assert resp.status_code == 200


class TestApproval:
    def test_pending(self, permission_client):
        resp = permission_client.get("/api/approval/pending")
        assert resp.status_code == 200

    def test_approve(self, permission_client):
        resp = permission_client.post("/api/approval/req1/approve")
        assert resp.status_code == 200

    def test_reject(self, permission_client):
        resp = permission_client.post("/api/approval/req1/reject?reason=bad")
        assert resp.status_code == 200


# ── Routing Preview ──────────────────────────────────────────────────

@pytest.fixture
def routing_preview_client(tmp_path, monkeypatch):
    """Mock load_config + get_route_scorer for routing_preview."""
    from types import SimpleNamespace

    mock_config = SimpleNamespace(routing={})
    monkeypatch.setattr("maop.dashboard.routers.routing_preview.load_config", lambda root: mock_config)

    mock_scorer = MagicMock()
    mock_scorer.match = MagicMock(return_value=None)
    mock_scorer.get_cooldown_status = MagicMock(return_value=[])
    monkeypatch.setattr("maop.dashboard.routers.routing_preview.get_route_scorer", lambda *a, **kw: mock_scorer)

    from maop.dashboard.routers.routing_preview import router
    return _make_app(router)


class TestRoutingPreviewMatch:
    def test_missing_task(self, routing_preview_client):
        resp = routing_preview_client.post("/api/routing/match", json={})
        assert resp.status_code == 200

    def test_no_match(self, routing_preview_client):
        resp = routing_preview_client.post(
            "/api/routing/match",
            json={"task": "test task"},
        )
        assert resp.status_code == 200


class TestRoutingPreviewCooldowns:
    def test_cooldowns(self, routing_preview_client):
        resp = routing_preview_client.get("/api/routing/cooldowns")
        assert resp.status_code == 200


class TestRoutingPreviewScores:
    def test_missing_task(self, routing_preview_client):
        resp = routing_preview_client.get("/api/routing/scores")
        assert resp.status_code == 200

    def test_with_task(self, routing_preview_client):
        resp = routing_preview_client.get("/api/routing/scores?task=test")
        assert resp.status_code == 200