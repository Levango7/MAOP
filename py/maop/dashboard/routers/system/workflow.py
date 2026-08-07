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

from maop.core.security.middleware import require_admin
from maop.dashboard.error_handler import handle_api_errors

from . import _deps

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/api/workflow/list")
@handle_api_errors
async def api_workflow_list() -> dict[str, Any]:
    """List available workflows from config directory."""
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
    body = await request.json()
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
    _deps.active_jobs[job_id] = {
        "action": "workflow", "status": "running",
        "start": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "task": wf_name, "process": proc,
    }
    return {"job_id": job_id, "status": "started", "workflow": wf_name}


@router.get("/api/workflows")
@handle_api_errors
async def api_workflows_v4() -> dict[str, Any]:
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
        return {"workflows": [], "count": 0, "error": "Workflows list failed"}