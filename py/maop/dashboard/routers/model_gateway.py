"""MAOP Dashboard — 模型授权网关 API 端点.

桥接 ``ModelGateway`` 模型授权网关到前端，提供权限规则管理/访问检查/
使用量记录/配置更新 7 个端点。所有端点需 admin 鉴权。
"""

from __future__ import annotations

import logging
import threading
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from maop.core.agent.llm_chat.model_gateway import (
    ModelGateway,
    ModelGatewayConfig,
    ModelPermission,
)
from maop.core.security.middleware import require_admin
from maop.dashboard.error_handler import handle_api_errors

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


# ── ModelGateway 单例（双重检查锁定）────────────────────────────────
_model_gateway: ModelGateway | None = None
_model_gateway_lock = threading.Lock()


def _get_model_gateway() -> ModelGateway:
    """获取 ModelGateway 单例。"""
    global _model_gateway
    if _model_gateway is None:
        with _model_gateway_lock:
            if _model_gateway is None:
                _model_gateway = ModelGateway()
    return _model_gateway


# ── API 端点：权限规则管理 ──────────────────────────────────────────
@router.get("/api/model-gateway/permissions")
@handle_api_errors("Model gateway permissions list", error_value={"status": "error", "error": "List failed"})
async def api_model_gateway_permissions_list(request: Request) -> dict[str, Any]:
    """列出所有权限规则。"""
    require_admin(request)
    gateway = _get_model_gateway()
    perms = gateway.list_permissions()
    return {
        "status": "ok",
        "permissions": [p.model_dump(mode="json") for p in perms],
        "count": len(perms),
    }


@router.post("/api/model-gateway/permissions")
@handle_api_errors("Model gateway permissions add", error_value={"status": "error", "error": "Add failed"})
async def api_model_gateway_permissions_add(body: ModelPermission, request: Request) -> dict[str, Any]:
    """添加权限规则。"""
    require_admin(request)
    if not body.model_pattern:
        raise HTTPException(400, "missing model_pattern")
    gateway = _get_model_gateway()
    gateway.add_permission(body)
    return {"status": "ok", "model_pattern": body.model_pattern}


@router.delete("/api/model-gateway/permissions/{model_pattern:path}")
@handle_api_errors("Model gateway permissions delete", error_value={"status": "error", "error": "Delete failed"})
async def api_model_gateway_permissions_delete(model_pattern: str, request: Request) -> dict[str, Any]:
    """删除权限规则。"""
    require_admin(request)
    gateway = _get_model_gateway()
    removed = gateway.remove_permission(model_pattern)
    if not removed:
        raise HTTPException(404, "permission rule not found")
    return {"status": "ok", "model_pattern": model_pattern}


# ── API 端点：访问检查 ──────────────────────────────────────────────
@router.post("/api/model-gateway/check")
@handle_api_errors("Model gateway check", error_value={"status": "error", "error": "Check failed"})
async def api_model_gateway_check(body: ModelCheckRequest, request: Request) -> dict[str, Any]:
    """检查模型访问权限。"""
    require_admin(request)
    if not body.model:
        raise HTTPException(400, "missing model")
    gateway = _get_model_gateway()
    decision = gateway.check_access(body.model, agent=body.agent, session_id=body.session_id)
    return {"status": "ok", "decision": decision.model_dump(mode="json")}


# ── API 端点：使用量 ────────────────────────────────────────────────
@router.post("/api/model-gateway/usage")
@handle_api_errors("Model gateway usage record", error_value={"status": "error", "error": "Record failed"})
async def api_model_gateway_usage_record(body: ModelUsageRequest, request: Request) -> dict[str, Any]:
    """记录模型使用量。"""
    require_admin(request)
    if not body.model:
        raise HTTPException(400, "missing model")
    gateway = _get_model_gateway()
    gateway.record_usage(body.model, body.tokens, agent=body.agent, session_id=body.session_id)
    return {"status": "ok", "model": body.model, "tokens": body.tokens}


@router.get("/api/model-gateway/usage")
@handle_api_errors("Model gateway usage get", error_value={"status": "error", "error": "Get failed"})
async def api_model_gateway_usage_get(request: Request, model: str = "") -> dict[str, Any]:
    """获取今日使用量。"""
    require_admin(request)
    gateway = _get_model_gateway()
    usage = gateway.get_daily_usage(model=model)
    total = sum(usage.values())
    return {"status": "ok", "usage": usage, "total_tokens": total}


# ── API 端点：配置更新 ──────────────────────────────────────────────
@router.put("/api/model-gateway/config")
@handle_api_errors("Model gateway config update", error_value={"status": "error", "error": "Update failed"})
async def api_model_gateway_config_update(body: ModelGatewayConfig, request: Request) -> dict[str, Any]:
    """更新网关配置。"""
    require_admin(request)
    gateway = _get_model_gateway()
    gateway.update_config(body)
    return {"status": "ok"}