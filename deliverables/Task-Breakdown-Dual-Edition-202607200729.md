# MAOP 双路线架构 — 实施任务拆解

| 字段 | 值 |
|------|-----|
| 版本 | 1.0 |
| 日期 | 2026-07-20 |
| 关联 | PRD-Dual-Edition-Architecture-202607200729 |
| 关联 | UI-Dual-Edition-Layout-202607200729 |

---

## 任务总览

| 阶段 | 任务数 | 预估复杂度 | 依赖 |
|------|--------|-----------|------|
| E-1 版本注册表框架 | 8 | 中 | 无 |
| E-2 企业版扩展包骨架 | 10 | 高 | E-1 |
| E-3 前端双路线实现 | 9 | 高 | E-1, E-2 |
| E-4 打包发布与文档 | 7 | 中 | E-1, E-2, E-3 |

**总计：34 个子任务**

---

## Phase E-1：版本注册表框架

> 目标：建立 edition.py 唯一真相源，所有模块通过 has_feature() 判断功能开关

### E-1.1 实现 edition.py 核心注册表

- **优先级**：P0（阻塞后续所有任务）
- **复杂度**：中
- **文件**：`py/maop/config/edition.py`
- **子任务**：
  1. 定义 `Edition` 枚举（PERSONAL / ENTERPRISE）
  2. 定义 `FeatureFlag` 数据类（name, personal, enterprise）
  3. 实现 `FEATURES` 注册表（12+ 功能开关）
  4. 实现 `get_edition()` / `set_edition()` / `has_feature()` / `edition_features()`
  5. 实现 `detect_edition()` — `import maop.enterprise` 检测
- **验收**：单元测试覆盖所有功能开关 + 版本检测

### E-1.2 重构 settings.py — edition 驱动配置

- **优先级**：P0
- **复杂度**：低
- **文件**：`py/maop/config/settings.py`
- **子任务**：
  1. 移除硬编码的 `edition` 字段，改用 `edition.py` 的 `get_edition()`
  2. `edition_features()` 改用 `edition.py` 的 `edition_features()`
  3. `edition_defaults()` 改用 `has_feature()` 判断后端选择
- **验收**：现有测试通过 + edition 切换后配置正确

### E-1.3 重构 backends.py — edition-aware 后端选择

- **优先级**：P1
- **复杂度**：低
- **文件**：`py/maop/core/backends.py`
- **子任务**：
  1. `_edition_defaults()` 改用 `has_feature("pg_backend")` 等判断
  2. 企业版检测到 PostgreSQL 不可用时降级 + WARNING
- **验收**：个人版选 SQLite，企业版选 PostgreSQL，降级测试

### E-1.4 重构 server.py — 条件加载企业版路由

- **优先级**：P1
- **复杂度**：中
- **文件**：`py/maop/dashboard/server.py`
- **子任务**：
  1. 用 `has_feature("rbac")` 控制是否加载 RBAC 路由
  2. 用 `has_feature("multi_tenant")` 控制是否加载租户路由
  3. 用 `has_feature("vue_dashboard")` 控制前端目录选择
  4. 企业版独有端点在个人版返回 404 + 明确错误信息
- **验收**：个人版不加载企业版路由，企业版正确加载

### E-1.5 实现 enterprise/__init__.py — 自动注册

- **优先级**：P0
- **复杂度**：低
- **文件**：`py/maop/enterprise/__init__.py`
- **子任务**：
  1. `set_edition(Edition.ENTERPRISE)` 自动执行
  2. 检测 PostgreSQL/Redis 可用性，不可用时打印 WARNING
  3. 降级时禁用多租户功能（`has_feature("multi_tenant")` 返回 False）
- **验收**：安装 maop-enterprise 后自动切换版本

### E-1.6 降级检测与日志

- **优先级**：P1
- **复杂度**：低
- **文件**：`py/maop/enterprise/__init__.py`
- **子任务**：
  1. 检测 PostgreSQL 可用性 → 不可用时 WARNING + 降级 SQLite
  2. 检测 Redis 可用性 → 不可用时 WARNING + 降级 LRUCache
  3. 降级时多租户标记不可用 + WARNING
- **验收**：降级场景日志输出正确

### E-1.7 API 404 守卫

- **优先级**：P1
- **复杂度**：低
- **文件**：`py/maop/dashboard/server.py` 或独立中间件
- **子任务**：
  1. 企业版独有端点列表注册到 edition.py
  2. 个人版请求企业版端点时返回 `404 + {"error": "This endpoint requires MAOP Enterprise Edition"}`
- **验收**：个人版调用 /api/tenants 返回 404

### E-1.8 单元测试

- **优先级**：P0
- **复杂度**：中
- **文件**：`py/tests/test_edition.py`（扩展）
- **子任务**：
  1. 测试 edition 检测（有/无 maop.enterprise）
  2. 测试 has_feature() 在两个版本下的返回值
  3. 测试降级行为（PostgreSQL 不可用）
  4. 测试 API 404 守卫
  5. 测试 settings.py edition 驱动配置
- **验收**：所有测试通过

---

## Phase E-2：企业版扩展包骨架

> 目标：实现企业版独有模块，每个模块都是 core/ 对应模块的增强版

### E-2.1 enterprise/rbac.py — RBAC 权限控制

- **优先级**：P0
- **复杂度**：高
- **文件**：`py/maop/enterprise/rbac.py`
- **子任务**：
  1. 定义 Role / Permission / RolePermission Pydantic 模型
  2. 实现 RBACManager — 角色增删改查 + 权限矩阵
  3. 实现 FastAPI 依赖注入 — `require_role("admin")` 装饰器
  4. RBAC 数据持久化（PostgreSQL 优先，降级 SQLite）
  5. 与现有 auth.py JWT 集成 — token 中携带角色信息
- **验收**：角色 CRUD + 权限检查 + API 端点保护

### E-2.2 enterprise/tenant_manager.py — 多租户管理器

- **优先级**：P0
- **复杂度**：高
- **文件**：`py/maop/enterprise/tenant_manager.py`
- **子任务**：
  1. 重构 core/tenant.py — 提取共享接口
  2. 实现 TenantManager — 租户 CRUD + 配额 + 隔离
  3. PostgreSQL 模式 — schema-per-tenant 隔离
  4. 降级 SQLite 模式 — 多租户不可用，API 返回 503
  5. 租户配额先查后写（已修复）+ 审计日志
- **验收**：租户 CRUD + 配额检查 + 降级 503

### E-2.3 enterprise/pg_backend.py — PostgreSQL 后端

- **优先级**：P1
- **复杂度**：高
- **文件**：`py/maop/enterprise/pg_backend.py`
- **子任务**：
  1. 实现 PGConnectionPool — psycopg2 连接池
  2. 实现 PGBackend — 与 db_utils.py sqlite_connect() 同接口
  3. Schema 迁移 — SQLite → PostgreSQL DDL 转换
  4. 数据迁移工具 — SQLite 数据导入 PostgreSQL
- **验收**：CRUD 操作在 PostgreSQL 上正常工作

### E-2.4 enterprise/redis_cache.py — Redis 缓存后端

- **优先级**：P2
- **复杂度**：中
- **文件**：`py/maop/enterprise/redis_cache.py`
- **子任务**：
  1. 实现 RedisCache — 与 LRUCache 同接口（put/get/delete/contains/clear）
  2. TTL 支持 — Redis EX 命令
  3. 降级到 LRUCache — Redis 不可用时自动切换
- **验收**：缓存操作在 Redis 上正常 + 降级测试

### E-2.5 enterprise/rabbitmq_queue.py — RabbitMQ 消息队列

- **优先级**：P2
- **复杂度**：中
- **文件**：`py/maop/enterprise/rabbitmq_queue.py`
- **子任务**：
  1. 实现 RabbitMQQueue — 与 MessageQueue 同接口（publish/consume/ack）
  2. 降级到 SQLite MessageQueue
- **验收**：消息收发在 RabbitMQ 上正常 + 降级测试

### E-2.6 enterprise/sso.py — SSO/LDAP 集成

- **优先级**：P2
- **复杂度**：高
- **文件**：`py/maop/enterprise/sso.py`
- **子任务**：
  1. OIDC 客户端 — Discovery + Token 验证 + UserInfo
  2. LDAP 客户端 — Bind + Search + Group 映射
  3. SSO 用户自动同步到 RBAC 角色
  4. 配置管理 — SSO 设置持久化
- **验收**：OIDC 登录流程 + LDAP 用户查询

### E-2.7 enterprise/audit.py — 审计合规引擎

- **优先级**：P1
- **复杂度**：中
- **文件**：`py/maop/enterprise/audit.py`
- **子任务**：
  1. 结构化审计日志 — 谁/何时/做了什么/对什么/结果
  2. 不可篡改存储 — PostgreSQL INSERT ONLY 表
  3. 审计查询 API — 按时间/租户/操作类型/用户筛选
  4. 合规报告导出 — JSON/CSV
- **验收**：审计日志写入 + 查询 + 导出

### E-2.8 enterprise/tls.py — TLS 配置管理

- **优先级**：P2
- **复杂度**：低
- **文件**：`py/maop/enterprise/tls.py`
- **子任务**：
  1. TLS 证书加载与验证
  2. 强制 HTTPS 中间件
  3. 证书过期监控
- **验收**：HTTPS 启动 + HTTP 重定向

### E-2.9 enterprise/container.py — 容器化支持

- **优先级**：P3
- **复杂度**：中
- **文件**：`py/maop/enterprise/container.py`
- **子任务**：
  1. Dockerfile — 多阶段构建
  2. docker-compose.yml — MAOP + PostgreSQL + Redis + RabbitMQ
  3. 健康检查端点 — /health
- **验收**：docker-compose up 后系统正常运行

### E-2.10 enterprise/ha.py — 高可用管理

- **优先级**：P3
- **复杂度**：高
- **文件**：`py/maop/enterprise/ha.py`
- **子任务**：
  1. 健康检查调度器 — 定期探测 Agent 可用性
  2. 断路器真实探测 — 调用 circuit_breaker.health_check(probe=...)
  3. 自动恢复策略
- **验收**：Agent 故障后自动恢复

---

## Phase E-3：前端双路线实现

> 目标：个人版精简 UI + 企业版 Vue 3 Dashboard

### E-3.1 个人版侧边栏精简

- **优先级**：P1
- **复杂度**：低
- **文件**：`dashboard/index.html`
- **子任务**：
  1. 移除说明类导航（四大工程、角色、模块、架构、工作流程）
  2. 移除 Agent升级、自进化、搜索、提示词
  3. 新增成本统计、设置导航项
  4. 底部状态栏显示 "SQLite · 本地模式"
- **验收**：侧边栏只保留 13 个页面导航

### E-3.2 个人版设置页实现

- **优先级**：P1
- **复杂度**：中
- **文件**：`dashboard/index.html` + `dashboard/js/`
- **子任务**：
  1. 版本信息卡片 — 调用 /api/info/edition
  2. BYOK 密钥状态卡片 — 调用 /api/byok/status
  3. 数据管理按钮 — 导出/导入/清理/备份
  4. 升级提示 — "pip install maop-enterprise" 提示
- **验收**：设置页完整可用

### E-3.3 个人版成本统计页增强

- **优先级**：P2
- **复杂度**：中
- **文件**：`dashboard/index.html` + `dashboard/js/`
- **子任务**：
  1. 统计卡片 — 今日/本周/本月/总成本
  2. 成本趋势折线图 — Chart.js
  3. Agent 成本排行横向柱状图
- **验收**：成本数据可视化

### E-3.4 企业版 Vue 3 Dashboard 框架搭建

- **优先级**：P0
- **复杂度**：中
- **文件**：`dashboard-enterprise/`
- **子任务**：
  1. 完善 App.vue — 侧边栏分组 + 企业版标识
  2. 完善 Router — 17 条路由（13 共享 + 4 独有）
  3. Pinia stores — edition store + api store + auth store
  4. 共享 API 服务层 — 与个人版调用同一套 API
- **验收**：Vue Dashboard 启动 + 侧边栏导航可用

### E-3.5 企业版共享页面实现（13个）

- **优先级**：P1
- **复杂度**：高
- **文件**：`dashboard-enterprise/src/views/`
- **子任务**：
  1. Overview.vue — 概览 + 租户统计
  2. Control.vue — 控制面板
  3. Chat.vue — 对话
  4. Agents.vue — Agent 管理
  5. Memory.vue — 记忆系统
  6. Monitor.vue — 监控
  7. Cost.vue — 成本统计 + 租户成本拆分
  8. Settings.vue — 增强版设置
  9. Skills.vue — Skills
  10. MCP.vue — MCP
  11. Models.vue — 大模型
  12. Logs.vue — 日志 + 结构化筛选
  13. Workflow.vue — 工作流
- **验收**：13 个共享页面数据展示正常

### E-3.6 企业版 RBAC 页面

- **优先级**：P1
- **复杂度**：中
- **文件**：`dashboard-enterprise/src/views/RBAC.vue`
- **子任务**：
  1. 角色列表 + 新建/编辑/删除
  2. 权限矩阵编辑
  3. 角色成员管理
- **验收**：RBAC CRUD 操作

### E-3.7 企业版租户管理页面

- **优先级**：P1
- **复杂度**：中
- **文件**：`dashboard-enterprise/src/views/Tenants.vue`
- **子任务**：
  1. 租户列表 + 统计卡片
  2. 租户 CRUD + 配额编辑
  3. 资源使用趋势图
- **验收**：租户管理完整可用

### E-3.8 企业版审计合规页面

- **优先级**：P2
- **复杂度**：中
- **文件**：`dashboard-enterprise/src/views/Audit.vue`
- **子任务**：
  1. 审计日志列表 + 多维筛选
  2. 操作类型分布饼图
  3. 合规状态卡片
  4. 报告导出按钮
- **验收**：审计日志查询 + 导出

### E-3.9 企业版 SSO 配置页面

- **优先级**：P2
- **复杂度**：中
- **文件**：`dashboard-enterprise/src/views/SSO.vue`（新建）
- **子任务**：
  1. OIDC 配置表单
  2. LDAP 配置表单
  3. 连接测试按钮
  4. 连接状态展示
- **验收**：SSO 配置保存 + 测试连接

---

## Phase E-4：打包发布与文档

> 目标：双包发布到 PyPI，文档完善

### E-4.1 拆分 pyproject.toml

- **优先级**：P0
- **复杂度**：中
- **文件**：`py/pyproject.toml` + `py/enterprise-pyproject.toml`
- **子任务**：
  1. maop 核心包 — 移除 psycopg2/redis/pika/python-ldap/cryptography 依赖
  2. maop-enterprise 扩展包 — 依赖 maop + 企业版依赖
  3. 入口点配置 — maop 命令行工具
- **验收**：`pip install -e .` 两个包分别安装成功

### E-4.2 CI/CD 双包发布流水线

- **优先级**：P1
- **复杂度**：中
- **文件**：`.github/workflows/`
- **子任务**：
  1. maop 包构建 + 测试 + 发布到 PyPI
  2. maop-enterprise 包构建 + 测试 + 发布到 PyPI
  3. import 检查 — maop 包不能 import maop.enterprise
- **验收**：CI 流水线通过

### E-4.3 个人版 → 企业版迁移工具

- **优先级**：P1
- **复杂度**：中
- **文件**：`py/maop/enterprise/migrate.py`
- **子任务**：
  1. SQLite → PostgreSQL 数据迁移
  2. 配置文件转换 — agents.yaml 无需改，settings 需补充
  3. 迁移验证 — 数据完整性检查
- **验收**：迁移后数据完整

### E-4.4 安装指南文档

- **优先级**：P1
- **复杂度**：低
- **文件**：`docs/install-guide.md`
- **子任务**：
  1. 个人版安装指南 — pip install maop
  2. 企业版安装指南 — pip install maop-enterprise + PostgreSQL/Redis 配置
  3. 降级场景说明
  4. 升级路径 — 个人版 → 企业版
- **验收**：按文档可完成安装

### E-4.5 API 文档更新

- **优先级**：P2
- **复杂度**：低
- **文件**：`docs/api-reference.md`
- **子任务**：
  1. 标注每个端点的版本要求（共享/企业版独有）
  2. 企业版独有端点文档
  3. 降级场景 API 行为说明
- **验收**：API 文档完整

### E-04.6 许可证文件

- **优先级**：P1
- **复杂度**：低
- **文件**：`LICENSE` + `enterprise/LICENSE`
- **子任务**：
  1. maop 核心 — Apache 2.0
  2. maop/enterprise/ — 商业许可
  3. README 中许可证说明
- **验收**：许可证文件正确

### E-4.7 端到端集成测试

- **优先级**：P0
- **复杂度**：高
- **文件**：`py/tests/test_dual_edition.py`
- **子任务**：
  1. 个人版完整流程测试 — 安装→启动→使用→关闭
  2. 企业版完整流程测试 — 安装→配置→启动→多租户操作
  3. 降级场景测试 — 无 PostgreSQL 时企业版降级
  4. API 兼容性测试 — 个人版 API 在企业版环境中通过
  5. 迁移测试 — 个人版数据迁移到企业版
- **验收**：所有集成测试通过

---

## 执行顺序（依赖关系）

```
E-1.1 ──→ E-1.2 ──→ E-1.3 ──→ E-1.4
  │                                    │
  └──→ E-1.5 ──→ E-1.6               │
                                      │
E-1.7 ←──────────────────────────────┘
E-1.8 ←── E-1.1 ~ E-1.7 全部完成后

E-2.1 ←── E-1.1 (has_feature)
E-2.2 ←── E-1.1 + E-2.1 (RBAC)
E-2.3 ←── E-1.1
E-2.4 ←── E-1.1
E-2.5 ←── E-1.1
E-2.6 ←── E-2.1 (RBAC)
E-2.7 ←── E-1.1
E-2.8 ←── E-1.1
E-2.9 ←── E-2.3 + E-2.4 (PG + Redis)
E-2.10 ←── E-1.1

E-3.1 ←── E-1.1
E-3.2 ←── E-1.4 (API 端点)
E-3.3 ←── E-1.4
E-3.4 ←── E-1.1
E-3.5 ←── E-3.4 + E-1.4
E-3.6 ←── E-2.1 + E-3.4
E-3.7 ←── E-2.2 + E-3.4
E-3.8 ←── E-2.7 + E-3.4
E-3.9 ←── E-2.6 + E-3.4

E-4.1 ←── E-2.1 ~ E-2.8
E-4.2 ←── E-4.1
E-4.3 ←── E-2.3
E-4.4 ←── E-4.1
E-4.5 ←── E-1.7 + E-2.1 ~ E-2.8
E-4.6 ←── 无依赖
E-4.7 ←── E-4.1 + 全部 E-2 + E-3
```

## 关键路径

```
E-1.1 → E-1.5 → E-2.1 → E-2.2 → E-3.4 → E-3.5 → E-4.1 → E-4.7
```

**建议先完成 E-1 全部（8 个子任务），再并行推进 E-2 和 E-3。**