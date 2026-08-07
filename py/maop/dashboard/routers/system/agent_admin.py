"""Agent configuration & upgrade endpoints.

Endpoints:
    GET  /api/agent/config         — list agents + routes from agents.yaml
    POST /api/agent/config/update  — update a single agent's config
    POST /api/agent/upgrade        — upgrade an agent CLI package
    GET  /api/agent/upgrade        — list agents with current/latest versions
"""

from __future__ import annotations

import asyncio
import logging
import shutil
import sys
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Request

from maop.core.security.middleware import require_admin
from maop.dashboard.error_handler import handle_api_errors

from . import _deps

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/api/agent/config")
@handle_api_errors
async def api_agent_config() -> dict[str, Any]:
    try:
        from maop.config.loader import ConfigLoader
        cfg = ConfigLoader(project_root=str(_deps.MAOP_ROOT)).load()
        agents = []
        for name, ad in cfg.agents.items():
            agents.append({
                "name": name, "cli": ad.cli, "driver": ad.driver,
                "model": getattr(ad, "model", ""),
                "timeout_s": ad.timeout_s, "capabilities": ad.capabilities,
                "description": ad.description, "fallback": getattr(ad, "fallback", ""),
            })
        routes = (
            [{"pattern": r.pattern, "agent": r.agent, "routing_key": r.routing_key} for r in cfg.routes]
            if hasattr(cfg, "routes")
            else []
        )
        return {"agents": agents, "routes": routes, "agent_count": len(agents)}
    except Exception as exc:
        logger.error('Agent config failed: %s', exc)
        return {"agents": [], "routes": [], "error": "Agent config failed"}


@router.post("/api/agent/config/update")
@handle_api_errors
async def api_agent_config_update(request: Request) -> dict[str, Any]:
    require_admin(request)
    body = await request.json()
    agent_name = body.get("agent", "")
    if not agent_name:
        raise HTTPException(400, "missing agent name")
    try:
        ypath = _deps.MAOP_ROOT / "config" / "agents.yaml"
        if not ypath.exists():
            ypath = _deps.MAOP_ROOT / "agents.yaml"
        if not ypath.exists():
            return {"status": "error", "error": "agents.yaml not found"}
        import yaml
        _text = await asyncio.to_thread(Path(ypath).read_text, encoding="utf-8")
        data = yaml.safe_load(_text)
        agents = data.get("agents", {})
        if agent_name not in agents:
            return {"status": "error", "error": f"Unknown agent: {agent_name}"}
        agent_cfg = agents[agent_name]

        # ── Schema validation: validate updates against AgentDef before writing ──
        from maop.config.loader import AgentDef
        merged = dict(agent_cfg)
        for key in ("model", "cli", "cli_args", "driver", "timeout_s", "description", "wrapper"):
            if key in body:
                merged[key] = body[key]
        if "capabilities" in body:
            caps = body["capabilities"]
            if not isinstance(caps, list):
                raise HTTPException(400, "capabilities must be a list of strings")
            for c in caps:
                if not isinstance(c, str):
                    raise HTTPException(400, "each capability must be a string")
            merged["capabilities"] = caps
        try:
            AgentDef(**merged)  # validate; raises ValidationError on bad input
        except HTTPException:
            raise
        except Exception as ve:
            raise HTTPException(400, f"Config validation failed: {ve}")

        for key in ("model", "cli", "cli_args", "driver", "timeout_s", "description", "wrapper"):
            if key in body:
                agent_cfg[key] = body[key]
        if "capabilities" in body:
            agent_cfg["capabilities"] = body["capabilities"]
        _dumped = yaml.dump(data, allow_unicode=True, default_flow_style=False)
        await asyncio.to_thread(Path(ypath).write_text, _dumped, encoding="utf-8")
        return {"status": "ok", "agent": agent_name, "config": agent_cfg}
    except HTTPException:
        raise
    except Exception:
        import logging
        logging.getLogger(__name__).exception("[system] Config update failed")
        return {"status": "error", "error": "Config update failed"}


# ── Agent Upgrade ─────────────────────────────────────────────────
@router.post("/api/agent/upgrade")
@handle_api_errors
async def api_agent_upgrade(request: Request, agent: str = "") -> dict[str, Any]:
    require_admin(request)
    agent_name = agent
    if not agent_name:
        try:
            body = await request.json()
            agent_name = body.get("agent", "")
        except Exception as exc:
            logger.warning('Failed to parse request body: %s', exc)
    if not agent_name:
        raise HTTPException(400, "missing agent name")
    try:
        from maop.config.loader import ConfigLoader
        cfg = ConfigLoader(project_root=str(_deps.MAOP_ROOT)).load()
        ad = cfg.agents.get(agent_name)
        if not ad:
            return {"status": "error", "error": f"agent {agent_name} not found"}
        cli_path = shutil.which(ad.cli) if ad.cli else None
        info = {
            "agent": agent_name, "cli": ad.cli, "cli_found": cli_path is not None,
            "cli_path": cli_path or "",
            "driver": ad.driver, "model": getattr(ad, "model", ""), "capabilities": ad.capabilities,
        }
        if cli_path:
            try:
                _rc, _out, _err = await _deps._run_subprocess([cli_path, "--version"], timeout=10)
                info["current_version"] = (_out or _err).strip()[:200]
            except Exception as exc:
                logger.warning("Failed to get current version: %s", exc)
                info["current_version"] = "unknown"
        if ad.cli:
            allowed = _deps._get_allowed_packages()
            if ad.cli not in allowed:
                info["upgradable"] = False
                info["upgrade_status"] = "blocked"
                info["upgrade_error"] = f"Package {ad.cli!r} not in allowed list"
            else:
                try:
                    _rc, _out, _err = await _deps._run_subprocess(
                        [sys.executable, "-m", "pip", "show", ad.cli], timeout=10
                    )
                    if _rc == 0:
                        info["upgradable"] = True
                        upgrade_proc = await asyncio.create_subprocess_exec(
                            sys.executable, "-m", "pip", "install", "--upgrade", ad.cli,
                            stdout=asyncio.subprocess.PIPE,
                            stderr=asyncio.subprocess.PIPE,
                        )
                        try:
                            upgrade_stdout, upgrade_stderr = await asyncio.wait_for(
                                upgrade_proc.communicate(), timeout=120
                            )
                        except asyncio.TimeoutError:
                            upgrade_proc.kill()
                            await upgrade_proc.wait()
                            return {"ok": False, "error": "pip install upgrade timed out (120s)"}
                        upgrade_r_stdout = upgrade_stdout.decode(errors="replace") if upgrade_stdout else ""
                        upgrade_r_stderr = upgrade_stderr.decode(errors="replace") if upgrade_stderr else ""
                        upgrade_r_returncode = upgrade_proc.returncode
                        info["upgrade_exit_code"] = upgrade_r_returncode
                        if upgrade_r_returncode == 0:
                            info["upgrade_status"] = "success"
                            info["upgrade_output"] = upgrade_r_stdout[-500:]
                            try:
                                _rc, _out, _err = await _deps._run_subprocess(
                                    [cli_path, "--version"], timeout=10  # type: ignore[list-item]
                                )
                                info["new_version"] = (_out or _err).strip()[:200]
                            except Exception as exc:
                                logger.warning("Failed to get new version: %s", exc)
                                info["new_version"] = "unknown"
                        else:
                            info["upgrade_status"] = "failed"
                            info["upgrade_output"] = (upgrade_r_stderr or upgrade_r_stdout)[-500:]
                    else:
                        info["upgradable"] = False
                        info["upgrade_status"] = "not_a_pip_package"
                except Exception as exc:
                    logger.error('Agent upgrade failed: %s', exc)
                    info["upgradable"] = False
                    info["upgrade_status"] = "error"
                    info["upgrade_error"] = "Agent upgrade failed"
        try:
            from maop.control.audit import AuditLevel, AuditLog
            AuditLog(_deps.MAOP_ROOT / "logs" / "audit.jsonl").log(
                action="agent.upgrade", actor="dashboard", target=agent_name,
                level=AuditLevel.INFO, detail=info,
            )
        except Exception as exc:
            logger.warning('Failed to log audit event: %s', exc)
        return {"status": "ok", "info": info}
    except Exception as exc:
        logger.error("Agent upgrade failed: %s", exc)
        return {"status": "error", "error": "Agent upgrade failed"}


@router.get("/api/agent/upgrade")
@handle_api_errors
async def api_agent_upgrade_get(agent: str = "") -> dict[str, Any]:
    try:
        from maop.config.loader import ConfigLoader
        cfg = ConfigLoader(project_root=str(_deps.MAOP_ROOT)).load()
        result = []
        for name, ad in cfg.agents.items():
            cli_path = shutil.which(ad.cli) if ad.cli else None
            current = ""
            if cli_path:
                try:
                    _rc, _out, _err = await _deps._run_subprocess([ad.cli, "--version"], timeout=5)
                    current = (_out or _err).strip()[:100]
                except Exception as exc:
                    logger.warning('Failed to get CLI version: %s', exc)
            latest = "?"
            if ad.cli:
                try:
                    _rc, _out, _err = await _deps._run_subprocess(
                        [sys.executable, "-m", "pip", "show", ad.cli], timeout=10
                    )
                    if _rc == 0:
                        for line in _out.split("\n"):
                            if line.startswith("Version:"):
                                latest = line.split(":", 1)[1].strip()
                                break
                except Exception as exc:
                    logger.warning('Failed to get pip package version: %s', exc)
            result.append({
                "name": name, "current": current, "latest": latest,
                "status": "ok" if cli_path else "unavailable",
            })
        return {"agents": result}
    except Exception as exc:
        logger.error('Agent upgrade list failed: %s', exc)
        return {"agents": [], "error": "Agent upgrade list failed"}