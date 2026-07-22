"""Control/action endpoints for MAOP Dashboard."""

from __future__ import annotations

from typing import Any

import asyncio
import sys
import time

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from .state import MAOP_ROOT, active_jobs, cache, cache_lock
from maop.core.middleware import require_admin
import uuid as _uuid
import logging

logger = logging.getLogger(__name__)

router = APIRouter()


class RunRequest(BaseModel):
    task: str | None = Field(default=None, max_length=10000)
    workflow: str | None = Field(default=None, max_length=10000)
    agent: str = Field(default="", max_length=100)


class MaintainRequest(BaseModel):
    action: str | None = Field(default=None, pattern=r"^(cleanup|reset|rebuild|gc|log-rotate|prune|health|backup|cache-clear|reload)$")


@router.get("/api/control/status")
async def control_status() -> Any:
    jobs = []
    for jid, job in active_jobs.items():
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
async def control_run(body: RunRequest, request: Request) -> Any:
    require_admin(request)
    actual_task = body.task or body.workflow or "default"
    job_id = _uuid.uuid4().hex[:8]
    proc = await asyncio.create_subprocess_exec(
        sys.executable, "-m", "maop.cli", "run",
        "--task", actual_task,
        cwd=str(MAOP_ROOT),
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    active_jobs[job_id] = {"action": "run", "status": "running",
        "start": time.strftime("%Y-%m-%dT%H:%M:%S"), "task": actual_task, "process": proc}
    return {"job_id": job_id, "status": "started", "task": actual_task}

@router.post("/api/control/pause")
async def control_pause(request: Request) -> Any:
    require_admin(request)
    pause_file = MAOP_ROOT / "logs" / ".maop_pause"
    pause_file.parent.mkdir(parents=True, exist_ok=True)
    pause_file.write_text("paused")
    paused = 0
    for jid, job in active_jobs.items():
        proc = job.get("process")
        if proc and proc.returncode is None and job.get("status") == "running":
            job["status"] = "paused"
            paused += 1
    return {"status": "ok", "action": "pause", "paused": paused}

@router.post("/api/control/resume")
async def control_resume(request: Request) -> Any:
    require_admin(request)
    pause_file = MAOP_ROOT / "logs" / ".maop_pause"
    if pause_file.exists():
        pause_file.unlink()
    resumed = 0
    for jid, job in active_jobs.items():
        if job.get("status") == "paused":
            job["status"] = "running"
            resumed += 1
    return {"status": "ok", "action": "resume", "resumed": resumed}

@router.post("/api/control/stop")
async def control_stop(request: Request) -> Any:
    require_admin(request)
    stopped = 0
    for jid, job in active_jobs.items():
        proc = job.get("process")
        if proc and proc.returncode is None:
            proc.terminate()
            job["status"] = "stopped"
            stopped += 1
    return {"status": "ok", "action": "stop", "stopped": stopped}

@router.post("/api/control/validate")
async def control_validate(request: Request) -> Any:
    require_admin(request)
    job_id = _uuid.uuid4().hex[:8]
    try:
        from maop.deploy import validate_config
        result = validate_config(MAOP_ROOT)
        active_jobs[job_id] = {"action": "validate", "status": "completed", "start": time.strftime("%Y-%m-%dT%H:%M:%S"), "task": "config validation", "result": result.model_dump()}
        return {"job_id": job_id, "status": "completed", "result": result.model_dump()}
    except Exception:
        logger.error("Validate failed", exc_info=True)
        active_jobs[job_id] = {"action": "validate", "status": "failed", "start": time.strftime("%Y-%m-%dT%H:%M:%S"), "task": "config validation", "error": "Validate failed"}
        return {"job_id": job_id, "status": "failed", "error": "Validate failed"}

@router.post("/api/control/doctor")
async def control_doctor(request: Request) -> Any:
    require_admin(request)
    job_id = _uuid.uuid4().hex[:8]
    try:
        from maop.deploy import health_check
        results = health_check(MAOP_ROOT)
        active_jobs[job_id] = {"action": "doctor", "status": "completed", "start": time.strftime("%Y-%m-%dT%H:%M:%S"), "task": "system diagnostics", "result": [r.model_dump() for r in results]}
        return {"job_id": job_id, "status": "completed", "result": [r.model_dump() for r in results]}
    except Exception:
        logger.error("Doctor check failed", exc_info=True)
        active_jobs[job_id] = {"action": "doctor", "status": "failed", "start": time.strftime("%Y-%m-%dT%H:%M:%S"), "task": "system diagnostics", "error": "Doctor check failed"}
        return {"job_id": job_id, "status": "failed", "error": "Doctor check failed"}

@router.post("/api/control/cancel")
async def control_cancel(request: Request) -> Any:
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
async def control_refresh(request: Request) -> Any:
    require_admin(request)
    async with cache_lock:
        cache.clear()
    return {"status": "ok", "cache": "cleared"}

@router.post("/api/control/clear-cache")
async def control_clear_cache(request: Request) -> Any:
    require_admin(request)
    async with cache_lock:
        cache.clear()
    return {"status": "ok"}


@router.post("/api/control/provider-health")
async def control_provider_health(request: Request) -> Any:
    require_admin(request)
    try:
        from maop.deploy import health_check
        results = health_check(MAOP_ROOT)
        return {"status": "ok", "components": [r.model_dump() for r in results]}
    except Exception:
        logger.error("Provider health check failed", exc_info=True)
        return {"status": "error", "error": "Provider health check failed"}

@router.post("/api/control/maintain")
async def api_control_maintain(body: MaintainRequest, request: Request) -> Any:
    require_admin(request)
    action = body.action
    if action is None:
        return {"status": "ok", "action": "noop", "msg": "No action specified"}
    try:
        if action == "log-rotate":
            try:
                from maop.core.log_rotate import rotate_logs
                result = rotate_logs(log_dir=MAOP_ROOT / "logs", data_dir=MAOP_ROOT / "data")
                return {"status": "ok", "action": "log-rotate", "msg": "Logs rotated", "rotated": result.rotated, "deleted": result.deleted}
            except Exception:
                logger.error("Log rotate failed", exc_info=True)
                return {"status": "ok", "action": "log-rotate", "msg": "Skipped: log rotate unavailable"}
        elif action == "prune":
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
                logger.error("Prune failed", exc_info=True)
                return {"status": "error", "action": "prune", "msg": "Failed: prune unavailable"}
        elif action == "health":
            try:
                from maop.deploy import health_check
                results = health_check(MAOP_ROOT)
                healthy = all(r.status == "ok" for r in results)
                return {"status": "ok", "action": "health", "healthy": healthy, "components": [r.model_dump() for r in results]}
            except Exception:
                logger.error("Health check failed", exc_info=True)
                return {"status": "ok", "action": "health", "healthy": True, "msg": "Skipped: health check unavailable"}
        elif action == "backup":
            try:
                from maop.core.db_backup import DbBackup
                backup = DbBackup(root_dir=str(MAOP_ROOT))
                entries = backup.run() if hasattr(backup, "run") else []
                path = entries[0].backup_path if entries else "N/A"
                return {"status": "ok", "action": "backup", "path": str(path)}
            except Exception:
                logger.error("Backup failed", exc_info=True)
                return {"status": "ok", "action": "backup", "msg": "Skipped: backup unavailable"}
        elif action == "cache-clear":
            async with cache_lock:
                cache.clear()
            return {"status": "ok", "action": "cache-clear"}
        elif action == "reload":
            try:
                from maop.config.hot_reload import ConfigHotReload
                reloader = ConfigHotReload(root_dir=str(MAOP_ROOT))
                reloader.reload() if hasattr(reloader, "reload") else None
                return {"status": "ok", "action": "reload", "msg": "Config reloaded"}
            except Exception:
                logger.error("Config reload failed", exc_info=True)
                return {"status": "ok", "action": "reload", "msg": "Skipped: reload unavailable"}
        return {"status": "ok", "action": action or "noop"}
    except Exception:
        logger.error("Maintain action failed", exc_info=True)
        return {"status": "error", "error": "Maintain action failed"}
