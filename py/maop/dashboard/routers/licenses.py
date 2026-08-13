"""Enterprise license management router — exposes LicenseManager via FastAPI.

Implements the License Management API (PRD: ``docs/prd-license-management.md``).
All operations require admin role via ``require_admin`` and are gated by
``FeatureFlag.LICENSE_MANAGEMENT`` (enterprise-only).

Endpoints
---------
- GET    /api/licenses/list                — list all licenses
- POST   /api/licenses/create              — issue a new license
- POST   /api/licenses/validate            — validate a license key
- GET    /api/licenses/{license_id}        — get a single license
- PATCH  /api/licenses/{license_id}        — update metadata
- POST   /api/licenses/{license_id}/renew  — renew (re-sign with new expiry)
- POST   /api/licenses/{license_id}/revoke — revoke a license
- DELETE /api/licenses/{license_id}        — delete a license
- GET    /api/licenses/{license_id}/audit  — audit log for a license
- GET    /api/licenses/audit/list          — all audit logs (with filters)
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request

from maop.config.edition import FeatureFlag, has_feature
from maop.core.security.middleware import require_admin
from maop.dashboard.error_handler import handle_api_errors

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/licenses", tags=["licenses"])

_license_manager: Any = None


def _get_manager() -> Any:
    """Lazy-init the LicenseManager singleton.

    Uses an in-memory Ed25519 keypair by default (auto-generated on first
    call). For production issuance, set ``MAOP_LICENSE_MGR_PRIVATE_KEY``
    to point to the real signing key path.
    """
    global _license_manager
    if _license_manager is None:
        import os
        from pathlib import Path

        from maop.enterprise.license_manager import LicenseManager

        priv_path_env = os.getenv("MAOP_LICENSE_MGR_PRIVATE_KEY", "").strip()
        priv_path = Path(priv_path_env) if priv_path_env else None
        _license_manager = LicenseManager(private_key_path=priv_path)
    return _license_manager


def _require_feature() -> None:
    """Return 404 if the LICENSE_MANAGEMENT feature is not available."""
    if not has_feature(FeatureFlag.LICENSE_MANAGEMENT):
        raise HTTPException(
            status_code=404,
            detail="license management not available in this edition",
        )


# ── Endpoints ─────────────────────────────────────────────────────


@router.get("/list")
@handle_api_errors
async def list_licenses(
    request: Request,
    status: str = Query(default="", description="Filter by status: active/revoked/expired"),
) -> dict[str, Any]:
    """List all licenses, optionally filtered by status."""
    require_admin(request)
    _require_feature()
    mgr = _get_manager()
    licenses = mgr.list_licenses(status=status)
    return {
        "status": "ok",
        "licenses": [lic.model_dump() for lic in licenses],
        "count": len(licenses),
    }


@router.post("/create")
@handle_api_errors
async def create_license(request: Request, body: dict[str, Any]) -> dict[str, Any]:
    """Issue a new license. Requires admin."""
    require_admin(request)
    _require_feature()
    from maop.enterprise.license_manager import LicenseCreateRequest

    req = LicenseCreateRequest(**body)
    mgr = _get_manager()
    record = mgr.create_license(
        customer=req.customer,
        expires_at=req.expires_at,
        max_users=req.max_users,
        fingerprint=req.fingerprint,
        features=req.features,
        issued_by=req.issued_by,
        notes=req.notes,
    )
    return {"status": "ok", "license": record.model_dump()}


@router.post("/validate")
@handle_api_errors
async def validate_license(request: Request, body: dict[str, Any]) -> dict[str, Any]:
    """Validate a license key (signature + expiry + revocation)."""
    require_admin(request)
    _require_feature()
    from maop.enterprise.license_manager import LicenseValidateRequest

    req = LicenseValidateRequest(**body)
    mgr = _get_manager()
    result = mgr.validate_license(req.license_key)
    return {"status": "ok", "validation": result}


@router.get("/{license_id}")
@handle_api_errors
async def get_license(license_id: str, request: Request) -> dict[str, Any]:
    """Get a single license by ID."""
    require_admin(request)
    _require_feature()
    from maop.enterprise.license_manager import LicenseNotFoundError

    mgr = _get_manager()
    try:
        record = mgr.get_license(license_id)
    except LicenseNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"status": "ok", "license": record.model_dump()}


@router.patch("/{license_id}")
@handle_api_errors
async def update_license(license_id: str, request: Request, body: dict[str, Any]) -> dict[str, Any]:
    """Update editable license metadata (customer, max_users, fingerprint, features, notes)."""
    require_admin(request)
    _require_feature()
    from maop.enterprise.license_manager import LicenseNotFoundError, LicenseUpdateRequest

    req = LicenseUpdateRequest(**body)
    mgr = _get_manager()
    try:
        record = mgr.update_license(
            license_id,
            customer=req.customer,
            max_users=req.max_users,
            fingerprint=req.fingerprint,
            features=req.features,
            notes=req.notes,
        )
    except LicenseNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"status": "ok", "license": record.model_dump()}


@router.post("/{license_id}/renew")
@handle_api_errors
async def renew_license(license_id: str, request: Request, body: dict[str, Any]) -> dict[str, Any]:
    """Renew a license: re-sign with a new expiry date."""
    require_admin(request)
    _require_feature()
    from maop.enterprise.license_manager import LicenseNotFoundError, LicenseRenewRequest

    req = LicenseRenewRequest(**body)
    mgr = _get_manager()
    try:
        record = mgr.renew_license(license_id, new_expires_at=req.new_expires_at, actor=req.actor)
    except LicenseNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"status": "ok", "license": record.model_dump()}


@router.post("/{license_id}/revoke")
@handle_api_errors
async def revoke_license(license_id: str, request: Request, body: dict[str, Any]) -> dict[str, Any]:
    """Revoke a license."""
    require_admin(request)
    _require_feature()
    from maop.enterprise.license_manager import LicenseNotFoundError, LicenseRevokeRequest

    req = LicenseRevokeRequest(**body)
    mgr = _get_manager()
    try:
        record = mgr.revoke_license(license_id, reason=req.reason, actor=req.actor)
    except LicenseNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"status": "ok", "license": record.model_dump()}


@router.delete("/{license_id}")
@handle_api_errors
async def delete_license(license_id: str, request: Request) -> dict[str, Any]:
    """Delete a license and its audit logs."""
    require_admin(request)
    _require_feature()
    from maop.enterprise.license_manager import LicenseNotFoundError

    mgr = _get_manager()
    try:
        deleted = mgr.delete_license(license_id)
    except LicenseNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"status": "ok" if deleted else "error", "deleted": deleted}


@router.get("/{license_id}/audit")
@handle_api_errors
async def get_license_audit(
    license_id: str,
    request: Request,
    limit: int = Query(default=100, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
) -> dict[str, Any]:
    """Get the audit log for a specific license."""
    require_admin(request)
    _require_feature()
    mgr = _get_manager()
    entries = mgr.get_audit_logs(license_id=license_id, limit=limit, offset=offset)
    return {
        "status": "ok",
        "audit_logs": [e.model_dump() for e in entries],
        "count": len(entries),
    }


@router.get("/audit/list")
@handle_api_errors
async def list_audit_logs(
    request: Request,
    license_id: str = Query(default=""),
    action: str = Query(default=""),
    limit: int = Query(default=100, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
) -> dict[str, Any]:
    """List all license audit logs with optional filters."""
    require_admin(request)
    _require_feature()
    mgr = _get_manager()
    entries = mgr.get_audit_logs(license_id=license_id, action=action, limit=limit, offset=offset)
    return {
        "status": "ok",
        "audit_logs": [e.model_dump() for e in entries],
        "count": len(entries),
    }