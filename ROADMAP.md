# MAOP Roadmap

> 本文件是 MAOP 版本规划的单一真相源：`CHANGELOG.md` 记录已发生，`ROADMAP.md` 记录将发生。
>
> 版本号遵循 [Semantic Versioning](https://semver.org/spec/v2.0.0.html)，变更条目遵循 [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) 风格。
> 所有日期为计划值，以实际发布为准；未定项标注 `TBD`。

## 当前状态

- **已发布**：v4.5.0（2026-08-06，minor）— core/ 子包重构 + 流式 DAG 执行进度推送 + 知识图谱可视化前端，覆盖率 87%，6130 passed。详见 [CHANGELOG.md](CHANGELOG.md)。
- **上一版**：v4.4.2（2026-08-06，patch）— 稳定化与文档收尾，覆盖率 85%，e2e 路由守卫 12 用例通过，vitest 138 passed。（详见 [v4.4.1-fix-report.md](deliverables/engineering-assurance/v4.4.1-fix-report.md)）
- **双版架构**：自 2026-07-20 起采用单代码库 + 运行时 Edition 检测（详见 [ADR-016](docs/adr/016-dual-edition-architecture.md)）。

## v4.4.2 (patch) — 已发布 2026-08-06

**主题**：稳定化与文档收尾。不引入新功能，聚焦质量门禁、文档一致性、交付物补齐。

### 范围

- **ADR-016 状态同步**：将 SAML SSO 从 `Medium / fail-closed 拒绝` 更新为 `Done`，与代码实际状态（`py/maop/enterprise/sso.py` + `saml_handler.py` + `docs/enterprise/saml-sso-guide.md`）对齐。
- **mypy 告警清理**：修复 `agents.py:252` return-value 类型错误；将 `vector.py` / `runtime.py` 的 `NotImplementedError` 文档化为 `@abstractmethod`，消除 mypy 误报。
- **覆盖率 80% → 85%**：补齐 `py/tests/` 关键路径用例，CI 阈值同步上调。
- **engineering-assurance 交付物补齐**：归档 v4.4.1 修复清单（`v4.4.1-fix-report.md`）；`.env.example` 与代码实际环境变量对齐审计（`env-audit-4.4.2.md`）。
- **`.env.example` 审计**：对比 `py/maop/` 中 `os.environ.get("MAOP_*")` / `os.getenv("MAOP_*")` 实际使用，补齐缺失变量、移除僵尸变量。
- **e2e 路由守卫用例**：`dashboard-enterprise/e2e/` 补企业版路由守卫用例（`/audit` `/rbac` `/tenants` 在 personal 版重定向 `/`）。
- **ROADMAP 建立**：本文件作为版本规划单一真相源纳入仓库。

### 验收标准

- [x] ADR-016 待完善表中 SAML 行状态为 `Done`，且引用 `docs/enterprise/saml-sso-guide.md`。
- [x] `mypy py/maop` 零 error，`ruff check` 零告警。
- [x] CI 覆盖率阈值 ≥ 85%，`pytest --cov` 实测达标。
- [x] `deliverables/engineering-assurance/` 包含 `v4.4.1-fix-report.md` 与 `env-audit-4.4.2.md`。
- [x] `.env.example` 与代码 `MAOP_*` 变量集合差异为零（或差异均有明确注释说明）。
- [x] `dashboard-enterprise/e2e/` 路由守卫用例通过。
- [x] `ROADMAP.md` 入库并被 README 引用。

## v4.5.0 (minor) — 已发布 2026-08-06

**主题**：core/ 子包重构 + 流式执行 + 知识图谱可视化。向后兼容，不破坏现有 API。

### 范围

- **core/ 子包重构**：按 [`py/maop/core/ARCHITECTURE.md`](py/maop/core/ARCHITECTURE.md) 规划，将 `core/`（107+ 模块）拆分为 9 个职责清晰的子包（如 `core/persistence`、`core/llm`、`core/vector`、`core/mcp`、`core/observability`、`core/security`、`core/config`、`core/runtime`、`core/utils`）。**兼容策略**：保留 `core/__init__.py` re-export，现有 `from maop.core.xxx import yyy` 调用无需改动。
- **流式 DAG 执行进度推送**：Orchestrator 在 DAG 节点级状态变更时通过 SSE/WebSocket 推送增量进度事件，前端实时渲染节点状态（pending/running/success/failed/skipped）。
- **知识图谱可视化前端**：基于三层记忆（short/long/vector）构建实体-关系图，支持节点筛选、路径高亮、时间轴回放。

### 验收标准

- [x] `core/` 拆分为 9 子包，`core/__init__.py` re-export 覆盖所有历史导出符号。
- [x] 现有测试套件零改动通过（验证 re-export 兼容）。
- [x] DAG 流式进度事件延迟 < 200ms（P95），前端节点状态与后端一致。
- [x] 知识图谱可视化页面通过 e2e 用例，支持 ≥ 1000 节点流畅交互。
- [x] `CHANGELOG.md` 记录所有 minor 变更，`ROADMAP.md` 更新状态。

## v5.0.0 (major) — TBD

**主题**：废弃清理与 API 收敛。**含不兼容变更**，需迁移指南。

### 范围

- **清理废弃 re-export**：移除 v4.5.0 为兼容而保留的 `core/__init__.py` re-export，强制调用方迁移到子包路径。
- **删除 archive/ legacy**：
  - 原生 JS 仪表盘（`archive/dashboard-legacy/`）— 已被 Vue 3 仪表盘取代。
  - PowerShell legacy 脚本（`archive/ps-legacy/`）— 已被 `cli.py` + `maop.ps1` 取代。
- **不兼容 API 清理**（待评估）：
  - 移除已 deprecated ≥ 2 个版本的 API。
  - 统一命名（如 `maop_loop` → `orchestrator`，若仍存在历史别名）。
  - 收敛配置项（合并语义重叠的 `MAOP_*` 环境变量）。

### 验收标准

- [ ] `archive/` 目录清空或移至独立仓库。
- [ ] 所有 deprecated API 移除，`CHANGELOG.md` 列出迁移路径。
- [ ] `MIGRATION-5.0.md` 迁移指南发布，覆盖后端 + 前端 + 配置。
- [ ] major 版本发布前完成完整 e2e 回归 + 性能基准对比。

## 长期方向（未排期）

- **多后端编排器适配**：支持把 MAOP 编排目标导出为 Temporal / Airflow DAG，便于嵌入企业现有调度体系。
- **Agent Marketplace**：社区共享 agent 配置与 prompt 模板，带版本与签名校验。
- **细粒度成本归因**：按 agent / phase / model 维度的实时成本归因与预算告警。
- **原生 K8s Operator**：以 CRD 形式声明 MAOP 编排任务，由 Operator 调度执行。

## 维护规则

1. 本文件由文档负责人在每个版本发布后更新：已发布版本移至"当前状态"或归档，进行中版本上移。
2. 任何进入 ROADMAP 的条目需有明确范围与验收标准，避免"探索性"占位。
3. 计划变更（范围调整、日期移动）在 PR 描述中说明，并更新本文件。
4. 与 `CHANGELOG.md` 互补：ROADMAP 只写"将发生"，CHANGELOG 只写"已发生"，不重复。