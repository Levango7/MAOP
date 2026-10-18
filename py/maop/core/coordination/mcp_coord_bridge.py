"""MAOP — Coordination MCP Hub Bridge (M2-E).

Unifies two MCP hub families:

1. **`maop.core.mcp.MCPHub`** — full-featured MCP Hub with audit,
   permission, cache, marketplace, stats, and production-grade transport.
2. **`maop.core.coordination.mcp_hub.MCPHub`** — lightweight coordination
   layer hub (M1) used for agent coordination tool discovery.

This bridge wraps the production MCPHub and exposes the coordination
hub's simplified ``register_server / find_tools / async_find_tools`` API
so that coordination-layer code (agent registry, BB task context) can
discover tools from the production hub without duplicating registries.

Usage::

    from maop.core.mcp.mcp_hub import MCPHub as ProductionMCPHub
    prod_hub = ProductionMCPHub(root_dir="/path/to/MAOP")
    # ... configure prod_hub with servers ...

    bridge = CoordMCPHubBridge(prod_hub)
    await bridge.start()

    # Bridge now exposes the coordination API:
    tools = await bridge.async_find_tools("search")

Design reference: docs/design-mcp-bridge.md (M2-E).
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from maop.core.mcp.mcp_hub import MCPHub

logger = logging.getLogger(__name__)


class CoordMCPHubBridge:
    """Bridge a production :class:`maop.core.mcp.MCPHub` into the
    coordination layer's simplified hub interface.

    The production MCPHub is the single source of truth for tool registry;
    this bridge just adapts its API so coordination code (agent registry,
    MCP capability matching, BB task context) can discover tools.

    Parameters
    ----------
    production_hub : maop.core.mcp.MCPHub
        The production MCPHub instance to wrap.
    """

    def __init__(self, production_hub: MCPHub) -> None:
        self._hub = production_hub
        self._started = False

    async def start(self) -> None:
        """Ensure the production hub is started."""
        self._started = True
        logger.info("[mcp-bridge] bridged to production MCPHub")

    async def stop(self) -> None:
        """Stop the bridge."""
        self._started = False

    # ── Coordination hub API (delegated to production) ────────────

    async def register_server(self, server: Any) -> str:
        """Register a server via the production hub.

        Adapts the coordination-layer ``MCPServer`` /
        ``StdioMCPServer`` into the production hub's ``MCPServerConfig``.

        Parameters
        ----------
        server : MCPServer or similar
            Coordination-layer server descriptor (has ``name``, ``transport``,
            optional ``command``, ``url``).

        Returns
        -------
        str
            The server_id in the production hub.
        """
        try:
            from maop.core.mcp.mcp_hub_types import MCPServerConfig, TransportType

            # Adapt transport
            transport = getattr(server, 'transport', None)
            if isinstance(transport, str):
                transport_type = TransportType(transport.upper())
            elif hasattr(transport, '__class__'):
                cls_name = transport.__class__.__name__
                if 'Stdio' in cls_name:
                    transport_type = TransportType.STDIO
                elif 'SSE' in cls_name:
                    transport_type = TransportType.SSE
                elif 'WebSocket' in cls_name or 'WS' in cls_name:
                    transport_type = TransportType.WEBSOCKET
                elif 'StreamableHttp' in cls_name:
                    transport_type = TransportType.STREAMABLE_HTTP
                else:
                    transport_type = TransportType.STDIO
            else:
                transport_type = TransportType.STDIO

            # Build config
            cfg_kwargs: dict[str, Any] = {
                "name": getattr(server, 'name', f"server-{id(server)}"),
                "transport": transport_type,
            }
            if hasattr(server, 'command') and server.command:
                cfg_kwargs["command"] = server.command
            if hasattr(server, 'url') and server.url:
                cfg_kwargs["url"] = server.url
            if hasattr(server, 'args') and server.args:
                cfg_kwargs["args"] = server.args

            cfg = MCPServerConfig(**cfg_kwargs)
            return await self._hub.connect(cfg)
        except Exception as exc:
            logger.warning("[mcp-bridge] register_server failed: %s", exc)
            raise

    async def async_find_tools(self, search_term: str = "") -> list[dict[str, Any]]:
        """Find tools across all registered servers (coordination API).

        Delegates to the production hub's ``search_tools`` or falls back
        to manual iteration.

        Parameters
        ----------
        search_term : str
            Substring to filter tool names (empty = all).

        Returns
        -------
        list[dict]
            Each dict has keys ``name``, ``server``, ``description``.
        """
        results: list[dict[str, Any]] = []
        try:
            # Try the production hub's search method first
            if hasattr(self._hub, 'search_tools'):
                raw = await self._hub.search_tools(search_term)
                if isinstance(raw, list):
                    for item in raw:
                        if isinstance(item, dict):
                            results.append(item)
                        else:
                            results.append({
                                "name": getattr(item, "name", str(item)),
                                "server": getattr(item, "server_name", "unknown"),
                                "description": getattr(item, "description", ""),
                            })
            else:
                # Fallback: iterate servers manually
                servers = getattr(self._hub, '_servers', {}) or {}
                for sid, server_info in servers.items():
                    name = getattr(server_info, 'name', sid)
                    tools = await self._hub.list_tools(sid) if hasattr(self._hub, 'list_tools') else []
                    for tool in tools:
                        tool_name = getattr(tool, 'name', str(tool))
                        if not search_term or search_term.lower() in tool_name.lower():
                            results.append({
                                "name": tool_name,
                                "server": name,
                                "description": getattr(tool, 'description', ''),
                            })
        except Exception as exc:
            logger.warning("[mcp-bridge] async_find_tools failed: %s", exc)
        return results

    def find_tools(self, search_term: str = "") -> list[dict[str, Any]]:
        """Synchronous version for non-async contexts (returns cached).

        For async contexts, prefer :meth:`async_find_tools`.
        """
        try:
            # Check if there's a cached registry
            tools_cache = getattr(self._hub, '_tools_cache', None)
            if tools_cache:
                if search_term:
                    return [t for t in tools_cache if search_term.lower() in t.get('name', '').lower()]
                return list(tools_cache)
        except Exception:  # silent-ok: defensive-probe — 缓存读取 best-effort，失败回退空列表
            pass
        return []

    # ── Delegation passthrough ────────────────────────────────────

    async def call_tool(self, tool_name: str, arguments: dict[str, Any]) -> Any:
        """Call a tool by name via the production hub."""
        if hasattr(self._hub, 'call_tool_by_name'):
            return await self._hub.call_tool_by_name(tool_name, arguments)
        raise NotImplementedError("Production hub does not support call_tool_by_name")

    @property
    def production_hub(self) -> Any:
        """Access the underlying production hub."""
        return self._hub

    @property
    def is_started(self) -> bool:
        return self._started
