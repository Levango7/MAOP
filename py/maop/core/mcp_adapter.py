"""MAOP MCP Adapter — bridges MCP servers to the AgentAdapter ABC.

F5a (2026-07-22, Phase F): provides a single-server MCP adapter so an
MCP server can be registered with ``AgentBridge`` and dispatched via
the unified ``bridge.call(name, task)`` API alongside other agent
backends (Claude CLI, OpenAI, A2A, etc.).

Design notes:

  - The ``AgentAdapter`` ABC is *synchronous* (``connect``, ``execute``,
    ``health_check``, ``sync_config``, ``disconnect`` all return
    plain values, not coroutines). The MCP client stack (``MCPHub``,
    ``MCPClient``) is *async-only*. To bridge the gap without
    modifying the ABC, ``MCPAdapter`` runs every MCP operation on a
    dedicated background event loop (``_BackgroundLoop``). The loop
    lives in a daemon thread so it never blocks the caller and is
    cleaned up on ``disconnect()``.

  - ``execute(task, **kwargs)`` semantics:
      * ``task``    → MCP tool name (e.g. ``"read_file"``)
      * ``kwargs``  → tool arguments (passed as the ``arguments`` dict)
    The MCP result is stringified so it matches the ``-> str`` contract.

  - ``sync_config(config)`` updates the in-memory ``MCPServerConfig``
    but does NOT reconnect — the caller must invoke ``disconnect()``
    followed by ``connect()`` for the new config to take effect.
    This matches the ABC's documented behavior ("push configuration")
    and avoids surprising callers with implicit reconnects.

See ``docs/adr/013-agent-llm-direct-cli-fallback.md``.
"""

from __future__ import annotations

import asyncio
import logging
import threading
from pathlib import Path
from typing import TYPE_CHECKING, Any

from maop.core.agent_bridge import AgentAdapter

if TYPE_CHECKING:
    from maop.core.mcp_hub import MCPHub, MCPServerConfig

logger = logging.getLogger(__name__)


# ── Background event loop bridge ─────────────────────────────────


class _BackgroundLoop:
    """Run coroutines on a dedicated background event loop.

    A daemon thread hosts an ``asyncio`` loop that is kept alive for
    the lifetime of this object. ``run(coro)`` submits a coroutine
    from any thread and blocks the caller until the result is ready.

    This avoids the ``asyncio.run()``-inside-running-loop conflict
    that arises when ``AgentAdapter``'s sync methods are called from
    an async context (e.g., a FastAPI route handler).
    """

    def __init__(self) -> None:
        self._loop: asyncio.AbstractEventLoop = asyncio.new_event_loop()
        self._ready = threading.Event()
        self._thread = threading.Thread(
            target=self._run, name="mcp-adapter-bg", daemon=True,
        )
        self._thread.start()
        # Wait until the loop is actually running before returning —
        # otherwise the first ``run()`` call may race the loop startup.
        self._ready.wait(timeout=5.0)

    def _run(self) -> None:
        asyncio.set_event_loop(self._loop)
        self._loop.call_soon(self._ready.set)
        try:
            self._loop.run_forever()
        finally:
            try:
                self._loop.close()
            except Exception:
                pass

    def run(self, coro: Any, timeout: float = 30.0) -> Any:
        """Submit ``coro`` to the background loop and block on its result."""
        if not self._loop.is_running():
            raise RuntimeError("background loop is not running")
        future = asyncio.run_coroutine_threadsafe(coro, self._loop)
        return future.result(timeout=timeout)

    def shutdown(self) -> None:
        """Stop the background loop and join the thread (best-effort)."""
        if self._loop.is_running():
            try:
                self._loop.call_soon_threadsafe(self._loop.stop)
            except RuntimeError:
                pass  # loop already stopped
        self._thread.join(timeout=5.0)


# ── MCPAdapter ───────────────────────────────────────────────────


class MCPAdapter(AgentAdapter):
    """Adapter that exposes an MCP server as an ``AgentAdapter``.

    Wraps a single MCP server connection (via ``MCPHub``) so it can be
    registered with ``AgentBridge`` and dispatched through the unified
    sync ``bridge.call(name, task)`` API.

    Parameters
    ----------
    server_config : MCPServerConfig | dict
        Either an ``MCPServerConfig`` instance or a dict with the same
        shape (``name``, ``transport``, ``command``, ``args``, ``env``,
        ``url``, ``timeout``). Dicts are convenient for YAML config
        loading; the adapter coerces them to ``MCPServerConfig``.
    root_dir : str | Path | None
        MAOP project root (forwarded to ``MCPHub``). When None, MCPHub
        uses its default discovery.

    Usage::

        from maop.core.mcp_adapter import MCPAdapter
        from maop.core.agent_bridge import AgentBridge

        bridge = AgentBridge(root_dir="/path/to/MAOP")
        bridge.register("fs", MCPAdapter({
            "name": "filesystem",
            "transport": "stdio",
            "command": "npx",
            "args": ["-y", "@modelcontextprotocol/server-filesystem", "/tmp"],
        }, root_dir="/path/to/MAOP"))
        bridge.connect_all()
        result = bridge.call("fs", "read_file", path="/tmp/test.txt")
    """

    def __init__(
        self,
        server_config: Any,
        root_dir: str | Path | None = None,
    ) -> None:
        # Lazy import so ``maop.core.mcp_adapter`` can be imported even
        # if MCP stack has optional deps that aren't installed.
        from maop.core.mcp_hub import MCPHub, MCPServerConfig, TransportType

        # Coerce dict -> MCPServerConfig (handle transport as str or enum)
        if isinstance(server_config, dict):
            cfg_dict = dict(server_config)  # shallow copy
            t = cfg_dict.get("transport")
            if isinstance(t, str):
                cfg_dict["transport"] = TransportType(t)
            server_config = MCPServerConfig(**cfg_dict)
        elif not isinstance(server_config, MCPServerConfig):
            raise TypeError(
                f"server_config must be MCPServerConfig or dict, got "
                f"{type(server_config).__name__}"
            )

        self._server_config: MCPServerConfig = server_config
        # MCPHub requires a root_dir (used for SQLite DB placement).
        # Default to cwd when the caller didn't provide one.
        effective_root = root_dir if root_dir is not None else Path.cwd()
        self._hub: MCPHub = MCPHub(root_dir=effective_root)
        self._server_id: str | None = None
        self._bg: _BackgroundLoop = _BackgroundLoop()

    # ── AgentAdapter ABC implementation ──────────────────────

    def connect(self) -> bool:
        """Connect to the MCP server. Returns True on success.

        Idempotent: returns True immediately if already connected.
        On failure, logs the error and returns False (no exception
        raised, matching the ABC contract).
        """
        if self._server_id is not None:
            return True
        try:
            server_id = self._bg.run(self._hub.connect(self._server_config))
            if server_id:
                self._server_id = server_id
                logger.info(
                    "[mcp_adapter] connected to '%s' (id=%s)",
                    self._server_config.name, server_id,
                )
                return True
            logger.warning("[mcp_adapter] connect returned empty server_id")
            return False
        except Exception as exc:
            logger.warning("[mcp_adapter] connect failed: %s", exc)
            return False

    def execute(self, task: str, **kwargs: Any) -> str:
        """Execute a tool on the MCP server.

        Parameters
        ----------
        task : str
            MCP tool name (e.g. ``"read_file"``).
        **kwargs : Any
            Tool arguments, passed as the ``arguments`` dict to
            ``MCPHub.call_tool``.

        Returns
        -------
        str
            Stringified tool output. On failure, returns
            ``"[MCP error] <message>"`` so the caller sees a clear
            error indicator without an exception propagating.
        """
        if self._server_id is None:
            raise RuntimeError(
                "MCPAdapter not connected — call connect() before execute()"
            )
        try:
            result = self._bg.run(
                self._hub.call_tool(self._server_id, task, kwargs)
            )
        except Exception as exc:
            logger.warning("[mcp_adapter] execute('%s') failed: %s", task, exc)
            return f"[MCP error] {exc}"

        # MCPHub.call_tool returns a ToolResult with ``content`` (list
        # of content dicts, each like {"type": "text", "text": "..."}),
        # ``is_error`` (bool), and ``error_message`` (str). Extract the
        # textual content for the success path; surface the error
        # message for the failure path.
        is_error = bool(getattr(result, "is_error", False))
        if is_error:
            err_msg = getattr(result, "error_message", "") or str(result)
            return f"[MCP error] {err_msg}"

        content = getattr(result, "content", None)
        if not content:
            return ""
        if isinstance(content, list):
            # Join text-type content items; fall back to str() for others.
            parts: list[str] = []
            for item in content:
                if isinstance(item, dict):
                    if item.get("type") == "text" and "text" in item:
                        parts.append(str(item["text"]))
                    else:
                        parts.append(str(item))
                else:
                    parts.append(str(item))
            return "\n".join(parts)
        return str(content)

    def health_check(self) -> bool:
        """Return True if the server is currently connected and healthy."""
        if self._server_id is None:
            return False
        try:
            return bool(self._bg.run(self._hub.health_check(self._server_id)))
        except Exception as exc:
            logger.debug("[mcp_adapter] health_check failed: %s", exc)
            return False

    def sync_config(self, config: dict[str, Any]) -> None:
        """Push updated configuration into the in-memory MCPServerConfig.

        Only known fields are applied (``name``, ``transport``,
        ``command``, ``args``, ``env``, ``url``, ``timeout``,
        ``enabled``, ``auto_connect``). Unknown keys are silently
        ignored to keep the API forgiving.

        Note: this does NOT reconnect. To apply the new config to a
        live connection, call ``disconnect()`` then ``connect()``.
        """
        for key, value in config.items():
            if hasattr(self._server_config, key):
                setattr(self._server_config, key, value)
                logger.debug(
                    "[mcp_adapter] config updated: %s=%r (reconnect to apply)",
                    key, value,
                )

    def disconnect(self) -> None:
        """Disconnect from the MCP server and shut down the background loop.

        After ``disconnect()``, the adapter cannot be reused —
        construct a new ``MCPAdapter`` to reconnect. This matches
        ``AgentBridge.unregister``'s expectation that ``disconnect()``
        fully releases resources.
        """
        if self._server_id is not None:
            try:
                self._bg.run(self._hub.disconnect(self._server_id))
            except Exception as exc:
                logger.warning("[mcp_adapter] disconnect failed: %s", exc)
            self._server_id = None
        self._bg.shutdown()

    # ── Convenience accessors (not part of the ABC) ─────────

    @property
    def server_name(self) -> str:
        return self._server_config.name

    @property
    def is_connected(self) -> bool:
        return self._server_id is not None

    @property
    def server_config(self) -> MCPServerConfig:
        return self._server_config
