"""MAOP Dashboard — Agent Router API endpoints.

暴露 ``AgentRouter`` 路由器 via REST API，供桌面应用调度层执行
路由选择、列出策略、管理并发槽位。

所有端点要求 admin 角色（via ``require_admin`` 守卫）。

业务逻辑已提取至 ``maop.dashboard.services.agent_service``（§2）。
本 router 仅保留：路由定义 / 请求参数解析 / 权限检查 /
service 调用 / 响应格式化 / 错误处理。
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field, field_validator

from maop.core.security.middleware import require_admin
from maop.dashboard.error_handler import handle_api_errors
from maop.dashboard.services import agent_service

logger = logging.getLogger(__name__)

router = APIRouter()


# ── Pydantic 请求模型 ──────────────────────────────────────────────
class RouteRequest(BaseModel):
    """POST /api/agent-router/route 请求体。

    与 ``RoutingContext`` 字段对齐，但 capabilities 用 list[str] 接收
    再转换为 ``AgentCapability`` 枚举。
    """

    required_capabilities: list[str] = Field(
        default_factory=list, description="需要的能力列表"
    )
    preferred_agent: str = Field(default="", description="优先 Agent 名")
    excluded_agents: list[str] = Field(
        default_factory=list, description="排除的 Agent 名列表"
    )
    max_cost_tier: str = Field(default="low", description="成本偏好: low/medium/high/any")
    require_healthy: bool = Field(default=True, description="是否要求健康")
    session_id: str = Field(default="", description="会话 ID（负载均衡粘性）")
    strategy: str = Field(
        default="capability_match",
        description="路由策略: capability_match/cost_optimized/load_balanced/priority/round_robin",
    )

    @field_validator("max_cost_tier")
    @classmethod
    def _validate_cost_tier(cls, v: str) -> str:
        allowed = {"low", "medium", "high", "any"}
        if v not in allowed:
            raise ValueError(f"max_cost_tier must be one of {sorted(allowed)}")
        return v


class AgentSlotRequest(BaseModel):
    """POST /api/agent-router/acquire|release 请求体。"""

    agent_name: str = Field(..., min_length=1, max_length=256, description="Agent 名称")


# ── 单例转发（供测试注入，转发到 service 层）──────────────────────
def _get_router() -> Any:
    return agent_service._get_router()


def _set_router(router: Any) -> None:
    agent_service._set_router(router)


# ── 端点 ──────────────────────────────────────────────────────────
@router.post("/api/agent-router/route")
@handle_api_errors(
    "Agent route",
    error_value={"status": "error", "error": "Route failed"},
)
async def api_agent_route(body: RouteRequest, request: Request) -> dict[str, Any]:
    """执行路由选择，返回主 Agent + 降级链 + 候选。"""
    require_admin(request)
    try:
        result = agent_service.agent_route(
            required_capabilities=body.required_capabilities,
            preferred_agent=body.preferred_agent,
            excluded_agents=body.excluded_agents,
            max_cost_tier=body.max_cost_tier,
            require_healthy=body.require_healthy,
            session_id=body.session_id,
            strategy=body.strategy,
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    return {"status": "ok", **result}


@router.get("/api/agent-router/strategies")
@handle_api_errors(
    "List strategies",
    error_value={"status": "error", "strategies": [], "error": "List failed"},
)
async def api_list_strategies(request: Request) -> dict[str, Any]:
    """列出所有可用路由策略。"""
    require_admin(request)
    result = agent_service.list_strategies()
    return {"status": "ok", **result}


@router.post("/api/agent-router/acquire")
@handle_api_errors(
    "Acquire agent slot",
    error_value={"status": "error", "error": "Acquire failed"},
)
async def api_acquire_slot(body: AgentSlotRequest, request: Request) -> dict[str, Any]:
    """占用一个 Agent 并发槽位。"""
    require_admin(request)
    result = agent_service.acquire_slot(body.agent_name)
    if result is None:
        raise HTTPException(409, f"Cannot acquire slot for agent: {body.agent_name}")
    return {"status": "ok", **result}


@router.post("/api/agent-router/release")
@handle_api_errors(
    "Release agent slot",
    error_value={"status": "error", "error": "Release failed"},
)
async def api_release_slot(body: AgentSlotRequest, request: Request) -> dict[str, Any]:
    """释放一个 Agent 并发槽位。"""
    require_admin(request)
    result = agent_service.release_slot(body.agent_name)
    if result is None:
        raise HTTPException(404, f"Agent not found: {body.agent_name}")
    return {"status": "ok", **result}


@router.get("/api/agent-router/active")
@handle_api_errors(
    "Active counts",
    error_value={"status": "error", "active": {}, "error": "Query failed"},
)
async def api_active_counts(request: Request) -> dict[str, Any]:
    """获取所有 Agent 的当前活跃并发数。"""
    require_admin(request)
    result = agent_service.active_counts()
    return {"status": "ok", **result}
