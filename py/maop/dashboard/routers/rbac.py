"""Enterprise RBAC router — exposes RBACManager via FastAPI endpoints.

Phase C (C1, 2026-07-22): bridges the gap between the enterprise RBACManager
(``maop.enterprise.rbac``) and the frontend ``RBAC.vue`` which calls these
APIs. Before this router existed, ``RBAC.vue`` got 404 on every request in
ENTERPRISE mode because ``server.py`` silently swallowed the ImportError.

All write operations (grant/revoke) require admin role via ``require_admin``.
Read operations (list grants, roles, permissions) are available to any
authenticated user so they can inspect their own permissions.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Request
from pydantic import BaseModel

from maop.dashboard.error_handler import handle_api_errors
from maop.core.middleware import require_admin

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/rbac", tags=["rbac"])

# Singleton RBACManager — initialized on first use. The manager's __init__
# calls require_feature(FeatureFlag.RBAC), so in personal edition this
# will raise and the router won't be registered (server.py guards the
# import with has_feature(FeatureFlag.MULTI_USER)).
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
    tenant_id: str = ""
    granted_by: str = ""


class RevokeRequest(BaseModel):
    user_id: str
    role: str
    tenant_id: str = ""


# ── Endpoints ─────────────────────────────────────────────────────


@router.get("/grants")
@handle_api_errors
async def list_grants(
    request: Request,
    user_id: str = "",
    tenant_id: str = "",
) -> Any:
    """List all RBAC role grants, optionally filtered by user or tenant."""
    mgr = _get_manager()
    grants = mgr.list_grants(user_id=user_id, tenant_id=tenant_id)
    return {
        "status": "ok",
        "grants": [g.model_dump() for g in grants],
        "count": len(grants),
    }


@router.post("/grant")
@handle_api_errors
async def grant_role(body: GrantRequest, request: Request) -> Any:
    """Grant a role to a user. Requires admin."""
    require_admin(request)
    from maop.enterprise.rbac import Role
    try:
        role = Role(body.role)
    except ValueError:
        return {
            "status": "error",
            "error": f"Invalid role '{body.role}'. Valid: {[r.value for r in Role]}",
        }
    mgr = _get_manager()
    grant = mgr.grant_role(
        body.user_id, role,
        granted_by=body.granted_by or _current_user(request),
        tenant_id=body.tenant_id,
    )
    return {"status": "ok", "grant": grant.model_dump()}


@router.post("/revoke")
@handle_api_errors
async def revoke_role(body: RevokeRequest, request: Request) -> Any:
    """Revoke a role from a user. Requires admin."""
    require_admin(request)
    from maop.enterprise.rbac import Role
    try:
        role = Role(body.role)
    except ValueError:
        return {
            "status": "error",
            "error": f"Invalid role '{body.role}'. Valid: {[r.value for r in Role]}",
        }
    mgr = _get_manager()
    revoked = mgr.revoke_role(body.user_id, role, tenant_id=body.tenant_id)
    return {"status": "ok" if revoked else "not_found", "revoked": revoked}


@router.get("/roles")
@handle_api_errors
async def list_roles(request: Request) -> Any:
    """List all available roles and their permissions."""
    from maop.enterprise.rbac import Role, ROLE_PERMISSIONS  # noqa: F401 (re-exported for router use)
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
async def list_permissions(request: Request) -> Any:
    """List all available permissions and the current user's grants."""
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
    return getattr(request.state, "auth_user", "") or ""
