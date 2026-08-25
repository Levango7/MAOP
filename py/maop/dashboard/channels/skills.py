"""Skills & versions endpoints for :class:`DataProxy`."""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

logger = logging.getLogger(__name__)


class SkillsMixin:
    """Skills list and version check endpoints.

    Provides:
        - ``skills_list``  — skills derived from tool_manager registry
        - ``versions_check`` — MAOP/python version info
    """

    if TYPE_CHECKING:
        # 宿主类（DataProxy）提供的属性与方法 —— 仅用于类型检查
        _root: Path
        _record_latency: Callable[..., None]


    async def skills_list(self) -> list[dict[str, Any]]:
        """Skills list — derived from tool_manager registry."""
        start = time.monotonic()
        try:
            from maop.core.agent.tools.tool_manager import ToolManager
            mgr = ToolManager(root_dir=self._root)
            tools = mgr.list()
            result = []
            for cat_group in tools:
                if cat_group.get("category") in ("skill", "skills"):
                    result.extend(cat_group.get("tools", []))
        except Exception as exc:
            logger.warning("[bridge] skills_list failed: %s", exc)
            result = []
        self._record_latency(start)
        return result

    async def versions_check(self) -> dict[str, Any]:
        """Version check — returns real MAOP version from package."""
        start = time.monotonic()
        try:
            from maop import __version__ as MAOP_ver
        except ImportError:
            MAOP_ver = "unknown"
        import sys as _sys
        result = {
            "MAOP_VERSION": MAOP_ver,
            "python": _sys.version.split()[0],
            "ps_bridge_active": False,
            "checked_at": datetime.now(timezone.utc).isoformat(),
        }
        self._record_latency(start)
        return result