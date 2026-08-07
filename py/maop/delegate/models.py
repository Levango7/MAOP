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
    driver: str = "cli"  # cli | wrapper | powershell | cmd
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


class DispatchResult(BaseModel):
    """Envelope returned by Dispatcher.dispatch()."""
    model_config = ConfigDict(protected_namespaces=())
    result: MaopResult
    driver_used: str = ""
    breaker_tripped: bool = False
    model_resolved: bool = True  # False if ModelSelector was configured but failed


# ── Security helpers ──────────────────────────────────────────

def _escape_for_cmd(s: str) -> str:
    """Escape string for cmd.exe /c context: & | ( ) < > ^ % " newline."""
    s = re.sub(r"([\^&|<>()%\"])", r"^\1", s)
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
