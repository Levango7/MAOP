"""MAOP Dashboard — 模型授权网关 API 端点.

桥接 ``ModelGateway`` 模型授权网关到前端，提供权限规则管理/访问检查/
使用量记录/配置更新 7 个端点。所有端点需 admin 鉴权。

Router 层只保留：路由定义、请求解析（Pydantic）、权限检查
（require_admin）、调用 service、响应格式化、错误处理。业务逻辑
在 ``maop.dashboard.services.model_service`` 中，框架无关。
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field

from maop.core.agent.llm_chat.model_gateway import (
    ModelGatewayConfig,
    ModelPermission,
)
from maop.core.security.middleware import require_admin
from maop.dashboard.error_handler import handle_api_errors
from maop.dashboard.services import model_service

logger = logging.getLogger(__name__)

router = APIRouter()


# ── Pydantic 请求模型 ──────────────────────────────────────────────
class ModelCheckRequest(BaseModel):
    """POST /api/model-gateway/check 请求体。"""

    model: str = Field(default="", max_length=256, description="模型名称")
    agent: str = Field(default="", max_length=256, description="Agent 名称")
    session_id: str = Field(default="", max_length=256, description="会话 ID")


class ModelUsageRequest(BaseModel):
    """POST /api/model-gateway/usage 请求体。"""

    model: str = Field(default="", max_length=256, description="模型名称")
    tokens: int = Field(default=0, ge=0, description="token 数")
    agent: str = Field(default="", max_length=256, description="Agent 名称")
    session_id: str = Field(default="", max_length=256, description="会话 ID")


# ── API 端点：权限规则管理 ──────────────────────────────────────────
@router.get("/api/model-gateway/permissions")
@handle_api_errors("Model gateway permissions list", error_value={"status": "error", "error": "List failed"})
async def api_model_gateway_permissions_list(request: Request) -> dict[str, Any]:
    """列出所有权限规则。"""
    require_admin(request)
    return {"status": "ok", **model_service.list_gateway_permissions()}


@router.post("/api/model-gateway/permissions")
@handle_api_errors("Model gateway permissions add", error_value={"status": "error", "error": "Add failed"})
async def api_model_gateway_permissions_add(body: ModelPermission, request: Request) -> dict[str, Any]:
    """添加权限规则。"""
    require_admin(request)
    if not body.model_pattern:
        raise HTTPException(400, "missing model_pattern")
    return {"status": "ok", **model_service.add_gateway_permission(body)}


@router.delete("/api/model-gateway/permissions/{model_pattern:path}")
@handle_api_errors("Model gateway permissions delete", error_value={"status": "error", "error": "Delete failed"})
async def api_model_gateway_permissions_delete(model_pattern: str, request: Request) -> dict[str, Any]:
    """删除权限规则。"""
    require_admin(request)
    try:
        return {"status": "ok", **model_service.remove_gateway_permission(model_pattern)}
    except KeyError as exc:
        raise HTTPException(404, str(exc)) from exc


@router.put("/api/model-gateway/permissions/{model_pattern:path}")
@handle_api_errors("Model gateway permissions update", error_value={"status": "error", "error": "Update failed"})
async def api_model_gateway_permissions_update(
    model_pattern: str, body: ModelPermission, request: Request
) -> dict[str, Any]:
    """更新权限规则。

    2026-09-23 新增：前端 AgentGateway 的权限编辑此前调用本端点，但后端只有
    POST/DELETE —— 请求落到 SPA 兜底返回 200 + HTML，前端报
    "Unexpected token '<'"，编辑功能不可用。

    路径参数 ``model_pattern`` 用于定位原规则；body 中的 ``model_pattern``
    可以与它不同（改名场景，服务层会先删旧规则）。
    """
    require_admin(request)
    if not body.model_pattern:
        raise HTTPException(400, "missing model_pattern")
    return {"status": "ok", **model_service.update_gateway_permission(model_pattern, body)}


# ── API 端点：访问检查 ──────────────────────────────────────────────
@router.post("/api/model-gateway/check")
@handle_api_errors("Model gateway check", error_value={"status": "error", "error": "Check failed"})
async def api_model_gateway_check(body: ModelCheckRequest, request: Request) -> dict[str, Any]:
    """检查模型访问权限。"""
    require_admin(request)
    if not body.model:
        raise HTTPException(400, "missing model")
    return {"status": "ok", **model_service.check_model_access(body.model, body.agent, body.session_id)}


# ── API 端点：使用量 ────────────────────────────────────────────────
@router.post("/api/model-gateway/usage")
@handle_api_errors("Model gateway usage record", error_value={"status": "error", "error": "Record failed"})
async def api_model_gateway_usage_record(body: ModelUsageRequest, request: Request) -> dict[str, Any]:
    """记录模型使用量。"""
    require_admin(request)
    if not body.model:
        raise HTTPException(400, "missing model")
    return {"status": "ok", **model_service.record_model_usage(body.model, body.tokens, body.agent, body.session_id)}


@router.get("/api/model-gateway/usage")
@handle_api_errors("Model gateway usage get", error_value={"status": "error", "error": "Get failed"})
async def api_model_gateway_usage_get(
    request: Request,
    model: str = Query("", description="按模型名过滤"),
) -> dict[str, Any]:
    """获取今日使用量。"""
    require_admin(request)
    return {"status": "ok", **model_service.get_model_daily_usage(model)}


@router.delete("/api/model-gateway/usage")
@handle_api_errors("Model gateway usage clear", error_value={"status": "error", "error": "Clear failed"})
async def api_model_gateway_usage_clear(
    request: Request,
    model: str = Query("", description="只清该模型；留空清今日全部"),
) -> dict[str, Any]:
    """清空今日使用量。

    2026-09-23 新增：前端 AgentGateway 的「清空今日用量」此前调用本端点，
    但后端只有 POST/GET —— 请求落到 SPA 兜底返回 200 + HTML，
    前端报 "Unexpected token '<'"。

    同时清内存缓存与 SQLite（用量是双写的），否则进程重启会读回旧数据。
    """
    require_admin(request)
    return {"status": "ok", **model_service.clear_model_daily_usage(model)}


# ── API 端点：配置更新 ──────────────────────────────────────────────
@router.put("/api/model-gateway/config")
@handle_api_errors("Model gateway config update", error_value={"status": "error", "error": "Update failed"})
async def api_model_gateway_config_update(body: ModelGatewayConfig, request: Request) -> dict[str, Any]:
    """更新网关配置。"""
    require_admin(request)
    model_service.update_gateway_config(body)
    return {"status": "ok"}
