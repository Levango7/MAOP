"""Enterprise license management router — exposes LicenseManager via FastAPI.

Implements the License Management API (PRD: ``docs/prd-license-management.md``).
All operations require admin role via ``require_admin`` and are gated by
``FeatureFlag.LICENSE_MANAGEMENT`` (enterprise-only).

业务逻辑已提取至 ``maop.dashboard.services.billing_service``（§4）。
本 router 仅保留：路由定义 / 请求参数解析 / 权限检查 / 特性守卫 /
service 调用 / 响应格式化 / 错误处理。

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

from maop.core.security.middleware import require_admin
from maop.dashboard.error_handler import handle_api_errors
from maop.dashboard.services import billing_service

# 直接复用 license_manager 中的 Pydantic 请求模型，让 FastAPI 自动校验。
from maop.enterprise.license_manager import (
    LicenseCreateRequest,
    LicenseRenewRequest,
    LicenseRevokeRequest,
    LicenseUpdateRequest,
    LicenseValidateRequest,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/licenses", tags=["licenses"])


# ── 单例转发（供测试注入，转发到 service 层）──────────────────────
def _get_manager() -> Any:
    return billing_service._get_license_manager()


def _set_manager(mgr: Any) -> None:
    billing_service._set_license_manager(mgr)


def _require_feature() -> None:
    """Return 404 if the LICENSE_MANAGEMENT feature is not available."""
    billing_service.require_license_management()


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
    licenses = billing_service.list_licenses(status=status)
    return {
        "status": "ok",
        "licenses": licenses,
        "count": len(licenses),
    }


@router.post("/create")
@handle_api_errors
async def create_license(request: Request, body: LicenseCreateRequest) -> dict[str, Any]:
    """Issue a new license. Requires admin."""
    require_admin(request)
    _require_feature()
    record = billing_service.create_license(
        customer=body.customer,
        expires_at=body.expires_at,
        max_users=body.max_users,
        fingerprint=body.fingerprint,
        features=body.features,
        issued_by=body.issued_by,
        notes=body.notes,
    )
    return {"status": "ok", "license": record}


@router.post("/validate")
@handle_api_errors
async def validate_license(request: Request, body: LicenseValidateRequest) -> dict[str, Any]:
    """Validate a license key (signature + expiry + revocation)."""
    require_admin(request)
    _require_feature()
    result = billing_service.validate_license(body.license_key)
    return {"status": "ok", "validation": result}


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
    entries = billing_service.list_license_audit_logs(
        license_id=license_id, action=action, limit=limit, offset=offset,
    )
    return {
        "status": "ok",
        "audit_logs": entries,
        "count": len(entries),
    }


@router.get("/{license_id}")
@handle_api_errors
async def get_license(license_id: str, request: Request) -> dict[str, Any]:
    """Get a single license by ID."""
    require_admin(request)
    _require_feature()
    from maop.enterprise.license_manager import LicenseNotFoundError

    try:
        record = billing_service.get_license(license_id)
    except LicenseNotFoundError as exc:
        # 批次3A: 脱敏——LicenseNotFoundError 细节不暴露给客户端，仅日志记录。
        logger.warning("[licenses] License not found: %s", exc)
        raise HTTPException(status_code=404, detail="License not found") from exc
    return {"status": "ok", "license": record}


@router.patch("/{license_id}")
@handle_api_errors
async def update_license(license_id: str, request: Request, body: LicenseUpdateRequest) -> dict[str, Any]:
    """Update editable license metadata (customer, max_users, fingerprint, features, notes)."""
    require_admin(request)
    _require_feature()
    from maop.enterprise.license_manager import LicenseNotFoundError

    try:
        record = billing_service.update_license(
            license_id,
            customer=body.customer,
            max_users=body.max_users,
            fingerprint=body.fingerprint,
            features=body.features,
            notes=body.notes,
        )
    except LicenseNotFoundError as exc:
        # 批次3A: 脱敏——LicenseNotFoundError 细节不暴露给客户端，仅日志记录。
        logger.warning("[licenses] License not found: %s", exc)
        raise HTTPException(status_code=404, detail="License not found") from exc
    return {"status": "ok", "license": record}


@router.post("/{license_id}/renew")
@handle_api_errors
async def renew_license(license_id: str, request: Request, body: LicenseRenewRequest) -> dict[str, Any]:
    """Renew a license: re-sign with a new expiry date."""
    require_admin(request)
    _require_feature()
    from maop.enterprise.license_manager import LicenseNotFoundError

    try:
        record = billing_service.renew_license(
            license_id, new_expires_at=body.new_expires_at, actor=body.actor,
        )
    except LicenseNotFoundError as exc:
        # 批次3A: 脱敏——LicenseNotFoundError 细节不暴露给客户端，仅日志记录。
        logger.warning("[licenses] License not found: %s", exc)
        raise HTTPException(status_code=404, detail="License not found") from exc
    return {"status": "ok", "license": record}


@router.post("/{license_id}/revoke")
@handle_api_errors
async def revoke_license(license_id: str, request: Request, body: LicenseRevokeRequest) -> dict[str, Any]:
    """Revoke a license."""
    require_admin(request)
    _require_feature()
    from maop.enterprise.license_manager import LicenseNotFoundError

    try:
        record = billing_service.revoke_license(
            license_id, reason=body.reason, actor=body.actor,
        )
    except LicenseNotFoundError as exc:
        # 批次3A: 脱敏——LicenseNotFoundError 细节不暴露给客户端，仅日志记录。
        logger.warning("[licenses] License not found: %s", exc)
        raise HTTPException(status_code=404, detail="License not found") from exc
    return {"status": "ok", "license": record}


@router.delete("/{license_id}")
@handle_api_errors
async def delete_license(license_id: str, request: Request) -> dict[str, Any]:
    """Delete a license and its audit logs."""
    require_admin(request)
    _require_feature()
    from maop.enterprise.license_manager import LicenseNotFoundError

    try:
        deleted = billing_service.delete_license(license_id)
    except LicenseNotFoundError as exc:
        # 批次3A: 脱敏——LicenseNotFoundError 细节不暴露给客户端，仅日志记录。
        logger.warning("[licenses] License not found: %s", exc)
        raise HTTPException(status_code=404, detail="License not found") from exc
    # P2-3 fix: 删除失败时返回 500 而非 200 + {"status": "error"}，
    # 由 @handle_api_errors 装饰器统一渲染为 ErrorSchema 响应。
    if not deleted:
        raise HTTPException(status_code=500, detail="Failed to delete license")
    return {"status": "ok", "deleted": True}


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
    entries = billing_service.get_license_audit(license_id, limit=limit, offset=offset)
    return {
        "status": "ok",
        "audit_logs": entries,
        "count": len(entries),
    }
