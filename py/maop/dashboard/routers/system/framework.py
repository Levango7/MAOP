"""Framework self-status / logging / config endpoints.

Endpoints:
    GET /api/framework/status   — framework version & module counts
    GET /api/framework/logs     — recent JSONL log entries
    GET /api/framework/config   — agents.yaml + routes summary
"""

from __future__ import annotations

import asyncio
import logging
import platform
import sys
import time
from typing import Any

from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse

from maop.dashboard.error_handler import handle_api_errors

from . import _deps

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/api/framework/status")
@handle_api_errors
async def api_framework_status(request: Request) -> dict[str, Any]:
    _deps.require_admin(request)
    try:
        from maop import __version__ as MAOP_ver
    except ImportError:
        MAOP_ver = "unknown"
    py_modules = sum(
        1
        for p in (_deps.MAOP_ROOT / "py" / "maop").rglob("*.py")
        if "__pycache__" not in str(p)
    )
    test_dir = _deps.MAOP_ROOT / "py" / "tests"
    test_files = sum(1 for p in test_dir.glob("test_*.py")) if test_dir.exists() else 0
    db_files = (
        [f.name for f in (_deps.MAOP_ROOT / "data").glob("*.db")]
        if (_deps.MAOP_ROOT / "data").exists()
        else []
    )
    return {
        "status": "ok",
        "version": MAOP_ver,
        "python": sys.version.split()[0],
        "platform": f"{platform.system()} {platform.machine()}",
        "py_modules": py_modules,
        "test_files": test_files,
        "db_files": db_files,
        "uptime_s": round(time.time() - _deps.start_time, 1),
        "root": str(_deps.MAOP_ROOT),
    }


@router.get("/api/framework/logs")
@handle_api_errors
async def api_framework_logs(request: Request, limit: int = Query(50)) -> dict[str, Any]:
    _deps.require_admin(request)
    logs = []
    log_dir = _deps.MAOP_ROOT / "logs"
    if log_dir.exists():
        for f in sorted(log_dir.glob("*.jsonl"), reverse=True):
            try:
                _text = await asyncio.to_thread(f.read_text, encoding="utf-8")
                lines = _text.strip().split("\n")
                for line in lines[-limit:]:
                    try:
                        import json as _json
                        logs.append(_json.loads(line))
                    except Exception as exc:
                        logger.warning('Failed to parse log line: %s', exc)
                if len(logs) >= limit:
                    logs = logs[:limit]
                    break
            except Exception as exc:
                logger.warning('Failed to read log file: %s', exc)
    if not logs:
        try:
            logs = await _deps.get_bridge().logs_get(name="dashboard", limit=limit)
        except Exception as exc:
            logger.warning('Failed to get logs from bridge: %s', exc)
    return {"status": "ok", "logs": logs, "count": len(logs)}


@router.get("/api/framework/config")
@handle_api_errors
async def api_framework_config(request: Request) -> dict[str, Any]:
    _deps.require_admin(request)
    try:
        from maop.config.loader import ConfigLoader
        cfg = ConfigLoader(project_root=str(_deps.MAOP_ROOT)).load()
        return {
            "status": "ok",
            "agents": {
                name: {
                    "cli": ad.cli,
                    "driver": ad.driver,
                    "model": getattr(ad, "model", ""),
                    "capabilities": ad.capabilities,
                }
                for name, ad in cfg.agents.items()
            },
            "routes": (
                [
                    {"pattern": r.pattern, "agent": r.agent, "routing_key": r.routing_key}
                    for r in cfg.routes
                ]
                if hasattr(cfg, "routes")
                else []
            ),
            "rules_count": len(cfg.rules) if hasattr(cfg, "rules") else 0,
        }
    except Exception as exc:
        logger.error('Framework config failed: %s', exc)
        # H1 fix: 统一错误响应——raise HTTPException 让 handle_api_errors 装饰器处理。
        raise HTTPException(
            status_code=500,
            detail="Framework config failed",
        )