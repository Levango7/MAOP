# MAOP Roadmap

> 本文件是 MAOP 版本规划的单一真相源：`CHANGELOG.md` 记录已发生，`ROADMAP.md` 记录将发生。
>
> 版本号遵循 [Semantic Versioning](https://semver.org/spec/v2.0.0.html)，变更条目遵循 [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) 风格。
> 所有日期为计划值，以实际发布为准；未定项标注 `TBD`。

## 当前状态

- **已发布**：v5.1.0（2026-08-14，minor）— 企业版 6 大功能（许可证/SSO/审计/配额/API Key/通知）+ v5.1.0 6 大新功能（LLM 任务拆分/工作流编辑器/配置历史/Skill 编辑器/异常调度/Hook 配置）+ 版本号统一至 v5.1.0。
- **上一版**：v5.0.0（2026-08-11，major）— 废弃清理 + 配置收敛 + 流式 Agent token 响应增强 + 迁移指南。含不兼容变更，详见 [MIGRATION-5.0.md](docs/migration-5.0.md)。
- **双版架构**：自 2026-07-20 起采用单代码库 + 运行时 Edition 检测（详见 [ADR-016](docs/adr/016-dual-edition-architecture.md)）。
- **阶段二启动**：2026-09-05（48h 长稳判定后）进入阶段二"智能增强"，首版 v5.2.0 = 自演化闭环 MVP（2026-09-05 ~ 10-31），详见下方 v5.2.0 节与 [PRD](docs/prd-three-phase-roadmap.md) / [HLD](docs/hld-three-phase-roadmap.md)。

## v4.4.2 (patch) — 已发布 2026-08-06

**主题**：稳定化与文档收尾。不引入新功能，聚焦质量门禁、文档一致性、交付物补齐。

### 范围

- **ADR-016 状态同步**：将 SAML SSO 从 `Medium / fail-closed 拒绝` 更新为 `Done`，与代码实际状态（`py/maop/enterprise/sso.py` + `saml_handler.py` + `docs/enterprise/saml-sso-guide.md`）对齐。
- **mypy 告警清理**：修复 `agents.py:252` return-value 类型错误；将 `vector.py` / `runtime.py` 的 `NotImplementedError` 文档化为 `@abstractmethod`，消除 mypy 误报。
- **覆盖率 80% → 85%**：补齐 `py/tests/` 关键路径用例，CI 阈值同步上调。（✅ 2026-08-21 核实：实测全量覆盖率 82%，ratchet baseline 已修正为 81%、FLOOR=80）
- **engineering-assurance 交付物补齐**：归档 v4.4.1 修复清单（`v4.4.1-fix-report.md`）；`.env.example` 与代码实际环境变量对齐审计（`env-audit-4.4.2.md`）。
- **`.env.example` 审计**：对比 `py/maop/` 中 `os.environ.get("MAOP_*")` / `os.getenv("MAOP_*")` 实际使用，补齐缺失变量、移除僵尸变量。
- **e2e 路由守卫用例**：`dashboard-enterprise/e2e/` 补企业版路由守卫用例（`/audit` `/rbac` `/tenants` 在 personal 版重定向 `/`）。
- **ROADMAP 建立**：本文件作为版本规划单一真相源纳入仓库。

### 验收标准

- [x] ADR-016 待完善表中 SAML 行状态为 `Done`，且引用 `docs/enterprise/saml-sso-guide.md`。
- [x] `mypy py/maop` 零 error，`ruff check` 零告警。
- [x] CI 覆盖率：实测 82%（ratchet baseline=81, FLOOR=80），2026-08-21 核实修正。
- [x] `deliverables/engineering-assurance/` 包含 `v4.4.1-fix-report.md` 与 `env-audit-4.4.2.md`。
- [x] `.env.example` 与代码 `MAOP_*` 变量集合差异为零（或差异均有明确注释说明）。
- [x] `dashboard-enterprise/e2e/` 路由守卫用例通过。
- [x] `ROADMAP.md` 入库并被 README 引用。

## v4.5.0 (minor) — 已发布 2026-08-06

**主题**：core/ 子包重构 + 流式执行 + 知识图谱可视化。向后兼容，不破坏现有 API。

### 范围

- ~~**core/ 子包重构**：按 [`py/maop/core/ARCHITECTURE.md`](py/maop/core/ARCHITECTURE.md) 规划，将 `core/`（107+ 模块）拆分为 9 个职责清晰的子包（如 `core/persistence`、`core/llm`、`core/vector`、`core/mcp`、`core/observability`、`core/security`、`core/config`、`core/runtime`、`core/utils`）。**兼容策略**：保留 `core/__init__.py` re-export，现有 `from maop.core.xxx import yyy` 调用无需改动。~~ ✅ 已完成（2026-08-06）
- **流式 DAG 执行进度推送**：Orchestrator 在 DAG 节点级状态变更时通过 SSE/WebSocket 推送增量进度事件，前端实时渲染节点状态（pending/running/success/failed/skipped）。
- **知识图谱可视化前端**：基于三层记忆（short/long/vector）构建实体-关系图，支持节点筛选、路径高亮、时间轴回放。

### 验收标准

- [x] `core/` 拆分为 9 子包，`core/__init__.py` re-export 覆盖所有历史导出符号。
- [x] 现有测试套件零改动通过（验证 re-export 兼容）。
- [x] DAG 流式进度事件延迟 < 200ms（P95），前端节点状态与后端一致。
- [x] 知识图谱可视化页面通过 e2e 用例，支持 ≥ 1000 节点流畅交互。
- [x] `CHANGELOG.md` 记录所有 minor 变更，`ROADMAP.md` 更新状态。

## v5.0.0 (major) — 已发布 2026-08-11

**主题**：废弃清理与 API 收敛。**含不兼容变更**，需迁移指南。

### 范围

- **清理废弃 re-export**：移除 v4.5.0 为兼容而保留的部分 re-export shim（`subagent_delegation`、`project_context`），强制调用方迁移到子包路径。`core/__init__.py` re-export 暂保留（影响面广，将在 v6.0.0 评估）。
- **删除 deprecated ≥ 2 版本的 API**：
  - `maop.dashboard.provider.create_app()` / `_render_html()`（deprecated since v4.0.0）
  - `maop.core.agent.delegation.subagent_delegation` shim
  - `maop.core.project_context` / `maop.core.agent.memory_ctx.project_context`
  - `maop_plan.py` legacy keyword routing fallback
  - `/api/batch` deprecated 端点
- **配置收敛**：短名环境变量（`MAOP_PORT`、`MAOP_WORKERS`、`MAOP_TLS`、`MAOP_AUTH`）加 `DeprecationWarning`，推荐迁移到规范长名（`MAOP_DASH_PORT`、`MAOP_DASH_WORKERS`、`MAOP_TLS_ENABLED`、`MAOP_AUTH_ENABLED`）。短名在 v6.0.0 移除。
- **流式 Agent token 响应增强**：新增 `/api/stream/agent/{execution_id}` SSE 端点 + 前端 `useAgentTokenStream.js` composable + Chat.vue 集成增强。
- **迁移指南**：`docs/migration-5.0.md` 覆盖后端 API 变更 + 配置迁移 + Docker 部署变更。
- **Phase 5b — 发布/性能/合规修复（G-08~G-17）**：
  - **G-12 SLA/支持体系**：`docs/sla.md` + `docs/support-policy.md`。
  - **G-13 隐私政策/DPA**：`docs/privacy-policy.md` + `docs/terms-of-service.md` + `docs/dpa.md` + `docs/cla.md`。
  - **G-14 PG 高可用**：`deploy/patroni/`（Patroni 集群 + HAProxy）+ `docker-compose.prod.yml` PG replica + `docs/runbook.md`。
  - **G-16 CI Playwright E2E**：`.github/workflows/ci.yml` 增加 playwright job。
  - **G-17 K8s Operator 集成测试**：`py/tests/test_k8s_operator.py` 支持 kind/k3s。
  - **G-09 性能压测**：`py/tests/performance/`（k6 + locust）+ `docs/capacity-planning.md`。
  - **G-10 LDAP 真实环境验证**：`py/tests/test_ldap_real_env.py` + `docs/ldap-integration-guide.md`。

### 验收标准

- [x] 所有 deprecated ≥ 2 版本的 API 移极移除，`CHANGELOG.md` 列出迁移路径。
- [x] `docs/migration-5.0.md` 迁移指南发布，覆盖后端 + 配置 + Docker。
- [x] 短名环境变量加 `DeprecationWarning`，`.env.example` 标注 deprecated alias。
- [x] 流式 Agent token 响应端点 + 前端 composable + Chat.vue 集成完成。
- [x] `ruff check` 0 error，`mypy` 0 error，测试 0 failed，前端构建成功。
- [ ] `archive/` 目录清空或移至独立仓库（推迟到 v6.0.0，避免 major 范围膨胀）。

## v5.1.0 (minor) — 已发布 2026-08-14

**主题**：企业版功能补全 + v5.1.0 新功能 + 版本号统一。向后兼容，不破坏现有 API。

### 范围

#### 企业版功能（v5.0.2+ 补全）
- **许可证管理**：License 管理 UI + CRUD API + 过期预警 + 特性开关绑定。
- **SSO/SAML 集成**：SAML 2.0 IdP 对接 + SP 配置 + 属性映射。
- **审计日志**：全操作审计 + 审计日志查询/导出 + 不可篡改性。
- **配额管理**：租户级配额（API 调用/Token/存储）+ 超额拒绝 + 用量看板。
- **API Key 管理**：API Key 生成/轮转/吊销 + scope 权限绑定。
- **通知中心**：邮件/Webhook 通知 + 通知模板 + 事件订阅。

#### v5.1.0 新功能
- **LLM 任务拆分**：自动将复杂任务拆分为子任务 + DAG 依赖编排。
- **工作流编辑器**：可视化 DAG 工作流编辑 + 节点配置 + 保存/加载。
- **配置历史**：配置变更快照 + 一键回滚 + 差异对比。
- **Skill 编辑器 + 市场**：Skill 在线编辑 + 模板市场 + 导入/导出。
- **异常调度**：异常检测 + 自动重试策略 + 降级调度。
- **Hook 配置**：Webhook Hook 配置 UI + 事件触发 + 执行日志。

#### 工程修复
- 版本号统一升级至 v5.1.0（pyproject.toml / __init__.py / Dockerfile / package.json / package-lock.json / Chart.yaml / values.yaml / controller.yaml）。
- 移除 pyproject.toml addopts 的 `--cov-fail-under=50`，改由 ratchet 脚本渐进门禁。
- 修复 `/users` 路由守卫缺失（补 `meta.requiresEnterprise`）。
- 修复 `Audit.test.js` chart.js/jsdom unhandled rejection。

### 验收标准

- [x] 企业版 6 大功能（许可证/SSO/审计/配额/API Key/通知）UI + API 完成并通过测试。
- [x] v5.1.0 6 大新功能（LLM 任务拆分/工作流编辑器/配置历史/Skill 编辑器/异常调度/Hook 配置）完成并通过测试。
- [x] 版本号在 pyproject.toml / __init__.py / Dockerfile / package.json / package-lock.json / Chart.yaml / values.yaml / controller.yaml 全部统一为 5.1.0。
- [x] `dashboard-enterprise` 前端 `npm run build` 构建成功。
- [x] `CHANGELOG.md` 补 v5.1.0 条目，`ROADMAP.md` 更新当前状态。

## v5.2.0 (minor) — 进行中（2026-09-05 启动，目标 2026-10-31）

**主题**：自演化闭环 MVP（三阶段路线图 [M2.1](docs/prd-three-phase-roadmap.md)，F2-01）。把已有的 `core/evolution/` 16 模块底座接入主循环，形成可观测、可审批、可回滚的完整闭环。

### 范围

- **EvolutionLoop 接入主循环**：`py/maop/core/evolution/evolution_loop.py` 七阶段闭环接入 `maop_loop_phases.py` 的 `_phase_evolve`（当前仅调 `EvolveEngine.analyze()` 产建议）。配置开关 `MAOP_EVOLUTION_LOOP_ENABLED` **默认关闭**——自动改 prompt / 自动部署属高危操作，稳定性优先。
- **人工 gate**：闭环状态机 `PendingApproval` 停靠 + dashboard 审批入口（复用 `dashboard/routers/evolve_insights.py` / `evolution_experiment.py` 既有路由基础）。
- **A/B 验证**：复用 `ab_test.py`（Z 检验）+ `evolution_perf_loop.py`（SPRT 序贯检验），对齐 HLD 3.1 决策规则（p < 0.05）。
- **自动回滚**：复用 `regression.py`（Persona 模拟回归）+ `prompt_version.py` 版本链回滚。
- **演化可视化**：dashboard 呈现闭环状态机流转与 A/B 结果。
- 开发启动时评估是否立 ADR-020（演化闭环安全边界 / 人工 gate 设计）。

### 验收标准

- [ ] 闭环 E2E：observe→suggest→approve→A/B→promote/rollback 在测试环境对模拟 agent 完整跑通。
- [ ] 劣化候选注入 → 自动回滚 < 5 分钟（PRD 4.2.4 验收的 MVP 子集）。
- [ ] `MAOP_EVOLUTION_LOOP_ENABLED` 默认关闭；开启后主循环其余阶段行为不变（全量测试零回归）。
- [ ] dashboard 可见闭环状态机流转与 A/B 结果。

## 阶段二后续里程碑

| 里程碑 | 版本 | 目标窗口 | 范围锚点 |
|--------|------|----------|----------|
| M2.2 | v5.3.0 | 2026-11-01 ~ 2027-01-15 | F2-02 多模态记忆（嵌入扩展 / pgvector 多向量列 / 融合检索，PRD 4.3）+ F2-03 KGE 选型预研 |
| M2.3 | v6.0.0 (major) | 2027-01-16 ~ 2027-03-05 | F2-03 知识图谱推理（Neo4j Enterprise / 双通道推理，PRD 4.4）+ F2-04 Plan 质量学习 MVP（PRD 4.5） |
| M2.4 | v7.0.0 | 2027-03 | F2-01 GA + F2-04 GA + 阶段二收官（PRD 4.2.4 / 4.5.4 全量验收） |

> 各里程碑进入开发窗口时，按 v5.2.0 节模式补明确范围与验收标准，避免探索性占位。

## 三阶段演进路线图（2026-08-07 制定）

> 详细文档：[PRD](docs/prd-three-phase-roadmap.md) | [HLD](docs/hld-three-phase-roadmap.md)

| 阶段 | 时间窗 | 主题 | 状态 | 关键交付 |
|------|--------|------|------|----------|
| 阶段一 | Month 1–3 | 稳定性与规模化 | ✅ 已完成 | 分布式执行、pgvector、UnifiedMemoryProtocol、OTel 可观测性 |
| 阶段二 | Month 4–9（2026-09-05 启动，至 2027-03） | 智能增强 | 🔄 进行中 | 自演化闭环、多模态记忆、知识图谱推理、Plan 质量学习 |
| 阶段三 | Month 10–21 | 生态与平台化 | 🔜 待启动 | Agent Marketplace、多后端编排器适配、原生 K8s Operator、细粒度成本归因 |

## 长期方向（未排期）

- **多后端编排器适配**：支持把 MAOP 编排目标导出为 Temporal / Airflow DAG，便于嵌入企业现有调度体系。
- **Agent Marketplace**：社区共享 agent 配置与 prompt 模板，带版本与签名校验。（v5.1.0 Skill 市场已实现基础导入/导出，社区共享与签名校验仍待排期。）
- **细粒度成本归因**：按 agent / phase / model 维度的实时成本归因与预算告警。
- **原生 K8s Operator**：以 CRD 形式声明 MAOP 编排任务，由 Operator 调度执行。（v5.0.0 G-17 已实现 K8s Operator 集成测试基线，CRD 声明式调度已在 `deploy/k8s/operator/` 落地，进一步多租户/插件增强待排期。）

## 维护规则

1. 本文件由文档负责人在每个版本发布后更新：已发布版本移至"当前状态"或归档，进行中版本上移。
2. 任何进入 ROADMAP 的条目需有明确范围与验收标准，避免"探索性"占位。
3. 计划变更（范围调整、日期移动）在 PR 描述中说明，并更新本文件。
4. 与 `CHANGELOG.md` 互补：ROADMAP 只写"将发生"，CHANGELOG 只写"已发生"，不重复。