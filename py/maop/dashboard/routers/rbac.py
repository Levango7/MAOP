"""Enterprise RBAC router — exposes RBACManager via FastAPI endpoints.

Phase C (C1, 2026-07-22): bridges the gap between the enterprise RBACManager
(``maop.enterprise.rbac``) and the frontend ``RBAC.vue`` which calls these
APIs. Before this router existed, ``RBAC.vue`` got 404 on every request in
ENTERPRISE mode because ``server.py`` silently swallowed the ImportError.

All write operations (grant/revoke) require admin role via ``require_admin``.
Read operations (list grants, roles, permissions) are available to any
authenticated user so they can inspect their own permissions.

G-07 security fix: ``tenant_id`` is always taken from the JWT-authenticated
request state (``request.state.tenant_id``), never from the request body.
This prevents cross-tenant privilege escalation via forged body parameters.

RBACManager singleton lives in ``maop.dashboard.services.rbac_service``.
This router only does request parsing, permission checks, service calls,
response formatting, and error handling.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel

from maop.config.edition import FeatureFlag, has_feature
from maop.core.security.middleware import require_admin
from maop.dashboard.error_handler import handle_api_errors

# ── Service layer ──────────────────────────────────────────────────
from maop.dashboard.services import rbac_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/rbac", tags=["rbac"])


# ── Request models ────────────────────────────────────────────────


class GrantRequest(BaseModel):
    user_id: str
    role: str  # Role enum value: superadmin/admin/operator/viewer
    # G-07 fix: tenant_id is intentionally NOT accepted from the body.
    # It is always taken from the JWT (request.state.tenant_id).
    granted_by: str = ""


class RevokeRequest(BaseModel):
    user_id: str
    role: str
    # G-07 fix: tenant_id is intentionally NOT accepted from the body.


def _tenant_id_from_jwt(request: Request) -> str:
    """Extract tenant_id from JWT-authenticated request state.

    G-07 fix: NEVER use body.tenant_id — always use the JWT claim.
    Falls back to empty string for single-tenant deployments.
    """
    return getattr(request.state, "tenant_id", "") or ""


def _current_user(request: Request) -> str:
    """Extract user_id from request state (set by auth middleware)."""
    # P1-14 fix: middleware sets auth_identity, not auth_user
    return getattr(request.state, "auth_identity", "") or ""

def _is_admin(request: Request) -> bool:
    """Check if the current user has admin/superadmin role."""
    roles = getattr(request.state, "auth_roles", None) or []
    return bool({"admin", "superadmin"} & set(roles))


# ── Endpoints ─────────────────────────────────────────────────────


@router.get("/grants")
@handle_api_errors
async def list_grants(
    request: Request,
    user_id: str = "",
) -> dict[str, Any]:
    """List all RBAC role grants, optionally filtered by user or tenant.

    G-07: tenant_id is taken from JWT, not from query/body parameters.
    P1-19: 非管理员只能查看自己的授权，不能列举所有用户授权。
    """
    # 企业版特性开关守卫：Personal 版直接返回 404，避免 import maop.enterprise.* 抛 500
    if not has_feature(FeatureFlag.RBAC):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="RBAC not available in this edition",
        )
    # G-07: tenant_id from JWT, not from query param.
    tenant_id = _tenant_id_from_jwt(request)
    # P1-19: 非管理员只能查看自己的授权 — 防止越权列举所有用户授权
    current_user = _current_user(request)
    is_admin = _is_admin(request)
    if not is_admin:
        user_id = current_user  # 强制非管理员只能查看自己的授权
    mgr = rbac_service.get_rbac_manager()
    grants = mgr.list_grants(user_id=user_id, tenant_id=tenant_id)
    return {
        "status": "ok",
        "grants": [g.model_dump() for g in grants],
        "count": len(grants),
    }


@router.post("/grant")
@handle_api_errors
async def grant_role(body: GrantRequest, request: Request) -> dict[str, Any]:
    """Grant a role to a user. Requires admin."""
    # P1-12: 特性守卫在 require_admin 之前 — Personal 版返回 404 而非 403，
    # 避免泄露路由存在性
    if not has_feature(FeatureFlag.RBAC):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="RBAC not available in this edition",
        )
    require_admin(request)
    from maop.enterprise.rbac import Role
    try:
        role = Role(body.role)
    except ValueError:
        # P1-11: 使用 raise HTTPException 代替 JSONResponse，统一错误处理
        raise HTTPException(
            status_code=400,
            detail=f"Invalid role '{body.role}'. Valid: {[r.value for r in Role]}",
        )
    mgr = rbac_service.get_rbac_manager()
    # G-07: tenant_id from JWT, not from body.
    tenant_id = _tenant_id_from_jwt(request)
    grant = mgr.grant_role(
        body.user_id, role,
        granted_by=body.granted_by or _current_user(request),
        tenant_id=tenant_id,
    )
    return {"status": "ok", "grant": grant.model_dump()}


@router.post("/revoke")
@handle_api_errors
async def revoke_role(body: RevokeRequest, request: Request) -> dict[str, Any]:
    """Revoke a role from a user. Requires admin."""
    # P1-12: 特性守卫在 require_admin 之前 — Personal 版返回 404 而非 403，
    # 避免泄露路由存在性
    if not has_feature(FeatureFlag.RBAC):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="RBAC not available in this edition",
        )
    require_admin(request)
    from maop.enterprise.rbac import Role
    try:
        role = Role(body.role)
    except ValueError:
        # P1-11: 使用 raise HTTPException 代替 JSONResponse，统一错误处理
        raise HTTPException(
            status_code=400,
            detail=f"Invalid role '{body.role}'. Valid: {[r.value for r in Role]}",
        )
    mgr = rbac_service.get_rbac_manager()
    # G-07: tenant_id from JWT, not from body.
    tenant_id = _tenant_id_from_jwt(request)
    revoked = mgr.revoke_role(body.user_id, role, tenant_id=tenant_id)
    if not revoked:
        # P2 fix: 404 应返回 404 状态码，而非 200。
        raise HTTPException(status_code=404, detail="Grant not found")
    return {"status": "ok", "revoked": revoked}


@router.get("/roles")
@handle_api_errors
async def list_roles(request: Request) -> dict[str, Any]:
    """List all available roles and their permissions."""
    # 企业版特性开关守卫：Personal 版直接返回 404，避免 import maop.enterprise.* 抛 500
    if not has_feature(FeatureFlag.RBAC):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="RBAC not available in this edition",
        )
    from maop.enterprise.rbac import (
        ROLE_PERMISSIONS,
        Role,
    )
    roles_info = []
    for role in Role:
        perms = ROLE_PERMISSIONS.get(role, frozenset())
        roles_info.append({
            "role": role.value,
            "permissions": sorted([p.value for p in perms]),
            "permission_count": len(perms),
        })
    return {"status": "ok", "roles": roles_info}


@router.get("/permissions")
@handle_api_errors
async def list_permissions(request: Request) -> dict[str, Any]:
    """List all available permissions and the current user's grants."""
    # 企业版特性开关守卫：Personal 版直接返回 404，避免 import maop.enterprise.* 抛 500
    if not has_feature(FeatureFlag.RBAC):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="RBAC not available in this edition",
        )
    from maop.enterprise.rbac import Permission
    all_perms = [{"value": p.value, "name": p.name} for p in Permission]
    # Also return the current user's roles/permissions if authenticated.
    user_id = _current_user(request)
    user_roles: list[str] = []
    user_perms: list[str] = []
    if user_id:
        mgr = rbac_service.get_rbac_manager()
        user_roles = [r.value for r in mgr.user_roles(user_id)]
        user_perms = sorted([p.value for p in mgr.user_permissions(user_id)])
    return {
        "status": "ok",
        "permissions": all_perms,
        "current_user": user_id,
        "current_roles": user_roles,
        "current_permissions": user_perms,
    }
