"""MAOP Dashboard — Agent Router API endpoints.

暴露 ``AgentRouter`` 路由器 via REST API，供桌面应用调度层执行
路由选择、列出策略、管理并发槽位。

所有端点要求 admin 角色（via ``require_admin`` 守卫）。
"""

from __future__ import annotations

import logging
import threading
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from maop.core.security.middleware import require_admin
from maop.dashboard.error_handler import handle_api_errors

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


class AgentSlotRequest(BaseModel):
    """POST /api/agent-router/acquire|release 请求体。"""

    agent_name: str = Field(..., min_length=1, max_length=256, description="Agent 名称")


# ── 单例（双重检查锁定）────────────────────────────────────────────
_router: Any = None
_router_lock = threading.Lock()


def _get_router() -> Any:
    """惰性初始化全局 AgentRouter 单例。"""
    global _router
    if _router is None:
        with _router_lock:
            if _router is None:
                from maop.core.agent.router.agent_router import AgentRouter
                _router = AgentRouter()
    return _router


def _set_router(router: Any) -> None:
    """供测试注入自定义 router（隔离 DB）。"""
    global _router
    with _router_lock:
        _router = router


# ── 端点 ──────────────────────────────────────────────────────────
@router.post("/api/agent-router/route")
@handle_api_errors(
    "Agent route",
    error_value={"status": "error", "error": "Route failed"},
)
async def api_agent_route(body: RouteRequest, request: Request) -> dict[str, Any]:
    """执行路由选择，返回主 Agent + 降级链 + 候选。"""
    require_admin(request)
    from maop.core.agent.registry.agent_catalog import AgentCapability
    from maop.core.agent.router.agent_router import (
        RoutingContext,
        RoutingStrategy,
    )

    # 转换能力枚举
    try:
        caps = [AgentCapability(c) for c in body.required_capabilities]
    except ValueError as exc:
        raise HTTPException(400, f"Invalid capability: {exc}")

    # 转换策略枚举
    try:
        strategy = RoutingStrategy(body.strategy)
    except ValueError:
        raise HTTPException(400, f"Invalid strategy: {body.strategy}")

    ctx = RoutingContext(
        required_capabilities=caps,
        preferred_agent=body.preferred_agent,
        excluded_agents=body.excluded_agents,
        max_cost_tier=body.max_cost_tier,
        require_healthy=body.require_healthy,
        session_id=body.session_id,
    )
    agent_router = _get_router()
    result = agent_router.route(ctx, strategy=strategy)
    return {
        "status": "ok",
        "primary": result.primary.model_dump(mode="json") if result.primary else None,
        "fallbacks": [a.model_dump(mode="json") for a in result.fallbacks],
        "alternatives": [a.model_dump(mode="json") for a in result.alternatives],
        "reason": result.reason,
    }


@router.get("/api/agent-router/strategies")
@handle_api_errors(
    "List strategies",
    error_value={"status": "error", "strategies": [], "error": "List failed"},
)
async def api_list_strategies(request: Request) -> dict[str, Any]:
    """列出所有可用路由策略。"""
    require_admin(request)
    from maop.core.agent.router.agent_router import RoutingStrategy

    strategies = [
        {"value": s.value, "name": s.name}
        for s in RoutingStrategy
    ]
    return {"status": "ok", "strategies": strategies, "count": len(strategies)}


@router.post("/api/agent-router/acquire")
@handle_api_errors(
    "Acquire agent slot",
    error_value={"status": "error", "error": "Acquire failed"},
)
async def api_acquire_slot(body: AgentSlotRequest, request: Request) -> dict[str, Any]:
    """占用一个 Agent 并发槽位。"""
    require_admin(request)
    agent_router = _get_router()
    ok = agent_router.acquire(body.agent_name)
    if not ok:
        raise HTTPException(409, f"Cannot acquire slot for agent: {body.agent_name}")
    active = agent_router.get_active_count(body.agent_name)
    return {"status": "ok", "agent_name": body.agent_name, "active_count": active}


@router.post("/api/agent-router/release")
@handle_api_errors(
    "Release agent slot",
    error_value={"status": "error", "error": "Release failed"},
)
async def api_release_slot(body: AgentSlotRequest, request: Request) -> dict[str, Any]:
    """释放一个 Agent 并发槽位。"""
    require_admin(request)
    agent_router = _get_router()
    agent_router.release(body.agent_name)
    active = agent_router.get_active_count(body.agent_name)
    return {"status": "ok", "agent_name": body.agent_name, "active_count": active}


@router.get("/api/agent-router/active")
@handle_api_errors(
    "Active counts",
    error_value={"status": "error", "active": {}, "error": "Query failed"},
)
async def api_active_counts(request: Request) -> dict[str, Any]:
    """获取所有 Agent 的当前活跃并发数。"""
    require_admin(request)
    agent_router = _get_router()
    # 从 catalog 获取所有 agent 名，结合 _active_count 返回
    catalog = agent_router.catalog
    all_agents = catalog.list_all()
    active: dict[str, int] = {}
    for a in all_agents:
        active[a.name] = agent_router.get_active_count(a.name)
    return {"status": "ok", "active": active, "count": len(active)}