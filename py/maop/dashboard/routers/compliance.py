"""Compliance router — GDPR/CCPA data deletion and export endpoints.

G-07 security fix: tenant_id is always taken from the JWT-authenticated
request state (``request.state.tenant_id``), never from the request body.
This prevents cross-tenant data access via forged body parameters.

ComplianceManager singleton lives in ``maop.dashboard.services.rbac_service``.
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

router = APIRouter(prefix="/api/compliance", tags=["compliance"])


def _tenant_id_from_jwt(request: Request) -> str:
    """Extract tenant_id from JWT-authenticated request state.

    G-07 fix: NEVER use body.tenant_id — always use the JWT claim.
    """
    tenant_id = getattr(request.state, "tenant_id", "") or ""
    if not tenant_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="tenant_id not found in JWT — cannot process compliance request",
        )
    return tenant_id


class DeleteUserDataRequest(BaseModel):
    user_id: str
    # NOTE: tenant_id is intentionally NOT in the body.
    # It is always taken from the JWT (request.state.tenant_id).


class ExportUserDataRequest(BaseModel):
    user_id: str
    # NOTE: tenant_id is intentionally NOT in the body.


@router.post("/delete-user-data")
@handle_api_errors
async def delete_user_data(
    body: DeleteUserDataRequest,
    request: Request,
) -> dict[str, Any]:
    """Delete all data for a user (GDPR right-to-erasure).

    G-07: tenant_id is taken from JWT, not from the request body.
    Requires admin role.
    """
    if not has_feature(FeatureFlag.MULTI_USER):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Compliance APIs not available in this edition",
        )
    require_admin(request)
    tenant_id = _tenant_id_from_jwt(request)
    root_dir = getattr(request.app.state, "root_dir", None)
    try:
        report = rbac_service.delete_user_data(
            body.user_id, tenant_id=tenant_id, root_dir=root_dir,
        )
    except rbac_service.ComplianceNotConfigured:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Compliance service not configured",
        )
    return {"status": "ok", **report}


@router.post("/export-user-data")
@handle_api_errors
async def export_user_data(
    body: ExportUserDataRequest,
    request: Request,
) -> dict[str, Any]:
    """Export all data for a user (GDPR data portability).

    G-07: tenant_id is taken from JWT, not from the request body.
    Requires admin role.
    """
    if not has_feature(FeatureFlag.MULTI_USER):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Compliance APIs not available in this edition",
        )
    require_admin(request)
    tenant_id = _tenant_id_from_jwt(request)
    root_dir = getattr(request.app.state, "root_dir", None)
    try:
        report = rbac_service.export_user_data(
            body.user_id, tenant_id=tenant_id, root_dir=root_dir,
        )
    except rbac_service.ComplianceNotConfigured:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Compliance service not configured",
        )
    return {"status": "ok", **report}
