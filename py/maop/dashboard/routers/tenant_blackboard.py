"""MAOP Dashboard — Multi-Tenant Blackboard Admin API.

Cross-tenant visibility lives here, so every endpoint that reads tenant data
requires the admin role: the roster (``/tenants``) and any single tenant's
snapshot (``/tenants/{id}``) both expose other tenants' identifiers, entry
counts and content to the caller.

Endpoints:
- ``GET  /api/tenant-blackboard/tenants`` — list all tenants (admin)
- ``GET  /api/tenant-blackboard/tenants/{tenant_id}`` — tenant details + snapshot (admin)
- ``DELETE /api/tenant-blackboard/tenants/{tenant_id}`` — purge all tenant data (admin)
- ``GET  /api/tenant-blackboard/rbac/permissions`` — list valid RBAC permissions (read)
- ``POST /api/tenant-blackboard/context/validate`` — validate a tenant context (admin)

``/rbac/permissions`` stays open on purpose: it returns the static
``RBACPermission`` enum, no tenant data.

``GET /tenants/{tenant_id}`` additionally threads the authenticated caller's
context into the service, so when the enterprise package is installed the M3
enforcer (MAOS grant table) is injected at construction time — option B in the
MAOS M3 readiness doc.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from maop.core.reliability.coordination_tenant import TenantContext
from maop.core.security.middleware import require_admin
from maop.dashboard.error_handler import handle_api_errors
from maop.dashboard.services import tenant_blackboard_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/tenant-blackboard", tags=["tenant-blackboard"])


def _caller_context(request: Request, tenant_id: str) -> TenantContext | None:
    """Build the requesting user's context, or ``None`` if there is no identity.

    With auth disabled (``MAOP_ENV`` dev/test/local, or ``MAOP_AUTH_ENABLED=0``)
    the middleware pins the identity to ``"anonymous"`` — there is no user/tenant
    boundary to enforce, so the service keeps its historical system context.
    Returning a context here is what lets the enterprise enforcer become
    authoritative for this read (option B injection).
    """
    user_id = getattr(request.state, "auth_identity", "") or ""
    if not user_id or user_id == "anonymous":
        return None
    roles = tuple(getattr(request.state, "auth_roles", None) or ())
    return tenant_blackboard_service.context_for_caller(
        tenant_id=tenant_id, user_id=user_id, roles=roles
    )


# ── Request models ─────────────────────────────────────────────────


class TenantContextRequest(BaseModel):
    """Validate a tenant context for correct RBAC resolution."""

    tenant_id: str = Field(..., description="Tenant identifier")
    user_id: str = Field(..., description="User principal name")
    roles: list[str] = Field(default_factory=list)
    permissions: list[str] = Field(default_factory=list)


# ── List tenants ────────────────────────────────────────────────────


@router.get("/tenants")
@handle_api_errors("tenant list")
async def list_tenants(request: Request) -> dict[str, Any]:
    """List all tenants detected via metadata tagging (admin, read-only)."""
    require_admin(request)
    return {"status": "ok", "data": tenant_blackboard_service.list_tenants()}


# ── Get single tenant ───────────────────────────────────────────────


@router.get("/tenants/{tenant_id}")
@handle_api_errors("tenant detail")
async def get_tenant(tenant_id: str, request: Request) -> dict[str, Any]:
    """Get detailed snapshot + recent history for a tenant.

    Two layers: ``require_admin`` keeps the HTTP admin gate that master added
    (cross-tenant read), and the caller's context is threaded into the service so
    the M3 enforcer is injected — enterprise deployments authorize through
    MAOS's grant table, the personal edition through MAOP's role-derived
    ``blackboard:read``. A ``PermissionError`` from the enforcer maps to 403.
    """
    require_admin(request)
    try:
        detail = tenant_blackboard_service.get_tenant(
            tenant_id, caller=_caller_context(request, tenant_id)
        )
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    return {"status": "ok", "data": detail}


# ── Delete tenant data ──────────────────────────────────────────────


@router.delete("/tenants/{tenant_id}")
@handle_api_errors("tenant delete")
async def delete_tenant(tenant_id: str, request: Request) -> dict[str, Any]:
    """Delete ALL data for a tenant (admin, destructive)."""
    require_admin(request)
    result = await tenant_blackboard_service.delete_tenant_data(tenant_id)
    return {"status": "ok", "data": result}


# ── RBAC permissions list ───────────────────────────────────────────


@router.get("/rbac/permissions")
@handle_api_errors("rbac permissions")
async def list_rbac_permissions() -> dict[str, Any]:
    """List all valid RBACPermission values (read-only)."""
    return {
        "status": "ok",
        "data": tenant_blackboard_service.get_rbac_permissions(),
    }


# ── Validate tenant context ─────────────────────────────────────────


@router.post("/context/validate")
@handle_api_errors("tenant context validate")
async def validate_context(body: TenantContextRequest, request: Request) -> dict[str, Any]:
    """Validate and inspect a resolved TenantContext (admin)."""
    require_admin(request)
    try:
        ctx = tenant_blackboard_service.create_tenant_context(
            tenant_id=body.tenant_id,
            user_id=body.user_id,
            roles=body.roles,
            permissions=body.permissions,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return {
        "status": "ok",
        "data": {
            "tenant_id": ctx.tenant_id,
            "user_id": ctx.user_id,
            "roles": sorted(ctx.roles),
            "permissions": sorted(p.value for p in ctx.permissions),
            "has_admin": ctx.has_permission(
                __import__(
                    "maop.core.reliability.coordination_tenant",
                    fromlist=["RBACPermission"],
                ).RBACPermission.BB_ADMIN
            ),
        },
    }
