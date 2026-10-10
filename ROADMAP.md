# MAOP Roadmap

> 本文件是 MAOP 版本规划的单一真相源：`CHANGELOG.md` 记录已发生，`ROADMAP.md` 记录将发生。
>
> 版本号遵循 [Semantic Versioning](https://semver.org/spec/v2.0.0.html)，变更条目遵循 [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) 风格。
> 所有日期为计划值，以实际发布为准；未定项标注 `TBD`。

## 当前状态

- **已发布**：v5.3.1（2026-10-10，patch）— PyPI 包元数据补全（署名/keywords/classifiers/urls）+ 发布链修复（packages-dir 路径 + 手工发布逃生舱）+ PyPI 首次上线（maop-orchestrator）。详见 CHANGELOG 的 [5.3.1] 段。
- **上一版**：v5.3.0（2026-10-10，minor）— 调度与编排能力打包（真抢占 γ-3 opt-in / 分布式优先级批量重排 + 负载感知选工 / 动态编排运行时 fan-out）+ 两批后端审计修复（分布式挂死、取消安全、泄漏、热路径）+ 前端 Chat 页 i18n 修复。范围与验收见下方 v5.3.0 节。
- **待发布**：v5.2.x（patch 系列，WS-1/WS-2 剩余任务，范围见下节）；后续里程碑 v5.4.0（M2.2，目标 2026-11 起）。
- **v5.2.0**（2026-09-08，minor）— 自演化闭环 MVP 接入主循环（可观测/可审批/可回滚），闭环 API 面实测为 `evolve_insights.py` 的 6 个 `/api/evolution/*` 端点（旧文案"7 个 evolution API 端点"无出处，2026-10-05 已在下方 v5.2.0 节更正，此处当时漏改）；桌面调度集成；前端可访问性/token 化修复多轮。详见 [CHANGELOG](CHANGELOG.md)。
- **v5.0.0**（2026-08-11，major）— 废弃清理 + 配置收敛 + 流式 Agent token 响应增强 + 迁移指南。含不兼容变更，详见 [MIGRATION-5.0.md](docs/migration-5.0.md)。
- **双版架构**：自 2026-07-20 起采用单代码库 + 运行时 Edition 检测（详见 [ADR-016](docs/adr/016-dual-edition-architecture.md)）。
- **阶段二**：2026-09-05 起进入阶段二"智能增强"，首版 v5.2.0 = 自演化闭环 MVP。2026-09-15 自演化闭环验收达成（见 git `3747805`）。详见下方 v5.2.0 节与 [PRD](docs/prd-three-phase-roadmap.md) / [HLD](docs/hld-three-phase-roadmap.md)。

## v4.4.2 (patch) — 已发布 2026-08-06

**主题**：稳定化与文档收尾。不引入新功能，聚焦质量门禁、文档一致性、交付物补齐。

### 范围

- **ADR-016 状态同步**：将 SAML SSO 从 `Medium / fail-closed 拒绝` 更新为 `Done`，与代码实际状态（`py/maop/enterprise/sso.py` + `saml_handler.py` + `docs/enterprise/saml-sso-guide.md`）对齐。
- **mypy 告警清理**：修复 `agents.py:252` return-value 类型错误；将 `vector.py` / `runtime.py` 的 `NotImplementedError` 文档化为 `@abstractmethod`，消除 mypy 误报。
- **覆盖率 80% → 85%**：补齐 `py/tests/` 关键路径用例，CI 阈值同步上调。（2026-08-21 核实：实测全量覆盖率 82%，ratchet baseline 已修正为 81%、FLOOR=80）
- **engineering-assurance 交付物补齐**：归档 v4.4.1 修复清单（`v4.4.1-fix-report.md`）；`.env.example` 与代码实际环境变量对齐审计（`env-audit-4.4.2.md`）。
- **`.env.example` 审计**：对比 `py/maop/` 中 `os.environ.get("MAOP_*")` / `os.getenv("MAOP_*")` 实际使用，补齐缺失变量、移除僵尸变量。
- **e2e 路由守卫用例**：`dashboard-enterprise/e2e/` 补企业版路由守卫用例（`/audit` `/rbac` `/tenants` 在 personal 版重定向 `/`）。
- **ROADMAP 建立**：本文件作为版本规划单一真相源纳入仓库。

### 验收标准

- [x] ADR-016 待完善表中 SAML 行状态为 `Done`，且引用 `docs/enterprise/saml-sso-guide.md`。
- [x] `mypy py/maop` 零 error，`ruff check` 零告警。
- [x] CI 覆盖率：实测 82%（ratchet baseline=81, FLOOR=80），2026-08-21 核实修正。
- ~~`deliverables/engineering-assurance/` 包含 `v4.4.1-fix-report.md` 与 `env-audit-4.4.2.md`。~~
      **2026-10-06 作废（不可满足，保留理由；先前两条说明都需要更正）**
      ① 这两份文件从未进入任何提交：`git log --all -- deliverables/engineering-assurance`
      只有一条真实交付物 `comprehensive-review-maop-2026-07-20.md`（初始提交带入，
      后由 `495eaca3` 归档到 `docs/archive/audits/`）。验收标准点名的文件名是凭空写的。
      ② 我 2026-10-05 的说明"`.gitignore:174` 忽略整个 `deliverables/`，这类交付物
      结构性地不可能作为仓库内物证"**不成立** —— `.gitignore` 只挡未跟踪文件，
      `git ls-tree origin/master -- deliverables/` 实测该目录仍有 9 个文件在版本控制内
      （含 `pypi-upload-guide.md`）。被忽略的是"以后新扔进去的东西"，不是"已入库的物证"。
      ③ 处置：该版本的工程保障物证实际在 `docs/archive/audits/`，按"物证存在但在别处"记录，
      不再作为一个可待办项挂着。要恢复成勾选项需重新定义验收物，而不是补一个不存在的文件名。
- [x] `.env.example` 与代码 `MAOP_*` 变量集合差异为零（或差异均有明确注释说明）。
- [x] `dashboard-enterprise/e2e/` 路由守卫用例通过。
- [x] `ROADMAP.md` 入库并被 README 引用。

## v4.5.0 (minor) — 已发布 2026-08-06

**主题**：core/ 子包重构 + 流式执行 + 知识图谱可视化。向后兼容，不破坏现有 API。

### 范围

- ~~**core/ 子包重构**：按 [`py/maop/core/ARCHITECTURE.md`](py/maop/core/ARCHITECTURE.md) 规划，将 `core/`（107+ 模块）拆分为 9 个职责清晰的子包（如 `core/persistence`、`core/llm`、`core/vector`、`core/mcp`、`core/observability`、`core/security`、`core/config`、`core/runtime`、`core/utils`）。**兼容策略**：保留 `core/__init__.py` re-export，现有 `from maop.core.xxx import yyy` 调用无需改动。~~（已完成，2026-08-06）
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
  - **G-17 K8s Operator 集成测试**：`py/tests/test_k8s_operator.py` 目前**仅做结构断言**（Chart/controller/crd 文件存在性）+ 3 个 skip 占位，kind/k3s 集成验证待实现。
  - **G-09 性能压测**：`py/tests/performance/`（k6 + locust）+ `docs/capacity-planning.md`。
  - **G-10 LDAP 真实环境验证**：`py/tests/test_ldap_real_env.py` + `docs/ldap-integration-guide.md`。

### 验收标准

- [x] 所有 deprecated ≥ 2 版本的 API 移极移除，`CHANGELOG.md` 列出迁移路径。
- [x] `docs/migration-5.0.md` 迁移指南发布，覆盖后端 + 配置 + Docker。
- [x] 短名环境变量加 `DeprecationWarning`，`.env.example` 标注 deprecated alias。
- [x] 流式 Agent token 响应端点 + 前端 composable + Chat.vue 集成完成。
- [x] `ruff check` 0 error，`mypy` 0 error，测试 0 failed，前端构建成功。
- [x] `archive/` 目录清空或移至独立仓库（推迟到 v6.0.0，避免 major 范围膨胀）。
      **2026-10-05 核对：已完成** —— `CHANGELOG` 记 2026-09-29 已 `git rm -r archive/`（107 文件），
      实测根目录已无 `archive/`；此前一直挂着未勾，属漏勾。

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

## v5.2.0 (minor) — 已发布（2026-09-08）

> **自演化闭环 MVP 已接入主循环**（可观测/可审批/可回滚），配置开关 `MAOP_EVOLUTION_LOOP_ENABLED` 默认关闭，下方四项验收标准**均已完成**。
> **2026-10-05 核对更正**：横幅原写「部分验收标准（见下方未勾选项）仍在迭代验证中」，但下方已无未勾选项（横幅漏更新）；原写的「7 个 evolution API 端点」与任何可数集合都不对应 —— 实测端点为 `dashboard/routers/evolve_insights.py` 9 个 `/api/evolve/*` 加 6 个 `/api/evolution/*`，另 `evolution_experiment.py` 16 个，故不再给单个数字。

**主题**：自演化闭环 MVP（三阶段路线图 [M2.1](docs/prd-three-phase-roadmap.md)，F2-01）。把已有的 `core/evolution/` 16 模块底座接入主循环，形成可观测、可审批、可回滚的完整闭环。

### 范围

- **EvolutionLoop 接入主循环**：`py/maop/core/evolution/evolution_loop.py` 七阶段闭环接入 `maop_loop_phases.py` 的 `_phase_evolve`（当前仅调 `EvolveEngine.analyze()` 产建议）。配置开关 `MAOP_EVOLUTION_LOOP_ENABLED` **默认关闭**——自动改 prompt / 自动部署属高危操作，稳定性优先。
- **人工 gate**：闭环状态机 `PendingApproval` 停靠 + dashboard 审批入口（复用 `dashboard/routers/evolve_insights.py` / `evolution_experiment.py` 既有路由基础）。
- **A/B 验证**：复用 `ab_test.py`（Z 检验）+ `evolution_perf_loop.py`（SPRT 序贯检验），对齐 HLD 3.1 决策规则（p < 0.05）。
- **自动回滚**：复用 `regression.py`（Persona 模拟回归）+ `prompt_version.py` 版本链回滚。
- **演化可视化**：dashboard 呈现闭环状态机流转与 A/B 结果。
- 开发启动时评估是否立 ADR-020（演化闭环安全边界 / 人工 gate 设计）。

### 验收标准

- [x] 闭环 E2E：observe→suggest→approve→A/B→promote/rollback 在测试环境对模拟 agent 完整跑通。
      （2026-10-03 T3.1：`test_evolution_loop_real_e2e.py` 真链路——零 mock 跑通
      ErrorLedger→auto_promote→EVALUATE→APPLY→VALIDATE；approve→APPLY 跨轮回流仍待接线，
      见投资计划 T3.1 遗留项。）
- [x] 劣化候选注入 → 自动回滚 < 5 分钟（PRD 4.2.4 验收的 MVP 子集）。
      （2026-10-03 T3.1：`inject_degradation_suggestion()` 真实入口 + CLI
      `maop evolution inject-degradation`；真 ChangeTracker 回滚、agents.yaml
      字节级恢复、SLA 实测 <300s。）
- [x] `MAOP_EVOLUTION_LOOP_ENABLED` 默认关闭；开启后主循环其余阶段行为不变（全量测试零回归）。
      （2026-10-03 T3.1：`test_evolution_switch_no_regression.py` 三条可执行基线——
      相位计数/事件面差异、闭环异常隔离、数据落 MAOP_DATA_DIR 不出仓库；
      变异验证：接线点改恒 True 即红。）
- [x] dashboard 可见闭环状态机流转与 A/B 结果。
      （2026-10-03 T3.1-c：EvolutionHistory 新增「闭环」tab 接全部 6 个 AC-07 端点，
      e2e 拦截真实请求验证 trigger 打通，三浏览器绿。）

## v5.2.1 (patch) — 已发布 2026-10-09

**主题**：解除与已发布 MAOS `enterprise-v5.2.3` 的兼容缺口，并把 v5.2.0 之后积压的修复
收口为可分发制品。

> **为什么必须先发这一版**：v5.2.0（2026-09-08）之后主干已有 **243 个提交未发布**
> （其中 119 个 `feat`/`fix`），积压 31 天，已超出本文件上方"发布节奏规范"对 patch 的
> 频率约束。期间 MAOS 发布了 `enterprise-v5.2.3`，其通知语义变更（空 `tenant_id`
> 不再等于"所有租户"，`f9cbc70`）**要求 MAOP 侧配套**——否则企业版 admin 的
> 渠道/规则/模板列表恒空。**已发布的 MAOP 5.2.0 + MAOS 5.2.3 因此是不兼容组合，
> 且 MAOP 侧当前没有任何版本地板声明。**（2026-10-09 实测：`all_tenants` 存在于主干
> `py/maop/dashboard/routers/notifications.py`，但 `git grep all_tenants v5.2.0` 零命中。）

### 范围

- **MAOS 兼容（P0）**：通知列表端点的租户作用域解析改为显式 `all_tenants=True`
  （`py/maop/dashboard/routers/notifications.py`）；`pyproject.toml` 与 README 声明
  **MAOS ≥ `enterprise-v5.2.3`** 的版本地板。
- **CI 可维护性**：pytest 矩阵 Linux 腿显式钉 `ubuntu-26.04`（`ubuntu-latest` 将于
  2026-10-19 ~ 11-19 被 GitHub 迁到 26.04，已由 nightly 金丝雀实测为绿后迁移）。
- **WS-2 工程债**：T2.3 `TestCallSyncFallback` flaky 的泄漏源归位（时间盒续期）。

### 验收标准

- [ ] 在 MAOS `enterprise-v5.2.3` 下，企业版 admin 的通知渠道/规则/模板列表不再恒空，
      由**非 mock** 集成用例锁定。
      （需 MAOS 环境，本仓无法验证，保持未勾。）
- [x] README 与 CHANGELOG 声明 MAOS 版本地板（`≥ enterprise-v5.2.3`），声明值指向真实存在的
      tag，并由 `py/tests/test_maos_version_floor.py` 守卫其不被静默删除；`pyproject.toml`
      因 MAOS 是私有包**无法表达依赖约束**，以注释记录原因。
- [x] 发布物从对应 tag 构建，GitHub Release 附件**回读校验**通过（零账号分发路径）。
      （2026-10-09：Release v5.2.1 附 wheel+sdist，`attach_release_assets.py` 回读校验通过。）
- [x] CI 全绿（含 26.04 腿）。

## v5.3.0 (minor) — 已发布 2026-10-10

**主题**：调度与编排能力打包 + 两批后端审计修复（v5.2.1 之后主干 6 个提交，23 文件 +2898/−277）。

### 范围

- **真抢占（Phase γ-3，opt-in）**：`PreemptableWorkerPool(true_preemption=True)` 在队首严格
  高于运行任务时取消最低优先级者并**原 token 重入队**；`PipelineCheckpoint` 接线（被抢占任务
  留 `running` 步供重试）；`TaskPreemptedError` 区分抢占/普通取消。默认关闭，行为不变。
- **分布式调度增强**：worker 侧批量优先级重排（同优先级 FIFO）；负载感知选工
  `weight/(1+在途数)`（默认生效，排空 worker 仍永不入选）。
- **动态编排（第一刀）**：运行时 fan-out——执行器返回对象携带 `spawn` 属性
  （`SpawnDirective`）即可在步成功后向运行图注入新步；重复 id / 未知依赖 / 每运行 100 上限
  三重校验。**边界**：仅单进程路径，分布式扇出需扩展调度协议，留待后续。
- **后端审计两批**：分布式路径四缺陷（结果流游标增量读取修复 >100 节点挂死、任务取消安全、
  run 级超时、重复扫描）+ 泄漏与可见性七项（WorkerPool 簿记有界保留、worktree 功能复活、
  spawn 队列清理、`safe_eval` 缓存、模板单遍替换修二阶注入、CONDITION 告警、license 掩码）。
- **前端**：Chat 页 8 个 i18n key 缺失修复（程序化审查发现原始 key 直显）。

### 验收标准

- [x] 版本站点四守卫一致（`test_version_sync_guard` 锁定 pyproject / `__init__` /
      Dockerfile / package.json）；`check_release_tag` 门禁通过（tag == 包内版本）。
      （2026-10-10 发布提交：7 站点同步 5.3.0。）
- [x] 发布物从对应 tag 构建，GitHub Release 附件**回读校验**通过。
      （2026-10-10：tag v5.3.0 → Release Assets 作业回读校验。）
- [x] 全量测试绿（本地 10375 passed / 75 skipped；含 PR CI 全矩阵）。
- [x] 独立 venv 安装发布物 `maop.__version__ == "5.3.0"`；起服务 `/api/health` 版本一致。
      （2026-10-10 发布验证：wheel 独立安装 + `/api/health`。）

## v5.2.x (patch 系列) — 计划中

**主题**：商业锁硬化（WS-1）与工程债清偿（WS-2）的剩余任务，按
[investment-plan-2026Q4](docs/investment-plan-2026Q4.md) §4 的优先级表推进；
**范围与验收标准以本节为准**，该文档降级为施工记录。

### 范围

- **T1.5 phase 2 — 二进制发行（MAOS）**：三平台矩阵各产出一份**产物签名清单**
  （`.pyd`/`.so` 按原始字节），发布链顺序改造（源码 → cythonize → **对产物**签名 →
  构建 wheel），并补体积与启动耗时基准。**三平台矩阵完成前，二进制产物不得发布。**
- **T1.6 M1 — 离线激活（MAOS）**：设备指纹（多因子）+ 请求/令牌格式与签名 +
  `validate()` 接入与宽限期（`MAOP_ACTIVATION_GRACE_DAYS`，默认 14 天）+ vendor 签发 CLI。
- **T2.1 — dry-run 信号产出方（MAOP）**：把 `_gate_dry_run` 从"验信号"变成"做预演"，
  合同收紧为行首哨兵 `MAOP_DRY_RUN_MARKER: {...}`（JSON 行，旧子串匹配保留一个 minor
  后移除）；pipeline 与 fileops 两类执行器分别落地，`config/agents.yaml` 加
  `dry_run_supported` 能力声明。
- **T2.3 — flaky 泄漏源归位或止损台账化（MAOP）**。

### 验收标准

- [ ] T1.5 phase 2：三平台各一份产物签名清单；`verify_wheel` 在二进制态五项校验全绿；
      变异验证"未登记 `.so` / 替换 `.pyd` / 删除 `.pyd`"三种均判红。
- [ ] T1.6 M1：换机在宽限期后失效；token 被篡改/过期/指纹不符一律拒绝；离线全流程演练一遍。
- [ ] T2.1：`MAOP_DRY_RUN_ENFORCE=1` 下 pipeline 任务真预演通过且写操作零副作用
      （临时目录断言）；未声明能力的外部 agent 不受影响。
- [ ] T2.3：连续 7 天 nightly 零假红（修复路线），或止损方案与"未复现条件/复现配方"
      台账化（降级路线）。

## v5.4.0 (minor) — 计划中（目标 2026-11-01 ~ 2027-01-15，里程碑 M2.2）

> 版本锚点 2026-10-10 由 v5.3.0 后移为 v5.4.0：v5.3.0 被调度与编排能力打包（含新能力
> 动态编排与默认生效的负载感知选工，按 minor 发布）占用；M2.2 的主题与窗口不变。

**主题**：阶段二 F2-02 多模态记忆落地（PRD 4.3）。

### 范围

- **前置 spike（T3.2，2 周时间盒）**：嵌入扩展接口（`UnifiedMemoryProtocol` 的 modality
  维度兼容设计）、pgvector 半精度/多向量列方案对比、融合检索（RRF 起步）PoC、
  KGE 选型对比（Neo4j Enterprise 成本 vs 纯 pgvector 演进路线）。产出：选型 ADR + PoC 分支。
  **不做功能落地**——落地在本版。
- **主体**：按 spike 结论落 F2-02（嵌入扩展 / 多向量列 / 融合检索）。
- **可选（按余力）**：T3.3 K8s Operator M0——kopf 骨架 + `MaopTask` reconcile 最小闭环 +
  `ghcr.io/maop/operator` 首次真实构建 + kind 集成腿激活（现有 skipif 钩子装了 kind 自动生效）。

### 验收标准

- [ ] 选型 ADR 合入，且记录 PoC **实测**数据（非推演）。
- [ ] F2-02 的多模态检索在**非 mock** 集成用例下通过。
- [ ] （若纳入 T3.3 M0）kind 集群 apply 一个 `MaopTask` → operator 拉起执行 →
      `status.phase` 由 Running 收敛到 Succeeded，e2e 在 CI 绿。

## 阶段二后续里程碑

| 里程碑 | 版本 | 目标窗口 | 范围锚点 |
|--------|------|----------|----------|
| M2.2 | v5.4.0 | 2026-11-01 ~ 2027-01-15 | F2-02 多模态记忆（嵌入扩展 / pgvector 多向量列 / 融合检索，PRD 4.3）+ F2-03 KGE 选型预研 |
| M2.3 | v6.0.0 (major) | 2027-01-16 ~ 2027-03-05 | F2-03 知识图谱推理（Neo4j Enterprise / 双通道推理，PRD 4.4）+ F2-04 Plan 质量学习 MVP（PRD 4.5） |
| M2.4 | v7.0.0 | 2027-03 | F2-01 GA + F2-04 GA + 阶段二收官（PRD 4.2.4 / 4.5.4 全量验收） |

> 各里程碑进入开发窗口时，按 v5.2.0 节模式补明确范围与验收标准，避免探索性占位。

## 三阶段演进路线图（2026-08-07 制定）

> 详细文档：[PRD](docs/prd-three-phase-roadmap.md) | [HLD](docs/hld-three-phase-roadmap.md)

| 阶段 | 时间窗 | 主题 | 状态 | 关键交付 |
|------|--------|------|------|----------|
| 阶段一 | Month 1–3 | 稳定性与规模化 | 已完成 | 分布式执行、pgvector、UnifiedMemoryProtocol、OTel 可观测性 |
| 阶段二 | Month 4–9（2026-09-05 启动，至 2027-03） | 智能增强 | 进行中 | 自演化闭环、多模态记忆、知识图谱推理、Plan 质量学习 |
| 阶段三 | Month 10–21 | 生态与平台化 | 待启动 | Agent Marketplace、多后端编排器适配、原生 K8s Operator、细粒度成本归因 |

## 长期方向（未排期）

- **多后端编排器适配**：支持把 MAOP 编排目标导出为 Temporal / Airflow DAG，便于嵌入企业现有调度体系。
- **Agent Marketplace**：社区共享 agent 配置与 prompt 模板，带版本与签名校验。（v5.1.0 Skill 市场已实现基础导入/导出，社区共享与签名校验仍待排期。）
- **细粒度成本归因**：按 agent / phase / model 维度的实时成本归因与预算告警。
- **原生 K8s Operator**：以 CRD 形式声明 MAOP 编排任务，由 Operator 调度执行。（**未实现 / planned**：`deploy/k8s/operator/` 目前只有规划中的 API 形状——无 kopf/kubernetes-client/watch controller 实现，`ghcr.io/maop/operator` 镜像无构建来源、未构建；结构测试仅断言文件存在，多租户/插件/RLS 均为 skip 占位，详见该目录 README §Status。）

## 维护规则

1. 本文件由文档负责人在每个版本发布后更新：已发布版本移至"当前状态"或归档，进行中版本上移。
2. 任何进入 ROADMAP 的条目需有明确范围与验收标准，避免"探索性"占位。
3. 计划变更（范围调整、日期移动）在 PR 描述中说明，并更新本文件。
4. 与 `CHANGELOG.md` 互补：ROADMAP 只写"将发生"，CHANGELOG 只写"已发生"，不重复。