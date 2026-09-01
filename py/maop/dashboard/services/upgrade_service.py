"""Agent CLI upgrade service.

Encapsulates version checking and upgrade execution for agent CLIs
(pip / npm / binary). Extracted from the agents router (§2.7) so the
router layer only does parameter parsing + service call + response.

The service is intentionally framework-agnostic: it does not import
FastAPI and can be unit-tested or invoked from CLI/CI without an
HTTP context. ``maop_root`` is passed in explicitly for audit logging
so the service has no hidden global state.
"""

from __future__ import annotations

import asyncio
import logging
import shutil
import sys
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Known npm package name overrides for CLIs whose npm scope differs from
# the binary name. Kept module-level so tests can monkeypatch if needed.
_NPM_PACKAGE_OVERRIDES: dict[str, str] = {
    "claude": "@anthropic-ai/claude-code",
    "codex": "@openai/codex",
    "gemini": "@google/gemini-cli",
    "openclaw": "openclaw",
    "crush": "crush",
}


def _resolve_cli_name(agent_cfg: Any) -> str:
    """Extract the CLI command name from an AgentDef-like config object."""
    return getattr(agent_cfg, "cli", "") or (
        agent_cfg.get("cli", "") if isinstance(agent_cfg, dict) else ""
    )


async def _run_subproc(args: list[str], timeout: float) -> tuple[int | None, bytes, bytes]:
    """Run a subprocess and return (returncode, stdout, stderr).

    Times out after ``timeout`` seconds (kills the process on timeout).

    Thin wrapper around :func:`maop.core.utils.async_subprocess.run_subprocess_bytes`
    kept for backward compatibility with callers that expect the private name.
    """
    from maop.core.utils.async_subprocess import run_subprocess_bytes

    return await run_subprocess_bytes(args, timeout)


async def check_agent_upgrade(name: str, agent_cfg: Any) -> dict[str, Any]:
    """Check current and latest version for an agent CLI (no upgrade performed).

    Returns a dict with: status / agent / cli / current_version /
    latest_version / install_method / update_available / release_notes.
    """
    cli_name = _resolve_cli_name(agent_cfg)
    if not cli_name:
        return {"status": "error", "error": "No CLI configured for this agent"}

    current_version = ""
    latest_version = ""
    install_method = "unknown"
    release_notes = ""

    # 获取当前版本
    cli_path = shutil.which(cli_name)
    if cli_path:
        try:
            _rc, out_b, err_b = await _run_subproc([cli_path, "--version"], timeout=5)
            current_version = (out_b.decode(errors="replace") or err_b.decode(errors="replace")).strip()[:100]
        except Exception:
            current_version = "unknown"

    # 检测安装方式 + 获取最新版本
    # 1. pip
    try:
        rc, out_b, _ = await _run_subproc([sys.executable, "-m", "pip", "show", cli_name], timeout=10)
        if rc == 0:
            install_method = "pip"
            for line in out_b.decode(errors="replace").split("\n"):
                if line.startswith("Version:") and not current_version:
                    current_version = line.split(":", 1)[1].strip()
            # 查询 PyPI 最新版本
            try:
                _irc, idx_out, _ = await _run_subproc(
                    [sys.executable, "-m", "pip", "index", "versions", cli_name], timeout=15
                )
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
        logger.warning("检测 CLI 当前/最新版本失败（check_agent_upgrade），latest_version 将标记为 unknown", exc_info=True)

    # 2. npm
    if install_method == "unknown":
        npm_path = shutil.which("npm")
        if npm_path:
            npm_pkg = _NPM_PACKAGE_OVERRIDES.get(cli_name, cli_name)
            install_method = "npm"
            try:
                _rc, out_b, _ = await _run_subproc(["npm", "view", npm_pkg, "version"], timeout=15)
                latest_version = out_b.decode(errors="replace").strip()
                # 获取当前安装版本
                _crc, cur_out, _ = await _run_subproc(
                    ["npm", "list", "-g", npm_pkg, "--depth=0"], timeout=10
                )
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


async def upgrade_agent_cli(name: str, agent_cfg: Any, maop_root: Path) -> dict[str, Any]:
    """Upgrade an agent CLI in-place (auto-detects pip / npm / binary).

    Returns ``{"status": "ok", "info": {...}}`` on completion (success or
    failure); the ``info.upgrade_status`` field reports the outcome.
    """
    cli_name = _resolve_cli_name(agent_cfg)
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
        rc, _out, _err = await _run_subproc([sys.executable, "-m", "pip", "show", cli_name], timeout=10)
        if rc == 0:
            info["install_method"] = "pip"
            # 执行 pip 升级
            try:
                up_rc, up_out, up_err = await _run_subproc(
                    [sys.executable, "-m", "pip", "install", "--upgrade", cli_name], timeout=120
                )
            except asyncio.TimeoutError:
                info["upgrade_status"] = "timeout"
                info["error"] = "pip install upgrade timed out (120s)"
                return {"status": "ok", "info": info}

            info["exit_code"] = up_rc
            if up_rc == 0:
                info["upgrade_status"] = "success"
                info["output"] = up_out.decode(errors="replace")[-500:]
            else:
                info["upgrade_status"] = "failed"
                info["output"] = (up_err.decode(errors="replace") or up_out.decode(errors="replace"))[-500:]
            return {"status": "ok", "info": info}
    except Exception:
        logger.warning("pip 自动升级失败（upgrade_agent_cli），将尝试 npm 降级安装", exc_info=True)

    # 2. 尝试 npm
    npm_path = shutil.which("npm")
    if npm_path:
        npm_pkg = _NPM_PACKAGE_OVERRIDES.get(cli_name, cli_name)
        info["install_method"] = "npm"
        info["npm_package"] = npm_pkg
        try:
            try:
                up_rc, up_out, up_err = await _run_subproc(
                    ["npm", "install", "-g", npm_pkg], timeout=120
                )
            except asyncio.TimeoutError:
                info["upgrade_status"] = "timeout"
                info["error"] = "npm install upgrade timed out (120s)"
                return {"status": "ok", "info": info}

            info["exit_code"] = up_rc
            if up_rc == 0:
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

        AuditLog(maop_root / "logs" / "audit.jsonl").log(
            action="agent.upgrade", actor="dashboard",
            target=name, level=AuditLevel.INFO, detail=info,
        )
    except Exception:
        logger.warning("写入升级审计日志失败（upgrade_agent_cli），审计记录被忽略", exc_info=True)

    return {"status": "ok", "info": info}