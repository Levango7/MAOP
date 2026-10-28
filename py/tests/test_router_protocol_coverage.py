"""Coverage tests for the protocol router — all POST/GET endpoints.

Uses isolated MAOP_ROOT + admin role injection + mocked managers.

（原 hook 段已随 ``routers/hook.py`` 的删除同步移除：单数 ``/api/hook/*``
协议并入复数 ``/api/hooks``，前端全量迁移；对应覆盖见 hooks 相关测试。）
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

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


# ── Protocol ─────────────────────────────────────────────────────────

@pytest.fixture
def protocol_env(tmp_path, monkeypatch):
    monkeypatch.setattr("maop.dashboard.routers.state.MAOP_ROOT", tmp_path)
    from maop.dashboard.services import routing_service
    routing_service._protocol_reg = None

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

    monkeypatch.setattr("maop.dashboard.services.routing_service._get_protocol_reg", lambda: mock_reg)
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
        assert resp.status_code == 404


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
