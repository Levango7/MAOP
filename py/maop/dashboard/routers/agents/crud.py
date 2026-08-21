"""CRUD endpoints for the agents router.

Covers: list / get / match / scan / register / unregister / enable /
disable / health-check(-all) / health-log / diagnose / repair.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

import asyncio
from typing import Any

from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, Field

from maop.core.security.middleware import require_admin
from maop.dashboard.error_handler import handle_api_errors

from . import _deps

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


# ── helpers for register/unregister (blocking YAML I/O) ────────────


def _agents_yaml_path():
    return _deps.MAOP_ROOT / "config" / "agents.yaml"


def _sync_agent_to_yaml(body) -> bool:
    """Register sync: write agent entry into config/agents.yaml (blocking I/O).

    Runs in a worker thread via asyncio.to_thread — never call directly
    from an async route.
    """
    import os

    import yaml as _yaml

    yaml_path = _agents_yaml_path()
    if not yaml_path.exists():
        return False
    with open(yaml_path, "r", encoding="utf-8") as f:
        data = _yaml.safe_load(f) or {}
    agents_dict = data.get("agents", {})
    if body.name in agents_dict:
        return False
    # 使用 CLI 可执行文件名而非完整路径 (约定: cli 字段是名称不是路径)
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
    return True


def _remove_agent_from_yaml(name: str) -> None:
    """Unregister sync: remove agent entry from config/agents.yaml (blocking I/O).

    Runs in a worker thread via asyncio.to_thread — never call directly
    from an async route.
    """
    import yaml as _yaml

    yaml_path = _agents_yaml_path()
    if not yaml_path.exists():
        return
    with open(yaml_path, "r", encoding="utf-8") as f:
        data = _yaml.safe_load(f) or {}
    agents_dict = data.get("agents", {})
    if name in agents_dict:
        del agents_dict[name]
        data["agents"] = agents_dict
        with open(yaml_path, "w", encoding="utf-8") as f:
            _yaml.dump(data, f, allow_unicode=True, default_flow_style=False, sort_keys=False)


# ── Endpoints ──────────────────────────────────────────────────────


@router.get("")
@handle_api_errors
async def list_agents(
    enabled_only: bool = Query(False, description="Only enabled agents"),
    healthy_only: bool = Query(False, description="Only healthy agents"),
    capability: str = Query("", description="Filter by capability"),
    provider: str = Query("", description="Filter by provider"),
):
    registry = _deps._get_registry()
    agents = registry.list_agents(
        enabled_only=enabled_only,
        healthy_only=healthy_only,
        capability=capability or "",
        provider=provider or "",
    )
    return {"agents": [a.model_dump() for a in agents]}


# ── 预置示例 Agent 模板（P1-4） ──────────────────────────────────────

PRESET_AGENTS: list[dict[str, Any]] = [
    {
        "name": "code-reviewer",
        "description": "代码审查助手 — 审查代码质量、发现 bug、提供建议",
        "capabilities": ["review", "codegen", "explain"],
        "model": "auto",
        "cli": "python",
        "cli_args": "-m maop.cli run --task \"{task}\"",
        "driver": "cli",
        "timeout_s": 180,
    },
    {
        "name": "doc-writer",
        "description": "文档撰写助手 — 撰写技术文档、API 文档、用户指南",
        "capabilities": ["chat", "explain", "search"],
        "model": "auto",
        "cli": "python",
        "cli_args": "-m maop.cli run --task \"{task}\"",
        "driver": "cli",
        "timeout_s": 120,
    },
    {
        "name": "data-analyst",
        "description": "数据分析助手 — 分析数据、生成报告、可视化建议",
        "capabilities": ["search", "explain", "chat"],
        "model": "auto",
        "cli": "python",
        "cli_args": "-m maop.cli run --task \"{task}\"",
        "driver": "cli",
        "timeout_s": 120,
    },
    {
        "name": "test-generator",
        "description": "测试生成助手 — 为代码自动生成测试用例",
        "capabilities": ["codegen", "review"],
        "model": "auto",
        "cli": "python",
        "cli_args": "-m maop.cli run --task \"{task}\"",
        "driver": "cli",
        "timeout_s": 180,
    },
]


@router.get("/presets")
@handle_api_errors
async def list_presets():
    """返回预置示例 Agent 模板，供前端一键创建。"""
    return {"presets": PRESET_AGENTS}


@router.get("/match")
@handle_api_errors
async def match_agents(
    task: str = Query(..., description="Task description"),
    requirements: str = Query("", description="Comma-separated capabilities"),
    top_k: int = Query(5, ge=1, le=20),
):
    matcher = _deps._get_matcher()
    reqs = [r.strip() for r in requirements.split(",") if r.strip()] if requirements else None
    scores = matcher.match(task=task, requirements=reqs, top_k=top_k)
    return {"matches": [s.model_dump() for s in scores]}


@router.get("/{name}")
@handle_api_errors
async def get_agent(name: str) -> Response:
    registry = _deps._get_registry()
    agent = registry.get_agent(name)
    if agent is None:
        return JSONResponse(status_code=404, content={"status": "error", "error": "Agent not found"})
    # Wrap in JSONResponse so both return paths satisfy the `-> Response` annotation.
    # RegisteredAgent fields are all JSON-native (str/int/bool/list[str]), so this
    # is equivalent to returning the dict directly (FastAPI would encode it the same way).
    return JSONResponse(status_code=200, content={"status": "ok", "agent": agent.model_dump()})


@router.post("/scan")
@handle_api_errors
async def scan_agents(request: Request) -> dict[str, Any]:
    require_admin(request)
    scanner = _deps._get_scanner()
    registry = _deps._get_registry()
    found = scanner.scan()
    synced = registry.sync_from_scanner(scanner, scanned=found)
    return {"scanned": len(found), "synced": synced, "agents": [a.model_dump() for a in found]}


@router.post("/{name}/health-check")
@handle_api_errors
async def check_agent_health(name: str, request: Request) -> dict[str, Any]:
    require_admin(request)
    registry = _deps._get_registry()
    result = registry.health_check(name)
    return {"result": result.model_dump()}


@router.post("/health-check-all")
@handle_api_errors
async def check_all_health(request: Request) -> dict[str, Any]:
    require_admin(request)
    registry = _deps._get_registry()
    results = registry.health_check_all()
    return {"results": [r.model_dump() for r in results]}


@router.post("/{name}/enable")
@handle_api_errors
async def enable_agent(name: str, request: Request) -> dict[str, Any]:
    require_admin(request)
    registry = _deps._get_registry()
    ok = registry.enable(name)
    return {"enabled": ok}


@router.post("/{name}/disable")
@handle_api_errors
async def disable_agent(name: str, request: Request) -> dict[str, Any]:
    require_admin(request)
    registry = _deps._get_registry()
    ok = registry.disable(name)
    return {"disabled": ok}


@router.post("/register")
@handle_api_errors
async def register_agent(body: RegisterAgentRequest, request: Request) -> dict[str, Any]:
    """注册新 agent——同时写入 registry 数据库和 config/agents.yaml。

    这样扫描到的 agent 也能使用升级/诊断/修复/自进化等依赖 yaml 的功能。
    """
    require_admin(request)

    from maop.core.agent.lifecycle.agent_registry import RegisteredAgent

    registry = _deps._get_registry()
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

    # 同步写入 agents.yaml，让 upgrade/diagnose/repair/evolve 等端点可用。
    # 文件读写放线程池执行，避免阻塞事件循环（ASYNC230）。
    synced_to_yaml = False
    try:
        synced_to_yaml = await asyncio.to_thread(_sync_agent_to_yaml, body)
    except Exception:
        logger.debug('swallowed exception', exc_info=True)
        # registry 已写入，yaml 写入失败不阻塞

    return {"agent": agent.model_dump(), "synced_to_yaml": synced_to_yaml}


@router.delete("/{name}")
@handle_api_errors
async def unregister_agent(name: str, request: Request) -> dict[str, Any]:
    """从注册表、扫描表和 agents.yaml 中移除 agent。

    清理范围：
      1. registered_agents 表（registry.unregister）
      2. scanned_agents 表（scanner.unregister）
      3. config/agents.yaml 中的对应条目
    """
    require_admin(request)
    registry = _deps._get_registry()
    scanner = _deps._get_scanner()
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

    # 3. 从 agents.yaml 移除（文件读写在线程池执行，不阻塞事件循环）
    try:
        await asyncio.to_thread(_remove_agent_from_yaml, name)
    except Exception as exc:
        errors.append(f"yaml cleanup: {exc}")

    # 4. 记录审计日志
    try:
        from maop.control.audit import AuditLevel, AuditLog

        root = _deps.MAOP_ROOT
        AuditLog(root / "logs" / "audit.jsonl").log(
            action="agent.remove", actor="dashboard",
            target=name, level=AuditLevel.WARN,
            detail={"registry": ok_registry, "errors": errors},
        )
    except Exception:
        logger.debug('swallowed exception', exc_info=True)

    return {"deleted": ok_registry, "errors": errors}


@router.get("/{name}/health-log")
@handle_api_errors
async def get_health_log(name: str, limit: int = Query(50, ge=1, le=200)) -> dict[str, Any]:
    registry = _deps._get_registry()
    log = registry.get_health_log(agent_name=name, limit=limit)
    return {"log": log}


# ── 诊断与修复 ────────────────────────────────────────────────────


@router.get("/{name}/diagnose")
@handle_api_errors
async def diagnose_agent(name: str, request: Request) -> dict[str, Any]:
    """诊断 agent CLI 的状态（CLI 存在性、依赖、配置）。"""
    require_admin(request)
    repair = _deps._get_repair()
    agent_cfg = _deps._get_agent_config(name)
    result = await repair.diagnose(name, agent_cfg)
    return {"diagnosis": result.model_dump()}


@router.post("/{name}/repair")
@handle_api_errors
async def repair_agent(name: str, request: Request) -> dict[str, Any]:
    """修复 agent CLI（安装缺失依赖、修复权限、重装 CLI）。"""
    require_admin(request)
    repair = _deps._get_repair()
    agent_cfg = _deps._get_agent_config(name)
    result = await repair.repair(name, agent_cfg)

    # 记录审计日志
    try:
        from maop.control.audit import AuditLevel, AuditLog

        root = _deps.MAOP_ROOT
        AuditLog(root / "logs" / "audit.jsonl").log(
            action="agent.repair", actor="dashboard",
            target=name, level=AuditLevel.INFO,
            detail=result.model_dump(),
        )
    except Exception:
        logger.debug('swallowed exception', exc_info=True)

    return {"result": result.model_dump()}