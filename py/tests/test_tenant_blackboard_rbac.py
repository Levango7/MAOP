"""RBAC on ``/api/tenant-blackboard/*`` — real guard + M3 enforcer injection.

Two themes live here:

1. The tenant roster and any single tenant's snapshot are cross-tenant reads:
   they hand the caller other tenants' identifiers, entry counts and content.
   Until 2026-10-24 they carried no guard at all, while ``DELETE`` and
   ``/context/validate`` did — and ``test_coordination_m4``'s ``admin_client``
   fixture replaced ``require_admin`` with a no-op, so nothing in the suite could
   tell a guarded endpoint from an unguarded one. Here the guard is real: role
   comes from ``request.state.auth_roles`` exactly as AuthMiddleware sets it.

2. The MAOS M3 readiness doc (待决 #2, option B) asks MAOP to inject the
   enterprise enforcer into ``TenantBlackboard``. ``get_tenant`` now threads the
   authenticated caller's context into the service, which lazily imports
   ``maop.enterprise.rbac_m3_adapter.create_m3_enforcer()`` — MAOP never imports
   enterprise at module scope. ``FallbackRBACEnforcer`` is pinned fail-closed
   (待决 #3: its docstring used to promise the opposite).
"""

from __future__ import annotations

import ast
import asyncio
import inspect
import pathlib
import sys
import textwrap
import types
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from maop.core.reliability.blackboard import Blackboard, get_blackboard
from maop.core.reliability.coordination_tenant import (
    FallbackRBACEnforcer,
    RBACPermission,
    TenantBlackboard,
    TenantContext,
)
from maop.dashboard.routers import tenant_blackboard as route_mod
from maop.dashboard.services import tenant_blackboard_service

TENANT_DATA_ENDPOINTS: list[tuple[str, str]] = [
    ("get", "/api/tenant-blackboard/tenants"),
    ("get", "/api/tenant-blackboard/tenants/any-tenant"),
    ("delete", "/api/tenant-blackboard/tenants/any-tenant"),
]


def _make_app(roles: list[str]) -> TestClient:
    app = FastAPI()

    @app.middleware("http")
    async def _inject_auth(request, call_next):
        request.state.auth_roles = roles
        request.state.auth_identity = "u1"
        request.state.tenant_id = "tenant-a"
        return await call_next(request)

    app.include_router(route_mod.router)
    return TestClient(app)


@pytest.fixture
def reader() -> TestClient:
    """A non-admin principal — what MAOP_AUTH=0 hands every caller."""
    return _make_app(roles=["read"])


@pytest.fixture
def admin() -> TestClient:
    return _make_app(roles=["admin"])


@pytest.fixture
def clean_board():
    bb = get_blackboard()
    for domain in list(bb._entries.keys()):
        asyncio.run(bb.clear(domain))
    yield bb
    for domain in list(bb._entries.keys()):
        asyncio.run(bb.clear(domain))


def _seed(bb, tenant_id: str, domain: str, content: dict[str, Any]) -> None:
    ctx = TenantContext(
        tenant_id=tenant_id,
        user_id=f"{tenant_id}-writer",
        permissions=frozenset({RBACPermission.BB_WRITE, RBACPermission.BB_READ}),
    )
    asyncio.run(TenantBlackboard(bb, ctx).write(domain, content))


@pytest.fixture
def bb_with_data():
    """An isolated blackboard holding exactly one entry tagged to ``acme``."""
    bb = Blackboard()
    ctx = TenantContext(
        tenant_id="acme",
        user_id="seed",
        permissions=frozenset({RBACPermission.BB_WRITE, RBACPermission.BB_READ}),
    )
    asyncio.run(TenantBlackboard(bb, ctx).write("decision", {"verdict": "approve"}))
    return bb


# ── the guard ─────────────────────────────────────────────────────


def test_read_only_caller_is_refused_every_tenant_data_endpoint(reader) -> None:
    for method, path in TENANT_DATA_ENDPOINTS:
        resp = getattr(reader, method)(path)
        assert resp.status_code == 403, f"{method.upper()} {path} -> {resp.status_code}"
        assert resp.json()["status"] == "error"


def test_admin_reaches_the_same_endpoints(admin) -> None:
    assert admin.get("/api/tenant-blackboard/tenants").json()["status"] == "ok"
    assert admin.get("/api/tenant-blackboard/tenants/ghost").json()["status"] == "ok"


def test_superadmin_counts_as_admin() -> None:
    """RBAC hierarchy: superadmin must not be locked out of the admin surface."""
    client = _make_app(roles=["superadmin"])
    assert client.get("/api/tenant-blackboard/tenants").status_code == 200


def test_no_roles_at_all_is_refused(reader) -> None:
    client = _make_app(roles=[])
    assert client.get("/api/tenant-blackboard/tenants").status_code == 403


def test_permission_enum_listing_stays_open(reader) -> None:
    """Not tenant data: the static RBACPermission values. Guarding it would buy
    nothing and this endpoint is used to build forms."""
    resp = reader.get("/api/tenant-blackboard/rbac/permissions")
    assert resp.status_code == 200
    listing = resp.json()["data"]
    assert {"name": "BB_READ", "value": RBACPermission.BB_READ.value} in listing, listing


def test_context_validate_still_requires_admin(reader, admin) -> None:
    body = {
        "tenant_id": "tenant-a",
        "user_id": "u1",
        "roles": ["user"],
        "permissions": [RBACPermission.BB_READ.value],
    }
    assert reader.post("/api/tenant-blackboard/context/validate", json=body).status_code == 403
    resp = admin.post("/api/tenant-blackboard/context/validate", json=body)
    assert resp.status_code == 200, resp.text
    assert resp.json()["data"]["tenant_id"] == "tenant-a"


# ── the destructive path ──────────────────────────────────────────


def test_delete_removes_only_the_named_tenant(admin, clean_board) -> None:
    """Tenant isolation survives the rewrite onto ``TenantBlackboard.clear``."""
    _seed(clean_board, "tenant-a", "decision", {"v": "keep-me"})
    _seed(clean_board, "tenant-a", "evidence", {"v": "keep-too"})
    _seed(clean_board, "tenant-b", "decision", {"v": "purge-me"})

    resp = admin.delete("/api/tenant-blackboard/tenants/tenant-b")
    assert resp.status_code == 200, resp.text
    assert resp.json()["data"]["removed_entries"] == 1

    survivors = {
        e.metadata.get("_tenant_id")
        for domain in list(clean_board._entries.keys())
        for e in clean_board.read(domain)
    }
    assert survivors == {"tenant-a"}
    assert [e.content["v"] for e in clean_board.read("decision")] == ["keep-me"]


def test_delete_of_an_unknown_tenant_removes_nothing(admin, clean_board) -> None:
    _seed(clean_board, "tenant-a", "decision", {"v": "keep-me"})
    resp = admin.delete("/api/tenant-blackboard/tenants/tenant-zzz")
    assert resp.status_code == 200, resp.text
    assert resp.json()["data"]["removed_entries"] == 0
    assert len(clean_board.read("decision")) == 1


def test_service_deletes_through_the_rbac_wrapper() -> None:
    """The hand-rolled store mutation is gone, not just unused.

    ``bb._lock`` / ``bb._entries`` in the service meant the tenant filter and
    the ``BB_ADMIN`` check lived outside ``TenantBlackboard``, duplicating the
    rule it enforces. Measured on the executable statements only — the
    function's own docstring names those attributes to explain the fix.
    """
    tree = ast.parse(textwrap.dedent(inspect.getsource(tenant_blackboard_service.delete_tenant_data)))
    body = tree.body[0].body
    if isinstance(body[0], ast.Expr):  # docstring
        body = body[1:]
    code = "\n".join(ast.unparse(stmt) for stmt in body)
    assert "bb._entries" not in code, code
    assert "bb._lock" not in code, code
    assert "await tbb.clear(domain)" in code, code


# ── G2: enterprise enforcer resolution (lazy import, singleton manager) ──


def test_no_top_level_enterprise_import() -> None:
    """The enterprise adapter must be imported lazily, not at module scope.

    AST over the module body — a grep would also match the docstring and the
    lazy import inside ``resolve_m3_enforcer``, which are exactly the cases we
    want to allow. A personal build must import this module cleanly.
    """
    tree = ast.parse(pathlib.Path(tenant_blackboard_service.__file__).read_text(encoding="utf-8"))
    offenders = [
        node
        for node in tree.body
        if isinstance(node, ast.ImportFrom) and "enterprise" in (node.module or "")
    ]
    assert offenders == []


def test_resolve_returns_none_without_enterprise_package() -> None:
    with patch.dict(sys.modules, {"maop.enterprise.rbac_m3_adapter": None}):
        assert tenant_blackboard_service.resolve_m3_enforcer() is None


def test_resolve_reuses_the_process_rbac_manager_singleton() -> None:
    """The adapter's own default would open a second RBAC store handle."""
    fake_adapter = types.ModuleType("maop.enterprise.rbac_m3_adapter")
    create = MagicMock(name="create_m3_enforcer")
    fake_adapter.create_m3_enforcer = create
    sentinel_manager = object()

    with patch.dict(sys.modules, {"maop.enterprise.rbac_m3_adapter": fake_adapter}), patch(
        "maop.dashboard.services.rbac_service.get_rbac_manager", return_value=sentinel_manager
    ):
        enforcer = tenant_blackboard_service.resolve_m3_enforcer()

    assert enforcer is create.return_value
    create.assert_called_once_with(sentinel_manager)


def test_resolve_degrades_to_fallback_when_manager_unavailable() -> None:
    fake_adapter = types.ModuleType("maop.enterprise.rbac_m3_adapter")
    fake_adapter.create_m3_enforcer = MagicMock()

    with patch.dict(sys.modules, {"maop.enterprise.rbac_m3_adapter": fake_adapter}), patch(
        "maop.dashboard.services.rbac_service.get_rbac_manager",
        side_effect=RuntimeError("feature RBAC not available"),
    ):
        assert tenant_blackboard_service.resolve_m3_enforcer() is None

    fake_adapter.create_m3_enforcer.assert_not_called()


# ── caller context ────────────────────────────────────────────────


def test_privileged_role_gets_blackboard_read() -> None:
    ctx = tenant_blackboard_service.context_for_caller("acme", "alice", ("admin",))
    assert (ctx.user_id, ctx.tenant_id) == ("alice", "acme")
    assert ctx.has_permission(RBACPermission.BB_READ)


def test_unprivileged_role_gets_no_permissions() -> None:
    ctx = tenant_blackboard_service.context_for_caller("acme", "bob", ("viewer",))
    assert ctx.permissions == frozenset()
    assert not ctx.has_permission(RBACPermission.BB_READ)


# ── G3: fallback enforcer is fail-closed (docstring/代码 一致) ──


def test_fallback_denies_context_without_the_permission() -> None:
    ctx = TenantContext(tenant_id="acme", user_id="bob", roles=frozenset({"viewer"}))
    assert FallbackRBACEnforcer().check(ctx, RBACPermission.BB_READ, "*") is False


def test_fallback_allows_granted_permission_then_vetoes_explicit_denial() -> None:
    enforcer = FallbackRBACEnforcer()
    ctx = TenantContext(
        tenant_id="acme",
        user_id="alice",
        roles=frozenset({"operator"}),
        permissions=frozenset({RBACPermission.BB_READ}),
    )
    assert enforcer.check(ctx, RBACPermission.BB_READ, "*") is True
    enforcer.deny("operator", RBACPermission.BB_READ, "*")
    assert enforcer.check(ctx, RBACPermission.BB_READ, "*") is False


# ── service-level injection ───────────────────────────────────────


def test_get_tenant_with_caller_uses_injected_enforcer(bb_with_data, monkeypatch) -> None:
    """The enforcer must see the caller, not a synthetic superadmin."""
    seen: list[tuple] = []

    class _Denying:
        def check(self, ctx, permission, resource):
            seen.append((ctx.user_id, permission, resource))
            return False

    monkeypatch.setattr(tenant_blackboard_service, "resolve_m3_enforcer", lambda: _Denying())
    monkeypatch.setattr(tenant_blackboard_service, "get_blackboard", lambda: bb_with_data)
    caller = tenant_blackboard_service.context_for_caller("acme", "bob", ("viewer",))

    with pytest.raises(PermissionError):
        tenant_blackboard_service.get_tenant("acme", caller=caller)

    assert seen[0] == ("bob", RBACPermission.BB_READ, "*")


def test_get_tenant_with_permitted_caller_reads_snapshot(bb_with_data, monkeypatch) -> None:
    class _Allowing:
        def check(self, ctx, permission, resource):
            return True

    monkeypatch.setattr(tenant_blackboard_service, "resolve_m3_enforcer", lambda: _Allowing())
    monkeypatch.setattr(tenant_blackboard_service, "get_blackboard", lambda: bb_with_data)
    caller = tenant_blackboard_service.context_for_caller("acme", "alice", ("admin",))

    detail = tenant_blackboard_service.get_tenant("acme", caller=caller)
    assert detail["tenant_id"] == "acme"
    assert detail["snapshot"]["_meta"]["total_entries"] == 1


def test_get_tenant_without_caller_keeps_system_context(bb_with_data, monkeypatch) -> None:
    """Auth disabled ⇒ no identity boundary; the historical path must not change."""
    calls: list[int] = []
    monkeypatch.setattr(
        tenant_blackboard_service, "resolve_m3_enforcer", lambda: calls.append(1) or None
    )
    monkeypatch.setattr(tenant_blackboard_service, "get_blackboard", lambda: bb_with_data)

    assert tenant_blackboard_service.get_tenant("acme")["snapshot"]["_meta"]["total_entries"] == 1
    assert calls == [], "no enforcer is resolved when there is no caller"


# ── HTTP surface: admin gate + per-caller enforcer ────────────────


def test_detail_still_requires_admin(reader, monkeypatch) -> None:
    """The master HTTP admin gate is retained (fixture admin_required_reads)."""
    monkeypatch.setattr(tenant_blackboard_service, "resolve_m3_enforcer", lambda: None)
    assert reader.get("/api/tenant-blackboard/tenants/acme").status_code == 403


def test_detail_200_for_admin_in_personal_edition(bb_with_data, monkeypatch) -> None:
    monkeypatch.setattr(tenant_blackboard_service, "get_blackboard", lambda: bb_with_data)
    monkeypatch.setattr(tenant_blackboard_service, "resolve_m3_enforcer", lambda: None)

    resp = _make_app(roles=["admin"]).get("/api/tenant-blackboard/tenants/acme")

    assert resp.status_code == 200
    assert resp.json()["data"]["tenant_id"] == "acme"


def test_detail_403_when_enforcer_denies_even_an_admin(bb_with_data, monkeypatch) -> None:
    """require_admin passes, yet the injected enforcer (MAOS grant table) denies.

    This is the G2 payoff: the HTTP role gate is not the whole story once the
    enterprise enforcer owns the decision.
    """

    class _Denying:
        def check(self, ctx, permission, resource):
            return False

    monkeypatch.setattr(tenant_blackboard_service, "get_blackboard", lambda: bb_with_data)
    monkeypatch.setattr(tenant_blackboard_service, "resolve_m3_enforcer", lambda: _Denying())

    resp = _make_app(roles=["admin"]).get("/api/tenant-blackboard/tenants/acme")

    assert resp.status_code == 403
    body = resp.json()
    assert body["code"] == "HTTP_403"
    assert "blackboard:read" in body["error"]