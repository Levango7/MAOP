"""HTTP contracts for the two ``/api/info/edition`` endpoints.

The GET payload is compared against ``edition_info()`` itself (the producer),
not against a hand-written dict: ``enterprise_available`` is API-only, and a
model that dropped it would silently narrow the response.

The POST branch is the edition switch: 400 for an unknown value, 403 when
enterprise is requested without a license. Both are ``HTTPException`` responses
rendered as ErrorSchema and must keep their status codes.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from maop.config import edition as edition_mod
from maop.config.edition import Edition
from maop.dashboard.routers.info import admin as info_admin
from maop.dashboard.routers.info import meta as info_meta
from maop.dashboard.routers.info_schemas import EditionInfoPayload, EditionSwitchPayload

ERROR_KEYS = {"status", "error", "code", "detail", "request_id"}


def _make_app(roles: list[str] | None = None) -> TestClient:
    app = FastAPI()

    @app.middleware("http")
    async def _inject_auth(request, call_next):
        request.state.auth_roles = roles if roles is not None else ["admin"]
        request.state.auth_identity = "tester"
        request.state.tenant_id = ""
        return await call_next(request)

    app.include_router(info_meta.router)
    app.include_router(info_admin.router)
    return TestClient(app)


@pytest.fixture
def client() -> TestClient:
    return _make_app()


@pytest.fixture(autouse=True)
def _isolate_edition(tmp_path, monkeypatch):
    """Keep the switch endpoint from writing audit logs into the repo."""
    previous = edition_mod.get_edition()
    monkeypatch.setattr(info_admin, "MAOP_ROOT", tmp_path)
    yield
    edition_mod.set_edition(previous)


def _openapi_props(client: TestClient, method: str) -> dict[str, Any]:
    schema = client.app.openapi()
    op = schema["paths"]["/api/info/edition"][method]
    ref = op["responses"]["200"]["content"]["application/json"]["schema"]["$ref"]
    return schema["components"]["schemas"][ref.rsplit("/", 1)[-1]]["properties"]


def test_the_get_payload_equals_the_producer(client) -> None:
    resp = client.get("/api/info/edition")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body == edition_mod.edition_info(), set(body) ^ set(edition_mod.edition_info())
    assert body["edition"] in {"personal", "enterprise"}
    assert isinstance(body["features"], dict)
    assert isinstance(body["backends"], dict)
    assert isinstance(body["degradations"], list)


def test_the_get_payload_keeps_the_api_only_key(client) -> None:
    body = client.get("/api/info/edition").json()
    assert "enterprise_available" in body
    assert isinstance(body["enterprise_available"], bool)


def test_non_admin_cannot_read_or_switch_edition() -> None:
    reader = _make_app(roles=["read"])
    resp = reader.get("/api/info/edition")
    assert resp.status_code == 403, resp.text
    assert set(resp.json()) == ERROR_KEYS

    resp = reader.post("/api/info/edition", json={"edition": "personal"})
    assert resp.status_code == 403, resp.text


def test_switching_to_personal_returns_the_switch_envelope(client) -> None:
    before = edition_mod.get_edition().value
    resp = client.post("/api/info/edition", json={"edition": "personal"})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert set(body) == {"status", "edition", "previous", "requested", "degraded"}, body
    assert body["status"] == "ok"
    assert body["edition"] == "personal"
    assert body["previous"] == before
    assert body["requested"] == "personal"
    assert body["degraded"] is False
    assert edition_mod.get_edition() is Edition.PERSONAL


def test_invalid_edition_is_400_and_does_not_switch(client) -> None:
    before = edition_mod.get_edition()
    resp = client.post("/api/info/edition", json={"edition": "galactic"})
    assert resp.status_code == 400, resp.text
    assert set(resp.json()) == ERROR_KEYS
    assert edition_mod.get_edition() is before


def test_enterprise_without_a_license_is_403_and_keeps_personal(client, monkeypatch) -> None:
    monkeypatch.setattr(
        edition_mod, "_detect_with_license_check", lambda target: Edition.PERSONAL,
    )
    resp = client.post("/api/info/edition", json={"edition": "enterprise"})
    assert resp.status_code == 403, resp.text
    assert set(resp.json()) == ERROR_KEYS
    assert edition_mod.get_edition() is Edition.PERSONAL


def test_both_routes_declare_the_exact_producer_keys(client) -> None:
    assert set(_openapi_props(client, "get")) == set(EditionInfoPayload.model_fields)
    assert set(_openapi_props(client, "post")) == set(EditionSwitchPayload.model_fields)
    assert "status" not in _openapi_props(client, "get")


def test_switch_envelope_defaults_to_error() -> None:
    assert EditionSwitchPayload.model_fields["status"].default == "error"
    assert EditionSwitchPayload.model_fields["degraded"].default is False
