"""MAOP Dashboard — Agent 额度分桶管理 API 端点.

桥接 ``QuotaBucket`` 三桶额度管理器到前端，提供获取/设置/消耗/退还/
剩余/重置 6 个端点。所有端点需 admin 鉴权。
"""

from __future__ import annotations

import logging
import threading
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from maop.core.agent.billing.quota_bucket import QuotaBucket, QuotaEntry
from maop.core.security.middleware import require_admin
from maop.dashboard.error_handler import handle_api_errors

logger = logging.getLogger(__name__)

router = APIRouter()


# ── Pydantic 请求模型 ──────────────────────────────────────────────
class QuotaConsumeRequest(BaseModel):
    """POST /api/quota/{agent_name}/consume 请求体。"""

    amount: int = Field(default=0, ge=0, description="消耗量")


class QuotaRefundRequest(BaseModel):
    """POST /api/quota/{agent_name}/refund 请求体。"""

    amount: int = Field(default=0, ge=0, description="退还量")
    bucket: str = Field(default="auto", description="退还到哪个桶: free/prepaid/postpaid/auto")


class QuotaResetRequest(BaseModel):
    """POST /api/quota/{agent_name}/reset 请求体。"""

    bucket: str = Field(default="all", description="重置哪个桶: free/prepaid/postpaid/all")


# ── QuotaBucket 单例（双重检查锁定）─────────────────────────────────
_quota_bucket: QuotaBucket | None = None
_quota_bucket_lock = threading.Lock()


def _get_quota_bucket() -> QuotaBucket:
    """获取 QuotaBucket 单例。"""
    global _quota_bucket
    if _quota_bucket is None:
        with _quota_bucket_lock:
            if _quota_bucket is None:
                _quota_bucket = QuotaBucket()
    return _quota_bucket


# ── API 端点 ────────────────────────────────────────────────────────
@router.get("/api/quota/{agent_name}")
@handle_api_errors("Quota get", error_value={"status": "error", "error": "Get failed"})
async def api_quota_get(agent_name: str, request: Request) -> dict[str, Any]:
    """获取 Agent 额度配置。"""
    require_admin(request)
    bucket = _get_quota_bucket()
    entry = bucket.get_quota(agent_name)
    return {"status": "ok", "agent_name": agent_name, "quota": entry.model_dump()}


@router.put("/api/quota/{agent_name}")
@handle_api_errors("Quota set", error_value={"status": "error", "error": "Set failed"})
async def api_quota_set(agent_name: str, body: QuotaEntry, request: Request) -> dict[str, Any]:
    """设置 Agent 额度配置。"""
    require_admin(request)
    if not agent_name:
        raise HTTPException(400, "missing agent_name")
    bucket = _get_quota_bucket()
    bucket.set_quota(agent_name, body)
    return {"status": "ok", "agent_name": agent_name}


@router.post("/api/quota/{agent_name}/consume")
@handle_api_errors("Quota consume", error_value={"status": "error", "error": "Consume failed"})
async def api_quota_consume(agent_name: str, body: QuotaConsumeRequest, request: Request) -> dict[str, Any]:
    """消耗 Agent 额度。"""
    require_admin(request)
    bucket = _get_quota_bucket()
    result = bucket.consume(agent_name, body.amount)
    return {"status": "ok", "agent_name": agent_name, "result": result.model_dump()}


@router.post("/api/quota/{agent_name}/refund")
@handle_api_errors("Quota refund", error_value={"status": "error", "error": "Refund failed"})
async def api_quota_refund(agent_name: str, body: QuotaRefundRequest, request: Request) -> dict[str, Any]:
    """退还 Agent 额度。"""
    require_admin(request)
    bucket = _get_quota_bucket()
    try:
        bucket.refund(agent_name, body.amount, body.bucket)
    except ValueError as exc:
        logger.warning("[quota] refund invalid bucket: %s", exc)
        raise HTTPException(400, "invalid bucket")
    return {"status": "ok", "agent_name": agent_name}


@router.get("/api/quota/{agent_name}/remaining")
@handle_api_errors("Quota remaining", error_value={"status": "error", "error": "Remaining failed"})
async def api_quota_remaining(agent_name: str, request: Request) -> dict[str, Any]:
    """获取 Agent 剩余额度。"""
    require_admin(request)
    bucket = _get_quota_bucket()
    remaining = bucket.get_remaining(agent_name)
    total = remaining["free"] + remaining["prepaid"] + remaining["postpaid"]
    return {"status": "ok", "agent_name": agent_name, "remaining": remaining, "total": total}


@router.post("/api/quota/{agent_name}/reset")
@handle_api_errors("Quota reset", error_value={"status": "error", "error": "Reset failed"})
async def api_quota_reset(agent_name: str, body: QuotaResetRequest, request: Request) -> dict[str, Any]:
    """重置 Agent 额度使用量。"""
    require_admin(request)
    bucket = _get_quota_bucket()
    try:
        bucket.reset_quota(agent_name, body.bucket)
    except ValueError as exc:
        logger.warning("[quota] reset invalid bucket: %s", exc)
        raise HTTPException(400, "invalid bucket")
    return {"status": "ok", "agent_name": agent_name}