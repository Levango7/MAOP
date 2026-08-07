"""MAOP Function Call Bridge — Unified function calling across LLM providers.

Translates function_call / tool_use responses from different LLM providers
(OpenAI, Anthropic, Ollama) into a unified MAOP ToolCall format, then
dispatches the call through MCPRegistry or ToolManager and returns the
result in the provider's expected format for re-injection.

Usage::

    from maop.core.agent.llm_chat.function_call import FunctionCallBridge

    bridge = FunctionCallBridge(root_dir="/path/to/MAOP")
    calls = bridge.parse_response(response_json, provider="openai")
    for call in calls:
        result = await bridge.execute(call)
        messages.append(bridge.format_result(call, result, provider="openai"))
"""

from __future__ import annotations

import json
import logging
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


class ToolProvider(str, Enum):
    OPENAI = "openai"
    ANTHROPIC = "anthropic"
    OLLAMA = "ollama"


class ToolCall(BaseModel):
    id: str = ""
    name: str
    arguments: dict[str, Any] = Field(default_factory=dict)
    provider: ToolProvider = ToolProvider.OPENAI


class ToolCallResult(BaseModel):
    call_id: str = ""
    tool_name: str = ""
    success: bool = True
    output: Any = None
    error: str = ""
    duration_ms: int = 0


class FunctionCallBridge:
    """Bridge between LLM function_call responses and MAOP tool execution.

    Flow:
        1. LLM returns a response containing function_call / tool_use
        2. ``parse_response()`` extracts unified ``ToolCall`` objects
        3. ``execute()`` dispatches each call via MCPRegistry → ToolManager
        4. ``format_result()`` wraps the result for re-injection into the LLM
    """

    def __init__(self, root_dir: str | None = None) -> None:
        self._root_dir = root_dir
        self._mcp_registry: Any = None
        self._tool_manager: Any = None
        self._call_count: int = 0
        self._error_count: int = 0

    @property
    def stats(self) -> dict[str, Any]:
        return {
            "call_count": self._call_count,
            "error_count": self._error_count,
        }

    def _get_mcp_registry(self):
        # δ-1: migrated from MCPRegistry (Stack B) to MCPHub (Stack A)
        if self._mcp_registry is None:
            try:
                from maop.core.mcp.mcp_hub import MCPHub
                root = self._root_dir
                if root is None:
                    from pathlib import Path
                    root = str(Path(__file__).resolve().parent.parent.parent)
                self._mcp_registry = MCPHub(root_dir=root)
            except Exception as exc:
                logger.debug("[fn_call] MCPHub unavailable: %s", exc)
        return self._mcp_registry

    def _get_tool_manager(self):
        if self._tool_manager is None and self._root_dir:
            try:
                from maop.core.agent.tools.tool_manager import ToolManager
                self._tool_manager = ToolManager(root_dir=self._root_dir)
            except Exception as exc:
                logger.debug("[fn_call] ToolManager unavailable: %s", exc)
        return self._tool_manager

    def parse_response(self, response: dict[str, Any], provider: str = "openai") -> list[ToolCall]:
        """Extract tool calls from an LLM response.

        Supports:
          - OpenAI: ``choices[0].message.tool_calls``
          - Anthropic: ``content`` blocks with ``type="tool_use"``
          - Ollama: ``message.tool_calls`` (OpenAI-compatible)
        """
        provider = provider.lower()
        if provider == "openai" or provider == "ollama":
            return self._parse_openai(response, provider)
        elif provider == "anthropic":
            return self._parse_anthropic(response)
        logger.warning("[fn_call] Unknown provider '%s', trying OpenAI format", provider)
        return self._parse_openai(response, provider)

    def _parse_openai(self, response: dict[str, Any], provider: str) -> list[ToolCall]:
        calls = []
        choices = response.get("choices", [])
        if not choices:
            ollama_calls = response.get("message", {}).get("tool_calls", [])
            if not ollama_calls:
                ollama_calls = response.get("tool_calls", [])
            for tc in ollama_calls:
                call = self._extract_openai_tool_call(tc, provider)
                if call:
                    calls.append(call)
            return calls
        message = choices[0].get("message", {})
        tool_calls = message.get("tool_calls", [])
        for tc in tool_calls:
            call = self._extract_openai_tool_call(tc, provider)
            if call:
                calls.append(call)
        return calls

    def _extract_openai_tool_call(self, tc: dict[str, Any], provider: str) -> ToolCall | None:
        try:
            func = tc.get("function", {})
            name = func.get("name", "")
            args_str = func.get("arguments", "{}")
            if isinstance(args_str, str):
                try:
                    arguments = json.loads(args_str)
                except (json.JSONDecodeError, ValueError):
                    arguments = {"_raw": args_str}
            else:
                arguments = args_str if isinstance(args_str, dict) else {}
            return ToolCall(
                id=tc.get("id", ""),
                name=name,
                arguments=arguments,
                provider=ToolProvider(provider),
            )
        except Exception as exc:
            logger.warning("[fn_call] Failed to parse OpenAI tool_call: %s", exc)
            return None

    def _parse_anthropic(self, response: dict[str, Any]) -> list[ToolCall]:
        calls: list[ToolCall] = []
        content_blocks = response.get("content", [])
        if not isinstance(content_blocks, list):
            return calls
        for block in content_blocks:
            if not isinstance(block, dict):
                continue
            if block.get("type") != "tool_use":
                continue
            try:
                arguments = block.get("input", {})
                if not isinstance(arguments, dict):
                    arguments = {}
                calls.append(ToolCall(
                    id=block.get("id", ""),
                    name=block.get("name", ""),
                    arguments=arguments,
                    provider=ToolProvider.ANTHROPIC,
                ))
            except Exception as exc:
                logger.warning("[fn_call] Failed to parse Anthropic tool_use: %s", exc)
        return calls

    async def execute(self, call: ToolCall) -> ToolCallResult:
        """Execute a tool call via MCPRegistry (preferred) or ToolManager.

        Resolution order:
          1. MCPRegistry (qualified ``server.tool`` or unqualified name)
          2. ToolManager (registered CLI tools)
        """
        import time as _time
        start = _time.monotonic()
        self._call_count += 1

        result = await self._try_mcp(call)
        if result is None:
            result = await self._try_tool_manager(call)
        if result is None:
            self._error_count += 1
            result = ToolCallResult(
                call_id=call.id,
                tool_name=call.name,
                success=False,
                error=f"Tool '{call.name}' not found in MCPRegistry or ToolManager",
                duration_ms=int((_time.monotonic() - start) * 1000),
            )
        result.duration_ms = int((_time.monotonic() - start) * 1000)
        return result

    async def _try_mcp(self, call: ToolCall) -> ToolCallResult | None:
        # δ-1: migrated from MCPRegistry (Stack B) to MCPHub (Stack A)
        hub = self._get_mcp_registry()
        if hub is None:
            return None
        server_id, _tool_name = hub.find_tool(call.name)
        if server_id is None:
            return None  # tool not registered in MCP, fall through to ToolManager
        # call_tool_by_name checks connection internally and returns ToolResult
        mcp_result = await hub.call_tool_by_name(call.name, call.arguments)
        return ToolCallResult(
            call_id=call.id,
            tool_name=call.name,
            success=not mcp_result.is_error,
            output=mcp_result.content if not mcp_result.is_error else None,
            error=mcp_result.error_message,
        )

    async def _try_tool_manager(self, call: ToolCall) -> ToolCallResult | None:
        """F7c (2026-07-22, Phase F): now async — awaits the async
        ``ToolManager.call()``. See ADR-013.
        """
        mgr = self._get_tool_manager()
        if mgr is None:
            return None
        tool_info = mgr.info(call.name)
        if tool_info is None:
            return None
        args_list = []
        for k, v in call.arguments.items():
            args_list.extend([f"--{k}", str(v)])
        tm_result = await mgr.call(call.name, args=args_list)
        return ToolCallResult(
            call_id=call.id,
            tool_name=call.name,
            success=tm_result.ok,
            output=tm_result.output,
            error=tm_result.error,
        )

    def format_result(
        self,
        call: ToolCall,
        result: ToolCallResult,
        provider: str = "openai",
    ) -> dict[str, Any]:
        """Format a tool result for re-injection into the LLM conversation.

        Returns a message dict in the provider's expected format:
          - OpenAI/Ollama: ``{"role": "tool", "tool_call_id": ..., "content": ...}``
          - Anthropic: ``{"role": "user", "content": [{"type": "tool_result", ...}]}``
        """
        provider = provider.lower()
        if provider == "anthropic":
            return self._format_anthropic(call, result)
        return self._format_openai(call, result)

    def _format_openai(self, call: ToolCall, result: ToolCallResult) -> dict[str, Any]:
        content = result.output if result.success else {"error": result.error}
        if not isinstance(content, str):
            try:
                content = json.dumps(content, ensure_ascii=False)
            except (TypeError, ValueError):
                content = str(content)
        return {
            "role": "tool",
            "tool_call_id": call.id or call.name,
            "content": content,
        }

    def _format_anthropic(self, call: ToolCall, result: ToolCallResult) -> dict[str, Any]:
        content_val = result.output if result.success else {"error": result.error}
        return {
            "role": "user",
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": call.id or call.name,
                    "content": content_val if isinstance(content_val, str) else json.dumps(content_val, ensure_ascii=False),
                }
            ],
        }

    def build_tools_param(self, provider: str = "openai") -> list[dict[str, Any]]:
        """Build the ``tools`` parameter for an LLM request.

        Aggregates tools from MCPRegistry and ToolManager into the
        provider-specific ``tools`` format (OpenAI-compatible by default).
        """
        tools = []
        registry = self._get_mcp_registry()
        if registry is not None:
            for mcp_tool in registry.all_tools():
                tools.append(self._mcp_tool_to_openai(mcp_tool))
        mgr = self._get_tool_manager()
        if mgr is not None:
            for group in mgr.list():
                for t in group.get("tools", []):
                    if t.get("enabled", True):
                        tools.append({
                            "type": "function",
                            "function": {
                                "name": t["id"],
                                "description": t.get("description", ""),
                                "parameters": {"type": "object", "properties": {}},
                            },
                        })
        if provider.lower() == "anthropic":
            return self._convert_tools_to_anthropic(tools)
        return tools

    def _mcp_tool_to_openai(self, mcp_tool: Any) -> dict[str, Any]:
        # δ-1: migrated from MCPToolDef (Stack B) to MCPTool (Stack A)
        from maop.core.mcp.mcp_hub import MCPTool
        if isinstance(mcp_tool, MCPTool):
            name = f"{mcp_tool.server_name}.{mcp_tool.name}" if mcp_tool.server_name else mcp_tool.name
            return {
                "type": "function",
                "function": {
                    "name": name,
                    "description": mcp_tool.description or "",
                    "parameters": mcp_tool.input_schema or {"type": "object", "properties": {}},
                },
            }
        return {
            "type": "function",
            "function": {
                "name": getattr(mcp_tool, "name", str(mcp_tool)),
                "description": getattr(mcp_tool, "description", ""),
                "parameters": {"type": "object", "properties": {}},
            },
        }

    def _convert_tools_to_anthropic(self, openai_tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
        anthropic_tools = []
        for t in openai_tools:
            func = t.get("function", {})
            anthropic_tools.append({
                "name": func.get("name", ""),
                "description": func.get("description", ""),
                "input_schema": func.get("parameters", {"type": "object", "properties": {}}),
            })
        return anthropic_tools
