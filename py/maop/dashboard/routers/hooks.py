"""MAOP Dashboard — Hook 可视化配置 CRUD API（任务199）.

提供与 ``routers/hook.py`` 互补的 RESTful 风格端点，使用复数路径
``/api/hooks``，便于前端 Settings.vue 的 Hook 管理 Tab 进行可视化配置：

  - ``GET    /api/hooks``          列出全部 hook
  - ``POST   /api/hooks``          创建 hook（webhook 类型）
  - ``GET    /api/hooks/events``   列出可用事件类型（必须在 {hook_id} 之前注册）
  - ``GET    /api/hooks/{hook_id}``         获取单个 hook
  - ``PUT    /api/hooks/{hook_id}``         更新 hook
  - ``DELETE /api/hooks/{hook_id}``         删除 hook
  - ``POST   /api/hooks/{hook_id}/test``    触发一次测试事件
  - ``POST   /api/hooks/{hook_id}/enable``  启用
  - ``POST   /api/hooks/{hook_id}/disable`` 禁用

设计要点：
  - 复用 ``HookManager`` 的 SQLite 持久化，不在 config/ 下另存 JSON，
    避免双源真相。
  - Pydantic schema 严格校验请求体；错误返回 400/404。
  - ``require_admin`` 保护写操作；读操作放开以便非管理员查看。
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from maop.core.agent.plugins_hooks.hook_manager import LifecycleEvent
from maop.core.security.middleware import require_admin

from .error_handler import handle_api_errors
from .state import MAOP_ROOT

logger = logging.getLogger(__name__)

router = APIRouter()

# ── HookManager 单例（按需懒加载，便于测试 monkeypatch）──────────────
_hook_mgr: Any = None


def _get_hook_mgr() -> Any:
    """返回 HookManager 单例。

    测试可通过 ``monkeypatch.setattr("maop.dashboard.routers.hooks._get_hook_mgr", ...)``
    替换为 mock；也可直接覆写模块级 ``_hook_mgr``。
    """
    global _hook_mgr
    if _hook_mgr is None:
        from maop.core.agent.plugins_hooks.hook_manager import HookManager
        _hook_mgr = HookManager(root_dir=str(MAOP_ROOT))
    return _hook_mgr


# ── Pydantic 请求/响应 schema ────────────────────────────────────────
class HookCreateRequest(BaseModel):
    """创建 Hook 请求体。"""
    name: str = Field(..., min_length=1, max_length=128, description="Hook 名称")
    event: str = Field(..., min_length=1, description="事件类型，需为 LifecycleEvent 之一")
    url: str = Field(..., min_length=1, description="Webhook 接收 URL")
    method: str = Field("POST", description="HTTP 方法，当前仅支持 POST")
    headers: dict[str, str] = Field(default_factory=dict, description="自定义请求头")
    enabled: bool = Field(True, description="是否启用")
    timeout: int = Field(10, ge=1, le=300, description="请求超时秒数")
    retry_count: int = Field(0, ge=0, le=10, description="失败重试次数")


class HookUpdateRequest(BaseModel):
    """更新 Hook 请求体，所有字段可选。"""
    name: str | None = Field(None, min_length=1, max_length=128)
    event: str | None = Field(None, min_length=1)
    url: str | None = Field(None, min_length=1)
    method: str | None = Field(None)
    headers: dict[str, str] | None = None
    enabled: bool | None = None
    timeout: int | None = Field(None, ge=1, le=300)
    retry_count: int | None = Field(None, ge=0, le=10)


class HookResponse(BaseModel):
    """单个 Hook 响应。"""
    id: str
    name: str
    event: str
    url: str
    method: str = "POST"
    headers: dict[str, str] = Field(default_factory=dict)
    enabled: bool = True
    timeout: int = 10
    retry_count: int = 0
    priority: int = 0
    description: str = ""
    created_at: str = ""
    source: str = "api"


class HookListResponse(BaseModel):
    """Hook 列表响应。"""
    hooks: list[HookResponse]
    count: int


class EventTypeInfo(BaseModel):
    """事件类型信息。"""
    name: str
    phase: str
    domain: str


class EventsListResponse(BaseModel):
    """可用事件类型列表。"""
    events: list[EventTypeInfo]
    count: int


class HookTestResponse(BaseModel):
    """Hook 测试触发结果。"""
    hook_id: str
    success: bool
    response: str = ""
    error: str = ""
    duration_ms: int = 0


# ── 辅助函数 ──────────────────────────────────────────────────────────
_VALID_EVENTS: set[str] = {e.value for e in LifecycleEvent}


def _is_valid_event(event: str) -> bool:
    """判断事件是否合法：精确匹配或 ``<domain>.*`` 通配。"""
    if event in _VALID_EVENTS:
        return True
    # 允许通配符，如 "agent.*"
    if event.endswith(".*"):
        prefix = event[:-2]
        return any(e.startswith(prefix + ".") for e in _VALID_EVENTS)
    return False


def _hook_def_to_response(hook_def: Any, name: str = "") -> HookResponse:
    """将 HookManager.HookDef 转为 HookResponse。

    ``HookDef`` 没有 name/headers/timeout/retry_count 字段，
    这里从 description 中解析（兼容旧数据），缺失则用默认值。
    """
    # description 字段复用为 name（持久化层无独立 name 列）
    display_name = name or hook_def.description or hook_def.id
    return HookResponse(
        id=hook_def.id,
        name=display_name,
        event=hook_def.event,
        url=hook_def.url,
        method="POST",
        headers={},
        enabled=hook_def.enabled,
        timeout=10,
        retry_count=0,
        priority=hook_def.priority,
        description=hook_def.description,
        created_at=hook_def.created_at,
        source=hook_def.source,
    )


# ── 路由：列出/创建 ──────────────────────────────────────────────────
@router.get("/api/hooks", response_model=HookListResponse)
@handle_api_errors("Hooks list", error_value=HookListResponse(hooks=[], count=0))
async def api_hooks_list(event: str = "") -> HookListResponse:
    """列出全部 hook，可选按事件过滤。"""
    mgr = _get_hook_mgr()
    hooks = mgr.list_hooks(event=event or "")
    return HookListResponse(
        hooks=[_hook_def_to_response(h) for h in hooks],
        count=len(hooks),
    )


@router.post("/api/hooks", response_model=HookResponse)
@handle_api_errors("Hook create", error_value=HookResponse(id="", name="", event="", url=""))
async def api_hooks_create(request: Request) -> HookResponse:
    """创建新 hook（webhook 类型）。"""
    require_admin(request)
    body = HookCreateRequest(**(await request.json()))

    if not _is_valid_event(body.event):
        raise HTTPException(400, f"Invalid event type: {body.event}")
    if body.method.upper() != "POST":
        raise HTTPException(400, f"Unsupported method: {body.method} (only POST supported)")

    mgr = _get_hook_mgr()
    hdef = mgr.register(
        event=body.event,
        url=body.url,
        priority=0,
        description=body.name,
        source="api",
    )
    logger.info("[hooks] Created hook '%s' for event '%s'", hdef.id, body.event)
    return _hook_def_to_response(hdef, name=body.name)


# ── 路由：事件类型（必须在 {hook_id} 之前注册，避免路径被吞）─────────
@router.get("/api/hooks/events", response_model=EventsListResponse)
@handle_api_errors("Hooks events", error_value=EventsListResponse(events=[], count=0))
async def api_hooks_events() -> EventsListResponse:
    """列出所有可用的 lifecycle 事件类型。"""
    events = [
        EventTypeInfo(
            name=e.value,
            phase=e.value.split(".")[-1],
            domain=e.value.split(".")[0],
        )
        for e in LifecycleEvent
    ]
    return EventsListResponse(events=events, count=len(events))


# ── 路由：单个 hook CRUD ─────────────────────────────────────────────
@router.get("/api/hooks/{hook_id}", response_model=HookResponse)
@handle_api_errors("Hook get", error_value=HookResponse(id="", name="", event="", url=""))
async def api_hooks_get(hook_id: str) -> HookResponse:
    """获取单个 hook 详情。"""
    if not hook_id:
        raise HTTPException(400, "missing hook_id")
    mgr = _get_hook_mgr()
    hdef = mgr.get_hook(hook_id)
    if hdef is None:
        raise HTTPException(404, f"Hook {hook_id} not found")
    return _hook_def_to_response(hdef)


@router.put("/api/hooks/{hook_id}", response_model=HookResponse)
@handle_api_errors("Hook update", error_value=HookResponse(id="", name="", event="", url=""))
async def api_hooks_update(hook_id: str, request: Request) -> HookResponse:
    """更新 hook 配置。

    实现策略：先删除旧 hook，再用新参数注册。这样可绕过
    HookManager 未提供 update 方法的限制，并保持持久化层一致。
    """
    require_admin(request)
    body = HookUpdateRequest(**(await request.json()))

    mgr = _get_hook_mgr()
    existing = mgr.get_hook(hook_id)
    if existing is None:
        raise HTTPException(404, f"Hook {hook_id} not found")

    # 合并新旧字段
    new_event = body.event if body.event is not None else existing.event
    new_url = body.url if body.url is not None else existing.url
    new_name = body.name if body.name is not None else (existing.description or hook_id)
    new_enabled = body.enabled if body.enabled is not None else existing.enabled

    if not _is_valid_event(new_event):
        raise HTTPException(400, f"Invalid event type: {new_event}")
    if not new_url:
        raise HTTPException(400, "url must not be empty")

    # 删除旧 hook 并注册新 hook（保留原 id）
    mgr.unregister(hook_id)
    hdef = mgr.register(
        event=new_event,
        url=new_url,
        priority=existing.priority,
        description=new_name,
        source="api",
        hook_id=hook_id,
    )
    if not new_enabled:
        mgr.disable(hook_id)
    logger.info("[hooks] Updated hook '%s'", hook_id)
    return _hook_def_to_response(hdef, name=new_name)


@router.delete("/api/hooks/{hook_id}")
@handle_api_errors("Hook delete", error_value={"status": "error", "removed": False})
async def api_hooks_delete(hook_id: str, request: Request) -> dict[str, Any]:
    """删除 hook。"""
    require_admin(request)
    if not hook_id:
        raise HTTPException(400, "missing hook_id")
    mgr = _get_hook_mgr()
    existing = mgr.get_hook(hook_id)
    if existing is None:
        raise HTTPException(404, f"Hook {hook_id} not found")
    removed = mgr.unregister(hook_id)
    return {"status": "ok" if removed else "error", "removed": removed}


# ── 路由：测试/启用/禁用 ────────────────────────────────────────────
@router.post("/api/hooks/{hook_id}/test", response_model=HookTestResponse)
@handle_api_errors("Hook test", error_value=HookTestResponse(hook_id="", success=False, error="Test failed"))
async def api_hooks_test(hook_id: str, request: Request) -> HookTestResponse:
    """触发一次测试事件，向 hook URL 发送 POST 请求。"""
    require_admin(request)
    mgr = _get_hook_mgr()
    hdef = mgr.get_hook(hook_id)
    if hdef is None:
        raise HTTPException(404, f"Hook {hook_id} not found")

    # 临时启用 hook 以便触发（不修改持久化状态）
    was_enabled = hdef.enabled
    if not was_enabled:
        mgr.enable(hook_id)
    try:
        results = await mgr.trigger(hdef.event, {"test": True, "hook_id": hook_id})
    finally:
        if not was_enabled:
            mgr.disable(hook_id)

    if not results:
        return HookTestResponse(hook_id=hook_id, success=True, response="no listeners")
    r = results[0]
    return HookTestResponse(
        hook_id=hook_id,
        success=r.success,
        response=r.response,
        error=r.error,
        duration_ms=r.duration_ms,
    )


@router.post("/api/hooks/{hook_id}/enable")
@handle_api_errors("Hook enable", error_value={"status": "error"})
async def api_hooks_enable(hook_id: str, request: Request) -> dict[str, Any]:
    """启用 hook。"""
    require_admin(request)
    mgr = _get_hook_mgr()
    if mgr.get_hook(hook_id) is None:
        raise HTTPException(404, f"Hook {hook_id} not found")
    ok = mgr.enable(hook_id)
    return {"status": "ok" if ok else "error"}


@router.post("/api/hooks/{hook_id}/disable")
@handle_api_errors("Hook disable", error_value={"status": "error"})
async def api_hooks_disable(hook_id: str, request: Request) -> dict[str, Any]:
    """禁用 hook。"""
    require_admin(request)
    mgr = _get_hook_mgr()
    if mgr.get_hook(hook_id) is None:
        raise HTTPException(404, f"Hook {hook_id} not found")
    ok = mgr.disable(hook_id)
    return {"status": "ok" if ok else "error"}