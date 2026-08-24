"""Coverage tests for hook + protocol routers — all POST/GET endpoints.

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


# ── Hook ─────────────────────────────────────────────────────────────

@pytest.fixture
def hook_env(tmp_path, monkeypatch):
    monkeypatch.setattr("maop.dashboard.routers.hook.MAOP_ROOT", tmp_path)
    import maop.dashboard.routers.hook as hook_mod
    hook_mod._hook_mgr = None

    mock_mgr = MagicMock()
    mock_mgr.register = MagicMock(return_value=SimpleNamespace(
        model_dump=lambda: {"id": "h1", "event": "on_complete"}
    ))
    mock_mgr.unregister = MagicMock(return_value=True)
    mock_mgr.enable = MagicMock(return_value=True)
    mock_mgr.disable = MagicMock(return_value=True)
    mock_mgr.list_hooks = MagicMock(return_value=[
        SimpleNamespace(model_dump=lambda: {"id": "h1", "event": "on_complete"})
    ])
    mock_mgr.get_hook = MagicMock(return_value=SimpleNamespace(
        model_dump=lambda: {"id": "h1", "event": "on_complete"}
    ))
    mock_mgr.trigger = AsyncMock(return_value=[
        SimpleNamespace(model_dump=lambda: {"ok": True})
    ])
    mock_mgr.get_logs = MagicMock(return_value=[{"ts": "2026-01-01", "event": "on_complete"}])

    monkeypatch.setattr("maop.dashboard.routers.hook._get_hook_mgr", lambda: mock_mgr)
    return mock_mgr


@pytest.fixture
def hook_client(hook_env):
    from maop.dashboard.routers.hook import router
    return _make_app(router)


class TestHookRegister:
    def test_missing_event(self, hook_client):
        resp = hook_client.post("/api/hook/register", json={})
        assert resp.status_code == 400

    def test_missing_url_and_callback(self, hook_client):
        resp = hook_client.post("/api/hook/register", json={"event": "on_complete"})
        assert resp.status_code == 400

    def test_happy_with_url(self, hook_client):
        resp = hook_client.post(
            "/api/hook/register",
            json={"event": "on_complete", "url": "http://example.com"},
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"

    def test_happy_with_callback(self, hook_client):
        resp = hook_client.post(
            "/api/hook/register",
            json={"event": "on_complete", "callback": "some_func"},
        )
        assert resp.status_code == 200


class TestHookUnregister:
    def test_missing_id(self, hook_client):
        resp = hook_client.post("/api/hook/unregister", json={})
        assert resp.status_code == 400

    def test_happy(self, hook_client):
        resp = hook_client.post("/api/hook/unregister", json={"id": "h1"})
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"

    def test_not_found(self, hook_env, hook_client):
        hook_env.unregister.return_value = False
        resp = hook_client.post("/api/hook/unregister", json={"id": "nonexistent"})
        assert resp.status_code == 200
        assert resp.json()["status"] == "not_found"


class TestHookEnable:
    def test_missing_id(self, hook_client):
        resp = hook_client.post("/api/hook/enable", json={})
        assert resp.status_code == 400

    def test_happy(self, hook_client):
        resp = hook_client.post("/api/hook/enable", json={"id": "h1"})
        assert resp.status_code == 200

    def test_not_found(self, hook_env, hook_client):
        hook_env.enable.return_value = False
        resp = hook_client.post("/api/hook/enable", json={"id": "nonexistent"})
        assert resp.status_code == 200


class TestHookDisable:
    def test_missing_id(self, hook_client):
        resp = hook_client.post("/api/hook/disable", json={})
        assert resp.status_code == 400

    def test_happy(self, hook_client):
        resp = hook_client.post("/api/hook/disable", json={"id": "h1"})
        assert resp.status_code == 200


class TestHookList:
    def test_list_all(self, hook_client):
        resp = hook_client.get("/api/hook/list")
        assert resp.status_code == 200
        assert resp.json()["count"] >= 1

    def test_list_with_event(self, hook_client):
        resp = hook_client.get("/api/hook/list?event=on_complete")
        assert resp.status_code == 200


class TestHookGet:
    def test_missing_id(self, hook_client):
        resp = hook_client.get("/api/hook/get")
        assert resp.status_code == 400

    def test_happy(self, hook_client):
        resp = hook_client.get("/api/hook/get?hook_id=h1")
        assert resp.status_code == 200

    def test_not_found(self, hook_env, hook_client):
        hook_env.get_hook.return_value = None
        resp = hook_client.get("/api/hook/get?hook_id=nonexistent")
        assert resp.status_code == 404


class TestHookTrigger:
    def test_missing_event(self, hook_client):
        resp = hook_client.post("/api/hook/trigger", json={})
        assert resp.status_code == 400

    def test_happy(self, hook_client):
        resp = hook_client.post(
            "/api/hook/trigger",
            json={"event": "on_complete", "data": {"x": 1}},
        )
        assert resp.status_code == 200


class TestHookLogs:
    def test_logs(self, hook_client):
        resp = hook_client.get("/api/hook/logs")
        assert resp.status_code == 200

    def test_logs_with_event(self, hook_client):
        resp = hook_client.get("/api/hook/logs?event=on_complete")
        assert resp.status_code == 200


class TestHookEvents:
    def test_events(self, hook_client):
        resp = hook_client.get("/api/hook/events")
        assert resp.status_code == 200
        assert resp.json()["count"] >= 1


# ── Protocol ─────────────────────────────────────────────────────────

@pytest.fixture
def protocol_env(tmp_path, monkeypatch):
    monkeypatch.setattr("maop.dashboard.routers.protocol.MAOP_ROOT", tmp_path)
    import maop.dashboard.routers.protocol as proto_mod
    proto_mod._protocol_reg = None

    mock_reg = MagicMock()
    mock_reg.register = MagicMock(return_value=SimpleNamespace(
        model_dump=lambda: {"name": "test-proto", "version": "1.0"}
    ))
    mock_reg.unregister = MagicMock(return_value=True)
    mock_reg.get = MagicMock(return_value=SimpleNamespace(
        model_dump=lambda: {"name": "test-proto", "version": "1.0"}
    ))
    mock_reg.list_protocols = MagicMock(return_value=[
        SimpleNamespace(model_dump=lambda: {"name": "p1", "version": "1.0"})
    ])
    mock_reg.list_versions = MagicMock(return_value=["1.0", "2.0"])
    mock_reg.validate = MagicMock(return_value=True)
    mock_reg.send_message = MagicMock(return_value=SimpleNamespace(
        model_dump=lambda: {"id": "m1", "protocol": "test-proto"}
    ))
    mock_reg.get_messages = MagicMock(return_value=[
        SimpleNamespace(model_dump=lambda: {"id": "m1", "protocol": "test-proto"})
    ])

    monkeypatch.setattr("maop.dashboard.routers.protocol._get_protocol_reg", lambda: mock_reg)
    return mock_reg


@pytest.fixture
def protocol_client(protocol_env):
    from maop.dashboard.routers.protocol import router
    return _make_app(router)


class TestProtocolRegister:
    def test_missing_name(self, protocol_client):
        resp = protocol_client.post("/api/protocol/register", json={})
        assert resp.status_code == 400

    def test_happy(self, protocol_client):
        resp = protocol_client.post(
            "/api/protocol/register",
            json={"name": "test-proto", "version": "1.0"},
        )
        assert resp.status_code == 200

    def test_with_schema(self, protocol_client):
        resp = protocol_client.post(
            "/api/protocol/register",
            json={"name": "p", "version": "1.0", "schema": {"type": "object"}},
        )
        assert resp.status_code == 200


class TestProtocolUnregister:
    def test_missing_name(self, protocol_client):
        resp = protocol_client.post("/api/protocol/unregister", json={})
        assert resp.status_code == 400

    def test_happy(self, protocol_client):
        resp = protocol_client.post(
            "/api/protocol/unregister",
            json={"name": "test-proto", "version": "1.0"},
        )
        assert resp.status_code == 200

    def test_not_found(self, protocol_env, protocol_client):
        protocol_env.unregister.return_value = False
        resp = protocol_client.post(
            "/api/protocol/unregister",
            json={"name": "nonexistent"},
        )
        assert resp.status_code == 200


class TestProtocolGet:
    def test_missing_name(self, protocol_client):
        resp = protocol_client.get("/api/protocol/get")
        assert resp.status_code == 400

    def test_happy(self, protocol_client):
        resp = protocol_client.get("/api/protocol/get?name=test-proto")
        assert resp.status_code == 200

    def test_not_found(self, protocol_env, protocol_client):
        protocol_env.get.return_value = None
        resp = protocol_client.get("/api/protocol/get?name=nonexistent")
        assert resp.status_code == 404


class TestProtocolList:
    def test_list(self, protocol_client):
        resp = protocol_client.get("/api/protocol/list")
        assert resp.status_code == 200
        assert resp.json()["count"] >= 1


class TestProtocolVersions:
    def test_missing_name(self, protocol_client):
        resp = protocol_client.get("/api/protocol/versions")
        assert resp.status_code == 400

    def test_happy(self, protocol_client):
        resp = protocol_client.get("/api/protocol/versions?name=test-proto")
        assert resp.status_code == 200


class TestProtocolValidate:
    def test_missing_protocol(self, protocol_client):
        resp = protocol_client.post("/api/protocol/validate", json={})
        assert resp.status_code == 400

    def test_happy(self, protocol_client):
        resp = protocol_client.post(
            "/api/protocol/validate",
            json={"protocol": "test-proto", "payload": {}},
        )
        assert resp.status_code == 200


class TestProtocolSend:
    def test_missing_fields(self, protocol_client):
        resp = protocol_client.post("/api/protocol/send", json={})
        assert resp.status_code == 400

    def test_missing_recipient(self, protocol_client):
        resp = protocol_client.post(
            "/api/protocol/send",
            json={"protocol": "p", "sender": "s"},
        )
        assert resp.status_code == 400

    def test_happy(self, protocol_client):
        resp = protocol_client.post(
            "/api/protocol/send",
            json={"protocol": "p", "sender": "s", "recipient": "r"},
        )
        assert resp.status_code == 200


class TestProtocolMessages:
    def test_missing_recipient(self, protocol_client):
        resp = protocol_client.get("/api/protocol/messages")
        assert resp.status_code == 400

    def test_happy(self, protocol_client):
        resp = protocol_client.get("/api/protocol/messages?recipient=r1")
        assert resp.status_code == 200

    def test_with_protocol(self, protocol_client):
        resp = protocol_client.get("/api/protocol/messages?recipient=r1&protocol=p1")
        assert resp.status_code == 200