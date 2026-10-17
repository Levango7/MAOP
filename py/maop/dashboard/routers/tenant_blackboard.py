"""MAOP Dashboard — Multi-Tenant Blackboard Admin API.

Endpoints:
- ``GET  /api/tenant-blackboard/tenants`` — list all tenants (read)
- ``GET  /api/tenant-blackboard/tenants/{tenant_id}`` — tenant details + snapshot (read)
- ``DELETE /api/tenant-blackboard/tenants/{tenant_id}`` — purge all tenant data (admin)
- ``GET  /api/tenant-blackboard/rbac/permissions`` — list valid RBAC permissions (read)
- ``POST /api/tenant-blackboard/context/validate`` — validate a tenant context (admin)
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from maop.core.security.middleware import require_admin
from maop.dashboard.error_handler import handle_api_errors
from maop.dashboard.services import tenant_blackboard_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/tenant-blackboard", tags=["tenant-blackboard"])


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
async def list_tenants() -> dict[str, Any]:
    """List all tenants detected via metadata tagging (read-only)."""
    return {"status": "ok", "data": tenant_blackboard_service.list_tenants()}


# ── Get single tenant ───────────────────────────────────────────────


@router.get("/tenants/{tenant_id}")
@handle_api_errors("tenant detail")
async def get_tenant(tenant_id: str) -> dict[str, Any]:
    """Get detailed snapshot + recent history for a tenant (read-only)."""
    try:
        detail = tenant_blackboard_service.get_tenant(tenant_id)
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    return {"status": "ok", "data": detail}


# ── Delete tenant data ──────────────────────────────────────────────


@router.delete("/tenants/{tenant_id}")
@handle_api_errors("tenant delete")
async def delete_tenant(tenant_id: str, request: Request) -> dict[str, Any]:
    """Delete ALL data for a tenant (admin, destructive)."""
    require_admin(request)
    result = tenant_blackboard_service.delete_tenant_data(tenant_id)
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
