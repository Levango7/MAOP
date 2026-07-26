"""Framework system, audit, agent config, overview, and workflow endpoints."""

from __future__ import annotations

from typing import Any

import asyncio
import importlib
import logging
import platform
import shutil
import sys
import time

from fastapi import APIRouter, HTTPException, Query, Request

from .state import MAOP_ROOT, get_bridge, active_jobs, init_subsystems, get_subsystems, start_time
from maop.core.middleware import require_admin
import uuid as _uuid

logger = logging.getLogger(__name__)


async def _run_subprocess(cmd: list[str], timeout: int = 10) -> tuple[int, str, str]:
    """M21 fix (Phase R7): async subprocess 替代 subprocess.run，避免阻塞事件循环。

    返回 (returncode, stdout, stderr)，超时或异常返回 (-1, "", error_msg)。
    """
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout_b, stderr_b = await asyncio.wait_for(
                proc.communicate(), timeout=timeout
            )
            return proc.returncode, stdout_b.decode("utf-8", errors="replace"), stderr_b.decode("utf-8", errors="replace")
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            return -1, "", f"timeout after {timeout}s"
    except Exception as exc:
        return -1, "", str(exc)

_HARDENED_ALLOWED_PACKAGES = frozenset({
    "MAOP", "MAOP-core", "openai", "anthropic", "sentence-transformers",
    "pydantic", "pydantic-settings", "fastapi", "uvicorn", "httpx",
    "yaml", "mmh3", "numpy",
})

_ALLOWED_PIP_PACKAGES: set[str] | None = None

def _get_allowed_packages() -> set[str]:
    global _ALLOWED_PIP_PACKAGES
    if _ALLOWED_PIP_PACKAGES is None:
        try:
            from maop.config.loader import ConfigLoader
            cfg = ConfigLoader(project_root=str(MAOP_ROOT)).load()
            dynamic = {ad.cli for ad in cfg.agents.values() if ad.cli}
        except Exception as exc:
            logger.warning('Failed to load config for allowed packages: %s', exc)
            dynamic = set()
        _ALLOWED_PIP_PACKAGES = dynamic & _HARDENED_ALLOWED_PACKAGES
    return _ALLOWED_PIP_PACKAGES

router = APIRouter()

# ── Subsystem Status ──────────────────────────────────────────────
@router.get("/api/subsystems")
async def api_subsystems() -> dict[str, Any]:
    init_subsystems()
    subs = get_subsystems()
    result = {}
    for name, info in subs.items():
        result[name] = {"available": info.get("available", False), "module": info.get("module", ""), "error": info.get("error")}
    return {"subsystems": result, "count": len(result),
            "available": sum(1 for v in subs.values() if v.get("available")),
            "unavailable": sum(1 for v in subs.values() if not v.get("available"))}

# ── Framework Self-Status ─────────────────────────────────────────
@router.get("/api/framework/status")
async def api_framework_status() -> dict[str, Any]:
    try:
        from maop import __version__ as MAOP_ver
    except ImportError:
        MAOP_ver = "unknown"
    py_modules = sum(1 for p in (MAOP_ROOT / "py" / "maop").rglob("*.py") if "__pycache__" not in str(p))
    test_dir = MAOP_ROOT / "py" / "tests"
    test_files = sum(1 for p in test_dir.glob("test_*.py")) if test_dir.exists() else 0
    db_files = [f.name for f in (MAOP_ROOT / "data").glob("*.db")] if (MAOP_ROOT / "data").exists() else []
    return {"version": MAOP_ver, "python": sys.version.split()[0],
            "platform": f"{platform.system()} {platform.machine()}",
            "py_modules": py_modules, "test_files": test_files, "db_files": db_files,
            "uptime_s": round(time.time() - start_time, 1), "root": str(MAOP_ROOT)}

# ── Framework Self-Logging ───────────────────────────────────────
@router.get("/api/framework/logs")
async def api_framework_logs(limit: int = Query(50)) -> dict[str, Any]:
    logs = []
    log_dir = MAOP_ROOT / "logs"
    if log_dir.exists():
        for f in sorted(log_dir.glob("*.jsonl"), reverse=True):
            try:
                lines = f.read_text(encoding="utf-8").strip().split("\n")
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
            logs = await get_bridge().logs_get(name="dashboard", limit=limit)
        except Exception as exc:
            logger.warning('Failed to get logs from bridge: %s', exc)
    return {"logs": logs, "count": len(logs)}

@router.get("/api/framework/config")
async def api_framework_config() -> dict[str, Any]:
    try:
        from maop.config.loader import ConfigLoader
        cfg = ConfigLoader(project_root=str(MAOP_ROOT)).load()
        return {"agents": {name: {"cli": ad.cli, "driver": ad.driver, "model": getattr(ad, "model", ""), "capabilities": ad.capabilities} for name, ad in cfg.agents.items()},
                "routes": [{"pattern": r.pattern, "agent": r.agent, "routing_key": r.routing_key} for r in cfg.routes] if hasattr(cfg, "routes") else [],
                "rules_count": len(cfg.rules) if hasattr(cfg, "rules") else 0}
    except Exception as exc:
        logger.error('Framework config failed: %s', exc)
        return {"error": "Framework config failed"}

# ── Agent Config ──────────────────────────────────────────────────
@router.get("/api/agent/config")
async def api_agent_config() -> dict[str, Any]:
    try:
        from maop.config.loader import ConfigLoader
        cfg = ConfigLoader(project_root=str(MAOP_ROOT)).load()
        agents = []
        for name, ad in cfg.agents.items():
            agents.append({"name": name, "cli": ad.cli, "driver": ad.driver, "model": getattr(ad, "model", ""),
                "timeout_s": ad.timeout_s, "capabilities": ad.capabilities, "description": ad.description, "fallback": getattr(ad, "fallback", "")})
        routes = [{"pattern": r.pattern, "agent": r.agent, "routing_key": r.routing_key} for r in cfg.routes] if hasattr(cfg, "routes") else []
        return {"agents": agents, "routes": routes, "agent_count": len(agents)}
    except Exception as exc:
        logger.error('Agent config failed: %s', exc)
        return {"agents": [], "routes": [], "error": "Agent config failed"}

@router.post("/api/agent/config/update")
async def api_agent_config_update(request: Request) -> dict[str, Any]:
    require_admin(request)
    body = await request.json()
    agent_name = body.get("agent", "")
    if not agent_name:
        raise HTTPException(400, "missing agent name")
    try:
        ypath = MAOP_ROOT / "config" / "agents.yaml"
        if not ypath.exists():
            ypath = MAOP_ROOT / "agents.yaml"
        if not ypath.exists():
            return {"status": "error", "error": "agents.yaml not found"}
        import yaml
        with open(ypath, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
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
        with open(ypath, "w", encoding="utf-8") as f:
            yaml.dump(data, f, allow_unicode=True, default_flow_style=False)
        return {"status": "ok", "agent": agent_name, "config": agent_cfg}
    except HTTPException:
        raise
    except Exception as e:
        import logging
        logging.getLogger(__name__).error("[system] Config update failed: %s", e, exc_info=True)
        return {"status": "error", "error": "Config update failed"}

# ── Agent Upgrade ─────────────────────────────────────────────────
@router.post("/api/agent/upgrade")
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
        cfg = ConfigLoader(project_root=str(MAOP_ROOT)).load()
        ad = cfg.agents.get(agent_name)
        if not ad:
            return {"status": "error", "error": f"agent {agent_name} not found"}
        cli_path = shutil.which(ad.cli) if ad.cli else None
        info = {"agent": agent_name, "cli": ad.cli, "cli_found": cli_path is not None, "cli_path": cli_path or "",
                "driver": ad.driver, "model": getattr(ad, "model", ""), "capabilities": ad.capabilities}
        if cli_path:
            try:
                _rc, _out, _err = await _run_subprocess([cli_path, "--version"], timeout=10)
                info["current_version"] = (_out or _err).strip()[:200]
            except Exception as exc:
                logger.warning("Failed to get current version: %s", exc)
                info["current_version"] = "unknown"
        if ad.cli:
            allowed = _get_allowed_packages()
            if ad.cli not in allowed:
                info["upgradable"] = False
                info["upgrade_status"] = "blocked"
                info["upgrade_error"] = f"Package {ad.cli!r} not in allowed list"
            else:
                try:
                    _rc, _out, _err = await _run_subprocess([sys.executable, "-m", "pip", "show", ad.cli], timeout=10)
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
                                _rc, _out, _err = await _run_subprocess([cli_path, "--version"], timeout=10)
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
            from maop.control.audit import AuditLog, AuditLevel
            AuditLog(MAOP_ROOT / "logs" / "audit.jsonl").log(action="agent.upgrade", actor="dashboard", target=agent_name, level=AuditLevel.INFO, detail=info)
        except Exception as exc:
            logger.warning('Failed to log audit event: %s', exc)
        return {"status": "ok", "info": info}
    except Exception as exc:
        logger.error("Agent upgrade failed: %s", exc)
        return {"status": "error", "error": "Agent upgrade failed"}

@router.get("/api/agent/upgrade")
async def api_agent_upgrade_get(agent: str = "") -> dict[str, Any]:
    try:
        from maop.config.loader import ConfigLoader
        cfg = ConfigLoader(project_root=str(MAOP_ROOT)).load()
        result = []
        for name, ad in cfg.agents.items():
            cli_path = shutil.which(ad.cli) if ad.cli else None
            current = ""
            if cli_path:
                try:
                    _rc, _out, _err = await _run_subprocess([ad.cli, "--version"], timeout=5)
                    current = (_out or _err).strip()[:100]
                except Exception as exc:
                    logger.warning('Failed to get CLI version: %s', exc)
            latest = "?"
            if ad.cli:
                try:
                    _rc, _out, _err = await _run_subprocess([sys.executable, "-m", "pip", "show", ad.cli], timeout=10)
                    if _rc == 0:
                        for line in _out.split("\n"):
                            if line.startswith("Version:"):
                                latest = line.split(":", 1)[1].strip()
                                break
                except Exception as exc:
                    logger.warning('Failed to get pip package version: %s', exc)
            result.append({"name": name, "current": current, "latest": latest, "status": "ok" if cli_path else "unavailable"})
        return {"agents": result}
    except Exception as exc:
        logger.error('Agent upgrade list failed: %s', exc)
        return {"agents": [], "error": "Agent upgrade list failed"}

# ── Workflow Management ───────────────────────────────────────────
@router.get("/api/workflow/list")
async def api_workflow_list() -> dict[str, Any]:
    """List available workflows from config directory."""
    cfg_dir = MAOP_ROOT / "config"
    wfs = []
    for f in cfg_dir.glob("*.yaml"):
        if "workflow" in f.name.lower() or "pipeline" in f.name.lower():
            wfs.append({"name": f.stem, "file": str(f)})
    return {"workflows": wfs, "count": len(wfs)}

@router.post("/api/workflow/run")
async def api_workflow_run(request: Request) -> dict[str, Any]:
    require_admin(request)
    import asyncio
    body = await request.json()
    wf_name = body.get("name", "")
    task = body.get("task", "")
    if not wf_name:
        raise HTTPException(400, "missing workflow name")

    # Sanitize: only allow alphanumeric, spaces, dots, hyphens, underscores
    import re as _re
    if not _re.match(r'^[a-zA-Z0-9_\-\s\.]+$', wf_name):
        raise HTTPException(400, "invalid workflow name: only alphanumeric, spaces, dots, hyphens, underscores allowed")
    if task and not _re.match(r'^[a-zA-Z0-9_\-\s\.]+$', task):
        raise HTTPException(400, "invalid task name: only alphanumeric, spaces, dots, hyphens, underscores allowed")

    job_id = _uuid.uuid4().hex[:8]
    # Use a proper Python module approach instead of injecting code into -c
    proc = await asyncio.create_subprocess_exec(
        sys.executable, "-m", "maop.cli", "run",
        "--task", wf_name if not task else task,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    active_jobs[job_id] = {"action": "workflow", "status": "running", "start": time.strftime("%Y-%m-%dT%H:%M:%S"), "task": wf_name, "process": proc}
    return {"job_id": job_id, "status": "started", "workflow": wf_name}

# ── Overview (v4 compat) ──────────────────────────────────────────
_overview_cache: dict[str, Any] = {}
_OVERVIEW_CACHE_TTL = 60.0
# P2-9 fix: cache file counts separately (rarely change, expensive to compute)
_file_counts_cache: dict[str, Any] = {}
_FILE_COUNTS_CACHE_TTL = 600.0  # 10 minutes

@router.get("/api/overview")
async def api_overview(request: Request) -> dict[str, Any]:
    import time as _time
    now = _time.monotonic()
    cached = _overview_cache.get("data")
    cached_at = _overview_cache.get("ts", 0)
    if cached and now - cached_at < _OVERVIEW_CACHE_TTL:
        return cached
    try:
        b = get_bridge()
        rpt = await b.report(hours=48)
        agents = await b.agent_stats()
        ts = await b.timeseries(hours=168)
        live = await b.live()
        fails = await b.failures()
        agent_count = len(agents.get("agents", [])) if isinstance(agents, dict) else (len(agents) if isinstance(agents, list) else 0)
        # P2-9 fix: cache file counts (10min TTL) to avoid per-request file traversal
        _fc_now = _time.monotonic()
        _fc_cached = _file_counts_cache.get("data")
        _fc_ts = _file_counts_cache.get("ts", 0)
        if _fc_cached and _fc_now - _fc_ts < _FILE_COUNTS_CACHE_TTL:
            source_files = _fc_cached["source_files"]
            code_lines = _fc_cached["code_lines"]
            test_files = _fc_cached["test_files"]
            tests_total = _fc_cached["tests_total"]
        else:
            py_dir = MAOP_ROOT / "py" / "maop"
            source_files = sum(1 for p in py_dir.rglob("*.py") if "__pycache__" not in str(p))
            code_lines = 0
            for p in py_dir.rglob("*.py"):
                if "__pycache__" in str(p):
                    continue
                try:
                    code_lines += sum(1 for _ in open(p, encoding="utf-8", errors="replace"))
                except Exception as exc:
                    logger.warning('Failed to count code lines: %s', exc)
            test_dir = MAOP_ROOT / "py" / "tests"
            test_files = sum(1 for p in test_dir.glob("test_*.py")) if test_dir.exists() else 0
            tests_total = 0
            if test_dir.exists():
                for tf in test_dir.glob("test_*.py"):
                    try:
                        tests_total += tf.read_text(encoding="utf-8", errors="replace").count("def test_")
                    except Exception as exc:
                        logger.warning('Failed to count test functions: %s', exc)
            _file_counts_cache["data"] = {
                "source_files": source_files, "code_lines": code_lines,
                "test_files": test_files, "tests_total": tests_total,
            }
            _file_counts_cache["ts"] = _fc_now
        # Count actual API endpoints from FastAPI app routes
        api_endpoints = sum(1 for r in request.app.routes if getattr(r, 'path', '').startswith('/api/'))
        result = {"agents_total": agent_count, "modules_total": source_files, "tests_total": tests_total,
                "success_rate": rpt.get("success_rate", 0) if isinstance(rpt, dict) else 0,
                "delegations_total": rpt.get("total_delegations", rpt.get("total", 0)) if isinstance(rpt, dict) else 0,
                "avg_latency_ms": rpt.get("avg_latency_ms", 0) if isinstance(rpt, dict) else 0,
                "recent_delegations": live.get("recent_delegations", []) if isinstance(live, dict) else (live if isinstance(live, list) else []),
                "fail_ranking": fails if isinstance(fails, list) else [],
                "source_files": source_files, "code_lines": code_lines, "test_files": test_files,
                "api_endpoints": api_endpoints,
                "python_ver": f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}",
                "platform": f"{platform.system()} {platform.machine()}",
                "version": "4.0", "uptime": f"{round(time.time() - start_time)}s",
                "timeseries": ts if isinstance(ts, list) else None}
        _overview_cache["data"] = result
        _overview_cache["ts"] = now
        return result
    except Exception as exc:
        logger.error('Overview failed: %s', exc)
        return {"error": "Overview failed", "agents_total": 0, "modules_total": 0, "tests_total": 0}

@router.get("/api/coordination_report")
async def api_coordination_report_v4() -> dict[str, Any]:
    try:
        from maop.config.loader import ConfigLoader
        cfg = ConfigLoader(project_root=str(MAOP_ROOT)).load()
        teams = [{"name": n, "cli": ad.cli, "model": getattr(ad,"model",""), "driver": ad.driver, "capabilities": ad.capabilities} for n, ad in cfg.agents.items()]
        return {"teams": teams, "agent_count": len(teams)}
    except Exception as exc:
        logger.error('Coordination report failed: %s', exc)
        return {"teams": [], "error": "Coordination report failed"}

@router.get("/api/workflows")
async def api_workflows_v4() -> dict[str, Any]:
    try:
        wfs = []
        wf_dir = MAOP_ROOT / "config" / "workflows"
        if not wf_dir.exists():
            wf_dir = MAOP_ROOT / "workflows"
        if wf_dir.exists():
            for f in sorted(wf_dir.glob("*.yaml")):
                wfs.append({"name": f.stem, "type": "yaml", "file": str(f)})
            for f in sorted(wf_dir.glob("*.yml")):
                wfs.append({"name": f.stem, "type": "yaml", "file": str(f)})
        if not wfs:
            wfs = [{"name": "analyze", "type": "engine", "description": "Analyze task and route to agent"},
                   {"name": "plan", "type": "engine", "description": "Generate execution plan via DAG"},
                   {"name": "execute", "type": "engine", "description": "Execute plan with agent delegation"},
                   {"name": "verify", "type": "engine", "description": "Three-gate verification (lint/test/semantic)"},
                   {"name": "evolve", "type": "engine", "description": "Self-evolution and feedback loop"}]
        return {"workflows": wfs, "count": len(wfs)}
    except Exception as exc:
        logger.error('Workflows list failed: %s', exc)
        return {"workflows": [], "count": 0, "error": "Workflows list failed"}

@router.get("/api/routing")
async def api_routing_v4() -> dict[str, Any]:
    try:
        from maop.config.loader import ConfigLoader
        cfg = ConfigLoader(project_root=str(MAOP_ROOT)).load()
        routes = [{"key": r.routing_key, "agent": r.agent, "pattern": r.pattern, "fallback": getattr(r, "fallback", "")} for r in cfg.routes] if hasattr(cfg, "routes") else []
        return {"routes": routes}
    except Exception as exc:
        logger.error('Routing config failed: %s', exc)
        return {"routes": [], "error": "Routing config failed"}

@router.get("/api/security/config")
async def api_security_config_v4() -> dict[str, Any]:
    result = {}
    for mod_name, mod_path, cls_name in [("tls", "maop.core.tls", "TLSSettings"), ("auth", "maop.core.auth", "AuthManager"),
        ("rate_limit", "maop.core.rate_limiter", "RateLimiter"), ("guardrail", "maop.core.guardrail", "Guardrail"), ("sandbox", "maop.core.sandbox", "SandboxManager")]:
        try:
            importlib.import_module(mod_path)
            result[mod_name] = True
        except Exception as exc:
            logger.warning('Failed to check subsystem availability: %s', exc)
            result[mod_name] = False
    return result

# ── Audit / Control Plane ─────────────────────────────────────────
@router.get("/api/audit/events")
async def api_audit_events(limit: int = Query(100)) -> dict[str, Any]:
    try:
        from maop.control.audit import AuditLog
        events = AuditLog(MAOP_ROOT / "logs" / "audit.jsonl").read_recent(limit=limit)
        return {"events": [e.model_dump() for e in events], "count": len(events)}
    except Exception as exc:
        logger.error('Audit events failed: %s', exc)
        return {"events": [], "count": 0, "error": "Audit events failed"}

@router.get("/api/audit/summary")
async def api_audit_summary() -> dict[str, Any]:
    try:
        from maop.control.audit import AuditLog
        log = AuditLog(MAOP_ROOT / "logs" / "audit.jsonl")
        events = log.read_recent(limit=500)
        by_action: dict[str, int] = {}
        by_actor: dict[str, int] = {}
        for e in events:
            by_action[e.action] = by_action.get(e.action, 0) + 1
            by_actor[e.actor] = by_actor.get(e.actor, 0) + 1
        return {"total": len(events), "by_action": by_action, "by_actor": by_actor}
    except Exception as exc:
        logger.error('Audit summary failed: %s', exc)
        return {"total": 0, "by_action": {}, "by_actor": {}, "error": str(exc)}

@router.get("/api/audit/filter")
async def api_audit_filter(action: str = "", actor: str = "", target: str = "", limit: int = Query(50)) -> dict[str, Any]:
    try:
        from maop.control.audit import AuditLog
        events = AuditLog(MAOP_ROOT / "logs" / "audit.jsonl").filter(action=action, actor=actor, target=target, limit=limit)
        return {"events": [e.model_dump() for e in events], "count": len(events)}
    except Exception as exc:
        logger.error('Audit filter failed: %s', exc)
        return {"events": [], "count": 0, "error": "Audit filter failed"}

# ── System Resources & Diagnostics (C-7 修复) ─────────────────────
def _dir_size_mb(path) -> float:
    """递归计算目录大小（MB）。"""
    p = path
    if not p.exists():
        return 0.0
    if p.is_file():
        return p.stat().st_size / 1024 / 1024
    total = 0.0
    for f in p.rglob("*"):
        if f.is_file():
            try:
                total += f.stat().st_size
            except OSError as exc:
                logger.warning('Failed to stat %s: %s', f, exc)
    return total / 1024 / 1024


def _pct(used: float, total: float) -> float:
    """计算使用率（0-1 之间），total <= 0 时返回 0。"""
    if total <= 0:
        return 0.0
    return round(min(used / total, 1.0), 4)


@router.get("/api/system/resources")
async def api_system_resources(request: Request) -> dict[str, Any]:
    """返回系统资源使用情况：内存存储 / SQLite DB / 向量索引 / 日志文件。

    每项返回 {pct, used_mb, total_mb}；失败时该项追加 {error} 字段，pct 降级为 0。
    psutil 未安装时 memory_store 降级为 0，并在该项标记 error。
    """
    require_admin(request)

    # ── Memory Store: 进程 RSS，总内存上限默认 100MB ──
    mem_total_mb = 100.0
    mem_used_mb = 0.0
    mem_error: str | None = None
    try:
        import psutil  # type: ignore[import-not-found]
        proc = psutil.Process()
        mem_used_mb = proc.memory_info().rss / 1024 / 1024
    except ImportError:
        mem_error = "psutil not available"
    except Exception as exc:
        mem_error = str(exc)[:200]
    memory_store: dict[str, Any] = {
        "pct": _pct(mem_used_mb, mem_total_mb),
        "used_mb": round(mem_used_mb, 2),
        "total_mb": mem_total_mb,
    }
    if mem_error:
        memory_store["error"] = mem_error

    # ── SQLite DB: data 目录下所有 .db 文件大小总和，上限默认 50MB ──
    sqlite_total_mb = 50.0
    sqlite_used_mb = 0.0
    sqlite_error: str | None = None
    try:
        data_dir = MAOP_ROOT / "data"
        if data_dir.exists():
            for f in data_dir.glob("*.db"):
                if f.is_file():
                    try:
                        sqlite_used_mb += f.stat().st_size / 1024 / 1024
                    except OSError as exc:
                        logger.warning('Failed to stat %s: %s', f, exc)
    except Exception as exc:
        sqlite_error = str(exc)[:200]
    sqlite_db: dict[str, Any] = {
        "pct": _pct(sqlite_used_mb, sqlite_total_mb),
        "used_mb": round(sqlite_used_mb, 2),
        "total_mb": sqlite_total_mb,
    }
    if sqlite_error:
        sqlite_db["error"] = sqlite_error

    # ── Vector Index: data 目录下向量相关文件大小，上限默认 100MB ──
    vector_total_mb = 100.0
    vector_used_mb = 0.0
    vector_error: str | None = None
    try:
        data_dir = MAOP_ROOT / "data"
        if data_dir.exists():
            for pattern in ("*.vec", "*.index", "vector*.db", "vectors*.db", "*.faiss"):
                for f in data_dir.glob(pattern):
                    if f.is_file():
                        try:
                            vector_used_mb += f.stat().st_size / 1024 / 1024
                        except OSError as exc:
                            logger.warning('Failed to stat %s: %s', f, exc)
    except Exception as exc:
        vector_error = str(exc)[:200]
    vector_index: dict[str, Any] = {
        "pct": _pct(vector_used_mb, vector_total_mb),
        "used_mb": round(vector_used_mb, 2),
        "total_mb": vector_total_mb,
    }
    if vector_error:
        vector_index["error"] = vector_error

    # ── Log Files: logs 目录总大小，上限默认 50MB ──
    log_total_mb = 50.0
    log_used_mb = 0.0
    log_error: str | None = None
    try:
        log_dir = MAOP_ROOT / "logs"
        log_used_mb = _dir_size_mb(log_dir)
    except Exception as exc:
        log_error = str(exc)[:200]
    log_files: dict[str, Any] = {
        "pct": _pct(log_used_mb, log_total_mb),
        "used_mb": round(log_used_mb, 2),
        "total_mb": log_total_mb,
    }
    if log_error:
        log_files["error"] = log_error

    return {
        "memory_store": memory_store,
        "sqlite_db": sqlite_db,
        "vector_index": vector_index,
        "log_files": log_files,
    }


@router.get("/api/system/diagnostics")
async def api_system_diagnostics(request: Request) -> dict[str, Any]:
    """运行系统诊断：database / agent_registry / memory_store / vector_index / config_loader / audit_log。

    每项返回 {ok, result}；检查失败返回 {ok: false, result: str(exc)[:200]}。
    Personal 版 audit_log 降级为 {ok: true, result: "N/A (personal edition)"}。
    """
    require_admin(request)

    result: dict[str, dict[str, Any]] = {}

    # ── Database: 尝试 SELECT 1 ──
    try:
        import sqlite3
        db_path = MAOP_ROOT / "data" / "maop.db"
        if not db_path.exists():
            data_dir = MAOP_ROOT / "data"
            db_files = list(data_dir.glob("*.db")) if data_dir.exists() else []
            if db_files:
                db_path = db_files[0]
        if db_path.exists():
            conn = sqlite3.connect(str(db_path), timeout=2)
            try:
                cur = conn.execute("SELECT 1")
                cur.fetchone()
                result["database"] = {"ok": True, "result": f"OK ({db_path.name})"}
            finally:
                conn.close()
        else:
            result["database"] = {"ok": True, "result": "OK (no db file yet)"}
    except Exception as exc:
        result["database"] = {"ok": False, "result": str(exc)[:200]}

    # ── Agent Registry: 调用 list_agents() 返回数量 ──
    try:
        from maop.core.agent_registry import AgentRegistry
        reg = AgentRegistry()
        agents = reg.list_agents() if hasattr(reg, "list_agents") else []
        count = len(agents) if hasattr(agents, "__len__") else 0
        result["agent_registry"] = {"ok": True, "result": f"{count} agents"}
    except Exception as exc:
        result["agent_registry"] = {"ok": False, "result": str(exc)[:200]}

    # ── Memory Store: try import 检查 ──
    try:
        from maop.core.cache import get_cache  # noqa: F401
        result["memory_store"] = {"ok": True, "result": "OK"}
    except Exception as exc:
        result["memory_store"] = {"ok": False, "result": str(exc)[:200]}

    # ── Vector Index: try import ──
    try:
        from maop.core.vector import VectorStore  # noqa: F401
        result["vector_index"] = {"ok": True, "result": "OK"}
    except Exception as exc:
        result["vector_index"] = {"ok": False, "result": str(exc)[:200]}

    # ── Config Loader: try 加载 agents.yaml ──
    try:
        from maop.config.loader import ConfigLoader
        cfg = ConfigLoader(project_root=str(MAOP_ROOT)).load()
        agent_count = len(cfg.agents) if hasattr(cfg, "agents") else 0
        result["config_loader"] = {"ok": True, "result": f"OK ({agent_count} agents configured)"}
    except Exception as exc:
        result["config_loader"] = {"ok": False, "result": str(exc)[:200]}

    # ── Audit Log: try import maop.enterprise.audit；Personal 版降级 ──
    try:
        from maop.enterprise.audit import AuditLog as _EntAudit  # noqa: F401
        result["audit_log"] = {"ok": True, "result": "OK (enterprise)"}
    except ImportError:
        result["audit_log"] = {"ok": True, "result": "N/A (personal edition)"}
    except Exception as exc:
        result["audit_log"] = {"ok": False, "result": str(exc)[:200]}

    return result
