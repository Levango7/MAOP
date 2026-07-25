# OmniRoute Integration

OmniRoute is a local AI gateway that provides unified access to 160+ LLM providers with automatic failover, MCP/A2A protocol support, and an OpenAI-compatible API.

## Integration Paths

MAOP integrates OmniRoute through two complementary paths:

### Path A: LLM Provider (config/models.yaml)

OmniRoute is registered as an `openai-compatible` provider. MAOP's `LLMProviderFactory` can route chat completions to OmniRoute, with automatic fallback to other providers (e.g., `yi-large`) if OmniRoute is unavailable.

**Configuration**: See `config/models.yaml` → `providers.omniroute` and `models.omniroute-auto-*`

**Benefits**:
- Automatic model-level fallback via `chat_with_fallback()`
- Quota management and budget control
- SSE streaming and tool use support
- Cost tracking

### Path B: MCP Server (config/mcp_servers.yaml)

OmniRoute is registered as an MCP server via `streamable_http` transport. Its tools (e.g., `omniroute.chat`, `omniroute.list_models`) are aggregated into MAOP's unified MCP namespace.

**Configuration**: See `config/mcp_servers.yaml` → `servers.omniroute`

**Benefits**:
- Permission checking (per-user, per-role, per-tool)
- Audit logging (all tool calls persisted)
- Result caching and RPM rate limiting
- Concurrency control
- Health check with auto-reconnect
- OpenTelemetry tracing

## Prerequisites

1. OmniRoute running at `http://localhost:20128`
2. Environment variable `OMNIROUTE_API_KEY` set (use `dummy` for local installs)
3. For Path B: OmniRoute must implement MCP 2025 streamable_http spec at `/mcp` endpoint

## Verification

```bash
# Check OmniRoute is running
curl http://localhost:20128/v1/models

# Test LLM provider path (Path A)
python -c "
from maop.core.llm_provider import LLMProviderFactory
# ... (see chat_engine.py for usage example)
"

# Test MCP server path (Path B)
python -c "
from maop.core.mcp_hub import MCPHub
hub = MCPHub()
# ... connect and list tools
"
```

## Architecture

```
┌─────────────────────────────────────────────────────┐
│                 MAOP Orchestrator                     │
├─────────────────────────────────────────────────────┤
│  Path A: LLM Provider                                 │
│  Dispatcher → llm_provider → OmniRoute /v1/chat/*     │
│              ↓ fallback                               │
│              yi-large (stepfun)                       │
├─────────────────────────────────────────────────────┤
│  Path B: MCP Server                                   │
│  Agent → mcp_hub → OmniRoute /mcp (streamable_http)   │
│         ↓ tools: omniroute.chat, omniroute.list_models│
└─────────────────────────────────────────────────────┘
          │                           │
          ▼                           ▼
┌─────────────────────────────────────────────────────┐
│              OmniRoute Gateway                        │
│  160+ providers | auto failover | MCP/A2A            │
│  http://localhost:20128                              │
└─────────────────────────────────────────────────────┘
```

## Conflict Analysis

Path A and Path B are **complementary and non-conflicting**:
- Path A uses `llm_provider.py` (httpx direct HTTP) for chat completions
- Path B uses `mcp_hub.py` (streamable_http transport) for tool calls
- Both share W3C trace context via `inject_trace_context`
- No file overlap between the two configurations