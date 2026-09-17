"""MAOP Dashboard — Hook management API endpoints.

业务逻辑已提取至 ``maop.dashboard.services.execution_service``（§3）。
本 router 仅保留：路由定义 / 请求参数解析 / 权限检查 /
service 调用 / 响应格式化 / 错误处理。
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field

from maop.core.security.middleware import require_admin
from maop.dashboard.error_handler import handle_api_errors
from maop.dashboard.services import execution_service

from .state import MAOP_ROOT

logger = logging.getLogger(__name__)

router = APIRouter()


# ── Pydantic 请求模型 ──────────────────────────────────────────────
class HookRegisterRequest(BaseModel):
    """注册 Hook 的请求体。"""
    event: str = Field(default="", max_length=256)
    url: str = Field(default="", max_length=2048)
    callback: str = Field(default="", max_length=256)
    priority: int = Field(default=0, ge=-1000, le=1000)
    description: str = Field(default="", max_length=10000)


class HookIdRequest(BaseModel):
    """按 ID 操作 Hook 的请求体。"""
    id: str = Field(default="", max_length=128)


class HookTriggerRequest(BaseModel):
    """触发 Hook 的请求体。"""
    event: str = Field(default="", max_length=256)
    data: dict[str, Any] = Field(default_factory=dict)


# ── 单例转发（供测试注入，转发到 service 层）──────────────────────
def _get_hook_mgr() -> Any:
    return execution_service._get_hook_mgr(maop_root=MAOP_ROOT)


@router.post("/api/hook/register")
@handle_api_errors("Hook register", error_value={"status": "error", "error": "Register failed"})
async def api_hook_register(body: HookRegisterRequest, request: Request) -> dict[str, Any]:
    require_admin(request)
    event = body.event
    url = body.url
    callback = body.callback
    priority = body.priority
    description = body.description
    if not event:
        raise HTTPException(400, "missing event")
    mgr = _get_hook_mgr()
    try:
        hdef = execution_service.hook_register(
            mgr, event=event, url=url, callback=callback,
            priority=priority, description=description,
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    return {"status": "ok", "hook": hdef.model_dump()}


@router.post("/api/hook/unregister")
@handle_api_errors("Hook unregister", error_value={"status": "error", "error": "Unregister failed"})
async def api_hook_unregister(body: HookIdRequest, request: Request) -> dict[str, Any]:
    require_admin(request)
    hook_id = body.id
    if not hook_id:
        raise HTTPException(400, "missing id")
    mgr = _get_hook_mgr()
    removed = execution_service.hook_unregister(mgr, hook_id)
    if not removed:
        # H-1 fix: 资源未找到应返回 404，而非 200 + status=not_found。
        raise HTTPException(status_code=404, detail="Hook not found")
    return {"status": "ok", "removed": removed}


@router.post("/api/hook/enable")
@handle_api_errors("Hook enable", error_value={"status": "error", "error": "Enable failed"})
async def api_hook_enable(body: HookIdRequest, request: Request) -> dict[str, Any]:
    require_admin(request)
    hook_id = body.id
    if not hook_id:
        raise HTTPException(400, "missing id")
    mgr = _get_hook_mgr()
    result = execution_service.hook_enable(mgr, hook_id)
    if not result:
        # H-1 fix: 资源未找到应返回 404，而非 200 + status=not_found。
        raise HTTPException(status_code=404, detail="Hook not found")
    return {"status": "ok"}


@router.post("/api/hook/disable")
@handle_api_errors("Hook disable", error_value={"status": "error", "error": "Disable failed"})
async def api_hook_disable(body: HookIdRequest, request: Request) -> dict[str, Any]:
    require_admin(request)
    hook_id = body.id
    if not hook_id:
        raise HTTPException(400, "missing id")
    mgr = _get_hook_mgr()
    result = execution_service.hook_disable(mgr, hook_id)
    if not result:
        # H-1 fix: 资源未找到应返回 404，而非 200 + status=not_found。
        raise HTTPException(status_code=404, detail="Hook not found")
    return {"status": "ok"}


@router.get("/api/hook/list")
@handle_api_errors("Hook list", error_value={"hooks": [], "count": 0, "error": "List failed"})
async def api_hook_list(request: Request, event: str = "") -> dict[str, Any]:
    require_admin(request)
    mgr = _get_hook_mgr()
    hooks = execution_service.hook_list(mgr, event)
    return {"status": "ok", "hooks": [h.model_dump() for h in hooks], "count": len(hooks)}


@router.get("/api/hook/get")
@handle_api_errors("Hook get", error_value={"status": "error", "error": "Get failed"})
async def api_hook_get(request: Request, hook_id: str = "") -> dict[str, Any]:
    require_admin(request)
    if not hook_id:
        raise HTTPException(400, "missing hook_id")
    mgr = _get_hook_mgr()
    hdef = execution_service.hook_get(mgr, hook_id)
    if hdef is None:
        raise HTTPException(404, f"Hook {hook_id} not found")
    return {"status": "ok", "hook": hdef.model_dump()}


@router.post("/api/hook/trigger")
@handle_api_errors("Hook trigger", error_value={"status": "error", "error": "Trigger failed"})
async def api_hook_trigger(body: HookTriggerRequest, request: Request) -> dict[str, Any]:
    require_admin(request)
    event = body.event
    data = body.data
    if not event:
        raise HTTPException(400, "missing event")
    mgr = _get_hook_mgr()
    results = await execution_service.hook_trigger(mgr, event, data)
    return {"status": "ok", "results": [r.model_dump() for r in results], "count": len(results)}


@router.get("/api/hook/logs")
@handle_api_errors("Hook logs", error_value={"logs": [], "error": "Logs failed"})
async def api_hook_logs(request: Request, event: str = "", limit: int = Query(100, ge=1, le=1000)) -> dict[str, Any]:
    require_admin(request)
    mgr = _get_hook_mgr()
    logs = execution_service.hook_logs(mgr, event, limit)
    return {"status": "ok", "logs": logs, "count": len(logs)}


@router.get("/api/hook/events")
@handle_api_errors("Hook events", error_value={"events": [], "error": "Events failed"})
async def api_hook_events(request: Request) -> dict[str, Any]:
    require_admin(request)
    events = execution_service.hook_events()
    return {"status": "ok", "events": events, "count": len(events)}
