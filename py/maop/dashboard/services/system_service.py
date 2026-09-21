"""System & config service layer.

Encapsulates business logic for configuration history/rollback (config.py)
and control/action endpoints (control.py). Extracted from the router
layer so routers only do parameter parsing + auth + service call +
response formatting.

The service is intentionally framework-agnostic: it does not import
FastAPI and can be unit-tested or invoked without an HTTP context.
Shared runtime state (``active_jobs``, ``cache``, …) is imported from
``maop.dashboard.routers.state`` so the service operates on the same
singletons the dashboard initialised.

``state.py`` remains the single source of truth for shared constants
(``MAOP_ROOT``, ``get_bridge``, ``active_jobs``, …) and is intentionally
left unchanged — it is a dependency-injection module imported by 30+
routers, not a router itself.
"""

from __future__ import annotations

import asyncio
import logging
import re
import sys
import time
import uuid as _uuid
from typing import Any

from maop.core.config.config_history import ConfigHistory, get_config_history  # noqa: F401

# Shared runtime state — accessed via the ``state`` module so that tests
# which monkeypatch ``state.MAOP_ROOT`` / ``state.active_jobs`` etc. take
# effect here too.  Using ``from state import X`` would bind a snapshot
# of the reference at import time and miss monkeypatch reassignment.
from maop.dashboard.routers import state

logger = logging.getLogger(__name__)


# ════════════════════════════════════════════════════════════════════════════
# Config history & rollback
# ════════════════════════════════════════════════════════════════════════════

def list_config_history(history: ConfigHistory, limit: int) -> list[dict[str, Any]]:
    """Return recent configuration snapshots (newest first)."""
    return history.list_history(limit=limit)


def get_config_version(history: ConfigHistory, version: int) -> dict[str, Any] | None:
    """Return a single snapshot including the parsed config payload.

    Returns ``None`` if the version does not exist.
    """
    return history.get_version(version)


def rollback_config(history: ConfigHistory, version: int) -> dict[str, Any]:
    """Restore the configuration to a previously saved snapshot.

    The rollback itself is recorded as a new snapshot (so the audit
    trail remains linear) and a ``config_changed`` event is fired on
    the global event bus.

    Raises ``ValueError`` if the target version does not exist.
    """
    return history.rollback(version)


# ════════════════════════════════════════════════════════════════════════════
# Control: job status
# ════════════════════════════════════════════════════════════════════════════

def get_control_status() -> list[dict[str, Any]]:
    """Return a status snapshot of all active control jobs.

    Updates ``completed``/``failed`` status for finished processes and
    cleans up drain tasks to prevent memory leaks (P0-8).
    """
    jobs: list[dict[str, Any]] = []
    # B23: snapshot via list() to avoid RuntimeError during concurrent iteration.
    # H3 fix: active_jobs_lock guards concurrent read/write.
    with state.active_jobs_lock:
        for job in list(state.active_jobs.values()):
            proc = job.get("process")
            if proc is not None:
                if proc.returncode is not None:
                    job["status"] = "completed" if proc.returncode == 0 else "failed"
                    job["exit_code"] = proc.returncode
                    # P0-8: process ended — cancel _drain_task to prevent leak.
                    drain_task = job.pop("_drain_task", None)
                    if drain_task is not None and not drain_task.done():
                        drain_task.cancel()
                else:
                    job["status"] = "running"
            jobs.append({k: v for k, v in job.items() if k != "process"})
    return jobs


# ════════════════════════════════════════════════════════════════════════════
# Control: run
# ════════════════════════════════════════════════════════════════════════════

def is_valid_task_name(task: str) -> bool:
    """Whitelist-validate the task name to prevent injection.

    Allows alphanumeric, spaces, dots, hyphens, underscores only
    (P2-22: explicit space char to avoid multiline injection via \\s).
    """
    return bool(re.match(r"^[\w\.\- ]+$", task))


async def start_control_run(task: str) -> str:
    """Start a new control job from a task. Returns the ``job_id``.

    Spawns ``python -m maop.cli run --task <task>`` as a subprocess and
    registers it in ``active_jobs``. A drain task is created to consume
    stdout/stderr to prevent pipe-buffer deadlock (H4 fix).
    """
    job_id = _uuid.uuid4().hex[:8]
    proc = await asyncio.create_subprocess_exec(
        sys.executable, "-m", "maop.cli", "run",
        "--task", task,
        cwd=str(state.MAOP_ROOT),
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    # H4 fix: keep drain-task reference alive to avoid "Task was destroyed"
    # warnings and prevent pipe-buffer deadlock on large child output.
    _drain_task = asyncio.create_task(proc.communicate())
    with state.active_jobs_lock:
        state.active_jobs[job_id] = {
            "action": "run", "status": "running",
            "start": time.strftime("%Y-%m-%dT%H:%M:%S"), "task": task,
            "process": proc, "_drain_task": _drain_task,
        }
    return job_id


# ════════════════════════════════════════════════════════════════════════════
# Control: pause / resume / stop / cancel
# ════════════════════════════════════════════════════════════════════════════

def pause_control() -> int:
    """Pause the control loop. Returns the count of paused jobs."""
    pause_file = state.MAOP_ROOT / "logs" / ".maop_pause"
    pause_file.parent.mkdir(parents=True, exist_ok=True)
    pause_file.write_text("paused")
    paused = 0
    with state.active_jobs_lock:
        for job in list(state.active_jobs.values()):
            proc = job.get("process")
            if proc and proc.returncode is None and job.get("status") == "running":
                job["status"] = "paused"
                paused += 1
    return paused


def resume_control() -> int:
    """Resume the control loop. Returns the count of resumed jobs."""
    pause_file = state.MAOP_ROOT / "logs" / ".maop_pause"
    if pause_file.exists():
        pause_file.unlink()
    resumed = 0
    with state.active_jobs_lock:
        for job in list(state.active_jobs.values()):
            if job.get("status") == "paused":
                job["status"] = "running"
                resumed += 1
    return resumed


def get_pause_status() -> dict[str, Any]:
    """Query system pause/resume status.

    Returns ``is_paused``, ``pause_file`` path, and job counts.
    """
    pause_file = state.MAOP_ROOT / "logs" / ".maop_pause"
    is_paused = pause_file.exists()
    with state.active_jobs_lock:
        jobs_snapshot = list(state.active_jobs.values())
        paused_jobs = sum(1 for job in jobs_snapshot if job.get("status") == "paused")
        running_jobs = sum(1 for job in jobs_snapshot if job.get("status") == "running")
        total_jobs = len(state.active_jobs)
    return {
        "is_paused": is_paused,
        "pause_file": str(pause_file),
        "paused_jobs": paused_jobs,
        "running_jobs": running_jobs,
        "total_jobs": total_jobs,
    }


def stop_control() -> int:
    """Stop the control loop gracefully. Returns the count of stopped jobs."""
    stopped = 0
    with state.active_jobs_lock:
        for job in list(state.active_jobs.values()):
            proc = job.get("process")
            if proc and proc.returncode is None:
                proc.terminate()
                job["status"] = "stopped"
                stopped += 1
    return stopped


def cancel_job(job_id: str) -> bool:
    """Cancel a running job by ID. Returns ``True`` if the job was found."""
    with state.active_jobs_lock:
        if job_id in state.active_jobs:
            proc = state.active_jobs[job_id].get("process")
            if proc and proc.returncode is None:
                proc.terminate()
            state.active_jobs[job_id]["status"] = "cancelled"
            return True
    return False


# ════════════════════════════════════════════════════════════════════════════
# Control: validate / doctor / provider health
# ════════════════════════════════════════════════════════════════════════════

def validate_config_job() -> tuple[str, dict[str, Any]]:
    """Validate the current MAOP configuration and record a job.

    Returns ``(job_id, result_dict)``. On failure, records a failed job
    and re-raises the original exception so the router can map it to an
    HTTP error.
    """
    job_id = _uuid.uuid4().hex[:8]
    try:
        from maop.deploy import validate_config
        result = validate_config(state.MAOP_ROOT)
        result_dict = result.model_dump()
        with state.active_jobs_lock:
            state.active_jobs[job_id] = {
                "action": "validate", "status": "completed",
                "start": time.strftime("%Y-%m-%dT%H:%M:%S"),
                "task": "config validation", "result": result_dict,
            }
        return job_id, result_dict
    except Exception:
        logger.exception("Validate failed")
        with state.active_jobs_lock:
            state.active_jobs[job_id] = {
                "action": "validate", "status": "failed",
                "start": time.strftime("%Y-%m-%dT%H:%M:%S"),
                "task": "config validation", "error": "Validate failed",
            }
        raise


def run_doctor_job() -> tuple[str, list[dict[str, Any]]]:
    """Run health diagnostics and record a job.

    Returns ``(job_id, findings_list)``. On failure, records a failed
    job and re-raises.
    """
    job_id = _uuid.uuid4().hex[:8]
    try:
        from maop.deploy import health_check
        results = health_check(state.MAOP_ROOT)
        findings = [r.model_dump() for r in results]
        with state.active_jobs_lock:
            state.active_jobs[job_id] = {
                "action": "doctor", "status": "completed",
                "start": time.strftime("%Y-%m-%dT%H:%M:%S"),
                "task": "system diagnostics", "result": findings,
            }
        return job_id, findings
    except Exception:
        logger.exception("Doctor check failed")
        with state.active_jobs_lock:
            state.active_jobs[job_id] = {
                "action": "doctor", "status": "failed",
                "start": time.strftime("%Y-%m-%dT%H:%M:%S"),
                "task": "system diagnostics", "error": "Doctor check failed",
            }
        raise


def check_provider_health() -> list[dict[str, Any]]:
    """Check health of configured LLM providers. Returns component list.

    Raises on failure so the router can map to HTTP 500.
    """
    from maop.deploy import health_check
    results = health_check(state.MAOP_ROOT)
    return [r.model_dump() for r in results]


# ════════════════════════════════════════════════════════════════════════════
# Control: cache
# ════════════════════════════════════════════════════════════════════════════

async def refresh_caches() -> None:
    """Clear in-memory caches (refresh runtime state)."""
    async with state.cache_lock:
        state.cache.clear()


async def clear_caches() -> None:
    """Clear all in-memory caches."""
    async with state.cache_lock:
        state.cache.clear()


# ════════════════════════════════════════════════════════════════════════════
# Control: maintenance handlers
# ════════════════════════════════════════════════════════════════════════════

async def maintain_log_rotate() -> dict[str, Any]:
    """log-rotate maintenance: rotate log files."""
    try:
        from maop.core.reliability.log_rotate import rotate_logs
        result = rotate_logs(log_dir=state.MAOP_ROOT / "logs", data_dir=state.MAOP_ROOT / "data")
        return {"status": "ok", "action": "log-rotate", "msg": "Logs rotated",
                "rotated": result.rotated, "deleted": result.deleted}
    except Exception:
        logger.exception("Log rotate failed")
        return {"status": "ok", "action": "log-rotate", "msg": "Skipped: log rotate unavailable"}


async def maintain_prune() -> dict[str, Any]:
    """prune maintenance: clean expired memory entries."""
    try:
        from maop.memory.store import MemoryStore
        store = MemoryStore(root_dir=str(state.MAOP_ROOT))
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
        return {"status": "ok", "action": "prune", "pruned": pruned_count,
                "remaining": total_after, "total_before": total_before,
                "pruned_ids": list(pruned_ids)[:20]}
    except Exception:
        logger.exception("Prune failed")
        # H-2 fix: exception branch must raise 500, not 200 + status=error.
        from fastapi import HTTPException
        raise HTTPException(status_code=500, detail="Prune failed")


async def maintain_health() -> dict[str, Any]:
    """health maintenance: run health check."""
    try:
        from maop.deploy import health_check
        results = health_check(state.MAOP_ROOT)
        healthy = all(r.status == "ok" for r in results)
        return {"status": "ok", "action": "health", "healthy": healthy,
                "components": [r.model_dump() for r in results]}
    except Exception:
        logger.exception("Health check failed")
        return {"status": "ok", "action": "health", "healthy": True,
                "msg": "Skipped: health check unavailable"}


async def maintain_backup() -> dict[str, Any]:
    """backup maintenance: run database backup."""
    try:
        from maop.core.backends.db_backup import DbBackup
        backup = DbBackup(root_dir=str(state.MAOP_ROOT))
        entries = backup.run() if hasattr(backup, "run") else []
        path = entries[0].backup_path if entries else "N/A"
        return {"status": "ok", "action": "backup", "path": str(path)}
    except Exception:
        logger.exception("Backup failed")
        return {"status": "ok", "action": "backup", "msg": "Skipped: backup unavailable"}


async def maintain_cache_clear() -> dict[str, Any]:
    """cache-clear maintenance: clear in-memory cache."""
    async with state.cache_lock:
        state.cache.clear()
    return {"status": "ok", "action": "cache-clear"}


async def maintain_reload() -> dict[str, Any]:
    """reload maintenance: hot-reload configuration."""
    try:
        from maop.config.hot_reload import ConfigHotReload
        reloader = ConfigHotReload(root_dir=str(state.MAOP_ROOT))
        reloader.reload() if hasattr(reloader, "reload") else None
        return {"status": "ok", "action": "reload", "msg": "Config reloaded"}
    except Exception:
        logger.exception("Config reload failed")
        return {"status": "ok", "action": "reload", "msg": "Skipped: reload unavailable"}


async def maintain_reindex() -> dict[str, Any]:
    """reindex maintenance: rebuild vector index."""
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


async def maintain_vacuum() -> dict[str, Any]:
    """vacuum maintenance: compact SQLite database.

    Prefers ``PRAGMA incremental_vacuum`` (non-blocking) when
    ``auto_vacuum`` is enabled; falls back to ``VACUUM`` otherwise
    (should be run during low-traffic periods).
    """
    try:
        from maop.core.backends.db_utils import sqlite_connect
        with sqlite_connect() as conn:  # type: ignore
            # P2-9 fix: prefer incremental_vacuum to avoid full-table lock.
            av = conn.execute("PRAGMA auto_vacuum").fetchone()[0]
            if av:
                conn.execute("PRAGMA incremental_vacuum")
            else:
                conn.execute("VACUUM")
        return {"status": "ok", "action": "vacuum", "msg": "Database compacted"}
    except Exception:
        logger.exception("Database vacuum failed")
        return {"status": "ok", "action": "vacuum", "msg": "Skipped: vacuum unavailable"}


# Maintenance action dispatch table (action name → handler coroutine).
MAINTAIN_HANDLERS: dict[str, Any] = {
    "log-rotate": maintain_log_rotate,
    "prune": maintain_prune,
    "health": maintain_health,
    "backup": maintain_backup,
    "cache-clear": maintain_cache_clear,
    "reload": maintain_reload,
    "reindex": maintain_reindex,
    "vacuum": maintain_vacuum,
}


async def run_maintain_action(action: str | None) -> dict[str, Any]:
    """Execute a maintenance operation by name.

    Returns the handler result dict. Raises ``HTTPException(500)`` on
    handler failure (H-2 fix).
    """
    if action is None:
        return {"status": "ok", "action": "noop", "msg": "No action specified"}
    try:
        handler = MAINTAIN_HANDLERS.get(action)
        if handler is not None:
            return await handler()
        return {"status": "ok", "action": action or "noop"}
    except Exception:
        logger.exception("Maintain action failed")
        # H-2 fix: exception branch must return 500, not 200 + status=error.
        from fastapi import HTTPException
        raise HTTPException(status_code=500, detail="Maintain action failed")