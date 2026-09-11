"""MAOP Dashboard — Agent Bridge API endpoints."""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from maop.core.security.middleware import require_admin
from maop.dashboard.error_handler import handle_api_errors

from .state import MAOP_ROOT

logger = logging.getLogger(__name__)

router = APIRouter()


# ── Pydantic 请求模型 (H-3 fix: 输入校验) ──────────────────────────
class BridgeCallRequest(BaseModel):
    """POST /api/bridge/call 请求体。"""
    adapter: str = Field(default="", max_length=256)
    task: str = Field(default="", max_length=10000)
    kwargs: dict[str, Any] = Field(default_factory=dict)


class BridgeSyncConfigRequest(BaseModel):
    """POST /api/bridge/sync-config 请求体。"""
    adapter: str = Field(default="", max_length=256)
    config: dict[str, Any] = Field(default_factory=dict)

_agent_proxy = None

def _get_bridge() -> Any:
    global _agent_proxy
    if _agent_proxy is None:
        from maop.core.agent.delegation.agent_proxy import AgentProxy
        _agent_proxy = AgentProxy(root_dir=str(MAOP_ROOT))
    return _agent_proxy


@router.get("/api/bridge/adapters")
@handle_api_errors("Bridge adapters", error_value={"adapters": [], "count": 0, "error": "List failed"})
async def api_bridge_adapters(request: Request) -> dict[str, Any]:
    """List available bridge adapters."""
    require_admin(request)
    bridge = _get_bridge()
    names = bridge.list_adapters()
    statuses = []
    for name in names:
        try:
            s = bridge.get_status(name)
            statuses.append(s.model_dump())
        except Exception:
            statuses.append({"name": name, "error": "status unavailable"})
    return {"status": "ok", "adapters": statuses, "count": len(statuses)}


@router.post("/api/bridge/call")
@handle_api_errors("Bridge call", error_value={"status": "error", "error": "Call failed"})
async def api_bridge_call(body: BridgeCallRequest, request: Request) -> dict[str, Any]:
    """Proxy a call through a bridge adapter."""
    require_admin(request)
    # H-3 fix: 用 Pydantic BridgeCallRequest 替代 await request.json()，
    # 由 FastAPI 自动校验请求体（adapter/task/kwargs 字段类型）。
    adapter_name = body.adapter
    task = body.task
    if not adapter_name or not task:
        raise HTTPException(400, "missing adapter or task")
    bridge = _get_bridge()
    try:
        result = bridge.call(adapter_name, task, **body.kwargs)
        return {"status": "ok", "result": result}
    except KeyError as exc:
        # 批次3A: 脱敏——KeyError 细节不暴露给客户端，仅日志记录。
        logger.warning("[bridge] call adapter not found: %s", exc)
        raise HTTPException(404, "Bridge adapter not found")
    except RuntimeError as exc:
        # 批次3A: 脱敏——RuntimeError 细节不暴露给客户端，仅日志记录。
        logger.warning("[bridge] call runtime error: %s", exc)
        raise HTTPException(500, "Bridge call failed")


@router.get("/api/bridge/health")
@handle_api_errors("Bridge health", error_value={"health": {}, "error": "Health check failed"})
async def api_bridge_health(request: Request) -> dict[str, Any]:
    """Check bridge adapter health status."""
    require_admin(request)
    bridge = _get_bridge()
    health = bridge.health_check_all()
    return {"status": "ok", "health": health}


@router.post("/api/bridge/sync-config")
@handle_api_errors("Bridge sync-config", error_value={"status": "error", "error": "Sync failed"})
async def api_bridge_sync_config(body: BridgeSyncConfigRequest, request: Request) -> dict[str, Any]:
    """Sync bridge adapter configuration."""
    require_admin(request)
    # H-3 fix: 用 Pydantic BridgeSyncConfigRequest 替代 await request.json()，
    # 由 FastAPI 自动校验请求体（adapter/config 字段类型）。
    adapter_name = body.adapter
    config = body.config
    if not adapter_name:
        raise HTTPException(400, "missing adapter")
    bridge = _get_bridge()
    try:
        bridge.sync_config(adapter_name, config)
        return {"status": "ok"}
    except KeyError as exc:
        # 批次3A: 脱敏——KeyError 细节不暴露给客户端，仅日志记录。
        logger.warning("[bridge] sync-config adapter not found: %s", exc)
        raise HTTPException(404, "Bridge adapter not found")
