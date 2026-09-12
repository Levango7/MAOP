"""MAOP Dashboard — 计费抽象引擎 API 端点.

桥接 ``BillingEngine`` 计费引擎到前端，提供计费/估算/摘要/记录列表 4 个端点。
所有端点需 admin 鉴权。
"""

from __future__ import annotations

import logging
import threading
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from maop.core.agent.billing.billing_abstraction import BillingEngine, BillingRecord
from maop.core.security.middleware import require_admin
from maop.dashboard.error_handler import handle_api_errors

logger = logging.getLogger(__name__)

router = APIRouter()


# ── Pydantic 请求模型 ──────────────────────────────────────────────
class BillingChargeRequest(BaseModel):
    """POST /api/billing/charge 请求体。"""

    agent_name: str = Field(default="", max_length=256, description="Agent 名称")
    tokens: int = Field(default=0, ge=0, description="消耗的 token 数")
    calls: int = Field(default=1, ge=0, description="调用次数")
    session_id: str = Field(default="", max_length=256, description="会话 ID")
    model: str = Field(default="", max_length=256, description="模型名称")


# ── BillingEngine 单例（双重检查锁定）───────────────────────────────
_billing_engine: BillingEngine | None = None
_billing_engine_lock = threading.Lock()


def _get_billing_engine() -> BillingEngine:
    """获取 BillingEngine 单例。"""
    global _billing_engine
    if _billing_engine is None:
        with _billing_engine_lock:
            if _billing_engine is None:
                _billing_engine = BillingEngine()
    return _billing_engine


# ── API 端点 ────────────────────────────────────────────────────────
@router.post("/api/billing/charge")
@handle_api_errors("Billing charge", error_value={"status": "error", "error": "Charge failed"})
async def api_billing_charge(body: BillingChargeRequest, request: Request) -> dict[str, Any]:
    """对 Agent 调用进行计费。"""
    require_admin(request)
    if not body.agent_name:
        raise HTTPException(400, "missing agent_name")
    engine = _get_billing_engine()
    result = engine.charge(
        body.agent_name,
        tokens=body.tokens,
        calls=body.calls,
        session_id=body.session_id,
        model=body.model,
    )
    return {
        "status": "ok",
        "success": result.success,
        "record": result.record.model_dump(mode="json") if result.record else None,
        "error": result.error,
    }


@router.get("/api/billing/estimate")
@handle_api_errors("Billing estimate", error_value={"status": "error", "error": "Estimate failed"})
async def api_billing_estimate(
    request: Request,
    agent_name: str = "",
    tokens: int = 0,
    calls: int = 1,
) -> dict[str, Any]:
    """估算成本（不实际扣减）。"""
    require_admin(request)
    if tokens < 0 or calls < 0:
        raise HTTPException(400, "tokens and calls must be non-negative")
    if not agent_name:
        raise HTTPException(400, "missing agent_name")
    engine = _get_billing_engine()
    cost = engine.estimate_cost(agent_name, tokens=tokens, calls=calls)
    return {
        "status": "ok",
        "agent_name": agent_name,
        "tokens": tokens,
        "calls": calls,
        "estimated_cost_usd": cost,
    }


@router.get("/api/billing/summary/{agent_name}")
@handle_api_errors("Billing summary", error_value={"status": "error", "error": "Summary failed"})
async def api_billing_summary(agent_name: str, request: Request) -> dict[str, Any]:
    """获取 Agent 计费摘要。"""
    require_admin(request)
    engine = _get_billing_engine()
    summary = engine.get_agent_billing_summary(agent_name)
    return {"status": "ok", "summary": summary}


@router.get("/api/billing/records")
@handle_api_errors("Billing records", error_value={"status": "error", "error": "Records failed"})
async def api_billing_records(
    request: Request,
    agent_name: str = "",
    limit: int = 100,
) -> dict[str, Any]:
    """获取计费记录列表。"""
    require_admin(request)
    if limit <= 0 or limit > 10000:
        raise HTTPException(400, "limit must be between 1 and 10000")
    engine = _get_billing_engine()
    records = engine.get_billing_records(agent_name=agent_name, limit=limit)
    return {
        "status": "ok",
        "records": [r.model_dump(mode="json") for r in records],
        "count": len(records),
    }