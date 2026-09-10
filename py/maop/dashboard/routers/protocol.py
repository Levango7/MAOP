"""MAOP Dashboard — Protocol registry API endpoints."""

from __future__ import annotations

import logging
import threading
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from maop.core.security.middleware import require_admin
from maop.dashboard.error_handler import handle_api_errors

from .state import MAOP_ROOT

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

_protocol_reg = None
_protocol_reg_lock = threading.Lock()

def _get_protocol_reg() -> Any:
    global _protocol_reg
    if _protocol_reg is None:
        with _protocol_reg_lock:
            # Double-checked locking: re-test inside the lock to avoid
            # re-initializing when another thread already did it.
            if _protocol_reg is None:
                from maop.core.agent.plugins_hooks.protocol import ProtocolRegistry
                _protocol_reg = ProtocolRegistry(root_dir=str(MAOP_ROOT))
    return _protocol_reg


@router.post("/api/protocol/register")
@handle_api_errors("Protocol register", error_value={"status": "error", "error": "Register failed"})
async def api_protocol_register(body: ProtocolRegisterRequest, request: Request) -> dict[str, Any]:
    require_admin(request)
    name = body.name
    version = body.version
    schema_def = body.schema
    participants = body.participants
    description = body.description
    if not name:
        raise HTTPException(400, "missing name")
    reg = _get_protocol_reg()
    proto = reg.register(name=name, version=version, schema_def=schema_def,
                         participants=participants, description=description)
    return {"status": "ok", "protocol": proto.model_dump()}


@router.post("/api/protocol/unregister")
@handle_api_errors("Protocol unregister", error_value={"status": "error", "error": "Unregister failed"})
async def api_protocol_unregister(body: ProtocolUnregisterRequest, request: Request) -> dict[str, Any]:
    require_admin(request)
    name = body.name
    version = body.version
    if not name:
        raise HTTPException(400, "missing name")
    reg = _get_protocol_reg()
    removed = reg.unregister(name, version)
    return {"status": "ok" if removed else "not_found", "removed": removed}


@router.get("/api/protocol/get")
@handle_api_errors("Protocol get", error_value={"status": "error", "error": "Get failed"})
async def api_protocol_get(request: Request, name: str = "", version: str = "1.0") -> dict[str, Any]:
    require_admin(request)
    if not name:
        raise HTTPException(400, "missing name")
    reg = _get_protocol_reg()
    proto = reg.get(name, version)
    if proto is None:
        raise HTTPException(404, f"Protocol {name} v{version} not found")
    return {"status": "ok", "protocol": proto.model_dump()}


@router.get("/api/protocol/list")
@handle_api_errors("Protocol list", error_value={"protocols": [], "count": 0, "error": "List failed"})
async def api_protocol_list(request: Request) -> dict[str, Any]:
    require_admin(request)
    reg = _get_protocol_reg()
    protocols = reg.list_protocols()
    return {"status": "ok", "protocols": [p.model_dump() for p in protocols], "count": len(protocols)}


@router.get("/api/protocol/versions")
@handle_api_errors("Protocol versions", error_value={"versions": [], "error": "Versions failed"})
async def api_protocol_versions(request: Request, name: str = "") -> dict[str, Any]:
    require_admin(request)
    if not name:
        raise HTTPException(400, "missing name")
    reg = _get_protocol_reg()
    versions = reg.list_versions(name)
    return {"status": "ok", "name": name, "versions": versions}


@router.post("/api/protocol/validate")
@handle_api_errors("Protocol validate", error_value={"valid": False, "error": "Validate failed"})
async def api_protocol_validate(body: ProtocolValidateRequest, request: Request) -> dict[str, Any]:
    require_admin(request)
    protocol_name = body.protocol
    version = body.version
    payload = body.payload
    if not protocol_name:
        raise HTTPException(400, "missing protocol")
    reg = _get_protocol_reg()
    valid = reg.validate(protocol_name, payload, version)
    return {"status": "ok", "valid": valid, "protocol": protocol_name, "version": version}


@router.post("/api/protocol/send")
@handle_api_errors("Protocol send", error_value={"status": "error", "error": "Send failed"})
async def api_protocol_send(body: ProtocolSendRequest, request: Request) -> dict[str, Any]:
    require_admin(request)
    protocol = body.protocol
    sender = body.sender
    recipient = body.recipient
    payload = body.payload
    version = body.version
    if not protocol or not sender or not recipient:
        raise HTTPException(400, "missing protocol, sender, or recipient")
    reg = _get_protocol_reg()
    msg = reg.send_message(protocol=protocol, sender=sender, recipient=recipient,
                           payload=payload, version=version)
    return {"status": "ok", "message": msg.model_dump()}


@router.get("/api/protocol/messages")
@handle_api_errors("Protocol messages", error_value={"messages": [], "count": 0, "error": "Messages failed"})
async def api_protocol_messages(request: Request, recipient: str = "", protocol: str = "", limit: int = 100) -> dict[str, Any]:
    require_admin(request)
    if not recipient:
        raise HTTPException(400, "missing recipient")
    reg = _get_protocol_reg()
    messages = reg.get_messages(recipient, protocol=protocol or None, limit=limit)
    return {"status": "ok", "messages": [m.model_dump() for m in messages], "count": len(messages)}
