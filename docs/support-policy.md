# MAOP 支持政策

> 适用版本：v5.0.0+ ｜ 生效日期：2026-08-11 ｜ 文档维护：MAOP 团队

## 第 1 章 支持层级

MAOP 提供三级支持体系，按客户订阅套餐自动匹配：

| 层级 | 适用套餐 | 渠道 | 工作时间 | SLA 响应 |
|------|----------|------|----------|----------|
| L1 社区支持 | 个人版（MIT） | GitHub Issues、Discussions | 社区驱动，无承诺 | 尽力而为 |
| L2 标准支持 | Business 套餐 | 邮件 + 工单系统 | 7×24 接收，工作时间处理 | 参见 [SLA](sla.md) §2.5 |
| L3 企业支持 | Enterprise 套餐 | 专属 TAM + 邮件 + 电话 + 工单 | 7×24 全天候 | 参见 [SLA](sla.md) §2.5 |

### 1.1 L1 社区支持

- **渠道**：[GitHub Issues](https://github.com/maop/maop/issues)（bug 报告）、[GitHub Discussions](https://github.com/maop/maop/discussions)（问答、最佳实践）。
- **响应**：由社区贡献者和 MAOP 团队志愿回复，无响应时间承诺。
- **范围**：使用咨询、bug 修复、功能请求。安全漏洞请按 [SECURITY.md](../SECURITY.md) 私密报告，**勿**在公开 Issue 提交。
- **生命周期**：Issue 30 天无活动自动标记 `stale`，再 7 天无活动关闭。

### 1.2 L2 标准支持

- **渠道**：`support@maop.io` + 客户门户工单系统。
- **工作时间**：工单 7×24 接收，处理时间为北京时间 09:00–22:00（含节假日值班）。
- **范围**：
  - 平台故障诊断与修复（MAOP 自身代码问题）。
  - 配置咨询（`MAOP_*` 环境变量、docker-compose、K8s 部署）。
  - 升级与迁移指导（参见 [ROADMAP.md](../ROADMAP.md)）。
  - 性能调优建议（基于 [docs/capacity-planning.md](capacity-planning.md)）。
- **不含**：
  - 客户自定义插件的代码开发（属 L3 专属服务）。
  - 上游 LLM 提供商（OpenAI/Anthropic）的账户问题。
  - 客户自有基础设施（K8s 集群、PG 运维）故障。

### 1.3 L3 企业支持

- **渠道**：专属技术客户经理（TAM）+ `urgent@maop.io` + 7×24 电话热线 + 工单。
- **TAM 职责**：
  - 首次响应后 5 工作日内完成 onboarding 评估。
  - 每季度主动巡检并出具健康报告。
  - 协调内部研发资源处理客户 P0/P1 事件。
- **范围**（在 L2 基础上扩展）：
  - 自定义插件代码级支持（含 debug、patch）。
  - 私有部署架构评审（含 PG 高可用、Redis 哨兵、Vault 集成）。
  - 安全事件应急响应（含漏洞分析、补丁优先交付）。
  - 季度架构评审与容量规划复盘。

## 第 2 章 工单优先级

### 2.1 优先级定义

| 优先级 | 判定标准 | 示例 |
|--------|----------|------|
| **P0 紧急** | 生产环境完全不可用；数据丢失风险 | 所有 API 5xx；PG 主库不可用且未自动切换 |
| **P1 高** | 核心功能不可用，有变通方案；安全可疑事件 | 某模型不可调用但可切其他模型；疑似越权访问 |
| **P2 中** | 非核心功能异常；性能劣化但未违约 | 仪表盘某图表不渲染；P95 延迟从 200ms 升至 500ms |
| **P3 低** | 咨询、功能请求、文档改进 | "如何配置 LDAP？"；希望支持某新 LLM 提供商 |

### 2.2 优先级调整

- 客户初次提交时自选优先级，MAOP 支持团队在 30 分钟内（工作时间）复核。
- 若判定与客户自选不符，MAOP 与客户协商调整并在工单中记录依据。
- P0 工单自动升级至 TAM 与值班研发负责人。

### 2.3 升级路径

```
P0 → TAM + 研发负责人 + 运维负责人（同时通知）
P1 → TAM + 模块负责人
P2 → 工单工程师（24 小时内分配）
P3 → 工单工程师（2 工作日内分配）
```

## 第 3 章 工单生命周期

### 3.1 状态流转

```
新建（New） → 已接收（Accepted） → 处理中（In Progress）
   → 待客户回复（Waiting on Customer） → 已解决（Resolved） → 已关闭（Closed）
```

- **新建 → 已接收**：L2/L3 需在 SLA 响应时间内完成。
- **已接收 → 处理中**：分配责任人后立即流转。
- **处理中 → 待客户回复**：需客户提供日志、复现步骤等信息时流转。
- **待客户回复**：若客户 7 天无回复，自动发提醒；14 天无回复自动关闭（可重开）。
- **已解决 → 已关闭**：客户确认解决后关闭；若 7 天无回复自动关闭。

### 3.2 工单内容要求

提交工单时请提供以下信息以加速处理：

1. **环境信息**：MAOP 版本（`maop --version`）、部署方式（docker-compose / K8s / 裸机）、Edition（personal/enterprise）。
2. **问题现象**：预期行为 vs 实际行为，是否可稳定复现。
3. **复现步骤**：最小复现命令或配置。
4. **日志**：相关日志片段（JSON 格式，参见 `MAOP_JSON_LOG=1`）。**注意脱敏**——勿粘贴 JWT、API key、`MAOP_JWT_SECRET` 等密钥。
5. **影响范围**：受影响的租户、用户数、业务流程。
6. **优先级诉求**：客户初步判定（参见 §2.1）。

## 第 4 章 自助支持资源

### 4.1 文档

| 文档 | 内容 |
|------|------|
| [README.md](../README.md) | 快速入门、架构总览 |
| [docs/deployment.md](deployment.md) | 部署指南（docker-compose / K8s） |
| [docs/troubleshooting.md](troubleshooting.md) | 常见问题排查 |
| [docs/runbook.md](runbook.md) | 运维手册（PG 故障切换等） |
| [docs/capacity-planning.md](capacity-planning.md) | 容量规划与性能基准 |
| [docs/api-reference.md](api-reference.md) | API 参考 |
| [docs/enterprise/saml-sso-guide.md](enterprise/saml-sso-guide.md) | SAML SSO 配置 |
| [docs/ldap-integration-guide.md](ldap-integration-guide.md) | LDAP 集成指南 |

### 4.2 健康检查

```bash
# API 健康检查
curl -sf http://localhost:9079/api/health

# 版本与构建信息
curl -sf http://localhost:9079/api/info

# PG 连接检查（企业版）
docker compose exec postgres pg_isready -U maop -d maop

# Redis 连接检查
docker compose exec redis redis-cli ping
```

### 4.3 日志收集

```bash
# 收集最近 1 小时全部服务日志
docker compose logs --since 1h > maop-logs-$(date +%Y%m%d-%H%M).txt

# 仅收集错误级别
docker compose logs --since 1h | grep '"level":"error"' > maop-errors.txt
```

## 第 5 章 版本支持策略

### 5.1 支持窗口

| 版本类型 | 支持周期 | 安全补丁 |
|----------|----------|----------|
| 当前稳定版（如 v5.0.x） | 发布后 12 个月 | 全期 |
| 前一稳定版（如 v4.5.x） | 新版发布后 6 个月 | 仅安全 |
| 更早版本 | 不支持 | 不支持 |

### 5.2 升级建议

- **补丁版本**（x.y.Z → x.y.Z+1）：仅含 bug 修复，建议立即升级。
- **次版本**（x.Y → x.Y+1）：含新功能，向后兼容，建议 30 天内升级。
- **主版本**（X → X+1）：含不兼容变更，需按迁移指南操作（参见 [ROADMAP.md](../ROADMAP.md)）。

### 5.3 长期支持（LTS）

- 每年第一个主版本标记为 LTS，支持期延长至 24 个月。
- LTS 版本仅接受安全补丁与关键 bug 修复，不接受新功能。
- 当前无 LTS 版本（v5.0.0 为首个候选 LTS）。

## 第 6 章 责任边界

### 6.1 MAOP 团队负责

- MAOP 平台自身代码缺陷。
- 文档错误或缺失。
- 默认配置下的安全漏洞。
- 平台性能未达 SLO（仅托管服务）。

### 6.2 客户负责

- 客户自定义插件与扩展代码。
- 客户自有基础设施（K8s 集群、网络、存储）。
- 客户自有身份源（LDAP/AD/IdP）配置与可用性。
- 密钥与凭证管理（`MAOP_JWT_SECRET`、`MAOP_PG_PASSWORD` 等）。
- 数据备份策略（自托管场景，参见 [docs/runbook.md](runbook.md)）。
- 遵守许可证条款（参见 [LICENSE](../LICENSE)、[docs/dpa.md](dpa.md)）。

### 6.3 共同负责

- 安全事件响应：客户负责通报与配合调查，MAOP 负责根因分析与补丁。
- 性能调优：MAOP 提供建议与基准，客户负责实施与验证。
- 升级执行：MAOP 提供迁移指南，客户负责在测试环境验证后执行。

---

> 本支持政策以简体中文为准。与 [SLA](sla.md) 配合阅读。最终解释权归 MAOP 团队所有。