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
| Infrastructure | `core/` (107+ modules) | Shared services & utilities |
| Data | SQLite, JSON, YAML | Persistence & configuration |

## 双版架构（Dual Edition）

MAOP 自 2026-07-20 起采用 **单一代码库 + 运行时 Edition 检测** 的双版架构（详见 [ADR-016](docs/adr/016-dual-edition-architecture.md)），同一套核心代码同时服务两类用户：

- **个人版 (Personal)**：MIT 许可，零配置开箱即用，面向个人开发者和小团队
- **企业版 (Enterprise)**：Commercial 许可，面向企业客户，提供 RBAC / 多租户 / SSO / 审计 / HA 等企业级能力

### 能力对比

| 能力 | Personal | Enterprise |
|------|----------|------------|
| 多代理编排 (Multi-Agent Orchestration) | ✓ | ✓ |
| MCP Hub | ✓ | ✓ |
| 三层记忆 (Three-Layer Memory) | ✓ | ✓ |
| 向量检索 (Vector Search) | ✓ | ✓ |
| 预算守卫 (Budget Guard) | ✓ | ✓ |
| 熔断器 (Circuit Breaker) | ✓ | ✓ |
| 热重载 (Hot Reload) | ✓ | ✓ |
| 插件系统 (Plugin System) | ✓ | ✓ |
| RBAC 角色权限 | ✗ | ✓ |
| SSO (OIDC + SAML) 单点登录 | ✗ | ✓ |
| PostgreSQL 持久化 | ✗ | ✓ |
| Redis 高可用缓存 | ✗ | ✓ |
| Vault 密钥管理 | ✗ | ✓ |
| 多租户隔离 (Tenant Isolation) | ✗ | ✓ |
| Audit Log 审计日志 | ✗ | ✓ |
| n8n 集成 | ✗ | ✓ |
| RabbitMQ 消息队列 | ✗ | ✓ (可选依赖 pika) |
| etcd 分布式 KV | ✗ | ✓ (可选依赖 etcd3) |
| License CRL 在线撤销 | ✗ | ✓ |
| Vue Dashboard 企业版路由 | ✗ | ✓ |

> Enterprise = Personal ∪ Enterprise 独占能力；企业版包含所有功能。

### Edition 检测优先级

通过 `maop/config/edition.py` 的 `detect_edition()` 检测，优先级：

1. `set_edition()` 程序覆盖（测试 / 灰度）
2. `MAOP_EDITION` 环境变量
3. `maop.enterprise` 包可导入性自动探测
4. 默认 `PERSONAL`

### FeatureFlag 统一 Gate

所有 edition 相关能力差异通过 `FeatureFlag` 枚举统一 gate，**禁止**在其他模块直接比较 `get_edition() == ENTERPRISE`：

```python
from maop.config.edition import has_feature, FeatureFlag, require_feature

if has_feature(FeatureFlag.RBAC):
    # 企业版逻辑
else:
    # 个人版逻辑或跳过

require_feature(FeatureFlag.SSO)  # 个人版抛 FeatureNotAvailable
```

### License 配置

企业版通过 Ed25519 license key 校验激活：

| 场景 | 行为 |
|------|------|
| 无 `MAOP_LICENSE_KEY` | honor-system 模式 + 警告日志（向后兼容） |
| key 有效 | 激活 ENTERPRISE |
| key 无效 / 过期 | 降级 PERSONAL + error 日志 + 7 天宽限期 |

```bash
# 配置 license（企业版）
$env:MAOP_LICENSE_KEY = "your-license-key-here"
maop start
```

License 颁发指南见 [docs/enterprise/license-issuance-guide.md](docs/enterprise/license-issuance-guide.md)。

### 后端默认值差异

| 后端类型 | Personal | Enterprise |
|----------|----------|------------|
| storage | sqlite | postgresql |
| cache | memory | redis |
| queue | sqlite | redis（默认；rabbitmq 可通过 `MAOP_QUEUE_BACKEND=rabbitmq` 启用，可选依赖 pika） |
| kv | sqlite | sqlite（默认；etcd 可通过 `MAOP_KV_BACKEND=etcd` 启用，可选依赖 etcd3） |
| secret | local | vault |

企业版后端不可用时自动降级到个人版后端（通过 `record_degradation()` 记录）。

### 双包发布

- `maop`（PyPI, MIT）：核心 + 个人版功能
- `maop-enterprise`（私有分发, Commercial）：依赖 `maop`，包含 `maop/enterprise/` 模块

```bash
pip install maop              # 个人版
pip install maop-enterprise   # 企业版（自动依赖 maop）
```

`maop/enterprise/__init__.py` 在 import 时调用 `set_edition(Edition.ENTERPRISE)`，这是企业版包"存在即激活"的机制。

## Quick Start

```bash
# Install (Python package)
cd py && pip install -e .

# Build frontend (Vue dashboard, 首次或前端变更后需要)
cd ../dashboard-enterprise && npm install && npm run build && cd ..

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
| Activate enterprise | `MAOP_LICENSE_KEY=... maop start` | [双版架构](#双版架构dual-edition) |

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
# Real-time updates via WebSocket at /ws
# (HTTP SSE transport endpoint removed per ADR-006; the internal SSEStreamer
#  primitive is still used as a streaming abstraction for server-side events)
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

> **Optional backends (RabbitMQ / etcd):** these are documented capabilities
> but are **off by default**. Enable them in production by adding the matching
> Compose profile and the corresponding env override:
> ```bash
> # RabbitMQ queue backend (needs optional `pika` dep, MAOP_QUEUE_BACKEND=rabbitmq)
> docker compose -f docker-compose.yml -f docker-compose.prod.yml \
>   --profile rabbitmq up -d
> # etcd distributed KV backend (needs optional `etc3` dep, MAOP_KV_BACKEND=etcd)
> docker compose -f docker-compose.yml -f docker-compose.prod.yml \
>   --profile etcd up -d
> ```

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
# Returns streaming response with real-time output
```

**Key features used:**
- MCP integration: Stdio/WebSocket transports (HTTP SSE endpoint removed per ADR-006; internal SSEStreamer primitive retained)
- Three-layer memory: Working (current turn) -> Episodic (conversation) -> Semantic (knowledge graph)
- Tool lifecycle: register -> enable -> call -> disable
- Streaming: WebSocket for bidirectional real-time output (HTTP SSE endpoint removed per ADR-006; internal SSEStreamer primitive retained)

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
| `MAOP_EDITION` | auto-detect | 强制 edition（personal/enterprise），覆盖自动检测 |
| `MAOP_LICENSE_KEY` | — | 企业版 license key（缺失时 honor-system，无效时降级 personal + 7 天宽限期） |

## Dashboard

Web dashboard at `http://localhost:9079` with:

- Agent status & monitoring with availability charts
- Performance metrics with latency distribution & radar charts
- Cost tracking with trend analysis & agent cost breakdown
- Real-time streaming for task execution (WebSocket, per ADR-006)
- Memory search & knowledge graph visualization
- Self-evolution suggestions & analysis
- Multi-tenant support with per-tenant quotas

前端源码位于 `dashboard-enterprise/`（Vue 3 + Vite），构建产物输出到 `dashboard/dist-enterprise/`。原生JS版本已归档至 `archive/js-dashboard/`。

> **技术栈说明**：Vue 3 是前端框架（声明式 UI、组件化、响应式数据），Vite 是构建工具（dev server + 生产打包）。两者配合使用，无冲突。详见 [DESIGN_RULES.md 第 10 节](DESIGN_RULES.md)。

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
| `core/streaming.py` | Streaming infrastructure (WebSocket; HTTP SSE endpoint removed per ADR-006, internal SSEStreamer primitive retained) |
| `config/edition.py` | Dual-edition 注册表与 FeatureFlag gate（[ADR-016](docs/adr/016-dual-edition-architecture.md)） |
| `enterprise/` | 企业版扩展模块（rbac/tenant/audit/sso/ha/license/n8n 等，仅 `maop-enterprise` 包含） |

## Development

```bash
# Lint
python -m ruff check py/maop/

# Type check
python -m mypy py/maop/ --ignore-missing-imports

# Test
python -m pytest py/tests/ -v

# Build frontend (Vue dashboard)
cd dashboard-enterprise && npm run build
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
- [ADR-014](docs/adr/014-ha-single-instance-status.md) — HA 单实例状态（Superseded by ADR-015）
- [ADR-015](docs/adr/015-distributed-ha-redis-lease.md) — 分布式 HA Redis 租约
- [ADR-016](docs/adr/016-dual-edition-architecture.md) — 双版架构（Personal / Enterprise）

## License

MIT License — see [LICENSE](LICENSE) for details.

> **Enterprise Edition Note**: `maop-enterprise` 包及 `py/maop/enterprise/` 模块遵循 Commercial 许可，详见 [docs/enterprise/license-issuance-guide.md](docs/enterprise/license-issuance-guide.md)。
