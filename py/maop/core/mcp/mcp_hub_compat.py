"""MCPHub — 名称兼容 shims mixin（Stack A/B 统一）。

T2 架构债治理：从 ``mcp_hub.py`` 拆分。公开 API 不变。
"""

from __future__ import annotations

import asyncio
import json
import logging
import time as _time
import uuid
from typing import Any

from maop.core.mcp.mcp_hub_types import (
    MCPServerConfig,
    MCPTool,
    ServerStatus,
    ToolResult,
)

logger = logging.getLogger(__name__)


class MCPHubCompatMixin:
    """名称兼容 shims（get_server_config/add_server/all_tools 等）。"""


    def get_server_config(self, name: str) -> MCPServerConfig | None:
        """Look up a stored server config by name."""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT config FROM mcp_servers WHERE name = ? ORDER BY updated_at DESC LIMIT 1",
                (name,),
            ).fetchone()
        if row is None:
            return None
        try:
            return MCPServerConfig.model_validate_json(row["config"])
        except Exception:
            logger.debug("Silent exception in core/mcp_hub.py:1594", exc_info=True)
            return None

    def find_server_id_by_name(self, name: str) -> str | None:
        """Look up a server_id by name (most recent record)."""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT id FROM mcp_servers WHERE name = ? ORDER BY updated_at DESC LIMIT 1",
                (name,),
            ).fetchone()
        return row["id"] if row else None

    def add_server(self, config: MCPServerConfig) -> bool:
        """Register a server config without connecting (compat shim)."""
        now = _time.time()
        with self._connect() as conn:
            conn.execute("DELETE FROM mcp_servers WHERE name = ?", (config.name,))
            server_id = uuid.uuid4().hex[:16]
            conn.execute(
                """INSERT INTO mcp_servers (id, name, transport, status, config, error, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (server_id, config.name, config.transport.value,
                 ServerStatus.DISCONNECTED.value, config.model_dump_json(),
                 "", now, now),
            )
        return True

    def remove_server(self, name: str) -> bool:
        """Remove a registered server by name (compat shim)."""
        server_id = self.find_server_id_by_name(name)
        if server_id is None:
            return False
        transport = self._transports.pop(server_id, None)
        if transport is not None:
            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                loop = None
            if loop is not None:
                # High fix: keep a strong reference to the stop task so it
                # is not garbage-collected before it runs.
                task = loop.create_task(transport.stop())
                self._stop_tasks.add(task)
                task.add_done_callback(self._stop_tasks.discard)
            else:
                # High fix: previously the RuntimeError path silently
                # skipped stop(), leaking the transport (for stdio: a
                # permanently running orphan child process). Run stop()
                # to completion on a fresh event loop instead.
                try:
                    asyncio.run(transport.stop())
                except Exception as exc:
                    logger.warning(
                        "[mcp_hub] transport.stop() failed during "
                        "remove_server('%s'): %s", name, exc,
                    )
        self._configs.pop(server_id, None)
        with self._connect() as conn:
            conn.execute("DELETE FROM mcp_servers WHERE id = ?", (server_id,))
            conn.execute("DELETE FROM mcp_tools WHERE server_id = ?", (server_id,))
            conn.execute("DELETE FROM mcp_resources WHERE server_id = ?", (server_id,))
        return True

    def all_tools(self) -> list[MCPTool]:
        """Return all tools across all servers (sync, compat shim)."""
        with self._connect() as conn:
            cursor = conn.execute("SELECT * FROM mcp_tools")
            cols = [d[0] for d in cursor.description] if cursor.description else []
            rows = cursor.fetchall()
        return [
            MCPTool(
                name=r["name"],
                description=r.get("description", ""),
                input_schema=json.loads(r.get("input_schema", "{}")),
                server_name=r.get("server_name", ""),
            )
            for r in (dict(zip(cols, row)) for row in rows)
        ]

    def find_tool(self, qualified_name: str) -> tuple[str | None, str]:
        """Find a tool by qualified name. Returns (server_id, tool_name)."""
        with self._connect() as conn:
            if "." in qualified_name:
                server_name, tool_name = qualified_name.split(".", 1)
                row = conn.execute(
                    "SELECT server_id FROM mcp_tools WHERE server_name = ? AND name = ? LIMIT 1",
                    (server_name, tool_name),
                ).fetchone()
                if row:
                    return row["server_id"], tool_name
            row = conn.execute(
                "SELECT server_id, name FROM mcp_tools WHERE name = ? LIMIT 1",
                (qualified_name,),
            ).fetchone()
            if row:
                return row["server_id"], row["name"]
        return None, qualified_name

    async def call_tool_by_name(
        self,
        qualified_name: str,
        arguments: dict[str, Any] | None = None,
        *,
        user_context: dict[str, Any] | None = None,
    ) -> ToolResult:
        """Call a tool by qualified name 'server.tool' (compat shim).

        δ-3: Forwards the optional ``user_context`` to :meth:`call_tool`
        so the dashboard router can pass the authenticated caller through
        to the permission checker without restructuring its existing
        name-based call sites.
        """
        server_id, tool_name = self.find_tool(qualified_name)
        if server_id is None:
            return ToolResult(is_error=True, error_message=f"Tool '{qualified_name}' not found")
        return await self.call_tool(server_id, tool_name, arguments or {}, user_context=user_context)  # type: ignore[no-any-return]

