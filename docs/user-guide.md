# MAOP 用户指南

> Multi Agents Orchestration Platform — 多智能体编排平台用户指南

## 第1章 概述

### 1.1 MAOP 是什么

MAOP（Multi Agents Orchestration Platform）是一个企业级多智能体编排平台，提供：

- 多 LLM 后端统一接入与路由
- MCP（Model Context Protocol）工具市场
- 多租户隔离与 RBAC 权限
- GDPR 合规与数据主体权利
- LDAP/Active Directory 集成
- 组织层级树与权限继承
- Agent 生命周期管理与自愈
- 三层记忆系统（短期 / 长期 / 向量）

### 1.2 系统要求

| 组件 | 最低版本 | 推荐 |
|------|---------|------|
| Python | 3.10 | 3.11+ |
| SQLite | 3.35 | 3.40+ |
| PostgreSQL | 13 | 15+（可选） |
| Node.js | 18 | 20+（仅 Dashboard） |

## 第2章 安装

### 2.1 pip 安装

```bash
pip install maop
```

### 2.2 源码安装

命令示例：从源码安装 MAOP

```bash
git clone https://github.com/Levango7/MAOP.git
cd MAOP
pip install -e ".[dev]"     # 开发环境（含测试/构建工具链）
pip install -e ".[postgresql]"  # 生产环境使用 PostgreSQL 后端时追加
```

> 注：extra 名称以 `py/pyproject.toml` 的 `[project.optional-dependencies]` 为准，
> 实际提供 `dev` / `ml` / `otel` / `enterprise` / `etcd` / `saml` / `postgresql`。

### 2.3 可选依赖

| extras | 用途 |
|--------|------|
| `[postgresql]` | PostgreSQL 后端（SQLAlchemy/asyncpg/psycopg2/pgvector） |
| `[ml]` | 向量嵌入与 ANN 检索（sentence-transformers/hnswlib） |
| `[otel]` | OpenTelemetry 可观测性依赖 |
| `[enterprise]` | 企业版依赖（alembic/psycopg/asyncpg/redis/pika/lxml） |
| `[etcd]` | etcd 配置/协调后端 |
| `[saml]` | SAML SSO 集成（xmlsec） |
| `[dev]` | 开发与测试工具链 |

## 第3章 配置

### 3.1 环境变量

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `MAOP_DATA_DIR` | `./data` | 数据目录 |
| `MAOP_DB_BACKEND` | `sqlite` | 数据库后端 |
| `MAOP_DATABASE_URL` | — | 数据库 URL |
| `MAOP_SQLITE_BUSY_TIMEOUT_MS` | `10000` | SQLite busy timeout |
| `MAOP_DB_PER_MODULE` | `0` | 是否每模块独立 DB |
| `HF_HUB_OFFLINE` | `0` | HuggingFace 离线模式 |

### 3.2 配置文件

MAOP 使用 YAML 配置文件，默认位于 `config/` 目录：

- `config/agents.yaml` — Agent 定义
- `config/models.yaml` — LLM 后端配置

### 3.3 多租户配置

代码示例：创建租户（Python）

```python
from maop.core.tenant import TenantManager

mgr = TenantManager(root_dir="./data")
mgr.create_tenant(
    "tenant-001",
    display_name="Acme Corp",
    quota_tokens=1_000_000,
    allowed_models=["gpt-4", "claude-3"],
)
```

## 第4章 使用

### 4.1 启动服务

命令示例：启动 MAOP 服务（默认端口 9079）

```bash
maop start                  # 默认 127.0.0.1:9079
maop start --host 0.0.0.0 --port 9079
```

### 4.2 组织层级

代码示例：创建组织树（Python）

```python
from maop.core.tenant.hierarchy import OrganizationHierarchy

h = OrganizationHierarchy("./data/maop.db")
h.create_organization("root", name="总部")
h.create_organization("eng", name="工程部", parent_id="root")
h.create_organization("eng-ai", name="AI 团队", parent_id="eng")

# 设置权限（子组织自动继承）
h.set_permissions("root", ["read", "deploy"])
h.set_permissions("eng", ["?["ci/cd"])

# 检查有效权限
eff = h.get_effective_permissions("eng-ai")
print(eff.permissions)  # ['ci/cd', 'deploy', 'read']
```

### 4.3 GDPR 合规

代码示例：处理数据主体访问请求（Python）

```python
from maop.core.tenant.compliance import GDPRComplianceManager

gdpr = GDPRComplianceManager("./data")

# Article 15: 知情权
request, report = gdpr.access_request("user-123", tenant_id="t1")

# Article 17: 删除权
request, report = gdpr.right_to_erasure("user-123", tenant_id="t1")

# Article 20: 数据可携权
request, report = gdpr.data_portability("user-123", tenant_id="t1")
```

### 4.4 LDAP 集成

代码示例：从 LDAP 同步用户（Python）

```python
from maop.core.security.ldap_provider import (
    LDAPConfig, LDAPProvider, GroupRoleMapping,
)

config = LDAPConfig(
    server_url="ldap://dc.example.com:389",
    bind_dn="cn=admin,dc=example,dc=com",
    bind_password="secret",
    user_base_dn="ou=users,dc=example,dc=com",
)

provider = LDAPProvider(config, group_mappings=[
    GroupRoleMapping(
        group_dn_pattern="cn=admins,ou=groups,dc=example,dc=com",
        role="admin",
    ),
])

# 同步用户
result = provider.sync_users()
print(f"synced={result.synced}, created={result.created}")

# 认证
auth = provider.authenticate("alice", "password")
print(f"authenticated={auth.authenticated}, roles={auth.roles}")
```

## 第5章 故障排查

### 5.1 常见问题

| 问题 | 原因 | 解决方案 |
|------|------|---------|
| `WinError 5` 权限拒绝 | Windows temp 目录权限 | 设置 `MAOP_DATA_DIR` |
| `sqlite3.OperationalError: database is locked` | 并发写入冲突 | 增大 `MAOP_SQLITE_BUSY_TIMEOUT_MS` |
| `ImportError: ldap3` | 未安装 LDAP 库 | `pip install ldap3`（LDAP 为可选能力，非内置 extra） |
| `HuggingFace Hub 超时` | 网络问题 | 设置 `HF_HUB_OFFLINE=1` |

### 5.2 日志

日志默认输出到 stderr，可通过 `MAOP_LOG_LEVEL` 调整级别：

```bash
export MAOP_LOG_LEVEL=DEBUG
```