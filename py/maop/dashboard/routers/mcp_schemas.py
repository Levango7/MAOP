"""Response models for the MCP router (and the combined ``GET /api/mcp``).

Producer-owned models are reused verbatim (``ServerInfo``, ``MCPTool``);
hand-built dicts are re-declared field for field so a rename on either side
fails a test instead of silently rendering an empty cell.

Two deliberate asymmetries:
* ``McpToolOut`` carries ``id``/``concurrency_limit`` because
  ``plugin_service.list_tools`` synthesises them (``MCPTool`` has neither) —
  the dashboard needs ``id`` to key rows and to target
  ``PUT /api/mcp/tools/{id}``.
* ``TopologyTool`` does **not** carry ``concurrency_limit``: the topology
  builder never sets it, and declaring it would put an invented ``0`` on the
  wire.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from maop.core.mcp.mcp_hub_types import MCPTool, ServerInfo

# ── GET /api/mcp (combined, YAML-backed) ─────────────────────────


class McpCombinedEnvelope(BaseModel):
    """Raw config-shaped rows: the bridge hands back ``mcp_servers.yaml``.

    Items stay ``dict[str, Any]`` because they are user-authored YAML —
    any key may or may not exist. Secrets are masked by the service
    (``headers`` / ``env`` / ``token``), never by dropping whole fields.
    """

    status: str = "ok"
    servers: list[dict[str, Any]] = []
    tools: list[dict[str, Any]] = []
    server_count: int = 0
    tool_count: int = 0
    error: str = ""


# ── Servers ───────────────────────────────────────────────────────


class ServerListEnvelope(BaseModel):
    status: str = "ok"
    servers: list[ServerInfo]
    count: int


class ServerDetail(BaseModel):
    """Editable config for one server; secrets are redacted to ``auth_type``."""

    id: str = ""
    name: str = ""
    transport: str = ""
    url: str = ""
    command: str = ""
    args: list[str] = []
    timeout_s: float = 0.0
    enabled: bool = True
    auto_connect: bool = False
    auth_type: str = "none"
    has_auth: bool = False


class ServerDetailEnvelope(BaseModel):
    status: str = "ok"
    server: ServerDetail


class AddServerEnvelope(BaseModel):
    status: str = "ok"
    server: str = ""


class UpdateServerEnvelope(BaseModel):
    status: str = "ok"
    server: str = ""
    id: str = ""


class RemoveServerEnvelope(BaseModel):
    status: str = "ok"
    server: str = ""


# ── Tools ─────────────────────────────────────────────────────────


class McpToolOut(MCPTool):
    id: str = ""
    concurrency_limit: int = 0


class ToolListEnvelope(BaseModel):
    status: str = "ok"
    tools: list[McpToolOut]
    count: int


class ToolLimitEnvelope(BaseModel):
    status: str = "ok"
    tool: str = ""
    concurrency_limit: int = 0


# ── Topology ──────────────────────────────────────────────────────


class TopologyTool(MCPTool):
    id: str = ""


class TopologyAgent(BaseModel):
    name: str = ""
    provider: str = ""
    enabled: bool = False


class TopologyEdge(BaseModel):
    id: str = ""
    source: str = ""
    target: str = ""
    type: str = ""


class TopologyEnvelope(BaseModel):
    status: str = "ok"
    servers: list[ServerInfo] = []
    tools: list[TopologyTool] = []
    agents: list[TopologyAgent] = []
    edges: list[TopologyEdge] = []


# ── Call stats ────────────────────────────────────────────────────


class StatBucket(BaseModel):
    """One hourly bucket (``avg_ms`` = mean of that hour's calls)."""

    ts: int = 0
    count: int = 0
    errors: int = 0
    avg_ms: float = 0.0


class ToolStat(BaseModel):
    tool: str = ""
    server_name: str = ""
    calls: int = 0
    errors: int = 0
    avg_ms: float = 0.0
    max_ms: float = 0.0
    error_rate: float = 0.0


class StatTotal(BaseModel):
    calls: int = 0
    errors: int = 0
    avg_ms: float = 0.0


class StatsEnvelope(BaseModel):
    status: str = "ok"
    series: list[StatBucket] = []
    per_tool: list[ToolStat] = []
    total: StatTotal = Field(default_factory=StatTotal)


# ── Marketplace ───────────────────────────────────────────────────


class MarketplaceTool(BaseModel):
    id: str = ""
    name: str = ""
    description: str = ""
    category: str = ""
    source: str = "mcp"
    version: str = ""
    installed: bool = False
    author: str = ""
    homepage: str = ""
    transport: str = ""
    verified: bool = False
    install_count: int = 0


class MarketplaceListEnvelope(BaseModel):
    status: str = "ok"
    tools: list[MarketplaceTool] = []
    count: int = 0
