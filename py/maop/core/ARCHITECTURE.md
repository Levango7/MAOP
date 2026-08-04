# MAOP Core Architecture

This document describes the structure of `py/maop/core/`, which contains 116 modules organized by functional responsibility.

## Module Groups

### MCP Protocol (`mcp_*`)
Multi-server MCP (Model Context Protocol) orchestration.

| Module | Lines | Responsibility |
|--------|-------|---------------|
| `mcp_hub.py` | 1097 | Main hub: server registry, lifecycle, tool aggregation, health check |
| `mcp_hub_types.py` | 136 | Pydantic models, enums, exceptions for MCP protocol |
| `mcp_hub_transport.py` | 542 | Transport implementations: stdio, SSE, WebSocket, streamable_http |
| `mcp_adapter.py` | — | Adapter for external MCP clients |
| `mcp_cache.py` | — | Tool result caching |
| `mcp_concurrency.py` | — | Per-server concurrency and rate limiting |
| `mcp_discovery.py` | — | Server capability discovery |
| `mcp_marketplace.py` | 563 | MCP server marketplace (install, search, publish) |
| `mcp_permission.py` | — | Permission checking for tool calls |
| `mcp_audit.py` | — | Audit logging for MCP operations |

### Agent Lifecycle (`agent_*`)
Agent registration, evolution, health, and management.

| Module | Lines | Responsibility |
|--------|-------|---------------|
| `agent_evolution.py` | 562 | Agent parameter evolution via genetic algorithm |
| `agent_lifecycle.py` | — | Agent lifecycle state machine |
| `agent_memory.py` | — | Per-agent memory management |
| `agent_performance.py` | — | Performance tracking and scoring |
| `agent_proxy.py` | — | Agent proxy for remote execution |
| `agent_registry.py` | 443 | Agent registration and lookup |
| `agent_repair.py` | — | Auto-repair for agent configuration issues |
| `agent_scanner.py` | 502 | Agent file scanning and validation |

### Memory (`three_layer_*`, `vector`, `semantic_cache`, `bloom_filter`)
Three-layer cognitive memory architecture.

| Module | Lines | Responsibility |
|--------|-------|---------------|
| `three_layer_memory.py` | 1188 | Main class: Working (LRU) + Episodic (SQLite) + Semantic (Vector) |
| `three_layer_memory_types.py` | 149 | Pydantic models, enums, decay policy |
| `three_layer_memory_utils.py` | 99 | Text processing helpers, focus configs |
| `vector.py` | 562 | Vector store for semantic search (SQLite + embeddings) |
| `semantic_cache.py` | — | Semantic similarity cache for LLM responses |
| `bloom_filter.py` | — | Bloom filter for deduplication |
| `hybrid_search.py` | — | Hybrid keyword + vector search |

### Backends (`backends*`)
Pluggable storage, cache, queue, and KV backends with fail-fast.

| Module | Lines | Responsibility |
|--------|-------|---------------|
| `backends.py` | 717 | Backend factory + SQLite defaults + fail-fast logic |
| `backends_distributed.py` | — | Distributed backends (Redis, PostgreSQL, RabbitMQ, etcd) |
| `backends_redis.py` | — | Redis-specific backend implementations |
| `backends_vault.py` | — | HashiCorp Vault KV backend |
| `backends_pg.py` | — | PostgreSQL backend |
| `backends_rabbitmq.py` | — | RabbitMQ message queue backend |

### LLM Providers (`llm_provider`, `chat_engine`, `react_loop`)
LLM integration and conversation management.

| Module | Lines | Responsibility |
|--------|-------|---------------|
| `llm_provider.py` | 863 | Multi-provider LLM client (OpenAI, Claude, Ollama, custom) |
| `chat_engine.py` | — | Chat orchestration engine with streaming |
| `react_loop.py` | 507 | ReAct (Reason-Act-Observe) loop execution |
| `load_balancer.py` | 667 | LLM provider load balancing and failover |
| `provider_health.py` | — | Provider health monitoring |

### Routing (`route_scorer`, `routing_decision`, `dynamic_router`)
Agent and provider routing.

| Module | Lines | Responsibility |
|--------|-------|---------------|
| `route_scorer.py` | 735 | Multi-factor route scoring (cost, latency, success rate) |
| `routing_decision.py` | 443 | Routing decision engine |
| `dynamic_router.py` | — | Dynamic route configuration and hot-reload |
| `capability_matcher.py` | — | Agent capability matching for routing |

### Reliability (`circuit_breaker`, `cache`, `message_queue`, `worker_pool`)
Resilience patterns and async infrastructure.

| Module | Lines | Responsibility |
|--------|-------|---------------|
| `circuit_breaker.py` | 717 | Circuit breaker pattern for fault tolerance |
| `cache.py` | 734 | LRU cache with TTL, persistence, and metrics |
| `message_queue.py` | 729 | Async message queue with dead-letter support |
| `worker_pool.py` | — | Preemptable async worker pool |
| `preemptable_worker_pool.py` | — | Worker pool with cancellation support |
| `priority_queue.py` | — | Priority-based async queue |
| `rate_limiter.py` | — | Token bucket rate limiter |
| `event_bus.py` | — | Async event bus for pub/sub |

### Data & Storage (`data`, `db_utils`, `db_backup`, `artifact_store`)
Database and file storage management.

| Module | Lines | Responsibility |
|--------|-------|---------------|
| `data.py` | 546 | Data access layer (SQLite, JSON, YAML) |
| `db_utils.py` | — | SQLite connection utilities (WAL, busy_timeout) |
| `db_backup.py` | 428 | Database backup and restore |
| `artifact_store.py` | — | Artifact storage (files, binaries) |
| `image_store.py` | — | Image storage with dedup |
| `kv_store.py` | — | Key-value store abstraction |

### Security (`auth`, `sandbox`, `guardrail`, `permission`, `tls`)
Authentication, authorization, and sandboxing.

| Module | Lines | Responsibility |
|--------|-------|---------------|
| `auth.py` | 513 | JWT auth, API key validation, session management |
| `sandbox.py` | — | Code execution sandbox |
| `guardrail.py` | — | Input/output guardrail checks |
| `permission.py` | — | Permission system |
| `tls.py` | — | TLS/SSL certificate management |
| `api_key_vault.py` | — | API key vault integration |
| `byok.py` | — | Bring Your Own Key support |

### Evolution (`evolution_loop`, `evolution_strategies`, `agent_evolution`)
Self-improvement and parameter optimization.

| Module | Lines | Responsibility |
|--------|-------|---------------|
| `evolution_loop.py` | 1112 | Main evolution loop (evaluate → select → mutate → deploy) |
| `evolution_strategies.py` | — | Evolution strategy implementations |
| `agent_evolution.py` | 562 | Per-agent evolution logic |
| `ab_test.py` | — | A/B testing framework for evolution |
| `regression.py` | — | Regression testing for evolved agents |

### Monitoring (`monitoring`, `otel`, `cost_tracker`, `timeseries`)
Observability and metrics.

| Module | Lines | Responsibility |
|--------|-------|---------------|
| `monitoring.py` | 775 | Metrics collection and Prometheus export |
| `otel.py` | — | OpenTelemetry integration |
| `cost_tracker.py` | — | LLM cost tracking |
| `timeseries.py` | — | Time-series metrics storage |

### Subagent (`subagent*`)
Subagent delegation and lifecycle.

| Module | Lines | Responsibility |
|--------|-------|---------------|
| `subagent.py` | — | Subagent definition and execution |
| `subagent_db.py` | — | Subagent state persistence |
| `subagent_delegation.py` | — | Task delegation to subagents |
| `subagent_lifecycle.py` | — | Subagent lifecycle management |
| `subagent_manager.py` | — | Subagent pool manager |

### Infrastructure
Core utilities and shared components.

| Module | Lines | Responsibility |
|--------|-------|---------------|
| `runtime.py` | — | Command execution runtime (local/docker/remote) |
| `config_mutator.py` | 472 | Safe configuration mutation |
| `change_tracker.py` | 431 | Change tracking for config files |
| `hook_manager.py` | 697 | Lifecycle hook management |
| `plugin.py` | 654 | Plugin system |
| `log_rotate.py` | — | Log rotation |
| `safe_writer.py` | — | Atomic file writer |
| `filelock.py` | — | Cross-platform file locking |
| `self_heal.py` | — | Self-healing diagnostics and repair |
| `services.py` | — | Service registry and discovery |

## Dependency Guidelines

- **No circular imports**: Modules within `core/` must not form import cycles. Use `TYPE_CHECKING` for type-only imports.
- **Hub modules** (`mcp_hub`, `chat_engine`, `evolution_loop`) are top-level orchestrators that import from other groups.
- **Utility modules** (`db_utils`, `safe_writer`, `filelock`) have no dependencies on other core modules.
- **Type modules** (`mcp_hub_types`, `three_layer_memory_types`) are leaf nodes — no imports from other core modules except `db_utils`.

## Future Refactoring Notes

The `core/` directory has 116 modules. Potential future reorganization into sub-packages:
- `core/mcp/` — MCP protocol modules
- `core/agent/` — Agent lifecycle modules
- `core/memory/` — Memory subsystem modules
- `core/backends/` — Backend implementations
- `core/routing/` — Routing and scoring
- `core/reliability/` — Circuit breaker, cache, queue
- `core/security/` — Auth, sandbox, guardrail
- `core/evolution/` — Evolution and A/B testing
- `core/monitoring/` — Metrics and observability

This would require updating all `from maop.core.xxx import` paths across the codebase and tests. Consider using `pyproject.toml` tool configuration or IDE refactoring support for a safe migration.