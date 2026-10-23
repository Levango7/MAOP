"""MAOP Dashboard — Agent 额度分桶管理 API 端点.

桥接 ``QuotaBucket`` 三桶额度管理器到前端，提供获取/设置/消耗/退还/
剩余/重置 6 个端点。所有端点需 admin 鉴权。

业务逻辑已提取至 ``maop.dashboard.services.billing_service``（§2）。
本 router 仅保留：路由定义 / 请求参数解析 / 权限检查 /
service 调用 / 响应格式化 / 错误处理。

请求/响应模型在 ``maop.dashboard.routers.quota_schemas``。
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Request

from maop.core.security.middleware import require_admin
from maop.dashboard.error_handler import handle_api_errors
from maop.dashboard.routers.quota_schemas import (
    CONSUME_BUCKET_NAMES,
    SETTABLE_BUCKET_NAMES,
    QuotaConsumeEnvelope,
    QuotaConsumeRequest,
    QuotaGetEnvelope,
    QuotaRefundEnvelope,
    QuotaRefundRequest,
    QuotaRemainingEnvelope,
    QuotaResetEnvelope,
    QuotaResetRequest,
    QuotaSetEnvelope,
    QuotaSetRequest,
)
from maop.dashboard.services import billing_service

logger = logging.getLogger(__name__)

router = APIRouter()


# ── 单例转发（供测试注入，转发到 service 层）──────────────────────
def _get_quota_bucket() -> Any:
    return billing_service._get_quota_bucket()


def _set_quota_bucket(bucket: Any) -> None:
    billing_service._set_quota_bucket(bucket)


# ── API 端点 ────────────────────────────────────────────────────────
@router.get("/api/quota/{agent_name}", response_model=QuotaGetEnvelope)
@handle_api_errors("Quota get", error_value={"status": "error", "error": "Get failed"})
async def api_quota_get(agent_name: str, request: Request) -> dict[str, Any]:
    """获取 Agent 额度配置。"""
    require_admin(request)
    if not agent_name:
        raise HTTPException(400, "missing agent_name")
    quota = billing_service.get_agent_quota(agent_name)
    return {"status": "ok", "agent_name": agent_name, "quota": quota}


@router.put("/api/quota/{agent_name}", response_model=QuotaSetEnvelope)
@handle_api_errors("Quota set", error_value={"status": "error", "error": "Set failed"})
async def api_quota_set(agent_name: str, body: QuotaSetRequest, request: Request) -> dict[str, Any]:
    """设置 Agent 某个桶的额度上限（不影响其它桶与已用量）。"""
    require_admin(request)
    if not agent_name:
        raise HTTPException(400, "missing agent_name")
    if body.bucket not in SETTABLE_BUCKET_NAMES:
        logger.warning("[quota] set invalid bucket: %r", body.bucket)
        raise HTTPException(400, "invalid bucket")
    quota = billing_service.set_agent_bucket_quota(agent_name, body.bucket, body.total)
    return {"status": "ok", "agent_name": agent_name, "quota": quota}


@router.post("/api/quota/{agent_name}/consume", response_model=QuotaConsumeEnvelope)
@handle_api_errors("Quota consume", error_value={"status": "error", "error": "Consume failed"})
async def api_quota_consume(
    agent_name: str, body: QuotaConsumeRequest, request: Request,
) -> dict[str, Any]:
    """消耗 Agent 额度（``bucket=auto`` 时按 免费 → 预付 → 后付 顺序扣减）。"""
    require_admin(request)
    if body.bucket not in CONSUME_BUCKET_NAMES:
        logger.warning("[quota] consume invalid bucket: %r", body.bucket)
        raise HTTPException(400, "invalid bucket")
    result = billing_service.consume_agent_quota(agent_name, body.amount, body.bucket)
    return {"status": "ok", "agent_name": agent_name, "result": result}


@router.post("/api/quota/{agent_name}/refund", response_model=QuotaRefundEnvelope)
@handle_api_errors("Quota refund", error_value={"status": "error", "error": "Refund failed"})
async def api_quota_refund(agent_name: str, body: QuotaRefundRequest, request: Request) -> dict[str, Any]:
    """退还 Agent 额度。"""
    require_admin(request)
    try:
        billing_service.refund_agent_quota(agent_name, body.amount, body.bucket)
    except ValueError as exc:
        logger.warning("[quota] refund invalid bucket: %s", exc)
        raise HTTPException(400, "invalid bucket")
    return {"status": "ok", "agent_name": agent_name}


@router.get("/api/quota/{agent_name}/remaining", response_model=QuotaRemainingEnvelope)
@handle_api_errors("Quota remaining", error_value={"status": "error", "error": "Remaining failed"})
async def api_quota_remaining(agent_name: str, request: Request) -> dict[str, Any]:
    """获取 Agent 剩余额度。"""
    require_admin(request)
    remaining = billing_service.get_agent_remaining(agent_name)
    total = remaining["free"] + remaining["prepaid"] + remaining["postpaid"]
    return {"status": "ok", "agent_name": agent_name, "remaining": remaining, "total": total}


@router.post("/api/quota/{agent_name}/reset", response_model=QuotaResetEnvelope)
@handle_api_errors("Quota reset", error_value={"status": "error", "error": "Reset failed"})
async def api_quota_reset(agent_name: str, body: QuotaResetRequest, request: Request) -> dict[str, Any]:
    """重置 Agent 额度使用量。"""
    require_admin(request)
    try:
        billing_service.reset_agent_quota(agent_name, body.bucket)
    except ValueError as exc:
        logger.warning("[quota] reset invalid bucket: %s", exc)
        raise HTTPException(400, "invalid bucket")
    return {"status": "ok", "agent_name": agent_name}
