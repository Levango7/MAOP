"""第三方中转平台管理 API 路由。

业务逻辑已提取至 ``maop.dashboard.services.integration_service``（§2 Relay）。
本 router 仅保留：路由定义 / 请求参数解析 / 权限检查 /
service 调用 / 响应格式化 / 错误处理。

提供中转平台的 CRUD、模型发现、价格对比、平台推荐等 HTTP 端点。

路由前缀：``/api/relay-platforms``

端点：
  GET    /                       列出所有平台（可按 type 过滤）
  POST   /                       注册平台
  DELETE /{name}                 删除平台
  GET    /{name}/models          发现平台可用模型
  POST   /compare                价格对比（body: {model_id: str}）
  GET    /recommend/{model_id}   推荐最优平台
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel

from maop.core.agent.llm_chat.relay_platform import (
    PriceComparison,
    RelayPlatform,
)
from maop.dashboard.error_handler import handle_api_errors
from maop.dashboard.services import integration_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/relay-platforms", tags=["relay-platforms"])


# ── 请求/响应模型 ────────────────────────────────────────────────────


class ComparePricesRequest(BaseModel):
    """价格对比请求体。"""

    model_id: str


class RecommendResponse(BaseModel):
    """平台推荐响应。"""

    model_id: str
    platform_name: str | None = None
    prefer_domestic: bool = False


# ── 端点实现 ────────────────────────────────────────────────────────


@router.get("", response_model=list[RelayPlatform])
@router.get("/", response_model=list[RelayPlatform], include_in_schema=False)
@handle_api_errors("list relay platforms", error_value=[])
async def list_platforms(
    request: Request,
    type: str = Query("", description="按类型过滤：relay/direct，空返回全部"),
) -> list[RelayPlatform]:
    """列出所有中转平台，可按类型过滤。"""
    mgr = integration_service.get_relay_manager(request.app.state)
    return integration_service.list_platforms(mgr, type=type)


@router.post("", response_model=RelayPlatform, status_code=201)
@router.post("/", response_model=RelayPlatform, status_code=201, include_in_schema=False)
@handle_api_errors("register relay platform")
async def register_platform(
    body: RelayPlatform, request: Request
) -> RelayPlatform:
    """注册或更新一个中转平台。"""
    mgr = integration_service.get_relay_manager(request.app.state)
    return integration_service.register_platform(mgr, body)


@router.delete("/{name}")
@handle_api_errors("delete relay platform")
async def remove_platform(name: str, request: Request) -> dict[str, Any]:
    """删除一个中转平台。"""
    mgr = integration_service.get_relay_manager(request.app.state)
    deleted = integration_service.remove_platform(mgr, name)
    if not deleted:
        raise HTTPException(status_code=404, detail=f"平台不存在: {name}")
    return {"status": "ok", "message": f"平台 {name} 已删除"}


@router.get("/{name}/models", response_model=list)
@handle_api_errors("discover relay platform models", error_value=[])
async def discover_models(
    name: str,
    request: Request,
    use_cache: bool = Query(True, description="是否使用缓存"),
) -> list[dict[str, Any]]:
    """发现指定中转平台的可用模型列表。"""
    mgr = integration_service.get_relay_manager(request.app.state)
    models = integration_service.discover_models(mgr, name, use_cache=use_cache)
    if models is None:
        raise HTTPException(status_code=404, detail=f"平台不存在: {name}")
    return models


@router.post("/compare", response_model=list[PriceComparison])
@handle_api_errors("compare relay platform prices", error_value=[])
async def compare_prices(
    body: ComparePricesRequest, request: Request
) -> list[PriceComparison]:
    """对比同一模型在不同平台的价格。"""
    mgr = integration_service.get_relay_manager(request.app.state)
    return integration_service.compare_prices(mgr, body.model_id)


@router.get("/recommend/{model_id}", response_model=RecommendResponse)
@handle_api_errors("recommend relay platform")
async def recommend_platform(
    model_id: str,
    request: Request,
    prefer_domestic: bool = Query(
        False, description="是否优先推荐国内平台"
    ),
) -> RecommendResponse:
    """推荐最优平台（价格最低 + 可用）。"""
    mgr = integration_service.get_relay_manager(request.app.state)
    platform_name = integration_service.recommend_platform(
        mgr, model_id, prefer_domestic=prefer_domestic,
    )
    return RecommendResponse(
        model_id=model_id,
        platform_name=platform_name,
        prefer_domestic=prefer_domestic,
    )
