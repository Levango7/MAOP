"""Enterprise tenant router — exposes TenantManager via FastAPI endpoints.

Phase C (C2, 2026-07-22): bridges the gap between the enterprise
TenantManager (``maop.enterprise.tenant``) and the frontend ``Tenants.vue``
which calls these APIs. Before this router existed, ``Tenants.vue`` got 404
on every request in ENTERPRISE mode.

All operations require admin role via ``require_admin``.

业务逻辑由 ``maop.dashboard.services.tenant_service`` 提供；本模块
仅负责路由定义、请求解析、权限检查、特性开关守卫、调用 service、
响应格式化与错误处理。
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from maop.config.edition import FeatureFlag, has_feature
from maop.core.security.middleware import require_admin
from maop.dashboard.error_handler import handle_api_errors
from maop.dashboard.services import tenant_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/tenant", tags=["tenant"])


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


def _require_tenant_isolation() -> None:
    """企业版特性开关守卫：Personal 版直接返回 404，避免 import maop.enterprise.* 抛 500。"""
    if not has_feature(FeatureFlag.TENANT_ISOLATION):
        raise HTTPException(
            status_code=404,
            detail="tenant isolation not available in this edition",
        )


# ── Endpoints ─────────────────────────────────────────────────────


@router.get("/list")
@handle_api_errors
async def list_tenants(
    request: Request,
    status: str = "",
) -> dict[str, Any]:
    """List all tenants, optionally filtered by status."""

    require_admin(request)
    _require_tenant_isolation()
    try:
        return tenant_service.list_tenants(status=status)
    except ValueError as exc:
        return JSONResponse(
            status_code=400,
            content={
                "status": "error",
                "error": str(exc),
            },
        )


@router.post("/create")
@handle_api_errors
async def create_tenant(body: CreateTenantRequest, request: Request) -> dict[str, Any]:
    """Create a new tenant. Requires admin."""
    require_admin(request)
    _require_tenant_isolation()
    return tenant_service.create_tenant(
        body.tenant_id, body.name, body.plan,
        body.max_api_calls_per_day, body.max_storage_mb,
    )


@router.get("/{tenant_id}")
@handle_api_errors
async def get_tenant(tenant_id: str, request: Request) -> dict[str, Any]:
    """Get a single tenant by ID."""
    require_admin(request)
    _require_tenant_isolation()
    try:
        return tenant_service.get_tenant(tenant_id)
    except KeyError:
        # P1-13: 使用 raise HTTPException 代替 JSONResponse，统一错误处理
        raise HTTPException(
            status_code=404,
            detail=f"Tenant '{tenant_id}' not found",
        )


@router.post("/{tenant_id}/suspend")
@handle_api_errors
async def suspend_tenant(tenant_id: str, request: Request) -> dict[str, Any]:
    """Suspend a tenant. Requires admin."""
    require_admin(request)
    _require_tenant_isolation()
    try:
        return tenant_service.suspend_tenant(tenant_id)
    except KeyError:
        # P2 fix: 404 应返回 404 状态码，而非 200。
        raise HTTPException(status_code=404, detail="Tenant not found")


@router.post("/{tenant_id}/activate")
@handle_api_errors
async def activate_tenant(tenant_id: str, request: Request) -> dict[str, Any]:
    """Activate a suspended tenant. Requires admin."""
    require_admin(request)
    _require_tenant_isolation()
    try:
        return tenant_service.activate_tenant(tenant_id)
    except KeyError:
        # H-1 fix: 资源未找到应返回 404，而非 200 + status=not_found。
        raise HTTPException(status_code=404, detail="Tenant not found")


@router.delete("/{tenant_id}")
@handle_api_errors
async def delete_tenant(tenant_id: str, request: Request) -> dict[str, Any]:
    """Delete a tenant. Requires admin."""
    require_admin(request)
    _require_tenant_isolation()
    try:
        return tenant_service.delete_tenant(tenant_id)
    except KeyError:
        # H-1 fix: 资源未找到应返回 404，而非 200 + status=not_found。
        raise HTTPException(status_code=404, detail="Tenant not found")


@router.get("/{tenant_id}/usage")
@handle_api_errors
async def get_usage(tenant_id: str, request: Request) -> dict[str, Any]:
    """Get resource usage for a tenant. Requires admin."""
    require_admin(request)
    _require_tenant_isolation()
    return tenant_service.get_tenant_usage(tenant_id)
