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

from fastapi import APIRouter

from maop.dashboard.error_handler import handle_api_errors

from . import _deps

logger = logging.getLogger(__name__)

router = APIRouter()


# ── Subsystem Status ──────────────────────────────────────────────
@router.get("/api/subsystems")
@handle_api_errors
async def api_subsystems() -> dict[str, Any]:
    _deps.init_subsystems()
    subs = _deps.get_subsystems()
    result = {}
    for name, info in subs.items():
        result[name] = {
            "available": info.get("available", False),
            "module": info.get("module", ""),
            "error": info.get("error"),
        }
    return {
        "subsystems": result,
        "count": len(result),
        "available": sum(1 for v in subs.values() if v.get("available")),
        "unavailable": sum(1 for v in subs.values() if not v.get("available")),
    }


@router.get("/api/coordination_report")
@handle_api_errors
async def api_coordination_report_v4() -> dict[str, Any]:
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
        return {"teams": teams, "agent_count": len(teams)}
    except Exception as exc:
        logger.error('Coordination report failed: %s', exc)
        return {"teams": [], "error": "Coordination report failed"}


@router.get("/api/routing")
@handle_api_errors
async def api_routing_v4() -> dict[str, Any]:
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
        return {"routes": routes}
    except Exception as exc:
        logger.error('Routing config failed: %s', exc)
        return {"routes": [], "error": "Routing config failed"}


@router.get("/api/security/config")
@handle_api_errors
async def api_security_config_v4() -> dict[str, Any]:
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
    return result