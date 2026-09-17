"""MAOP Dashboard — Protocol registry API endpoints.

Router 层只保留：路由定义、请求解析（Pydantic）、权限检查
（require_admin）、调用 service、响应格式化、错误处理。业务逻辑
在 ``maop.dashboard.services.routing_service`` 中，框架无关。
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field

from maop.core.security.middleware import require_admin
from maop.dashboard.error_handler import handle_api_errors
from maop.dashboard.services import routing_service

logger = logging.getLogger(__name__)

router = APIRouter()


# ── Pydantic 请求模型 ──────────────────────────────────────────────
class ProtocolRegisterRequest(BaseModel):
    """注册协议的请求体。"""
    name: str = Field(default="", max_length=256)
    version: str = Field(default="1.0", max_length=64)
    schema: dict[str, Any] = Field(default_factory=dict)
    participants: list[str] = Field(default_factory=list)
    description: str = Field(default="", max_length=10000)


class ProtocolUnregisterRequest(BaseModel):
    """注销协议的请求体。"""
    name: str = Field(default="", max_length=256)
    version: str = Field(default="1.0", max_length=64)


class ProtocolValidateRequest(BaseModel):
    """验证协议消息的请求体。"""
    protocol: str = Field(default="", max_length=256)
    version: str = Field(default="1.0", max_length=64)
    payload: dict[str, Any] = Field(default_factory=dict)


class ProtocolSendRequest(BaseModel):
    """发送协议消息的请求体。"""
    protocol: str = Field(default="", max_length=256)
    sender: str = Field(default="", max_length=256)
    recipient: str = Field(default="", max_length=256)
    payload: dict[str, Any] = Field(default_factory=dict)
    version: str = Field(default="1.0", max_length=64)


@router.post("/api/protocol/register")
@handle_api_errors("Protocol register", error_value={"status": "error", "error": "Register failed"})
async def api_protocol_register(body: ProtocolRegisterRequest, request: Request) -> dict[str, Any]:
    require_admin(request)
    if not body.name:
        raise HTTPException(400, "missing name")
    return {"status": "ok", **routing_service.register_protocol(
        body.name, body.version, body.schema, body.participants, body.description,
    )}


@router.post("/api/protocol/unregister")
@handle_api_errors("Protocol unregister", error_value={"status": "error", "error": "Unregister failed"})
async def api_protocol_unregister(body: ProtocolUnregisterRequest, request: Request) -> dict[str, Any]:
    require_admin(request)
    if not body.name:
        raise HTTPException(400, "missing name")
    try:
        return {"status": "ok", **routing_service.unregister_protocol(body.name, body.version)}
    except KeyError as exc:
        # P2 fix: 404 应返回 404 状态码，而非 200。
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/api/protocol/get")
@handle_api_errors("Protocol get", error_value={"status": "error", "error": "Get failed"})
async def api_protocol_get(request: Request, name: str = "", version: str = "1.0") -> dict[str, Any]:
    require_admin(request)
    if not name:
        raise HTTPException(400, "missing name")
    try:
        return {"status": "ok", **routing_service.get_protocol(name, version)}
    except KeyError as exc:
        raise HTTPException(404, str(exc)) from exc


@router.get("/api/protocol/list")
@handle_api_errors("Protocol list", error_value={"protocols": [], "count": 0, "error": "List failed"})
async def api_protocol_list(request: Request) -> dict[str, Any]:
    require_admin(request)
    return {"status": "ok", **routing_service.list_protocols()}


@router.get("/api/protocol/versions")
@handle_api_errors("Protocol versions", error_value={"versions": [], "error": "Versions failed"})
async def api_protocol_versions(request: Request, name: str = "") -> dict[str, Any]:
    require_admin(request)
    if not name:
        raise HTTPException(400, "missing name")
    return {"status": "ok", **routing_service.list_protocol_versions(name)}


@router.post("/api/protocol/validate")
@handle_api_errors("Protocol validate", error_value={"valid": False, "error": "Validate failed"})
async def api_protocol_validate(body: ProtocolValidateRequest, request: Request) -> dict[str, Any]:
    require_admin(request)
    if not body.protocol:
        raise HTTPException(400, "missing protocol")
    return {"status": "ok", **routing_service.validate_protocol(body.protocol, body.payload, body.version)}


@router.post("/api/protocol/send")
@handle_api_errors("Protocol send", error_value={"status": "error", "error": "Send failed"})
async def api_protocol_send(body: ProtocolSendRequest, request: Request) -> dict[str, Any]:
    require_admin(request)
    if not body.protocol or not body.sender or not body.recipient:
        raise HTTPException(400, "missing protocol, sender, or recipient")
    return {"status": "ok", **routing_service.send_protocol_message(
        body.protocol, body.sender, body.recipient, body.payload, body.version,
    )}


@router.get("/api/protocol/messages")
@handle_api_errors("Protocol messages", error_value={"messages": [], "count": 0, "error": "Messages failed"})
async def api_protocol_messages(request: Request, recipient: str = "", protocol: str = "", limit: int = Query(100, ge=1, le=1000)) -> dict[str, Any]:
    require_admin(request)
    if not recipient:
        raise HTTPException(400, "missing recipient")
    return {"status": "ok", **routing_service.get_protocol_messages(recipient, protocol, limit)}
