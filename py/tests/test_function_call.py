"""Tests for MAOP.core.function_call and MAOP.core.tool_schema."""

from __future__ import annotations

import json

import pytest

from maop.core.agent.llm_chat.function_call import (
    FunctionCallBridge,
    ToolCall,
    ToolCallResult,
    ToolProvider,
)
from maop.core.agent.tools.tool_schema import ToolSchemaDef, ToolSchemaGenerator

# ── FunctionCallBridge: parse_response ──────────────────────────


class TestParseOpenAI:
    def test_openai_tool_calls(self):
        bridge = FunctionCallBridge()
        response = {
            "choices": [{
                "message": {
                    "tool_calls": [{
                        "id": "call_abc123",
                        "type": "function",
                        "function": {
                            "name": "read_file",
                            "arguments": '{"path": "/tmp/test.txt"}',
                        },
                    }],
                },
            }],
        }
        calls = bridge.parse_response(response, provider="openai")
        assert len(calls) == 1
        assert calls[0].name == "read_file"
        assert calls[0].arguments == {"path": "/tmp/test.txt"}
        assert calls[0].id == "call_abc123"
        assert calls[0].provider == ToolProvider.OPENAI

    def test_openai_no_tool_calls(self):
        bridge = FunctionCallBridge()
        response = {"choices": [{"message": {"content": "Hello!"}}]}
        calls = bridge.parse_response(response, provider="openai")
        assert calls == []

    def test_openai_multiple_tool_calls(self):
        bridge = FunctionCallBridge()
        response = {
            "choices": [{
                "message": {
                    "tool_calls": [
                        {"id": "c1", "type": "function", "function": {"name": "fn1", "arguments": "{}"}},
                        {"id": "c2", "type": "function", "function": {"name": "fn2", "arguments": '{"x": 1}'}},
                    ],
                },
            }],
        }
        calls = bridge.parse_response(response, provider="openai")
        assert len(calls) == 2
        assert calls[0].name == "fn1"
        assert calls[1].name == "fn2"
        assert calls[1].arguments == {"x": 1}

    def test_openai_bad_arguments_json(self):
        bridge = FunctionCallBridge()
        response = {
            "choices": [{
                "message": {
                    "tool_calls": [{
                        "id": "c1", "type": "function",
                        "function": {"name": "fn1", "arguments": "not-json"},
                    }],
                },
            }],
        }
        calls = bridge.parse_response(response, provider="openai")
        assert len(calls) == 1
        assert calls[0].arguments == {"_raw": "not-json"}


class TestParseAnthropic:
    def test_anthropic_tool_use(self):
        bridge = FunctionCallBridge()
        response = {
            "content": [
                {"type": "text", "text": "Let me check that."},
                {
                    "type": "tool_use",
                    "id": "toolu_123",
                    "name": "search",
                    "input": {"query": "python"},
                },
            ],
        }
        calls = bridge.parse_response(response, provider="anthropic")
        assert len(calls) == 1
        assert calls[0].name == "search"
        assert calls[0].arguments == {"query": "python"}
        assert calls[0].provider == ToolProvider.ANTHROPIC

    def test_anthropic_no_tool_use(self):
        bridge = FunctionCallBridge()
        response = {"content": [{"type": "text", "text": "Hello!"}]}
        calls = bridge.parse_response(response, provider="anthropic")
        assert calls == []

    def test_anthropic_multiple_tool_uses(self):
        bridge = FunctionCallBridge()
        response = {
            "content": [
                {"type": "tool_use", "id": "t1", "name": "fn1", "input": {}},
                {"type": "tool_use", "id": "t2", "name": "fn2", "input": {"a": 1}},
            ],
        }
        calls = bridge.parse_response(response, provider="anthropic")
        assert len(calls) == 2


class TestParseOllama:
    def test_ollama_format(self):
        bridge = FunctionCallBridge()
        response = {
            "message": {
                "tool_calls": [{
                    "id": "oc1",
                    "type": "function",
                    "function": {"name": "calc", "arguments": '{"expr": "1+1"}'},
                }],
            },
        }
        calls = bridge.parse_response(response, provider="ollama")
        assert len(calls) == 1
        assert calls[0].name == "calc"


# ── FunctionCallBridge: format_result ───────────────────────────


class TestFormatResult:
    def test_format_openai(self):
        bridge = FunctionCallBridge()
        call = ToolCall(id="c1", name="read_file", arguments={"path": "/tmp"}, provider=ToolProvider.OPENAI)
        result = ToolCallResult(call_id="c1", tool_name="read_file", success=True, output="file contents")
        msg = bridge.format_result(call, result, provider="openai")
        assert msg["role"] == "tool"
        assert msg["tool_call_id"] == "c1"
        assert msg["content"] == "file contents"

    def test_format_openai_error(self):
        bridge = FunctionCallBridge()
        call = ToolCall(id="c1", name="bad_fn", arguments={}, provider=ToolProvider.OPENAI)
        result = ToolCallResult(call_id="c1", tool_name="bad_fn", success=False, error="not found")
        msg = bridge.format_result(call, result, provider="openai")
        assert msg["role"] == "tool"
        parsed = json.loads(msg["content"])
        assert "error" in parsed

    def test_format_anthropic(self):
        bridge = FunctionCallBridge()
        call = ToolCall(id="t1", name="search", arguments={"q": "test"}, provider=ToolProvider.ANTHROPIC)
        result = ToolCallResult(call_id="t1", tool_name="search", success=True, output={"results": []})
        msg = bridge.format_result(call, result, provider="anthropic")
        assert msg["role"] == "user"
        assert len(msg["content"]) == 1
        assert msg["content"][0]["type"] == "tool_result"

    def test_format_anthropic_error(self):
        bridge = FunctionCallBridge()
        call = ToolCall(id="t1", name="bad", arguments={}, provider=ToolProvider.ANTHROPIC)
        result = ToolCallResult(call_id="t1", tool_name="bad", success=False, error="fail")
        msg = bridge.format_result(call, result, provider="anthropic")
        assert msg["role"] == "user"
        content = msg["content"][0]
        assert content["type"] == "tool_result"


# ── FunctionCallBridge: execute ─────────────────────────────────


class TestExecute:
    @pytest.mark.asyncio
    async def test_execute_tool_not_found(self):
        bridge = FunctionCallBridge()
        call = ToolCall(id="c1", name="nonexistent_tool", arguments={})
        result = await bridge.execute(call)
        assert result.success is False
        assert "not found" in result.error

    def test_stats(self):
        bridge = FunctionCallBridge()
        assert bridge.stats["call_count"] == 0
        assert bridge.stats["error_count"] == 0


# ── FunctionCallBridge: build_tools_param ───────────────────────


class TestBuildToolsParam:
    def test_build_empty(self):
        bridge = FunctionCallBridge()
        tools = bridge.build_tools_param(provider="openai")
        assert isinstance(tools, list)

    def test_build_anthropic_format(self):
        bridge = FunctionCallBridge()
        tools = bridge.build_tools_param(provider="anthropic")
        assert isinstance(tools, list)


# ── ToolSchemaGenerator ─────────────────────────────────────────


class TestToolSchemaDef:
    def test_defaults(self):
        s = ToolSchemaDef(name="test_fn")
        assert s.name == "test_fn"
        assert s.source == "manual"
        assert s.parameters["type"] == "object"


class TestFromPythonFunction:
    def test_basic_function(self):
        gen = ToolSchemaGenerator()

        def greet(name: str, age: int = 0) -> str:
            """Greet a person.

            Args:
                name: The person's name
                age: The person's age
            """
            return f"Hello {name}"

        schema = gen.from_python_function(greet)
        assert schema.name == "greet"
        assert schema.source == "python"
        assert "name" in schema.parameters["properties"]
        assert "age" in schema.parameters["properties"]
        assert "name" in schema.parameters["required"]
        assert "age" not in schema.parameters["required"]
        assert schema.parameters["properties"]["name"]["type"] == "string"
        assert schema.parameters["properties"]["age"]["type"] == "integer"

    def test_custom_name_and_description(self):
        gen = ToolSchemaGenerator()

        def fn(x: str) -> str:
            return x

        schema = gen.from_python_function(fn, name="custom_name", description="Custom desc")
        assert schema.name == "custom_name"
        assert schema.description == "Custom desc"

    def test_no_annotations(self):
        gen = ToolSchemaGenerator()

        def simple(x, y):
            return x + y

        schema = gen.from_python_function(simple)
        assert schema.name == "simple"
        assert "x" in schema.parameters["properties"]
        assert schema.parameters["properties"]["x"]["type"] == "string"


class TestFromMCPTool:
    def test_mcp_tool_conversion(self):
        # δ-1: migrated from MCPToolDef (Stack B) to MCPTool (Stack A)
        from maop.core.mcp.mcp_hub import MCPTool
        gen = ToolSchemaGenerator()
        mcp_tool = MCPTool(
            name="read_file",
            description="Read a file",
            input_schema={"type": "object", "properties": {"path": {"type": "string"}}},
            server_name="fs",
        )
        schema = gen.from_mcp_tool(mcp_tool)
        assert schema.name == "fs.read_file"
        assert schema.description == "Read a file"
        assert schema.source == "mcp"
        assert "path" in schema.parameters["properties"]


class TestRegisterUnregister:
    def test_register_and_generate(self):
        gen = ToolSchemaGenerator()
        schema = ToolSchemaDef(name="my_tool", description="A tool")
        gen.register(schema)
        tools = gen.generate(provider="openai")
        names = [t["function"]["name"] for t in tools]
        assert "my_tool" in names

    def test_unregister(self):
        gen = ToolSchemaGenerator()
        schema = ToolSchemaDef(name="my_tool", description="A tool")
        gen.register(schema)
        assert gen.unregister("my_tool") is True
        assert gen.unregister("my_tool") is False


class TestGenerateFormats:
    def test_openai_format(self):
        gen = ToolSchemaGenerator()
        gen.register(ToolSchemaDef(name="fn1", description="desc"))
        tools = gen.generate(provider="openai")
        assert tools[0]["type"] == "function"
        assert "function" in tools[0]
        assert tools[0]["function"]["name"] == "fn1"

    def test_anthropic_format(self):
        gen = ToolSchemaGenerator()
        gen.register(ToolSchemaDef(name="fn1", description="desc"))
        tools = gen.generate(provider="anthropic")
        assert "name" in tools[0]
        assert "input_schema" in tools[0]
        assert tools[0]["name"] == "fn1"
