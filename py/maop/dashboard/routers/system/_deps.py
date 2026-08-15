"""Shared dependencies for the system router subpackage.

Centralizes module-level state (MAOP_ROOT, active_jobs, start_time, …),
lazy bridge/subsystem helpers, and pure utility functions
(_run_subprocess, _dir_size_mb, _pct, _get_allowed_packages, …) so that
all sub-routers (framework / agent_admin / overview / workflow / v4_misc)
share a single source of truth.

Backward-compatibility note:
    Tests historically monkeypatched ``maop.dashboard.routers.system.xxx``
    symbols.  Those attributes are re-exported from the package ``__init__``
    but sub-routers always call them through ``_deps.xxx`` so that patching
    ``maop.dashboard.routers.system._deps.xxx`` takes effect regardless of
    which sub-router the endpoint lives in.
"""

from __future__ import annotations

import asyncio
import logging
from typing import cast

from maop import __version__ as MAOP_VERSION  # noqa: F401  — re-exported
from maop.core.backends.db_utils import get_db_path  # noqa: F401  — re-exported
from maop.core.security.middleware import require_admin  # noqa: F401  — re-exported
from maop.dashboard.error_handler import handle_api_errors  # noqa: F401  — re-exported
from maop.dashboard.routers.state import (  # noqa: F401  — re-exported
    MAOP_ROOT,
    active_jobs,
    get_bridge,
    get_subsystems,
    init_subsystems,
    start_time,
)

logger = logging.getLogger("maop.dashboard.routers.system")


def _count_file_lines(path) -> int:
    """Count lines in a file (sync helper for asyncio.to_thread)."""
    with open(path, encoding="utf-8", errors="replace") as fh:
        return sum(1 for _ in fh)


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
            return (
                cast(int, proc.returncode),
                stdout_b.decode("utf-8", errors="replace"),
                stderr_b.decode("utf-8", errors="replace"),
            )
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