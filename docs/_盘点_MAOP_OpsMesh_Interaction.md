# 接口盘点报告：MAOP + OpsMesh + Interaction

> 盘点时间：2026-08-11
> 盘点范围：F:\Nexus\MAOP、F:\Nexus\OpsMesh、F:\Nexus\Interaction
> 盘点维度：对外 API 端点 / 端口配置 / 鉴权机制 / 事件总线 / 多租户隔离 / 健康检查可观测性 / 部署方式 / Agent 机制
> 盘点方式：基于实际代码与配置文件只读扫描（Read/Grep/Glob），未修改任何源码

---

## 1. MAOP（Multi-Agent Orchestration Platform）

- 仓库路径：`F:\Nexus\MAOP`
- 技术栈：Python 3 + FastAPI + uvicorn + SQLite/PostgreSQL + Redis + Vue 3 + Vite
- 架构层次：Entry (cli.py / maop.ps1) → Orchestrator (maop_loop.py + engine.py) → Dispatcher (dispatcher.py + maop_plan.py) → Infrastructure (core/) → Data (SQLite/JSON/YAML)
- 双版架构：Personal (MIT) / Enterprise (Commercial)，通过 `maop/config/edition.py` 的 FeatureFlag 统一 gate

### 1.1 对外 API 端点

MAOP Dashboard 由 FastAPI 实现（`py/maop/dashboard/server.py`），路由按域拆分到 `py/maop/dashboard/routers/` 下 41 个 router 文件，共注册 **346 个端点**（含 `/api/v1/*` 自动别名）。监听端口默认 **9079**。

#### 1.1.1 核心 HTTP REST 端点（按 router 分组）

| 路径前缀 | router 文件 | 主要端点 | 功能 |
|---|---|---|---|
| `/` | server.py | `GET /` | SPA 入口（Vue3 index.html） |
| `/style.css`, `/favicon.svg`, `/assets/*`, `/public/*` | server.py | 静态资源 | Vite 构建产物 |
| `/api/health` | server.py | `GET` | 健康检查（含 active_agents、edition、tls、auth 状态） |
| `/api/prometheus` | server.py | `GET` | Prometheus 文本格式指标 |
| `/api/csp-report`, `/api/csp-violations` | server.py | `POST`, `GET` | CSP 违规上报与查询 |
| `/api/v1/version` | server.py | `GET` | API 版本 |
| `/api/auth/*` | auth.py | `GET /status`, `POST /login`, `POST /refresh`, `POST /logout`, `POST /register`, `GET /users`, `DELETE /users/{username}`, `PUT /users/{username}` | JWT 鉴权 + 用户管理（PBKDF2-HMAC-SHA256 600k 迭代） |
| `/api/control/*` | control.py | `GET /status`, `POST /run`, `POST /pause`, `POST /resume`, `POST /stop`, `POST /validate`, `POST /doctor`, `POST /cancel`, `POST /refresh`, `POST /clear-cache`, `POST /provider-health`, `POST /maintain` | 编排控制面 |
| `/api/report`, `/api/agents/stats`, `/api/timeseries`, `/api/metrics`, `/api/live`, `/api/snapshot`, `/api/failures`, `/api/chain`, `/api/optimizer` | data.py | `GET` | 数据查询（报表/时序/快照/失败链/优化器） |
| `/api/graph/*` | data.py | `GET /stats`, `/nodes`, `/edges`, `/neighbors` | 知识图谱 |
| `/api/vector/*` | data.py | `GET /stats`, `/list`, `/search` | 向量检索 |
| `/api/wiki/stats`, `/api/prompts`, `/api/coordination`, `/api/teams`, `/api/skills`, `/api/tools/stats`, `/api/guardrails`, `/api/sandbox/list`, `/api/human/pending`, `/api/mcp/servers`, `/api/mcp/tools`, `/api/mcp`, `/api/versions`, `/api/providers` | data.py | `GET` | Wiki/Prompt/Coordination/Team/Skill/Tool/Guardrail/Sandbox/MCP 元信息 |
| `/api/logs`, `/api/logs/delegations`, `/api/logs/checker`, `/api/logs/analysis` | data.py | `GET` | 日志检索 |
| `/api/model/*` | model.py | `GET /agents`, `/quota`, `/registry`, `/list`, `/providers`, `/select`, `/budget`, `/quota/status`, `/policies`, `/key/list`; `POST /switch`, `/provider/add`, `/provider/delete`, `/add`, `/delete`, `/key/store`, `/key/delete`, `/health/check` | 模型管理 + BYOK |
| `/api/evolve/*` | evolve.py |-| 自演化（status/analyze/suggestions/report/strategies/history/apply-suggestion） |
| `/api/evolution/*` | evolution.py |-| 自演化闭环（evaluate/suggest/ab/create/ab/record/ab/evaluate/ab/list/deploy/promote/deploy/rollback/deploy/history/run/cycles/pending/approve） |
| `/api/memory/*` | memory.py | `GET /deep`, `/search`, `/trace`, `/stats`; `POST /store` | 三层记忆 |
| `/api/neural/*` | memory.py | `GET /status`, `/attention`; `POST /attention` | 神经机制 |
| `/api/overview`, `/api/system/resources`, `/api/system/diagnostics` | system/overview.py | `GET` | 系统总览 |
| `/api/subsystems`, `/api/coordination_report`, `/api/routing`, `/api/security/config` | system/v4_misc.py | `GET` | 子系统/路由/安全配置 |
| `/api/workflow/list`, `/api/workflows` | system/workflow.py | `GET`; `POST /run` | 工作流 |
| `/api/agent/config`, `/api/agent/upgrade` | system/agent_admin.py |-| Agent 配置/升级 |
| `/api/framework/status`, `/api/framework/logs`, `/api/framework/config` | system/framework.py | `GET` | 框架状态 |
| `/api/agents/*` | agents.py + agents/ 子包 | `GET ""`, `/routes`, `/match`, `/{name}`, `/{name}/health-log`, `/{name}/diagnose`, `/{name}/memory`, `/{name}/memory/summary`, `/upgrade/status`, `/{name}/upgrade/check`, `/{name}/evolution-status`; `POST /scan`, `/{name}/health-check`, `/health-check-all`, `/{name}/enable`, `/{name}/disable`, `/register`, `/{name}/repair`, `/{name}/upgrade`, `/{name}/evolve`, `/{name}/memory`; `DELETE /{name}` | Agent CRUD + 健康检查 + 演化 + 记忆 |
| `/api/chat/*` | chat.py | `POST ""`, `/stream`, `/memory/search`, `/memory/consolidate`, `/upload`; `GET /models`, `/sessions`, `/{session_id}`, `/memory/stats`, `/images/{session_id}`; `DELETE /{session_id}`, `/images/{image_id}` | Chat + 流式 + 多模态 |
| `/api/knowledge/*` | knowledge.py | `GET /stats`, `/facts`, `/entities/{name}`, `/relations`, `/graph`, `/context`, `/vector/stats`; `POST /extract`, `/vector/search`, `/vector/index` | 知识图谱 + 向量 |
| `/api/info/*` | info.py + info/ 子包 | `GET /pillars`, `/roles`, `/modules`, `/workflows`, `/architecture`, `/edition`, `/config`, `/activity`, `/adrs`; `POST /edition` | 元信息 + ADR |
| `/api/cost/*` | cost.py | `GET /entries`, `/summary`, `/budget`, `/pricing`; `PUT /pricing/{model}`; `POST /record` | 成本追踪 |
| `/api/budget/*` | budget.py | `GET /status`; `POST /reset`, `/record` | 预算守卫 |
| `/api/stream/*` | stream.py | `GET ""`, `/active`, `/dag/{execution_id}`, `/agent/{execution_id}`, `/{trace_id}` | SSE 流式（内部 SSEStreamer 原语，HTTP SSE 端点按 ADR-006 移除，但 router 仍保留） |
| `/api/mcp/*` | mcp.py | `GET /servers`, `/tools`, `/health`; `POST /connect/{server_name}`, `/disconnect/{server_name}`, `/servers`, `/call`; `DELETE /servers/{server_name}` | MCP Hub |
| `/api/plugins/*` | plugin.py | `GET ""`, `/{plugin_id}`; `POST /discover`, `/{plugin_id}/load`, `/{plugin_id}/start`, `/{plugin_id}/stop`, `/{plugin_id}/reload`, `/load-all`, `/start-all`, `/stop-all`; `PUT /{plugin_id}/config` | 插件系统 |
| `/api/protocol/*` | protocol.py | `GET /get`, `/list`, `/versions`, `/messages`; `POST /register`, `/unregister`, `/validate`, `/send` | A2A / 协议注册 |
| `/api/subagent/*` | subagent.py | `GET /list`, `/transcript`; `POST /spawn`, `/wait`, `/cancel` | Subagent 管理 |
| `/api/worktree/*` | worktree.py |-| Git worktree（create-root/branch/abandon/get/list/merge/checkpoint/rollback） |
| `/api/hook/*` | hook.py |-| Hook（register/unregister/enable/disable/list/get/trigger/logs/events） |
| `/api/session/*` | session.py |-| 会话管理（CRUD + messages + context） |
| `/api/react/*` | react.py |-| ReAct 循环（snapshots/diff/changes/artifacts） |
| `/api/routing/*` | routing.py + routing_preview.py |-| 路由决策追踪 + 预览（match/cooldowns/scores/decisions） |
| `/api/tool-audit/*` | tool_audit.py |-| 工具审计（entries/stats/cleanup） |
| `/api/bridge/*` | agent_proxy.py |-| Agent 桥接（adapters/call/health/sync-config） |
| `/api/permission/*`, `/api/approval/*` | permission.py |-| 权限规则 + 审批 |
| `/api/observability/*` | observability.py |-| 可观测性（status/metrics/metrics/prometheus/traces/health/config; POST record/setup） |
| `/api/compliance/*` | compliance.py | `POST /delete-user-data`, `/export-user-data` | GDPR 合规 |
| `/api/audit/*` | audit.py | `GET /events`, `/summary`, `/filter` | 审计日志（双版统一） |
| `/api/tenant/*` | tenant.py | `GET /list`, `/{tenant_id}`, `/{tenant_id}/usage`; `POST /create`, `/{tenant_id}/suspend`, `/{tenant_id}/activate`; `DELETE /{tenant_id}` | 多租户管理（Enterprise） |
| `/api/rbac/*` | rbac.py | `GET /grants`, `/roles`, `/permissions`; `POST /grant`, `/revoke` | RBAC（Enterprise） |
| `/api/sso/*` | sso.py | `GET /authorize`, `/callback`, `/validate`, `/config`; `POST /logout` | SSO OIDC/SAML（Enterprise） |
| `/api/n8n/*` | n8n.py | `GET /workflows`, `/executions/{execution_id}`, `/health`; `POST /webhook`, `/workflows/{workflow_id}/trigger` | n8n 集成（Enterprise） |
| `/a2a` | core/agent/delegation/a2a.py | `POST ""`; `GET /cards`, `/tasks/{task_id}` | A2A 协议（JSON-RPC 2.0，Google A2A 标准） |
| `/api/v1/*` | server.py 自动别名 | 所有 `/api/*` 端点（除 health/stream/auth） | 向后兼容版本化 |

#### 1.1.2 WebSocket 端点

| 路径 | 协议 | 功能 |
|---|---|---|
| `/ws` | WebSocket | 实时推送（snapshot/live/report/timeseries，15s 间隔；token 经 Sec-WebSocket-Protocol 子协议或 query param 传递） |

#### 1.1.3 端点数量统计

- HTTP REST 端点：**343** 个（含 `/api/v1/*` 别名则翻倍）
- WebSocket 端点：**1** 个（`/ws`）
- A2A JSON-RPC 端点：**3** 个（`/a2a`, `/a2a/cards`, `/a2a/tasks/{task_id}`）
- **总计：347 个对外端点**

### 1.2 端口配置

| 配置项 | 默认值 | 来源 | 说明 |
|---|---|---|---|
| `MAOP_DASH_PORT` / `MAOP_PORT` | **9079** | `.env.example`、`start.sh`、`docker-compose.yml`、`cli.py` | Dashboard 监听端口 |
| `MAOP_DASH_HOST` |Bash| `0.0.0.0`（容器）/ `127.0.0.1`（start.sh） | 监听地址 |
| `MAOP_DASH_WORKERS` / `MAOP_WORKERS` | 1 / 4 | `.env.example` | uvicorn worker 数 |
| `MAOP_REDIS_PORT` | 6379 | `.env.example` | Redis（可选 profile） |
| `MAOP_PG_PORT` | 5432 | `.env.example` | PostgreSQL（可选 profile） |
| `MAOP_VAULT_ADDR` | http://vault:8200 | `.env.example` | Vault（可选 profile） |
| `MAOP_OTEL_ENDPOINT` | http://otel-collector:4317 | `.env.example` | OTLP gRPC |
| `N8N_BASE_URL` | http://localhost:5678 | `.env.example` | n8n（可选 profile） |

- 启动命令：`maop start --port 9079` 或 `python -m maop.dashboard.server`
- start.sh 默认 `127.0.0.1:9079`，容器内 `0.0.0.0:9079`
- 多 worker 模式下 `MAOP_BACKGROUND_TASKS=0` 自动禁用 per-worker 后台任务

### 1.3 鉴权机制

| 机制 | 实现 | 配置 | 说明 |
|---|---|---|---|
| **JWT** | `core/security/auth.py` `JWTHandler` | `MAOP_JWT_SECRET`、`MAOP_JWT_ALLOW_EPHEMERAL=1` | HS256，持久化撤销黑名单 |
| **密码哈希** | `dashboard/routers/auth.py` | PBKDF2-HMAC-SHA256 600k 迭代 | OWASP 2023 推荐 |
| **RBAC** | `enterprise/rbac.py` + `dashboard/routers/rbac.py` | `FeatureFlag.RBAC` | admin/operator/viewer 三级角色 |
| **SSO/OIDC/SAML** | `enterprise/sso.py` + `enterprise/saml_handler.py` + `dashboard/routers/sso.py` | `MAOP_SSO_*`、`FeatureFlag.SSO` | 企业版单点登录 |
| **TLS** | `core/security/tls.py` | `MAOP_TLS=1`、`MAOP_TLS_CERT`、`MAOP_TLS_KEY`、`MAOP_TLS_MIN_VERSION=TLSv1_2` | uvicorn 直接终止或 nginx profile |
| **API Key Vault** | `core/security/api_key_vault.py` `ApiKeyVault` | `MAOP_KEY` / `MAOP_KEY_FILE` | Fernet 加密（32 字节 base64） |
| **CSP** | `core/security/middleware.py` `CSPMiddleware` | `MAOP_CSP=1`、`MAOP_CSP_REPORT_ONLY`、`MAOP_CSP_CONNECT_SRC` | Content-Security-Policy + 报告端点 |
| **Rate Limit** | `core/security/middleware.py` `RateLimitMiddleware` | `MAOP_RATE_LIMIT_RPS=30`、`MAOP_RATE_LIMIT_BURST=60` | 全局令牌桶 |
| **Edition Gate** | `config/edition.py` `FeatureFlag` | `MAOP_EDITION`、`MAOP_LICENSE_KEY` | Personal/Enterprise 双版特性 gate |
| **License** | `enterprise/license.py` | `MAOP_LICENSE_KEY`（Ed25519） | honor-system / 激活 / 降级 + 7 天宽限 |
| **CRL** | `enterprise/crl.py` | `MAOP_CRL_URL`、`MAOP_CRL_STRICT` | License 在线撤销 |
| **LDAP** | `core/security/ldap_provider.py` | `LDAPConfig` | 企业版 LDAP 集成 |
| **Auth Middleware** | `core/security/middleware.py` `AuthMiddleware` | `MAOP_AUTH=1` / `MAOP_AUTH_ENABLED=1` | public_paths 白名单（health/prometheus/auth/login/stream） |
| **CORS** | `server.py` `CORSMiddleware` | `MAOP_CORS_ORIGINS` | 显式 methods/headers，非通配 |
| **Trust Proxy** |Bash| `MAOP_TRUST_PROXY=0` | XFF 信任（默认不信任防伪造） |
| **MCP 安全** | `core/mcp_audit.py`、`core/mcp_permission.py` | `MAOP_MCP_STRICT_COMMAND_WHITELIST=1` | Stdio 命令白名单（RCE 防护） |
| **Plugin 校验** | `core/plugin.py` | `MAOP_PLUGIN_STRICT_CHECKSUM=1` | 插件 checksum 强校验 |
| **Vault Secret** | `core/backends_vault.py` | `MAOP_SECRET_BACKEND=vault`、`MAOP_VAULT_TOKEN` | 企业版密钥托管 |
| **BYOK** | `core/security/byok.py` `BYOKGateway` |Bash| Bring-Your-Own-Key 网关 |
| **Guardrail** | `core/security/guardrail.py` `Guardrail` | `config/rules.yaml` | 规则引擎（fnmatch 模式匹配） |
| **Sandbox** | `core/security/sandbox.py` `SandboxManager` |Bash| 沙箱隔离 |
| **Session** | `core/security/session.py` `SessionManager` |Bash| 会话管理 |

- **生产默认**：`MAOP_ENV=production` 时 `MAOP_AUTH=1` 强制开启（C-P0-1 fix）
- **个人版**：dev/development/local/test 环境默认禁用 auth，其余默认启用

### 1.4 事件总线 / 消息

| 机制 | 实现 | 说明 |
|---|---|---|
| **Async EventBus** | `core/event_bus.py` `EventBus` | 进程内 async pub/sub，支持 wildcard topic、ACK、retry、dead-letter、priority（LOW/NORMAL/HIGH/CRITICAL）、history（200 条）、dead letters（1000 条） |
| **RabbitMQ** | `core/backends_rabbitmq.py` `RabbitMQQueueBackend` | 可选依赖 `pika>=1.3.0`，`MAOP_QUEUE_BACKEND=rabbitmq`、`MAOP_RABBITMQ_URL`；持久化（delivery_mode=2）、consumer group、DLX、延迟投递（TTL+DLX） |
| **Redis Streams** | `core/backends_redis.py` + `worker/distributed_worker.py` | 分布式 worker 消费 Redis Streams（F1-01） |
| **WebSocket Streaming** | `dashboard/server.py` `/ws` + `dashboard/ws_broadcast.py` | 实时推送 snapshot（15s 间隔，5s TTL 缓存），per-client 5s send timeout |
| **SSE Streamer** | `core/streaming.py` `SSEStreamer` | 内部原语（HTTP SSE 端点按 ADR-006 移除，原语保留作服务端流式抽象） |
| **Hook Manager** | `core/hook_manager.py` | 注册式 hook（register/unregister/enable/disable/trigger） |
| **Message Queue** | `core/message_queue.py` + `core/priority_queue.py` | 优先级队列 |
| **Worker Pool** | `core/worker_pool.py` + `core/preemptable_worker_pool.py` | 进程内 worker 池 |

- **Event 模型**：`Event(topic, data, source, timestamp, priority, ack_required)`
- **Dead Letter**：`DeadLetterEntry(event_id, topic, handler_name, error, attempts, timestamp)`
- **可选后端**：memory（默认）/ redis / rabbitmq / sqlite

### 1.5 多租户隔离

| 能力 | 实现 | 说明 |
|---|---|---|
| **TenantManager** | `core/tenant/manager.py` | 多租户管理（create/get/suspend/activate/delete/usage） |
| **Row-Level Security** | `core/tenant/rls.py` `TenantRLS` | 行级安全 scoping（scoped_tables 配置） |
| **Resource Quota** | `core/tenant/quota.py` `ResourceQuotaManager` | 多资源配额（tokens/requests/agents/models） |
| **Audit** | `core/tenant/audit.py` `AuditLogger` | append-only 审计 trail |
| **Hierarchy** | `core/tenant/hierarchy.py` | 租户层级 |
| **Compliance** | `core/tenant/compliance.py` | GDPR 合规（delete-user-data / export-user-data） |
| **Schema 隔离** | PostgreSQL（`MAOP_STORAGE_BACKEND=postgresql`） | 企业版后端 |
| **Tenant API** | `dashboard/routers/tenant.py` | `/api/tenant/*`（Enterprise only，FeatureFlag.MULTI_USER gate） |
| **Edition Guard** | `server.py` `enterprise_api_guard` | 个人版对 `/api/tenant/*`、`/api/sso/*`、`/api/rbac/*`、`/api/n8n/*` 返回 404 |

- **TenantConfig**：`tenant_id, display_name, enabled, quota_tokens, quota_requests, allowed_agents, allowed_models, metadata`
- **个人版**：无多租户（单人）
- **企业版**：tenant_id 过滤所有查询，配额 enforcement

### 1.6 健康检查 / 可观测性

| 端点 / 机制 | 实现 | 说明 |
|---|---|---|
| `GET /api/health` | `server.py` | 返回 status/version/edition/uptime_ms/active_agents/tls/auth/rate_limit |
| `GET /api/prometheus` | `server.py` + `core/monitoring/monitoring.py` | Prometheus 文本格式（Counter/Gauge/Histogram） |
| **OpenTelemetry** | `core/monitoring/otel.py` + `core/otel.py` | `MAOP_OTEL_ENABLED=1`、`MAOP_OTEL_EXPORTER=otlp/console/none`、`MAOP_OTEL_ENDPOINT`、`MAOP_OTEL_SERVICE_NAME` |
| **Metrics** | `core/monitoring/monitoring.py` `MetricsCollector` | Counter/Gauge/Histogram + cardinality 上限（`MAOP_METRIC_MAX_CARDINALITY=1000`） |
| **Cost Tracking** | `core/monitoring/cost_tracker.py` `CostTracker` | LLM API 用量与成本（token counts + 估算成本） |
| **Budget Guard** | `core/monitoring/budget_guard.py` `BudgetGuard` + `core/budget_guard.py` | 日/月预算上限，超限 exit_code=-6 |
| **TimeSeries** | `core/monitoring/timeseries.py` `TimeSeriesStore` | 时序存储 + 保留策略 |
| **JSON Logging** | `core/monitoring/monitoring.py` `JsonLogFormatter` + `setup_json_logging` | `MAOP_JSON_LOG=1`（ELK/Loki/CloudWatch 友好） |
| **Structured Logger** | `core/monitoring/monitoring.py` `StructuredLogger` | 结构化日志 |
| **Observability API** | `dashboard/routers/observability.py` | `/api/observability/{status,metrics,metrics/prometheus,traces,health,config}` + `POST /record`, `/setup` |
| **Log Rotate** | `core/reliability/log_rotate.py` `LogRotateScheduler` | 自动日志滚动（`MAOP_LOGROTATE_INTERVAL=600`） |
| **DB Backup** | `core/backends/db_backup.py` `DbBackup` | 自动备份（`MAOP_BACKUP_INTERVAL=3600`） |
| **Circuit Breaker** | `core/circuit_breaker.py` | 熔断器（`MAOP_CB_FAILURE_THRESHOLD=5`、`MAOP_CB_RECOVERY_TIMEOUT_S=30`） |
| **OTel Collector** | `docker-compose.yml` profile `otel` | `otel/opentelemetry-collector-contrib:0.96.0`，OTLP gRPC 4317 / HTTP 4318 |
| **Prometheus + AlertManager** | `docker-compose.yml` profile `monitoring` | `prom/prometheus:v2.51.0` + `prom/alertmanager:v0.27.0` |

### 1.7 部署方式

| 方式 | 命令 / 文件 | 说明 |
|---|---|---|
| **pip install** | `cd py && pip install -e .` | 个人版开发安装 |
| **pip install maop-enterprise** |Bash| 企业版（自动依赖 maop） |
| **maop.ps1** | `maop.ps1 start` | PowerShell 启动器（Windows） |
| **start.sh** | `./start.sh` | Bash 启动器（Linux/macOS），默认 127.0.0.1:9079 |
| **CLI** | `maop start --port 9079` | Python CLI（`py/maop/cli.py`） |
| **Makefile** | `make install / test / lint / clean` | 本地测试 runner（venv + pytest + ruff） |
| **Docker Compose** | `docker compose up -d` | `docker-compose.yml`（dashboard + agent-exec + queue-worker） |
| **Docker Compose Prod** | `docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d` | 生产 overlay |
| **可选 profiles** | `--profile postgres/redis/vault/otel/tls/monitoring/n8n` | PostgreSQL / Redis / Vault / OTel / Nginx TLS / Prometheus / n8n |
| **Dockerfile** | `py/Dockerfile` | 多阶段构建 |
| **Nginx** | `nginx.conf` + `nginx.prod.conf` | TLS 终止反代 |
| **前端构建** | `cd dashboard-enterprise && npm install && npm run build` | Vue 3 + Vite，产物到 `dashboard/dist-enterprise/` |

- **容器安全**：`cap_drop: ALL` + `no-new-privileges:true` + 资源限制
- **健康检查**：`python -c "import urllib.request; urllib.request.urlopen('http://localhost:9079/api/health')"`
- **服务拓扑**：dashboard（FastAPI）+ agent-exec（worker）+ queue-worker（human approval / async tasks）

### 1.8 Agent 机制 / 能力节点注册

| 机制 | 实现 | 说明 |
|---|---|---|
| **CLI** | `py/maop/cli.py` | `maop {start, stop, status, run, validate, health}` + `maop mcp marketplace {list-registries, add-registry, remove-registry, search, install, uninstall, list-installed}` + `maop worker start` + `maop config migrate` + `maop migrate` |
| **agents.yaml** | `config/agents.yaml`（735 行） | 声明式 agent 配置：name/driver(cli/openai/anthropic)/model/capabilities/cli_args/timeout_s/subagents |
| **Plan-Execute-Verify** | `maop_loop.py` + `maop_plan.py` + `maop_execute.py` + `maop_verify.py` | 三阶段循环 |
| **Dispatcher** | `delegate/dispatcher.py` + `core/dynamic_router.py` + `core/capability_matcher.py` + `core/route_scorer.py` | 配置驱动路由（capability match + regex scoring） |
| **MCP Hub** | `core/mcp_hub.py` + `core/mcp_adapter.py` + `core/mcp_marketplace.py` + `core/mcp_discovery.py` + `core/mcp_permission.py` + `core/mcp_audit.py` + `core/mcp_cache.py` + `core/mcp_concurrency.py` | MCP 服务器管理 + 工具调用 + marketplace + 安全 |
| **A2A 协议** | `core/a2a.py` + `core/agent/delegation/a2a.py` | Google A2A 标准（JSON-RPC 2.0），`/a2a` 端点，跨系统 agent 通信（ADK/LangGraph/CrewAI） |
| **Subagent** | `core/subagent.py` + `core/subagent_manager.py` + `core/subagent_delegation.py` + `core/subagent_lifecycle.py` + `core/subagent_db.py` | 子代理 spawn/wait/cancel/list/transcript |
| **Worker Pool** | `core/worker_pool.py` + `core/preemptable_worker_pool.py` + `worker/agent_executor.py` + `worker/queue_worker.py` + `worker/distributed_worker.py` | 进程内 + 分布式 worker |
| **Distributed Scheduler** | `core/scheduling/` | DAG 节点调度 + WorkerRegistry + 心跳 |
| **Three-Layer Memory** | `core/three_layer_memory.py` + `core/agent_memory.py` + `core/memory/` | Working → Episodic → Semantic |
| **Circuit Breaker** | `core/circuit_breaker.py` | 自动 failover |
| **Budget Guard** | `core/budget_guard.py` + `core/monitoring/budget_guard.py` | 预算守卫 |
| **Plugin System** | `core/plugin.py` + `core/plugins/` + `core/marketplace/` | 插件注册/load/start/stop/reload + checksum 校验 |
| **Hooks** | `core/hook_manager.py` | 生命周期 hook |
| **Self-Evolution** | `core/evolution_loop.py` + `core/agent_evolution.py` + `core/agent_performance.py` + `core/ab_test.py` + `core/evolution_strategies.py` | 自演化闭环（PerformanceEvaluator / ABTest SPRT / AutoDeployer） |
| **Agent Registry** | `core/agent_registry.py` + `core/agent_lifecycle.py` + `core/agent_repair.py` + `core/agent_scanner.py` | agent 注册/扫描/健康/修复 |
| **Agent Strategy Learner** | `agent_strategy_learner.py` | 策略学习 |
| **Cache Evolver** | `cache_evolver.py` | 缓存演化 |
| **Loop Analyzer** | `loop_analyzer.py` + `history_analyzer.py` | 循环分析 |
| **Concurrency** | `concurrency.py` | 并发控制 |
| **Function Call** | `core/function_call.py` + `core/tool_manager.py` + `core/tool_schema.py` + `core/tool_audit.py` | 工具调用 |
| **Output Parser** | `core/output_parser.py` | 输出解析 |
| **Prompt Manager** | `prompt_manager.py` + `core/prompt_version.py` | Prompt 版本管理 |
| **Project Context** | `core/project_context.py` | 项目上下文 |
| **React Loop** | `core/react_loop.py` + `dashboard/routers/react.py` | ReAct 循环 + 快照/diff/回滚 |
| **Regression** | `core/regression.py` | CI/CD 回归 + persona 模拟 |
| **Runtime** | `core/runtime.py` | 运行时 |
| **Safe Writer** | `core/safe_writer.py` | 安全写入 |
| **State Classifier** | `core/state_classifier.py` | 状态分类 |
| **Worktree** | `core/worktree.py` + `dashboard/routers/worktree.py` | Git worktree 隔离 |
| **Knowledge Graph** | `core/knowledge_graph.py` + `core/knowledge_extractor.py` | 知识图谱 |
| **Vector Store** | `core/vector.py` + `core/vector/` + `core/hybrid_search.py` | 向量检索 |
| **Chat Engine** | `core/chat_engine.py` + `core/conversation.py` + `core/context_compressor.py` | 对话引擎 |
| **LLM Provider** | `core/llm_provider.py` + `core/provider_health.py` + `core/load_balancer.py` | LLM 提供商管理 |
| **BYOK Gateway** | `core/security/byok.py` | Bring-Your-Own-Key |
| **Skill Version** | `core/skill_version.py` | Skill Git 版本管理 |

- **配置示例**（`config/agents.yaml`）：
  ```yaml
  agents:
    MAOP:
      capabilities: [codegen, chat, planning, search, review, explain, orchestrate, verify, memory, mcp, pipeline, vision, multimodal]
      driver: cli
      cli_args: -m maop.cli run --task "{task}"
      subagents:
        self: { capabilities: [...], cli_args: -m maop.cli run --task "{task}" --depth {depth} }
  ```

---

## 2. OpsMesh（网段运维中枢）

- 仓库路径：`F:\Nexus\OpsMesh`
- 技术栈：Go 1.26 + gRPC + MySQL/Redis + Prometheus + Helm + distroless
- 架构：单二进制双模式（`--mode=controlplane|agent`），控制面 HTTP+gRPC+metrics 三监听
- 通信模型：gRPC（agent 通道）+ HTTP REST/SSE（B/S 仪表盘）+ Prometheus（指标）

### 2.1 对外 API 端点

OpsMesh 控制面由 Go `net/http` 实现（`internal/controlplane/server.go`），注册在主 mux 上；gRPC 服务由手写 ServiceDesc + protobuf IDL 双路径并行。

#### 2.1.1 HTTP REST 端点（端口 8080）

| 路径 | 方法 | 功能 |
|---|---|---|
| `/` | GET | B/S 仪表盘（设备/任务双表 + 详情抽屉 + 5s 轮询） |
| `/assets/` | GET | 前端静态资源（E2 独立化） |
| `/healthz` | GET | K8s liveness 探针（P1-C2 深度检查：store ping） |
| `/readyz` | GET | K8s readiness 探针（依赖 store/redis 就绪 + IsLeader） |
| `/metrics` | GET | Prometheus 文本指标（独立端口 9091） |
| `/install.sh` | GET | agent bootstrap 脚本（curl ... \| sh -s -- --token=） |
| `/bin/opsmesh-agent` | GET | agent 二进制下载 |
| `/api/v1/devices` | GET | 设备清单（按网段分组） |
| `/api/v1/devices/{id}` | GET | 设备详情 |
| `/api/v1/devices/{id}` | DELETE | 设备退役 |
| `/api/v1/devices/{id}/provision` | POST | B1 纳管：签发 install token + bootstrap |
| `/api/v1/provision/auto` | POST | 自动纳管：按网段批量签发 |
| `/api/v1/agents` | GET | agent 清单 |
| `/api/v1/me` | GET | 当前身份（X-Tenant-ID / X-User-Id / X-User-Roles） |
| `/api/v1/tasks` | GET / POST | 任务列表 / 下发任务 |
| `/api/v1/tasks/batch` | POST | 批量下发 |
| `/api/v1/tasks/{id}/cancel` | POST | 取消任务 |
| `/api/v1/tasks/{id}/result` | GET | 查询结果 |
| `/api/v1/audits` | GET | 审计检索（?tenant=&action=&from=&to=&limit=） |
| `/api/v1/alerts` | GET | 活跃告警 |
| `/api/v1/alerts/{id}/ack` | POST | 告警确认 |
| `/api/v1/alerts/{id}/silence` | POST | 告警静默 |
| `/api/v1/alert-rules` | GET / POST | 告警规则 CRUD |
| `/api/v1/alert-rules/{id}` | DELETE | 删除告警规则 |
| `/api/v1/events/stream` | GET | **SSE 实时推送**（task_status / alert_new / device_online / device_offline / hello） |
| `/api/v1/auth/register` | POST | 用户注册（--public-register / --allow-public-register） |
| `/api/v1/auth/login` | POST | 登录（HS256 JWT + AT/RT 双 Cookie） |
| `/api/v1/auth/me` | GET | 当前用户 |
| `/api/v1/auth/logout` | POST | 登出（吊销 RT） |
| `/api/v1/auth/refresh` | POST | 刷新 AT（凭 RT，旋转） |
| `/api/v1/auth/change-password` | POST | 改密（预置弱口令强制） |
| `/api/v1/users` | GET / POST | 用户 CRUD |
| `/api/v1/users/{id}` |Bash| 用户详情/修改/删除 |
| `/api/v1/roles` | GET / POST | 角色 CRUD |
| `/api/v1/roles/{id}` |Bash| 角色详情/修改/删除 |
| `/api/v1/permissions` | GET / POST | 权限 CRUD |
| `/api/v1/os-templates` | GET / POST | OS 优化模板 |
| `/api/v1/os-templates/{id}` |Bash| 模板详情 |
| `/api/v1/os-templates/{id}/execute` | POST | 在指定 agent 执行 |
| `/api/v1/middleware-templates` | GET / POST | 中间件模板 |
| `/api/v1/middleware-templates/{id}` |Bash| 模板详情 |
| `/api/v1/middleware-templates/{id}/deploy` | POST | 在 agent 部署 |
| `/api/v1/middleware-instances` | GET / POST | 已部署实例 |
| `/api/v1/middleware-instances/{id}/uninstall` | POST | 卸载 |
| `/api/v1/k8s/clusters` | GET / POST | K8s 集群管理 |
| `/api/v1/k8s/clusters/{id}` |Bash| 集群详情/删除 |
| `/api/v1/k8s/clusters/{id}/test` | POST | 测试连接 |
| `/api/v1/deploys` | GET / POST | 部署中心（计划/fan-out/Reconcile/Rollback） |
| `/api/v1/deploys/*` |Bash| 部署详情 |
| `/api/v1/workflows` | GET / POST | 作业编排（DAG） |
| `/api/v1/workflows/*` |Bash| 工作流详情/触发/状态 |
| `/api/v1/cmdb/*` |Bash| CMDB（模型/实例 CRUD + 采集） |
| `/api/v1/logs` | GET / POST | 日志检索（memory/sql/loki/es） |
| `/api/v1/federation/peers` | GET | 联邦 peer 列表 + 在线状态 |
| `/api/v1/federation/forward/task` | POST | 跨网段转发任务 |
| `/api/v1/federation/devices` | GET | 联邦设备视图 |

#### 2.1.2 gRPC 端点（端口 9090）

| 服务 | 方法 | 请求 | 响应 | 说明 |
|---|---|---|---|---|
| `opsmesh.v1.Registration` | `Register` | `AgentInfo` | `RegisterResp` | agent 注册（携带 InstallToken 自动纳管） |
| | `Heartbeat` | `HeartbeatReq` | `Empty` | 心跳 + CMDB 增量上报 |
| | `PullTasks` | `PullTasksReq` | `PullTasksResp` | 原子领取 pending 任务 |
| | `ReportResult` | `TaskResult` | `Empty` | 上报执行结果 |
| | `CancelTask` | `CancelTaskReq` | `Empty` | 取消任务（租户隔离） |
| | `PollCancels` | `PollCancelsReq` | `PollCancelsResp` | 轮询取消信号 |

- **protobuf IDL**：`proto/opsmesh/v1/registration.proto`（M3-3A 引入，与手写 ServiceDesc 同名同方法集，buf breaking FILE 策略守护）
- **codec**：手写 JSON codec（默认）↔ protobuf codec（灰度切换）
- **TLS/mTLS**：`--tls-cert` + `--tls-key` + `--client-ca`

#### 2.1.3 SSE 事件流（端口 8080）

| 端点 | 协议 | 事件类型 | 信封格式 |
|---|---|---|---|
| `GET /api/v1/events/stream` | `text/event-stream` | `task_status` / `alert_new` / `device_online` / `device_offline` / `hello` | `event: <type>\ndata: {"type":"...","tenantID":"...","data":{...}}\n\n` |

- 慢消费者策略：buffered chan(16)，缓冲满丢弃
- 保活：每 15s 发 `: ping\n\n` 注释帧
- 租户隔离：requireAuth 时缺失身份 → 401

#### 2.1.4 端点数量统计

- HTTP REST 端点：**约 55** 个（含子路径通配）
- gRPC 方法：**6** 个
- SSE 端点：**1** 个
- **总计：62 个对外端点**

### 2.2 端口配置

| Flag | 默认值 | 环境变量 | 说明 |
|---|---|---|---|
| `--http-port` | **8080** | `OPSMESH_HTTP_PORT` | 控制面 HTTP(B/S) 端口 |
| `--grpc-port` | **9090** | `OPSMESH_GRPC_PORT` | gRPC 端口（注册/心跳/拉任务/上报/取消） |
| `--metrics-port` | **9091** | `OPSMESH_METRICS_PORT` | Prometheus metrics 端口 |
| `--federation-port` | 0 | `OPSMESH_FEDERATION_PORT` | 联邦独立 mTLS 监听端口（>0 启用） |
| `--replicas` | 1 | `OPSMESH_REPLICAS` | 控制面副本数（>1 须 mysql） |
| `--addr` | 127.0.0.1 | `OPSMESH_ADDR` | agent 自身地址 |
| `--control-addr` | http://127.0.0.1:8080 | `OPSMESH_CONTROL_ADDR` | 控制面 HTTP 地址 |
| `--control-addrs` | "" | `OPSMESH_CONTROL_ADDRS` | 多控制面地址（逗号分隔，HA failover） |

- 共 **79 个 flag**，全部支持"命令行 flag 优先、环境变量兜底"（前缀 `OPSMESH_`）
- 完整定义见 `internal/config/config.go`

### 2.3 鉴权机制

| 机制 | 实现 | 配置 | 说明 |
|---|---|---|---|
| **网关身份头注入** | `internal/authctx/` | `X-Tenant-ID` / `X-User-Id` / `X-User-Roles` | Sidecar 模式（APISIX/Envoy 前置校验） |
| **--require-auth** | `server.go` | `--require-auth` / `OPSMESH_REQUIRE_AUTH` | 缺失 X-Tenant-ID 拒绝（401）；--production 默认 true |
| **--production** | `config.go` | `--production` | 默认开启 require-auth + cookie-secure + grpc-require-signature，store=memory 强告警，jwt-secret≥32 字节 |
| **gRPC TLS/mTLS** | `internal/tlsutil/` | `--tls-cert` + `--tls-key` + `--client-ca` | gRPC 通道加密 + 双向认证 |
| **JWT HS256** | `internal/authctx/` `ParseHSJWT` | `--jwt-secret` / `OPSMESH_JWT_SECRET` | 用户中心签发密钥；多副本须一致；空=随机（重启失效）；生产 <32 字节 fail-fast |
| **JWT RS256 验签** | `internal/authctx/` | `--jwt-public-key` + `--jwt-issuer` | 网关剥离 + 内核二次校验（纵深防御） |
| **gRPC HMAC 签名** | `internal/controlplane/grpc.go` `verifyAgentSignature` | `--grpc-signature-key` + `--grpc-require-signature` | agent 身份绑定（PullTasks/ReportResult/PollCancels/Heartbeat 携带 HMAC-SHA256） |
| **Install Token** | `internal/provision/` | `--provision-secret` | B1 自动纳管：一次性 token（HMAC-SHA256 签名，15 分钟有效），ConsumeToken 校验 |
| **Cookie Secure** | `server.go` | `--cookie-secure` / `OPSMESH_COOKIE_SECURE` | AT/RT HttpOnly Cookie 的 Secure 标志 |
| **Trust Proxy** | `server.go` | `--trust-proxy` / `OPSMESH_TRUST_PROXY` | XFF 信任（默认 false 防 XFF 伪造绕过限流） |
| **Metrics CIDR** | `server.go` `metricsAllowed` | `--metrics-allow-cidr` | /metrics 访问控制（CIDR 白名单） |
| **kubeconfig 加密** | `internal/k8s/` | `--encryption-key` | AES-256-GCM（32 字节 hex/base64） |
| **Agent Shell 白名单** | `internal/agent/agent.go` `checkShellWhitelist` | `--agent-shell-whitelist` | 命令前缀白名单（ls,cat,echo,ping,systemctl,docker,kubectl） |
| **Agent File Root 白名单** | `internal/agent/agent.go` `checkFileRootWhitelist` | `--agent-file-root-whitelist` | 文件任务根目录白名单 + 路径遍历/符号链接拒绝 |
| **注册策略** | `server.go` `handleAuthRegister` | `--public-register` + `--allow-public-register` | true=开放注册须审批 / false=仅管理员创建 / 免审批=立即签发 |
| **联邦 mTLS + HMAC** | `internal/controlplane/federation.go` `signFederationRequest` | `--federation-secret` + `--federation-tls-cert/key/ca` | 跨不可信网段签名身份头 + mTLS |
| **Session Store** | `internal/authctx/` | `--session-store` | memory（单副本）/ redis://host:port（多副本 HA） |
| **Device FP Deadline** | `internal/authctx/` | `--device-fp-deadline` | 设备指纹强制非空截止（防裸注册） |
| **Rate Limit** | `server.go` `rateLimitMiddleware` |Bash| API 限流（429 Too Many Requests） |
| **CSRF Origin** | `server.go` `csrfOriginCheck` |Bash| 状态变更方法 CSRF Origin 校验 |
| **Security Headers** | `server.go` `securityHeadersMiddleware` |Bash| H5 安全头 + B1 CSP nonce |
| **Recovery** | `server.go` `recoveryMiddleware` |Bash| panic 兜底盘 |

### 2.4 事件总线 / 消息

| 机制 | 实现 | 说明 |
|---|---|---|
| **可插拔 Bus** | `internal/events/events.go` `Bus` interface | `NoopBus`（默认）/ `LogBus`（结构化日志）/ `KafkaBus`（//go:build kafka 编译标签） |
| **Kafka** | `internal/events/kafka.go` + `kafka_wal.go` + `kafka_stub.go` | 编译标签 `kafka` 启用，WAL 持久化 + 重连 |
| **Event Schema** | `internal/events/events.go` `Event` | `SchemaVersion = "1.0.0"`，信封 {tenantID, userID, action, target, detail, level, version} |
| **stampingBus** | `events.go` | 发布前强制加盖契约版本（跨版本演进锚点） |
| **SSE 推送** | `internal/controlplane/sse.go` | `publishEvent` 非阻塞广播，buffered chan(16)，慢消费者丢帧 |
| **Audit Log** | `internal/store/sql_audits.go` + `internal/controlplane/server_audits.go` | 100% 留痕（AuditEvent → audit_log / memory ring） |
| **Alert Webhook** | `internal/notify/` | generic / feishu / dingtalk / slack / 企业微信 / SMTP 邮件 |
| **Log Collect** | `internal/agent/agent.go` `logCollectLoop` + `internal/logstore/` | agent → loki / es 直推 |

- **Event Level**：`LevelInfo` / `LevelWarn` / `LevelAlert`
- **Action 类型**：register / create_task / report_result / alert / cancel / ...

### 2.5 多租户隔离

| 能力 | 实现 | 说明 |
|---|---|---|
| **TenantID 行级隔离** | `internal/store/` 所有表带 `tenant_id` 列 | 查询自动过滤，越权 403/404 |
| **--multi-schema** | `internal/store/multi_schema.go` `SchemaNamer` | 每租户独立 MySQL schema（prefix + tenantID） |
| **--schema-prefix** | `multi_schema.go` `DefaultSchemaNamer` | 默认 `opsmesh_tenant_`，白名单校验 `[a-zA-Z0-9_]` |
| **require-auth** | `server.go` | 缺失 X-Tenant-ID 拒绝（401） |
| **索引** | `sql.go` | `idx_tasks_tenant_created`、`idx_audit_tenant_created`（tenant_id + created_at DESC） |
| **联邦租户** | `federation.go` | 跨网段转发保留身份头 + HMAC 签名防伪造 |

- **存储后端**：memory（默认，零依赖，单实例）/ mysql（U-04 数据本地化，多副本）
- **Redis**：agent/device 状态缓存（`--redis-addr`）

### 2.6 健康检查 / 可观测性

| 端点 / 机制 | 实现 | 说明 |
|---|---|---|
| `GET /healthz` | `server.go` `handleHealthz` | K8s liveness（P1-C2 深度：store ping，2s 超时） |
| `GET /readyz` | `server.go` `handleReadyz` | K8s readiness（store + IsLeader 检查） |
| `GET /metrics` | `server.go` `buildMetrics`（独立端口 9091） | Prometheus 文本（agent 数/队列深度/duration/HTTP 计数+延迟/runtime） |
| **OTel** | `internal/otelx/` | `--otel-endpoint`（OTLP gRPC）/ `--otel-service-name` / `--otel-stdout` |
| **HTTP Middleware** | `otelx.HTTPMiddleware` | 自动 span + W3C Trace Context 提取/注入 |
| **gRPC Interceptor** | `otelx.GRPCServerUnaryInterceptor` + `GRPCClientUnaryInterceptor` | agent→控制面 trace_id 贯穿 |
| **Metrics Collector** | `internal/metrics/metrics.go` `M` | Counter/Gauge/Histogram + defBuckets（0.005-10s） |
| **HTTP 指标** | `httpMetricsMiddleware` | 计数 + 延迟直方图（method/path/status） |
| **Runtime 指标** | `metrics.go` `appendRuntimeMetrics` | Go runtime（goroutine/mem/GC） |
| **--health 子命令** | `cmd/opsmesh/main.go` `runHealth` | distroless 无 curl，二进制内置探活 localhost:{httpPort}/healthz |
| **Prometheus Rule** | `deploy/helm/opsmesh/templates/prometheusrule.yaml` | Helm Chart 自带告警规则 |
| **ServiceMonitor** | `deploy/helm/opsmesh/templates/servicemonitor.yaml` | Prometheus Operator 集成 |

### 2.7 部署方式

| 方式 | 命令 / 文件 | 说明 |
|---|---|---|
| **go build** | `go build -o opsmesh ./cmd/opsmesh` | 单二进制 |
| **控制面启动** | `./opsmesh --mode=controlplane` | HTTP 8080 + gRPC 9090 + metrics 9091 |
| **agent 启动** | `./opsmesh --mode=agent --segment=seg-a --control-addr=http://127.0.0.1:8080` | 注册到控制面 |
| **演示模式** | `--demo` | 每个 agent 注册预置 uname -a 示例任务 |
| **Dockerfile** | `Dockerfile` | 控制面多阶段（golang:1.26-bookworm → distroless/static-debian12，nonroot） |
| **Dockerfile.agent** | `Dockerfile.agent` | agent 多阶段（base-debian12 含 sh，执行 shell/service 任务） |
| **docker-compose.yaml** | `docker compose up -d` | controlplane + agent + mysql + redis 一键 |
| **Helm Chart** | `deploy/helm/opsmesh/` | 17 个模板：controlplane-deployment / agent-daemonset / mysql-statefulset / redis-statefulset / ingress / hpa / pdb / servicemonitor / prometheusrule / secret / configmap / serviceaccount + values.yaml + values-production.yaml |
| **Helm 安装** | `helm install opsmesh ./deploy/helm/opsmesh -n opsmesh --create-namespace` | 开发：单副本 + memory |
| **Helm 生产** | `helm install opsmesh ./deploy/helm/opsmesh -f deploy/helm/opsmesh/values-production.yaml --set controlplane.provisionSecret=$(openssl rand -hex 32)` | 3 副本 + mysql + TLS + require-auth |
| **systemd** | `deploy/systemd/` | systemd unit + env 文件 |
| **start.bat** | `start.bat` | Windows 启动 |
| **Makefile** | `Makefile` | 构建/测试 |
| **GitHub Actions** | `.github/workflows/` | lint/test/security/image CI |
| **backup 子命令** | `opsmesh backup --output <file> [--format json|sql] [--include-config] [--include-audits]` | 离线备份 |
| **restore 子命令** | `opsmesh restore --input <file> [--dry-run] [--overwrite]` | 灾备恢复 |
| **--version** | `opsmesh --version` | 版本信息 |
| **--health** | `opsmesh --health` | 探活子命令 |

- **HA**：多副本 leader 选举（`leader_lease` 表，`--leader-ttl-sec` / `--leader-tick-sec`），仅 leader 执行 reclaimLoop/scheduleLoop/archiveLoop
- **Agent failover**：`--control-addrs="cp1:9090,cp2:9090"` 依次重连

### 2.8 Agent 机制 / 能力节点注册

| 机制 | 实现 | 说明 |
|---|---|---|
| **--mode=agent** | `internal/agent/agent.go` `Agent.Run` | 单二进制 agent 模式 |
| **gRPC 通道** | `internal/agent/grpcclient.go` | Register / Heartbeat / PullTasks / ReportResult / PollCancels |
| **--segment** | `agent.go` | 网段分桶键（U-02） |
| **--install-token** | `agent.go` `installToken` | B1 自动纳管（一次性 token） |
| **--control-addrs** | `agent.go` `setupDiscoveryBalancer` | 多控制面 failover |
| **任务类型** | `agent.go` `execute` | shell / service / file（含 timeout、retry、dead_letter、cancel） |
| **Shell 执行** | `agent.go` `executeShell` + `exec_unix.go` / `exec_other.go` | exec.CommandContext + shell metachar 检查 + 白名单 |
| **Service 管理** | `agent.go` `execService` | systemctl start/stop/restart/status |
| **File 分发** | `agent.go` `execFile` | 原子写入 + rename + 根目录白名单 |
| **失败重试** | `store.go` | `max_retries`（默认 3），耗尽进死信 + critical 告警 |
| **任务取消** | `agent.go` `cancelLoop` | 每 2s PollCancels，命中 cancel context → exec.CommandContext 中止 |
| **定时调度** | `controlplane/server.go` `scheduleLoop` | 5 字段 cron（`internal/cron/`），派生 pending 实例 |
| **CMDB 上报** | `agent.go` `collectCmdbReport` + `collectCmdbServices` + `collectCmdbMiddleware` + `collectCmdbNetwork` | Heartbeat 增量上报（machine/os/service/network） |
| **Device Metrics** | `agent.go` `collectDeviceMetrics` + `metrics_collect.go` | 设备指标采集 |
| **Log Collect** | `agent.go` `logCollectLoop` + `readLogIncrement` | 日志增量采集 → loki/es |
| **HMAC 签名** | `agent.go` `computeAgentSignature` | gRPC 请求携带 HMAC-SHA256（timestamp + agentID） |
| **Rlimit** | `agent.go` `applyRlimits` + `rlimit_unix.go` / `rlimit_other.go` | 资源限制 |
| **Agent ID 持久化** | `agent.go` `loadOrCreateAgentID` | `--data-dir` 落盘 agent.id，重启沿用 |
| **联邦** | `internal/controlplane/federation.go` `FederationManager` | 跨网段任务转发 + 联邦设备视图 + mTLS + HMAC |
| **Operator** | `operator/` | K8s Operator（CRD） |

- **任务生命周期**：pending → running → done / failed → dead_letter；cancelled（pending 拦截 / running 强杀）
- **HA 协调**：ClaimTask 原子领取（pending→running），reclaimLoop 回收超期任务

---

## 3. Interaction（Agent 工作台）

- 仓库路径：`F:\Nexus\Interaction`
- 技术栈：原生 HTML/CSS/JS（单文件 33193 行） + Electron + PWA + Web Crypto API
- 架构：单文件 `agent-workbench.html`（UI + 逻辑 + 数据） + Electron 封装（可选）
- 定位：Windows 上的套壳 Agent 工作台，4 场景 subagent（办公/编程/学习/生活），纯本地零安装

### 3.1 对外 API 端点

Interaction 无后端服务器，是纯前端单文件应用。所有"端点"分三类：本地静态服务、Electron IPC、外部 API 调用。

#### 3.1.1 本地静态服务

| 端点 | 协议 | 端口 | 功能 |
|---|---|---|---|
| `http://127.0.0.1:{port}/agent-workbench.html` | HTTP | 8123（候选：8123 8134 8145 8156 8167 8178 8189 8200） | `python -m http.server {port} --bind 127.0.0.1`（仅静态文件，避开 CORS） |
| `file:///.../agent-workbench.html` | file:// | — | Edge/Chrome --app 模式直接打开 |

- 仅绑定 127.0.0.1，局域网无法访问

#### 3.1.2 Electron IPC 端点（`electronAPI.*`）

| 页面调用 | preload 暴露 | 主进程句柄 | 功能 |
|---|---|---|---|
| `electronAPI.platform` | `contextBridge` 静态值 | — | 平台标识 |
| `electronAPI.version()` | `ipcRenderer.invoke("get-version")` | `ipcMain.handle("get-version")` | 应用版本 |
| `electronAPI.isPackaged()` | `ipcRenderer.invoke("get-packaged")` | `ipcMain.handle("get-packaged")` | 是否打包态 |
| `electronAPI.getAutoLaunch()` | `ipcRenderer.invoke("get-auto-launch")` | `ipcMain.handle("get-auto-launch")` | 开机自启状态 |
| `electronAPI.setAutoLaunch(on)` | `ipcRenderer.send("set-auto-launch")` | `ipcMain.on("set-auto-launch")` | 设置开机自启 |
| `electronAPI.getAiConfig()` | `ipcRenderer.invoke("get-ai-config")` | `ipcMain.handle("get-ai-config")` | 读取 AI 配置（Key 不进渲染进程） |
| `electronAPI.setAiConfig(cfg)` | `ipcRenderer.invoke("set-ai-config")` | `ipcMain.handle("set-ai-config")` | 保存 AI 配置（safeStorage 加密） |
| `electronAPI.chat(arg)` | `ipcRenderer.invoke("chat")` | `ipcMain.handle("chat")` | AI 代理请求（主进程 fetch，规避 CORS + Key 暴露） |

- 安全基线：`contextIsolation: true` + `nodeIntegration: false` + `sandbox: true`
- 单实例锁：`app.requestSingleInstanceLock()`
- AppUserModelId：`com.agent.workbench`（Windows 任务栏分组）

#### 3.1.3 外部 API 调用（fetch）

| 目标 | 端点 | 方法 | 触发场景 |
|---|---|---|---|
| **LLM API**（OpenAI 兼容） | `{base}/chat/completions` | POST | AI 对话（DeepSeek/通义/豆包/Ollama 等） |
| **Notion** | `https://api.notion.com/v1/users/me`、`/v1/pages/{id}`、`/v1/pages` | GET/POST/PATCH | 集成同步 |
| **Linear** | `https://api.linear.app/graphql` | POST | GraphQL 集成 |
| **Jira** | `{domain}/rest/api/3/myself`、`/issue/{id}`、`/issue`、`/search?jql=` | GET/POST | Jira 集成 |
| **Slack** | `https://slack.com/api/auth.test`、`/api/chat.postMessage` | POST | Slack 集成 |
| **飞书** | `https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal`、`/open-apis/im/v1/messages` | POST | 飞书集成 |
| **钉钉** | `https://oapi.dingtalk.com/gettoken`、`/chat/send` | GET/POST | 钉钉集成 |
| **Google Calendar** | `{endpoints.base}/{eventsPath}/{eventId}` | GET/POST/PUT/DELETE | 日历同步 |
| **Outlook** | `{endpoints.base}/{eventsPath}/{eventId}` | GET/POST/PUT/DELETE | 日历同步 |
| **Webhook**（用户配置） | `{hook.url}` | POST | 自动化工作流触发 |

#### 3.1.4 端点数量统计

- 本地静态服务：**1** 个（python http.server）
- Electron IPC：**8** 个
- 外部 API 调用：**约 20+** 个（LLM + 8 个 SaaS 集成 + webhook）
- **总计：约 29 个对外交互点**（无自托管后端 API）

### 3.2 端口配置

| 配置 | 默认值 | 来源 | 说明 |
|---|---|---|---|
| 本地服务端口 | **8123** | `启动本地服务.bat` | 候选列表 `8123 8134 8145 8156 8167 8178 8189 8200`，顺延找第一个空闲 |
| 绑定地址 | **127.0.0.1** | `启动本地服务.bat` | 仅回环，杜绝 LAN 暴露 |
| Electron 窗口 | 1200×800（min 800×600） | `electron/main.js` | 桌面窗口 |

- 无独立后端端口（纯前端 + Electron 主进程 IPC）

### 3.3 鉴权机制

| 机制 | 实现 | 说明 |
|---|---|---|
| **无账号体系** |Bash| 单人本地应用，无登录/SSO/用户管理 |
| **AI Key 加密（浏览器）** | `agent-workbench.html` `encryptKey` / `decryptKey` | AES-GCM + 设备密钥（Web Crypto API `crypto.subtle`），密钥与密文同存 localStorage（**混淆级防护**，防随手翻看不防本机恶意进程） |
| **AI Key 加密（Electron）** | `electron/main.js` `saveAiConfig` / `loadAiConfig` | `safeStorage`（Windows DPAPI，操作系统托管，真实保护），Key 不进渲染进程 |
| **旧版迁移** | `main.js` `legacyDecrypt` | 机器绑定派生 AES-256-GCM 密钥（hostname + username），一次性迁移到 safeStorage |
| **Base URL 校验** | `agent-workbench.html` `validateBaseUrl` | 只允许 `https://`（允许 `http://localhost` 供开发），非法直接报错 |
| **Electron 安全基线** | `main.js` `webPreferences` | `contextIsolation: true` + `nodeIntegration: false` + `sandbox: true` |
| **单实例锁** | `main.js` `requestSingleInstanceLock` | 防重复打开 |
| **CSP** | `agent-workbench.html` meta | Content-Security-Policy |
| **内置 RBAC** | `agent-workbench.html` `ROLES = {ADMIN, EDITOR, VIEWER}` | 单用户本地应用不需要（注释明确说明） |
| **Zero Trust 框架** | `agent-workbench.html` `ztCheckPermission` | 模拟 ZT 检查（role-based policy） |
| **自定义角色** | `agent-workbench.html` `ENTERPRISE_ROLES` + `_customRoles` | admin/manager/member/viewer（多工作区场景） |
| **SSO 框架** | `agent-workbench.html` `sso*` | SAML/OAuth2/OIDC 模拟（不实际连接 IdP） |
| **Token 生成** | `agent-workbench.html` `generateToken` | 本地 token（payload + HMAC 签名 + TTL） |
| **密钥版本管理** | `agent-workbench.html` `_keyVersions` | 密钥轮换（version + salt + algorithm） |
| **GDPR 日志** | `agent-workbench.html` `_gdprLogs` | GDPR 处理日志 |
| **Webhook 签名** | `agent-workbench.html` `webhookSign` / `webhookVerifySignature` | HMAC-SHA256 签名 + 时间戳防重放 |

### 3.4 事件总线 / 消息

| 机制 | 实现 | 说明 |
|---|---|---|
| **localStorage 事件** | `agent-workbench.html` 数据层 | `wb_agent_*` 前缀键值存储 + storage 事件跨 tab 同步 |
| **IndexedDB** | `wb_agent_idb`（kv 镜像） + `wb_agent_idb_data`（V2 数据） + `wb_sync_queue`（离线队列） | 大容量结构化存储 |
| **场景联动（习惯链）** | `DEFAULT_LINKS` + `wb_custom_links` | office(交付)→study(看视频) / study(复习)→code(写项目) / code(上线)→life(犒劳) |
| **Webhook Bus** | `64-webhook-bus.js`（内联） `webhookEmit` | 订阅/发布/重试/DLQ/签名，持久化 `wb_webhook_bus_subs/dlq/history` |
| **自动化工作流** | `49-automation.js`（内联） | 规则引擎 + cron 定时任务 + 多步骤工作流 + webhook 集成 |
| **Background Sync API** | `service-worker.js` `sync` 事件 | `sync-tasks` 标签，离线排队操作 |
| **Web Push** | `service-worker.js` `push` 事件 + `pushsubscriptionchange` | 推送通知 |
| **WebSocket 框架** | `agent-workbench.html` 实时协作模块 | **模拟**（不实际连接服务器），CRDT + OT + presence |
| **实时协作** | `agent-workbench.html` `createCollabSession` / `joinSession` / `leaveSession` | 看板分享 + 权限管理 + 任务评论（模拟） |
| **Cron 定时** | `_cronJobs` + `_cronTimer` | 浏览器内定时任务 |
| **Toast 通知** | `agent-workbench.html` | UI 通知 |
| **命令面板** | Ctrl/Cmd+K | 全局命令 |

- **数据层**：localStorage（主） + IndexedDB（镜像 + 大数据） + 自动备份（`wb_agent_autobackup`，30 条顶部提示）

### 3.5 多租户隔离

| 能力 | 实现 | 说明 |
|---|---|---|
| **无账号体系** |Bash| 单人本地应用，无多租户 |
| **工作区（Workspace）** | `agent-workbench.html` `listWorkspaces` / `addWorkspaceMember` / `removeWorkspaceMember` | 多工作区隔离（模拟，本地数据） |
| **工作区角色** | `getWorkspaceUserRole` / `assignRole` / `checkRole` | admin/manager/member/viewer |
| **数据访问控制** | `checkDataAccess` | 工作区成员 × 角色 × 数据类型 × 操作 |
| **看板分享** | `generateBoardShareLink` / `shareBoard` | 生成分享链接 + 角色权限 |
| **自定义角色** | `_customRoles` + `CUSTOM_ROLES_KEY` | 工作区级自定义角色 |

- **本质**：单用户本地应用，多租户/工作区为模拟框架（为未来云端版预留）

### 3.6 健康检查 / 可观测性

| 机制 | 实现 | 说明 |
|---|---|---|
| **PWA Service Worker** | `service-worker.js` | 分层缓存（cache-first / stale-while-revalidate / network-first） + Background Sync + Web Push + IndexedDB 离线队列 |
| **PWA Manifest** | `manifest.json` | 可安装、离线可用、shortcuts（看板/统计/设置） |
| **滚动日志（Electron）** | `electron/main.js` `logLine` | `userData/logs/app.log`，JSON Lines，1MB 自动截断保留 512KB |
| **诊断日志** | `agent-workbench.html` `pushDiag` | 前端诊断（error/warn/info） |
| **自动备份** | `agent-workbench.html` `autoBackup` | `wb_agent_autobackup`，累计 30 条顶部提示 |
| **自动更新（Electron）** | `electron/main.js` `electron-updater` | 仅打包态，`autoDownload = false`，用户手动决定 |
| **无 Prometheus / metrics** |Bash| 纯前端应用无指标暴露 |
| **无 /health 端点** |Bash| 本地静态服务无健康检查 |

### 3.7 部署方式

| 方式 | 命令 / 文件 | 说明 |
|---|---|---|
| **Edge 应用模式** | `启动Agent工作台.bat` | `msedge --app="file:///..."`，零安装最常用 |
| **本地服务模式** | `启动本地服务.bat` | `python -m http.server 8123 --bind 127.0.0.1` + `msedge --app="http://127.0.0.1:8123/..."`，启用 AI 时避开 CORS |
| **Electron 便携包** | `cd electron && npm install && npm run dist` | `electron-builder` 打包 Windows 便携版 exe → `electron/dist/*.exe` |
| **Electron 开发** | `cd electron && npm start` | 开发预览 |
| **PWA 线上** | https://levango7.github.io/Interaction/ | GitHub Pages，可安装到桌面/手机 |
| **Vercel** | `vercel.json` | 部署配置（service-worker no-cache + manifest Content-Type） |
| **构建** | `npm run build` | `scripts/build.mjs`（HTML 处理） |
| **测试** | `npm test` / `npm run e2e` | vitest（单元） + playwright（E2E） |
| **Lint** | `npm run lint` | eslint + lint-colors |
| **TypeCheck** | `npm run typecheck` | tsc + jsconfig.json |

- **单一交付物**：`agent-workbench.html`（33193 行，HTML/CSS 全内联 + 原生 JS + 内联 SVG）
- **三种形态共用同一份 HTML**，不会版本漂移
- **prebuild**：自动把根目录 HTML 复制进 `electron/`

### 3.8 Agent 机制 / 能力节点注册

| 机制 | 实现 | 说明 |
|---|---|---|
| **4 场景 subagent** | `SCENARIOS = {office, code, study, life}` | 办公/编程/学习/生活，左侧导航切换 |
| **AI 工具调用** | `TOOLS` 数组（17 个 function-calling 工具） | OpenAI 兼容 function-calling |
| **create_task** | `TOOLS[0]` | 在指定场景创建任务 |
| **list_tasks** | `TOOLS[1]` | 查询场景任务（按状态过滤） |
| **complete_task** | `TOOLS[2]` | 按id/标题标记完成 |
| **update_task** | `TOOLS[3]` | 修改状态/优先级/截止/标签 |
| **delete_task** | `TOOLS[4]` | 删除（进回收站可恢复） |
| **add_record** | `TOOLS[5]` | 向资料库添加记录 |
| **search** | `TOOLS[6]` | 全局搜索任务与资料库 |
| **query_overview** | `TOOLS[7]` | 各场景统计 + 今日/逾期待处理 |
| **export_data** | `TOOLS[8]` | 导出 JSON 备份 |
| **remember / recall / forget** | `TOOLS[9-11]` | 工作记忆（60 条环形截断，场景隔离，近期+命中加权召回） |
| **plan / complete_step / complete_goal** | `TOOLS[12-14]` | 多步目标编排（对话循环上限 6→12 轮） |
| **list_records** | `TOOLS[15]` | 跨场景资料库查询 |
| **工作记忆** | `recallMemories` + `rememberMemory` + `forgetMemory` | 用户偏好/决定沉淀，自动注入上下文 |
| **多步目标编排** | `plan` + `complete_step` + `complete_goal` | 单目标聚焦，新目标自动顶替 |
| **跨场景协调** | `list_records` 跨场景 | 目标步骤可跨场景调用既有工具 |
| **subagent 面板** | 每场景一个 AI 助手面板 | 独立 sysprompt + 独立会话 |
| **场景联动** | `DEFAULT_LINKS` + 自定义链 | 任务完成触发跨场景奖励/后续任务（习惯链） |
| **插件系统** | `registerPlugin` + `BUILTIN_PLUGINS` + `registerPluginFromJson` | 插件注册/load/start/stop + JSON 导入 |
| **自定义场景** | `CUSTOM_SC_KEY` + `SC_OVERRIDE_KEY` | 用户增删场景 + 内置场景改名/换色 |
| **命令面板** | Ctrl/Cmd+K | 全局命令（场景切换/新建任务/设置/...） |
| **快捷键** | 1-4 切场景 / G 总览 / N 新建 / Ctrl+K 命令面板 | |
| **周报生成器** | 办公/编程场景 | 自动汇总本周已完成任务 |
| **SM-2 间隔复习** | 学习场景 | 遗忘曲线驱动复习计划 |
| **健康记录** | 生活场景 | 运动记录/体重追踪/睡眠记录/喝水提醒 |
| **数据总览** | 近 14 天趋势 + 月日历热力图 + 各场景进度条 | |
| **集成** | Notion / Linear / Jira / Slack / 飞书 / 钉钉 / Google Calendar / Outlook | 8 个 SaaS 集成（同步/搜索/创建/通知） |
| **自动化工作流** | 规则引擎 + cron + 多步骤工作流 + webhook | 49-automation.js |
| **Webhook Bus** | 订阅/发布/重试/DLQ/签名 | 64-webhook-bus.js |
| **实时协作** | WebSocket 框架 + CRDT + OT + presence | 模拟（不实际连接服务器） |
| **推荐系统** | `hybridRecommend` = `collaborativeFilter` + `contentBasedRecommend` | 协同过滤 + 内容推荐 |
| **智能调度** | `smartSchedule` + `energyMatch` + `scoreTask` | 能量曲线 + 任务评分 |
| **多 AI Profile** | 多供应商配置（OpenAI/Anthropic/Ollama/DeepSeek/通义/豆包） | 切换/新建/删除/复制 |

- **场景定义**（`SCENARIOS`）：name + color + icon + sysprompt + extraCard + record.fields
- **AI 调用路径**：Electron 模式经主进程 IPC（`electronAPI.chat`）→ 浏览器/Edge 直连 `fetch(base+"/chat/completions")`

---

## 4. 三项目对比汇总

### 4.1 API 端点数量

| 项目 | HTTP REST | gRPC | WebSocket | SSE | IPC | 外部 API | 总计 |
|---|---|---|---|---|---|---|---|
| **MAOP** | 343 | 0 | 1（/ws） | 5（/api/stream/*，原语保留） | 0 | 0 | **347** |
| **OpsMesh** | ~55 | 6 | 0 | 1（/api/v1/events/stream） | 0 | 0 | **62** |
| **Interaction** | 1（静态） | 0 | 0（模拟） | 0 | 8（Electron） | ~20+ | **~29** |

### 4.2 关键端口

| 项目 | 主端口 | gRPC | Metrics | 其他 |
|---|---|---|---|---|
| **MAOP** | 9079（FastAPI） | — | /api/prometheus（同端口） | Redis 6379 / PG 5432 / Vault 8200 / OTel 4317 / n8n 5678（可选 profile） |
| **OpsMesh** | 8080（HTTP B/S） | 9090 | 9091（Prometheus） | 联邦 mTLS 可配 / MySQL 3306 / Redis 6379 |
| **Interaction** | 8123（python http.server，仅静态） | — | — | Electron 主进程 IPC（无端口） |

### 4.3 鉴权方式

| 项目 | 主要机制 | 企业级能力 |
|---|---|---|
| **MAOP** | JWT(HS256) + RBAC + TLS + CSP + RateLimit + Edition Gate + License(Ed25519) + CRL | SSO(OIDC/SAML) + LDAP + Vault + 多租户 + 审计 |
| **OpsMesh** | 网关身份头注入 + JWT(HS256+RS256) + gRPC mTLS + HMAC 签名 + Install Token + Cookie Secure | --production fail-fast + 多租户(行级+Schema) + 联邦 mTLS + 审计 100% |
| **Interaction** | AI Key 加密（浏览器 AES-GCM 混淆级 / Electron safeStorage DPAPI 操作系统级） + Base URL 校验 + Electron 安全基线 | 内置 RBAC/SSO/Zero Trust 框架（模拟，单用户不需要） |

### 4.4 事件机制

| 项目 | 内部总线 | 外部消息 | 实时推送 |
|---|---|---|---|
| **MAOP** | Async EventBus（ACK/retry/dead-letter/priority） + 可选 RabbitMQ(pika) + Redis Streams | WebSocket（/ws，15s snapshot） + SSE 原语 | WebSocket |
| **OpsMesh** | 可插拔 Bus（noop/log/kafka） + Audit Log + Alert Webhook | SSE（/api/v1/events/stream，task_status/alert_new/device_online/device_offline） | SSE |
| **Interaction** | localStorage 事件 + IndexedDB + Webhook Bus（订阅/发布/重试/DLQ/签名） + 自动化工作流（规则+cron+工作流） | Background Sync + Web Push + 外部 SaaS 集成（Notion/Linear/Jira/Slack/飞书/钉钉/Calendar） | WebSocket 模拟（不实际连接） |

### 4.5 Agent 机制

| 项目 | Agent 形态 | 路由 / 注册 | 协议 |
|---|---|---|---|
| **MAOP** | CLI agent（agents.yaml 声明式） + Subagent + Distributed Worker | Dispatcher（capability match + regex scoring） + Dynamic Router + Agent Registry | CLI + MCP Hub（Stdio/WebSocket） + A2A（JSON-RPC 2.0，/a2a） + Plan-Execute-Verify |
| **OpsMesh** | 单二进制 --mode=agent + gRPC 通道 | 控制面统一调度（ClaimTask 原子领取） + 网段分桶（--segment） + HA failover（--control-addrs） | gRPC（Register/Heartbeat/PullTasks/ReportResult/CancelTask/PollCancels） + HMAC 签名 |
| **Interaction** | 4 场景 subagent（office/code/study/life） + AI function-calling | 场景切换 + 工作记忆召回 + 多步目标编排 | OpenAI 兼容 function-calling（17 个工具） + Electron IPC + SaaS 集成 |

### 4.6 部署方式

| 项目 | 构建产物 | 容器化 | K8s |
|---|---|---|---|
| **MAOP** | pip 包（maop / maop-enterprise） + Vue dist | docker-compose.yml + prod.yml + 7 个 profile | 通过 docker-compose 部署（无原生 Helm） |
| **OpsMesh** | 单 Go 二进制（controlplane + agent 同一份） | Dockerfile（distroless） + Dockerfile.agent（debian） + docker-compose.yaml | **Helm Chart**（17 模板，含 Deployment/DaemonSet/StatefulSet/Ingress/HPA/PDB/ServiceMonitor） + systemd + Operator（CRD） |
| **Interaction** | 单 HTML 文件 + Electron exe | 无 | 无（PWA + GitHub Pages + Vercel） |

### 4.7 多租户隔离

| 项目 | 隔离方式 | 配额 | 审计 |
|---|---|---|---|
| **MAOP** | tenant_id 行级过滤 + RLS + Schema（PG，Enterprise） | tokens/requests/agents/models 多资源配额 | AuditLogger（append-only） + Compliance（GDPR） |
| **OpsMesh** | TenantID 行锁 + --multi-schema（MySQL，每租户独立 schema） | — | AuditEvent → audit_log / memory ring，100% 留痕，可查（tenant/action/time/limit） |
| **Interaction** | 无（单人） + 工作区模拟 | — | GDPR 处理日志（模拟） |

### 4.8 健康检查 / 可观测性

| 项目 | 健康端点 | Metrics | Tracing | 日志 |
|---|---|---|---|---|
| **MAOP** | /api/health | /api/prometheus + /api/observability/metrics | OTel（OTLP/console） | JSON 结构化（MAOP_JSON_LOG） + Log Rotate |
| **OpsMesh** | /healthz + /readyz | /metrics（端口 9091，Prometheus 文本） | OTel（OTLP gRPC/stdout） + W3C Trace Context（HTTP+gRPC） | 结构化日志 + Log Collect（loki/es） |
| **Interaction** | 无 | 无 | 无 | Electron 滚动日志（JSON Lines，1MB 截断） + 前端诊断 |

---

## 5. 统一编排平台设计建议

基于本次盘点的关键发现：

1. **协议统一**：MAOP（HTTP REST + WebSocket + A2A JSON-RPC）+ OpsMesh（HTTP REST + gRPC + SSE）+ Interaction（HTTP + Electron IPC）。建议编排平台采用 **HTTP REST + WebSocket/SSE** 作为统一对外协议，gRPC 保留为 OpsMesh agent 通道，A2A 作为 MAOP 跨系统 agent 通信标准。

2. **鉴权统一**：三项目均有 JWT，但算法不同（MAOP HS256、OpsMesh HS256+RS256、Interaction 无）。建议统一为 **JWT RS256 + 网关身份头注入**（OpsMesh 模式），MAOP 的 RBAC/SSO/License 与 OpsMesh 的 Install Token/HMAC 签名作为补充。

3. **事件总线统一**：MAOP（Async EventBus + 可选 RabbitMQ）+ OpsMesh（noop/log/kafka）+ Interaction（Webhook Bus）。建议统一为 **可插拔 Bus**（OpsMesh 模式），支持 noop/log/kafka/rabbitmq/redis，SSE/WebSocket 作为实时推送。

4. **多租户统一**：MAOP（tenant_id + RLS + Schema）+ OpsMesh（TenantID 行锁 + Schema）已具备成熟模型，Interaction 无。建议采用 **tenant_id 行级隔离 + 可选 Schema 隔离**（OpsMesh 模式）。

5. **Agent 机制统一**：MAOP（agents.yaml + Dispatcher + A2A + MCP）+ OpsMesh（gRPC 通道 + 网段分桶）+ Interaction（function-calling + 场景 subagent）。建议采用 **A2A 作为跨系统 agent 通信标准**（MAOP 已实现），MCP 作为工具调用标准（MAOP 已实现 MCP Hub），OpsMesh 的 gRPC 通道保留为基础设施层 agent 通信。

6. **部署统一**：建议采用 **Helm Chart**（OpsMesh 已有完整 17 模板）作为 K8s 部署标准，MAOP 补充 Helm Chart（当前仅 docker-compose），Interaction 保持 PWA + Electron 单文件形态。

7. **可观测性统一**：建议采用 **Prometheus + OTel**（MAOP + OpsMesh 均已实现），统一 /metrics + /healthz 端点，OTLP gRPC 导出。

---

**盘点完成。报告基于实际代码与配置文件只读扫描，未修改任何源码。**