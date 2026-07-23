"""MAOP MCP Registry — Manage multiple MCP server connections.

Central registry for all MCP client connections. Provides:
  - Unified tool namespace (server_name.tool_name)
  - Server lifecycle management (connect/disconnect/reconnect)
  - Tool discovery across all connected servers
  - Tool routing (find the right server for a tool)
  - SQLite persistence for server configurations
"""


from __future__ import annotations


# ── t14: Deprecation notice ──────────────────────────────────────────────
# This module is part of the "Stack B" MCP implementation that duplicates
# concepts already provided by maop.core.mcp_hub (the canonical "Stack A").
#
# Duplicated concepts:
#   - Transport: StdioTransport/SSETransport  ↔  mcp_hub._StdioTransport/_SSETransport
#   - Config:    MCPServerConfig              ↔  mcp_hub.MCPServerConfig
#   - Models:    MCPToolDef/MCPResourceDef/... ↔  mcp_hub.MCPTool/MCPResource/...
#
# Migration path (future PR):
#   1. Align class names with mcp_hub (or add aliases).
#   2. Re-export from mcp_hub instead of maintaining separate impls.
#   3. Update function_call.py / tool_schema.py to import from mcp_hub.
#   4. Remove this file once all callers have migrated.
#
# Until then, this module remains fully functional but emits a one-time
# DeprecationWarning on first import.
import warnings as _warnings_t14
_warnings_t14.warn(
    f"{__name__} is part of the deprecated Stack B MCP implementation; "
    "prefer maop.core.mcp_hub for new code. See module docstring for "
    "the migration path.",
    DeprecationWarning,
    stacklevel=2,
)
del _warnings_t14

import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


from maop.core.db_utils import get_db_path, sqlite_connect
from maop.core.mcp_client import MCPClient, MCPServerConfig, MCPToolDef, MCPToolResult, MCPServerStatus

logger = logging.getLogger(__name__)


# ── Parallel Implementation Note ──────────────────────────────
# NOTE: MCPRegistry is one of two parallel MCP management implementations.
# The other is MCPHub in maop/core/mcp_hub.py.
# Both have production callers:
#   - MCPRegistry (this class): used by core/function_call.py, core/tool_schema.py
#   - MCPHub: used by core/mcp_adapter.py, core/mcp_discovery.py
# Future work: consider merging into a single canonical implementation.

class MCPRegistry:
    """Central registry for MCP server connections.

    Usage::

        registry = MCPRegistry(root_dir="/path/to/MAOP")
        await registry.add_server(MCPServerConfig(name="fs", command="npx", args=["-y", "@modelcontextprotocol/server-filesystem", "/tmp"]))
        await registry.connect_server("fs")
        tools = registry.all_tools()
        result = await registry.call_tool("fs.read_file", {"path": "/tmp/test.txt"})
    """

    def __init__(self, root_dir: str | Path) -> None:
        self._root = Path(root_dir)
        self._clients: dict[str, MCPClient] = {}
        self._db_path = get_db_path("mcp_registry")
        self._ensure_db()

    def _ensure_db(self) -> None:
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS mcp_servers (
                    name TEXT PRIMARY KEY,
                    config TEXT NOT NULL,
                    created TEXT NOT NULL,
                    updated TEXT
                )
            """)

    def _connect(self):
        return sqlite_connect(self._db_path, foreign_keys=False)

    def add_server(self, config: MCPServerConfig) -> MCPClient:
        client = MCPClient(config)
        self._clients[config.name] = client
        now = datetime.now(timezone.utc).isoformat()
        with self._connect() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO mcp_servers (name, config, created, updated) VALUES (?, ?, ?, ?)",
                (config.name, config.model_dump_json(), now, now),
            )
        logger.info("MCP server registered: %s", config.name)
        return client

    def remove_server(self, name: str) -> bool:
        client = self._clients.pop(name, None)
        if client is None:
            return False
        with self._connect() as conn:
            conn.execute("DELETE FROM mcp_servers WHERE name=?", (name,))
        logger.info("MCP server removed: %s", name)
        return True

    async def connect_server(self, name: str) -> bool:
        client = self._clients.get(name)
        if client is None:
            logger.warning("MCP server '%s' not found", name)
            return False
        return await client.connect()

    async def disconnect_server(self, name: str) -> None:
        client = self._clients.get(name)
        if client:
            await client.disconnect()

    async def connect_all(self) -> dict[str, bool]:
        results = {}
        for name, client in self._clients.items():
            if client._config.enabled or client._config.auto_connect:
                results[name] = await client.connect()
        return results

    async def disconnect_all(self) -> None:
        for client in self._clients.values():
            await client.disconnect()

    def get_client(self, name: str) -> MCPClient | None:
        return self._clients.get(name)

    def list_servers(self) -> list[dict[str, Any]]:
        return [client.stats for client in self._clients.values()]

    def all_tools(self) -> list[MCPToolDef]:
        tools = []
        for client in self._clients.values():
            tools.extend(client.tools)
        return tools

    def find_tool(self, qualified_name: str) -> tuple[MCPClient | None, str]:
        if "." in qualified_name:
            server_name, tool_name = qualified_name.split(".", 1)
            client = self._clients.get(server_name)
            if client:
                return client, tool_name
        for client in self._clients.values():
            for t in client.tools:
                if t.name == qualified_name:
                    return client, t.name
        return None, qualified_name

    async def call_tool(self, qualified_name: str, arguments: dict[str, Any] | None = None) -> MCPToolResult:
        client, tool_name = self.find_tool(qualified_name)
        if client is None:
            return MCPToolResult(success=False, error=f"Tool '{qualified_name}' not found in any connected server")
        if client.status != MCPServerStatus.CONNECTED:
            return MCPToolResult(success=False, error=f"Server '{client.name}' is not connected")
        return await client.call_tool(tool_name, arguments)

    def load_from_db(self) -> int:
        count = 0
        try:
            with self._connect() as conn:
                rows = conn.execute("SELECT name, config FROM mcp_servers").fetchall()
            for row in rows:
                try:
                    config = MCPServerConfig.model_validate_json(row["config"])
                    if config.name not in self._clients:
                        self._clients[config.name] = MCPClient(config)
                        count += 1
                except Exception as exc:
                    logger.warning("Failed to load MCP server '%s': %s", row["name"], exc)
        except Exception as exc:
            logger.warning("Failed to load MCP servers from DB: %s", exc)
        return count


_registry: MCPRegistry | None = None


def get_mcp_registry(root_dir: str | Path | None = None) -> MCPRegistry:
    global _registry
    if _registry is None:
        if root_dir is None:
            from pathlib import Path as _P
            root_dir = _P(__file__).resolve().parent.parent.parent
        _registry = MCPRegistry(root_dir=root_dir)
        _registry.load_from_db()
    return _registry
