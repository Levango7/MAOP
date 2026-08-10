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
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel

from maop.config.edition import FeatureFlag, has_feature
from maop.core.security.middleware import require_admin
from maop.dashboard.error_handler import handle_api_errors

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/rbac", tags=["rbac"])

# Singleton RBACManager — initialized on first use. The manager's __init__
# calls require_feature(FeatureFlag.RBAC), so in personal edition this
# will raise and the router won't be registered (server.py guards the
# import with has_feature(FeatureFlag.MULTI_USER)).
# 路由层再加一道显式守卫（双层防护）：即便 manager __init__ 守卫被绕过，
# 每个端点仍会在入口处检查 FeatureFlag.RBAC，确保 Personal 版返回 404。
_rbac_manager: Any = None


def _get_manager() -> Any:
    global _rbac_manager
    if _rbac_manager is None:
        from maop.enterprise.rbac import RBACManager
        _rbac_manager = RBACManager()
    return _rbac_manager


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


# ── Endpoints ─────────────────────────────────────────────────────


@router.get("/grants")
@handle_api_errors
async def list_grants(
    request: Request,
    user_id: str = "",
) -> dict[str, Any]:
    """List all RBAC role grants, optionally filtered by user or tenant.

    G-07: tenant_id is taken from JWT, not from query/body parameters.
    """
    # 企业版特性开关守卫：Personal 版直接返回 404，避免 import maop.enterprise.* 抛 500
    if not has_feature(FeatureFlag.RBAC):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="RBAC not available in this edition",
        )
    # G-07: tenant_id from JWT, not from query param.
    tenant_id = _tenant_id_from_jwt(request)
    mgr = _get_manager()
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
    require_admin(request)
    # 企业版特性开关守卫：Personal 版直接返回 404，避免 import maop.enterprise.* 抛 500
    if not has_feature(FeatureFlag.RBAC):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="RBAC not available in this edition",
        )
    from maop.enterprise.rbac import Role
    try:
        role = Role(body.role)
    except ValueError:
        return {
            "status": "error",
            "error": f"Invalid role '{body.role}'. Valid: {[r.value for r in Role]}",
        }
    mgr = _get_manager()
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
    require_admin(request)
    # 企业版特性开关守卫：Personal 版直接返回 404，避免 import maop.enterprise.* 抛 500
    if not has_feature(FeatureFlag.RBAC):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="RBAC not available in this edition",
        )
    from maop.enterprise.rbac import Role
    try:
        role = Role(body.role)
    except ValueError:
        return {
            "status": "error",
            "error": f"Invalid role '{body.role}'. Valid: {[r.value for r in Role]}",
        }
    mgr = _get_manager()
    # G-07: tenant_id from JWT, not from body.
    tenant_id = _tenant_id_from_jwt(request)
    revoked = mgr.revoke_role(body.user_id, role, tenant_id=tenant_id)
    return {"status": "ok" if revoked else "not_found", "revoked": revoked}


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
        mgr = _get_manager()
        user_roles = [r.value for r in mgr.user_roles(user_id)]
        user_perms = sorted([p.value for p in mgr.user_permissions(user_id)])
    return {
        "status": "ok",
        "permissions": all_perms,
        "current_user": user_id,
        "current_roles": user_roles,
        "current_permissions": user_perms,
    }


def _current_user(request: Request) -> str:
    """Extract user_id from request state (set by auth middleware)."""
    # P1-14 fix: middleware sets auth_identity, not auth_user
    return getattr(request.state, "auth_identity", "") or ""
