"""MAOP Agent Repair — 检测并修复本地部署的 agent CLI。

诊断项目：
  - CLI 可执行文件是否存在且可执行
  - CLI 版本是否能正常获取
  - Python/npm 依赖是否完整
  - 配置文件是否有效

修复策略：
  - CLI 不存在 → 尝试通过 pip/npm 安装
  - 依赖缺失 → 安装缺失的依赖包
  - 配置损坏 → 恢复默认配置
  - 权限问题 → 修正文件权限

Usage::

    from maop.core.agent_repair import AgentRepair

    repair = AgentRepair(root_dir="/path/to/MAOP")
    diagnosis = await repair.diagnose("claude")
    result = await repair.repair("claude")
"""

from __future__ import annotations

import asyncio
import logging
import os
import shutil
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class DiagnosisResult:
    """单个 agent 的诊断结果。"""
    agent_name: str = ""
    cli_name: str = ""
    cli_path: str = ""
    cli_exists: bool = False
    cli_executable: bool = False
    version: str = ""
    install_method: str = ""  # pip / npm / binary / unknown
    missing_dependencies: list[str] = field(default_factory=list)
    config_issues: list[str] = field(default_factory=list)
    overall_status: str = "healthy"  # healthy / degraded / broken

    def model_dump(self) -> dict[str, Any]:
        return {
            "agent_name": self.agent_name,
            "cli_name": self.cli_name,
            "cli_path": self.cli_path,
            "cli_exists": self.cli_exists,
            "cli_executable": self.cli_executable,
            "version": self.version,
            "install_method": self.install_method,
            "missing_dependencies": self.missing_dependencies,
            "config_issues": self.config_issues,
            "overall_status": self.overall_status,
        }


@dataclass
class RepairResult:
    """修复操作的结果。"""
    agent_name: str = ""
    success: bool = False
    actions_taken: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    diagnosis_before: dict[str, Any] = field(default_factory=dict)
    diagnosis_after: dict[str, Any] = field(default_factory=dict)

    def model_dump(self) -> dict[str, Any]:
        return {
            "agent_name": self.agent_name,
            "success": self.success,
            "actions_taken": self.actions_taken,
            "errors": self.errors,
            "diagnosis_before": self.diagnosis_before,
            "diagnosis_after": self.diagnosis_after,
        }


# 已知的 agent CLI 及其安装方式映射
# key=cli名, value=(安装方式, 安装命令前缀)
_KNOWN_INSTALLERS: dict[str, tuple[str, list[str]]] = {
    "claude": ("npm", ["npm", "install", "-g", "@anthropic-ai/claude-code"]),
    "codex": ("npm", ["npm", "install", "-g", "@openai/codex"]),
    "cursor": ("binary", []),  # 二进制分发，无法自动安装
    "gemini": ("npm", ["npm", "install", "-g", "@google/gemini-cli"]),
    "trae": ("binary", []),  # Trae 是 ByteDance IDE 产品，二进制分发
    "openclaw": ("npm", ["npm", "install", "-g", "openclaw"]),
    "crush": ("npm", ["npm", "install", "-g", "crush"]),
    "copilot": ("binary", []),
    "python": ("system", []),
}

# Python pip 包名映射（当 CLI 名与 pip 包名不同时）
_PIP_PACKAGE_MAP: dict[str, str] = {
    "maop": "maop-core",
}


class AgentRepair:
    """Agent CLI 诊断与修复引擎。"""

    def __init__(self, root_dir: str | Path = ".") -> None:
        self._root = Path(root_dir)

    async def _run_subprocess(
        self, cmd: list[str], timeout: int = 30
    ) -> tuple[int, str, str]:
        """运行子进程，返回 (returncode, stdout, stderr)。"""
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout_b, stderr_b = await asyncio.wait_for(
                proc.communicate(), timeout=timeout
            )
            return (
                proc.returncode or 0,
                stdout_b.decode(errors="replace") if stdout_b else "",
                stderr_b.decode(errors="replace") if stderr_b else "",
            )
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            return -1, "", "timeout"
        except Exception as exc:
            return -1, "", str(exc)

    def _detect_install_method(self, cli_name: str) -> str:
        """检测 agent CLI 的安装方式。"""
        if cli_name in _KNOWN_INSTALLERS:
            return _KNOWN_INSTALLERS[cli_name][0]
        return "unknown"

    async def diagnose(self, agent_name: str, agent_config: Any = None) -> DiagnosisResult:
        """诊断单个 agent CLI 的状态。

        Args:
            agent_name: agent 名称
            agent_config: 可选的 agent 配置对象（含 cli, model 等字段）
        """
        result = DiagnosisResult(agent_name=agent_name)

        # 从配置获取 CLI 名
        cli_name = ""
        if agent_config and hasattr(agent_config, "cli"):
            cli_name = agent_config.cli
        elif agent_config and isinstance(agent_config, dict):
            cli_name = agent_config.get("cli", "")
        result.cli_name = cli_name

        if not cli_name:
            result.overall_status = "broken"
            result.config_issues.append("No CLI configured for this agent")
            return result

        # 1. 检查 CLI 是否存在
        cli_path = shutil.which(cli_name)
        if cli_path:
            result.cli_path = cli_path
            result.cli_exists = True
            # 检查可执行权限
            result.cli_executable = os.access(cli_path, os.X_OK)
        else:
            result.cli_exists = False
            result.overall_status = "broken"
            result.config_issues.append(f"CLI '{cli_name}' not found in PATH")
            # 仍然继续检测安装方式
            result.install_method = self._detect_install_method(cli_name)
            return result

        # 2. 获取版本
        try:
            rc, out, err = await self._run_subprocess([cli_path, "--version"], timeout=10)
            if rc == 0:
                result.version = (out or err).strip()[:200]
            else:
                result.config_issues.append(f"Failed to get version: {err[:100]}")
                result.overall_status = "degraded"
        except Exception as exc:
            result.config_issues.append(f"Version check error: {exc}")
            result.overall_status = "degraded"

        # 3. 检测安装方式
        result.install_method = self._detect_install_method(cli_name)

        # 4. 检查 Python 依赖（如果是 pip 安装的）
        if result.install_method == "pip":
            pip_name = _PIP_PACKAGE_MAP.get(cli_name, cli_name)
            try:
                rc, out, err = await self._run_subprocess(
                    [sys.executable, "-m", "pip", "show", pip_name], timeout=10
                )
                if rc != 0:
                    result.missing_dependencies.append(pip_name)
                    result.overall_status = "degraded"
            except Exception:
                pass  # 非关键

        # 5. 检查配置完整性
        if agent_config:
            if hasattr(agent_config, "model") and not getattr(agent_config, "model", ""):
                result.config_issues.append("No model configured")
                if result.overall_status == "healthy":
                    result.overall_status = "degraded"
            if hasattr(agent_config, "timeout_s") and getattr(agent_config, "timeout_s", 0) < 1:
                result.config_issues.append("Invalid timeout_s value")

        return result

    async def diagnose_all(self, agents_config: dict[str, Any]) -> list[DiagnosisResult]:
        """诊断所有 agent。"""
        results = []
        for name, cfg in agents_config.items():
            try:
                r = await self.diagnose(name, cfg)
                results.append(r)
            except Exception as exc:
                logger.error("[agent_repair] Failed to diagnose %s: %s", name, exc)
                results.append(DiagnosisResult(
                    agent_name=name, overall_status="broken",
                    config_issues=[f"Diagnosis error: {exc}"],
                ))
        return results

    async def repair(self, agent_name: str, agent_config: Any = None) -> RepairResult:
        """修复单个 agent CLI。

        根据诊断结果执行修复操作：
        - CLI 不存在 → 尝试安装
        - 依赖缺失 → 安装缺失依赖
        - 配置问题 → 记录建议（不自动修改配置文件）
        """
        result = RepairResult(agent_name=agent_name)

        # 修复前诊断
        before = await self.diagnose(agent_name, agent_config)
        result.diagnosis_before = before.model_dump()

        cli_name = before.cli_name
        if not cli_name:
            result.errors.append("No CLI configured, cannot repair")
            return result

        # 1. 如果 CLI 不存在，尝试安装
        if not before.cli_exists:
            install_method, install_cmd = _KNOWN_INSTALLERS.get(
                cli_name, ("unknown", [])
            )
            if install_method == "npm" and install_cmd:
                result.actions_taken.append(
                    f"Attempting npm install: {' '.join(install_cmd)}"
                )
                rc, _, err = await self._run_subprocess(install_cmd, timeout=120)
                if rc == 0:
                    result.actions_taken.append("npm install succeeded")
                else:
                    result.errors.append(f"npm install failed: {err[:200]}")

            elif install_method == "pip":
                pip_name = _PIP_PACKAGE_MAP.get(cli_name, cli_name)
                cmd = [sys.executable, "-m", "pip", "install", "--upgrade", pip_name]
                result.actions_taken.append(f"Attempting pip install: {pip_name}")
                rc, _, err = await self._run_subprocess(cmd, timeout=120)
                if rc == 0:
                    result.actions_taken.append("pip install succeeded")
                else:
                    result.errors.append(f"pip install failed: {err[:200]}")

            elif install_method == "binary":
                result.errors.append(
                    f"'{cli_name}' is a binary-distributed CLI, "
                    "cannot auto-install. Please download manually."
                )
            else:
                # 尝试 pip 作为兜底
                pip_name = _PIP_PACKAGE_MAP.get(cli_name, cli_name)
                cmd = [sys.executable, "-m", "pip", "install", "--upgrade", pip_name]
                result.actions_taken.append(f"Attempting pip install (fallback): {pip_name}")
                rc, _, err = await self._run_subprocess(cmd, timeout=120)
                if rc != 0:
                    result.errors.append(f"pip install failed: {err[:200]}")

        # 2. 修复缺失的依赖
        for dep in before.missing_dependencies:
            result.actions_taken.append(f"Installing missing dependency: {dep}")
            rc, _, err = await self._run_subprocess(
                [sys.executable, "-m", "pip", "install", dep], timeout=60
            )
            if rc == 0:
                result.actions_taken.append(f"Installed {dep}")
            else:
                result.errors.append(f"Failed to install {dep}: {err[:200]}")

        # 3. 修复权限问题
        if before.cli_exists and not before.cli_executable:
            try:
                os.chmod(before.cli_path, 0o755)
                result.actions_taken.append(f"Fixed execute permission: {before.cli_path}")
            except Exception as exc:
                result.errors.append(f"Failed to fix permission: {exc}")

        # 4. 配置问题只记录建议，不自动修改
        for issue in before.config_issues:
            if "No model configured" in issue:
                result.actions_taken.append("Suggestion: configure a model for this agent")
            elif "Invalid timeout" in issue:
                result.actions_taken.append("Suggestion: set a valid timeout_s (>=1)")

        # 修复后诊断
        after = await self.diagnose(agent_name, agent_config)
        result.diagnosis_after = after.model_dump()

        # 判断是否成功
        result.success = (
            after.cli_exists
            and after.cli_executable
            and not after.missing_dependencies
        )
        if after.overall_status == "healthy":
            result.success = True

        return result
