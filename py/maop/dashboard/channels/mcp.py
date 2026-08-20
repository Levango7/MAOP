"""Tool / sandbox / MCP endpoints for :class:`DataProxy`."""

from __future__ import annotations

import logging
import time
from typing import Any

logger = logging.getLogger(__name__)


class McpMixin:
    """Tool manager, sandbox, and MCP server endpoints.

    Provides:
        - ``tools_stats``   — tool manager statistics
        - ``tools_list``    — tool list
        - ``sandbox_list``  — sandbox list
        - ``mcp_servers``   — MCP servers from config
        - ``mcp_tools``     — MCP tools from config
    """

    async def tools_stats(self) -> dict[str, Any]:
        """Tool manager statistics — replaces tool-manager.ps1 -Action stats."""
        start = time.monotonic()
        try:
            if self._tool_mgr is None:
                from maop.core.agent.tools.tool_manager import ToolManager
                self._tool_mgr = ToolManager(root_dir=self._root)
            assert self._tool_mgr is not None
            result: dict[str, Any] = self._tool_mgr.stats()
        except Exception as exc:
            logger.warning("[bridge] tools_stats failed: %s", exc)
            result = {"total": 0, "enabled": 0, "disabled": 0, "total_calls": 0}
        self._record_latency(start)
        return result

    async def tools_list(self) -> list[dict[str, Any]]:
        """Tool list — replaces tool-manager.ps1 -Action list."""
        start = time.monotonic()
        try:
            if self._tool_mgr is None:
                from maop.core.agent.tools.tool_manager import ToolManager
                self._tool_mgr = ToolManager(root_dir=self._root)
            assert self._tool_mgr is not None
            result: list[Any] = self._tool_mgr.list()
        except Exception as exc:
            logger.warning("[bridge] tools_list failed: %s", exc)
            result = []
        self._record_latency(start)
        return result

    async def sandbox_list(self) -> list[dict[str, Any]]:
        """Sandbox list — replaces sandbox.ps1 -Action list."""
        start = time.monotonic()
        try:
            if self._sandbox_mgr is None:
                from maop.core.security.sandbox import SandboxManager
                self._sandbox_mgr = SandboxManager(root_dir=self._root)
            sandboxes = self._sandbox_mgr.list_all()
            result = [s.model_dump() for s in sandboxes]
        except Exception as exc:
            logger.warning("[bridge] sandbox_list failed: %s", exc)
            result = []
        self._record_latency(start)
        return result

    async def mcp_servers(self) -> list[dict[str, Any]]:
        """MCP servers list — from config if available."""
        start = time.monotonic()
        mcp_config = self._root / "config" / "mcp_servers.yaml"
        result = []
        if mcp_config.exists():
            try:
                import yaml
                data = yaml.safe_load(mcp_config.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    result = data.get("servers", [])
            except Exception as exc:
                # H1: log instead of silently swallowing
                logger.warning("[bridge] silent except: %s", exc)
        self._record_latency(start)
        return result

    async def mcp_tools(self) -> list[dict[str, Any]]:
        """MCP tools list — from config if available."""
        start = time.monotonic()
        mcp_config = self._root / "config" / "mcp_servers.yaml"
        result = []
        if mcp_config.exists():
            try:
                import yaml
                data = yaml.safe_load(mcp_config.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    for server in data.get("servers", []):
                        if isinstance(server, dict):
                            result.extend(server.get("tools", []))
            except Exception as exc:
                # H1: log instead of silently swallowing
                logger.warning("[bridge] silent except: %s", exc)
        self._record_latency(start)
        return result