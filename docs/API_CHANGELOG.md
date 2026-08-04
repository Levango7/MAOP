# API Changelog

All notable changes to the MAOP REST API and WebSocket API.

## v4.4.1 (2026-08-05)

### Added
- `GET /api/agents/{id}/status` — Agent status with health and version info
- `POST /api/mcp/servers/{id}/health-check` — Manual MCP server health check
- `GET /api/memory/three-layer/stats` — Three-layer memory statistics
- `GET /api/evolve/history` — Evolution loop cycle history
- `GET /api/evolve/stats` — Evolution statistics summary
- WebSocket event `agent.evolved` — Emitted when agent parameters are updated by evolution loop

### Changed
- `POST /api/agents` — Now accepts `metadata` field (arbitrary key-value pairs)
- `GET /api/agents` — Response includes `latest_version` and `current_version` fields
- `POST /api/chat` — Supports `stream=true` for SSE streaming responses

### Security
- All `v-html` renderings sanitized via DOMPurify
- CSP headers hardened (removed `unsafe-eval`)
- API key vault uses Fernet encryption with key rotation

### Deprecated
- `GET /api/queue/stats` — Use `GET /api/queue/depth` instead (renamed for clarity)

## v4.4.0 (2026-07-31)

### Added
- Dual-edition architecture (Personal/Enterprise) with FeatureFlag gates
- `GET /api/edition` — Current edition and available features
- `GET /api/rbac/roles` — Role-based access control (Enterprise only)
- `POST /api/rbac/roles` — Create custom role (Enterprise only)
- `GET /api/tenants` — Multi-tenant management (Enterprise only)
- `GET /api/audit/logs` — Audit log query (Enterprise only)

### Changed
- All API responses include `X-Request-ID` header for tracing
- Error responses standardized to `{ "error": { "code": "...", "message": "..." } }`

## v4.3.0 (2026-07-25)

### Added
- MCP Hub with multi-transport support (stdio, SSE, WebSocket, streamable_http)
- `GET /api/mcp/servers` — List registered MCP servers
- `POST /api/mcp/servers` — Register new MCP server
- `GET /api/mcp/tools` — List all available MCP tools (aggregated)
- `POST /api/mcp/tools/{name}/call` — Call MCP tool by name

### Changed
- Agent dispatch now supports MCP tool calls alongside CLI execution
- Routing engine supports MCP tool routing

## v4.2.0 (2026-07-20)

### Added
- Three-layer memory system (Working/Episodic/Semantic)
- `GET /api/memory/episodic` — Episodic memory search
- `POST /api/memory/episodic` — Store episodic entry
- `GET /api/memory/semantic` — Semantic (vector) search
- `POST /api/memory/consolidate` — Trigger memory consolidation

## v4.0.0 (2026-07-15)

### Breaking Changes
- Python-first architecture — PowerShell engine deprecated
- API base path changed from `/` to `/api/`
- Authentication required on all endpoints (API key or JWT)

### Added
- Plan-Execute-Verify loop
- Agent registry and dispatch
- Real-time dashboard with WebSocket streaming
- Cost tracking and budget management