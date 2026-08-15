# MAOP 技术白皮书

> Multi Agents Orchestration Platform — 架构、设计、性能技术白皮书
> 定位：编排与治理**外部 CLI agent** 的框架层（内置 31 个第三方 CLI 适配器）；内置 LLM 用于对话/分析/建议，不承担 agent 执行引擎角色。

## 第1章 架构概览

### 1.1 设计目标

MAOP 旨在解决企业级多智能体编排的核心挑战：

1. **多租户隔离**：租户间数据 / 配额 / 权限完全隔离
2. **合规性**：GDPR / CCPA 数据主体权利、DPA、处理记录
3. **可扩展性**：水平扩展、插件化后端
4. **可靠性**：circuit breaker、self-heal、graceful degradation
5. **可观测性**：OpenTelemetry、结构化日志、审计链

### 1.2 系统架构

图：MAOP 系统架构图

```
┌─────────────────────────────────────────────────┐
│                  Dashboard / CLI                 │
├─────────────────────────────────────────────────┤
│                   API Gateway                    │
│  (Auth · Rate Limit · Tenant Scope · Audit)     │
├──────┬──────┬──────┬──────┬──────┬──────────────┤
│Agent │ MCP  │Multi-│Evolu-│Memory│  Tenant      │
│Mgr   │Hub   │modal │tion  │Layer │  Hierarchy  │
├──────┴──────┴──────┴──────┴──────┴──────────────┤
│              Backends Abstraction                │
│  (SQLite · PostgreSQL · Redis · RabbitMQ · S3)  │
├─────────────────────────────────────────────────┤
│           LLM Providers / MCP Servers            │
└─────────────────────────────────────────────────┘
```

### 1.3 核心子系统

| 子系统 | 模块 | 职责 |
|--------|------|------|
| Agent 管理 | `core/agent_*` | 生命周期、注册、修复 |
| MCP 工具市场 | `E`core/mcp_*` | 工具发现、调用、审计 |
| 多模态推理 | `core/multimodal` | 统一接口 |
| 演化引擎 | `core/evolution_*` | 策略迭代 |
| 三层记忆 | `core/three_layer_memory` | 短期 / 长期 / 向量 |
| 租户隔离 | `core/tenant` | RLS、配额、审计 |
| 安全 | `core/security` | 认证、授权、沙箱 |
| 合规 | `core/tenant/compliance` | GDPR、DPA |
| LDAP | `core/security/ldap_provider` | AD 集成 |

## 第2章 多租户设计

### 2.1 隔离层次

1. **数据库层**：RLS（Row-Level Security）自动附加 `WHERE tenant_id = ?`
2. **配额层**：每租户独立的 token / request / storage 配额
3. **审计层**：每租户独立的 append-only 审计链（SHA-256 hash chain）
4. **组织层**：多级组织树 + 权限继承

### 2.2 组织层级

组织树使用**闭包表**（closure table）维护祖先-后代关系：

表：闭包表结构

| 列 |' | 类型 | 说明 |
|------|------|------|
| ancestor | TEXT | 祖先 org_id |
| descendant | TEXT | 后代 org_id |
| distance | INT | �%距离（0 = 自引用） |

闭包表的优势：

- O(1) 查询任意深度的祖先 / 后代
- 移动子树时只需更新闭包表，无需递归
- 支持循环检测（`distance > 0` 的自环）

### 2.3 权限继承算法

```
effective_permissions(org):
  accumulated = {}
  for ancestor in ancestors(org, order=distance_asc):
    if ancestor == org:
      self_denied = ancestor.denied
    accumulated |= ancestor.permissions
    if ancestor != org and ancestor.block_inherit:
      break
  accumulated -= self_denied
  return accumulated
```

## 第3章 GDPR 合规设计

### 3.1 数据主体权利

| GDPR 条款 | 方法 | 说明 |
|-----------|------|------|
| Art. 15 | `access_request` | 知情权 / 访问请求 |
| Art. 17 | `right_to_erasure` | 删除权 / 被遗忘权 |
| Art. 20 | `data_portability` | 数据可携权 |
| Art. 28 |" | `register_dpa` | 数据处理协议 |
| Art. 30 | `record_processing_activity` | 处理记录 |

### 3.2 级联删除顺序

为避免孤立引用，删除按以下顺序：

1. Sessions（引用 agents + memory）
2. Memory entries（引用 agents）
3. Agent configurations
4. RBAC grants
5. Audit entries（默认保留用于合规）

### 3.3 数据可携权

仅导出用户**主动提供**的数据：

- agents 配置（用户创建的）
- explicit memory（source='user'）
- sessions 元数据

排除系统推断 / 观察数据。

## 第4章 LDAP 集成设计

### 4.1 连接策略

- 优先使用 `ldap3`（纯 Python，跨平台）
- 回退到 `python-ldap`（Unix only）
- 支持注入 mock connection factory（测试）

### 4.2 同步策略

| 模式 | 触发 | 范围 |
|------|------|------|
| 全量 | 定时（如每小时） | 所有用户 + 停用缺失用户 |
| 增量 | 频繁（如每 5 分钟） | `modifyTimestamp >= since` |

### 4.3 组映射

支持三种F三种映射规则：

1. **精确匹配**：`group_dn_pattern == group_dn`（大小写不敏感）
2. **正则匹配**：`re.search(pattern, group_dn)`
3. **默认 role**：`is_default=True`，所有同步用户获得

## 第5章 性能设计

### 5.1 数据库

| 后端 | 适用场景 | 并发 | 性能 |
|------|---------|------|------|
| SQLite | 单机 / 小规模 | 1 writer + N readers | ~10K req/s |
| PostgreSQL | 分布式 | M writers + N readers | ~50K req/s |

### 5.2 缓存

- **语义缓存**：相似请求命中缓存（embedding 相似度）
- **布隆过滤器**：快速判断 key 是否存在
- **LRU + TTL**：多级缓存淘汰

### 5.3 异步 I/O

所有 I/O 密集型操作提供 async 包装：

```python
await mgr.check_quota_async(tenant_id, tokens_used=100)
await sync_users_async(provider)
await authenticate_async(provider, user, pass)
```

## 第6章 安全设计

### 6.1 认证

- API Key：SHA-256 hash 存储，永不存明文
- JWT：HMAC-SHA256，可配置 TTL
- LDAP：bind 验证，密码不记录日志

### 6.2 审计

- Append-only 日志（不可修改）
- SHA-256 hash chain（检测篡改）
- 每租户独立序列号

### 6.3 沙箱

- Agent 代码在隔离沙箱执行
- 文件系统 / 网络 / 系统调用限制
- 资源配额（CPU / memory / time）

## 第7章 可观测性

### 7.1 OpenTelemetry

所有关键操作自动埋点：

- HTTP 请求 span
- DB 查询 span
- Agent 调用 span
- MCP 工具调用 span

### 7.2 结构化日志

JSON 格式日志，包含：

- `timestamp`、`level`、`logger`
- `tenant_id`、`user_id`、`request_id`
- `action`、`resource`、`result`
- `duration_ms`、`error`（如有）