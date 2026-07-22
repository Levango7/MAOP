"""Tests for MAOP.core.mcp_client, mcp_transport, and mcp_registry."""

from __future__ import annotations

import pytest

from maop.core.mcp_transport import StdioTransport, SSETransport, TransportMessage
from maop.core.mcp_client import MCPClient, MCPServerConfig, MCPServerStatus, MCPToolDef
from maop.core.mcp_registry import MCPRegistry


class TestTransportMessage:
    def test_request_message(self):
        msg = TransportMessage(id=1, method="tools/list", params={})
        d = msg.model_dump(exclude_none=True)
        assert d["jsonrpc"] == "2.0"
        assert d["method"] == "tools/list"

    def test_response_message(self):
        msg = TransportMessage(id=1, result={"tools": []})
        assert msg.result == {"tools": []}


class TestStdioTransport:
    def test_init(self):
        t = StdioTransport(command="echo")
        assert t._command == "echo"
        assert not t.connected

    @pytest.mark.asyncio
    async def test_connect_disconnect(self):
        t = StdioTransport(command="python", args=["-c", "import sys; sys.stdin.read()"])
        ok = await t.connect()
        assert ok is True
        assert t.connected
        await t.disconnect()
        assert not t.connected

    @pytest.mark.asyncio
    async def test_connect_bad_command(self):
        t = StdioTransport(command="nonexistent_command_xyz")
        ok = await t.connect()
        assert ok is False


class TestSSETransport:
    def test_init(self):
        t = SSETransport(url="http://localhost:3001/sse")
        assert t._url == "http://localhost:3001/sse"
        assert not t.connected


class TestMCPServerConfig:
    def test_defaults(self):
        cfg = MCPServerConfig(name="test", command="echo")
        assert cfg.transport == "stdio"
        assert cfg.enabled is True
        assert cfg.timeout == 30.0

    def test_sse_config(self):
        cfg = MCPServerConfig(name="remote", transport="sse", url="http://localhost:3001/sse")
        assert cfg.transport == "sse"
        assert cfg.url == "http://localhost:3001/sse"


class TestMCPClient:
    def test_init(self):
        cfg = MCPServerConfig(name="test", command="echo")
        client = MCPClient(cfg)
        assert client.name == "test"
        assert client.status == MCPServerStatus.DISCONNECTED
        assert client.tools == []

    @pytest.mark.asyncio
    async def test_connect_disconnect(self):
        cfg = MCPServerConfig(name="test", command="python", args=["-c", "import sys; sys.stdin.read()"])
        client = MCPClient(cfg)
        ok = await client.connect()
        assert ok is True
        assert client.status == MCPServerStatus.CONNECTED
        await client.disconnect()
        assert client.status == MCPServerStatus.DISCONNECTED

    @pytest.mark.asyncio
    async def test_call_tool_not_connected(self):
        cfg = MCPServerConfig(name="test", command="echo")
        client = MCPClient(cfg)
        result = await client.call_tool("some_tool", {})
        assert result.success is False
        assert "Not connected" in result.error

    def test_stats(self):
        cfg = MCPServerConfig(name="test", command="echo")
        client = MCPClient(cfg)
        stats = client.stats
        assert stats["name"] == "test"
        assert stats["status"] == "disconnected"


class TestMCPRegistry:
    def test_add_remove_server(self, tmp_path):
        reg = MCPRegistry(root_dir=str(tmp_path))
        cfg = MCPServerConfig(name="fs", command="echo")
        client = reg.add_server(cfg)
        assert client.name == "fs"
        assert reg.get_client("fs") is not None

        removed = reg.remove_server("fs")
        assert removed is True
        assert reg.get_client("fs") is None

    def test_list_servers(self, tmp_path):
        reg = MCPRegistry(root_dir=str(tmp_path))
        reg.add_server(MCPServerConfig(name="a", command="echo"))
        reg.add_server(MCPServerConfig(name="b", command="echo"))
        servers = reg.list_servers()
        assert len(servers) == 2

    def test_find_tool(self, tmp_path):
        reg = MCPRegistry(root_dir=str(tmp_path))
        client = reg.add_server(MCPServerConfig(name="fs", command="echo"))
        client._tools = [MCPToolDef(name="read_file", server_name="fs")]
        found_client, tool_name = reg.find_tool("fs.read_file")
        assert found_client is not None
        assert tool_name == "read_file"

    def test_find_tool_unqualified(self, tmp_path):
        reg = MCPRegistry(root_dir=str(tmp_path))
        client = reg.add_server(MCPServerConfig(name="fs", command="echo"))
        client._tools = [MCPToolDef(name="read_file", server_name="fs")]
        found_client, tool_name = reg.find_tool("read_file")
        assert found_client is not None
        assert tool_name == "read_file"

    def test_all_tools(self, tmp_path):
        reg = MCPRegistry(root_dir=str(tmp_path))
        client = reg.add_server(MCPServerConfig(name="fs", command="echo"))
        client._tools = [MCPToolDef(name="read_file", server_name="fs")]
        tools = reg.all_tools()
        assert len(tools) == 1
        assert tools[0].name == "read_file"

    @pytest.mark.asyncio
    async def test_call_tool_not_connected(self, tmp_path):
        reg = MCPRegistry(root_dir=str(tmp_path))
        client = reg.add_server(MCPServerConfig(name="fs", command="echo"))
        client._tools = [MCPToolDef(name="read_file", server_name="fs")]
        result = await reg.call_tool("fs.read_file", {"path": "/tmp/test"})
        assert result.success is False

    def test_load_from_db(self, tmp_path):
        reg = MCPRegistry(root_dir=str(tmp_path))
        reg.add_server(MCPServerConfig(name="persist", command="echo"))
        reg2 = MCPRegistry(root_dir=str(tmp_path))
        count = reg2.load_from_db()
        assert count == 1
        assert reg2.get_client("persist") is not None