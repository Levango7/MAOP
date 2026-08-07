"""Tests for maop.core.mcp_adapter.MCPAdapter.

F5a (2026-07-22, Phase F): verifies the MCPAdapter correctly bridges
the sync AgentAdapter ABC to the async MCPHub API.

All tests mock MCPHub so no real subprocess/transport is started. The
_BackgroundLoop is real (a daemon thread hosting an asyncio loop) —
this is the production code path and exercises the sync→async bridge.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from maop.core.agent.delegation.agent_proxy import AgentAdapter
from maop.core.mcp.mcp_adapter import MCPAdapter, _BackgroundLoop

# ── Helpers ──────────────────────────────────────────────────────


def _config_dict() -> dict:
    """A minimal MCPServerConfig-shaped dict (transport as str)."""
    return {
        "name": "filesystem",
        "transport": "stdio",
        "command": "npx",
        "args": ["-y", "@modelcontextprotocol/server-filesystem", "/tmp"],
    }


@pytest.fixture
def mock_hub():
    """Patch MCPHub class so MCPAdapter.__init__ picks up the mock.

    Yields the mock hub instance (``MCPHub.return_value``). Every test
    that constructs an MCPAdapter must use this fixture, otherwise a
    real MCPHub would try to open a SQLite DB and start subprocesses.
    """
    with patch("maop.core.mcp.mcp_hub.MCPHub") as mock_cls:
        mock_instance = mock_cls.return_value
        # Default all hub methods to AsyncMock so coroutines resolve cleanly.
        mock_instance.connect = AsyncMock(return_value="srv-001")
        mock_instance.disconnect = AsyncMock(return_value=True)
        mock_instance.health_check = AsyncMock(return_value=True)
        mock_instance.call_tool = AsyncMock(return_value=None)
        yield mock_instance


# ── ABC contract ────────────────────────────────────────────────


def test_mcpadapter_is_agent_adapter_subclass():
    """MCPAdapter must be a proper subclass of AgentAdapter."""
    assert issubclass(MCPAdapter, AgentAdapter)


# ── _BackgroundLoop ─────────────────────────────────────────────


def test_background_loop_runs_coroutine_and_returns_result():
    """_BackgroundLoop.run must block on a coroutine and return its result."""

    async def _coro():
        return 42

    bg = _BackgroundLoop()
    try:
        assert bg.run(_coro()) == 42
    finally:
        bg.shutdown()


def test_background_loop_shutdown_stops_thread():
    """shutdown() must stop the background loop and join the thread."""
    bg = _BackgroundLoop()
    assert bg._thread.is_alive()
    bg.shutdown()
    assert not bg._thread.is_alive()


# ── Construction & config coercion ───────────────────────────────


def test_dict_config_is_coerced_to_mcpserverconfig(mock_hub):
    """A plain dict config (transport as str) must be coerced to MCPServerConfig."""
    from maop.core.mcp.mcp_hub import MCPServerConfig, TransportType

    adapter = MCPAdapter(_config_dict(), root_dir="/tmp")
    assert isinstance(adapter.server_config, MCPServerConfig)
    assert adapter.server_config.transport == TransportType.STDIO
    assert adapter.server_config.name == "filesystem"


def test_dict_config_with_sse_transport(mock_hub):
    """A dict with transport='sse' is coerced to TransportType.SSE."""
    from maop.core.mcp.mcp_hub import TransportType

    cfg = {"name": "remote", "transport": "sse", "url": "http://localhost:8080"}
    adapter = MCPAdapter(cfg, root_dir="/tmp")
    assert adapter.server_config.transport == TransportType.SSE


def test_invalid_config_raises_typeerror(mock_hub):
    """A non-dict, non-MCPServerConfig config must raise TypeError."""
    with pytest.raises(TypeError, match="server_config must be"):
        MCPAdapter(["not", "a", "dict"], root_dir="/tmp")  # type: ignore[arg-type]


def test_mcpserverconfig_instance_accepted(mock_hub):
    """An MCPServerConfig instance is accepted as-is (no coercion)."""
    from maop.core.mcp.mcp_hub import MCPServerConfig

    cfg = MCPServerConfig(name="fs", command="npx")
    adapter = MCPAdapter(cfg, root_dir="/tmp")
    assert adapter.server_config is cfg


def test_root_dir_forwarded_to_mcphub(mock_hub):
    """When root_dir is provided, MCPHub is constructed with it."""
    with patch("maop.core.mcp.mcp_hub.MCPHub") as mock_cls:
        mock_instance = mock_cls.return_value
        mock_instance.connect = AsyncMock(return_value="srv-x")
        mock_instance.disconnect = AsyncMock(return_value=True)
        mock_instance.health_check = AsyncMock(return_value=True)
        mock_instance.call_tool = AsyncMock(return_value=None)
        MCPAdapter(_config_dict(), root_dir="/custom/root")
        mock_cls.assert_called_once_with(root_dir="/custom/root")


def test_no_root_dir_uses_default_constructor(mock_hub):
    """When root_dir is None, MCPHub is constructed with Path.cwd() as root_dir."""
    with patch("maop.core.mcp.mcp_hub.MCPHub") as mock_cls:
        mock_instance = mock_cls.return_value
        mock_instance.connect = AsyncMock(return_value="srv-y")
        mock_instance.disconnect = AsyncMock(return_value=True)
        mock_instance.health_check = AsyncMock(return_value=True)
        mock_instance.call_tool = AsyncMock(return_value=None)
        MCPAdapter(_config_dict())
        # MCPHub requires root_dir; MCPAdapter defaults to cwd when None.
        mock_cls.assert_called_once()
        _, kwargs = mock_cls.call_args
        assert "root_dir" in kwargs
        from pathlib import Path
        assert Path(kwargs["root_dir"]).is_absolute()  # cwd is absolute


# ── connect() ────────────────────────────────────────────────────


def test_connect_success_returns_true_and_stores_server_id(mock_hub):
    """connect() returns True and stores the server_id returned by MCPHub.connect."""
    mock_hub.connect = AsyncMock(return_value="srv-abc")
    adapter = MCPAdapter(_config_dict(), root_dir="/tmp")
    try:
        assert adapter.connect() is True
        assert adapter._server_id == "srv-abc"
        assert adapter.is_connected is True
        mock_hub.connect.assert_awaited_once()
    finally:
        adapter._bg.shutdown()


def test_connect_failure_returns_false(mock_hub):
    """When MCPHub.connect raises, connect() returns False and server_id stays None."""
    mock_hub.connect = AsyncMock(side_effect=ConnectionError("subprocess died"))
    adapter = MCPAdapter(_config_dict(), root_dir="/tmp")
    try:
        assert adapter.connect() is False
        assert adapter._server_id is None
        assert adapter.is_connected is False
    finally:
        adapter._bg.shutdown()


def test_connect_returns_false_on_empty_server_id(mock_hub):
    """An empty/falsy server_id is treated as a connection failure."""
    mock_hub.connect = AsyncMock(return_value="")
    adapter = MCPAdapter(_config_dict(), root_dir="/tmp")
    try:
        assert adapter.connect() is False
        assert adapter._server_id is None
    finally:
        adapter._bg.shutdown()


def test_connect_is_idempotent(mock_hub):
    """Calling connect() twice does not re-invoke MCPHub.connect the second time."""
    mock_hub.connect = AsyncMock(return_value="srv-once")
    adapter = MCPAdapter(_config_dict(), root_dir="/tmp")
    try:
        assert adapter.connect() is True
        assert adapter.connect() is True
        assert mock_hub.connect.await_count == 1
    finally:
        adapter._bg.shutdown()


# ── execute() ────────────────────────────────────────────────────


def test_execute_returns_text_content_on_success(mock_hub):
    """execute() extracts text from ToolResult.content on the success path."""
    from maop.core.mcp.mcp_hub import ToolResult

    mock_hub.call_tool = AsyncMock(
        return_value=ToolResult(content=[{"type": "text", "text": "hello world"}])
    )
    adapter = MCPAdapter(_config_dict(), root_dir="/tmp")
    adapter._server_id = "srv-1"  # bypass connect
    try:
        result = adapter.execute("read_file", path="/tmp/x")
        assert result == "hello world"
        mock_hub.call_tool.assert_awaited_once()
        # Verify the tool name and arguments were forwarded correctly.
        call_args = mock_hub.call_tool.call_args
        assert call_args.args[0] == "srv-1"
        assert call_args.args[1] == "read_file"
        # kwargs are passed as the arguments dict.
        assert call_args.args[2] == {"path": "/tmp/x"}
    finally:
        adapter._bg.shutdown()


def test_execute_joins_multiple_text_items(mock_hub):
    """Multiple text content items are joined with newlines."""
    from maop.core.mcp.mcp_hub import ToolResult

    mock_hub.call_tool = AsyncMock(
        return_value=ToolResult(
            content=[
                {"type": "text", "text": "line1"},
                {"type": "text", "text": "line2"},
            ]
        )
    )
    adapter = MCPAdapter(_config_dict(), root_dir="/tmp")
    adapter._server_id = "srv-2"
    try:
        assert adapter.execute("grep") == "line1\nline2"
    finally:
        adapter._bg.shutdown()


def test_execute_returns_error_prefix_on_is_error(mock_hub):
    """When ToolResult.is_error is True, execute() returns '[MCP error] ...'."""
    from maop.core.mcp.mcp_hub import ToolResult

    mock_hub.call_tool = AsyncMock(
        return_value=ToolResult(is_error=True, error_message="file not found")
    )
    adapter = MCPAdapter(_config_dict(), root_dir="/tmp")
    adapter._server_id = "srv-3"
    try:
        result = adapter.execute("read_file", path="/missing")
        assert result == "[MCP error] file not found"
    finally:
        adapter._bg.shutdown()


def test_execute_returns_empty_string_for_empty_content(mock_hub):
    """An empty content list yields an empty string (not '[MCP error]')."""
    from maop.core.mcp.mcp_hub import ToolResult

    mock_hub.call_tool = AsyncMock(return_value=ToolResult(content=[]))
    adapter = MCPAdapter(_config_dict(), root_dir="/tmp")
    adapter._server_id = "srv-4"
    try:
        assert adapter.execute("noop") == ""
    finally:
        adapter._bg.shutdown()


def test_execute_returns_error_prefix_on_exception(mock_hub):
    """When MCPHub.call_tool raises, execute() returns '[MCP error] ...'."""
    mock_hub.call_tool = AsyncMock(side_effect=RuntimeError("transport closed"))
    adapter = MCPAdapter(_config_dict(), root_dir="/tmp")
    adapter._server_id = "srv-5"
    try:
        result = adapter.execute("read_file")
        assert result.startswith("[MCP error]")
        assert "transport closed" in result
    finally:
        adapter._bg.shutdown()


def test_execute_raises_runtimeerror_when_not_connected(mock_hub):
    """execute() raises RuntimeError if called before connect()."""
    adapter = MCPAdapter(_config_dict(), root_dir="/tmp")
    try:
        with pytest.raises(RuntimeError, match="not connected"):
            adapter.execute("anything")
    finally:
        adapter._bg.shutdown()


def test_execute_forwards_kwargs_as_arguments_dict(mock_hub):
    """**kwargs are collected and passed as the arguments dict to call_tool."""
    from maop.core.mcp.mcp_hub import ToolResult

    mock_hub.call_tool = AsyncMock(
        return_value=ToolResult(content=[{"type": "text", "text": "ok"}])
    )
    adapter = MCPAdapter(_config_dict(), root_dir="/tmp")
    adapter._server_id = "srv-6"
    try:
        adapter.execute("search", query="foo", limit=10, regex=True)
        call_args = mock_hub.call_tool.call_args
        assert call_args.args[2] == {"query": "foo", "limit": 10, "regex": True}
    finally:
        adapter._bg.shutdown()


# ── health_check() ──────────────────────────────────────────────


def test_health_check_returns_true_when_hub_says_healthy(mock_hub):
    """health_check() returns True when MCPHub.health_check returns True."""
    mock_hub.health_check = AsyncMock(return_value=True)
    adapter = MCPAdapter(_config_dict(), root_dir="/tmp")
    adapter._server_id = "srv-h1"
    try:
        assert adapter.health_check() is True
        mock_hub.health_check.assert_awaited_once_with("srv-h1")
    finally:
        adapter._bg.shutdown()


def test_health_check_returns_false_when_hub_says_unhealthy(mock_hub):
    """health_check() returns False when MCPHub.health_check returns False."""
    mock_hub.health_check = AsyncMock(return_value=False)
    adapter = MCPAdapter(_config_dict(), root_dir="/tmp")
    adapter._server_id = "srv-h2"
    try:
        assert adapter.health_check() is False
    finally:
        adapter._bg.shutdown()


def test_health_check_returns_false_on_exception(mock_hub):
    """health_check() returns False when MCPHub.health_check raises."""
    mock_hub.health_check = AsyncMock(side_effect=RuntimeError("ping timeout"))
    adapter = MCPAdapter(_config_dict(), root_dir="/tmp")
    adapter._server_id = "srv-h3"
    try:
        assert adapter.health_check() is False
    finally:
        adapter._bg.shutdown()


def test_health_check_returns_false_when_not_connected(mock_hub):
    """health_check() returns False without calling the hub when not connected."""
    mock_hub.health_check = AsyncMock(return_value=True)
    adapter = MCPAdapter(_config_dict(), root_dir="/tmp")
    try:
        assert adapter.health_check() is False
        mock_hub.health_check.assert_not_awaited()
    finally:
        adapter._bg.shutdown()


# ── sync_config() ────────────────────────────────────────────────


def test_sync_config_updates_known_fields(mock_hub):
    """sync_config() sets known MCPServerConfig fields via setattr."""
    adapter = MCPAdapter(_config_dict(), root_dir="/tmp")
    try:
        adapter.sync_config({"name": "renamed", "command": "python", "args": ["-m", "srv"]})
        assert adapter.server_config.name == "renamed"
        assert adapter.server_config.command == "python"
        assert adapter.server_config.args == ["-m", "srv"]
    finally:
        adapter._bg.shutdown()


def test_sync_config_ignores_unknown_keys(mock_hub):
    """Unknown keys are silently ignored (no AttributeError raised)."""
    adapter = MCPAdapter(_config_dict(), root_dir="/tmp")
    try:
        # Should not raise.
        adapter.sync_config({"unknown_field": "x", "another_unknown": 123})
        # Original config is unchanged.
        assert adapter.server_config.name == "filesystem"
    finally:
        adapter._bg.shutdown()


def test_sync_config_does_not_reconnect(mock_hub):
    """sync_config() must NOT call connect/disconnect (no implicit reconnect)."""
    adapter = MCPAdapter(_config_dict(), root_dir="/tmp")
    adapter._server_id = "srv-sc"
    try:
        adapter.sync_config({"command": "newcmd"})
        mock_hub.connect.assert_not_awaited()
        mock_hub.disconnect.assert_not_awaited()
    finally:
        adapter._bg.shutdown()


# ── disconnect() ─────────────────────────────────────────────────


def test_disconnect_releases_server_connection(mock_hub):
    """disconnect() calls MCPHub.disconnect and clears the server_id."""
    mock_hub.disconnect = AsyncMock(return_value=True)
    adapter = MCPAdapter(_config_dict(), root_dir="/tmp")
    adapter._server_id = "srv-d1"
    try:
        adapter.disconnect()
        mock_hub.disconnect.assert_awaited_once_with("srv-d1")
        assert adapter._server_id is None
        assert adapter.is_connected is False
    finally:
        # Already shut down by disconnect(); this is a no-op safety.
        pass


def test_disconnect_swallows_hub_exception(mock_hub):
    """If MCPHub.disconnect raises, disconnect() logs and still clears state."""
    mock_hub.disconnect = AsyncMock(side_effect=RuntimeError("cleanup failed"))
    adapter = MCPAdapter(_config_dict(), root_dir="/tmp")
    adapter._server_id = "srv-d2"
    try:
        # Should NOT raise.
        adapter.disconnect()
        assert adapter._server_id is None
    finally:
        pass


def test_disconnect_is_idempotent(mock_hub):
    """Calling disconnect() twice does not error on the second call."""
    mock_hub.disconnect = AsyncMock(return_value=True)
    adapter = MCPAdapter(_config_dict(), root_dir="/tmp")
    adapter._server_id = "srv-d3"
    try:
        adapter.disconnect()
        # Second call: server_id is None, so hub.disconnect is skipped.
        adapter.disconnect()
        assert mock_hub.disconnect.await_count == 1
    finally:
        pass


def test_disconnect_without_connect_only_shuts_down_loop(mock_hub):
    """If disconnect() is called before connect(), only the bg loop is shut down."""
    mock_hub.disconnect = AsyncMock(return_value=True)
    adapter = MCPAdapter(_config_dict(), root_dir="/tmp")
    adapter.disconnect()
    mock_hub.disconnect.assert_not_awaited()


# ── Convenience accessors ────────────────────────────────────────


def test_server_name_reflects_config(mock_hub):
    """server_name property returns the MCPServerConfig.name."""
    adapter = MCPAdapter(_config_dict(), root_dir="/tmp")
    try:
        assert adapter.server_name == "filesystem"
    finally:
        adapter._bg.shutdown()


def test_server_config_property_returns_stored_config(mock_hub):
    """server_config property returns the stored MCPServerConfig."""
    from maop.core.mcp.mcp_hub import MCPServerConfig

    adapter = MCPAdapter(_config_dict(), root_dir="/tmp")
    try:
        assert isinstance(adapter.server_config, MCPServerConfig)
        assert adapter.server_config.command == "npx"
    finally:
        adapter._bg.shutdown()


def test_is_connected_false_by_default(mock_hub):
    """A freshly-constructed adapter reports is_connected=False."""
    adapter = MCPAdapter(_config_dict(), root_dir="/tmp")
    try:
        assert adapter.is_connected is False
    finally:
        adapter._bg.shutdown()
