"""MAOP Delegate Models — AgentConfig, DispatchResult, and security helpers.

Extracted from dispatcher.py for single-responsibility separation.
"""

from __future__ import annotations

import re

from pydantic import BaseModel, ConfigDict, Field

from maop.core.reliability.error_schema import MaopResult

# ── Models ────────────────────────────────────────────────────

class AgentConfig(BaseModel):
    """Agent definition from agents.yaml."""
    name: str
    cli: str = ""
    driver: str = "cli"  # cli | wrapper | powershell | cmd | desktop_app | ide_extension | vibe_coding
    cli_args: str = ""
    capabilities: list[str] = Field(default_factory=list)
    timeout_s: int = 180
    model: str | None = None
    # F2a (2026-07-22, Phase F): LLM provider name for direct API path.
    # When non-empty + model is set, ReactLoop tries LLM direct call first
    # and falls back to CLI driver on failure (ADR-013 dual-path). Empty
    # by default — preserves prior CLI-only behavior.
    provider: str = ""
    wrapper: str = ""
    command: str = ""
    env: dict[str, str] = Field(default_factory=dict)
    supports_vision: bool = False
    image_arg_template: str = ""
    # ── 桌面应用驱动专用字段（仅 driver=desktop_app 时有意义，其他 driver 忽略）──
    # 桌面应用进程名（如 "Cursor.exe"），用于检测应用是否运行
    process_name: str | None = None
    # IPC 通信路径：Windows Named Pipe 或 Unix Socket 文件路径
    ipc_path: str | None = None
    # 本地 HTTP 端口 URL（如 "http://localhost:3456"）
    http_url: str | None = None
    # 通信方式回退顺序（如 ["cli", "ipc", "http"]）
    fallback_order: list[str] | None = None


class DispatchResult(BaseModel):
    """Envelope returned by Dispatcher.dispatch()."""
    model_config = ConfigDict(protected_namespaces=())
    result: MaopResult
    driver_used: str = ""
    breaker_tripped: bool = False
    model_resolved: bool = True  # False if ModelSelector was configured but failed


# ── Security helpers ──────────────────────────────────────────

def _escape_for_cmd(s: str) -> str:
    """Escape string for cmd.exe /c context: & | ( ) < > ^ % " ! newline."""
    # Strip null bytes first to prevent injection via truncation
    s = s.replace('\x00', '')
    # 转义所有 cmd.exe 特殊字符，包括 ! (延迟变量扩展)
    s = re.sub(r'([\^&|<>()%"!])', r'^\1', s)
    s = s.replace("\n", "^\n").replace("\r", "")
    return s


def _escape_for_ps_command(s: str) -> str:
    """Escape string for PowerShell -Command context (single-quote).

    Single-quoting prevents variable expansion ($var) and command substitution ($(cmd)).
    However, single quotes themselves must be escaped as ''.
    We also strip null bytes to prevent potential injection.
    """
    # Strip null bytes
    s = s.replace('\x00', '')
    # Escape single quotes by doubling them
    return "'" + s.replace("'", "''") + "'"
