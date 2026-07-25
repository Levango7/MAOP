# ADR-016: Dual-Edition Architecture (Personal / Enterprise)

**Status**: Active  
**Date**: 2026-07-25  
**Decision Owner**: MAOP Architecture Team  
**Related**: PRD-Dual-Edition-Architecture-202607200729, ADR-013 (Agent LLM Direct + CLI Fallback)

## Context

### Background

MAOP 项目从 2026-07-20 起明确双线产品策略：
- **个人版 (Personal)**：MIT 许可，零配置开箱即用，面向个人开发者和小团队
- **企业版 (Enterprise)**：Commercial 许可，面向企业客户，提供 RBAC/多租户/SSO/审计/HA 等企业级能力

### Problem

需要设计一个架构，使得：
1. 个人版和企业版共享同一套核心代码（避免双分支维护成本）
2. 企业版功能可以被技术性 gate（不仅是法律条款）
3. 个人版用户能零摩擦升级到企业版（无需迁移数据/重写配置）
4. 商业边界清晰，便于销售和合规

### Constraints

- 不能引入运行时 license 服务器依赖（客户可能在内网部署）
- 不能破坏个人版的零配置体验
- 必须支持渐进式 feature rollout（per-feature 粒度覆盖）
- 必须向后兼容（现有 honor-system 部署不能突然失效）

## Decision

### 1. 单一代码库 + 运行时 Edition 检测

采用**单一代码库 + 运行时 edition 检测**，而非双分支或编译时区分：

```
maop (核心包, MIT)
├── maop/core/           # 共享核心
├── maop/config/edition.py  # Edition 注册表（唯一真相源）
└── maop/enterprise/     # 企业版扩展（仅 maop-enterprise 包含）
```

**检测优先级**（见 `detect_edition()`）：
1. `set_edition()` 程序覆盖（测试/灰度）
2. `MAOP_EDITION` 环境变量
3. `maop.enterprise` 包可导入性自动探测
4. 默认 PERSONAL

### 2. FeatureFlag 枚举作为唯一 Gate

所有 edition 相关的能力差异通过 `FeatureFlag` 枚举统一 gate：

```python
from maop.config.edition import has_feature, FeatureFlag, require_feature

if has_feature(FeatureFlag.RBAC):
    # 企业版逻辑
else:
    # 个人版逻辑或跳过

require_feature(FeatureFlag.SSO)  # 个人版抛 FeatureNotAvailable
```

**禁止**：在其他模块直接检查 `get_edition() == ENTERPRISE`，必须通过 `has_feature()` / `require_feature()`。

`FeatureFlag` 当前共 24 个枚举值，按 edition 分组：
- **个人版独占**（10 个）：COST_TRACKING / CIRCUIT_BREAKER / MEMORY_STORE / HOT_RELOAD / HOOKS / PLUGIN_SYSTEM / MCP_HUB / VECTOR_SEARCH / REACT_LOOP / BUDGET_GUARD
- **企业版独占**（15 个）：RBAC / AUDIT_LOG / MULTI_USER / SSO / DASHBOARD_ANALYTICS / VUE_DASHBOARD / POSTGRESQL / REDIS / RABBITMQ / VAULT / ETCD / TENANT_ISOLATION / TLS_AUTO / AUTH_AUTO / N8N_INTEGRATION
- **企业版 = 个人版 ∪ 企业版独占**（即企业版包含所有功能）

### 3. License 校验三态降级

企业版检测时集成 Ed25519 license 校验（ADR-016 同期实现）：

| 场景 | 行为 |
|------|------|
| 无 license key | honor-system + 警告（向后兼容） |
| key 有效 | ENTERPRISE |
| key 无效 | 降级 PERSONAL + error 日志 |

7 天宽限期避免服务突然中断。详见 `license-issuance-guide.md`。

降级链路：`_detect_with_license_check()` 捕获 `LicenseError` → 调用 `record_degradation("license", "enterprise", "personal", "license_invalid")` → 返回 `Edition.PERSONAL`。

### 4. 后端默认值差异

| 后端 | 个人版 | 企业版 |
|------|--------|--------|
| storage | sqlite | postgresql |
| cache | memory | redis |
| queue | sqlite | rabbitmq |
| kv | sqlite | etcd |
| secret | local | vault |

企业版后端不可用时自动降级到个人版后端（通过 `record_degradation()` 记录）。后端默认值由 `backend_defaults()` 返回。

### 5. 双包发布

- `maop`（PyPI, MIT）：核心 + 个人版功能
- `maop-enterprise`（私有分发, Commercial）：依赖 `maop`，包含 `maop/enterprise/` 模块

`pip install maop` → 个人版  
`pip install maop-enterprise` → 企业版（自动依赖 maop）

`maop/enterprise/__init__.py` 在 import 时调用 `set_edition(Edition.ENTERPRISE)`，这是企业版包"存在即激活"的机制。

### 6. API 兼容性

个人版 API 是企业版 API 的严格子集。企业版路由在个人版中返回 404（通过 `has_feature()` gate）。

`/api/info/edition` 端点（由 `edition_info()` 提供）暴露当前 edition、功能列表、后端默认值、是否安装企业版包、降级记录等信息，便于运维和排障。

## Alternatives Considered

### A. 双分支（personal/main, enterprise/main）

**否决原因**：维护成本高，功能同步困难，容易产生分歧。

### B. 编译时区分（#ifdef ENTERPRISE）

**否决原因**：Python 不支持编译时区分；且需要分发两套二进制，违背单一代码库原则。

### C. Feature Flag 服务（如 LaunchDarkly）

**否决原因**：引入外部依赖，违背"内网可部署"约束；且成本高。

### D. 纯法律条款（无技术 gate）

**否决原因**：商业边界模糊，难以防止未授权使用；当前 license 校验正是为了补充这一缺口。

## Consequences

### 正面

- ✅ 单一代码库，维护成本最低
- ✅ 个人版用户零摩擦升级（pip install maop-enterprise 即可）
- ✅ FeatureFlag 提供 per-feature 粒度控制，支持渐进式 rollout
- ✅ License 校验提供技术性商业边界
- ✅ 向后兼容现有 honor-system 部署

### 负面

- ⚠️ 企业版代码在个人版仓库中可见（虽然受 Commercial 许可约束）
- ⚠️ 无在线 license 撤销机制（依赖客户配合删除 key）
- ⚠️ Edition 检测有轻微运行时开销（首次 import 时）
- ⚠️ FeatureFlag 枚举膨胀风险（需定期审查）

### 缓解措施

- 企业版代码可见性：通过 Commercial 许可条款法律约束 + 未来可考虑代码混淆
- License 撤销：未来实现 CRL（Certificate Revocation List）机制
- 性能：edition 检测结果缓存（`_current_edition` 全局变量）
- FeatureFlag 膨胀：定期审查，合并相似 flag

## Implementation

### 已实现

| 组件 | 状态 |
|------|------|
| `config/edition.py` Edition 注册表 | ✅ 完整 |
| `enterprise/` 8 个企业模块（rbac/tenant/audit/sso/ha/container/tls_auto/pg_persist） | ✅ 完整 |
| `enterprise/license.py` Ed25519 license 校验 | ✅ 完整（2026-07-25） |
| `enterprise/n8n.py` n8n 集成 | ✅ 完整（2026-07-25） |
| Settings 集成（edition 字段 + defaults） | ✅ 完整 |
| Dashboard edition-gated 路由 | ✅ 完整 |
| 前端 edition store | ✅ 完整 |
| 双 pyproject 打包 | ✅ 完整 |
| Docker profiles（postgres/redis/vault/n8n/...） | ✅ 完整 |
| 测试套件（edition + 8 企业模块 + license + n8n） | ✅ 完整 |

### 待完善

| 项 | 优先级 | 说明 |
|----|--------|------|
| SAML SSO 完整实现 | Medium | 当前仅 stub session |
| RabbitMQ 队列后端 | Low | Phase 3.4 规划 |
| etcd/Consul KV 后端 | Low | Phase 3.4 规划 |
| License 在线撤销（CRL） | Low | 未来增强 |
| 前端 edition 切换 UI | Low | 当前需手动配置环境变量 |

## References

- [PRD: Dual-Edition Architecture](../../deliverables/PRD-Dual-Edition-Architecture-202607200729.md)
- [Task Breakdown: Dual-Edition](../../deliverables/Task-Breakdown-Dual-Edition-202607200729.md)
- [UI Layout: Dual-Edition](../../deliverables/UI-Dual-Edition-Layout-202607200729.md)
- [License Issuance Guide](../enterprise/license-issuance-guide.md)
- ADR-013: Agent LLM Direct + CLI Fallback
- `py/maop/config/edition.py` — Edition 注册表（唯一真相源）
- `py/maop/enterprise/license.py` — License 校验实现
