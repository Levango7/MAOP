"""MAOP Tool Schema — Auto-generate OpenAI-compatible tool definitions.

Converts tool metadata from multiple sources into the ``tools`` parameter
format expected by OpenAI / Anthropic / Ollama function calling APIs.

Supported sources:
  - MCP tools (MCPToolDef from mcp_client)
  - Python functions (inspect signature + docstring)
  - CLI tools (ToolDef from tool_manager)
  - Manual definitions (dict / ToolSchemaDef)

Usage::

    from maop.core.agent.tools.tool_schema import ToolSchemaGenerator

    gen = ToolSchemaGenerator(root_dir="/path/to/MAOP")
    tools = gen.generate(provider="openai")
"""

from __future__ import annotations

import inspect
import logging
import re
from collections.abc import Callable
from typing import Any, get_type_hints

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

_PYTHON_TYPE_TO_JSON = {
    str: "string",
    int: "integer",
    float: "number",
    bool: "boolean",
    list: "array",
    dict: "object",
}


class ToolSchemaDef(BaseModel):
    name: str
    description: str = ""
    parameters: dict[str, Any] = Field(default_factory=lambda: {
        "type": "object",
        "properties": {},
        "required": [],
    })
    source: str = "manual"


class ToolSchemaGenerator:
    """Generate OpenAI-compatible tool definitions from various sources."""

    def __init__(self, root_dir: str | None = None) -> None:
        self._root_dir = root_dir
        self._custom_schemas: dict[str, ToolSchemaDef] = {}

    def register(self, schema: ToolSchemaDef) -> None:
        self._custom_schemas[schema.name] = schema

    def unregister(self, name: str) -> bool:
        return self._custom_schemas.pop(name, None) is not None

    def from_python_function(
        self,
        func: Callable,
        name: str | None = None,
        description: str | None = None,
    ) -> ToolSchemaDef:
        """Generate a ToolSchemaDef from a Python function's signature and docstring.

        Parses:
          - Function name → tool name
          - Type annotations → parameter types
          - Default values → optional parameters
          - Docstring → description + parameter descriptions
        """
        tool_name = name or func.__name__
        docstring = inspect.getdoc(func) or ""
        desc = description or self._extract_description(docstring)
        param_docs = self._extract_param_docs(docstring)

        sig = inspect.signature(func)
        try:
            hints = get_type_hints(func)
        except Exception:
            hints = {}

        properties: dict[str, Any] = {}
        required: list[str] = []

        for param_name, param in sig.parameters.items():
            if param_name in ("self", "cls", "streamer"):
                continue
            if param.kind in (inspect.Parameter.VAR_POSITIONAL, inspect.Parameter.VAR_KEYWORD):
                continue

            prop: dict[str, Any] = {}
            ann = hints.get(param_name)
            if ann is not None:
                json_type = _PYTHON_TYPE_TO_JSON.get(ann)
                if json_type:
                    prop["type"] = json_type
                elif hasattr(ann, "__origin__"):
                    origin = getattr(ann, "__origin__", None)
                    if origin is list:
                        prop["type"] = "array"
                    elif origin is dict:
                        prop["type"] = "object"
                    else:
                        prop["type"] = "string"
                else:
                    prop["type"] = "string"
            else:
                prop["type"] = "string"

            if param_name in param_docs:
                prop["description"] = param_docs[param_name]

            if param.default is inspect.Parameter.empty:
                required.append(param_name)

            properties[param_name] = prop

        return ToolSchemaDef(
            name=tool_name,
            description=desc,
            parameters={
                "type": "object",
                "properties": properties,
                "required": required,
            },
            source="python",
        )

    def from_mcp_tool(self, mcp_tool: Any) -> ToolSchemaDef:
        """Convert an MCPToolDef to a ToolSchemaDef."""
        # δ-1: migrated from MCPToolDef (Stack B) to MCPTool (Stack A)
        from maop.core.mcp.mcp_hub import MCPTool
        if isinstance(mcp_tool, MCPTool):
            qualified = f"{mcp_tool.server_name}.{mcp_tool.name}" if mcp_tool.server_name else mcp_tool.name
            return ToolSchemaDef(
                name=qualified,
                description=mcp_tool.description or "",
                parameters=mcp_tool.input_schema or {"type": "object", "properties": {}},
                source="mcp",
            )
        return ToolSchemaDef(
            name=getattr(mcp_tool, "name", str(mcp_tool)),
            description=getattr(mcp_tool, "description", ""),
            source="mcp",
        )

    def from_cli_tool(self, cli_tool: Any) -> ToolSchemaDef:
        """Convert a ToolManager ToolDef to a ToolSchemaDef."""
        tool_id = getattr(cli_tool, "id", "") or getattr(cli_tool, "name", "")
        desc = getattr(cli_tool, "description", "")
        params = getattr(cli_tool, "params", {})
        properties = {}
        required: list[str] = []
        if isinstance(params, dict):
            for pname, pval in params.items():
                if isinstance(pval, dict):
                    properties[pname] = pval
                else:
                    properties[pname] = {"type": "string", "description": str(pval)}
        return ToolSchemaDef(
            name=tool_id,
            description=desc,
            parameters={
                "type": "object",
                "properties": properties,
                "required": required,
            },
            source="cli",
        )

    def generate(self, provider: str = "openai") -> list[dict[str, Any]]:
        """Generate the complete ``tools`` parameter for an LLM request.

        Aggregates from:
          1. Custom registered schemas
          2. MCP tools (if MCPRegistry available)
          3. CLI tools (if ToolManager available)
        """
        schemas = list(self._custom_schemas.values())
        schemas.extend(self._collect_mcp_schemas())
        schemas.extend(self._collect_cli_schemas())

        if provider.lower() == "anthropic":
            return [self._to_anthropic(s) for s in schemas]
        return [self._to_openai(s) for s in schemas]

    def _collect_mcp_schemas(self) -> list[ToolSchemaDef]:
        schemas = []
        try:
            # δ-1: migrated from MCPRegistry (Stack B) to MCPHub (Stack A)
            from maop.core.mcp.mcp_hub import MCPHub
            root = self._root_dir
            if root is None:
                from pathlib import Path
                root = str(Path(__file__).resolve().parent.parent.parent)
            registry = MCPHub(root_dir=root)
            for tool in registry.all_tools():
                schemas.append(self.from_mcp_tool(tool))
        except Exception as exc:
            logger.debug("[tool_schema] MCP tools unavailable: %s", exc)
        return schemas

    def _collect_cli_schemas(self) -> list[ToolSchemaDef]:
        schemas: list[ToolSchemaDef] = []
        if not self._root_dir:
            return schemas
        try:
            from maop.core.agent.tools.tool_manager import ToolManager
            mgr = ToolManager(root_dir=self._root_dir)
            for group in mgr.list():
                for t in group.get("tools", []):
                    if t.get("enabled", True):
                        schemas.append(ToolSchemaDef(
                            name=t["id"],
                            description=t.get("description", ""),
                            source="cli",
                        ))
        except Exception as exc:
            logger.debug("[tool_schema] CLI tools unavailable: %s", exc)
        return schemas

    def _to_openai(self, schema: ToolSchemaDef) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": schema.name,
                "description": schema.description,
                "parameters": schema.parameters,
            },
        }

    def _to_anthropic(self, schema: ToolSchemaDef) -> dict[str, Any]:
        return {
            "name": schema.name,
            "description": schema.description,
            "input_schema": schema.parameters,
        }

    def _extract_description(self, docstring: str) -> str:
        lines = docstring.strip().splitlines()
        desc_lines = []
        for line in lines:
            stripped = line.strip()
            if stripped.startswith((":param", ":type", ":return")):
                break
            if stripped.startswith(("Args:", "Returns:", "Raises:")):
                break
            desc_lines.append(stripped)
        return " ".join(desc_lines).strip()

    def _extract_param_docs(self, docstring: str) -> dict[str, str]:
        result = {}
        pattern = re.compile(r":param\s+(\w+):\s*(.+?)(?=\n\s*:param|\n\s*:type|\n\s*:return|\Z)", re.DOTALL)
        for m in pattern.finditer(docstring):
            result[m.group(1)] = m.group(2).strip()
        args_pattern = re.compile(r"Args:\s*\n((?:\s+\w+.*\n?)+)", re.MULTILINE)
        m = args_pattern.search(docstring)  # type: ignore[assignment]
        if m:
            for line in m.group(1).strip().splitlines():
                parts = line.strip().split(None, 1)
                if len(parts) == 2:
                    name = parts[0].rstrip(":")
                    result[name] = parts[1].strip()
        return result
