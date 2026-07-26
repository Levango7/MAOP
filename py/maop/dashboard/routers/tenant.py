"""Enterprise tenant router — exposes TenantManager via FastAPI endpoints.

Phase C (C2, 2026-07-22): bridges the gap between the enterprise
TenantManager (``maop.enterprise.tenant``) and the frontend ``Tenants.vue``
which calls these APIs. Before this router existed, ``Tenants.vue`` got 404
on every request in ENTERPRISE mode.

All operations require admin role via ``require_admin``.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from maop.config.edition import FeatureFlag, has_feature
from maop.core.middleware import require_admin
from maop.dashboard.error_handler import handle_api_errors

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/tenant", tags=["tenant"])

_tenant_manager: Any = None


def _get_manager() -> Any:
    global _tenant_manager
    if _tenant_manager is None:
        from maop.enterprise.tenant import TenantManager
        _tenant_manager = TenantManager()
    return _tenant_manager


# ── Request models ────────────────────────────────────────────────


class CreateTenantRequest(BaseModel):
    tenant_id: str
    name: str
    plan: str = "starter"
    max_api_calls_per_day: int = 10000
    max_storage_mb: int = 5120


class UpdateTenantRequest(BaseModel):
    name: str | None = None
    plan: str | None = None
    max_api_calls_per_day: int | None = None
    max_storage_mb: int | None = None


# ── Endpoints ─────────────────────────────────────────────────────


@router.get("/list")
@handle_api_errors
async def list_tenants(
    request: Request,
    status: str = "",
) -> dict[str, Any]:
    """List all tenants, optionally filtered by status."""
    require_admin(request)
    # 企业版特性开关守卫：Personal 版直接返回 404，避免 import maop.enterprise.* 抛 500
    if not has_feature(FeatureFlag.TENANT_ISOLATION):
        raise HTTPException(
            status_code=404,
            detail="tenant isolation not available in this edition",
        )
    mgr = _get_manager()
    from maop.enterprise.tenant import TenantStatus
    status_filter = None
    if status:
        try:
            status_filter = TenantStatus(status)
        except ValueError:
            return {
                "status": "error",
                "error": f"Invalid status '{status}'. Valid: {[s.value for s in TenantStatus]}",
            }
    tenants = mgr.list_tenants(status=status_filter)
    return {
        "status": "ok",
        "tenants": [t.model_dump() for t in tenants],
        "count": len(tenants),
    }


@router.post("/create")
@handle_api_errors
async def create_tenant(body: CreateTenantRequest, request: Request) -> dict[str, Any]:
    """Create a new tenant. Requires admin."""
    require_admin(request)
    # 企业版特性开关守卫：Personal 版直接返回 404，避免 import maop.enterprise.* 抛 500
    if not has_feature(FeatureFlag.TENANT_ISOLATION):
        raise HTTPException(
            status_code=404,
            detail="tenant isolation not available in this edition",
        )
    from maop.enterprise.tenant import TenantQuota
    quota = TenantQuota(
        max_api_calls_per_day=body.max_api_calls_per_day,
        max_storage_mb=body.max_storage_mb,
    )
    mgr = _get_manager()
    tenant = mgr.create_tenant(body.tenant_id, body.name, plan=body.plan, quota=quota)
    return {"status": "ok", "tenant": tenant.model_dump()}


@router.get("/{tenant_id}")
@handle_api_errors
async def get_tenant(tenant_id: str, request: Request) -> dict[str, Any]:
    """Get a single tenant by ID."""
    require_admin(request)
    # 企业版特性开关守卫：Personal 版直接返回 404，避免 import maop.enterprise.* 抛 500
    if not has_feature(FeatureFlag.TENANT_ISOLATION):
        raise HTTPException(
            status_code=404,
            detail="tenant isolation not available in this edition",
        )
    mgr = _get_manager()
    tenant = mgr.get_tenant(tenant_id)
    if tenant is None:
        return {"status": "error", "error": f"Tenant '{tenant_id}' not found"}
    return {"status": "ok", "tenant": tenant.model_dump()}


@router.post("/{tenant_id}/suspend")
@handle_api_errors
async def suspend_tenant(tenant_id: str, request: Request) -> dict[str, Any]:
    """Suspend a tenant. Requires admin."""
    require_admin(request)
    # 企业版特性开关守卫：Personal 版直接返回 404，避免 import maop.enterprise.* 抛 500
    if not has_feature(FeatureFlag.TENANT_ISOLATION):
        raise HTTPException(
            status_code=404,
            detail="tenant isolation not available in this edition",
        )
    mgr = _get_manager()
    suspended = mgr.suspend_tenant(tenant_id)
    return {"status": "ok" if suspended else "not_found", "suspended": suspended}


@router.post("/{tenant_id}/activate")
@handle_api_errors
async def activate_tenant(tenant_id: str, request: Request) -> dict[str, Any]:
    """Activate a suspended tenant. Requires admin."""
    require_admin(request)
    # 企业版特性开关守卫：Personal 版直接返回 404，避免 import maop.enterprise.* 抛 500
    if not has_feature(FeatureFlag.TENANT_ISOLATION):
        raise HTTPException(
            status_code=404,
            detail="tenant isolation not available in this edition",
        )
    mgr = _get_manager()
    activated = mgr.activate_tenant(tenant_id)
    return {"status": "ok" if activated else "not_found", "activated": activated}


@router.delete("/{tenant_id}")
@handle_api_errors
async def delete_tenant(tenant_id: str, request: Request) -> dict[str, Any]:
    """Delete a tenant. Requires admin."""
    require_admin(request)
    # 企业版特性开关守卫：Personal 版直接返回 404，避免 import maop.enterprise.* 抛 500
    if not has_feature(FeatureFlag.TENANT_ISOLATION):
        raise HTTPException(
            status_code=404,
            detail="tenant isolation not available in this edition",
        )
    mgr = _get_manager()
    deleted = mgr.delete_tenant(tenant_id)
    return {"status": "ok" if deleted else "not_found", "deleted": deleted}


@router.get("/{tenant_id}/usage")
@handle_api_errors
async def get_usage(tenant_id: str, request: Request) -> dict[str, Any]:
    """Get resource usage for a tenant. Requires admin."""
    require_admin(request)
    # 企业版特性开关守卫：Personal 版直接返回 404，避免 import maop.enterprise.* 抛 500
    if not has_feature(FeatureFlag.TENANT_ISOLATION):
        raise HTTPException(
            status_code=404,
            detail="tenant isolation not available in this edition",
        )
    mgr = _get_manager()
    usage = mgr.get_usage(tenant_id)
    return {"status": "ok", "usage": usage.model_dump()}
