"""Workflow management endpoints.

Endpoints:
    GET  /api/workflow/list — list workflow YAML files in config/
    POST /api/workflow/run  — launch a workflow via `maop cli run`
    GET  /api/workflows     — v4 workflows list (config/workflows/ or defaults)
"""

from __future__ import annotations

import asyncio
import logging
import re as _re
import sys
import time
import uuid as _uuid
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse

from maop.core.security.middleware import require_admin
from maop.dashboard.error_handler import handle_api_errors

from . import _deps

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/api/workflow/list")
@handle_api_errors
async def api_workflow_list(request: Request) -> dict[str, Any]:
    """List available workflows from config directory."""
    require_admin(request)
    cfg_dir = _deps.MAOP_ROOT / "config"
    wfs = []
    for f in cfg_dir.glob("*.yaml"):
        if "workflow" in f.name.lower() or "pipeline" in f.name.lower():
            wfs.append({"name": f.stem, "file": str(f)})
    return {"workflows": wfs, "count": len(wfs)}


@router.post("/api/workflow/run")
@handle_api_errors
async def api_workflow_run(request: Request) -> dict[str, Any]:
    require_admin(request)
    # P0 fix: JSON 解析失败时返回 400 而非 500。
    try:
        body = await request.json()
    except Exception as exc:
        logger.warning("[workflow] Invalid JSON in workflow run request: %s", exc)
        raise HTTPException(status_code=400, detail="Invalid JSON body") from exc
    wf_name = body.get("name", "")
    task = body.get("task", "")
    if not wf_name:
        raise HTTPException(400, "missing workflow name")

    # Sanitize: only allow alphanumeric, spaces, dots, hyphens, underscores
    if not _re.match(r'^[a-zA-Z0-9_\-\s\.]+$', wf_name):
        raise HTTPException(
            400,
            "invalid workflow name: only alphanumeric, spaces, dots, hyphens, underscores allowed",
        )
    if task and not _re.match(r'^[a-zA-Z0-9_\-\s\.]+$', task):
        raise HTTPException(
            400,
            "invalid task name: only alphanumeric, spaces, dots, hyphens, underscores allowed",
        )

    job_id = _uuid.uuid4().hex[:8]
    # Use a proper Python module approach instead of injecting code into -c
    proc = await asyncio.create_subprocess_exec(
        sys.executable, "-m", "maop.cli", "run",
        "--task", task or wf_name,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    # H4 fix: 保存 task 引用到 active_jobs 字典，避免未保存的 task 引用被
    # GC 回收时触发 "Task was destroyed but it is pending" warning。
    _drain_task = asyncio.create_task(proc.communicate())
    # H3 fix: 用 active_jobs_lock 保护并发读写。
    with _deps.active_jobs_lock:
        _deps.active_jobs[job_id] = {
            "action": "workflow", "status": "running",
            "start": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "task": wf_name, "process": proc, "_drain_task": _drain_task,
        }
    return {"job_id": job_id, "status": "started", "workflow": wf_name}


@router.get("/api/workflows")
@handle_api_errors
async def api_workflows_v4(request: Request) -> dict[str, Any]:
    require_admin(request)
    try:
        wfs = []
        wf_dir = _deps.MAOP_ROOT / "config" / "workflows"
        if not wf_dir.exists():
            wf_dir = _deps.MAOP_ROOT / "workflows"
        if wf_dir.exists():
            for f in sorted(wf_dir.glob("*.yaml")):
                wfs.append({"name": f.stem, "type": "yaml", "file": str(f)})
            for f in sorted(wf_dir.glob("*.yml")):
                wfs.append({"name": f.stem, "type": "yaml", "file": str(f)})
        if not wfs:
            wfs = [
                {"name": "analyze", "type": "engine", "description": "Analyze task and route to agent"},
                {"name": "plan", "type": "engine", "description": "Generate execution plan via DAG"},
                {"name": "execute", "type": "engine", "description": "Execute plan with agent delegation"},
                {"name": "verify", "type": "engine", "description": "Three-gate verification (lint/test/semantic)"},
                {"name": "evolve", "type": "engine", "description": "Self-evolution and feedback loop"},
            ]
        return {"workflows": wfs, "count": len(wfs)}
    except Exception as exc:
        logger.error('Workflows list failed: %s', exc)
        # H1 fix: 统一错误响应——raise HTTPException 让 handle_api_errors 装饰器处理。
        raise HTTPException(
            status_code=500,
            detail="Workflows list failed",
        )