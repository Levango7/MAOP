# MAOP 文档中心

> **MAOP** (Multi-Agent Orchestration Platform) — 个人版，免费开源
> **MAOS** (Multi-Agent Orchestration Suite) — 企业版，需 License

欢迎来到 MAOP 文档中心。本索引入口页按类别组织全部文档，帮助您快速定位所需内容。

**导航提示：** 若您是首次使用，建议从"快速入门"起步；若需了解系统架构，请查阅"架构设计"；运维人员请直接跳转"部署运维"。

---

## 第1章 快速入门

| 文档 | 说明 |
|------|------|
| [用户指南](./user-guide.md) | MAOP 入门与操作手册，覆盖安装、配置、Agent 管理、记忆系统等核心功能 |
| [API 示例](./api-examples.md) | 常见用例的 curl / Python 示例，含认证、Agent 调用、记忆检索等场景 |
| [API 参考](./api-reference.md) | 完整 HTTP 与 WebSocket 端点规格（v5.1.0），380+ 个端点，供集成方与运维人员查阅 |

---

## 第2章 架构设计

| 文档 | 说明 |
|------|------|
| [技术白皮书](./technical-whitepaper.md) | MAOP 架构、设计目标、多租户隔离、合规性、可观测性技术白皮书 |
| [Nexus 统一编排平台 HLD](./Nexus统一编排平台_HLD.md) | 五层架构（交互层→网关层→编排层→能力节点层→事件/可观测层）高层设计 |
| [Nexus 交付流水线 LLD](./Nexus交付流水线_LLD.md) | 需求→编码→审查→构建→测试→部署→压测→回归 8 阶段交付流水线低层设计 |
| [数据库 Schema](./database-schema.md) | 数据库表结构参考（117 张表，当前记录 53 张） |
| [设计规范](./DESIGN_RULES.md) | Dashboard 权威设计规范 — 色彩体系、组件契约、交互模式 |
| [产品设计 RFC-001](./product-design-rfc-001.md) | MAOP 控制台从"功能仓库"到"工作台"的产品设计演进方案 |
| [接口盘点报告](./_盘点_MAOP_OpsMesh_Interaction.md) | MAOP × OpsMesh × Interaction 三项目接口盘点，含 API 端点/鉴权/事件总线 |
| [架构决策记录](./adr/README.md) | ADR 001–017，含双版架构、HA 设计、Python 主引擎、安全加固等关键决策 |

---

## 第3章 部署运维

| 文档 | 说明 |
|------|------|
| [部署指南](./deployment.md) | Docker Compose 快速启动与 Kubernetes 生产部署指南 |
| [运维手册](./runbook.md) | 生产环境运维 Runbook — PG 故障切换、备份恢复、监控告警 |
| [故障排查](./troubleshooting.md) | 系统化故障排查手册，含诊断工具、常见问题与解决流程 |
| [容量规划](./capacity-planning.md) | 各组件资源需求基准、小/中/大/超大部署配置、扩缩容策略 |
| [性能基准](./performance-benchmarks.md) | 性能基准数据、API 延迟分级、资源使用基线、调优指南 |
| [SLA](./sla.md) | 服务等级协议 — 可用性 SLO、违约补偿条款 |
| [支持政策](./support-policy.md) | 三级支持体系（社区/标准/企业）、响应 SLA、生命周期 |
| [服务条款](./terms-of-service.md) | 双版许可条款、用户义务、责任限制 |
| [隐私政策](./privacy-policy.md) | 数据收集与处理政策（PIPL/GDPR/CCPA 合规） |
| [数据处理协议 (DPA)](./dpa.md) | 企业版托管服务数据处理协议 — 子处理者、跨境传输、安全事件 |
| [贡献者许可协议 (CLA)](./cla.md) | 贡献者知识产权安排与许可授予 |

---

## 第4章 开发指南

| 文档 | 说明 |
|------|------|
| [贡献指南](./contributing.md) | 开发环境搭建、CI/CD 流程、代码规范、发布流程 |
| [分支策略](./BRANCH_STRATEGY.md) | Trunk-based 开发模型 — master/develop/feature/fix/hotfix/release |
| [前端设计规范](./frontend-style-guide.md) | Vue 3 前端交互/视觉规范 — ListPageLayout、FilterBar、chartTokens |
| [API 参考](./api-reference.md) | 完整 HTTP/WebSocket 端点规格，供前端开发与集成联调使用 |

---

## 第5章 变更路线图

| 文档 | 说明 |
|------|------|
| [v5.0.0 发布说明](./release-5.0.0.md) | v5.0.0 major release — 不兼容变更、Phase 5b 发布/性能/合规修复 |
| [v5.0.0 迁移指南](./migration-5.0.md) | v4.5.0 → v5.0.0 全部不兼容变更与迁移步骤 |
| [API 变更日志](./API_CHANGELOG.md) | REST API 与 WebSocket API 版本变更记录（v4.4.0–v4.4.1） |
| [v5.1.0 修复计划书](./remediation-plan-v5.1.0.md) | CI/CD、依赖安全、预算守卫、死代码、文档、部署等系统性问题修复方案 |
| [三阶段演进 PRD](./prd-three-phase-roadmap.md) | 21 个月三阶段路线图产品需求文档 — 阶段一已完成、阶段二/三待启动 |
| [三阶段演进 HLD](./hld-three-phase-roadmap.md) | 三阶段路线图高层设计 — 分布式执行/智能增强/生态平台化架构设计 |
| [平台化演进](./platform-evolution.md) | Model Management、Control Plane、Contract Testing 三大支柱演进 |
| [Agent 自演化指南](./evolution-guide.md) | OBSERVE→HEAL→SUGGEST→EVALUATE→APPLY→VALIDATE→CONSOLIDATE 七段闭环 |
| [插件迁移指南](./plugin-migration.md) | delegate.ps1（switch-case）→ delegate-plugin.ps1（配置驱动）迁移步骤 |

---

## 第6章 专题指南

| 文档 | 说明 |
|------|------|
| [集成指南](./integration-guide.md) | API/Webhook/SSO/LDAP 集成总览，含认证、Agent 调用、Webhook 接收 |
| [LDAP 集成指南](./ldap-integration-guide.md) | OpenLDAP / Microsoft AD 用户认证与同步配置 |
| [SSO 集成 PRD](./prd-sso-integration.md) | OIDC + SAML 2.0 对接 Keycloak / Azure AD 产品需求文档 |
| [SAML SSO 配置指南](./enterprise/saml-sso-guide.md) | SAML 2.0 SP-initiated SSO 配置（Azure AD / Okta / ADFS） |
| [License 签发指南](./enterprise/license-issuance-guide.md) | Ed25519 签名 License 签发流程、密钥管理、客户交付（商业机密） |
| [License CRL 撤销指南](./enterprise/license-crl-guide.md) | License 在线撤销列表配置与工作原理 |
| [混淆与防破解加固](./enterprise/obfuscation-guide.md) | 企业版六层防破解深度防御模型与 PyArmor 混淆配置 |
| [OmniRoute 集成](./integrations/omniroute.md) | 160+ LLM 提供商统一网关集成 — Provider 路径 + MCP Server 路径 |
| [n8n 集成](./integrations/n8n.md) | 400+ 外部触发器/SaaS 工作流自动化集成（企业版专属） |

---

## 第7章 归档

历史文档归档于 [archive/](./archive/README.md) 目录，包含：

| 子目录 | 用途 |
|--------|------|
| `audits/` | 历史审查报告、综合分析、版本评估报告 |
| `plans/` | 历史计划文档、路线图、重构计划、执行清单 |
| `fixes/` | 修复方案、执行计划、技术债务治理 |
| `merge-plans/` | 合并计划、可行性报告、补丁测试合并方案 |

> **注意：** 归档文档仅作历史参考与追溯之用，不作为当前项目状态或架构决策的依据。当前权威文档以上述第1–6章所列为准。

---

*文档索引维护：MAOP 团队 ｜ 最后更新：2026-08-21*
