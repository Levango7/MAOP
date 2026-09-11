"""V4 miscellaneous endpoints: subsystems, coordination, routing, security.

Endpoints:
    GET /api/subsystems           — subsystem availability report
    GET /api/coordination_report  — v4 team coordination report
    GET /api/routing              — v4 routing config
    GET /api/security/config      — v4 security subsystem availability
"""

from __future__ import annotations

import importlib
import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse

from maop.core.security.middleware import require_admin
from maop.dashboard.error_handler import handle_api_errors

from . import _deps

logger = logging.getLogger(__name__)

router = APIRouter()


# ── Subsystem Status ──────────────────────────────────────────────
@router.get("/api/subsystems")
@handle_api_errors
async def api_subsystems(request: Request) -> dict[str, Any]:
    require_admin(request)
    _deps.init_subsystems()
    subs = _deps.get_subsystems()
    result = {}
    for name, info in subs.items():
        result[name] = {
            "available": info.get("available", False),
            "module": info.get("module", ""),
            # P1 fix: 仅返回是否有错误，不透传异常字符串。
            "error": bool(info.get("error")),
        }
    # P2 fix: 统一响应格式为 {status, data}。
    return {
        "status": "ok",
        "data": {
            "subsystems": result,
            "count": len(result),
            "available": sum(1 for v in subs.values() if v.get("available")),
            "unavailable": sum(1 for v in subs.values() if not v.get("available")),
        },
    }


@router.get("/api/coordination_report")
@handle_api_errors
async def api_coordination_report_v4(request: Request) -> dict[str, Any]:
    require_admin(request)
    try:
        from maop.config.loader import ConfigLoader
        cfg = ConfigLoader(project_root=str(_deps.MAOP_ROOT)).load()
        teams = [
            {
                "name": n, "cli": ad.cli, "model": getattr(ad, "model", ""),
                "driver": ad.driver, "capabilities": ad.capabilities,
            }
            for n, ad in cfg.agents.items()
        ]
        # P2 fix: 统一响应格式为 {status, data}。
        return {"status": "ok", "data": {"teams": teams, "agent_count": len(teams)}}
    except Exception as exc:
        logger.error('Coordination report failed: %s', exc)
        # P3 fix: 异常分支格式与正常分支对齐 {status, data, error}。
        return {"status": "error", "data": {"teams": [], "agent_count": 0}, "error": "Coordination report unavailable"}


@router.get("/api/routing")
@handle_api_errors
async def api_routing_v4(request: Request) -> dict[str, Any]:
    require_admin(request)
    try:
        from maop.config.loader import ConfigLoader
        cfg = ConfigLoader(project_root=str(_deps.MAOP_ROOT)).load()
        routes = (
            [
                {
                    "key": r.routing_key, "agent": r.agent, "pattern": r.pattern,
                    "fallback": getattr(r, "fallback", ""),
                }
                for r in cfg.routes
            ]
            if hasattr(cfg, "routes")
            else []
        )
        # P2 fix: 统一响应格式为 {status, data}。
        return {"status": "ok", "data": {"routes": routes}}
    except Exception as exc:
        logger.error('Routing config failed: %s', exc)
        # P3 fix: 异常分支格式与正常分支对齐 {status, data, error}。
        return {"status": "error", "data": {"routes": []}, "error": "Routing config unavailable"}


@router.get("/api/security/config")
@handle_api_errors
async def api_security_config_v4(request: Request) -> dict[str, Any]:
    require_admin(request)
    result = {}
    for mod_name, mod_path, _cls_name in [
        ("tls", "maop.core.security.tls", "TLSSettings"),
        ("auth", "maop.core.security.auth", "AuthManager"),
        ("rate_limit", "maop.core.reliability.rate_limiter", "RateLimiter"),
        ("guardrail", "maop.core.security.guardrail", "Guardrail"),
        ("sandbox", "maop.core.security.sandbox", "SandboxManager"),
    ]:
        try:
            importlib.import_module(mod_path)
            result[mod_name] = True
        except Exception as exc:
            logger.warning('Failed to check subsystem availability: %s', exc)
            result[mod_name] = False
    # P2 fix: 统一响应格式为 {status, data}。
    return {"status": "ok", "data": result}
