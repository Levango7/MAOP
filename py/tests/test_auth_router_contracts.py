"""HTTP contracts for the auth / user-management endpoints the console calls.

Eight allowlist entries are burned here: ``POST /api/auth/login``,
``POST /api/auth/refresh``, ``POST /api/auth/logout``,
``POST /api/auth/register``, ``GET /api/auth/status``,
``GET /api/auth/users``, ``DELETE`` and ``PUT`` on
``/api/auth/users/{username}``.

Producer facts this file exists to pin:

1. ``GET /api/auth/status`` has **no** ``status`` key. App/Agents/Chat/Settings
   read only ``auth_enabled`` / ``has_token``; adding an envelope field would
   promise a key the handler never sends.
2. ``users.created_at`` is epoch **seconds** (``time.time()`` into a ``REAL``
   column). ``Users.vue`` multiplies by 1000; a milliseconds change would make
   every row render as 1970.
3. ``/api/auth/refresh`` must accept the httpOnly cookie: the M6 dashboard never
   sets an ``Authorization`` header (``stores/api.js`` has no token to send), so
   a header-only refresh is dead in production.

The row shape is asserted against ``auth_service.db_list_users()`` over the
per-test isolated SQLite file (``conftest`` points ``MAOP_DATA_DIR`` at a tmp
dir), never against a hand-written mock.
"""

from __future__ import annotations

import inspect
import re
import time
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from maop.core.backends.db_utils import get_db_path
from maop.dashboard.routers import auth as auth_router
from maop.dashboard.routers.auth_schemas import (
    AuthLogoutEnvelope,
    AuthMutationEnvelope,
    AuthRegisterEnvelope,
    AuthStatusPayload,
    AuthTokenEnvelope,
    AuthUsersEnvelope,
)
from maop.dashboard.services import auth_service

CONTRACT_ROUTES: list[tuple[str, str]] = [
    ("POST", "/api/auth/login"),
    ("POST", "/api/auth/logout"),
    ("POST", "/api/auth/refresh"),
    ("POST", "/api/auth/register"),
    ("GET", "/api/auth/status"),
    ("GET", "/api/auth/users"),
    ("DELETE", "/api/auth/users/{username}"),
    ("PUT", "/api/auth/users/{username}"),
]

VIEW_FILE = (
    Path(__file__).resolve().parents[2]
    / "dashboard-enterprise"
    / "src"
    / "views"
    / "Users.vue"
)

#: The literal the register branch feeds to ``api.post``.
_REGISTER_BODY_RE = re.compile(
    r"api\.post\('/api/auth/register', \{(.*?)\}\);", re.DOTALL,
)


@pytest.fixture(autouse=True)
def _reset_auth_singletons():
    """Bind the lazily-built manager to this test's tmp ``MAOP_DATA_DIR``."""
    auth_router._auth_mgr = None
    auth_router._login_failures_table_ready = False
    auth_service._auth_mgr = None
    yield
    auth_router._auth_mgr = None
    auth_service._auth_mgr = None


def _make_app(roles: list[str] | None = None, identity: str = "admin") -> TestClient:
    app = FastAPI()

    @app.middleware("http")
    async def _inject_auth(request, call_next):
        request.state.auth_roles = roles if roles is not None else ["admin"]
        request.state.auth_identity = identity
        request.state.tenant_id = ""
        return await call_next(request)

    app.include_router(auth_router.router)
    return TestClient(app)


@pytest.fixture
def client() -> TestClient:
    return _make_app()


def _register(
    client: TestClient,
    username: str = "bob",
    password: str = "secret123",
    roles: tuple[str, ...] = ("read",),
) -> dict[str, Any]:
    resp = client.post(
        "/api/auth/register",
        json={"username": username, "password": password, "roles": list(roles)},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


def _users(client: TestClient) -> list[dict[str, Any]]:
    body = client.get("/api/auth/users").json()
    assert body["status"] == "ok"
    return body["users"]


def _row(client: TestClient, username: str) -> dict[str, Any]:
    matches = [u for u in _users(client) if u["username"] == username]
    assert matches, f"{username} missing from {_users(client)}"
    return matches[0]


# ── the row speaks the vocabulary the view reads ──────────────────


def test_the_user_row_carries_every_key_the_view_reads(client) -> None:
    _register(client)
    row = _row(client, "bob")
    source = VIEW_FILE.read_text(encoding="utf-8")
    read_keys = set(re.findall(r"\bu\.([A-Za-z_][A-Za-z0-9_]*)", source))
    assert read_keys, "the scan found no user property access — the view moved?"
    assert read_keys <= set(row), f"view reads keys the API never sends: {read_keys - set(row)}"


def test_the_row_keeps_every_producer_column(client) -> None:
    """``db_list_users()`` is the producer; no column may be dropped en route."""
    _register(client)
    raw = [u for u in auth_service.db_list_users(str(get_db_path("auth"))) if u["username"] == "bob"]
    assert len(raw) == 1
    row = _row(client, "bob")
    assert set(raw[0]) == set(row), set(raw[0]) ^ set(row)
    assert row["roles"] == ["read"]
    assert row["enabled"] is True
    assert row["tenant_id"] == ""


def test_the_row_has_no_last_login(client) -> None:
    """The removed table column: no producer anywhere fills it, and the view
    no longer asks for it. Re-adding it requires a schema migration first."""
    _register(client)
    assert "last_login" not in _row(client, "bob")
    source = VIEW_FILE.read_text(encoding="utf-8")
    assert "last_login" not in source
    assert "users-cell--login" not in source


def test_created_at_is_epoch_seconds(client) -> None:
    """``Users.vue`` renders ``new Date(created_at * 1000)``. If the producer
    ever switches to milliseconds (or an ISO string) every row jumps to 1970."""
    before = time.time()
    _register(client)
    created = _row(client, "bob")["created_at"]
    after = time.time()
    assert isinstance(created, (int, float)) and not isinstance(created, bool)
    assert before - 5 <= created <= after + 5, created
    source = VIEW_FILE.read_text(encoding="utf-8")
    assert "new Date(n * 1000)" in source, "the seconds→ms conversion moved"


# ── envelopes ─────────────────────────────────────────────────────


def test_the_status_payload_is_the_two_keys_the_views_read(client) -> None:
    body = client.get("/api/auth/status").json()
    assert set(body) == {"auth_enabled", "has_token"}, body
    assert "status" not in body
    op = client.app.openapi()["paths"]["/api/auth/status"]["get"]
    schema = op["responses"]["200"]["content"]["application/json"]["schema"]
    props = client.app.openapi()["components"]["schemas"][
        schema["$ref"].rsplit("/", 1)[-1]
    ]["properties"]
    assert set(props) == {"auth_enabled", "has_token"}, props


def test_register_returns_the_producer_envelope(client) -> None:
    body = _register(client)
    raw = auth_service.db_register_user(
        str(get_db_path("auth")), "carol", "secret123", ["read"],
    )
    assert set(body) == set(raw), set(body) ^ set(raw)
    assert body["status"] == "ok"
    assert body["username"] == "bob"
    assert body["roles"] == ["read"]
    assert body["tenant_id"] == ""


def test_the_update_returns_the_mutation_envelope(client) -> None:
    _register(client)
    body = client.put("/api/auth/users/bob", json={"roles": ["write"]}).json()
    assert set(body) == {"status", "message"}, body
    assert body["status"] == "ok"
    assert _row(client, "bob")["roles"] == ["write"]


def test_the_update_round_trips_enabled(client) -> None:
    _register(client)
    body = client.put("/api/auth/users/bob", json={"enabled": False}).json()
    assert body["status"] == "ok"
    assert _row(client, "bob")["enabled"] is False


def test_the_delete_confirms_what_it_removed(client) -> None:
    _register(client)
    body = client.delete("/api/auth/users/bob").json()
    assert set(body) == {"status", "message"}, body
    assert body["status"] == "ok"
    assert "bob" not in {u["username"] for u in _users(client)}


# ── login / refresh / logout ──────────────────────────────────────


def _login(client: TestClient, username: str = "bob", password: str = "secret123"):
    return client.post("/api/auth/login", json={"username": username, "password": password})


def test_login_returns_the_token_envelope_and_sets_the_cookie(client) -> None:
    _register(client)
    resp = _login(client)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert set(body) == {"status", "token", "username", "roles", "expires_in"}, body
    assert body["status"] == "ok"
    assert body["username"] == "bob"
    assert body["roles"] == ["read"]
    assert body["expires_in"] == int(auth_service._JWT_TTL_S)
    assert isinstance(body["token"], str) and body["token"]

    cookie = resp.headers["set-cookie"]
    assert "maop_token=" in cookie
    assert "HttpOnly" in cookie
    assert "SameSite=strict" in cookie
    assert "Path=/" in cookie


def test_login_failure_is_the_error_schema(client) -> None:
    _register(client)
    resp = _login(client, password="wrong-password")
    assert resp.status_code == 401, resp.text
    body = resp.json()
    assert set(body) == {"status", "error", "code", "detail", "request_id"}, body
    assert body["status"] == "error"
    assert body["code"] == "HTTP_401"
    assert body["error"]


def test_refresh_accepts_the_cookie_the_dashboard_actually_sends(client) -> None:
    """M6: the view has no token to put in an Authorization header, so the
    cookie path is the only one production can take."""
    _register(client)
    old = _login(client).json()["token"]
    resp = client.post("/api/auth/refresh")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert set(body) == {"status", "token", "username", "roles", "expires_in"}, body
    assert body["status"] == "ok"
    assert body["token"] != old
    assert "maop_token=" in resp.headers["set-cookie"]

    handler = auth_service.get_auth_mgr().jwt_handler
    assert handler.validate_token(old).authenticated is False
    assert handler.validate_token(body["token"]).authenticated is True


def test_refresh_still_accepts_the_authorization_header() -> None:
    admin = _make_app()
    _register(admin)
    token = _login(admin).json()["token"]
    # A separate client has no cookie jar, so only the header can authenticate.
    header_only = _make_app()
    resp = header_only.post(
        "/api/auth/refresh",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "ok"


def test_refresh_without_a_credential_is_401(client) -> None:
    resp = client.post("/api/auth/refresh")
    assert resp.status_code == 401, resp.text
    assert resp.json()["status"] == "error"


def test_logout_revokes_the_cookie_token_and_clears_it(client) -> None:
    _register(client)
    token = _login(client).json()["token"]
    resp = client.post("/api/auth/logout")
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"status": "ok", "message": "Token revoked."}
    cookie = resp.headers["set-cookie"]
    assert "maop_token=" in cookie
    assert "Max-Age=0" in cookie
    assert auth_service.get_auth_mgr().jwt_handler.validate_token(token).authenticated is False


def test_the_token_endpoints_return_models_instead_of_raw_responses() -> None:
    """A ``JSONResponse`` return would bypass ``response_model`` — the model
    would be declared in OpenAPI and never executed."""
    for fn, model in (
        (auth_router.auth_login, "AuthTokenEnvelope"),
        (auth_router.auth_refresh, "AuthTokenEnvelope"),
        (auth_router.auth_logout, "AuthLogoutEnvelope"),
    ):
        source = inspect.getsource(fn)
        assert "JSONResponse" not in source, fn.__name__
        assert f"return {model}(" in source, fn.__name__


# ── request shapes ────────────────────────────────────────────────


def test_the_view_request_bodies_match_the_declared_request_models(client) -> None:
    schema = client.app.openapi()
    components = schema["components"]["schemas"]

    def _declared(method: str, template: str) -> dict:
        op = schema["paths"][template][method]
        body = op["requestBody"]["content"]["application/json"]["schema"]
        ref = body.get("$ref")
        return components[ref.rsplit("/", 1)[-1]] if ref else body

    create = _declared("post", "/api/auth/register")
    assert set(create["properties"]) == {"username", "password", "roles"}
    assert create.get("additionalProperties") is False

    update = _declared("put", "/api/auth/users/{username}")
    assert set(update["properties"]) == {"roles", "enabled", "password"}
    assert update.get("additionalProperties") is False

    source = VIEW_FILE.read_text(encoding="utf-8")
    match = _REGISTER_BODY_RE.search(source)
    assert match, "the register payload literal moved — update this scan"
    sent = set(re.findall(r"^\s*([A-Za-z_][A-Za-z0-9_]*):", match.group(1), re.MULTILINE))
    assert sent == {"username", "password", "roles"}, sent
    assert sent <= set(create["properties"])
    # The edit branch sends ``{roles}`` plus an optional password — never the
    # username (it is in the path) and never ``enabled``.
    assert "const body = { roles: form.value.roles };" in source
    assert "if (form.value.password) body.password = form.value.password;" in source
    assert "body.username" not in source
    assert "body.enabled" not in source


def test_register_rejects_unknown_fields(client) -> None:
    """A typo (``role`` vs ``roles``) used to be dropped in silence and the
    user created with the default role set."""
    resp = client.post(
        "/api/auth/register",
        json={"username": "bob", "password": "secret123", "role": ["write"]},
    )
    assert resp.status_code == 422, resp.text
    assert "bob" not in {u["username"] for u in _users(client)}


def test_register_still_refuses_privileged_roles(client) -> None:
    resp = client.post(
        "/api/auth/register",
        json={"username": "mallory", "password": "secret123", "roles": ["admin"]},
    )
    assert resp.status_code == 400, resp.text
    assert "mallory" not in {u["username"] for u in _users(client)}


def test_the_update_rejects_unknown_fields(client) -> None:
    """``{"role": [...]}`` previously returned ``status: ok`` while changing
    nothing — the operator would believe the role was granted."""
    _register(client)
    resp = client.put("/api/auth/users/bob", json={"role": ["write"]})
    assert resp.status_code == 422, resp.text
    assert _row(client, "bob")["roles"] == ["read"]


# ── error branches bypass the envelope, keeping their status ──────


def test_errors_bypass_the_envelope_and_keep_their_status(client) -> None:
    resp = client.put("/api/auth/users/does-not-exist", json={"roles": ["read"]})
    assert resp.status_code == 404
    assert resp.json()["status"] == "error"

    resp = client.delete("/api/auth/users/does-not-exist")
    assert resp.status_code == 404

    resp = client.delete("/api/auth/users/admin")
    assert resp.status_code == 403


def test_envelope_defaults_are_the_error_branch_defaults() -> None:
    """A missing ``status`` must not default to ``"ok"``."""
    for model in (
        AuthRegisterEnvelope,
        AuthUsersEnvelope,
        AuthMutationEnvelope,
        AuthTokenEnvelope,
        AuthLogoutEnvelope,
    ):
        assert model.model_fields["status"].default == "error", model.__name__
    assert AuthMutationEnvelope.model_fields["message"].default == ""
    assert AuthRegisterEnvelope.model_fields["username"].default == ""
    assert AuthTokenEnvelope.model_fields["token"].default == ""
    assert AuthTokenEnvelope.model_fields["expires_in"].default == 0
    # The status payload deliberately has no error-envelope fields at all.
    assert "status" not in AuthStatusPayload.model_fields
    assert "error" not in AuthStatusPayload.model_fields


def test_all_contract_routes_declare_a_response_model(client) -> None:
    schema = client.app.openapi()
    problems: list[str] = []
    for method, template in CONTRACT_ROUTES:
        op = (schema["paths"].get(template) or {}).get(method.lower())
        if op is None:
            problems.append(f"MISSING {method} {template}")
            continue
        body = ((op.get("responses") or {}).get("200", {}).get("content") or {}).get(
            "application/json", {},
        ).get("schema") or {}
        if "$ref" not in body and "properties" not in body:
            problems.append(f"UNTYPED {method} {template}")
    assert problems == []
