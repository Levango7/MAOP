"""RBAC on ``/api/tenant-blackboard/*`` — enforced by the real guard, not a patch.

The tenant roster and any single tenant's snapshot are cross-tenant reads: they
hand the caller other tenants' identifiers, entry counts and content. Until
2026-10-24 they carried no guard at all, while ``DELETE`` and
``/context/validate`` did — and ``test_coordination_m4``'s ``admin_client``
fixture replaced ``require_admin`` with a no-op, so nothing in the suite could
tell a guarded endpoint from an unguarded one.

Here the guard is real: role comes from ``request.state.auth_roles`` exactly as
AuthMiddleware sets it, so a read-only principal getting 403 is evidence about
the product, not about a mock.
"""

from __future__ import annotations

import ast
import asyncio
import inspect
import textwrap
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from maop.core.reliability.blackboard import get_blackboard
from maop.core.reliability.coordination_tenant import (
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
