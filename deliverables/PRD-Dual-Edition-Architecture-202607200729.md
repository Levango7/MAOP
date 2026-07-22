# MAOP 双路线架构产品设计规格书

| 字段 | 值 |
|------|-----|
| 文档版本 | 1.0 |
| 作者 | MAOP Team |
| 日期 | 2026-07-20 |
| 状态 | 已确认 |
| 文档类型 | PRD — 产品需求文档 |

---

## 1. 背景与目标

### 1.1 背景

MAOP 当前以单一形态运行，所有功能模块（RBAC、多租户、PostgreSQL、Redis 等）混合在同一代码库中。这导致：

- **个人用户**：被迫承载企业级依赖（PostgreSQL 驱动、Redis 客户端、RBAC 中间件），安装体积大、启动慢
- **企业用户**：关键功能（多租户隔离、审计合规、SSO）缺乏正式保障，降级路径不明确
- **开发者**：功能边界模糊，无法清晰判断某模块属于哪个版本

### 1.2 目标

1. 将 MAOP 拆分为 **个人版 (Personal)** 和 **企业版 (Enterprise)** 两条产品线
2. 个人版是核心的简化子集 — 轻量、零配置、开箱即用
3. 企业版是核心的完整扩展 — 安全、合规、可扩展
4. 两条产品线共享核心引擎，通过安装包和条件加载区分
5. API 严格兼容 — 个人版 API 是企业版 API 的子集

### 1.3 成功指标

| 指标 | 个人版 | 企业版 |
|------|--------|--------|
| 安装后磁盘占用 | < 50 MB | < 120 MB |
| 冷启动时间 | < 3 秒 | < 8 秒 |
| 外部依赖 | 0（纯 SQLite） | PostgreSQL + Redis（可降级 SQLite） |
| pip install 依赖数 | < 15 | < 30 |

---

## 2. 用户角色与场景

### 2.1 个人版用户画像

- **独立开发者**：本地使用 MAOP 编排 Agent，处理代码生成、文档编写等日常任务
- **小团队（< 5 人）**：共享一台开发机，不需要多租户和 RBAC
- **原型验证**：快速搭建 Agent 工作流，验证可行性后再考虑企业版

### 2.2 企业版用户画像

- **企业 IT 团队**：部署在内网服务器，多部门共享，需要权限隔离
- **SaaS 运营方**：对外提供 Agent 服务，需要租户隔离、配额管理、审计合规
- **合规要求场景**：金融、医疗等行业，需要完整审计链、SSO、数据加密

### 2.3 核心场景对比

| 场景 | 个人版 | 企业版 |
|------|--------|--------|
| 本地单用户运行 | ✅ 主要场景 | ✅ 降级场景 |
| 多用户共享服务器 | ❌ | ✅ 主要场景 |
| 多租户隔离 | ❌ 不可用 | ✅ 强制 |
| RBAC 权限控制 | ❌ | ✅ 强制 |
| SSO/LDAP 集成 | ❌ | ✅ |
| 审计合规报告 | ❌ | ✅ |
| 容器化部署 | ❌ | ✅ |
| 高可用/故障转移 | ❌ | ✅ |

---

## 3. 功能需求

### 3.1 功能矩阵

#### 共享层（两个版本都包含）

| 模块 | 说明 |
|------|------|
| maop_loop / engine | 核心编排引擎 |
| dispatcher | Agent 调度器 |
| maop_plan | 计划 + 工作流 DSL + DAG |
| Agent 适配器 | OpenAI / Claude / Kimi / Codex 等 |
| BYOK Gateway | 多源密钥路由 |
| 成本追踪 | BudgetGuard + CostTracker |
| YAML 配置 | agents.yaml 路由表 |
| Permission Manager | 权限检查（个人版 ask→deny，企业版 ask→人工审批流） |
| Circuit Breaker | 断路器 + 故障转移链 |
| Guardrail | 输入/输出安全检查 |
| Plugin System | 插件加载 + 沙箱 |
| Hook Manager | 生命周期钩子 |
| Vector Store | 自研向量存储 |
| Memory / LRUCache | 短期记忆 + LRU 缓存 |
| MCP Hub | MCP 协议中心 |
| Dashboard API | 核心 REST API（/api/agents, /api/tasks 等） |
| 原生 JS Dashboard | 轻量前端（/dashboard） |

#### 个人版特有

| 模块 | 说明 |
|------|------|
| SQLite 全栈 | 数据库/缓存/队列全部 SQLite |
| Memory 缓存 | LRUCache 替代 Redis |
| SQLite Queue | MessageQueue 替代 RabbitMQ |
| 轻量 Dashboard | 原生 JS + Vite，无框架依赖 |
| 本地文件日志 | 审计日志写入本地文件 |

#### 企业版特有

| 模块 | 说明 |
|------|------|
| RBAC | 角色-权限-资源访问控制 |
| 多租户管理 | 租户创建/配额/隔离/审计 |
| PostgreSQL 后端 | 关系型数据库（替代 SQLite） |
| Redis 缓存 | 分布式缓存（替代 LRUCache） |
| RabbitMQ 队列 | 消息队列（替代 SQLite Queue） |
| SSO / LDAP | 企业身份集成 |
| 审计合规 | 结构化审计日志 + 合规报告导出 |
| TLS 强制 | 传输加密 |
| Vue 3 Dashboard | 企业级前端（Pinia + Vue Router） |
| 容器化支持 | Docker / K8s 部署配置 |
| 高可用 | 健康检查 + 自动恢复 + 水平扩展 |

### 3.2 用户故事

#### 个人版

| ID | 用户故事 | 验收标准 |
|----|---------|---------|
| P-01 | 作为个人开发者，我想一键安装 MAOP 并立即使用 | `pip install maop` 后无需任何配置即可启动 |
| P-02 | 作为个人开发者，我不想安装 PostgreSQL/Redis | 所有存储使用 SQLite，零外部依赖 |
| P-03 | 作为个人开发者，我想通过浏览器管理 Agent | 内置轻量 Dashboard，无需 npm build |
| P-04 | 作为个人开发者，我不需要多租户 | 系统不展示租户相关 UI 和 API |
| P-05 | 作为个人开发者，我想升级到企业版 | `pip install maop-enterprise` 后自动启用企业功能 |

#### 企业版

| ID | 用户故事 | 验收标准 |
|----|---------|---------|
| E-01 | 作为企业管理员，我想创建多个租户并分配配额 | 租户 CRUD + 配额限制 + 使用量监控 |
| E-02 | 作为企业管理员，我想配置 RBAC 权限 | 角色-权限矩阵 + API 级访问控制 |
| E-03 | 作为企业管理员，我想集成企业 SSO | 支持 OIDC / SAML / LDAP 认证 |
| E-04 | 作为企业管理员，我想导出审计报告 | 按时间/租户/操作类型筛选并导出 |
| E-05 | 作为运维人员，我想部署到 K8s | 提供 Helm Chart + 健康检查端点 |
| E-06 | 作为运维人员，企业版在无 PostgreSQL 时应可降级 | 自动降级到 SQLite + WARNING 日志，多租户功能标记不可用 |

---

## 4. 非功能需求

### 4.1 性能

| 指标 | 个人版 | 企业版 |
|------|--------|--------|
| 单次 Agent 调用延迟 | < 500ms（本地） | < 200ms（网络） |
| 并发任务数 | 1-5 | 50-500 |
| 数据库写入 TPS | SQLite: ~100 | PostgreSQL: ~5000 |

### 4.2 安全

| 要求 | 个人版 | 企业版 |
|------|--------|--------|
| 认证 | 可选（本地信任模式） | 强制 JWT + TLS |
| 密钥存储 | 环境变量 | Vault / 环境变量（禁用 direct 明文） |
| 沙箱隔离 | 工作目录 + 超时 | 容器级（可选） |
| 审计日志 | 本地文件 | 结构化 + 不可篡改 |

### 4.3 兼容性

| 维度 | 要求 |
|------|------|
| Python | >= 3.11 |
| 操作系统 | Windows / macOS / Linux |
| API 兼容 | 个人版 API 是企业版 API 的严格子集 |
| 数据迁移 | 个人版 SQLite 数据可迁移到企业版 PostgreSQL |

---

## 5. 架构设计

### 5.1 安装包结构

```
PyPI 包拆分：
  maop                    ← 核心包（共享层 + 个人版功能）
  maop-enterprise         ← 企业版扩展包（依赖 maop）
```

安装命令：
- 个人版：`pip install maop`
- 企业版：`pip install maop-enterprise`（自动拉取 maop 核心包）

### 5.2 代码目录结构

```
py/maop/
  core/                    ← 共享层（两个版本都用）
    engine.py              ← 编排引擎
    dispatcher.py          ← 调度器
    maop_plan.py           ← 计划 + 工作流
    byok.py                ← 密钥路由
    cost_tracker.py        ← 成本追踪
    budget_guard.py        ← 预算守卫
    permission.py          ← 权限管理
    circuit_breaker.py     ← 断路器
    guardrail.py           ← 安全检查
    plugin.py              ← 插件系统
    hook_manager.py        ← 钩子管理
    vector.py              ← 向量存储
    memory.py              ← 记忆管理
    lru_cache.py           ← LRU 缓存
    mcp_hub.py             ← MCP 中心
    mcp_client.py          ← MCP 客户端
    mcp_transport.py       ← MCP 传输
    db_utils.py            ← SQLite 工具
    kv_store.py            ← KV 存储
    timeseries.py          ← 时序数据
    auth.py                ← JWT 认证（共享，企业版增强）
    sandbox.py             ← 沙箱管理
    human_proxy.py         ← 人工审批代理
    error_schema.py        ← 错误模型
    regression.py          ← 回归测试
    tool_schema.py         ← 工具模型
    function_call.py       ← 函数调用
    message_queue.py       ← SQLite 消息队列
    tenant.py              ← 租户管理（企业版启用）
    services.py            ← 服务容器
    project_context.py     ← 项目上下文
    migration.py           ← 数据迁移
    db_backup.py           ← 数据库备份

  enterprise/              ← 企业版独有模块
    __init__.py            ← 版本检测 + 自动注册
    rbac.py                ← RBAC 权限控制
    tenant_manager.py      ← 多租户管理器（增强版）
    pg_backend.py          ← PostgreSQL 后端
    redis_cache.py         ← Redis 缓存后端
    rabbitmq_queue.py      ← RabbitMQ 消息队列
    sso.py                 ← SSO / LDAP 集成
    audit.py               ← 审计合规引擎
    tls.py                 ← TLS 配置管理
    container.py           ← 容器化支持
    ha.py                  ← 高可用管理

  config/
    edition.py             ← 版本特征注册表（唯一真相源）
    settings.py            ← 配置模型
    loader.py              ← YAML 加载器

  dashboard/               ← 共享 Dashboard API + 个人版前端
    server.py              ← FastAPI 服务
    routers/               ← REST API 路由
    static/                ← 个人版原生 JS 前端

  dashboard_enterprise/    ← 企业版 Vue 3 前端
    src/
      views/               ← 企业版独有页面
        RBAC.vue
        Tenants.vue
        Audit.vue
        SSO.vue
```

### 5.3 版本特征注册表（edition.py — 唯一真相源）

edition.py 是整个双路线架构的控制中心，定义每个版本的功能开关：

```python
# config/edition.py

from __future__ import annotations
from enum import Enum
from typing import Any

class Edition(str, Enum):
    PERSONAL = "personal"
    ENTERPRISE = "enterprise"

class FeatureFlag:
    def __init__(self, name: str, personal: bool, enterprise: bool) -> None:
        self.name = name
        self.personal = personal
        self.enterprise = enterprise

    def enabled(self, edition: Edition) -> bool:
        if edition == Edition.PERSONAL:
            return self.personal
        return self.enterprise

FEATURES: dict[str, FeatureFlag] = {
    "rbac":                FeatureFlag("rbac",                personal=False, enterprise=True),
    "multi_tenant":        FeatureFlag("multi_tenant",        personal=False, enterprise=True),
    "pg_backend":          FeatureFlag("pg_backend",          personal=False, enterprise=True),
    "redis_cache":         FeatureFlag("redis_cache",         personal=False, enterprise=True),
    "rabbitmq_queue":      FeatureFlag("rabbitmq_queue",      personal=False, enterprise=True),
    "sso":                 FeatureFlag("sso",                 personal=False, enterprise=True),
    "audit_compliance":    FeatureFlag("audit_compliance",    personal=False, enterprise=True),
    "tls_enforced":        FeatureFlag("tls_enforced",        personal=False, enterprise=True),
    "container_support":   FeatureFlag("container_support",   personal=False, enterprise=True),
    "high_availability":   FeatureFlag("high_availability",   personal=False, enterprise=True),
    "vue_dashboard":       FeatureFlag("vue_dashboard",       personal=False, enterprise=True),
    "permission_ask_deny": FeatureFlag("permission_ask_deny", personal=True,  enterprise=False),
    "permission_ask_flow": FeatureFlag("permission_ask_flow", personal=False, enterprise=True),
}

_current_edition: Edition = Edition.PERSONAL

def get_edition() -> Edition:
    return _current_edition

def set_edition(edition: Edition) -> None:
    global _current_edition
    _current_edition = edition

def has_feature(name: str) -> bool:
    flag = FEATURES.get(name)
    if flag is None:
        return False
    return flag.enabled(_current_edition)

def edition_features() -> dict[str, bool]:
    return {name: flag.enabled(_current_edition) for name, flag in FEATURES.items()}

def detect_edition() -> Edition:
    try:
        import maop.enterprise  # noqa: F401
        return Edition.ENTERPRISE
    except ImportError:
        return Edition.PERSONAL
```

### 5.4 条件加载机制

企业版扩展包安装后，`maop/enterprise/__init__.py` 自动执行注册：

```python
# enterprise/__init__.py

from maop.config.edition import Edition, set_edition

set_edition(Edition.ENTERPRISE)
```

核心代码通过 `has_feature()` 判断是否加载企业版模块：

```python
# 示例：server.py 中的条件加载
from maop.config.edition import has_feature

if has_feature("rbac"):
    from maop.enterprise.rbac import RBACManager
    app.include_router(rbac_router)

if has_feature("vue_dashboard"):
    # serve Vue 3 enterprise frontend
    ...
```

### 5.5 降级策略

企业版在无 PostgreSQL/Redis 时的降级行为：

```
启动检测流程：
  1. edition = ENTERPRISE
  2. 检测 PostgreSQL 可用性
     → 可用：使用 PostgreSQL 后端
     → 不可用：降级到 SQLite + 打印 WARNING
        WARNING: PostgreSQL unavailable, falling back to SQLite — multi-tenant isolation is NOT guaranteed
  3. 检测 Redis 可用性
     → 可用：使用 Redis 缓存
     → 不可用：降级到 LRUCache + 打印 WARNING
  4. 多租户功能
     → PostgreSQL 可用：完整租户隔离
     → 降级到 SQLite：多租户功能标记不可用
        WARNING: Multi-tenant features disabled — running on SQLite without tenant isolation
```

降级时行为：
- **多租户**：不可用。API 返回 503 + 明确错误信息
- **RBAC**：可用（SQLite 存储），但无租户隔离
- **审计**：可用（SQLite 存储），但无不可篡改保障
- **SSO**：可用（不依赖数据库类型）
- **Vue Dashboard**：可用，但租户管理页面灰显

### 5.6 API 兼容性设计

原则：**个人版 API 是企业版 API 的严格子集**

```
共享 API（两个版本都有）：
  GET  /api/agents              ← 列出 Agent
  POST /api/tasks               ← 提交任务
  GET  /api/tasks/{id}          ← 查询任务状态
  GET  /api/info                ← 系统信息
  GET  /api/info/edition        ← 版本信息 + 功能开关
  GET  /api/costs               ← 成本统计
  GET  /api/config              ← 配置查看
  ...

企业版独有 API：
  POST /api/rbac/roles          ← 创建角色
  GET  /api/rbac/roles          ← 列出角色
  POST /api/tenants             ← 创建租户
  GET  /api/tenants/{id}/quota  ← 查询租户配额
  GET  /api/audit/logs          ← 审计日志
  POST /api/sso/config          ← SSO 配置
  ...

个人版调用企业版 API：
  → 返回 404 + {"error": "This endpoint requires MAOP Enterprise Edition"}
```

---

## 6. 数据需求

### 6.1 个人版数据层

| 组件 | 存储 | 文件/连接 |
|------|------|-----------|
| 主数据库 | SQLite | data/maop.db |
| KV 存储 | SQLite | data/kv_store.db |
| 时序数据 | SQLite | data/maop.db (同主库) |
| 缓存 | LRUCache | 内存 |
| 消息队列 | SQLite | data/maop.db (同主库) |
| 向量存储 | SQLite | data/maop.db (同主库) |
| 审计日志 | 文件 | logs/audit.jsonl |

### 6.2 企业版数据层

| 组件 | 存储（首选） | 存储（降级） |
|------|-------------|-------------|
| 主数据库 | PostgreSQL | SQLite |
| KV 存储 | PostgreSQL | SQLite |
| 时序数据 | PostgreSQL | SQLite |
| 缓存 | Redis | LRUCache |
| 消息队列 | RabbitMQ | SQLite Queue |
| 向量存储 | PostgreSQL (pgvector) | SQLite |
| 审计日志 | PostgreSQL + 不可篡改 | 文件 |

### 6.3 数据迁移

个人版 → 企业版迁移路径：

1. `maop.db` (SQLite) → PostgreSQL（通过 migration.py 自动迁移）
2. `kv_store.db` (SQLite) → PostgreSQL
3. `logs/audit.jsonl` → PostgreSQL audit 表
4. LRUCache 数据 → Redis（运行时重建，无需迁移）

---

## 7. 依赖与约束

### 7.1 个人版依赖

```
maop (核心包)：
  - pydantic >= 2.0
  - pyyaml
  - fastapi
  - uvicorn
  - httpx
  - aiohttp
  - 无 PostgreSQL/Redis/RabbitMQ 依赖
```

### 7.2 企业版依赖

```
maop-enterprise (扩展包)：
  - maop (核心包)
  - psycopg2-binary  (PostgreSQL)
  - redis
  - pika             (RabbitMQ)
  - python-ldap      (LDAP)
  - cryptography     (TLS)
```

### 7.3 约束

1. **核心包不可依赖企业版模块** — `maop.core.*` 不能 import `maop.enterprise.*`
2. **企业版可依赖核心包** — `maop.enterprise.*` 可 import `maop.core.*`
3. **API 路径不可冲突** — 企业版独有端点必须使用独立路径前缀
4. **配置文件兼容** — agents.yaml 在两个版本中格式一致
5. **数据库 Schema 兼容** — 个人版 SQLite schema 是企业版 PostgreSQL schema 的子集

---

## 8. 里程碑

### Phase E-1：版本注册表 + 条件加载框架

- [ ] 实现 `config/edition.py` — FeatureFlag + detect_edition + has_feature
- [ ] 重构 `config/settings.py` — edition 字段驱动后端选择
- [ ] 重构 `backends.py` — edition-aware 后端选择
- [ ] 重构 `server.py` — 条件加载企业版 router
- [ ] 单元测试：edition 检测、功能开关、降级行为

### Phase E-2：企业版扩展包骨架

- [ ] 创建 `maop/enterprise/` 目录结构
- [ ] 实现 `enterprise/__init__.py` — 自动注册
- [ ] 实现 `enterprise/pg_backend.py` — PostgreSQL 后端
- [ ] 实现 `enterprise/redis_cache.py` — Redis 缓存
- [ ] 实现 `enterprise/rbac.py` — RBAC 权限控制
- [ ] 实现 `enterprise/tenant_manager.py` — 多租户管理器
- [ ] 降级检测 + WARNING 日志

### Phase E-3：企业版前端 + 部署

- [ ] 完善 Vue 3 Dashboard（RBAC/Tenants/Audit/SSO 页面）
- [ ] 实现 `enterprise/sso.py` — OIDC/SAML/LDAP
- [ ] 实现 `enterprise/audit.py` — 审计合规引擎
- [ ] Docker / K8s 部署配置
- [ ] 端到端测试

### Phase E-4：打包发布

- [ ] 拆分 pyproject.toml — maop + maop-enterprise
- [ ] CI/CD 流水线 — 双包发布
- [ ] 文档 — 安装指南 + 迁移指南
- [ ] 个人版 → 企业版迁移工具

---

## 9. 风险与缓解

| 风险 | 概率 | 影响 | 缓解 |
|------|------|------|------|
| 核心包意外依赖企业版模块 | 中 | 高 | CI 中对 maop 包做 import 检查，禁止 import maop.enterprise |
| 降级路径数据不一致 | 中 | 高 | 降级时禁止多租户操作，SQLite 模式下租户 API 返回 503 |
| API 兼容性破坏 | 低 | 高 | 自动化测试：个人版 API 测试在企业版环境中也必须通过 |
| 企业版功能泄露到个人版 | 中 | 中 | has_feature() 守卫所有企业版端点 + 前端功能开关 |
| Vue Dashboard 维护成本 | 高 | 中 | Vue Dashboard 仅企业版独有页面，共享 API 层复用 |

---

## 10. 附录

### A. 版本检测流程图

```
应用启动
  │
  ├─ import maop.enterprise 成功？
  │     ├─ 是 → Edition.ENTERPRISE
  │     └─ 否 → Edition.PERSONAL
  │
  ├─ 企业版：检测 PostgreSQL
  │     ├─ 可用 → pg_backend
  │     └─ 不可用 → SQLite + WARNING + 多租户禁用
  │
  ├─ 企业版：检测 Redis
  │     ├─ 可用 → redis_cache
  │     └─ 不可用 → LRUCache + WARNING
  │
  └─ 加载对应 Dashboard
        ├─ 个人版 → 原生 JS
        └─ 企业版 → Vue 3
```

### B. 当前代码迁移映射

| 现有模块 | 迁移目标 | 说明 |
|---------|---------|------|
| core/tenant.py | enterprise/tenant_manager.py | 增强版多租户，个人版不可用 |
| core/auth.py | 保留在 core/ | 共享 JWT，企业版增加 SSO 扩展 |
| core/sandbox.py | 保留在 core/ | 共享工作目录沙箱，企业版增加容器沙箱 |
| dashboard/ | 保留 | 个人版前端 + 共享 API |
| dashboard-enterprise/ | → dashboard_enterprise/ | Vue 3 企业版前端 |
| config/settings.py | 重构 | edition 字段驱动所有配置 |

### C. 许可证

| 目录/包 | 许可证 |
|---------|--------|
| maop (核心 + 个人版) | Apache 2.0 |
| maop/enterprise/ | 商业许可（需购买授权） |
| maop-enterprise (PyPI 包) | 商业许可 |