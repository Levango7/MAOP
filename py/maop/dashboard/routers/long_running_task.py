"""长任务管理路由 — 自主 Agent 长任务的提交、查询、取消与进度流.

为 Devin / OpenHands 等自主 Agent 提供长任务的 HTTP API。所有状态
持久化到 SQLite，支持 SSE 实时进度推送。

Endpoints:
  POST   /api/long-running-tasks                        — 提交长任务
  GET    /api/long-running-tasks                        — 列出活跃任务
  GET    /api/long-running-tasks/{task_id}              — 查询任务状态
  POST   /api/long-running-tasks/{task_id}/cancel       — 取消任务
  POST   /api/long-running-tasks/{task_id}/progress     — 执行器上报进度（2026-11-08）
  POST   /api/long-running-tasks/{task_id}/complete     — 执行器上报完成（2026-11-08）
  POST   /api/long-running-tasks/{task_id}/fail         — 执行器上报失败（2026-11-08）
  GET    /api/long-running-tasks/{task_id}/progress     — SSE 进度流

数据存储: SQLite ``long_running_tasks`` 表（位于统一 maop.db）.

Business logic (manager singleton, CRUD, SSE generator) lives in
:mod:`maop.dashboard.services.session_service`; this router only does
request parsing, service dispatch, and response formatting. The manager
accessors are re-exported here for backward compatibility with tests
that monkeypatch ``lrt_router._set_manager_for_tests``.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Request, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from maop.core.security.middleware import require_admin
from maop.dashboard.error_handler import handle_api_errors
from maop.dashboard.routers.long_running_task_schemas import (
    LongRunningTaskCancelEnvelope,
    LongRunningTaskListEnvelope,
)
from maop.dashboard.services import session_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/long-running-tasks", tags=["long-running-tasks"])


# ── Pydantic 请求模型 ─────────────────────────────────────────────


class LongRunningTaskSubmit(BaseModel):
    """提交长任务请求体."""

    agent_name: str = Field(..., min_length=1, max_length=255, description="Agent 名称")
    task: str = Field(..., min_length=1, description="任务描述/指令")
    timeout_s: float = Field(default=3600.0, gt=0, description="超时阈值（秒）")
    metadata: dict[str, Any] = Field(
        default_factory=dict, description="扩展元数据",
    )


class LongRunningTaskProgressReport(BaseModel):
    """执行器上报进度请求体（2026-11-08 新增端点）."""

    progress: float = Field(..., ge=0.0, le=1.0, description="进度比例 0.0–1.0")
    message: str = Field(default="", max_length=2000, description="进度说明")


class LongRunningTaskCompleteReport(BaseModel):
    """执行器上报完成请求体（2026-11-08 新增端点）."""

    result: str = Field(default="", max_length=20000, description="任务结果摘要")


class LongRunningTaskFailReport(BaseModel):
    """执行器上报失败请求体（2026-11-08 新增端点）."""

    error: str = Field(default="", max_length=20000, description="失败原因")


# ── 管理器单例访问（向后兼容重新导出）──────────────────────────────
# 测试通过 ``lrt_router._set_manager_for_tests`` 注入/重置管理器实例，
# 因此这里保留薄包装转发到 service 层。

def get_manager() -> Any:
    """获取全局长任务管理器单例（转发到 service 层）."""
    return session_service.get_long_running_task_manager()


def _set_manager_for_tests(manager: Any) -> None:
    """注入/重置管理器实例 — 仅供单元测试隔离使用."""
    session_service.set_long_running_task_manager_for_tests(manager)


# ── Endpoints ────────────────────────────────────────────────────


@router.post("")
@handle_api_errors
async def submit_long_running_task(
    body: LongRunningTaskSubmit,
    request: Request,
) -> dict[str, Any]:
    """提交长任务.

    创建一个 ``pending`` 状态的长任务，返回 ``task_id``。
    Agent 执行器随后通过 ``update_progress`` 拉取并推进任务。
    """
    require_admin(request)
    task_id = session_service.submit_long_running_task(
        agent_name=body.agent_name,
        task=body.task,
        timeout_s=body.timeout_s,
        metadata=body.metadata,
    )
    return {"status": "ok", "task_id": task_id}


@router.get("", response_model=LongRunningTaskListEnvelope)
@handle_api_errors
async def list_active_long_running_tasks(request: Request) -> dict[str, Any]:
    """列出所有活跃长任务（pending / running）."""
    require_admin(request)
    tasks = session_service.list_active_long_running_tasks()
    return {
        "status": "ok",
        "count": len(tasks),
        "tasks": tasks,
    }


@router.get("/{task_id}")
@handle_api_errors
async def get_long_running_task(task_id: str, request: Request) -> dict[str, Any]:
    """查询单个长任务状态."""
    require_admin(request)
    try:
        task = session_service.get_long_running_task_status(task_id)
    except KeyError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"任务不存在: {task_id}",
        ) from None
    return {"status": "ok", "task": task}


@router.post("/{task_id}/cancel", response_model=LongRunningTaskCancelEnvelope)
@handle_api_errors
async def cancel_long_running_task(task_id: str, request: Request) -> dict[str, Any]:
    """取消长任务.

    仅对 ``pending`` / ``running`` 状态的任务有效。已进入终态的任务
    返回 409 Conflict。
    """
    require_admin(request)
    ok = session_service.cancel_long_running_task(task_id)
    if not ok:
        # 区分不存在与已终态
        try:
            task = session_service.get_long_running_task_status(task_id)
        except KeyError:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"任务不存在: {task_id}",
            ) from None
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"任务已处于终态 {task['status']}，无法取消",
        ) from None
    return {"status": "ok", "task_id": task_id}


@router.post("/{task_id}/progress", response_model=LongRunningTaskCancelEnvelope)
@handle_api_errors
async def report_long_running_task_progress(
    task_id: str, body: LongRunningTaskProgressReport, request: Request,
) -> dict[str, Any]:
    """执行器上报进度（2026-11-08 新增）.

    这是长任务子系统此前缺的那一环：manager 的 ``update_progress`` 一直在，
    但没有任何入口能把进度推进回来（全仓零调用方、无 HTTP 上报端点），
    于是提交的任务永远停在 ``pending``、SSE 进度流永不动。本端点补上
    执行器 → manager 的上报通道，上报同时把状态推进为 ``running``。
    """
    require_admin(request)
    try:
        session_service.report_long_running_task_progress(
            task_id, body.progress, body.message,
        )
    except KeyError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"任务不存在: {task_id}",
        ) from None
    return {"status": "ok", "task_id": task_id}


@router.post("/{task_id}/complete", response_model=LongRunningTaskCancelEnvelope)
@handle_api_errors
async def complete_long_running_task(
    task_id: str, body: LongRunningTaskCompleteReport, request: Request,
) -> dict[str, Any]:
    """执行器上报成功完成（2026-11-08 新增）. 写入 result 并推 SSE done 事件."""
    require_admin(request)
    try:
        session_service.complete_long_running_task(task_id, body.result)
    except KeyError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"任务不存在: {task_id}",
        ) from None
    return {"status": "ok", "task_id": task_id}


@router.post("/{task_id}/fail", response_model=LongRunningTaskCancelEnvelope)
@handle_api_errors
async def fail_long_running_task(
    task_id: str, body: LongRunningTaskFailReport, request: Request,
) -> dict[str, Any]:
    """执行器上报失败（2026-11-08 新增）. 写入 error 并推 SSE error 事件."""
    require_admin(request)
    try:
        session_service.fail_long_running_task(task_id, body.error)
    except KeyError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"任务不存在: {task_id}",
        ) from None
    return {"status": "ok", "task_id": task_id}


@router.get("/{task_id}/progress")
@handle_api_errors
async def long_running_task_progress_stream(task_id: str, request: Request) -> Any:
    """SSE 进度流 — 实时推送任务进度更新.

    客户端使用 ``EventSource`` 连接此端点，接收 ``progress`` 事件。
    任务进入终态后推送最终状态并关闭流。

    SSE 事件格式::

        event: progress
        data: {"task_id": "...", "progress": 0.5, "message": "...", "status": "running"}

        event: done
        data: {"task_id": "...", "status": "completed", "result": "..."}

    若任务不存在，推送 ``error`` 事件并关闭。
    """
    require_admin(request)
    # 预检任务是否存在
    try:
        session_service.get_long_running_task_raw(task_id)
    except KeyError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"任务不存在: {task_id}",
        ) from None

    return StreamingResponse(
        session_service.long_running_task_progress_generator(task_id),
        media_type="text/event-stream",
    )
