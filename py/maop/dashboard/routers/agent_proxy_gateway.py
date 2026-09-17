"""MAOP Dashboard — Agent Proxy Gateway API endpoints.

暴露 ``AgentProxyGateway`` 企业内网 Agent 代理网关 via REST API，供桌面应用
管理代理规则 / 查询审计日志 / 设置部门预算。

所有端点要求 admin 角色（via ``require_admin`` 守卫）。
审计日志只追加不修改——本路由仅提供查询端点，无修改/删除端点。

业务逻辑已提取至 ``maop.dashboard.services.agent_service``（§4）。
本 router 仅保留：路由定义 / 请求参数解析 / 权限检查 /
service 调用 / 响应格式化 / 错误处理。
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Request, Query
from pydantic import BaseModel, Field

from maop.core.security.middleware import require_admin
from maop.dashboard.error_handler import handle_api_errors
from maop.dashboard.services import agent_service

logger = logging.getLogger(__name__)

router = APIRouter()


# ── Pydantic 请求模型 ──────────────────────────────────────────────


class CreateProxyRuleRequest(BaseModel):
    """POST /api/agent-proxy/rules 请求体。"""

    internal_path: str = Field(..., min_length=1, description="内网路径")
    external_agent: str = Field(..., min_length=1, description="外网 Agent 名")
    allowed_departments: list[str] = Field(default_factory=list, description="允许的部门")
    enabled: bool = Field(default=True, description="是否启用")


class SetDepartmentBudgetRequest(BaseModel):
    """POST /api/agent-proxy/budget 请求体。"""

    department: str = Field(..., min_length=1, description="部门名")
    agent: str = Field(..., min_length=1, description="Agent 名")
    monthly_budget: float = Field(..., ge=0.0, description="月预算(USD)")
    used: float = Field(default=0.0, ge=0.0, description="已用额度")
    reset_day: int = Field(default=1, ge=1, le=31, description="每月重置日(1-31)")


class CheckBudgetRequest(BaseModel):
    """POST /api/agent-proxy/budget/check 请求体。"""

    department: str = Field(..., min_length=1, description="部门名")
    agent: str = Field(..., min_length=1, description="Agent 名")
    cost: float = Field(..., ge=0.0, description="待扣减成本(USD)")


# ── 单例转发（供测试注入，转发到 service 层）──────────────────────
def _get_gateway() -> Any:
    return agent_service._get_gateway()


def _set_gateway(gateway: Any) -> None:
    agent_service._set_gateway(gateway)


# ── 代理规则端点 ──────────────────────────────────────────────────


@router.post("/api/agent-proxy/rules")
@handle_api_errors(
    "Add proxy rule",
    error_value={"status": "error", "error": "Add rule failed"},
)
async def api_add_proxy_rule(
    body: CreateProxyRuleRequest, request: Request
) -> dict[str, Any]:
    """添加代理规则。"""
    require_admin(request)
    rule_id = agent_service.add_proxy_rule(
        internal_path=body.internal_path,
        external_agent=body.external_agent,
        allowed_departments=body.allowed_departments,
        enabled=body.enabled,
    )
    return {"status": "ok", "rule_id": rule_id}


@router.get("/api/agent-proxy/rules")
@handle_api_errors(
    "List proxy rules",
    error_value={"status": "error", "rules": [], "count": 0, "error": "List failed"},
)
async def api_list_proxy_rules(request: Request) -> dict[str, Any]:
    """列出全部代理规则。"""
    require_admin(request)
    result = agent_service.list_proxy_rules()
    return {"status": "ok", **result}


@router.delete("/api/agent-proxy/rules/{rule_id}")
@handle_api_errors(
    "Remove proxy rule",
    error_value={"status": "error", "error": "Remove failed"},
)
async def api_remove_proxy_rule(rule_id: str, request: Request) -> dict[str, Any]:
    """移除代理规则。"""
    require_admin(request)
    agent_service.remove_proxy_rule(rule_id)
    return {"status": "ok", "removed": rule_id}


# ── 审计日志端点（只读）────────────────────────────────────────────


@router.get("/api/agent-proxy/audit")
@handle_api_errors(
    "Query audit log",
    error_value={"status": "error", "events": [], "count": 0, "error": "Query failed"},
)
async def api_query_audit(
    request: Request,
    user: str = Query("", description="按用户过滤"),
    agent: str = Query("", description="按 Agent 过滤"),
    limit: int = Query(100, ge=1, le=1000, description="返回条数上限"),
) -> dict[str, Any]:
    """查询审计日志（按时间倒序）。"""
    require_admin(request)
    result = agent_service.query_audit(user=user, agent=agent, limit=limit)
    return {"status": "ok", **result}


# ── 部门预算端点 ──────────────────────────────────────────────────


@router.post("/api/agent-proxy/budget")
@handle_api_errors(
    "Set department budget",
    error_value={"status": "error", "error": "Set budget failed"},
)
async def api_set_department_budget(
    body: SetDepartmentBudgetRequest, request: Request
) -> dict[str, Any]:
    """设置部门预算。"""
    require_admin(request)
    result = agent_service.set_department_budget(
        department=body.department,
        agent=body.agent,
        monthly_budget=body.monthly_budget,
        used=body.used,
        reset_day=body.reset_day,
    )
    return {"status": "ok", **result}


@router.get("/api/agent-proxy/budget")
@handle_api_errors(
    "Get budget summary",
    error_value={"status": "error", "budgets": [], "count": 0, "error": "Query failed"},
)
async def api_get_budget_summary(
    request: Request,
    department: str = Query("", description="按部门过滤（空=全部）"),
) -> dict[str, Any]:
    """查询预算汇总。"""
    require_admin(request)
    result = agent_service.get_budget_summary(department)
    return {"status": "ok", **result}


@router.post("/api/agent-proxy/budget/check")
@handle_api_errors(
    "Check budget",
    error_value={"status": "error", "error": "Check failed"},
)
async def api_check_budget(
    body: CheckBudgetRequest, request: Request
) -> dict[str, Any]:
    """检查部门预算是否足够扣减。"""
    require_admin(request)
    ok = agent_service.check_budget(body.department, body.agent, body.cost)
    return {
        "status": "ok",
        "allowed": ok,
        "department": body.department,
        "agent": body.agent,
        "cost": body.cost,
    }
