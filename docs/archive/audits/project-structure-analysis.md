# MAOP 项目结构分析

> 用途：为后续 6 个企业版功能的 PRD 编写提供基础信息。本文档基于对现有代码库的静态研究，覆盖后端 API 模式、数据模型、认证体系、多租户架构、前端路由/组件/API 调用/i18n 结构。
>
> 生成时间：2026-08-13 · 研究范围：`py/maop/` 后端 + `dashboard-enterprise/` 前端

---

## 目录

1. [后端 API 模式](#1-后端-api-模式)
2. [数据模型和数据库](#2-数据模型和数据库)
3. [认证和权限体系](#3-认证和权限体系)
4. [配置和环境变量](#4-配置和环境变量)
5. [前端路由和页面结构](#5-前端路由和页面结构)
6. [前端 API 调用模式](#6-前端-api-调用模式)
7. [状态管理](#7-状态管理)
8. [i18n 结构](#8-i18n-结构)
9. [组件库](#9-组件库)
10. [对 6 个新功能的建议](#10-对-6-个新功能的建议)

---

## 1. 后端 API 模式

### 1.1 框架与入口

| 项 | 值 |
|---|---|
| Web 框架 | FastAPI（异步、ASGI） |
| 入口文件 | `py/maop/dashboard/server.py` |
| 启动命令 | `python -m maop.dashboard.server` |
| 默认端口 | 9079（`MAOP_DASH_PORT`） |
| API 文档 | `/api/docs`（生产环境关闭）、`/api/redoc` |
| 健康检查 | `/api/health` |
| Prometheus 指标 | `/api/prometheus` |
| WebSocket | `/ws`（实时推送 snapshot） |
| SPA fallback | `/{full_path:path}` → 返回 `index.html` |

### 1.2 路由注册方式

路由文件位于 `py/maop/dashboard/routers/`，共 41 个文件（含子目录）。`server.py` 通过 `app.include_router(...)` 显式注册每个路由器。

**注册分两类**：

1. **通用路由**（personal + enterprise 都启用）：`data`、`control`、`model`、`evolve`、`memory`、`system`、`auth`、`subagent`、`worktree`、`protocol`、`hook`、`stream`、`permission`、`mcp`、`session`、`react`、`plugin`、`cost`、`agents`、`chat`、`knowledge`、`info`、`budget`、`tool_audit`、`agent_proxy`、`routing_preview`、`routing`、`evolution`、`observability`、`audit`（统一版）。

2. **企业版专属路由**（gated by `FeatureFlag`）：
   - `tenant` — `has_feature(FeatureFlag.MULTI_USER)` 守卫
   - `rbac` — `has_feature(FeatureFlag.MULTI_USER)` 守卫
   - `sso` — `has_feature(FeatureFlag.MULTI_USER)` 守卫
   - `n8n` — `has_feature(FeatureFlag.N8N_INTEGRATION)` 守卫

**企业版路由注册模板**（`server.py:454-498`）：

```python
if has_feature(FeatureFlag.MULTI_USER):
    try:
        from maop.dashboard.routers import tenant as tenant_router
        app.include_router(tenant_router.router)
        has_tenant_router = True
    except ImportError as _e:
        logger.warning("[server] Enterprise router MISSING: tenant ...", _e)
```

### 1.3 API 版本化

- 默认路径：`/api/<resource>/<action>`（无版本前缀）
- 别名路径：`/api/v1/<resource>/<action>`（通过 `_register_v1_aliases()` 自动注册）
- 豁免版本化的路径：`/api/health`、`/api/stream`、`/api/auth/login`、`/api/auth/logout`、`/api/auth/refresh`

### 1.4 路由前缀模式

两种风格并存：

| 风格 | 示例 | 说明 |
|---|---|---|
| 显式 prefix | `APIRouter(prefix="/api/agents", tags=["agents"])` | 新模块推荐此风格（agents、tenant、rbac、sso、audit、n8n） |
| 路径硬编码 | `@router.get("/api/report")` | 旧模块（data、control）使用此风格 |

**新功能建议**：使用 `APIRouter(prefix="/api/<resource>", tags=["<resource>"])` 显式前缀风格。

### 1.5 请求/响应模型（Pydantic）

请求体使用 Pydantic `BaseModel`，字段使用 `Field` 添加约束：

```python
class RegisterAgentRequest(BaseModel):
    name: str = Field(max_length=100)
    cli_path: str = Field(default="", max_length=500)
    capabilities: list[str] = Field(default_factory=list)
    timeout_s: int = Field(default=120, ge=1, le=3600)
```

响应体约定：

| 场景 | 响应形状 |
|---|---|
| 成功（单对象） | `{"status": "ok", "<key>": <value>}` |
| 成功（列表） | `{"status": "ok", "<key>s": [...], "count": N}` |
| 错误 | `ErrorSchema`：`{"status": "error", "error": "...", "code": "...", "detail": "...", "request_id": "..."}` |

### 1.6 认证/权限装饰器

| 装饰器/守卫 | 来源 | 用途 |
|---|---|---|
| `require_admin(request)` | `maop.core.security.middleware` | 抛 `HTTPException(403)` 若非 admin/superadmin |
| `@handle_api_errors` | `maop.dashboard.error_handler` | 统一异常捕获，返回 `ErrorSchema` |
| `has_feature(FeatureFlag.XXX)` | `maop.config.edition` | 企业版特性开关，Personal 版返回 404 |
| `require_feature(FeatureFlag.XXX)` | `maop.config.edition` | 同上但抛 `FeatureNotAvailable` 异常 |

**典型端点装饰器栈**：

```python
@router.post("/grant")
@handle_api_errors
async def grant_role(body: GrantRequest, request: Request) -> dict[str, Any]:
    require_admin(request)
    if not has_feature(FeatureFlag.RBAC):
        raise HTTPException(status_code=404, detail="RBAC not available in this edition")
    ...
```

### 1.7 错误处理模式

**三层错误处理**：

1. **端点级**：`@handle_api_errors` 装饰器捕获所有异常，返回统一 `ErrorSchema`。
2. **全局级**：`server.py:256` 注册 `@app.exception_handler(Exception)` 兜底，返回 `{"status": "error", "error": "Internal server error"}`。
3. **企业版守卫级**：`enterprise_api_guard` 中间件（`server.py:531`）在 Personal 版对 `/api/tenant`、`/api/sso`、`/api/rbac`、`/api/n8n` 路径返回 404 或软降级响应。

**`ErrorSchema` 定义**（`error_handler.py:39`）：

```python
class ErrorSchema(BaseModel):
    status: str = "error"
    error: str = ""
    code: str = ""
    detail: str = ""
    request_id: str = ""
```

### 1.8 分页模式

当前代码库**无统一分页模式**。各端点使用 `Query` 参数自行约定：

```python
async def list_events(
    request: Request,
    tenant_id: str = "",
    action: str = "",
    severity: str = "",
    hours: int = 24,
    limit: int = 100,   # ← 简单 limit
    offset: int = 0,    # ← 简单 offset
)
```

**新功能建议**：统一使用 `limit` + `offset` 参数，响应中返回 `total` 字段，便于前端 DataTable 渲染分页器。

### 1.9 多租户上下文传递

- **请求级**：`request.state.tenant_id`（由 AuthMiddleware 从 JWT claim 注入）
- **数据过滤**：`data.py:36` 的 `_tenant_filter()` 递归过滤掉 `tenant_id` 不匹配的条目
- **RBAC 守卫**：`rbac.py:67` 的 `_tenant_id_from_jwt()` 强制从 JWT 取 tenant_id，**禁止从请求体取**（G-07 安全修复，防跨租户提权）

### 1.10 典型路由文件清单

| 文件 | prefix | 主要端点 | 守卫 |
|---|---|---|---|
| `auth.py` | `/api/auth` | login/logout/refresh/register/users CRUD | `require_admin`（写操作） |
| `agents.py` | `/api/agents` | list/register/unregister/diagnose/repair/upgrade | `require_admin`（写操作） |
| `data.py` | `/api` | report/agents/timeseries/metrics/live/graph/vector | `require_admin` + tenant 过滤 |
| `tenant.py` | `/api/tenant` | list/create/get/suspend/activate/delete/usage | `require_admin` + `FeatureFlag.TENANT_ISOLATION` |
| `rbac.py` | `/api/rbac` | grants/grant/revoke/roles/permissions | `require_admin`（写） + `FeatureFlag.RBAC` |
| `audit.py` | `/api/audit` | events/summary/filter | `require_admin` + edition 分支（enterprise/personal） |
| `sso.py` | `/api/sso` | authorize/callback/logout/validate/config | `FeatureFlag.SSO` |
| `n8n.py` | `/api/n8n` | webhook/workflows/trigger/executions/health | `require_admin` + `FeatureFlag.N8N_INTEGRATION` |

---

## 2. 数据模型和数据库

### 2.1 数据库后端

| 后端 | 配置 | 用途 |
|---|---|---|
| SQLite（默认） | `MAOP_DB_BACKEND=sqlite` | Personal 版、开发环境 |
| PostgreSQL | `MAOP_DB_BACKEND=postgresql` + `MAOP_DATABASE_URL` | Enterprise 版、生产环境 |

**统一 DB 路由**（`db_utils.py:203`）：
- 默认所有模块共享 `data/maop.db`（unified mode），通过表名前缀隔离
- `MAOP_DB_PER_MODULE=1` 切换为每模块独立 `.db` 文件（legacy mode）
- SQLAlchemy engine 工厂：`get_db_engine()`（`db_utils.py:284`），支持连接池

**SQLite 连接约定**（`sqlite_connect`）：
- WAL journal mode
- `PRAGMA foreign_keys=ON`
- `PRAGMA busy_timeout=10000`（可配置 `MAOP_SQLITE_BUSY_TIMEOUT_MS`）
- 默认 row_factory = `sqlite3.Row`

### 2.2 现有 Schema 概览

完整 schema 见 `docs/database-schema.md`（53 张表，32 个源文件）。关键表分组：

| 分组 | 表 | 来源 |
|---|---|---|
| 认证 | `api_keys`、`users` | `core/auth.py`、`dashboard/routers/auth.py` |
| 核心数据 | `delegations`、`metrics`、`checkpoints`、`circuit_breaker`、`error_log` | `core/data.py` |
| 会话 | `sessions` | `core/session.py` |
| 熔断 | `circuit_breaker_state`、`failover_chains`、`breaker_events` | `core/circuit_breaker.py` |
| 时序 | `ts_raw`、`ts_5min`、`ts_1hour` | `core/timeseries.py` |
| KV | `kv_store` | `core/kv_store.py` |
| 向量 | `vector_entries` | `core/vector.py` |
| 工作树 | `worktrees` | `core/worktree.py` |
| 工具 | `tools` | `core/tool_manager.py` |
| 子智能体 | `subagents`、`agent_messages` | `core/subagent.py` |
| 沙箱 | `sandboxes` | `core/sandbox.py` |
| 协议 | `protocols`、`protocol_messages` | `core/protocol.py` |
| 插件 | `plugins` | `core/plugin.py` |
| 权限 | `permission_rules` | `core/permission.py` |
| 消息队列 | `queue_messages`、`queue_dead_letters`、`queue_idempotent` | `core/message_queue.py` |
| MCP | `mcp_servers` | `core/mcp_registry.py` |
| 知识抽取 | `facts`、`entities`、`relations` | `core/knowledge_extractor.py` |
| 图像 | `images` | `core/image_store.py` |
| 人工代理 | `approval_requests` | `core/human_proxy.py` |
| Hook | `hooks`、`hook_logs` | `core/hook_manager.py` |
| 成本 | `cost_entries` | `core/cost_tracker.py` |
| 对话 | `messages` | `core/conversation.py` |
| 变更追踪 | `snapshots`、`file_states`、`change_log` | `core/change_tracker.py` |
| 制品 | `artifacts`、`artifact_versions` | `core/artifact_store.py` |
| Agent 扫描 | `scanned_agents` | `core/agent_scanner.py` |
| Agent 注册 | `registered_agents`、`health_log` | `core/agent_registry.py` |
| 迁移 | `_migrations` | `core/migration.py` |
| 记忆 | `memory_entries`、`memory_traces`、`memory_trajectory`、`memory_fts` | `memory/models.py` |
| Prompt | `prompt_templates`、`prompt_versions` | `prompt_manager.py` |

### 2.3 企业版专属表（PostgreSQL）

定义在 `py/maop/enterprise/pg_persist.py`，自动建表（`CREATE TABLE IF NOT EXISTS`）：

#### tenants

```sql
CREATE TABLE IF NOT EXISTS tenants (
    tenant_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    status TEXT DEFAULT 'trial',     -- active/suspended/trial/terminated
    plan TEXT DEFAULT 'starter',     -- starter/pro/enterprise
    quota JSONB DEFAULT '{}',
    created_at DOUBLE PRECISION DEFAULT 0,
    updated_at DOUBLE PRECISION DEFAULT 0,
    expires_at DOUBLE PRECISION DEFAULT NULL,
    metadata JSONB DEFAULT '{}'
);
```

#### tenant_usage

```sql
CREATE TABLE IF NOT EXISTS tenant_usage (
    tenant_id TEXT PRIMARY KEY,
    api_calls_today INTEGER DEFAULT 0,
    storage_mb REAL DEFAULT 0,
    active_agents INTEGER DEFAULT 0,
    concurrent_tasks INTEGER DEFAULT 0,
    active_users INTEGER DEFAULT 0,
    FOREIGN KEY (tenant_id) REFERENCES tenants(tenant_id) ON DELETE CASCADE
);
```

#### rbac_grants

```sql
CREATE TABLE IF NOT EXISTS rbac_grants (
    id SERIAL PRIMARY KEY,
    user_id TEXT NOT NULL,
    role TEXT NOT NULL,             -- superadmin/admin/operator/viewer
    tenant_id TEXT DEFAULT '',
    granted_by TEXT DEFAULT '',
    granted_at DOUBLE PRECISION DEFAULT 0,
    expires_at DOUBLE PRECISION DEFAULT NULL,
    UNIQUE(user_id, role, tenant_id)
);
-- 索引: idx_rbac_user(user_id), idx_rbac_tenant(tenant_id)
```

#### audit_events

```sql
CREATE TABLE IF NOT EXISTS audit_events (
    event_id TEXT PRIMARY KEY,
    timestamp DOUBLE PRECISION NOT NULL,
    action TEXT NOT NULL,
    severity TEXT DEFAULT 'info',   -- info/warning/critical
    actor TEXT DEFAULT '',
    tenant_id TEXT DEFAULT '',
    resource TEXT DEFAULT '',
    detail TEXT DEFAULT '',
    result TEXT DEFAULT 'success',
    ip_address TEXT DEFAULT '',
    user_agent TEXT DEFAULT '',
    metadata JSONB DEFAULT '{}'
);
-- 索引: idx_audit_timestamp, idx_audit_actor, idx_audit_tenant, idx_audit_action
```

### 2.4 多租户架构

| 维度 | 实现方式 |
|---|---|
| 租户模型 | `Tenant`（Pydantic）：tenant_id、name、status、plan、quota、metadata |
| 配额模型 | `TenantQuota`：max_api_calls_per_day、max_storage_mb、max_agents、max_concurrent_tasks、max_users |
| 用量模型 | `TenantUsage`：api_calls_today、storage_mb、active_agents、concurrent_tasks、active_users |
| 状态枚举 | `TenantStatus`：ACTIVE / SUSPENDED / TRIAL / TERMINATED |
| 隔离方式 | 行级隔离（每行带 `tenant_id` 字段）+ 应用层过滤（`_tenant_filter`） |
| 上下文传递 | JWT claim → `request.state.tenant_id` → 路由层读取 |
| 持久化 | `TenantManager`（内存 + 可选 `PgTenantStore`）；PG 不可用时降级为内存 |
| 配额检查 | `TenantManager.check_quota(tenant_id, resource, current)` |

### 2.5 用户/认证模型

#### users 表（SQLite，`auth.py:105`）

```sql
CREATE TABLE IF NOT EXISTS users (
    username TEXT PRIMARY KEY,
    password_hash TEXT NOT NULL,    -- pbkdf2_sha256$<iter>$<salt_b64>$<digest_b64>
    roles TEXT NOT NULL DEFAULT '["admin"]',  -- JSON array
    created_at REAL NOT NULL,
    enabled INTEGER NOT NULL DEFAULT 1
);
```

- 密码哈希：PBKDF2-HMAC-SHA256，600,000 次迭代（OWASP 2023 推荐）
- 默认 admin 用户：首次启动自动创建，密码来自 `MAOP_ADMIN_PASSWORD` 或随机生成（生产环境必须显式设置）
- 角色：JSON 数组，如 `["admin","read","write","execute"]`

#### api_keys 表（SQLite，`core/security/auth.py:83`）

```sql
CREATE TABLE IF NOT EXISTS api_keys (
    key_hash TEXT PRIMARY KEY,      -- SHA256(plaintext)
    name TEXT NOT NULL,
    roles TEXT NOT NULL DEFAULT '[]',
    created_at REAL NOT NULL,
    expires_at REAL,
    enabled INTEGER NOT NULL DEFAULT 1,
    rate_limit INTEGER NOT NULL DEFAULT 0
);
```

---

## 3. 认证和权限体系

### 3.1 认证流程

**登录流程**（`auth.py:285` `/api/auth/login`）：

1. 接收 `{username, password}`
2. IP + username 双维度限流（5 次/15 分钟锁定）
3. SQLite 查 `users` 表，PBKDF2 验证密码
4. 生成 JWT（`AuthManager.jwt_handler.create_token`，TTL=7200s）
5. 返回 `{token, username, roles, expires_in}` + 设置 httpOnly cookie `maop_token`

**Token 刷新**（`/api/auth/refresh`）：验证旧 token → 颁发新 token → 撤销旧 token

**登出**（`/api/auth/logout`）：服务端撤销 JWT token

### 3.2 JWT 配置

| 项 | 值 |
|---|---|
| 算法 | HS256 |
| 密钥 | `MAOP_JWT_SECRET` 或自动生成并持久化到 `data/.jwt_secret` |
| 默认 TTL | 7200s（2 小时） |
| 签发者 | `MAOP` |
| claim | `identity`（username）、`roles`（数组）、`exp`、`iat` |
| 撤销 | 服务端维护撤销列表（`JWTHandler.revoke_token`） |

### 3.3 中间件栈

`server.py` 注册顺序（执行顺序为逆序）：

| 顺序 | 中间件 | 作用 |
|---|---|---|
| 1 | `CSPMiddleware` | Content-Security-Policy + 安全响应头 |
| 2 | `AuthMiddleware` | JWT/API Key 认证，注入 `request.state.auth_identity` / `auth_roles` |
| 3 | `RateLimitMiddleware` | 每 IP 令牌桶限流（默认 30 rps，burst 60） |
| 4 | `CORSMiddleware` | CORS（生产环境显式配置 origins） |
| 5 | `enterprise_api_guard` | Personal 版对企业 API 路径返回 404/软降级 |

**公开路径**（无需认证）：

```python
public_paths = [
    "/", "/api/health", "/api/prometheus",
    "/style.css", "/favicon.svg",
    "/api/docs", "/openapi.json",
    "/api/auth/status", "/api/auth/login",
    "/api/csp-report",
    "/api/stream",  # SSE token 在 handler 内单独验证
]
```

### 3.4 RBAC 体系

**角色层级**（`enterprise/rbac.py:25`）：

```
superadmin > admin > operator > viewer
```

**权限模型**（`resource:action` 格式）：

| 权限 | 说明 |
|---|---|
| `agents:read` / `agents:write` / `agents:execute` | 智能体管理 |
| `config:read` / `config:write` | 配置管理 |
| `memory:read` / `memory:write` | 记忆访问 |
| `models:read` / `models:write` | 模型管理 |
| `cost:read` | 成本查看 |
| `tenant:read` / `tenant:write` / `tenant:admin` | 租户管理 |
| `audit:read` | 审计查看 |
| `rbac:read` / `rbac:write` | RBAC 管理 |
| `system:admin` | 系统管理 |

**角色-权限映射**：

| 角色 | 权限集 |
|---|---|
| superadmin | 全部权限 |
| admin | 除 `tenant:admin`、`system:admin` 外全部 |
| operator | agents 读写执行 + config 读 + memory 读写 + models 读 + cost 读 |
| viewer | 只读：agents/config/memory/models/cost 读 |

**授权记录**（`RoleGrant`）：user_id + role + tenant_id + granted_by + granted_at + expires_at（支持临时授权）

### 3.5 Edition 区分机制

**核心文件**：`py/maop/config/edition.py`

**Edition 枚举**：`PERSONAL` | `ENTERPRISE`

**FeatureFlag 枚举**（25 个）：

| 分类 | Flag | Personal | Enterprise |
|---|---|---|---|
| 核心能力 | `COST_TRACKING`、`CIRCUIT_BREAKER`、`MEMORY_STORE`、`HOT_RELOAD`、`HOOKS`、`PLUGIN_SYSTEM`、`MCP_HUB`、`VECTOR_SEARCH`、`REACT_LOOP`、`BUDGET_GUARD` | ✅ | ✅ |
| 企业能力 | `RBAC`、`AUDIT_LOG`、`MULTI_USER`、`SSO`、`DASHBOARD_ANALYTICS`、`VUE_DASHBOARD`、`POSTGRESQL`、`REDIS`、`VAULT`、`TENANT_ISOLATION`、`TLS_AUTO`、`AUTH_AUTO`、`N8N_INTEGRATION` | ❌ | ✅ |
| 可选后端 | `RABBITMQ`、`ETCD` | 按需 | 按需 |

**检测流程**（`detect_edition()`）：

1. `MAOP_EDITION` 环境变量（`enterprise`/`personal`/`auto`）
2. `maop.enterprise` 包可导入 + license 校验通过 → ENTERPRISE
3. 否则 → PERSONAL

**License 校验**（`_detect_with_license_check`）：
- `MAOP_LICENSE_KEY` 环境变量或 `data/license.key` 文件
- 校验失败 → 降级为 PERSONAL + 记录 degradation
- 模块完整性校验（防篡改）：`verify_module_integrity()`

**运行时守卫**：

```python
# 路由层
if not has_feature(FeatureFlag.RBAC):
    raise HTTPException(404, "RBAC not available in this edition")

# 业务层
require_feature(FeatureFlag.TENANT_ISOLATION)  # 抛 FeatureNotAvailable
```

### 3.6 SSO 集成

**支持协议**：OIDC（OpenID Connect via authlib）、SAML 2.0（SP-initiated + XML 签名验证）

**配置**（环境变量）：

| 变量 | 说明 |
|---|---|
| `MAOP_SSO_PROVIDER` | `oidc` 或 `saml` |
| `MAOP_SSO_CLIENT_ID` / `MAOP_SSO_CLIENT_SECRET` | OIDC 客户端凭据 |
| `MAOP_SSO_AUTHORIZE_URL` / `MAOP_SSO_TOKEN_URL` / `MAOP_SSO_USERINFO_URL` | OIDC 端点 |
| `MAOP_SSO_REDIRECT_URI` | 回调 URL |
| `MAOP_SSO_SCOPES` | OAuth scopes（默认 `openid profile email`） |

**端点**：`/api/sso/authorize`、`/api/sso/callback`、`/api/sso/logout`、`/api/sso/validate`、`/api/sso/config`

---

## 4. 配置和环境变量

### 4.1 配置加载优先级

`py/maop/config/settings.py` 的 `MAOPSettings`（Pydantic BaseSettings）：

1. 环境变量（`MAOP_` 前缀）
2. `.env` 文件（项目根目录）
3. `config/settings.yaml`（可选）
4. 代码默认值

### 4.2 关键环境变量分类

#### 核心配置

| 变量 | 默认值 | 说明 |
|---|---|---|
| `MAOP_ENV` | `production` | 环境标识（影响认证默认策略） |
| `MAOP_DASH_PORT` | 9079 | Dashboard 端口 |
| `MAOP_DASH_HOST` | `0.0.0.0` | 监听地址 |
| `MAOP_DASH_WORKERS` | 1 | Uvicorn worker 数 |
| `MAOP_ROOT` | `/app` | 项目根目录 |
| `MAOP_DATA_DIR` | `<root>/data` | 数据目录 |
| `MAOP_EDITION` | `auto` | `personal` / `enterprise` / `auto` |

#### 安全/认证

| 变量 | 默认 | 说明 |
|---|---|---|
| `MAOP_AUTH_ENABLED`（或 `MAOP_AUTH`） | secure-by-default | 认证开关 |
| `MAOP_JWT_SECRET` | `change-me` | JWT 签名密钥 |
| `MAOP_ADMIN_PASSWORD` | 空 | 初始 admin 密码 |
| `MAOP_KEY` / `MAOP_KEY_FILE` | 空 | API Key Vault 主密钥（Fernet） |
| `MAOP_LICENSE_KEY` | 空 | 企业版 license |

#### TLS

| 变量 | 默认 | 说明 |
|---|---|---|
| `MAOP_TLS_ENABLED`（或 `MAOP_TLS`） | 0 | TLS 开关 |
| `MAOP_TLS_CERT` / `MAOP_TLS_KEY` | 空 | 证书/私钥路径 |
| `MAOP_TLS_MIN_VERSION` | `TLSv1_2` | 最低 TLS 版本 |

#### 数据库后端

| 变量 | 默认 | 说明 |
|---|---|---|
| `MAOP_DB_BACKEND` | `sqlite` | `sqlite` / `postgresql` |
| `MAOP_DATABASE_URL`（或 `MAOP_DB_URL`） | 空 | SQLAlchemy URL |
| `MAOP_DB_PER_MODULE` | 0 | 每模块独立 DB |
| `MAOP_SQLITE_BUSY_TIMEOUT_MS` | 10000 | SQLite busy timeout |

#### 企业版后端

| 变量 | 默认 | 说明 |
|---|---|---|
| `MAOP_STORAGE_BACKEND` | `sqlite` | `sqlite` / `postgresql` |
| `MAOP_CACHE_BACKEND` | `memory` | `memory` / `redis` |
| `MAOP_QUEUE_BACKEND` | `sqlite` | `sqlite` / `redis` / `rabbitmq` |
| `MAOP_KV_BACKEND` | `memory` | `memory` / `sqlite` / `etcd` |
| `MAOP_SECRET_BACKEND` | `local` | `local` / `vault` |

#### Redis/PostgreSQL/Vault 连接

| 变量 | 说明 |
|---|---|
| `MAOP_REDIS_HOST` / `MAOP_REDIS_PORT` / `MAOP_REDIS_PASSWORD` / `MAOP_REDIS_DB` | Redis 连接 |
| `MAOP_PG_HOST` / `MAOP_PG_PORT` / `MAOP_PG_DATABASE` / `MAOP_PG_USER` / `MAOP_PG_PASSWORD` | PG 连接 |
| `MAOP_VAULT_ADDR` / `MAOP_VAULT_TOKEN` / `MAOP_VAULT_MOUNT` / `MAOP_VAULT_PATH` | Vault 连接 |

#### 限流/CORS/CSP

| 变量 | 默认 | 说明 |
|---|---|---|
| `MAOP_RATE_LIMIT_ENABLED` | 1 | 限流开关 |
| `MAOP_RATE_LIMIT_RPS` | 30 | 每秒请求数 |
| `MAOP_RATE_LIMIT_BURST` | 60 | 突发上限 |
| `MAOP_TRUST_PROXY` | 0 | 信任 X-Forwarded-For |
| `MAOP_CORS_ORIGINS` | `http://localhost:9079,...` | CORS origins |
| `MAOP_CSP` / `MAOP_CSP_REPORT_ONLY` / `MAOP_CSP_REPORT_URI` | 1 / 0 / 空 | CSP 配置 |

#### SSO

| 变量 | 说明 |
|---|---|
| `MAOP_SSO_PROVIDER` / `MAOP_SSO_CLIENT_ID` / `MAOP_SSO_CLIENT_SECRET` | SSO 配置 |
| `MAOP_SSO_AUTHORIZE_URL` / `MAOP_SSO_TOKEN_URL` / `MAOP_SSO_USERINFO_URL` | OIDC 端点 |
| `MAOP_SSO_REDIRECT_URI` / `MAOP_SSO_SCOPES` | 回调与 scopes |

#### n8n

| 变量 | 说明 |
|---|---|
| `N8N_BASE_URL` / `N8N_USER` / `N8N_PASSWORD` | n8n 连接 |
| `N8N_API_KEY` / `N8N_WEBHOOK_SECRET` | n8n 鉴权 |

#### 监控/日志

| 变量 | 默认 | 说明 |
|---|---|---|
| `MAOP_OTEL_ENABLED` | 0 | OpenTelemetry 开关 |
| `MAOP_OTEL_ENDPOINT` | `http://otel-collector:4317` | OTel collector |
| `MAOP_METRICS_ENABLED` | 1 | Prometheus 指标开关 |
| `MAOP_JSON_LOG` | 1 | JSON 结构化日志 |

---

## 5. 前端路由和页面结构

### 5.1 技术栈

| 项 | 值 |
|---|---|
| 框架 | Vue 3.5（Composition API，`<script setup>`） |
| 路由 | vue-router 4.5 |
| 状态 | Pinia 3.0 |
| 构建 | Vite 8.1 |
| 测试 | Vitest 3.0 + Playwright 1.62 |
| 图表 | Chart.js 4.5 + vue-chartjs |
| 图谱 | vis-network 9.1 |
| 类型 | TypeScript 5.6（可选，`vue-tsc`） |
| Lint | ESLint 9 + Prettier 3 |

### 5.2 路由定义

文件：`src/router/index.js`

| 路径 | name | 组件 | 企业版守卫 |
|---|---|---|---|
| `/` | overview | Overview.vue | - |
| `/run` | run | Run.vue | - |
| `/control` | - | 重定向 → `/run?tab=structured` | - |
| `/chat` | - | 重定向 → `/run?tab=chat` | - |
| `/agents` | agents | Agents.vue | - |
| `/memory` | memory | ThreeLayerMemory.vue | - |
| `/evolve` | evolve | Evolve.vue | - |
| `/evolution-history` | - | 重定向 → `/evolve?tab=history` | - |
| `/search` | search | Search.vue | - |
| `/vector` | vector | VectorSearch.vue | - |
| `/models` | models | Models.vue | - |
| `/tools` | tools | Tools.vue | - |
| `/logs` | logs | Logs.vue | - |
| `/monitor` | monitor | Monitor.vue | - |
| `/observability` | observability | Observability.vue | - |
| `/cost` | cost | Cost.vue | - |
| `/audit` | audit | Audit.vue | `meta.requiresEnterprise: true` |
| `/rbac` | rbac | RBAC.vue | `meta.requiresEnterprise: true` |
| `/tenants` | tenants | Tenants.vue | `meta.requiresEnterprise: true` |
| `/settings` | settings | Settings.vue | - |
| `/users` | users | Users.vue | - |
| `/docs` | docs | Docs.vue | - |
| `/knowledge-graph` | knowledge-graph | KnowledgeGraph.vue | - |
| `/:pathMatch(.*)*` | - | 重定向 → `/` | - |

**企业版路由守卫**（`router/index.js:113`）：
- `meta.requiresEnterprise: true` 的路由在 Personal 版重定向到 `/`
- 守卫读取 `useEditionStore()` + localStorage 快照（冷加载安全失败为 `personal`）
- 异步从 `/api/info/config` hydrate 真实 edition

### 5.3 导航分组

文件：`src/nav.js`，6 个分组（RFC-001 迭代 A 信息架构）：

| 分组 | i18n key | 包含页面 |
|---|---|---|
| Workbench | `nav.workbench` | Overview、Monitor |
| Build | `nav.build` | Run、Agents、Evolve |
| Data Assets | `nav.assets` | Memory、KnowledgeGraph、Search、Vector、Tools |
| Observe | `nav.observe` | Observability、Logs、Cost |
| Govern | `nav.govern` | Models、Audit⚠️、RBAC⚠️、Tenants⚠️、Users⚠️ |
| System | `nav.system` | Settings、Docs |

⚠️ = `enterprise: true`，Personal 版通过 `filterNavByEdition()` 隐藏。

### 5.4 页面组件清单

`src/views/` 下 23 个 `.vue` 文件：

| 组件 | 用途 |
|---|---|
| Overview.vue | 平台概览仪表盘 |
| Run.vue | 任务运行 + Chat（合并 Control + Chat） |
| ControlPanel.vue | 旧控制面板（已被 Run 取代） |
| Chat.vue | 旧对话页（已重定向到 Run） |
| Agents.vue | 智能体管理 |
| ThreeLayerMemory.vue | 三层记忆 |
| Evolve.vue | 自演化 + 历史（合并） |
| EvolutionHistory.vue | 旧演化历史（已重定向） |
| Search.vue | 统一搜索 |
| VectorSearch.vue | 向量搜索 |
| Models.vue | 模型管理 |
| Tools.vue | 工具管理 |
| Logs.vue | 日志查看 |
| Monitor.vue | 实时监控 |
| Observability.vue | 可观测性 |
| Cost.vue | 成本分析 |
| Audit.vue | 审计日志（企业版） |
| RBAC.vue | 角色权限管理（企业版） |
| Tenants.vue | 租户管理（企业版） |
| Users.vue | 用户管理 |
| Settings.vue | 系统设置 |
| Docs.vue | 文档 |
| KnowledgeGraph.vue | 知识图谱 |

---

## 6. 前端 API 调用模式

### 6.1 API Store

文件：`src/stores/api.js`（Pinia store）

**核心方法**：

| 方法 | 签名 | 行为 |
|---|---|---|
| `get(url, opts)` | `→ Promise<json>` | GET 请求，自动注入 Bearer token |
| `post(url, body, opts)` | `→ Promise<json>` | POST 请求 |
| `put(url, body)` | `→ Promise<json>` | PUT 请求 |
| `delete(url)` | `→ Promise<json>` | DELETE 请求 |
| `authToken()` | `→ string` | 读取当前 token |
| `setAuthToken(token, user)` | `void` | 登录成功后设置 |
| `clearAuthToken()` | `async void` | 登出（通知后端撤销 + 清除本地） |

**关键特性**：

1. **认证注入**：`withAuth()` 自动添加 `Authorization: Bearer <token>` 头 + `credentials: 'include'`（发送 httpOnly cookie）
2. **超时控制**：`fetchWithTimeout()` 30s 超时（`AbortController`）
3. **401 自动刷新**：遇到 401 → 调用 `/api/auth/refresh` → 重试原请求 → 仍 401 则触发 `maop:unauthorized` 事件
4. **Token 持久化**：`localStorage.maop_token` + `localStorage.maop_user`
5. **错误抛出**：`!res.ok` 时抛 `Error(errBody.error || 'API <url>: <status>')`

### 6.2 调用示例（来自 Tenants.vue）

```javascript
const api = useApiStore();

// 列表
const d = await api.get('/api/tenant/list');
tenants.value = d.tenants || [];

// 创建
await api.post('/api/tenant/create', {
  tenant_id: newTenant.value.tenant_id,
  name: newTenant.value.name,
  plan: newTenant.value.plan,
});

// 删除
await api.delete(`/api/tenant/${id}`);
```

### 6.3 Composables

`src/composables/` 下 9 个 composable：

| 文件 | 用途 |
|---|---|
| `useWebSocket.js` | WebSocket 连接（自动重连、JWT via subprotocol） |
| `useStreamingFetch.js` | SSE 流式请求 |
| `useAgentTokenStream.js` | Agent token 流 |
| `useDagProgress.js` | DAG 进度追踪 |
| `useKnowledgeGraph.js` | 知识图谱数据 |
| `useToast.js` | 全局 Toast 通知 |
| `useModalA11y.js` | Modal 无障碍 |
| `chartOptions.js` | Chart.js 配置 |
| `chartTokens.js` | 图表设计 token |

### 6.4 WebSocket

- URL：`ws(s)://<host>:<port>/ws`
- 认证：JWT 通过 `Sec-WebSocket-Protocol` subprotocol 传递（避免 URL/access-log 泄露）
- 重连：最多 10 次，间隔 3s
- 关闭码 4401 = 认证失败，触发 `maop:unauthorized` 事件

---

## 7. 状态管理

### 7.1 Store 清单

`src/stores/` 下 4 个 Pinia store：

| Store | 文件 | 职责 |
|---|---|---|
| `useApiStore` | `api.js` | HTTP 请求封装、认证 token 管理 |
| `useEditionStore` | `edition.js` | Edition 状态（personal/enterprise）、feature flags、后端信息 |
| `useRealtimeStore` | `realtime.js` | WebSocket 连接、实时 snapshot |
| `useUiStore` | `ui.js` | UI 偏好（theme、density、rail、locale） |

### 7.2 useEditionStore 详解

```javascript
state: {
  edition: 'personal' | 'enterprise',  // 冷加载从 localStorage 读取
  features: {},      // { rbac: true, audit_log: true, ... }
  backends: {},      // { storage: 'postgresql', cache: 'redis', ... }
  degradations: [],  // 后端降级记录
  loading: false,
  switching: false,
  switchError: '',
}
actions: {
  fetchEdition(),          // GET /api/info/edition
  switchEdition(target),   // POST /api/info/edition
}
getters: {
  isEnterprise, isPersonal, hasFeature(name), hasDegradations
}
```

### 7.3 useUiStore 详解

```javascript
state: {
  theme: 'light' | 'dark',       // localStorage.maop_theme
  density: 'comfortable' | 'compact',  // localStorage.maop_density
  rail: boolean,                 // localStorage.maop_rail (侧栏折叠)
  locale: 'en' | 'zh',           // localStorage.maop_locale
}
// 副作用：setTheme/setDensity/setLocale 会写 <html data-theme/data-density/data-lang>
```

---

## 8. i18n 结构

### 8.1 架构

文件：`src/i18n/index.js`

- **零依赖**：自实现轻量 i18n，无 vue-i18n
- **双语**：`en`（源） + `zh`（翻译），未翻译 key 回退到 `en`
- **响应式**：`useI18n()` 返回的 `t()` 随 `useUiStore().locale` 响应更新
- **参数插值**：`t('users.welcome', { name: 'Alice' })` → `"Welcome, Alice"`

### 8.2 字典组织

```
src/i18n/
├── index.js              # coreMessages + 自动收集 view-*.js
├── view-agents.js        # Agents.vue 专属 key
├── view-audit.js         # Audit.vue 专属 key
├── view-chat.js
├── view-control.js
├── view-cost.js
├── view-evolution-history.js
├── view-evolve.js
├── view-knowledge-graph.js
├── view-logs.js
├── view-models.js
├── view-monitor.js
├── view-overview.js
├── view-rbac.js
├── view-search.js
├── view-settings.js
├── view-tenants.js
├── view-tlmemory.js
├── view-tools.js
└── view-vector.js
```

**自动收集**（`index.js:468`）：

```javascript
const viewModules = import.meta.glob('./view-*.js', { eager: true });
// 合并所有 view-*.js 的 messages.en / messages.zh
```

### 8.3 命名模式

| 前缀 | 用途 | 示例 |
|---|---|---|
| `nav.*` | 导航标签与副标题 | `nav.overview`、`nav.agents.subtitle` |
| `status.*` | 状态文本 | `status.live`、`status.offline` |
| `action.*` | 动作按钮 | `action.logout`、`action.skip` |
| `auth.*` | 登录相关 | `auth.signIn`、`auth.username` |
| `footer.*` | 页脚 | `footer.tagline`、`footer.copyright` |
| `settings.*` | 设置页 | `settings.theme`、`settings.density` |
| `common.*` | 通用词汇 | `common.refresh`、`common.save` |
| `view.<page>.*` | 页面专属 | `view.tenants.createTenant`、`view.audit.filterActor` |
| `topbar.*` | 顶栏 | `topbar.refreshTime`、`topbar.role.admin` |
| `users.*` | 用户管理 | `users.registerUser`、`users.confirmDelete` |
| `a11y.*` | 无障碍标签 | `a11y.mainNavigation`、`a11y.send` |
| `coach.*` | 引导提示 | `coach.actions.title` |
| `palette.*` | 命令面板 | `palette.placeholder` |
| `error.*` | 错误信息 | `error.somethingWrong` |

**新功能建议**：新增页面时创建 `src/i18n/view-<page>.js`，导出 `{ messages: { en: {...}, zh: {...} } }`，无需修改 `index.js`。

### 8.4 view-tenants.js 示例

```javascript
export const messages = {
  en: {
    'view.tenants.subtitle': 'Isolated workspaces and resource quotas',
    'view.tenants.createTenant': 'Create Tenant',
    'view.tenants.tenantId': 'Tenant ID',
    'view.tenants.plan': 'Plan',
    // ...
  },
  zh: {
    'view.tenants.subtitle': '隔离的工作空间与资源配额',
    'view.tenants.createTenant': '创建 Tenant',
    'view.tenants.tenantId': 'Tenant ID',
    'view.tenants.plan': '套餐',
    // ...
  },
};
```

---

## 9. 组件库

### 9.1 组件清单

`src/components/` 下 22 个组件：

| 组件 | 文件 | 用途 | 是否导出 |
|---|---|---|---|
| `AppIcon` | AppIcon.vue | 统一图标集（简约小众风格） | ✅ |
| `Card` | Card.vue | 卡片容器 | ✅ |
| `StatCard` | StatCard.vue | 统计卡片 | ✅ |
| `Badge` | Badge.vue | 徽章/标签 | ✅ |
| `DataTable` | DataTable.vue | 数据表格（排序、骨架、空态） | ✅ |
| `Segmented` | Segmented.vue | 分段控件 | ✅ |
| `Skeleton` | Skeleton.vue | 骨架屏 | ✅ |
| `EmptyState` | EmptyState.vue | 空状态 | ✅ |
| `Toast` | Toast.vue | 全局通知 | ✅ |
| `PageHeader` | PageHeader.vue | 页面头部 | ✅ |
| `DagGraph` | DagGraph.vue | DAG 图可视化 | ✅ |
| `NodeDetailPanel` | NodeDetailPanel.vue | 节点详情面板 | ✅ |
| `ListPageLayout` | ListPageLayout.vue | **列表页骨架** | ❌（按需 import） |
| `FilterBar` | FilterBar.vue | **声明式过滤器** | ❌ |
| `DetailDrawer` | DetailDrawer.vue | **详情抽屉** | ❌ |
| `TopBar` | TopBar.vue | 顶栏 | ❌ |
| `AppFooter` | AppFooter.vue | 页脚 | ❌ |
| `CoachMarks` | CoachMarks.vue | 引导标记 | ❌ |
| `CommandPalette` | CommandPalette.vue | 命令面板（Ctrl+K） | ❌ |
| `McpTopology` | McpTopology.vue | MCP 拓扑图 | ❌ |
| `EvolutionTimeline` | EvolutionTimeline.vue | 演化时间线 | ❌ |

### 9.2 关键可复用组件

#### ListPageLayout（列表页骨架）

统一 22 个视图的"页头/统计/过滤/三态"结构：

```vue
<ListPageLayout
  :loading="loading"
  :error="error"
  :empty="!rows.length"
  :filter-schema="filterSchema"
  search-key="query"
  :search-placeholder="t('common.search')"
  :results-label="`${rows.length} items`"
  error-title="Failed"
  empty-title="No data">
  <template #badges><Badge tone="brand">Enterprise</Badge></template>
  <template #actions><button @click="openCreate">Create</button></template>
  <template #stats><StatCard ... /></template>
  <template #content="{ filters }">
    <DataTable :rows="filteredRows" :columns="cols" />
  </template>
</ListPageLayout>
```

**插槽**：`badges`、`actions`、`stats`、`content`（作用域插槽，暴露 `filters`）、`itemsEmpty`、`loading`、`error`

#### FilterBar（声明式过滤器）

```vue
<FilterBar
  :model-value="filters"
  :schema="[
    { key: 'level', label: 'Level', options: [{value:'info'},{value:'warning'},{value:'critical'}] },
    { key: 'status', label: 'Status', options: [...] }
  ]"
  search-key="actor"
  search-placeholder="Filter by actor…"
  :results-label="`${n} rows`"
/>
```

#### DetailDrawer（详情抽屉）

```vue
<DetailDrawer :open="open" :title="title" icon="building" @close="open = false">
  <!-- 内容 -->
  <template #footer>
    <button @click="save">Save</button>
  </template>
</DetailDrawer>
```

特性：Teleport to body、Esc 关闭、焦点陷阱、焦点还原、右侧滑出 480px。

#### DataTable（数据表格）

```vue
<DataTable
  :columns="[
    { key: 'name', label: 'Name' },
    { key: 'status', label: 'Status', type: 'badge' },
    { key: 'created', label: 'Created', type: 'time' },
    { key: 'active', label: 'Active', type: 'bool-icon' },
  ]"
  :rows="rows"
  :loading="loading"
  row-key="id"
  :empty-text="t('common.noData')"
  sortable
  compact
/>
```

列类型：`text`（默认）、`badge`、`bool-icon`、`num`、`time`（相对时间）。

### 9.3 设计规范

- **主题**：light/dark（`<html data-theme>`），偏好浅色/白色
- **密度**：comfortable/compact（`<html data-density>`）
- **图标**：AppIcon 统一集，简约小众风格
- **布局**：顶栏 + 侧栏 + 内容区，左右宽度一致
- **样式**：`src/styles/` 下 `tokens.css`（设计 token）+ `themes.css` + `layout.css` + `pages.css`

---

## 10. 对 6 个新功能的建议

> 以下为新增企业版功能的实现清单模板。每个功能需指明：后端文件、前端文件、数据库表、FeatureFlag、路由前缀、i18n key 前缀。

### 10.1 通用实现清单模板

每个新企业版功能需新增/修改以下文件：

#### 后端

| 文件 | 用途 | 必需 |
|---|---|---|
| `py/maop/dashboard/routers/<feature>.py` | FastAPI 路由 | ✅ |
| `py/maop/enterprise/<feature>.py` | 业务逻辑 Manager | ✅ |
| `py/maop/enterprise/pg_persist.py`（追加） | PG 持久化 Store | 若需 PG |
| `py/maop/config/edition.py`（追加 FeatureFlag） | 特性开关 | ✅ |
| `py/maop/dashboard/server.py`（追加 include_router） | 路由注册 | ✅ |

#### 前端

| 文件 | 用途 | 必需 |
|---|---|---|
| `dashboard-enterprise/src/views/<Feature>.vue` | 页面组件 | ✅ |
| `dashboard-enterprise/src/i18n/view-<feature>.js` | 翻译 | ✅ |
| `dashboard-enterprise/src/router/index.js`（追加路由） | 路由注册 | ✅ |
| `dashboard-enterprise/src/nav.js`（追加导航项） | 导航菜单 | ✅ |
| `dashboard-enterprise/src/i18n/index.js`（追加 nav key） | 导航翻译 | ✅ |

#### 数据库

| 项 | 说明 |
|---|---|
| SQLite 表 | 在 `<feature>.py` 或 `pg_persist.py` 中 `CREATE TABLE IF NOT EXISTS` |
| PostgreSQL 表 | 在 `Pg<Feature>Store._ensure_schema()` 中建表 |
| Alembic 迁移 | `py/maop/migrations/alembic/versions/<rev>_<feature>.py` |

#### 配置

| 项 | 说明 |
|---|---|
| `.env.example`（追加） | 新增环境变量示例 |
| `py/maop/config/settings.py`（追加字段） | Pydantic 设置字段 |

### 10.2 后端路由模板

```python
"""Enterprise <Feature> router — exposes <Feature>Manager via FastAPI endpoints.

All operations require admin role via require_admin.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel

from maop.config.edition import FeatureFlag, has_feature
from maop.core.security.middleware import require_admin
from maop.dashboard.error_handler import handle_api_errors

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/<feature>", tags=["<feature>"])

_<feature>_manager: Any = None


def _get_manager() -> Any:
    global _<feature>_manager
    if _<feature>_manager is None:
        from maop.enterprise.<feature> import <Feature>Manager
        _<feature>_manager = <Feature>Manager()
    return _<feature>_manager


# ── Request models ────────────────────────────────────────────────

class Create<Feature>Request(BaseModel):
    name: str
    # ... 其他字段


# ── Endpoints ─────────────────────────────────────────────────────

@router.get("/list")
@handle_api_errors
async def list_<feature>s(request: Request) -> dict[str, Any]:
    require_admin(request)
    if not has_feature(FeatureFlag.<FEATURE>):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                            detail="<feature> not available in this edition")
    mgr = _get_manager()
    items = mgr.list_all()
    return {"status": "ok", "<feature>s": [i.model_dump() for i in items], "count": len(items)}


@router.post("/create")
@handle_api_errors
async def create_<feature>(body: Create<Feature>Request, request: Request) -> dict[str, Any]:
    require_admin(request)
    if not has_feature(FeatureFlag.<FEATURE>):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                            detail="<feature> not available in this edition")
    mgr = _get_manager()
    item = mgr.create(**body.model_dump())
    return {"status": "ok", "<feature>": item.model_dump()}
```

### 10.3 前端页面模板

```vue
<template>
  <ListPageLayout
    :loading="loading"
    :error="error"
    :empty="!items.length"
    :filter-schema="filterSchema"
    search-key="query"
    :search-placeholder="t('common.search')"
    :error-title="t('view.<feature>.loadError')"
    :empty-title="t('view.<feature>.noItems')">
    <template #badges>
      <Badge tone="brand">{{ t('view.<feature>.enterprise') }}</Badge>
    </template>
    <template #actions>
      <button class="btn btn--primary" @click="openCreate">
        <AppIcon name="<icon>" :size="15" /> {{ t('view.<feature>.create') }}
      </button>
    </template>
    <template #content>
      <DataTable :columns="cols" :rows="items" :loading="loading" />
    </template>
  </ListPageLayout>

  <!-- 创建 Modal -->
  <div v-if="showCreate" v-modal-a11y class="modal-overlay" @click.self="showCreate = false">
    <div class="modal">
      <h3>{{ t('view.<feature>.create') }}</h3>
      <!-- 表单字段 -->
      <div class="modal-actions">
        <button class="btn" @click="showCreate = false">{{ t('common.cancel') }}</button>
        <button class="btn btn--primary" :disabled="saving" @click="createItem">
          {{ saving ? t('common.loading') : t('common.save') }}
        </button>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue';
import { useApiStore } from '../stores/api.js';
import { useToast } from '../composables/useToast.js';
import { useI18n } from '../i18n';
import { Badge, DataTable } from '../components/index.js';
import ListPageLayout from '../components/ListPageLayout.vue';
import AppIcon from '../components/AppIcon.vue';

const { t } = useI18n();
const api = useApiStore();
const toast = useToast();

const items = ref([]);
const loading = ref(true);
const error = ref('');
const showCreate = ref(false);
const saving = ref(false);

const cols = [
  { key: 'name', label: t('common.name') },
  { key: 'status', label: t('common.status'), type: 'badge' },
  { key: 'created_at', label: t('common.created'), type: 'time' },
];

async function load() {
  loading.value = true;
  try {
    const d = await api.get('/api/<feature>/list');
    items.value = d.<feature>s || [];
  } catch (e) {
    error.value = e.message;
  } finally {
    loading.value = false;
  }
}

onMounted(load);
</script>
```

### 10.4 i18n 模板

```javascript
// src/i18n/view-<feature>.js
export const messages = {
  en: {
    'view.<feature>.subtitle': '<Feature description>',
    'view.<feature>.enterprise': 'Enterprise',
    'view.<feature>.create': 'Create <Feature>',
    'view.<feature>.noItems': 'No items',
    'view.<feature>.loadError': 'Could not load items',
    // ...
  },
  zh: {
    'view.<feature>.subtitle': '<功能描述>',
    'view.<feature>.enterprise': '企业版',
    'view.<feature>.create': '创建<功能>',
    'view.<feature>.noItems': '暂无数据',
    'view.<feature>.loadError': '加载失败',
    // ...
  },
};
```

### 10.5 路由/导航注册

```javascript
// src/router/index.js — 追加
{ path: '/<feature>', name: '<feature>',
  component: () => import('../views/<Feature>.vue'),
  meta: { requiresEnterprise: true } },

// src/nav.js — 追加到 nav.govern 分组
{ to: '/<feature>', label: 'nav.<feature>', icon: '<icon>',
  subtitle: 'nav.<feature>.subtitle', enterprise: true },

// src/i18n/index.js — coreMessages 追加
'en': { 'nav.<feature>': '<Feature>', 'nav.<feature>.subtitle': '<description>' },
'zh': { 'nav.<feature>': '<功能>', 'nav.<feature>.subtitle': '<描述>' },
```

### 10.6 FeatureFlag 注册

```python
# py/maop/config/edition.py — FeatureFlag 枚举追加
class FeatureFlag(str, Enum):
    # ... 现有
    <FEATURE> = "<feature>"  # 新增

# _ENTERPRISE_FEATURES 集合追加
_ENTERPRISE_FEATURES: frozenset[FeatureFlag] = frozenset({
    # ... 现有
    FeatureFlag.<FEATURE>,
})
```

### 10.7 server.py 路由注册

```python
# py/maop/dashboard/server.py — 追加
if has_feature(FeatureFlag.<FEATURE>):
    try:
        from maop.dashboard.routers import <feature> as <feature>_router
        app.include_router(<feature>_router.router)
        has_<feature>_router = True
        logger.info("[server] Enterprise router: <feature> enabled")
    except ImportError as _e:
        logger.warning("[server] Enterprise router MISSING: <feature> (%s)", _e)
```

### 10.8 数据库表设计建议

每个新功能的表应遵循以下约定：

| 约定 | 说明 |
|---|---|
| 主键 | `id SERIAL PRIMARY KEY`（PG）或 `id INTEGER PRIMARY KEY AUTOINCREMENT`（SQLite） |
| 时间戳 | `created_at DOUBLE PRECISION`（Unix timestamp） |
| 软删除 | 避免 `DELETE`，使用 `deleted_at` 字段或 `enabled` 标志 |
| 租户隔离 | 行级 `tenant_id TEXT` 字段 + 索引 |
| JSON 字段 | PG 用 `JSONB`，SQLite 用 `TEXT`（存 JSON 字符串） |
| 索引 | 高频查询字段建索引（`tenant_id`、`created_at`、`status`） |
| 外键 | `FOREIGN KEY ... ON DELETE CASCADE`（如 tenant_usage → tenants） |

### 10.9 现有企业版功能参考

新功能可参考以下已实现的企业版功能的完整链路：

| 功能 | 后端路由 | 后端 Manager | 前端页面 | i18n | FeatureFlag |
|---|---|---|---|---|---|
| 租户管理 | `routers/tenant.py` | `enterprise/tenant.py::TenantManager` | `views/Tenants.vue` | `view-tenants.js` | `TENANT_ISOLATION` |
| RBAC | `routers/rbac.py` | `enterprise/rbac.py::RBACManager` | `views/RBAC.vue` | `view-rbac.js` | `RBAC` |
| 审计日志 | `routers/audit.py` | `enterprise/audit.py::EnterpriseAuditLogger` | `views/Audit.vue` | `view-audit.js` | `AUDIT_LOG` |
| SSO | `routers/sso.py` | `enterprise/sso.py::SSOManager` | （无专门页面） | - | `SSO` |
| n8n 集成 | `routers/n8n.py` | `enterprise/n8n.py::N8nClient` | （无专门页面） | - | `N8N_INTEGRATION` |
| 用户管理 | `routers/auth.py`（users 端点） | `core/security/auth.py::AuthManager` | `views/Users.vue` | `index.js::users.*` | `MULTI_USER` |

### 10.10 PRD 编写检查清单

每个新功能 PRD 应明确以下内容：

- [ ] 功能名称与 FeatureFlag 名称
- [ ] 路由前缀（`/api/<feature>`）
- [ ] 前端路由路径（`/<feature>`）与导航分组
- [ ] 数据库表 schema（SQLite + PostgreSQL）
- [ ] 请求/响应 Pydantic 模型
- [ ] 权限要求（`require_admin` / 具体 Permission）
- [ ] 多租户隔离方式（行级 `tenant_id` / 全局）
- [ ] 环境变量配置
- [ ] i18n key 命名（`view.<feature>.*`）
- [ ] 图标选择（AppIcon 名称）
- [ ] 是否需要 PG 持久化（`Pg<Feature>Store`）
- [ ] 是否需要 WebSocket 推送
- [ ] 是否需要审计日志记录
- [ ] Personal 版降级行为（404 / 软降级响应）

---

## 附录：关键文件路径速查

### 后端

| 用途 | 路径 |
|---|---|
| FastAPI 入口 | `py/maop/dashboard/server.py` |
| 路由目录 | `py/maop/dashboard/routers/` |
| 错误处理 | `py/maop/dashboard/error_handler.py` |
| Edition 配置 | `py/maop/config/edition.py` |
| Settings | `py/maop/config/settings.py` |
| 认证中间件 | `py/maop/core/security/middleware.py` |
| JWT/API Key | `py/maop/core/security/auth.py` |
| DB 工具 | `py/maop/core/backends/db_utils.py` |
| PG 后端 | `py/maop/core/backends/backends_pg.py` |
| 企业版模块 | `py/maop/enterprise/` |
| PG 持久化 | `py/maop/enterprise/pg_persist.py` |
| 多租户 | `py/maop/enterprise/tenant.py` |
| RBAC | `py/maop/enterprise/rbac.py` |
| 审计 | `py/maop/enterprise/audit.py` |
| SSO | `py/maop/enterprise/sso.py` |
| License | `py/maop/enterprise/license.py` |

### 前端

| 用途 | 路径 |
|---|---|
| 入口 | `dashboard-enterprise/src/main.js` |
| App 根组件 | `dashboard-enterprise/src/App.vue` |
| 路由 | `dashboard-enterprise/src/router/index.js` |
| 导航 | `dashboard-enterprise/src/nav.js` |
| 视图目录 | `dashboard-enterprise/src/views/` |
| 组件目录 | `dashboard-enterprise/src/components/` |
| 组件导出 | `dashboard-enterprise/src/components/index.js` |
| Store 目录 | `dashboard-enterprise/src/stores/` |
| API Store | `dashboard-enterprise/src/stores/api.js` |
| Edition Store | `dashboard-enterprise/src/stores/edition.js` |
| i18n 入口 | `dashboard-enterprise/src/i18n/index.js` |
| i18n 视图字典 | `dashboard-enterprise/src/i18n/view-*.js` |
| Composables | `dashboard-enterprise/src/composables/` |
| 样式 | `dashboard-enterprise/src/styles/` |

### 配置

| 用途 | 路径 |
|---|---|
| 环境变量示例 | `.env.example` |
| 数据库 schema 文档 | `docs/database-schema.md` |
| 企业版文档 | `docs/enterprise/` |
| 设计系统（已归档） | `docs/archive/audits/design-system-legacy.md` |
| 前端风格指南 | `docs/frontend-style-guide.md` |