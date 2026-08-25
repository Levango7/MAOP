# API Changelog

All notable changes to the MAOP REST API and WebSocket API.

## v5.1.0 (2026-08-14)

### Added
- LLM 任务拆分 API — 自动将复杂任务拆分为子任务 + DAG 依赖编排
- 工作流编辑器 API — 可视化 DAG 工作流 CRUD + 节点配置 + 保存/加载
- 配置历史 API — 配置变更快照 + 一键回滚 + 差异对比
- Skill 编辑器 + 市场 API — Skill 在线编辑 + 模板市场 + 导入/导出
- 异常调度 API — 异常检测 + 自动重试策略 + 降级调度
- Hook 配置 API — Webhook Hook 配置 + 事件触发 + 执行日志
- License 管理 CRUD API（企业版）— 过期预警 + 特性开关绑定
- SSO/SAML 集成 API（企业版）— SAML 2.0 IdP 对接 + SP 配置 + 属性映射
- 审计日志查询/导出 API（企业版）— 全操作审计 + 不可篡改性
- 配额管理 API（企业版）— 租户级配额（API 调用/Token/存储）+ 超额拒绝 + 用量看板
- API Key 管理 API（企业版）— 生成/轮转/吊销 + scope 权限绑定
- 通知中心 API（企业版）— 邮件/Webhook 通知 + 通知模板 + 事件订阅

### Changed
- **⚠ Breaking**：统一错误响应格式对齐 `ErrorSchema` — 所有经 `handle_api_errors` 装饰器（含 `HTTPException`）的端点错误响应采用扁平结构 `{status, error, code, detail, request_id}`（全部 string 类型），取代历史嵌套 `{"error":{code,message}}`
- **⚠ Breaking**：Engine 无 `step_executor` 时不再返回假成功 — AGENT/DAG/PLAN 步骤在未注入执行器时一律返回 `StepStatus.FAILED` + `error="No step executor configured..."`

## v5.0.2 (2026-08-13)

### Changed
- 前端布局重构与 ESLint warnings 修复（无后端 API 变更）

## v5.0.1 (2026-08-13)

### Fixed
- `enterprise_api_guard` 中间件顺序修正：未认证请求现在返回 401 而非 404（guard 先放行让 AuthMiddleware 处理认证）

## v5.0.0 (2026-08-11)

### ⚠ Breaking Changes
- 移除 `maop.dashboard.provider.create_app()`（自 v4.0.0 起废弃，生产代码应使用 `maop.dashboard.server:app`）
- 移除 `maop.dashboard.provider._render_html()`（自 v4.0.0 起废弃，v3.x 静态 HTML 渲染器，已被 Vue 3 SPA 取代）
- 移除 `maop_plan.py` legacy keyword routing（`_fallback_keyword_route` / `_route_by_keyword` 及 `_ROUTING_RULES`，自 v4.0.0 起标记 DEPRECATED）
- 移除 `/api/batch` 端点（`dashboard/routers/data.py` 中 `deprecated=True` 的批量端点）

### Deprecated
- 短名环境变量加 DeprecationWarning（将在 v6.0.0 移除，推荐迁移到规范长名）：
  - `MAOP_PORT` → `MAOP_DASH_PORT`
  - `MAOP_WORKERS` → `MAOP_DASH_WORKERS`
  - `MAOP_TLS` → `MAOP_TLS_ENABLED`
  - `MAOP_AUTH` → `MAOP_AUTH_ENABLED`

### Added
- `GET /api/stream/agent/{execution_id}` — Agent 执行过程 token-by-token SSE 流式推送（区别于 `/api/chat/stream` 的 chat 流式）
- 迁移指南 `docs/migration-5.0.md`

## v4.5.0 (2026-08-06)

### Added
- `GET /api/stream/dag/{execution_id}` — DAG 执行进度 SSE 端点（支持 Last-Event-ID 断线重连）
- `GET /api/knowledge-graph` — 聚合三层记忆（short/long/vector）实体-关系数据，知识图谱可视化

### Changed
- `core/` 子包重构：将 `core/`（116 模块）拆分为 9 个职责清晰的子包，保留 `core/__init__.py` re-export shim，现有 `from maop.core.xxx import yyy` 调用零改动通过

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