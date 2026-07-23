# MAOP — Multi-Agent Orchestration Platform

> Python-first agent orchestration framework with Plan-Execute-Verify loop,
> model management, control plane, and real-time dashboard.

## Architecture

```
Entry (maop.ps1 / cli.py)
  → Orchestrator (maop_loop.py + engine.py)
    → Dispatcher (dispatcher.py + maop_plan.py)
      → Infrastructure (core/)
        → Data Layer (SQLite + JSON + YAML)
```

**Five-Layer Architecture:**

| Layer | Components | Purpose |
|-------|-----------|---------|
| Entry | `maop.ps1`, `cli.py` | CLI & startup |
| Orchestration | `maop_loop.py`, `engine.py` | Phase pipeline & DAG workflows |
| Dispatch | `dispatcher.py`, `maop_plan.py` | Config-driven agent routing |
| Infrastructure | `core/` (30+ modules) | Shared services & utilities |
| Data | SQLite, JSON, YAML | Persistence & configuration |

## Quick Start

```bash
# Install
cd py && pip install -e .

# Start Dashboard (FastAPI, port 9079)
maop start --port 9079

# Run a task through the orchestration loop
maop run --task "refactor auth module"

# Health check
maop health

# Run tests
pytest py/tests
```

**Choose your starting point:**

| Goal | Command | Learn more |
|------|---------|------------|
| Run a quick task | `maop run --task "..."` | [Use Case 1](#1-personal-developer--multi-agent-coordination) |
| Deploy with auth | `MAOP_AUTH=1 maop start` | [Use Case 2](#2-enterprise-deployment--rbac--multi-tenant) |
| Chat with memory | Open dashboard → Chat tab | [Use Case 3](#3-tool-calling--memory-augmented-chat) |
| View costs | Dashboard → Overview → Cost | [Cost Tracking](#cost-tracking) |

## Use Cases

### 1. Personal Developer — Multi-Agent Coordination

Orchestrate multiple agents for a complex refactoring task:

```bash
# Define agents with complementary capabilities
# config/agents.yaml already includes coder, analyst, reviewer

# Run a task that requires planning + coding + review
maop run --task "refactor auth module to use JWT"

# Monitor real-time progress via dashboard
# Open http://localhost:9079 -> Overview tab shows live delegation
# SSE stream at /api/stream/trace/{trace_id} shows token-by-token output
```

**Key features used:**
- Dynamic routing: agent selected by capability match + regex scoring
- Circuit breaker: auto-failover if an agent fails repeatedly
- Three-layer memory: context preserved across agent handoffs
- Plan-Execute-Verify loop: auto-validation after each step

### 2. Enterprise Deployment — RBAC + Multi-Tenant

Deploy with authentication and role-based access control:

```bash
# Enable authentication
$env:MAOP_AUTH = "1"
$env:MAOP_JWT_SECRET = "your-secret-key"
maop start --port 9079

# Login as admin (default credentials in data/.admin-password)
# First launch auto-generates admin password

# Create tenants and assign roles via dashboard
# -> Settings -> RBAC -> Add Tenant
# -> Settings -> RBAC -> Add User with role (admin/operator/viewer)

# Agents are isolated per tenant:
# - Tenant A's users cannot see Tenant B's delegations
# - Quotas enforced per tenant (agent count, API calls)
```

**Key features used:**
- JWT authentication with persistent revocation blacklist
- RBAC: admin / operator / viewer roles
- Multi-tenant data isolation (tenant_id filtering on all queries)
- Audit logging (all write operations recorded)
- TLS support for production (MAOP_TLS=1)

### 3. Tool Calling + Memory-Augmented Chat

Build a knowledge-augmented assistant:

```bash
# Start dashboard
maop start

# Via Chat UI (http://localhost:9079 -> Chat tab):
# 1. Ask: "Summarize the auth module's design"
# 2. Agent uses MCP tools to read files
# 3. Three-layer memory stores the conversation context
# 4. Follow-up: "What are the security risks?"
# 5. Agent retrieves episodic memory from previous turn

# Via API:
curl -X POST http://localhost:9079/api/chat `
  -H "Content-Type: application/json" `
  -d '{"message": "Analyze the routing algorithm", "stream": true}'
# Returns SSE stream with real-time token output
```

**Key features used:**
- MCP integration: Stdio/SSE/WebSocket transports
- Three-layer memory: Working (current turn) -> Episodic (conversation) -> Semantic (knowledge graph)
- Tool lifecycle: register -> enable -> call -> disable
- Streaming: SSE for token-by-token output, WebSocket for bidirectional

## Cost Tracking

MAOP includes built-in cost tracking for LLM API usage:

```bash
# View cost summary via dashboard
# -> Overview tab -> Cost section shows:
#   - Total cost (today / week / month)
#   - Cost per agent
#   - Cost trend chart

# Via API
curl http://localhost:9079/api/cost/summary
# Returns: {"today": 1.23, "week": 8.45, "month": 32.10, "by_agent": {...}}

# Set budget limits
curl -X POST http://localhost:9079/api/budget/set `
  -d '{"daily_limit": 10.0, "monthly_limit": 200.0}'
# When budget exceeded, dispatcher blocks new delegations (exit_code=-6)
```

**How it works:**
- `CostTracker` records token counts (input/output) and estimated cost per call
- `BudgetGuard` enforces daily/monthly limits — excess requests return `exit_code=-6`
- Cost data stored in SQLite `budget_ledger` table
- Dashboard auto-refreshes every 30s
## Configuration

Agents are defined in `config/agents.yaml`:

```yaml
agents:
  - name: coder
    driver: openai
    model: gpt-4
    capabilities: [code, debug, refactor]
  - name: analyst
    driver: anthropic
    model: claude-3
    capabilities: [analyze, summarize]
```

### Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `MAOP_ROOT` | auto-detect | Project root directory |
| `MAOP_ENV` | development | Environment (development/production) |
| `MAOP_JWT_SECRET` | — | JWT secret (required in production) |
| `MAOP_OTEL_ENABLED` | 0 | Enable OpenTelemetry tracing |
| `MAOP_OTEL_EXPORTER` | none | OTel exporter (otlp/console/none) |
| `MAOP_TLS` | 0 | Enable TLS |
| `MAOP_JSON_LOG` | 0 | Enable JSON structured logging |
| `MAOP_PLUGIN_STRICT_CHECKSUM` | 0 | Enforce plugin checksum validation |

## Dashboard

Web dashboard at `http://localhost:9079` with:

- Agent status & monitoring with availability charts
- Performance metrics with latency distribution & radar charts
- Cost tracking with trend analysis & agent cost breakdown
- Real-time SSE streaming for task execution
- Memory search & knowledge graph visualization
- Self-evolution suggestions & analysis
- Multi-tenant support with per-tenant quotas

## Core Modules

| Module | Description |
|--------|-------------|
| `core/services.py` | Service container & dependency injection |
| `core/db_utils.py` | Connection pool & unified SQLite access |
| `core/phases.py` | Phase context & result models |
| `core/otel.py` | OpenTelemetry native tracing integration |
| `core/a2a.py` | Agent-to-Agent (A2A) protocol implementation |
| `core/regression.py` | CI/CD regression testing & persona simulation |
| `core/byok.py` | Bring-Your-Own-Key gateway |
| `core/skill_version.py` | Skill Git-based version management |
| `core/tenant.py` | Multi-tenant isolation & quota management |
| `core/event_bus.py` | Async event bus |
| `core/circuit_breaker.py` | Circuit breaker pattern |
| `core/vector.py` | Vector store for semantic search |
| `core/streaming.py` | SSE streaming infrastructure |

## Development

```bash
# Lint
python -m ruff check py/maop/

# Type check
python -m mypy py/maop/ --ignore-missing-imports

# Test
python -m pytest py/tests/ -v

# Build frontend
cd dashboard-vite && npm run build
```

## Decision Records

Key architectural decisions in [docs/adr/](docs/adr/README.md):
- [ADR-001](docs/adr/001-python-yaml-bridge.md) — Python YAML bridge
- [ADR-002](docs/adr/002-server-merge-orchestrator-deprecation.md) — Server merge + orchestrator deprecation
- [ADR-003](docs/adr/003-mock-fallback-removal.md) — Dashboard mock fallback removal
- [ADR-004](docs/adr/004-security-hardening.md) — Security hardening
- [ADR-005](docs/adr/005-powershell-retention.md) — PowerShell retention
- [ADR-006](docs/adr/006-sse-removal-sync-architecture.md) — SSE removal + sync architecture
- [ADR-007](docs/adr/007-cache-warmup-fix.md) — Cache persistence + warmup fix
- [ADR-008](docs/adr/008-dual-arch-scheduling-audit.md) — Dual-arch scheduling audit
- [ADR-009](docs/adr/009-python-primary-engine.md) — Python primary engine architecture
- [ADR-010](docs/adr/010-bugfix-batch.md) — Batch bugfix (critical/high/medium)
- [ADR-011](docs/adr/011-state-unification.md) — State source unification
- [ADR-012](docs/adr/012-routing-refactor.md) — Config-driven routing refactor
- [ADR-013](docs/adr/013-agent-llm-direct-cli-fallback.md) — Agent LLM direct + CLI fallback

## License

MIT License — see [LICENSE](LICENSE) for details.
