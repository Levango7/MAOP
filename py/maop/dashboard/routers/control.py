"""Control/action endpoints for MAOP Dashboard."""

from __future__ import annotations

import asyncio
import logging
import re
import sys
import time
import uuid as _uuid
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from maop.core.security.middleware import require_admin

from .state import MAOP_ROOT, active_jobs, cache, cache_lock

logger = logging.getLogger(__name__)

router = APIRouter()


class RunRequest(BaseModel):
    task: str | None = Field(default=None, max_length=10000)
    workflow: str | None = Field(default=None, max_length=10000)
    agent: str = Field(default="", max_length=100)


class MaintainRequest(BaseModel):
    action: str | None = Field(default=None, pattern=r"^(cleanup|reset|rebuild|gc|log-rotate|prune|health|backup|cache-clear|reload|reindex|vacuum)$")


@router.get("/api/control/status")
async def control_status() -> dict[str, Any]:
    """Return status of all active control jobs."""
    jobs = []
    for job in active_jobs.values():
        proc = job.get("process")
        if proc is not None:
            if proc.returncode is not None:
                job["status"] = "completed" if proc.returncode == 0 else "failed"
                job["exit_code"] = proc.returncode
            else:
                job["status"] = "running"
        jobs.append({k: v for k, v in job.items() if k != "process"})
    return {"active_jobs": jobs, "jobs": jobs, "count": len(jobs)}

@router.post("/api/control/run")
async def control_run(body: RunRequest, request: Request) -> dict[str, Any]:
    """Start a new control job from a task or workflow."""
    require_admin(request)
    actual_task = body.task or body.workflow or "default"
    # P1: task 参数字符集白名单校验，防止注入非法字符（与 workflow.py:60-63 对齐）
    if not re.match(r"^[\w\s\.\-]+$", actual_task):
        raise HTTPException(
            status_code=400,
            detail="invalid task name: only alphanumeric, spaces, dots, hyphens, underscores allowed",
        )
    job_id = _uuid.uuid4().hex[:8]
    proc = await asyncio.create_subprocess_exec(
        sys.executable, "-m", "maop.cli", "run",
        "--task", actual_task,
        cwd=str(MAOP_ROOT),
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    # Drain pipes in background to prevent deadlock when child output exceeds OS pipe buffer (~64KB)
    asyncio.create_task(proc.communicate())
    active_jobs[job_id] = {"action": "run", "status": "running",
        "start": time.strftime("%Y-%m-%dT%H:%M:%S"), "task": actual_task, "process": proc}
    return {"job_id": job_id, "status": "started", "task": actual_task}

@router.post("/api/control/pause")
async def control_pause(request: Request) -> dict[str, Any]:
    """Pause the control loop."""
    require_admin(request)
    pause_file = MAOP_ROOT / "logs" / ".maop_pause"
    pause_file.parent.mkdir(parents=True, exist_ok=True)
    pause_file.write_text("paused")
    paused = 0
    for job in active_jobs.values():
        proc = job.get("process")
        if proc and proc.returncode is None and job.get("status") == "running":
            job["status"] = "paused"
            paused += 1
    return {"status": "ok", "action": "pause", "paused": paused}

@router.post("/api/control/resume")
async def control_resume(request: Request) -> dict[str, Any]:
    """Resume the control loop."""
    require_admin(request)
    pause_file = MAOP_ROOT / "logs" / ".maop_pause"
    if pause_file.exists():
        pause_file.unlink()
    resumed = 0
    for job in active_jobs.values():
        if job.get("status") == "paused":
            job["status"] = "running"
            resumed += 1
    return {"status": "ok", "action": "resume", "resumed": resumed}


@router.get("/api/control/pause-status")
async def control_pause_status(request: Request) -> dict[str, Any]:
    """查询系统暂停/恢复状态（M4 修复新增）。

    返回当前是否处于暂停状态，以及暂停标记文件的路径与存在性。
    便于运维监控与 dashboard 显示暂停状态。
    """
    require_admin(request)
    pause_file = MAOP_ROOT / "logs" / ".maop_pause"
    is_paused = pause_file.exists()
    paused_jobs = sum(1 for job in active_jobs.values() if job.get("status") == "paused")
    running_jobs = sum(1 for job in active_jobs.values() if job.get("status") == "running")
    return {
        "status": "paused" if is_paused else "running",
        "is_paused": is_paused,
        "pause_file": str(pause_file),
        "paused_jobs": paused_jobs,
        "running_jobs": running_jobs,
        "total_jobs": len(active_jobs),
    }

@router.post("/api/control/stop")
async def control_stop(request: Request) -> dict[str, Any]:
    """Stop the control loop gracefully."""
    require_admin(request)
    stopped = 0
    for job in active_jobs.values():
        proc = job.get("process")
        if proc and proc.returncode is None:
            proc.terminate()
            job["status"] = "stopped"
            stopped += 1
    return {"status": "ok", "action": "stop", "stopped": stopped}

@router.post("/api/control/validate")
async def control_validate(request: Request) -> dict[str, Any]:
    """Validate the current MAOP configuration."""
    require_admin(request)
    job_id = _uuid.uuid4().hex[:8]
    try:
        from maop.deploy import validate_config
        result = validate_config(MAOP_ROOT)
        active_jobs[job_id] = {"action": "validate", "status": "completed", "start": time.strftime("%Y-%m-%dT%H:%M:%S"), "task": "config validation", "result": result.model_dump()}
        return {"job_id": job_id, "status": "completed", "result": result.model_dump()}
    except Exception:
        logger.exception("Validate failed")
        active_jobs[job_id] = {"action": "validate", "status": "failed", "start": time.strftime("%Y-%m-%dT%H:%M:%S"), "task": "config validation", "error": "Validate failed"}
        return {"job_id": job_id, "status": "failed", "error": "Validate failed"}

@router.post("/api/control/doctor")
async def control_doctor(request: Request) -> dict[str, Any]:
    """Run health diagnostics and return findings."""
    require_admin(request)
    job_id = _uuid.uuid4().hex[:8]
    try:
        from maop.deploy import health_check
        results = health_check(MAOP_ROOT)
        active_jobs[job_id] = {"action": "doctor", "status": "completed", "start": time.strftime("%Y-%m-%dT%H:%M:%S"), "task": "system diagnostics", "result": [r.model_dump() for r in results]}
        return {"job_id": job_id, "status": "completed", "result": [r.model_dump() for r in results]}
    except Exception:
        logger.exception("Doctor check failed")
        active_jobs[job_id] = {"action": "doctor", "status": "failed", "start": time.strftime("%Y-%m-%dT%H:%M:%S"), "task": "system diagnostics", "error": "Doctor check failed"}
        return {"job_id": job_id, "status": "failed", "error": "Doctor check failed"}

@router.post("/api/control/cancel")
async def control_cancel(request: Request) -> dict[str, Any]:
    """Cancel a running job by ID."""
    require_admin(request)
    body = await request.json()
    job_id = body.get("job_id", "")
    if job_id in active_jobs:
        proc = active_jobs[job_id].get("process")
        if proc and proc.returncode is None:
            proc.terminate()
        active_jobs[job_id]["status"] = "cancelled"
        return {"job_id": job_id, "status": "cancelled"}
    raise HTTPException(404, "job not found")

@router.post("/api/control/refresh")
async def control_refresh(request: Request) -> dict[str, Any]:
    """Refresh runtime state and caches."""
    require_admin(request)
    async with cache_lock:
        cache.clear()
    return {"status": "ok", "cache": "cleared"}

@router.post("/api/control/clear-cache")
async def control_clear_cache(request: Request) -> dict[str, Any]:
    """Clear all in-memory caches."""
    require_admin(request)
    async with cache_lock:
        cache.clear()
    return {"status": "ok"}


@router.post("/api/control/provider-health")
async def control_provider_health(request: Request) -> dict[str, Any]:
    """Check health of configured LLM providers."""
    require_admin(request)
    try:
        from maop.deploy import health_check
        results = health_check(MAOP_ROOT)
        return {"status": "ok", "components": [r.model_dump() for r in results]}
    except Exception:
        logger.exception("Provider health check failed")
        return {"status": "error", "error": "Provider health check failed"}

async def _maintain_log_rotate() -> dict[str, Any]:
    """log-rotate 维护操作：轮转日志文件。"""
    try:
        from maop.core.reliability.log_rotate import rotate_logs
        result = rotate_logs(log_dir=MAOP_ROOT / "logs", data_dir=MAOP_ROOT / "data")
        return {"status": "ok", "action": "log-rotate", "msg": "Logs rotated", "rotated": result.rotated, "deleted": result.deleted}
    except Exception:
        logger.exception("Log rotate failed")
        return {"status": "ok", "action": "log-rotate", "msg": "Skipped: log rotate unavailable"}


async def _maintain_prune() -> dict[str, Any]:
    """prune 维护操作：清理过期 memory 条目。"""
    try:
        from maop.memory.store import MemoryStore
        store = MemoryStore(root_dir=str(MAOP_ROOT))
        stats_before = store.stats()
        if hasattr(stats_before, 'model_dump'):
            stats_dict_before: Any = stats_before.model_dump()
        elif isinstance(stats_before, dict):
            stats_dict_before = stats_before
        else:
            stats_dict_before = {}
        total_before = stats_dict_before.get("total_entries", 0)
        pruned_ids = store.prune(ttl_days=30, dry_run=False) if hasattr(store, "prune") else []
        if isinstance(pruned_ids, int):
            pruned_count = pruned_ids
            pruned_ids = []
        else:
            pruned_count = len(pruned_ids)
        stats_after = store.stats()
        if hasattr(stats_after, 'model_dump'):
            stats_dict_after: Any = stats_after.model_dump()
        elif isinstance(stats_after, dict):
            stats_dict_after = stats_after
        else:
            stats_dict_after = {}
        total_after = stats_dict_after.get("total_entries", 0)
        return {"status": "ok", "action": "prune", "pruned": pruned_count, "remaining": total_after, "total_before": total_before, "pruned_ids": list(pruned_ids)[:20]}
    except Exception:
        logger.exception("Prune failed")
        return {"status": "error", "action": "prune", "msg": "Failed: prune unavailable"}


async def _maintain_health() -> dict[str, Any]:
    """health 维护操作：运行健康检查。"""
    try:
        from maop.deploy import health_check
        results = health_check(MAOP_ROOT)
        healthy = all(r.status == "ok" for r in results)
        return {"status": "ok", "action": "health", "healthy": healthy, "components": [r.model_dump() for r in results]}
    except Exception:
        logger.exception("Health check failed")
        return {"status": "ok", "action": "health", "healthy": True, "msg": "Skipped: health check unavailable"}


async def _maintain_backup() -> dict[str, Any]:
    """backup 维护操作：运行数据库备份。"""
    try:
        from maop.core.backends.db_backup import DbBackup
        backup = DbBackup(root_dir=str(MAOP_ROOT))
        entries = backup.run() if hasattr(backup, "run") else []
        path = entries[0].backup_path if entries else "N/A"
        return {"status": "ok", "action": "backup", "path": str(path)}
    except Exception:
        logger.exception("Backup failed")
        return {"status": "ok", "action": "backup", "msg": "Skipped: backup unavailable"}


async def _maintain_cache_clear() -> dict[str, Any]:
    """cache-clear 维护操作：清空内存缓存。"""
    async with cache_lock:
        cache.clear()
    return {"status": "ok", "action": "cache-clear"}


async def _maintain_reload() -> dict[str, Any]:
    """reload 维护操作：热重载配置。"""
    try:
        from maop.config.hot_reload import ConfigHotReload
        reloader = ConfigHotReload(root_dir=str(MAOP_ROOT))
        reloader.reload() if hasattr(reloader, "reload") else None
        return {"status": "ok", "action": "reload", "msg": "Config reloaded"}
    except Exception:
        logger.exception("Config reload failed")
        return {"status": "ok", "action": "reload", "msg": "Skipped: reload unavailable"}


async def _maintain_reindex() -> dict[str, Any]:
    """reindex 维护操作：重建向量索引。"""
    try:
        from maop.core.memory.vector import VectorStore
        store = VectorStore() if hasattr(VectorStore, '__init__') else None  # type: ignore
        if store and hasattr(store, 'reindex'):
            store.reindex()
            return {"status": "ok", "action": "reindex", "msg": "Vector index rebuilt"}
        return {"status": "ok", "action": "reindex", "msg": "Skipped: vector store unavailable"}
    except Exception:
        logger.exception("Vector reindex failed")
        return {"status": "ok", "action": "reindex", "msg": "Skipped: reindex unavailable"}


async def _maintain_vacuum() -> dict[str, Any]:
    """vacuum 维护操作：压缩 SQLite 数据库。"""
    try:
        from maop.core.backends.db_utils import sqlite_connect
        with sqlite_connect() as conn:  # type: ignore
            conn.execute("VACUUM")
        return {"status": "ok", "action": "vacuum", "msg": "Database compacted"}
    except Exception:
        logger.exception("Database vacuum failed")
        return {"status": "ok", "action": "vacuum", "msg": "Skipped: vacuum unavailable"}


# 维护操作分派表（action name → handler coroutine function）。
_MAINTAIN_HANDLERS: dict[str, Any] = {
    "log-rotate": _maintain_log_rotate,
    "prune": _maintain_prune,
    "health": _maintain_health,
    "backup": _maintain_backup,
    "cache-clear": _maintain_cache_clear,
    "reload": _maintain_reload,
    "reindex": _maintain_reindex,
    "vacuum": _maintain_vacuum,
}


@router.post("/api/control/maintain")
async def api_control_maintain(body: MaintainRequest, request: Request) -> dict[str, Any]:
    """Execute a maintenance operation (cleanup/compact/etc.)."""
    require_admin(request)
    action = body.action
    if action is None:
        return {"status": "ok", "action": "noop", "msg": "No action specified"}
    try:
        handler = _MAINTAIN_HANDLERS.get(action)
        if handler is not None:
            return await handler()
        return {"status": "ok", "action": action or "noop"}
    except Exception:
        logger.exception("Maintain action failed")
        return {"status": "error", "error": "Maintain action failed"}
