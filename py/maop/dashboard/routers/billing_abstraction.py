"""MAOP Dashboard — 计费抽象引擎 API 端点.

桥接 ``BillingEngine`` 计费引擎到前端，提供计费/估算/摘要/记录列表 4 个端点。
所有端点需 admin 鉴权。

业务逻辑已提取至 ``maop.dashboard.services.billing_service``（§3）。
本 router 仅保留：路由定义 / 请求参数解析 / 权限检查 /
service 调用 / 响应格式化 / 错误处理。
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Request, Query
from pydantic import BaseModel, Field

from maop.core.security.middleware import require_admin
from maop.dashboard.error_handler import handle_api_errors
from maop.dashboard.services import billing_service

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


# ── 单例转发（供测试注入，转发到 service 层）──────────────────────
def _get_billing_engine() -> Any:
    return billing_service._get_billing_engine()


def _set_billing_engine(engine: Any) -> None:
    billing_service._set_billing_engine(engine)


# ── API 端点 ────────────────────────────────────────────────────────
@router.post("/api/billing/charge")
@handle_api_errors("Billing charge", error_value={"status": "error", "error": "Charge failed"})
async def api_billing_charge(body: BillingChargeRequest, request: Request) -> dict[str, Any]:
    """对 Agent 调用进行计费。"""
    require_admin(request)
    if not body.agent_name:
        raise HTTPException(400, "missing agent_name")
    result = billing_service.charge_agent(
        body.agent_name,
        tokens=body.tokens,
        calls=body.calls,
        session_id=body.session_id,
        model=body.model,
    )
    return {"status": "ok", **result}


@router.get("/api/billing/estimate")
@handle_api_errors("Billing estimate", error_value={"status": "error", "error": "Estimate failed"})
async def api_billing_estimate(
    request: Request,
    agent_name: str = Query("", description="Agent 名称"),
    tokens: int = Query(0, ge=0, description="Token 数量"),
    calls: int = Query(1, ge=0, description="调用次数"),
) -> dict[str, Any]:
    """估算成本（不实际扣减）。"""
    require_admin(request)
    if not agent_name:
        raise HTTPException(400, "missing agent_name")
    cost = billing_service.estimate_agent_cost(agent_name, tokens=tokens, calls=calls)
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
    summary = billing_service.get_agent_billing_summary(agent_name)
    return {"status": "ok", "summary": summary}


@router.get("/api/billing/records")
@handle_api_errors("Billing records", error_value={"status": "error", "error": "Records failed"})
async def api_billing_records(
    request: Request,
    agent_name: str = Query("", description="按 Agent 过滤"),
    limit: int = Query(100, ge=1, le=10000, description="返回条数上限"),
) -> dict[str, Any]:
    """获取计费记录列表。"""
    require_admin(request)
    records = billing_service.get_billing_records(agent_name=agent_name, limit=limit)
    return {
        "status": "ok",
        "records": records,
        "count": len(records),
    }
