"""长任务管理路由 — 自主 Agent 长任务的提交、查询、取消与进度流.

为 Devin / OpenHands 等自主 Agent 提供长任务的 HTTP API。所有状态
持久化到 SQLite，支持 SSE 实时进度推送。

Endpoints:
  POST   /api/long-running-tasks                        — 提交长任务
  GET    /api/long-running-tasks                        — 列出活跃任务
  GET    /api/long-running-tasks/{task_id}              — 查询任务状态
  POST   /api/long-running-tasks/{task_id}/cancel       — 取消任务
  GET    /api/long-running-tasks/{task_id}/progress     — SSE 进度流

数据存储: SQLite ``long_running_tasks`` 表（位于统一 maop.db）.
"""

from __future__ import annotations

import asyncio
import json
import logging
import threading
from typing import Any

from fastapi import APIRouter, HTTPException, Request, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from maop.core.agent.router.long_running_task import (
    LongRunningTask,
    LongRunningTaskManager,
)
from maop.core.backends.db_utils import get_db_path
from maop.dashboard.error_handler import handle_api_errors

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


# ── 管理器单例（线程安全懒初始化）─────────────────────────────────
# 路由模块维护一个全局 LongRunningTaskManager 实例，使用统一 maop.db。
# 测试可通过 ``_set_manager_for_tests`` 注入自定义实例以隔离 SQLite。

_manager_lock = threading.Lock()
_manager: LongRunningTaskManager | None = None


def get_manager() -> LongRunningTaskManager:
    """获取全局长任务管理器单例（线程安全懒初始化）."""
    global _manager
    if _manager is not None:
        return _manager
    with _manager_lock:
        if _manager is None:
            _manager = LongRunningTaskManager(db_path=get_db_path("long_running_task"))
    return _manager


def _set_manager_for_tests(manager: LongRunningTaskManager | None) -> None:
    """注入/重置管理器实例 — 仅供单元测试隔离使用."""
    global _manager
    with _manager_lock:
        _manager = manager


# ── Endpoints ────────────────────────────────────────────────────


@router.post("")
@handle_api_errors
async def submit_long_running_task(
    body: LongRunningTaskSubmit,
) -> dict[str, Any]:
    """提交长任务.

    创建一个 ``pending`` 状态的长任务，返回 ``task_id``。
    Agent 执行器随后通过 ``update_progress`` 拉取并推进任务。
    """
    mgr = get_manager()
    task_id = mgr.submit(
        agent_name=body.agent_name,
        task=body.task,
        timeout_s=body.timeout_s,
        metadata=body.metadata,
    )
    return {"status": "ok", "task_id": task_id}


@router.get("")
@handle_api_errors
async def list_active_long_running_tasks() -> dict[str, Any]:
    """列出所有活跃长任务（pending / running）."""
    mgr = get_manager()
    tasks = mgr.list_active()
    return {
        "status": "ok",
        "count": len(tasks),
        "tasks": [t.model_dump() for t in tasks],
    }


@router.get("/{task_id}")
@handle_api_errors
async def get_long_running_task(task_id: str) -> dict[str, Any]:
    """查询单个长任务状态."""
    mgr = get_manager()
    try:
        task = mgr.get_status(task_id)
    except KeyError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"任务不存在: {task_id}",
        ) from None
    return {"status": "ok", "task": task.model_dump()}


@router.post("/{task_id}/cancel")
@handle_api_errors
async def cancel_long_running_task(task_id: str) -> dict[str, Any]:
    """取消长任务.

    仅对 ``pending`` / ``running`` 状态的任务有效。已进入终态的任务
    返回 409 Conflict。
    """
    mgr = get_manager()
    ok = mgr.cancel(task_id)
    if not ok:
        # 区分不存在与已终态
        try:
            task = mgr.get_status(task_id)
        except KeyError:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"任务不存在: {task_id}",
            ) from None
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"任务已处于终态 {task.status}，无法取消",
        ) from None
    return {"status": "ok", "task_id": task_id}


@router.get("/{task_id}/progress")
@handle_api_errors
async def long_running_task_progress_stream(task_id: str) -> Any:
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
    mgr = get_manager()

    # 预检任务是否存在
    try:
        initial = mgr.get_status(task_id)
    except KeyError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"任务不存在: {task_id}",
        ) from None

    # 终态集合
    terminal_statuses = {"completed", "failed", "cancelled", "timeout"}

    async def generate() -> Any:
        """SSE 生成器：轮询任务状态并推送进度."""
        last_progress: float = -1.0
        last_message: str = ""

        # 若初始已是终态，直接推送最终事件
        if initial.status in terminal_statuses:
            event_data = {
                "task_id": task_id,
                "status": initial.status,
                "progress": initial.progress,
                "result": initial.result,
                "error": initial.error,
            }
            yield f"event: done\ndata: {json.dumps(event_data)}\n\n"
            return

        while True:
            try:
                task = mgr.get_status(task_id)
            except KeyError:
                # 任务被删除（理论上不会发生，task_id 是 PK）
                err_data = {"task_id": task_id, "error": "任务不存在"}
                yield f"event: error\ndata: {json.dumps(err_data)}\n\n"
                return

            # 仅在进度或消息变化时推送，减少无效事件
            if task.progress != last_progress or task.progress_message != last_message:
                progress_data = {
                    "task_id": task_id,
                    "progress": task.progress,
                    "message": task.progress_message,
                    "status": task.status,
                }
                yield f"event: progress\ndata: {json.dumps(progress_data)}\n\n"
                last_progress = task.progress
                last_message = task.progress_message

            # 终态：推送 done 事件并关闭流
            if task.status in terminal_statuses:
                done_data = {
                    "task_id": task_id,
                    "status": task.status,
                    "progress": task.progress,
                    "result": task.result,
                    "error": task.error,
                }
                yield f"event: done\ndata: {json.dumps(done_data)}\n\n"
                return

            # 轮询间隔：1 秒
            await asyncio.sleep(1.0)

    return StreamingResponse(generate(), media_type="text/event-stream")