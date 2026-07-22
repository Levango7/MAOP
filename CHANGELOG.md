# Changelog

All notable changes to MAOP (Plan-Execute-Verify) are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

---

## [4.0.0] — 2026-07-19

### New Features

- **Plugin System** (`core/plugin.py`) — Full plugin lifecycle manager with discovery, loading, starting, stopping, and hot-reload. Plugins declare metadata in `maop-plugin.yaml` manifests, expose `maop_plugin_init`/`maop_plugin_shutdown` entry points, and can register hooks via HookManager bridge. SQLite persistence for plugin state. Dashboard API at `/api/plugins/*`.

- **Real-time Cost Tracker** (`core/cost_tracker.py`) — Per-call token usage recording with automatic cost calculation for 7+ models (GPT-4o, Claude 3.5, DeepSeek, etc.). Aggregated summaries by model/agent/session. Daily/monthly budget monitoring with HookManager alert integration. Customizable pricing. Dashboard API at `/api/cost/*`.

- **ReAct Loop Engine** (`core/react_loop.py`) — Thought→Action→Observation micro-cycle for autonomous agent reasoning. Integrates FunctionCallBridge and ChangeTracker. Configurable max iterations and tool selection.

- **Change Tracker** (`core/change_tracker.py`) — File system snapshot/diff/rollback with unauthorized change detection. Change log persistence. Dashboard API at `/api/react/snapshots/*` and `/api/react/diff`.

- **Artifact Store** (`core/artifact_store.py`) — Versioned artifact storage with save/load/history/restore/tag/diff. Blob file persistence. Dashboard API at `/api/react/artifacts/*`.

- **Session Manager** (`core/session.py`) — Full session CRUD with status management, token budget tracking, and SQLite persistence.

- **Conversation Manager** (`core/conversation.py`) — Multi-turn message history with context window sliding, auto-compression, and message search.

- **Project Context** (`core/project_context.py`) — Project structure tree, tech stack detection, config file reading, instruction file injection, git status.

- **MCP Client Runtime** (`core/mcp_client.py` + `mcp_transport.py` + `mcp_registry.py`) — Full MCP protocol client with Stdio/SSE transport, tool discovery, and execution.

- **Function Calling Bridge** (`core/function_call.py`) — Unified OpenAI/Anthropic/Ollama function calling → MAOP ToolCall → MCP/ToolManager execution → result re-injection loop.

- **Structured Output Parser** (`core/output_parser.py`) — JSON extraction from code blocks/raw/embedded, Pydantic validation, schema verification gate.

- **Streaming Integration** (`core/streaming.py`) — SubprocessStreamer + StreamRegistry, driver streamer support, SSE endpoint.

- **Permission Manager** (`core/permission.py`) — Allow/ask/deny policy, HumanProxy integration into maop_execute + maop_loop.

- **Hook Decision Influence** — HookResult now supports decision (allow/deny/modify) + modified_data, enabling hooks to veto or transform pipeline data.

### Changed

- `maop_execute.py` — Added tools/provider/max_tool_rounds + react_mode/react_max_iterations parameters, function_call re-injection loop, ReAct mode branch.
- `maop_verify.py` — Added `_gate_schema` verification gate for structured output validation.
- `error_schema.py` — MaopResult now has `structured_output` field.
- `dashboard/server.py` — Registered 7 new routers (stream, permission, mcp, session, react, plugin, cost).
- Version bumped from 3.5.0 → 4.0.0.

### Tests

- 43 tests in `test_plugin_cost.py` (PluginManager: 19, CostTracker: 24)
- 31 tests in `test_react_loop.py` (ReactLoop + ChangeTracker + ArtifactStore)
- 36 tests in `test_session.py` (Session + Conversation + ProjectContext)
- 25 tests in `test_function_call.py` (FunctionCallBridge + ToolSchemaGenerator)
- 27 tests in `test_output_parser.py` (OutputParser + SchemaGate)
- Total new tests: **162**

---

## [3.5.0] — 2026-07-18

### New Features

- **Mavis subagent merge** — Restructured `agents.yaml`: removed `mavis-coder`, `mavis-general`, `mavis-verifier` as standalone agents; merged into `mavis.subagents` configuration. Subagents are now only reachable via parent delegation (`mavis/verifier`, `mavis/coder`, `mavis/general`).

- **Subagent routing support** — `Dispatcher._resolve_agent()` now supports `parent/child` format (e.g. `mavis/verifier`). When a `/` is detected, the parent's `cli` + child's `cli_args` are combined to build the final `AgentConfig`. Routing table entries `mavis/verifier` for `verify` and `review` routes now resolve correctly.

- **ConfigLoader subagents parsing** — `ConfigLoader.load()` now correctly parses the `subagents` section from `agents.yaml` into `AgentDef.subagents: dict[str, SubagentDef]`. Added `SubagentDef` Pydantic model with `cli_args`, `capabilities`, `description`, `model_display` fields.

### Changed

- **agents.yaml routing** — `review.fallback` changed from `mavis-verifier` to `mavis/verifier`; `verify.primary` changed from `mavis-verifier` to `mavis/verifier`.

### Tests

- Added 9 tests in `test_dispatcher_extended.py`: subagent resolution (parent/child, coder, unknown child/parent, caching, parent still works), dispatch integration, ConfigLoader subagents parsing.

---

## [3.3.0] — 2026-07-18

### New Features

- **Subagent hierarchical delegation** — Added `core/subagent.py` (SubagentManager) with parent/child lifecycle tracking, depth-limited recursive delegation, and inter-agent message passing. Integrated into `Dispatcher.delegate_to_subagent()` for recursive agent→agent→agent dispatch. Dashboard API: `/api/subagent/spawn`, `/api/subagent/terminate`, `/api/subagent/children`, `/api/subagent/tree`, `/api/subagent/send`, `/api/subagent/receive`, `/api/subagent/purge`.

- **Worktree parallel workspaces** — Added `core/worktree.py` (WorktreeManager) with git worktree-based filesystem isolation for parallel task execution. Automatic branch creation, stale worktree cleanup, and fallback directory copy when git is unavailable. Integrated into `WorkerPool._run_task()` for automatic worktree creation/cleanup per task. Dashboard API: `/api/worktree/create`, `/api/worktree/remove`, `/api/worktree/list`, `/api/worktree/cleanup`.

- **Protocol registry system** — Added `core/protocol.py` (ProtocolRegistry) for dynamic agent communication protocol registration with schema validation, versioning, and protocol-validated messaging. Supports runtime protocol write without code changes. Dashboard API: `/api/protocol/register`, `/api/protocol/unregister`, `/api/protocol/validate`, `/api/protocol/send`, `/api/protocol/messages`.

- **LLM Provider enhancements** — Four sub-features:
  - **Ollama local model support** — Added `ProviderType.OLLAMA` and `ProtocolType.OLLAMA_CHAT` to schema, with `thinking_to_api_params()` mapping for Ollama's `options.num_predict`. Added `ollama` provider to `models.yaml` (disabled by default).
  - **Provider/Model dynamic CRUD** — Added `add_provider()`, `remove_provider()`, `add_model()`, `remove_model()`, `save()` to `ModelRegistry`. Dashboard API: `/api/model/provider/add`, `/api/model/provider/delete`, `/api/model/add`, `/api/model/delete`.
  - **API Key encrypted vault** — Added `core/api_key_vault.py` (ApiKeyVault) with Fernet symmetric encryption, MAOP_KEY env / `.enc_key` file key management, and plaintext fallback. Dashboard API: `/api/model/key/store`, `/api/model/key/delete`, `/api/model/key/list`.
  - **Runtime health check** — Added `core/provider_health.py` (ProviderHealthChecker) with actual API call verification via httpx, latency measurement, and model list discovery. Dashboard API: `/api/model/health/check`.

### Tests

- Added `test_subagent.py` (14 tests): spawn, terminate, get, list_children, tree, messaging.
- Added `test_worktree.py` (10 tests, 9 skip if no git): create, remove, get, list, cleanup.
- Added `test_protocol.py` (20 tests): register, unregister, get, list, validate, messaging.
- Added `test_provider_enhanced.py` (24 tests): Ollama types, CRUD, vault, health check.

### Bug Fixes

- **worktree.py FileNotFoundError** — `_git()` now catches `FileNotFoundError` when git is not in PATH instead of crashing.
- **registry.py save() Enum serialization** — Fixed yaml.dump producing Python object tags for Enum values; now serializes `.value` strings.

---

## [3.2.3] — 2026-07-18

### Security (P0)

- **SQL injection in message_queue._count()** — Added `_VALID_TABLES` whitelist to prevent arbitrary table name injection.
- **Path injection in db_backup.py VACUUM INTO** — Added regex validation for backup path components + single-quote rejection.
- **Missing checksum validation in migration.py** — Added SHA256 checksum verification for migration SQL files.
- **GET /api/control/run → POST** — Converted state-changing control endpoint from GET to POST; added independent `/api/control/pause`, `/api/control/resume`, `/api/control/stop` endpoints.
- **Unrestricted agent upgrade in system.py** — Added package name whitelist for agent upgrade operations.
- **SHA-256 legacy auth compatibility** — Removed SHA-256 password hash fallback from `auth.py`; only bcrypt is accepted.
- **Guardrail fail-open** — Changed guardrail evaluation to fail-closed: exceptions during rule evaluation now block execution instead of allowing it.
- **CI Python 3.14** — Pinned CI to Python 3.12/3.13 (3.14 not yet stable).
- **docker-compose.yml build context** — Fixed build context path to point to correct `py/` directory.

### Hardening (P1)

- **Password leak to stderr** — Removed password plaintext logging from `auth.py` stderr output.
- **CORS tightened** — Restricted CORS allowlist from wildcard to explicit origins.
- **Shared db_utils.py** — Extracted common SQLite connection management into `core/db_utils.py` (WAL mode, foreign keys, busy timeout).
- **Shared _find_project_root()** — Unified project root discovery across modules.
- **Extended core module tests** — Added 58 new tests across 4 test files: `test_maop_loop_extended.py` (13), `test_guardrail_extended.py` (20), `test_engine_extended.py` (14), `test_dispatcher_extended.py` (11).
- **ADR-010 regression tests** — Added 9 regression tests in `test_adr010_regression.py`.
- **Dockerfile version sync** — Updated Dockerfile to version 3.2.2.
- **doc-pipeline Python driver** — Added Python CLI driver support for doc-pipeline workflow.

### Optimization & Quality (P2)

- **VectorStore performance** — numpy-accelerated cosine similarity, batch loading with `executemany`, triple cache (`_cache`/`_text_cache`/`_meta_cache`).
- **Dashboard router error handling** — Added `@handle_api_errors` decorator to `evolve.py`, `memory.py`, `model.py` routers.
- **CI consolidation** — Merged two CI workflow files into single `ci.yml`.
- **Coverage gate** — Added `--cov-fail-under=40` to CI pytest command.
- **kv_store connection mode** — Unified to WAL mode with foreign key enforcement.
- **TLS version enforcement** — `settings.py` now rejects TLSv1/TLSv1_1 connections.
- **CJK token estimation** — `context_compressor.py` now uses character-based estimation for CJK text.
- **maop.ps1 PS 5.1 compatibility** — Fixed PowerShell 5.1 compatibility issues in entry script.

### Deep Audit Fixes

- **S-01: db_backup.py VACUUM INTO injection** — Added single-quote rejection for backup_path to prevent SQL escape.
- **S-02: auth.py JWT key persistence** — `JWTHandler.__init__` now calls `load_jwt_secret()` instead of generating ephemeral key, ensuring JWT tokens survive restarts.
- **S-03: auth.py APIKeyStore thread safety** — `_get_conn()` now protected by `self._lock` to prevent race condition.
- **S-06: model.py model/switch validation** — Added `models.yaml` lookup to validate new_model before writing to `agents.yaml`.
- **S-08: middleware.py public paths** — Added `/api/auth/login` and `/api/auth/status` to public paths list.

### Integration Fixes

- **test_router_control.py** — Merged duplicate `TestControlRunPost` classes; aligned tests with POST handler behavior (no-op → default task); migrated pause/resume/stop tests to independent endpoints; converted maintain/provider-health tests from GET to POST.
- **test_dashboard_auth.py** — Updated SHA-256 compatibility tests to verify legacy hashes are rejected.
- **evolve.py @handle_api_errors** — Added missing error handler decorator to `/api/evolve/analyze` endpoint.
- **system.py indentation** — Fixed indentation error introduced during P0-4 refactoring.

### Deep Audit Fixes (Round 2)

- **S-04: dispatcher.py PS cli_args injection** — Added regex whitelist validation for `cli_args` template; rejects unsafe characters before PowerShell execution.
- **S-05: system.py pip whitelist hardening** — Changed `_get_allowed_packages()` to intersect dynamic agent CLI list with a hardcoded safe package set, preventing `agents.yaml` modification from bypassing the whitelist.
- **S-07: auth.py login brute-force protection** — Added per-username login failure tracking; locks account for 15 minutes after 5 consecutive failures.
- **S-09: tls.py placeholder cert rejection** — `create_ssl_context()` now checks for placeholder comment lines in cert files and refuses to load them.
- **S-10: data.py query() internal API** — Renamed `query()` to `_query()` with warning docstring; updated all internal and test callers.
- **S-11: kv_store.py connection leak** — Added `close()` method and `__del__` destructor to KVStore for proper connection cleanup.
- **P-03: DataBridge singleton instances** — Cached `ToolManager`, `SandboxManager`, `HumanProxy` instances in `DataBridge.__init__` instead of creating new ones per request.
- **P-04: DataBridge queue stats** — Merged 3 independent `SELECT COUNT(*)` queries into single `GROUP BY status` query.
- **P-08: system.py overview cache** — Added 60-second TTL cache to `/api/overview` endpoint to avoid re-scanning source files on every request.
- **A-01: Unified _connect()** — Replaced duplicated `_connect()` context managers in `vector.py`, `message_queue.py`, `sandbox.py`, `data.py`, `tool_manager.py`, `human_proxy.py` with `db_utils.sqlite_connect()`.
- **A-02: Deduplicated validate_identifier** — `data.py` now imports `validate_identifier` from `db_utils` instead of re-implementing it.
- **T-01: test_db_utils.py** — 17 new tests covering `validate_identifier` and `sqlite_connect` (WAL, foreign keys, rollback, row factory).
- **T-02: test_sandbox.py** — 10 new tests covering SandboxManager create/get/list/cleanup/run.
- **T-03: test_runtime.py** — 12 new tests covering `_resolve_cmd`, `LocalRuntime`, `IsolatedRuntime`, `RuntimeConfig`.

---

## [3.2.2] — 2026-07-17

### Fixed

- **cache_guard.py SingleFlight deadlock** — `_wait()` was called inside `with self._mutex` but itself tries to acquire `_mutex`, causing a non-reentrant lock deadlock. Fixed by extracting event reference inside lock, calling `_wait()` outside.
- **cache_guard.py SingleFlight result cleanup race** — `finally` block deleted results before waiters could read them. Fixed by lazy cleanup on next call.

### Added

- **934 new tests** (763→1697 total, all passing, zero regression):
  - `test_store.py` (57) — MemoryStore: store/search/facets/JSON search/trace/trajectory/inject/stats/prune
  - `test_vector.py` (46) — VectorStore: cosine similarity, embedding, indexing, search, persistence
  - `test_analyzer.py` (49) — DependencyDAG, topo sort, parallel groups, cycles, rule decomposition, strategy selection
  - `test_context_compressor.py` (40) — All 9 section extractors, compress/to_prompt, trim-to-budget
  - `test_timeseries.py` (38) — Record/batch, raw & aggregated query, downsampling, retention policy
  - `test_kv_store.py` (52) — Get/set, TTL, CAS, namespaces, bulk ops, stats
  - `test_monitoring.py` (40) — Metrics recording, alerting, health checks
  - `test_tool_manager.py` (32) — Tool registration, execution, validation
  - `test_cache_guard.py` (23) — SingleFlight, cache guard, dedup
  - `test_rate_limiter.py` (28) — Token bucket, sliding window, burst control
  - `test_state_classifier.py` (29) — Task state classification, transition rules
  - `test_human_proxy.py` (30) — Human-in-the-loop proxy, queue management
  - `test_auth.py` (35) — Authentication, token management, RBAC
  - `test_registry.py` (53) — ProviderRegistry, ModelRegistry, agent model resolution
  - `test_budget.py` (30) — BudgetGuard: spend tracking, alerts, reconciliation
  - `test_selector.py` (26) — Model selection, fallback chain, routing
  - `test_schema.py` (35) — All Pydantic models, enums, serialization
  - `test_provider.py` (36) — DashboardState, agent status, delegation counting
  - `test_loader.py` (33) — Config loading, YAML parsing, reload detection
  - `test_hot_reload.py` (29) — File hashing, change detection, watch loop
  - `test_evolve.py` (38) — EvolveEngine: analyze/suggest/apply/promote/status
  - `test_prompt_manager.py` (37) — Template CRUD, render, search, export/import
  - `test_concurrency.py` (33) — TaskQueue, TaskPool, SSEStreamer, TokenStreamer
  - `test_consolidator.py` (23) — Dream pipeline, summary building, consolidation
  - `test_cli.py` (20) — Argument parsing, command dispatch, all subcommands

---

## [3.2.1] — 2026-07-16

### Fixed

- **maop_execute trace_id propagation** — `maop_execute()` now sets `result.trace_id` after dispatch, ensuring trace IDs are always present on returned `MaopResult` objects for observability.
- **pyproject.toml dependencies** — Added missing `python-dotenv` (pydantic-settings dep), `mmh3` (MurmurHash3), and `sentence-transformers` (optional ML, under `[ml]`).

### Added

- **100 new tests** (663→763 total, all passing):
  - `test_fallback.py` (16) — FallbackManager: chain building, failure tracking, should_fallback, reset
  - `test_quota.py` (15) — QuotaEnforcer: sliding window, check/consume, usage stats
  - `test_maop_plan.py` (21) — Task routing: keyword matching, gate selection, budget config
  - `test_maop_verify.py` (24) — Verification gates: exit_code, output, content-safety, syntax-check, lint, dry-run
  - `test_maop_execute.py` (14) — Execution: dispatch, guardrail pre/post, trace_id, error handling

---

## [3.2.0] — 2026-07-16

### Summary

MAOP completes its transformation from a PowerShell-based multi-agent prototype to a
production-ready Python orchestration package. The PowerShell engine is archived to
`archive/ps-legacy/` (EOL v4.0). Python is now the sole runtime.

### Added

- **Model Management** — `model/registry.py`, `model/selector.py`, `model/budget.py`,
  `model/quota.py`, `model/fallback.py`, `model/schema.py` (7 modules).
  Policy-driven model selection with budget guards, quota tracking, and fallback chains.
- **Control Plane** — `control/plane.py`, `control/audit.py` (3 modules).
  Process-level job management with audit logging.
- **Contract Testing** — `tests/contract/` (4 test files, 92 tests).
  Behavioral, dispatcher, model API, and control API contracts.
- **Dashboard Routers** — `dashboard/routers/` package with 6 router modules
  (data, control, model, evolve, memory, system). 83 API endpoints.
- **Data Migrations** — `core/migration.py` + `data/migrations/001_init.sql`.
  Version-tracked schema migrations with up/down SQL and SHA256 checksums.
- **Dynamic Router (Python)** — `core/dynamic_router.py`.
  Port of `dynamic-router.ps1` with agent health scoring and 30s cache.
- **Safe Expression Evaluator** — `engine.py` `safe_eval()` using `ast` module.
  Replaces bare `eval()` with whitelist-based AST node evaluation.
- **WebSocket Snapshot Cache** — 5s TTL cache on `_ws_push_loop` to avoid
  redundant DataBridge queries during 15s broadcast intervals.
- **Chart.js Local Vendor** — `dashboard/js/vendor/chart.umd.min.js` (205 KB).
  Eliminates CDN dependency for air-gapped deployments.
- **Requirements Lock** — `py/requirements.lock` with full transitive dependency pinning.
- **Design Rules** — `dashboard/DESIGN_RULES.md` documenting the 3-level border hierarchy
  and divider width rules (outer 3px / middle 2px / inner 1px).
- **CHANGELOG.md** — This file.

### Changed

- **README** — Fully rewritten to reflect Python-first architecture, 6-layer structure,
  port 9079, 7 sub-packages, 711 tests, ~80 API endpoints, 18 agents, 13 gate scripts.
- **Version Unification** — `pyproject.toml`, `__init__.py`, `index.html` all at 3.2.0.
- **Dependency Pinning** — All `pyproject.toml` dependencies changed from `>=` to `==`.
- **agents.yaml** — `doc-pipeline` workflow migrated from `driver: wrapper` (PS) to
  `driver: cli` (Python). Model fields normalized with `model_ref` for precise lookup.
- **Model Registry** — `resolve_agent_model()` accepts `model_ref` parameter for exact
  model reference; heuristic fallback emits `logger.warning`.
- **Dashboard Frontend** — Monolithic `app.js` split into 11 modular files (`js/app-*.js`).
  Version branding updated from `v7` to `v3.2`.
- **maop.ps1** — PS fallback path replaced with archive notice and `exit 1`.
  Python-first routing is the only path.
- **hot_reload.py** — Watch list updated: `agents.yaml` + `rules.yaml` + `models.yaml`
  (removed `routing.yaml`).

### Removed

- **PowerShell Engine** — 61 `.ps1` files moved to `archive/ps-legacy/`.
  `src/` directory cleared of engine scripts.
- **Startup Wrappers** — `start_dashboard.py`, `start.bat`, `start-server.ps1`,
  `run_dashboard.py` moved to `archive/`. Canonical entry: `python -m maop.dashboard.server`.
- **Dead Code** — `_invoke_ps_fallback()` and `fallback_to_ps` parameter removed from
  `data_bridge.py`. `app.js.bak` and `app.js.legacy` deleted.
- **Residual Files** — `circuit-breaker.json` deleted (pure legacy, no Python reads/writes).
  `human-queue.json` reset to empty (stale data from 2026-07-03).
  `py/.tmp/` cache directory removed.
- **eval()** — Bare `eval(condition_expr, {"__builtins__": {}}, context)` replaced by
  `safe_eval(condition_expr, context)` with AST whitelist.

### Security

- **Command Injection** — 4 Critical PS injection vectors closed (delegate-plugin.ps1).
  Python dispatcher uses `shlex.quote()` / `subprocess` list args (no shell=True).
- **Path Traversal** — Gate name validation (`^[a-zA-Z0-9_-]+$`), memory ID validation,
  sandbox workdir confinement.
- **Expression Safety** — `safe_eval` blocks function calls, attribute chains to
  `__class__`/`__subclasses__`, and all builtin access. 12 expression tests + 5 attack
  vectors verified.
- **API Security** — TLS support, API key auth middleware, rate limiting (30 RPS / 60 burst),
  CORS allowlist.

### Tests

- **711 total tests** (up from 547).
- **561 passed** in system Python (without fastapi/pytest-asyncio).
- **Full pass** with dev dependencies installed: `pip install -e ".[dev]"`.

---

## [3.1.0] — 2026-07-14

### Added

- Dashboard v7 UI rewrite with 18 navigation items in 5 groups.
- Six-layer architecture: CLI → MaopLoop → Engine → Services → Infrastructure → Data.
- 42 Python modules (up from 13).
- Docker multi-stage build, docker-compose, graceful shutdown.
- GitHub Actions CI/CD pipeline.
- Pydantic-based configuration with `.env` support.

### Changed

- PowerShell-to-Python migration: 8 phases completed.
- 220 tests all green (0 failures).

---

## [3.0.0] — 2026-07-10

### Added

- Initial Python engine: `maop_loop.py`, `engine.py`, `dispatcher.py`, `guardrail.py`.
- SQLite-backed persistence: `maop.db`, `memory.db`, `queue.db`.
- FastAPI dashboard server (port 9079).
- Circuit breaker, DAG engine, memory store with vector search.

### Changed

- Architecture shift from PowerShell-first to Python-first.
- Config-driven agent registration via `agents.yaml`.

---

## [2.x] — 2026-06-01 to 2026-07-09

PowerShell era. 48 scripts in `src/`, 13 gate scripts in `src/gates/`.
See `archive/ps-legacy/` for historical code. These versions are EOL and unsupported.

---

## [1.x] — 2026-05-01 to 2026-05-31

Initial prototype. Single-file `maop.ps1` with basic Plan-Execute-Verify loop.
No dashboard, no model management, no contract tests.
