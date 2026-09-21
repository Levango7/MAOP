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
import uuid
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


# ════════════════════════════════════════════════════════════════════
# GDPR 法定流程端点（数据主体请求 / DPA / 处理活动记录）
#
# 2026-09-21 接线：此前 GDPRComplianceManager 自 e2e0f2a 模块化拆分后
# 从未被实例化、无测试覆盖，这些能力实为缺失（在用的 ComplianceManager
# 只有 delete/export 两个方法）。
#
# 安全约定与上面的端点完全一致：
#   * require_admin + has_feature(MULTI_USER)
#   * tenant_id 一律取自 JWT；即使请求体携带也强制覆盖，绝不采信。
# ════════════════════════════════════════════════════════════════════

from maop.core.tenant.gdpr_manager import ProcessingAgreement, ProcessingRecord


def _gdpr_manager(request: Request) -> Any:
    """取 GDPR 管理器单例；未配置 root_dir 时转 500。"""
    root_dir = getattr(request.app.state, "root_dir", None)
    try:
        return rbac_service.get_gdpr_manager(root_dir)
    except rbac_service.ComplianceNotConfigured:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Compliance service not configured",
        )


def _require_multi_user() -> None:
    if not has_feature(FeatureFlag.MULTI_USER):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Compliance APIs not available in this edition",
        )


# ── 数据主体请求（GDPR Articles 15-22）────────────────────────────


class GdprSubjectRequestCreate(BaseModel):
    user_id: str
    detail: dict[str, Any] = {}
    # NOTE: tenant_id 故意不在请求体中，一律取 JWT。


class GdprPortabilityRequestCreate(BaseModel):
    user_id: str
    fmt: str = "json"
    detail: dict[str, Any] = {}
    # NOTE: tenant_id 故意不在请求体中，一律取 JWT。


@router.post("/gdpr/access-request")
@handle_api_errors
async def gdpr_access_request(body: GdprSubjectRequestCreate, request: Request) -> dict[str, Any]:
    """GDPR 访问权（Art. 15）：导出用户数据并留存请求记录。"""
    _require_multi_user()
    require_admin(request)
    tenant_id = _tenant_id_from_jwt(request)
    mgr = _gdpr_manager(request)
    req, report = mgr.access_request(
        body.user_id, tenant_id=tenant_id, detail=body.detail or None,
    )
    return {"status": "ok", "request": req.model_dump(), "report": report.model_dump()}


@router.post("/gdpr/erasure-request")
@handle_api_errors
async def gdpr_erasure_request(body: GdprSubjectRequestCreate, request: Request) -> dict[str, Any]:
    """GDPR 删除权（Art. 17）：删除用户数据并留存请求记录。"""
    _require_multi_user()
    require_admin(request)
    tenant_id = _tenant_id_from_jwt(request)
    mgr = _gdpr_manager(request)
    req, report = mgr.right_to_erasure(
        body.user_id, tenant_id=tenant_id, detail=body.detail or None,
    )
    return {"status": "ok", "request": req.model_dump(), "report": report.model_dump()}


@router.post("/gdpr/portability-request")
@handle_api_errors
async def gdpr_portability_request(
    body: GdprPortabilityRequestCreate, request: Request,
) -> dict[str, Any]:
    """GDPR 数据可携权（Art. 20）：导出用户主动提供的数据。"""
    _require_multi_user()
    require_admin(request)
    tenant_id = _tenant_id_from_jwt(request)
    mgr = _gdpr_manager(request)
    req, report = mgr.data_portability(
        body.user_id, tenant_id=tenant_id, fmt=body.fmt, detail=body.detail or None,
    )
    return {"status": "ok", "request": req.model_dump(), "report": report.model_dump()}


@router.get("/gdpr/requests")
@handle_api_errors
async def gdpr_list_requests(
    request: Request,
    user_id: str = "",
    status_filter: str = "",
    request_type: str = "",
) -> dict[str, Any]:
    """列出本租户的数据主体请求。

    G-07：结果按 JWT 中的租户过滤，客户端无法看到其他租户的请求。
    """
    _require_multi_user()
    require_admin(request)
    tenant_id = _tenant_id_from_jwt(request)
    mgr = _gdpr_manager(request)
    rows = mgr.list_requests(
        user_id=user_id, tenant_id=tenant_id, status=status_filter,
        request_type=request_type,
    )
    return {"status": "ok", "requests": [r.model_dump() for r in rows], "total": len(rows)}


@router.get("/gdpr/requests/{request_id}")
@handle_api_errors
async def gdpr_get_request(request_id: str, request: Request) -> dict[str, Any]:
    """按 ID 取数据主体请求；跨租户访问返回 404（不泄露存在性）。"""
    _require_multi_user()
    require_admin(request)
    tenant_id = _tenant_id_from_jwt(request)
    mgr = _gdpr_manager(request)
    req = mgr.get_request(request_id)
    if req is None or req.tenant_id != tenant_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Request not found")
    return {"status": "ok", "request": req.model_dump()}


# ── 数据处理协议 DPA（Art. 28）───────────────────────────────────


class DpaCreate(BaseModel):
    controller_name: str
    processor_name: str
    purpose: str = ""
    data_categories: list[str] = []
    sub_processors: list[str] = []
    security_measures: list[str] = []
    effective_date: str = ""
    termination_date: str = ""
    status: str = "active"
    # NOTE: tenant_id / dpa_id 不由客户端指定。


@router.post("/gdpr/dpa")
@handle_api_errors
async def gdpr_register_dpa(body: DpaCreate, request: Request) -> dict[str, Any]:
    """登记数据处理协议（DPA, Art. 28）。租户取自 JWT 并强制覆盖。"""
    _require_multi_user()
    require_admin(request)
    tenant_id = _tenant_id_from_jwt(request)
    mgr = _gdpr_manager(request)
    dpa = ProcessingAgreement(dpa_id=f"dpa-{uuid.uuid4().hex[:12]}", **body.model_dump())
    dpa.tenant_id = tenant_id          # 强制覆盖，绝不采信请求体
    saved = mgr.register_dpa(dpa)
    return {"status": "ok", "dpa": saved.model_dump()}


@router.get("/gdpr/dpa")
@handle_api_errors
async def gdpr_list_dpas(request: Request, dp_status: str = "") -> dict[str, Any]:
    """列出本租户的 DPA。"""
    _require_multi_user()
    require_admin(request)
    tenant_id = _tenant_id_from_jwt(request)
    mgr = _gdpr_manager(request)
    rows = mgr.list_dpas(tenant_id=tenant_id, status=dp_status)
    return {"status": "ok", "dpas": [d.model_dump() for d in rows], "total": len(rows)}


@router.get("/gdpr/dpa/{dpa_id}")
@handle_api_errors
async def gdpr_get_dpa(dpa_id: str, request: Request) -> dict[str, Any]:
    """按 ID 取 DPA；跨租户返回 404。"""
    _require_multi_user()
    require_admin(request)
    tenant_id = _tenant_id_from_jwt(request)
    mgr = _gdpr_manager(request)
    dpa = mgr.get_dpa(dpa_id)
    if dpa is None or dpa.tenant_id != tenant_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="DPA not found")
    return {"status": "ok", "dpa": dpa.model_dump()}


# ── 处理活动记录（Art. 30）───────────────────────────────────────


class ProcessingRecordCreate(BaseModel):
    """字段与 :class:`ProcessingRecord` 对齐（除 tenant_id / record_id）。

    .. note::
       两处易错且已踩过：``cross_border_transfers`` 是 **list**（不是 bool），
       ``retention_period_days`` 是 **int**（不接受 None）——照抄字段名而不核对
       类型会导致 pydantic 校验失败，且被 ``handle_api_errors`` 吞成 500 响应。
    """

    activity_name: str
    purpose: str = ""
    data_categories: list[str] = []
    data_subject_categories: list[str] = []
    recipients: list[str] = []
    cross_border_transfers: list[str] = []
    retention_period_days: int = 0
    security_measures: list[str] = []
    legal_basis: str = ""
    # NOTE: tenant_id / record_id 不由客户端指定。


@router.post("/gdpr/processing-records")
@handle_api_errors
async def gdpr_record_processing(
    body: ProcessingRecordCreate, request: Request,
) -> dict[str, Any]:
    """登记数据处理活动（Art. 30 记录）。租户取自 JWT 并强制覆盖。"""
    _require_multi_user()
    require_admin(request)
    tenant_id = _tenant_id_from_jwt(request)
    mgr = _gdpr_manager(request)
    rec = ProcessingRecord(record_id=f"proc-{uuid.uuid4().hex[:12]}", **body.model_dump())
    rec.tenant_id = tenant_id           # 强制覆盖
    saved = mgr.record_processing_activity(rec)
    return {"status": "ok", "record": saved.model_dump()}


@router.get("/gdpr/processing-records")
@handle_api_errors
async def gdpr_list_processing_records(request: Request) -> dict[str, Any]:
    """列出本租户的处理活动记录（Art. 30）。"""
    _require_multi_user()
    require_admin(request)
    tenant_id = _tenant_id_from_jwt(request)
    mgr = _gdpr_manager(request)
    rows = mgr.list_processing_records(tenant_id=tenant_id)
    return {"status": "ok", "records": [r.model_dump() for r in rows], "total": len(rows)}


@router.get("/gdpr/processing-records/{record_id}")
@handle_api_errors
async def gdpr_get_processing_record(record_id: str, request: Request) -> dict[str, Any]:
    """按 ID 取处理活动记录；跨租户返回 404。"""
    _require_multi_user()
    require_admin(request)
    tenant_id = _tenant_id_from_jwt(request)
    mgr = _gdpr_manager(request)
    rec = mgr.get_processing_record(record_id)
    if rec is None or rec.tenant_id != tenant_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Record not found")
    return {"status": "ok", "record": rec.model_dump()}


# 供测试与运维查询请求类型常量
GDPR_REQUEST_TYPES: tuple[str, ...] = ("access", "erasure", "portability")
