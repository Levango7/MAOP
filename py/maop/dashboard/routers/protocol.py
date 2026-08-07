"""MAOP Dashboard — Protocol registry API endpoints."""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Request

from maop.core.security.middleware import require_admin

from .error_handler import handle_api_errors
from .state import MAOP_ROOT

logger = logging.getLogger(__name__)

router = APIRouter()

_protocol_reg = None

def _get_protocol_reg() -> Any:
    global _protocol_reg
    if _protocol_reg is None:
        from maop.core.agent.plugins_hooks.protocol import ProtocolRegistry
        _protocol_reg = ProtocolRegistry(root_dir=str(MAOP_ROOT))
    return _protocol_reg


@router.post("/api/protocol/register")
@handle_api_errors("Protocol register", error_value={"status": "error", "error": "Register failed"})
async def api_protocol_register(request: Request) -> dict[str, Any]:
    require_admin(request)
    body = await request.json()
    name = body.get("name", "")
    version = body.get("version", "1.0")
    schema_def = body.get("schema", {})
    participants = body.get("participants", [])
    description = body.get("description", "")
    if not name:
        raise HTTPException(400, "missing name")
    reg = _get_protocol_reg()
    proto = reg.register(name=name, version=version, schema_def=schema_def,
                         participants=participants, description=description)
    return {"status": "ok", "protocol": proto.model_dump()}


@router.post("/api/protocol/unregister")
@handle_api_errors("Protocol unregister", error_value={"status": "error", "error": "Unregister failed"})
async def api_protocol_unregister(request: Request) -> dict[str, Any]:
    require_admin(request)
    body = await request.json()
    name = body.get("name", "")
    version = body.get("version", "1.0")
    if not name:
        raise HTTPException(400, "missing name")
    reg = _get_protocol_reg()
    removed = reg.unregister(name, version)
    return {"status": "ok" if removed else "not_found", "removed": removed}


@router.get("/api/protocol/get")
@handle_api_errors("Protocol get", error_value={"status": "error", "error": "Get failed"})
async def api_protocol_get(name: str = "", version: str = "1.0") -> dict[str, Any]:
    if not name:
        raise HTTPException(400, "missing name")
    reg = _get_protocol_reg()
    proto = reg.get(name, version)
    if proto is None:
        raise HTTPException(404, f"Protocol {name} v{version} not found")
    return {"status": "ok", "protocol": proto.model_dump()}


@router.get("/api/protocol/list")
@handle_api_errors("Protocol list", error_value={"protocols": [], "count": 0, "error": "List failed"})
async def api_protocol_list() -> dict[str, Any]:
    reg = _get_protocol_reg()
    protocols = reg.list_protocols()
    return {"protocols": [p.model_dump() for p in protocols], "count": len(protocols)}


@router.get("/api/protocol/versions")
@handle_api_errors("Protocol versions", error_value={"versions": [], "error": "Versions failed"})
async def api_protocol_versions(name: str = "") -> dict[str, Any]:
    if not name:
        raise HTTPException(400, "missing name")
    reg = _get_protocol_reg()
    versions = reg.list_versions(name)
    return {"name": name, "versions": versions}


@router.post("/api/protocol/validate")
@handle_api_errors("Protocol validate", error_value={"valid": False, "error": "Validate failed"})
async def api_protocol_validate(request: Request) -> dict[str, Any]:
    require_admin(request)
    body = await request.json()
    protocol_name = body.get("protocol", "")
    version = body.get("version", "1.0")
    payload = body.get("payload", {})
    if not protocol_name:
        raise HTTPException(400, "missing protocol")
    reg = _get_protocol_reg()
    valid = reg.validate(protocol_name, payload, version)
    return {"valid": valid, "protocol": protocol_name, "version": version}


@router.post("/api/protocol/send")
@handle_api_errors("Protocol send", error_value={"status": "error", "error": "Send failed"})
async def api_protocol_send(request: Request) -> dict[str, Any]:
    require_admin(request)
    body = await request.json()
    protocol = body.get("protocol", "")
    sender = body.get("sender", "")
    recipient = body.get("recipient", "")
    payload = body.get("payload", {})
    version = body.get("version", "1.0")
    if not protocol or not sender or not recipient:
        raise HTTPException(400, "missing protocol, sender, or recipient")
    reg = _get_protocol_reg()
    msg = reg.send_message(protocol=protocol, sender=sender, recipient=recipient,
                           payload=payload, version=version)
    return {"status": "ok", "message": msg.model_dump()}


@router.get("/api/protocol/messages")
@handle_api_errors("Protocol messages", error_value={"messages": [], "count": 0, "error": "Messages failed"})
async def api_protocol_messages(recipient: str = "", protocol: str = "", limit: int = 100) -> dict[str, Any]:
    if not recipient:
        raise HTTPException(400, "missing recipient")
    reg = _get_protocol_reg()
    messages = reg.get_messages(recipient, protocol=protocol or None, limit=limit)
    return {"messages": [m.model_dump() for m in messages], "count": len(messages)}
