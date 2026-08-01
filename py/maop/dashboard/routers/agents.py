"""MAOP Dashboard — Agent Platform Management API endpoints."""

from __future__ import annotations


from typing import Any

from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from maop.core.middleware import require_admin
from maop.dashboard.error_handler import handle_api_errors
from maop.dashboard.routers.state import MAOP_ROOT

router = APIRouter(prefix="/api/agents", tags=["agents"])


class RegisterAgentRequest(BaseModel):
    name: str = Field(max_length=100)
    cli_path: str = Field(default="", max_length=500)
    capabilities: list[str] = Field(default_factory=list)
    provider: str = Field(default="", max_length=100)
    description: str = Field(default="", max_length=500)
    model: str = Field(default="", max_length=100)
    driver: str = Field(default="cli", max_length=50)
    cli_args: str = Field(default="", max_length=500)
    timeout_s: int = Field(default=120, ge=1, le=3600)


_instance_cache: dict[str, Any] = {}


def _get_scanner():
    if "scanner" not in _instance_cache:
        from maop.core.agent_scanner import AgentScanner
        root = MAOP_ROOT
        _instance_cache["scanner"] = AgentScanner(root_dir=str(root))
    return _instance_cache["scanner"]


def _get_registry():
    if "registry" not in _instance_cache:
        from maop.core.agent_registry import AgentRegistry
        root = MAOP_ROOT
        _instance_cache["registry"] = AgentRegistry(root_dir=str(root))
    return _instance_cache["registry"]


def _get_matcher():
    if "matcher" not in _instance_cache:
        from maop.core.agent_registry import AgentRegistry
        from maop.core.capability_matcher import CapabilityMatcher
        root = MAOP_ROOT
        registry = AgentRegistry(root_dir=str(root))
        _instance_cache["matcher"] = CapabilityMatcher(registry=registry)
    return _instance_cache["matcher"]


def _get_repair():
    if "repair" not in _instance_cache:
        from maop.core.agent_repair import AgentRepair
        root = MAOP_ROOT
        _instance_cache["repair"] = AgentRepair(root_dir=str(root))
    return _instance_cache["repair"]


def _get_memory():
    if "memory" not in _instance_cache:
        from maop.core.agent_memory import AgentMemory
        root = MAOP_ROOT
        _instance_cache["memory"] = AgentMemory(root_dir=str(root))
    return _instance_cache["memory"]


def _get_evolution():
    if "evolution" not in _instance_cache:
        from maop.core.agent_evolution import AgentEvolution
        root = MAOP_ROOT
        _instance_cache["evolution"] = AgentEvolution(root_dir=str(root))
    return _instance_cache["evolution"]


def _get_agent_config(agent_name: str):
    """从 agents.yaml 加载指定 agent 的配置。"""
    try:
        from maop.config.loader import ConfigLoader
        root = MAOP_ROOT
        cfg = ConfigLoader(project_root=str(root)).load()
        return cfg.agents.get(agent_name)
    except Exception:
        return None


@router.get("")
@handle_api_errors
async def list_agents(
    enabled_only: bool = Query(False, description="Only enabled agents"),
    healthy_only: bool = Query(False, description="Only healthy agents"),
    capability: str = Query("", description="Filter by capability"),
    provider: str = Query("", description="Filter by provider"),
):
    registry = _get_registry()
    agents = registry.list_agents(
        enabled_only=enabled_only,
        healthy_only=healthy_only,
        capability=capability or "",
        provider=provider or "",
    )
    return {"agents": [a.model_dump() for a in agents]}


@router.get("/routes")
@handle_api_errors
async def get_agent_routes():
    """返回路由配置 (capability → primary/fallback/tertiary)，合并 agent 信息。

    数据来源:
      1. registry.list_agents() — 提供 name/provider/enabled/model/capabilities
      2. config/agents.yaml routing: 块 — 提供 capability 路由规则

    每个 route 包含 Agents.vue 期望的 name/provider/enabled 字段。
    若 routing 配置为空，则从 agent 列表构建 routes。
    """
    import yaml as _yaml

    # 1. 从 registry 获取 agent 信息
    agent_map: dict[str, dict] = {}
    try:
        registry = _get_registry()
        for a in registry.list_agents():
            name = getattr(a, "name", "") or ""
            if not name:
                continue
            agent_map[name] = {
                "name": name,
                "provider": getattr(a, "provider", "") or "",
                "enabled": getattr(a, "enabled", True),
                "model": getattr(a, "model", "") or "",
                "capabilities": getattr(a, "capabilities", []) or [],
            }
    except Exception:
        pass

    # 2. 读 agents.yaml routing 配置
    yaml_path = MAOP_ROOT / "config" / "agents.yaml"
    if not yaml_path.exists():
        yaml_path = MAOP_ROOT / "agents.yaml"

    agents_cfg: dict = {}
    routing_cfg: dict = {}
    if yaml_path.exists():
        try:
            data = _yaml.safe_load(yaml_path.read_text(encoding="utf-8")) or {}
            agents_cfg = data.get("agents", {}) or {}
            routing_cfg = data.get("routing", {}) or {}
        except Exception:
            pass

    def _agent_model(agent_name: str) -> str:
        ad = agents_cfg.get(agent_name, {})
        if isinstance(ad, dict):
            return ad.get("model", "") or ad.get("model_display", "") or ""
        return ""

    def _agent_provider(agent_name: str) -> str:
        info = agent_map.get(agent_name, {})
        if info.get("provider"):
            return info["provider"]
        ad = agents_cfg.get(agent_name, {})
        if isinstance(ad, dict):
            return ad.get("provider", "") or ""
        return ""

    def _agent_enabled(agent_name: str) -> bool:
        info = agent_map.get(agent_name, {})
        if "enabled" in info:
            return info["enabled"]
        ad = agents_cfg.get(agent_name, {})
        if isinstance(ad, dict):
            return ad.get("enabled", True)
        return True

    routes = []

    # 3. 有 routing 配置: 为每个 capability 创建 route
    for capability, rule in routing_cfg.items():
        if not isinstance(rule, dict):
            continue
        primary = rule.get("primary", "")
        fallback = rule.get("fallback", "")
        tertiary = rule.get("tertiary", "")
        keywords = rule.get("keywords", []) or []
        match_pattern = rule.get("match", "")

        routes.append({
            "capability": capability,
            "name": primary,
            "provider": _agent_provider(primary),
            "enabled": _agent_enabled(primary),
            "primary": primary,
            "primary_model": _agent_model(primary),
            "fallback": fallback,
            "fallback_model": _agent_model(fallback),
            "tertiary": tertiary,
            "tertiary_model": _agent_model(tertiary),
            "keywords": keywords,
            "match": match_pattern,
        })

    # 4. 无 routing 配置: 从 agent 列表构建 routes
    if not routes and agent_map:
        for name, info in agent_map.items():
            caps = info.get("capabilities", [])
            cap = caps[0] if caps else "general"
            routes.append({
                "capability": cap,
                "name": name,
                "provider": info.get("provider", ""),
                "enabled": info.get("enabled", True),
                "primary": name,
                "primary_model": info.get("model", ""),
                "fallback": "",
                "fallback_model": "",
                "tertiary": "",
                "tertiary_model": "",
                "keywords": [],
                "match": "",
            })

    return {"routes": routes, "count": len(routes)}

@router.get("/match")
@handle_api_errors
async def match_agents(
    task: str = Query(..., description="Task description"),
    requirements: str = Query("", description="Comma-separated capabilities"),
    top_k: int = Query(5, ge=1, le=20),
):
    matcher = _get_matcher()
    reqs = [r.strip() for r in requirements.split(",") if r.strip()] if requirements else None
    scores = matcher.match(task=task, requirements=reqs, top_k=top_k)
    return {"matches": [s.model_dump() for s in scores]}

@router.get("/{name}")
@handle_api_errors
async def get_agent(name: str):
    registry = _get_registry()
    agent = registry.get_agent(name)
    if agent is None:
        return JSONResponse(status_code=404, content={"status": "error", "error": "Agent not found"})
    return {"status": "ok", "agent": agent.model_dump()}


@router.post("/scan")
@handle_api_errors
async def scan_agents(request: Request):
    require_admin(request)
    scanner = _get_scanner()
    registry = _get_registry()
    found = scanner.scan()
    synced = registry.sync_from_scanner(scanner, scanned=found)
    return {"scanned": len(found), "synced": synced, "agents": [a.model_dump() for a in found]}


@router.post("/{name}/health-check")
@handle_api_errors
async def check_agent_health(name: str, request: Request):
    require_admin(request)
    registry = _get_registry()
    result = registry.health_check(name)
    return {"result": result.model_dump()}


@router.post("/health-check-all")
@handle_api_errors
async def check_all_health(request: Request):
    require_admin(request)
    registry = _get_registry()
    results = registry.health_check_all()
    return {"results": [r.model_dump() for r in results]}


@router.post("/{name}/enable")
@handle_api_errors
async def enable_agent(name: str, request: Request):
    require_admin(request)
    registry = _get_registry()
    ok = registry.enable(name)
    return {"enabled": ok}


@router.post("/{name}/disable")
@handle_api_errors
async def disable_agent(name: str, request: Request):
    require_admin(request)
    registry = _get_registry()
    ok = registry.disable(name)
    return {"disabled": ok}


@router.post("/register")
@handle_api_errors
async def register_agent(body: RegisterAgentRequest, request: Request):
    """注册新 agent——同时写入 registry 数据库和 config/agents.yaml。

    这样扫描到的 agent 也能使用升级/诊断/修复/自进化等依赖 yaml 的功能。
    """
    require_admin(request)
    import yaml as _yaml
    from maop.core.agent_registry import RegisteredAgent

    registry = _get_registry()
    agent = RegisteredAgent(
        name=body.name,
        cli_path=body.cli_path,
        provider=body.provider,
        capabilities=body.capabilities,
        description=body.description,
        model=body.model,
        driver=body.driver,
        cli_args=body.cli_args,
        timeout_s=body.timeout_s,
        source="manual",
    )
    registry.register(agent)

    # 同步写入 agents.yaml，让 upgrade/diagnose/repair/evolve 等端点可用
    synced_to_yaml = False
    yaml_path = MAOP_ROOT / "config" / "agents.yaml"
    if yaml_path.exists():
        try:
            with open(yaml_path, "r", encoding="utf-8") as f:
                data = _yaml.safe_load(f) or {}
            agents_dict = data.get("agents", {})
            if body.name not in agents_dict:
                # 使用 CLI 可执行文件名而非完整路径 (约定: cli 字段是名称不是路径)
                import os
                cli_name = os.path.basename(body.cli_path) if body.cli_path else body.name
                agents_dict[body.name] = {
                    "cli": cli_name,
                    "cli_args": body.cli_args or '--task "{task}"',
                    "capabilities": body.capabilities,
                    "description": body.description or f"{body.name} agent",
                    "driver": body.driver,
                    "model": body.model or "auto",
                    "timeout_s": body.timeout_s,
                }
                if body.provider:
                    agents_dict[body.name]["provider"] = body.provider
                data["agents"] = agents_dict
                with open(yaml_path, "w", encoding="utf-8") as f:
                    _yaml.dump(data, f, allow_unicode=True, default_flow_style=False, sort_keys=False)
                synced_to_yaml = True
        except Exception:
            pass  # registry 已写入，yaml 写入失败不阻塞

    return {"agent": agent.model_dump(), "synced_to_yaml": synced_to_yaml}


@router.delete("/{name}")
@handle_api_errors
async def unregister_agent(name: str, request: Request):
    """从注册表、扫描表和 agents.yaml 中移除 agent。

    清理范围：
      1. registered_agents 表（registry.unregister）
      2. scanned_agents 表（scanner.unregister）
      3. config/agents.yaml 中的对应条目
    """
    require_admin(request)
    registry = _get_registry()
    scanner = _get_scanner()
    errors: list[str] = []

    # 1. 从注册表移除
    ok_registry = registry.unregister(name)
    if not ok_registry:
        errors.append("not found in registry")

    # 2. 从扫描表移除
    try:
        scanner.unregister(name)
    except Exception as exc:
        errors.append(f"scanner cleanup: {exc}")

    # 3. 从 agents.yaml 移除
    try:
        import yaml as _yaml
        root = MAOP_ROOT
        yaml_path = root / "config" / "agents.yaml"
        if yaml_path.exists():
            with open(yaml_path, "r", encoding="utf-8") as f:
                data = _yaml.safe_load(f) or {}
            agents_dict = data.get("agents", {})
            if name in agents_dict:
                del agents_dict[name]
                data["agents"] = agents_dict
                with open(yaml_path, "w", encoding="utf-8") as f:
                    _yaml.dump(data, f, allow_unicode=True, default_flow_style=False, sort_keys=False)
    except Exception as exc:
        errors.append(f"yaml cleanup: {exc}")

    # 4. 记录审计日志
    try:
        from maop.control.audit import AuditLevel, AuditLog
        root = MAOP_ROOT
        AuditLog(root / "logs" / "audit.jsonl").log(
            action="agent.remove", actor="dashboard",
            target=name, level=AuditLevel.WARN,
            detail={"registry": ok_registry, "errors": errors},
        )
    except Exception:
        pass

    return {"deleted": ok_registry, "errors": errors}




@router.get("/{name}/health-log")
@handle_api_errors
async def get_health_log(name: str, limit: int = Query(50, ge=1, le=200)):
    registry = _get_registry()
    log = registry.get_health_log(agent_name=name, limit=limit)
    return {"log": log}


# ── 诊断与修复 ────────────────────────────────────────────────────

@router.get("/{name}/diagnose")
@handle_api_errors
async def diagnose_agent(name: str, request: Request):
    """诊断 agent CLI 的状态（CLI 存在性、依赖、配置）。"""
    require_admin(request)
    repair = _get_repair()
    agent_cfg = _get_agent_config(name)
    result = await repair.diagnose(name, agent_cfg)
    return {"diagnosis": result.model_dump()}


@router.post("/{name}/repair")
@handle_api_errors
async def repair_agent(name: str, request: Request):
    """修复 agent CLI（安装缺失依赖、修复权限、重装 CLI）。"""
    require_admin(request)
    repair = _get_repair()
    agent_cfg = _get_agent_config(name)
    result = await repair.repair(name, agent_cfg)

    # 记录审计日志
    try:
        from maop.control.audit import AuditLevel, AuditLog
        root = MAOP_ROOT
        AuditLog(root / "logs" / "audit.jsonl").log(
            action="agent.repair", actor="dashboard",
            target=name, level=AuditLevel.INFO,
            detail=result.model_dump(),
        )
    except Exception:
        pass

    return {"result": result.model_dump()}


# ── 升级（修正版，支持 pip/npm/二进制） ──────────────────────────

@router.get("/upgrade/status")
@handle_api_errors
async def get_upgrade_status(request: Request):
    """获取所有 agent 的升级状态（当前版本 vs 最新版本）。"""
    require_admin(request)
    import asyncio
    import shutil as _shutil
    import sys as _sys

    try:
        from maop.config.loader import ConfigLoader
        root = MAOP_ROOT
        cfg = ConfigLoader(project_root=str(root)).load()
    except Exception:
        return {"agents": []}

    result = []
    for agent_name, ad in cfg.agents.items():
        cli_path = _shutil.which(ad.cli) if ad.cli else None
        current = ""
        if cli_path:
            try:
                proc = await asyncio.create_subprocess_exec(
                    cli_path, "--version",
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                out_b, err_b = await asyncio.wait_for(proc.communicate(), timeout=5)
                current = (out_b.decode(errors="replace") or err_b.decode(errors="replace")).strip()[:100]
            except Exception:
                current = "unknown"

        # 检测安装方式和最新版本
        install_method = "unknown"
        latest = "?"
        if ad.cli:
            # 尝试 pip
            try:
                proc = await asyncio.create_subprocess_exec(
                    _sys.executable, "-m", "pip", "show", ad.cli,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                out_b, _ = await asyncio.wait_for(proc.communicate(), timeout=10)
                if proc.returncode == 0:
                    install_method = "pip"
                    for line in out_b.decode(errors="replace").split("\n"):
                        if line.startswith("Version:"):
                            latest = line.split(":", 1)[1].strip()
                            break
            except Exception:
                pass

            # 尝试 npm
            if install_method == "unknown":
                npm_path = _shutil.which("npm")
                if npm_path:
                    try:
                        proc = await asyncio.create_subprocess_exec(
                            "npm", "list", "-g", "--json",
                            stdout=asyncio.subprocess.PIPE,
                            stderr=asyncio.subprocess.PIPE,
                        )
                        out_b, _ = await asyncio.wait_for(proc.communicate(), timeout=10)
                        install_method = "npm"
                        latest = "check npm"
                    except Exception:
                        pass

            # 二进制分发
            if install_method == "unknown" and cli_path:
                install_method = "binary"

        result.append({
            "name": agent_name,
            "cli": ad.cli,
            "current": current,
            "latest": latest,
            "install_method": install_method,
            "status": "ok" if cli_path else "unavailable",
        })
    return {"agents": result}


@router.get("/{name}/upgrade/check")
@handle_api_errors
async def check_upgrade(name: str, request: Request):
    """检查 agent 的当前版本和最新版本（升级前调用，不执行实际升级）。

    返回：
      - current_version: 当前安装版本
      - latest_version: 最新可用版本
      - install_method: 安装方式 (pip/npm/binary)
      - update_available: 是否有更新
      - release_notes: 新版本简介（如有）
    """
    require_admin(request)
    import asyncio
    import shutil as _shutil
    import sys as _sys

    agent_cfg = _get_agent_config(name)
    if not agent_cfg:
        return {"status": "error", "error": f"agent {name} not found in config"}

    cli_name = getattr(agent_cfg, "cli", "") or (agent_cfg.get("cli", "") if isinstance(agent_cfg, dict) else "")
    if not cli_name:
        return {"status": "error", "error": "No CLI configured for this agent"}

    current_version = ""
    latest_version = ""
    install_method = "unknown"
    release_notes = ""

    # 获取当前版本
    cli_path = _shutil.which(cli_name)
    if cli_path:
        try:
            proc = await asyncio.create_subprocess_exec(
                cli_path, "--version",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            out_b, err_b = await asyncio.wait_for(proc.communicate(), timeout=5)
            current_version = (out_b.decode(errors="replace") or err_b.decode(errors="replace")).strip()[:100]
        except Exception:
            current_version = "unknown"

    # 检测安装方式 + 获取最新版本
    # 1. pip
    try:
        proc = await asyncio.create_subprocess_exec(
            _sys.executable, "-m", "pip", "show", cli_name,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        out_b, _ = await asyncio.wait_for(proc.communicate(), timeout=10)
        if proc.returncode == 0:
            install_method = "pip"
            for line in out_b.decode(errors="replace").split("\n"):
                if line.startswith("Version:") and not current_version:
                    current_version = line.split(":", 1)[1].strip()
            # 查询 PyPI 最新版本
            try:
                idx_proc = await asyncio.create_subprocess_exec(
                    _sys.executable, "-m", "pip", "index", "versions", cli_name,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                idx_out, _ = await asyncio.wait_for(idx_proc.communicate(), timeout=15)
                idx_text = idx_out.decode(errors="replace")
                for line in idx_text.split("\n"):
                    if "Available versions:" in line:
                        versions = line.split(":", 1)[1].strip().split(", ")
                        if versions:
                            latest_version = versions[0].strip()
                        break
            except Exception:
                latest_version = "unknown"
    except Exception:
        pass

    # 2. npm
    if install_method == "unknown":
        npm_path = _shutil.which("npm")
        if npm_path:
            known_npm_packages = {
                "claude": "@anthropic-ai/claude-code",
                "codex": "@openai/codex",
                "gemini": "@google/gemini-cli",
                "openclaw": "openclaw",
                "crush": "crush",
            }
            npm_pkg = known_npm_packages.get(cli_name, cli_name)
            install_method = "npm"
            try:
                proc = await asyncio.create_subprocess_exec(
                    "npm", "view", npm_pkg, "version",
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                out_b, _ = await asyncio.wait_for(proc.communicate(), timeout=15)
                latest_version = out_b.decode(errors="replace").strip()
                # 获取当前安装版本
                cur_proc = await asyncio.create_subprocess_exec(
                    "npm", "list", "-g", npm_pkg, "--depth=0",
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                cur_out, _ = await asyncio.wait_for(cur_proc.communicate(), timeout=10)
                cur_text = cur_out.decode(errors="replace")
                if "@" in cur_text:
                    parts = cur_text.split("@")
                    if len(parts) >= 2:
                        current_version = parts[-1].strip().split("\n")[0]
            except Exception:
                latest_version = "unknown"

    # 3. 二进制
    if install_method == "unknown" and cli_path:
        install_method = "binary"

    update_available = bool(
        latest_version
        and latest_version not in ("unknown", "?", "check npm", "")
        and current_version
        and current_version not in ("unknown", "")
        and latest_version != current_version
    )

    return {
        "status": "ok",
        "agent": name,
        "cli": cli_name,
        "current_version": current_version,
        "latest_version": latest_version,
        "install_method": install_method,
        "update_available": update_available,
        "release_notes": release_notes,
    }


@router.post("/{name}/upgrade")
@handle_api_errors
async def upgrade_agent(name: str, request: Request):
    """升级 agent CLI（自动检测安装方式：pip/npm/二进制）。"""
    require_admin(request)
    import asyncio
    import shutil as _shutil
    import sys as _sys

    agent_cfg = _get_agent_config(name)
    if not agent_cfg:
        return {"status": "error", "error": f"agent {name} not found in config"}

    cli_name = getattr(agent_cfg, "cli", "") or (agent_cfg.get("cli", "") if isinstance(agent_cfg, dict) else "")
    if not cli_name:
        return {"status": "error", "error": "No CLI configured for this agent"}

    info: dict[str, Any] = {
        "agent": name,
        "cli": cli_name,
        "upgrade_status": "unknown",
    }

    # 检测安装方式并执行升级
    # 1. 先尝试 pip
    try:
        proc = await asyncio.create_subprocess_exec(
            _sys.executable, "-m", "pip", "show", cli_name,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        out_b, _ = await asyncio.wait_for(proc.communicate(), timeout=10)
        if proc.returncode == 0:
            info["install_method"] = "pip"
            # 执行 pip 升级
            upgrade_proc = await asyncio.create_subprocess_exec(
                _sys.executable, "-m", "pip", "install", "--upgrade", cli_name,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            try:
                up_out, up_err = await asyncio.wait_for(upgrade_proc.communicate(), timeout=120)
            except asyncio.TimeoutError:
                upgrade_proc.kill()
                await upgrade_proc.wait()
                info["upgrade_status"] = "timeout"
                info["error"] = "pip install upgrade timed out (120s)"
                return {"status": "ok", "info": info}

            info["exit_code"] = upgrade_proc.returncode
            if upgrade_proc.returncode == 0:
                info["upgrade_status"] = "success"
                info["output"] = up_out.decode(errors="replace")[-500:]
            else:
                info["upgrade_status"] = "failed"
                info["output"] = (up_err.decode(errors="replace") or up_out.decode(errors="replace"))[-500:]
            return {"status": "ok", "info": info}
    except Exception:
        pass

    # 2. 尝试 npm
    npm_path = _shutil.which("npm")
    if npm_path:
        known_npm_packages = {
            "claude": "@anthropic-ai/claude-code",
            "codex": "@openai/codex",
            "gemini": "@google/gemini-cli",
            "openclaw": "openclaw",
            "crush": "crush",
        }
        npm_pkg = known_npm_packages.get(cli_name, cli_name)
        info["install_method"] = "npm"
        info["npm_package"] = npm_pkg
        try:
            upgrade_proc = await asyncio.create_subprocess_exec(
                "npm", "install", "-g", npm_pkg,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            try:
                up_out, up_err = await asyncio.wait_for(upgrade_proc.communicate(), timeout=120)
            except asyncio.TimeoutError:
                upgrade_proc.kill()
                await upgrade_proc.wait()
                info["upgrade_status"] = "timeout"
                info["error"] = "npm install upgrade timed out (120s)"
                return {"status": "ok", "info": info}

            info["exit_code"] = upgrade_proc.returncode
            if upgrade_proc.returncode == 0:
                info["upgrade_status"] = "success"
                info["output"] = up_out.decode(errors="replace")[-500:]
            else:
                info["upgrade_status"] = "failed"
                info["output"] = (up_err.decode(errors="replace") or up_out.decode(errors="replace"))[-500:]
            return {"status": "ok", "info": info}
        except Exception as exc:
            info["upgrade_status"] = "error"
            info["error"] = f"npm upgrade failed: {exc}"
            return {"status": "ok", "info": info}

    # 3. 二进制分发，无法自动升级
    info["install_method"] = "binary"
    info["upgrade_status"] = "not_supported"
    info["error"] = f"'{cli_name}' is a binary-distributed CLI, cannot auto-upgrade. Please update manually."

    # 记录审计日志
    try:
        from maop.control.audit import AuditLevel, AuditLog
        root = MAOP_ROOT
        AuditLog(root / "logs" / "audit.jsonl").log(
            action="agent.upgrade", actor="dashboard",
            target=name, level=AuditLevel.INFO, detail=info,
        )
    except Exception:
        pass

    return {"status": "ok", "info": info}


# ── Agent 记忆 ────────────────────────────────────────────────────

class MemoryStoreRequest(BaseModel):
    memory_type: str = Field(description="interaction/preference/error_pattern/performance/lesson")
    content: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)
    importance: float = Field(default=0.5, ge=0.0, le=1.0)


@router.get("/{name}/memory")
@handle_api_errors
async def get_agent_memory(
    name: str,
    memory_type: str = Query("", description="Filter by memory type"),
    limit: int = Query(50, ge=1, le=500),
):
    """获取 agent 的记忆记录。"""
    memory = _get_memory()
    records = memory.retrieve(name, memory_type=memory_type or None, limit=limit)
    return {"memories": records, "count": len(records)}


@router.post("/{name}/memory")
@handle_api_errors
async def store_agent_memory(name: str, body: MemoryStoreRequest, request: Request):
    """存储一条 agent 记忆。"""
    require_admin(request)
    memory = _get_memory()
    record_id = memory.store(
        agent_name=name,
        memory_type=body.memory_type,
        content=body.content,
        metadata=body.metadata,
        importance=body.importance,
    )
    return {"id": record_id, "status": "stored"}


@router.delete("/{name}/memory")
@handle_api_errors
async def clear_agent_memory(
    name: str,
    request: Request,
    memory_id: int = Query(0, description="Specific memory ID to delete, 0 = all"),
):
    """清除 agent 的记忆（全部或指定条目）。"""
    require_admin(request)
    memory = _get_memory()
    if memory_id:
        deleted = memory.forget(name, memory_id)
    else:
        deleted = memory.forget(name)
    return {"deleted": deleted}


@router.get("/{name}/memory/summary")
@handle_api_errors
async def get_memory_summary(name: str):
    """获取 agent 记忆的统计摘要。"""
    memory = _get_memory()
    summary = memory.summarize(name)
    return {"summary": summary}


# ── Agent 自进化 ──────────────────────────────────────────────────

@router.post("/{name}/evolve")
@handle_api_errors
async def evolve_agent(name: str, request: Request):
    """触发 agent 自进化分析。"""
    require_admin(request)
    evolution = _get_evolution()
    agent_cfg = _get_agent_config(name)
    result = await evolution.evolve(name, agent_cfg)

    # 记录审计日志
    try:
        from maop.control.audit import AuditLevel, AuditLog
        root = MAOP_ROOT
        AuditLog(root / "logs" / "audit.jsonl").log(
            action="agent.evolve", actor="dashboard",
            target=name, level=AuditLevel.INFO,
            detail={"summary": result.summary, "auto_applied": len(result.auto_applied)},
        )
    except Exception:
        pass

    return {"result": result.model_dump()}


@router.get("/{name}/evolution-status")
@handle_api_errors
async def get_evolution_status(name: str):
    """获取 agent 的自进化状态。"""
    evolution = _get_evolution()
    status = evolution.get_status(name)
    return {"status": status}
