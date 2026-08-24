# API Changelog

All notable changes to the MAOP REST API and WebSocket API.

## v5.1.0 (2026-08-14)

### Added
- **LLM 任务拆分**：自动将复杂任务拆分为子任务 + DAG 依赖编排
- **工作流编辑器**：可视化 DAG 工作流编辑 + 节点配置 + 保存/加载
- **配置历史**：配置变更快照 + 一键回滚 + 差异对比
- **Skill 编辑器 + 市场**：Skill 在线编辑 + 模板市场 + 导入/导出
- **异常调度**：异常检测 + 自动重试策略 + 降级调度
- **Hook 配置**：Webhook Hook 配置 UI + 事件触发 + 执行日志
- **企业版功能补全**：许可证管理（CRUD + 过期预警 + 特性开关绑定）、SSO/SAML 2.0 集成、审计日志（查询/导出 + 不可篡改性）、租户级配额管理、API Key 生成/轮转/吊销、通知中心（邮件/Webhook + 模板 + 事件订阅）

### Changed
- **统一错误响应格式对齐 `ErrorSchema`**：所有经 `handle_api_errors` 装饰器（含 `HTTPException`）的端点错误响应采用扁平结构 `{ "status": "error", "error": "...", "code": "...", "detail": "...", "request_id": "..." }`（全部 string 类型，`status` 默认 `"error"`，其余默认空串），取代历史嵌套 `{"error":{code,message}}` 描述。权威定义见 `py/maop/dashboard/error_handler.py` `ErrorSchema`
- 版本号统一升级至 v5.1.0（pyproject.toml / __init__.py / Dockerfile / package.json / Chart.yaml 等）

### Fixed
- 修复 `/users` 路由守卫缺失（补 `meta.requiresEnterprise`）
- **⚠ Breaking（P2-1）**：Engine 无 `step_executor` 时不再返回假成功。AGENT/DAG/PLAN 步骤在未注入执行器时一律返回 `StepStatus.FAILED` + `error="No step executor configured..."`，消除监控假阳性

## v5.0.0 (2026-08-11)

### Breaking Changes
- **废弃清理与 API 收敛**：删除 deprecated ≥ 2 版本的 API
  - `maop.dashboard.provider.create_app()` / `_render_html()`（deprecated since v4.0.0）
  - `maop.core.agent.delegation.subagent_delegation` shim
  - `maop.core.project_context` / `maop.core.agent.memory_ctx.project_context`
  - `maop_plan.py` legacy keyword routing fallback
  - `/api/batch` deprecated 端点
- **配置收敛**：短名环境变量（`MAOP_PORT`、`MAOP_WORKERS`、`MAOP_TLS`、`MAOP_AUTH`）加 `DeprecationWarning`，推荐迁移到规范长名（`MAOP_DASH_PORT`、`MAOP_DASH_WORKERS`、`MAOP_TLS_ENABLED`、`MAOP_AUTH_ENABLED`）。短名在 v6.0.0 移除

### Added
- **流式 Agent token 响应增强**：新增 `GET /api/stream/agent/{execution_id}` SSE 端点 + 前端 `useAgentTokenStream.js` composable + Chat.vue 集成增强
- **DAG 进度推送端点**：Orchestrator 在 DAG 节点级状态变更时通过 SSE/WebSocket 推送增量进度事件（节点 pending/running/success/failed/skipped）
- **知识图谱端点**：基于三层记忆（short/long/vector）构建实体-关系图，支持节点筛选、路径高亮、时间轴回放
- **分布式执行支持**：DAG 工作流分布式调度与执行
- **记忆统一接口**：三层记忆（Working/Episodic/Semantic）统一访问接口
- **迁移指南**：`docs/migration-5.0.md` 覆盖后端 API 变更 + 配置迁移 + Docker 部署变更
- **Phase 5b 交付物**：SLA/支持体系、隐私政策/DPA、PG 高可用（Patroni）、CI Playwright E2E、K8s Operator 集成测试、性能压测、LDAP 真实环境验证

### Changed
- **统一错误响应格式对齐 `ErrorSchema`**：所有经 `handle_api_errors` 装饰器（含 `HTTPException`）的端点错误响应采用扁平结构 `{ "status": "error", "error": "...", "code": "...", "detail": "...", "request_id": "..." }`（全部 string 类型），取代历史嵌套 `{"error":{code,message}}` 描述。权威定义见 `py/maop/dashboard/error_handler.py` `ErrorSchema`

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
- Error responses standardized to flat `ErrorSchema` shape `{ "status": "error", "error": "...", "code": "...", "detail": "...", "request_id": "..." }` (all string fields; authoritative definition in `py/maop/dashboard/error_handler.py`)

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