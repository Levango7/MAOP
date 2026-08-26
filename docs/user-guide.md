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

### 1.3 版本说明

MAOP 个人版（v5.1.0）为单机/小团队设计，包含完整的 Agent 编排、记忆系统、工具集成能力。

**企业版功能**（RBAC、多租户、SSO、配额管理、审计日志、分布式执行等）由独立的商业包 **MAOS**（`maop-enterprise`）提供，需商业授权。个人版不包含这些功能。

如需企业版功能，请联系获取 MAOS 商业 license。

## 第2章 安装

### 2.1 pip 安装

```bash
pip install maop
```

### 2.2 源码安装

命令示例：从源码安装 MAOP

```bash
git clone https://github.com/your-org/MAOP.git
cd MAOP
pip install -e ".[all]"
```

### 2.3 可选依赖

| extras | 用途 |
|--------|------|
| `[postgresql]` | PostgreSQL 后端 |
| `[ldap]` | LDAP/AD 集成（ldap3） |
| `[vector]` | 向量存储（pgvector） |
| `[dashboard]` | Web Dashboard |
| `[all]` | 全部可选依赖 |

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
- `config/tenants.yaml` — 租户配置

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

命令示例：启动 MAOP API 服务

```bash
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
print(f"authenticated={auth4uth.authenticated}, roles={auth.roles}")
```

### 4.5 分布式执行

分布式执行（多机 worker / Redis Streams 任务队列 / DAG 节点级分发）是企业版（MAOS HA）特性。

个人版仅支持单进程执行，适用于单机/小团队场景。如需分布式执行能力，请升级到企业版。

在个人版下执行 `maop worker start` 会明确提示"需企业版授权"并以非零退出码退出，不会静默启动分布式 worker。

## 第5章 故障排查

### 5.1 常见问题

| 问题 | 原因 | 解决方案 |
|------|------|---------|
| `WinError 5` 权限拒绝 | Windows temp 目录权限 | 设置 `MAOP_DATA_DIR` |
| `sqlite3.OperationalError: database is locked` | 并发写入冲突 | 增大 `MAOP_SQLITE_BUSY_TIMEOUT_MS` |
| `ImportError: ldap3` | 未安装 LDAP 库 | `pip install maop[ldap]` |
| `HuggingFace Hub 超时` | 网络问题 | 设置 `HF_HUB_OFFLINE=1` |

### 5.2 日志

日志默认输出到 stderr，可通过 `MAOP_LOG_LEVEL` 调整级别：

```bash
export MAOP_LOG_LEVEL=DEBUG
```