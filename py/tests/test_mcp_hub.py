"""Tests for MCP Hub — multi-transport MCP protocol center."""

import shutil
import tempfile

import pytest

from maop.core.mcp_hub import (
    MCPHub,
    MCPServerConfig,
    MCPTool,
    ToolResult,
    TransportType,
    _SSETransport,
    _StdioTransport,
    _WebSocketTransport,
)


@pytest.fixture
def hub():
    tmpdir = tempfile.mkdtemp()
    h = MCPHub(root_dir=tmpdir)
    yield h
    shutil.rmtree(tmpdir, ignore_errors=True)


class TestMCPServerConfig:
    def test_stdio_config(self):
        cfg = MCPServerConfig(
            name="filesystem",
            transport=TransportType.STDIO,
            command="npx -y @modelcontextprotocol/server-filesystem /tmp",
        )
        assert cfg.transport == TransportType.STDIO
        assert cfg.command

    def test_sse_config(self):
        cfg = MCPServerConfig(
            name="remote-server",
            transport=TransportType.SSE,
            url="http://localhost:3001/sse",
        )
        assert cfg.transport == TransportType.SSE
        assert cfg.url

    def test_websocket_config(self):
        cfg = MCPServerConfig(
            name="ws-server",
            transport=TransportType.WEBSOCKET,
            url="ws://localhost:8080/mcp",
        )
        assert cfg.transport == TransportType.WEBSOCKET
        assert cfg.url

    def test_auto_reconnect_default(self):
        cfg = MCPServerConfig(name="test")
        assert cfg.auto_reconnect is True
        assert cfg.max_reconnect_attempts == 3


class TestMCPHubConnect:
    @pytest.mark.asyncio
    async def test_connect_sse(self, hub):
        cfg = MCPServerConfig(
            name="test-sse",
            transport=TransportType.SSE,
            url="http://localhost:9999/sse",
        )
        server_id = await hub.connect(cfg)
        assert server_id
        servers = hub.list_servers()
        assert len(servers) >= 1
        assert any(s.name == "test-sse" for s in servers)

    @pytest.mark.asyncio
    async def test_disconnect(self, hub):
        cfg = MCPServerConfig(name="test", transport=TransportType.SSE, url="http://localhost:9999/sse")
        server_id = await hub.connect(cfg)
        result = await hub.disconnect(server_id)
        assert result is True

    @pytest.mark.asyncio
    async def test_disconnect_nonexistent(self, hub):
        result = await hub.disconnect("nonexistent")
        assert result is False


class TestMCPHubTools:
    @pytest.mark.asyncio
    async def test_list_tools_empty(self, hub):
        tools = await hub.list_tools()
        assert tools == []

    @pytest.mark.asyncio
    async def test_call_tool_disconnected(self, hub):
        result = await hub.call_tool("nonexistent", "some_tool", {})
        assert result.is_error is True

    @pytest.mark.asyncio
    async def test_call_tool_no_transport(self, hub):
        result = await hub.call_tool("fake-id", "tool", {})
        assert result.is_error is True
        assert "not connected" in result.error_message


class TestMCPHubResources:
    @pytest.mark.asyncio
    async def test_list_resources_empty(self, hub):
        resources = await hub.list_resources()
        assert resources == []

    @pytest.mark.asyncio
    async def test_read_resource_disconnected(self, hub):
        result = await hub.read_resource("nonexistent", "file:///tmp/test.txt")
        assert "Error" in result.text


class TestMCPHubHealthCheck:
    @pytest.mark.asyncio
    async def test_health_check_nonexistent(self, hub):
        result = await hub.health_check("nonexistent")
        assert result is False

    @pytest.mark.asyncio
    async def test_health_check_all_empty(self, hub):
        results = await hub.health_check_all()
        assert results == {}


class TestStdioTransport:
    def test_not_alive_initially(self):
        cfg = MCPServerConfig(name="test", transport=TransportType.STDIO, command="echo")
        t = _StdioTransport(cfg)
        assert t.is_alive is False


class TestSSETransport:
    def test_alive_with_url(self):
        cfg = MCPServerConfig(name="test", transport=TransportType.SSE, url="http://localhost:3001")
        t = _SSETransport(cfg)
        assert t.is_alive is True

    def test_not_alive_without_url(self):
        cfg = MCPServerConfig(name="test", transport=TransportType.SSE, url="")
        t = _SSETransport(cfg)
        assert t.is_alive is False


class TestWebSocketTransport:
    def test_not_alive_initially(self):
        cfg = MCPServerConfig(name="test", transport=TransportType.WEBSOCKET, url="ws://localhost:8080")
        t = _WebSocketTransport(cfg)
        assert t.is_alive is False


class TestMCPTool:
    def test_tool_creation(self):
        tool = MCPTool(
            name="read_file",
            description="Read a file from the filesystem",
            input_schema={"type": "object", "properties": {"path": {"type": "string"}}},
            server_name="filesystem",
        )
        assert tool.name == "read_file"
        assert tool.server_name == "filesystem"


class TestToolResult:
    def test_success_result(self):
        result = ToolResult(content=[{"type": "text", "text": "Hello"}])
        assert result.is_error is False
        assert len(result.content) == 1

    def test_error_result(self):
        result = ToolResult(is_error=True, error_message="File not found")
        assert result.is_error is True
        assert result.error_message == "File not found"