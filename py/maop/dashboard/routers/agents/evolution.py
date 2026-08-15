"""Evolution & upgrade endpoints for the agents router.

Covers: evolve / evolution-status / upgrade-check / upgrade-run /
upgrade-status. The heavy upgrade logic lives in
``maop.dashboard.services.upgrade_service`` (§2.7) — handlers here only
parse params, resolve the agent config, and delegate to the service.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

import asyncio
import shutil
import sys
from typing import Any

from fastapi import APIRouter, Request

from maop.core.security.middleware import require_admin
from maop.dashboard.error_handler import handle_api_errors
from maop.dashboard.services import upgrade_service

from . import _deps

router = APIRouter(prefix="/api/agents", tags=["agents"])


# ── 升级状态（全量 agent） ──────────────────────────────────────────


@router.get("/upgrade/status")
@handle_api_errors
async def get_upgrade_status(request: Request) -> dict[str, Any]:
    """获取所有 agent 的升级状态（当前版本 vs 最新版本）。"""
    require_admin(request)

    try:
        from maop.config.loader import ConfigLoader

        root = _deps.MAOP_ROOT
        cfg = ConfigLoader(project_root=str(root)).load()
    except Exception:
        return {"agents": []}

    result = []
    for agent_name, ad in cfg.agents.items():
        cli_path = shutil.which(ad.cli) if ad.cli else None
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
                    sys.executable, "-m", "pip", "show", ad.cli,
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
                logger.debug('swallowed exception', exc_info=True)

            # 尝试 npm
            if install_method == "unknown":
                npm_path = shutil.which("npm")
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
                        logger.debug('swallowed exception', exc_info=True)

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


# ── 单 agent 升级检查 / 执行（业务逻辑在 upgrade_service） ─────────


@router.get("/{name}/upgrade/check")
@handle_api_errors
async def check_upgrade(name: str, request: Request) -> dict[str, Any]:
    """检查 agent 的当前版本和最新版本（升级前调用，不执行实际升级）。

    返回：
      - current_version: 当前安装版本
      - latest_version: 最新可用版本
      - install_method: 安装方式 (pip/npm/binary)
      - update_available: 是否有更新
      - release_notes: 新版本简介（如有）
    """
    require_admin(request)

    agent_cfg = _deps._get_agent_config(name)
    if not agent_cfg:
        return {"status": "error", "error": f"agent {name} not found in config"}

    return await upgrade_service.check_agent_upgrade(name, agent_cfg)


@router.post("/{name}/upgrade")
@handle_api_errors
async def upgrade_agent(name: str, request: Request) -> dict[str, Any]:
    """升级 agent CLI（自动检测安装方式：pip/npm/二进制）。"""
    require_admin(request)

    agent_cfg = _deps._get_agent_config(name)
    if not agent_cfg:
        return {"status": "error", "error": f"agent {name} not found in config"}

    return await upgrade_service.upgrade_agent_cli(name, agent_cfg, _deps.MAOP_ROOT)


# ── Agent 自进化 ──────────────────────────────────────────────────


@router.post("/{name}/evolve")
@handle_api_errors
async def evolve_agent(name: str, request: Request) -> dict[str, Any]:
    """触发 agent 自进化分析。"""
    require_admin(request)
    evolution = _deps._get_evolution()
    agent_cfg = _deps._get_agent_config(name)
    result = await evolution.evolve(name, agent_cfg)

    # 记录审计日志
    try:
        from maop.control.audit import AuditLevel, AuditLog

        root = _deps.MAOP_ROOT
        AuditLog(root / "logs" / "audit.jsonl").log(
            action="agent.evolve", actor="dashboard",
            target=name, level=AuditLevel.INFO,
            detail={"summary": result.summary, "auto_applied": len(result.auto_applied)},
        )
    except Exception:
        logger.debug('swallowed exception', exc_info=True)

    return {"result": result.model_dump()}


@router.get("/{name}/evolution-status")
@handle_api_errors
async def get_evolution_status(name: str) -> dict[str, Any]:
    """获取 agent 的自进化状态。"""
    evolution = _deps._get_evolution()
    status = evolution.get_status(name)
    return {"status": status}