"""Control/action endpoints for MAOP Dashboard.

Business logic (job lifecycle, maintenance handlers, validate/doctor)
lives in :mod:`maop.dashboard.services.system_service`; this router
only does request parsing, auth, service dispatch, and response
formatting.
"""

from __future__ import annotations

import asyncio  # noqa: F401 — re-exported for test monkeypatch (ctrl.asyncio)
import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from maop.core.security.middleware import require_admin
from maop.dashboard.error_handler import handle_api_errors
from maop.dashboard.services import system_service

# Re-export shared state for backward compatibility (tests monkeypatch
# ``control.MAOP_ROOT`` / ``control.active_jobs`` etc.).  The service
# layer accesses these via the ``state`` module so monkeypatching
# ``state.MAOP_ROOT`` is what takes effect at runtime.
from .state import MAOP_ROOT, active_jobs, active_jobs_lock, cache, cache_lock  # noqa: F401

logger = logging.getLogger(__name__)

router = APIRouter()


class RunRequest(BaseModel):
    task: str | None = Field(default=None, max_length=10000)
    workflow: str | None = Field(default=None, max_length=10000)
    agent: str = Field(default="", max_length=100)


class MaintainRequest(BaseModel):
    action: str | None = Field(default=None, pattern=r"^(cleanup|reset|rebuild|gc|log-rotate|prune|health|backup|cache-clear|reload|reindex|vacuum)$")


class CancelRequest(BaseModel):
    """取消运行中作业的请求体。"""
    job_id: str = Field(default="", max_length=128)


@router.get("/api/control/status")
@handle_api_errors("control status")
async def control_status(request: Request) -> dict[str, Any]:
    """Return status of all active control jobs."""
    require_admin(request)
    jobs = system_service.get_control_status()
    return {"status": "ok", "active_jobs": jobs, "jobs": jobs, "count": len(jobs)}

@router.post("/api/control/run")
@handle_api_errors("control run")
async def control_run(body: RunRequest, request: Request) -> dict[str, Any]:
    """Start a new control job from a task or workflow."""
    require_admin(request)
    actual_task = body.task or body.workflow or "default"
    # P1: task 参数字符集白名单校验，防止注入非法字符（与 workflow.py:60-63 对齐）
    # P2-22: \s 允许换行符(\n)，改为显式空格字符避免多行注入
    if not system_service.is_valid_task_name(actual_task):
        raise HTTPException(
            status_code=400,
            detail="invalid task name: only alphanumeric, spaces, dots, hyphens, underscores allowed",
        )
    job_id = await system_service.start_control_run(actual_task)
    return {"status": "ok", "job_id": job_id, "task": actual_task}

@router.post("/api/control/pause")
@handle_api_errors("control pause")
async def control_pause(request: Request) -> dict[str, Any]:
    """Pause the control loop."""
    require_admin(request)
    paused = system_service.pause_control()
    return {"status": "ok", "action": "pause", "paused": paused}

@router.post("/api/control/resume")
@handle_api_errors("control resume")
async def control_resume(request: Request) -> dict[str, Any]:
    """Resume the control loop."""
    require_admin(request)
    resumed = system_service.resume_control()
    return {"status": "ok", "action": "resume", "resumed": resumed}


@router.get("/api/control/pause-status")
@handle_api_errors("control pause status")
async def control_pause_status(request: Request) -> dict[str, Any]:
    """查询系统暂停/恢复状态（M4 修复新增）。

    返回当前是否处于暂停状态，以及暂停标记文件的路径与存在性。
    便于运维监控与 dashboard 显示暂停状态。
    """
    require_admin(request)
    info = system_service.get_pause_status()
    return {
        # M-4 fix: 统一 status 值为 "ok"，用 is_paused 字段表达状态。
        "status": "ok",
        "is_paused": info["is_paused"],
        "pause_file": info["pause_file"],
        "paused_jobs": info["paused_jobs"],
        "running_jobs": info["running_jobs"],
        "total_jobs": info["total_jobs"],
    }

@router.post("/api/control/stop")
@handle_api_errors("control stop")
async def control_stop(request: Request) -> dict[str, Any]:
    """Stop the control loop gracefully."""
    require_admin(request)
    stopped = system_service.stop_control()
    return {"status": "ok", "action": "stop", "stopped": stopped}

@router.post("/api/control/validate")
@handle_api_errors("control validate")
async def control_validate(request: Request) -> dict[str, Any]:
    """Validate the current MAOP configuration."""
    require_admin(request)
    try:
        job_id, result = system_service.validate_config_job()
        return {"status": "ok", "job_id": job_id, "result": result}
    except Exception:
        # M4 fix: 统一错误处理——raise HTTPException 让 handle_api_errors 装饰器处理。
        raise HTTPException(status_code=500, detail="Validate failed")

@router.post("/api/control/doctor")
@handle_api_errors("control doctor")
async def control_doctor(request: Request) -> dict[str, Any]:
    """Run health diagnostics and return findings."""
    require_admin(request)
    try:
        job_id, findings = system_service.run_doctor_job()
        return {"status": "ok", "job_id": job_id, "result": findings}
    except Exception:
        # M4 fix: 统一错误处理——raise HTTPException 让 handle_api_errors 装饰器处理。
        raise HTTPException(status_code=500, detail="Doctor check failed")

@router.post("/api/control/cancel")
@handle_api_errors("control cancel")
async def control_cancel(body: CancelRequest, request: Request) -> dict[str, Any]:
    """Cancel a running job by ID."""
    require_admin(request)
    if system_service.cancel_job(body.job_id):
        return {"status": "ok", "job_id": body.job_id}
    raise HTTPException(status_code=404, detail="job not found")

@router.post("/api/control/refresh")
@handle_api_errors("control refresh")
async def control_refresh(request: Request) -> dict[str, Any]:
    """Refresh runtime state and caches."""
    require_admin(request)
    await system_service.refresh_caches()
    return {"status": "ok", "cache": "cleared"}

@router.post("/api/control/clear-cache")
@handle_api_errors("control clear cache")
async def control_clear_cache(request: Request) -> dict[str, Any]:
    """Clear all in-memory caches."""
    require_admin(request)
    await system_service.clear_caches()
    return {"status": "ok"}


@router.post("/api/control/provider-health")
@handle_api_errors("control provider health")
async def control_provider_health(request: Request) -> dict[str, Any]:
    """Check health of configured LLM providers."""
    require_admin(request)
    try:
        components = system_service.check_provider_health()
        return {"status": "ok", "components": components}
    except Exception:
        logger.exception("Provider health check failed")
        # M4 fix: 统一错误处理——raise HTTPException 让 handle_api_errors 装饰器处理。
        raise HTTPException(status_code=500, detail="Provider health check failed")


@router.post("/api/control/maintain")
@handle_api_errors("control maintain")
async def api_control_maintain(body: MaintainRequest, request: Request) -> dict[str, Any]:
    """Execute a maintenance operation (cleanup/compact/etc.)."""
    require_admin(request)
    return await system_service.run_maintain_action(body.action)
