"""Route scoring and cooldown API endpoints.

Provides visibility into the multi-factor route matching process:
- POST /api/routing/match: Preview which route a task would match and why
- GET /api/routing/cooldowns: List agents currently in cooldown
- GET /api/routing/scores: Show all route scores for a given task

Router 层只保留：路由定义、请求解析、权限检查、调用 service、响应
格式化、错误处理。业务逻辑在 ``maop.dashboard.services.routing_service``
中，框架无关。
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Request

from maop.config.env import get_root_dir
from maop.config.loader import load_config
from maop.core.security.middleware import require_admin
from maop.dashboard.error_handler import handle_api_errors
from maop.dashboard.routers import state
from maop.dashboard.services import routing_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/routing", tags=["routing"])


def _resolve_root() -> str:
    return str(get_root_dir(default=str(state.MAOP_ROOT)))


@router.get("")
@handle_api_errors("routing preview root")
async def routing_root(request: Request) -> dict[str, Any]:
    """GET /api/routing — 返回所有路由列表，供前端 Tools.vue 根路径调用。"""
    require_admin(request)
    root = _resolve_root()
    config = load_config(root)
    routes = []
    for rk, route in config.routing.items():
        routes.append({
            "routing_key": rk,
            "primary": route.primary,
            "fallback": route.fallback or None,
            "tertiary": route.tertiary or None,
        })
    return {"status": "ok", "data": {"routes": routes}}


@router.post("/match")
@handle_api_errors("routing preview match")
async def preview_match(body: dict[str, Any], request: Request) -> dict[str, Any]:
    require_admin(request)
    """Preview route matching for a task description.

    Returns the matched route, agent, score, confidence, and all candidate
    routes with their individual scores.
    """
    task = body.get("task", "")
    if not task:
        raise HTTPException(status_code=400, detail="task is required")
    return {"status": "ok", **routing_service.preview_route_match(task)}


@router.get("/cooldowns")
@handle_api_errors("routing cooldowns")
async def get_cooldowns(request: Request) -> dict[str, Any]:
    """Get all agents currently in cooldown (recently failed)."""
    require_admin(request)
    return {"status": "ok", **routing_service.get_route_cooldowns()}


@router.get("/scores")
@handle_api_errors("routing scores")
async def get_route_scores(request: Request, task: str = "") -> dict[str, Any]:
    """Get scores for all routes against a given task."""
    require_admin(request)
    if not task:
        raise HTTPException(status_code=400, detail="task parameter is required")
    return {"status": "ok", **routing_service.get_route_scores(task)}
