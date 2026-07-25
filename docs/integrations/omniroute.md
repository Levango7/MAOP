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

## Default LLM Exit (Phase 2)

As of 2026-07-25, OmniRoute is configured as the **default LLM exit** for MAOP.
This means:

1. **ModelSelector preference**: When `default_provider: omniroute` is set in
   `models.yaml`, the selector prefers OmniRoute models as primary for any
   capability they support.

2. **Agent routing fallback**: Key routing keys (codegen, chat, refactor,
   review, planning, quickfix, docgen, techdoc, verify) now include
   `omniroute` as a fallback agent, so if the primary agent fails, the
   request is retried via OmniRoute.

3. **Last-resort default**: `default_model: omniroute-auto-coding` is used
   when no model is specified and all other selection logic fails.

### Configuration

```yaml
# config/models.yaml
default_provider: omniroute
default_model: omniroute-auto-coding
```

### Disabling the default

To revert to legacy strategy-based selection:

```yaml
default_provider: ""
default_model: ""
```

### Fallback topology

```
Request -> ModelSelector
  +- Step 1: Exact model resolution (agent.model field)
  +- Step 1.5: Default provider preference (NEW)
  |   +- If default_provider set, prefer its models for the capability
  +- Step 2: Strategy-based selection (legacy)
  +- Step 3: Built-in fallback
  +- Step 4: Quota-aware fallback
```

### Expanded capabilities

To support the default-exit role, OmniRoute model capabilities were expanded:

- `omniroute-auto-coding`: added `chat`, `quickfix`, `docgen`, `techdoc`, `verify`
  (was: `codegen, refactor, explain, review, planning, search, tool_use`)
- `omniroute-auto-reasoning`: added `chat`, `verify`
  (was: `reasoning, planning, explain, review, search`)