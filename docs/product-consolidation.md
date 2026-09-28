# MAOP 产品整合方案（定位 · 盘点 · 处置）

> 制定：2026-09-29。依据：README（2026-09-26 口径）、`config/agents.yaml`、py/maop 代码树实测、
> docs/design-spec.md、ADR-016/017、ROADMAP.md。
>
> 本文回答"产品是什么、现在有什么、哪些留/删/补/改"。版本规划以 [ROADMAP.md](../ROADMAP.md) 为准，
> 三阶段路线以 [PRD](prd-three-phase-roadmap.md) / [HLD](hld-three-phase-roadmap.md) 为准，
> 前端规范以 [design-spec.md](design-spec.md) / [frontend-style-guide.md](frontend-style-guide.md) 为准——
> 本文不重复它们的内容，只做收口与处置。

---

## 1. 定位

### 1.1 一句话

MAOP 是**外部 CLI agent 的编排与治理层**：把已经装在这台机器上的 Claude Code、Codex、Gemini CLI
等外部 agent 编进 Plan-Execute-Verify 循环，统一路由、校验、记忆与治理；它不造 agent 运行时。

### 1.2 三要素

| 维度 | 内容 |
|------|------|
| 为谁 | 个人版：已在用多个外部 CLI agent、但任务调度与成本靠人肉的开发者/小团队。企业版：要把 agent 用进生产、需要 RBAC / 多租户 / SSO / 审计的团队 |
| 解决什么 | 单个 CLI 各自为战：没有统一的计划与校验、用哪个 agent 靠人挑、成本与调用轨迹不可观测、多 agent 协作无治理。MAOP 把这些收进一个控制面 |
| 凭什么不同 | ① 不造运行时，编排现成 CLI——`config/agents.yaml` 顶层 31 条（23 开箱可派发第三方 + 5 需配置 + 1 自研 doc-pipeline + 1 自引用）；② 循环自带门禁（content-safety / dry-run / verify）；③ 演化时间线——agent 配置的每次变更可追溯、可审批、可回滚 |

### 1.3 边界

**是**：编排层、治理层、控制台、记忆层。

**不是**：agent 运行时（执行交给外部 CLI）、通用 LLM 网关（内置 LLM 仅用于对话/分析/建议生成）、
IDE、单体 agent 工具——对标单体 CLI 时，MAOP 的定位是它的上层而不是替代品。

### 1.4 口径纪律

"31 个适配器"只有一条口径，出自 README 定位说明段（2026-09-26 用脚本核对）：

```
31 顶层条目 = 23 开箱可派发的第三方 CLI 适配器
            + 5 需额外配置（enabled: false：deepcode/langcli/mimo/qoder/qwen）
            + 1 自研适配器（doc-pipeline，driver: python）
            + 1 自引用（MAOP 自身，max_self_ref_depth=3 防递归）
另：omniroute 虽 enabled=true 但 cli 为空，派发即 exit_code=-1，不计入开箱可用。
```

2026-09-29 复核 `config/agents.yaml` 顶部条目仍为 31，口径成立。
**已知冲突（2026-09-29 已更正）**：[technical-whitepaper.md](technical-whitepaper.md) 原写作"内置 31 个
第三方 CLI 适配器"——把"31 个条目"误作"31 个第三方"，现已按上表改写；其子系统表的模块路径
（`core/agent_*`、`core/three_layer_memory`、`core/multimodal` 等）在代码树中不存在，一并按实核结果更正。

---

## 2. 产品形态与用户旅程

- **CLI**：`maop.ps1` / `py/maop/cli.py`，任务的提交与批处理入口。
- **Web 控制台**：单一 Vue 3 前端。源码 `dashboard-enterprise/` → `vite build` 产物
  `dashboard/dist-enterprise/` → `py/maop/dashboard/server.py` 统一服务两种 edition
  （证据：server.py 的 `_SERVE_DIR` 优先级链，注释明写 "both personal & enterprise"；
  旧版原生 JS 控制台已归档 `archive/js-dashboard/`）。
  企业能力在导航中按 `enterprise: true` 过滤（`src/nav.js`），个人版所见即所得。
- **双版双仓**：单代码库 + 运行时 Edition 检测（ADR-016），企业代码物理隔离到私有仓库 MAOS（ADR-017）。
  能力边界见 README 的 Personal/Enterprise 对比表。

一次典型旅程：提交任务 → **Plan**（路由到某个 CLI agent，附预算与门禁声明）→
**Execute**（派发到外部 CLI，工作树隔离）→ **Verify**（门禁校验）→ 记忆落盘 →
反馈进入演化建议（可审批、可回滚）。

---

## 3. 资产盘点

### 3.1 模块地图

规模基线（2026-09-29 实测）：`py/maop` 466 个 .py / 139,643 行；测试全量 10019 passed / 64 skipped /
0 failed / 覆盖率 84%。

| 分组 | 承载模块 | 说明 |
|------|----------|------|
| 循环主干 | `maop_loop*.py`、`engine*.py`、`maop_plan.py`、`maop_verify.py`、`delegate/` | Plan-Execute-Verify 的落点；`delegate/dispatcher.py` 是 shim，真身在 `dispatch_core.py` |
| 路由与调度 | `core/routing/`、`core/scheduling/`、`subagent_delegation.py` | RouteScorer、冷却、子代理委派 |
| 执行与隔离 | `core/agent/`、`core/backends/`、`core/cache/`、`core/data/` | agent 生命周期、存储抽象、工作树与沙箱 |
| 记忆与检索 | `memory/`、`core/memory/`、`core/vector/` | 三层记忆（工作/短期/长期）+ 向量检索 + 知识图谱 |
| 演化与学习 | `core/evolution/`、`evolve.py`、`cache_evolver.py`、`agent_strategy_learner.py` | 自演化闭环（v5.2.0 MVP）、A/B 框架 |
| 治理与安全 | `core/security/`、`core/tenant/`、`core/config/`、`budget_guard.py`、`personal_cost_guard.py` | 认证授权沙箱、租户隔离、预算守卫 |
| 可观测与成本 | `core/monitoring/`、`core/observability/`、`cost_tracker.py`、`core/reliability/` | 指标、链路、熔断、降级 |
| 集成与扩展 | `core/mcp/`、`core/marketplace/`、`plugins/`、`core/utils/` | MCP Hub、技能市场、插件 |
| 控制面 | `dashboard/`（62 个顶层 router 模块，含子目录共 79 个 .py）、`control/`、`concurrency.py` | FastAPI 服务与统一控制 |

`core/` 现为 **5 个顶层 .py + 18 个子包**（2026-09-29 实测）。README 架构表写
"5 files + 16 subpackages"，**已过时 2 个，须改**。

### 3.2 成熟度分级

- **A（稳定，有测试与门禁兜底）**：循环主干、路由、记忆、监控、成本、控制台前端、CI 门禁。
- **B（可用，待打磨）**：演化闭环（v5.2.0 MVP 已验收，UI 与叙事弱——见 RFC-001 P3）、
  技能市场、工作树、MCP Hub 的真实连接路径（websockets 依赖区间 2026-09-26 刚重判）。
- **C（证据不足或空壳，见第 6 节处置）**：`plugins/` 空目录、`archive/` 死代码、
  `deliverables/` 历史残留、`data/` 运行残留。

---

## 4. 数据层与技术选型

| 项 | 现状（2026-09-29 实测） |
|----|------------------------|
| 数据库 | SQLite + WAL + busy_timeout（env 可覆盖 `MAOP_SQLITE_BUSY_TIMEOUT_MS`）；表去重后 **130 个** CREATE TABLE 表名（含 1 个临时表 `_subagents_new`；其中 53 个来自 PG 迁移 `migrations/pg/versions/001_initial_schema.py`）。统计方法：正则扫描 `py/maop/**/*.py` 全部 CREATE TABLE 形式 |
| 迁移 | 根部 `alembic.ini(.template)` + `py/maop/migrations/` |
| 企业升配 | PostgreSQL / Redis / Vault / RabbitMQ(可选) / etcd(planned)，见 README 能力对比表 |
| 后端 | Python >= 3.10；FastAPI 0.141.1；Pydantic 2.x + pydantic-settings 2.15；uvicorn 0.53；httpx；PyJWT >= 2.13 |
| 前端 | Vue 3.5 / vue-router 5.3 / Pinia 4.0 / Vite 8 / vitest 3.2.7（实测 lock）/ Playwright 1.63 |
| 测试与门禁 | pytest 10019 passed（-n 4 全量，忽略 e2e）、覆盖率 84%；ruff；前端 typecheck + coverage 门禁（2026-09-27 起进 CI） |

已知失真：`docs/database-schema.md` 原写"101 张 distinct 表"（2026-08-26 口径），已按上述方法重算为
130 并更新；`.github/workflows/ci.yml:27-28` 注释称 vitest@5，实为 3.2.7（待修，见 6.2）。

---

## 5. 前端骨架（现状即目标态）

- **导航单源** `src/nav.js`：7 组任务流（首页 / 执行 / 记忆 / 能力 / 运维 / 分析 / 反馈）+ 企业"管理"组；
  旧路由经 `matchPaths` 301 重定向（`/control`、`/chat` → `/run` 等），外部书签不断。
- **组件基座已落地并被广泛接线**（2026-09-29 复核）：`ListPageLayout`（34 个引用文件）、
  `DetailDrawer`（12 个视图使用）、`FilterBar`、`PageHeader`（22 处）、`CommandPalette`、
  `CoachMarks`、`EvolutionTimeline`。
- **结论**：[product-design-rfc-001.md](product-design-rfc-001.md) 的三个迭代（A 信息架构 / B 差异化 /
  C 交互抹平）**已全部实现**——A 于 2026-09-01 以"7 组任务流"方案落地（与 RFC 里 6 分组的草案不同，
  以 nav.js 为准），B/C 组件如上。RFC 状态已更正为 Implemented。

---

## 6. 处置台账

### 6.1 删（死重）

| 对象 | 现状 | 处置 | 依据/风险 |
|------|------|------|-----------|
| `archive/` | 107 文件 / 1.2 MB，**全部被 git 跟踪**，零运行时引用 | 先 `git tag archive-v4.0-before-removal`，再 `git rm -r archive/` | 方案早已写好（`docs/audits/archive-cleanup-plan.md`，2026-08-16）但从未执行；注意 `dashboard/` 的 fallback 仍指向 `DASH_DIR`，删前确认无引用 |
| `plugins/` | 空目录，0 跟踪文件 | 删除，或补 README 说明插件目录约定 | 空目录无信息量 |
| `data/` 运行残留 | degradation 日志 11 个，文件名呈"链式拼接"（每次轮转把旧名当前缀，如 `degradation_..._20260913-232600.log`） | 清理；先查轮转命名 bug | 链式文件名是 bug 征兆（轮转未替换而是追加），不是设计 |
| `deliverables/` | 21 文件 / 4.6 MB（仅 9 个被跟踪） | 保留近期交付，其余移入 `docs/archive/` 或删除 | 需人工核对哪些是发布证据 |

### 6.2 改（失真与不一致）

| 对象 | 问题 | 处置 | 状态 |
|------|------|------|------|
| `technical-whitepaper.md` | "内置 31 个第三方 CLI 适配器"与 README 口径冲突；`\| MCP 工具市场 \| \`E\`core/mcp_*\` \|` 乱码；子系统表模块路径不存在（`core/agent_*`、`core/three_layer_memory`、`core/multimodal`） | 按 1.4 口径改写；修乱码；路径实核后更正（多模态标注为路线图项） | 已改 |
| `docs/database-schema.md` | 表计数过时（101 vs 实测 130） | 按第 4 节方法重算并更新首尾两处 | 已改 |
| `README.md` | 架构表 "16 subpackages" 过时（实测 18） | 改 | 已改 |
| `product-design-rfc-001.md` | 状态仍是 Draft + "待你回复" | 更正为 Implemented，新增"实施结果"节回答原 3 问 | 已改 |
| `.github/workflows/ci.yml` | 已知 6 处：:27-28 假称 vitest@5（实 3.2.7）；`publish` job（:822-828）只在 push.branches 触发、无 tags = 死代码；`pull_request.branches` 缺 develop；monitoring/deploy 被范围分类器归为 docs-only；Node 24 与 docker node:20 漂移；:793 trivy 缓存注释过期 | 逐项修复，修完在 ci-gates.md 记录语义 | 待执行 |
| `docs/README.md` | 索引称 api-reference "448 个端点（计数待复核）"；`_盘点_` 报告（2026-08-11）为 346 端点/41 router，现 router 模块已 62 个（顶层 .py，含子目录 79） | 以代码重算端点计数后统一两处口径 | 待执行 |
| `py/maop/dashboard/routers/sso.py` | 新增的 400 分支（缺 state）零测试覆盖 | 补路由级测试 | 待执行 |

### 6.3 留（不动）

循环主干、`core/` 子包、双版架构与 ADR、前端骨架与规范文档、PRD/HLD/ROADMAP 三层体系——
它们是产品的承重墙，本轮只做上面列的收口，不做重构。

### 6.4 补（缺失）

| 缺口 | 说明 |
|------|------|
| 定位成文 | 本文即交付（README 口径 + 白皮书更正后，定位在仓库内首次一致） |
| SSO 路由级测试 | 见 6.2 |
| 覆盖率地板 | 前端覆盖率门禁 2026-09-27 刚进 CI，尚无 ratchet 历史；观察 2-3 个 PR 后定基线 |

---

## 7. 执行顺序

1. **口径统一**（已完成 2026-09-29）：technical-whitepaper 更正（1.4 口径 + 模块路径）→
   README 子包计数 18 → database-schema 重算 130 → RFC-001 状态 → docs/README 索引。
2. **文档收口（去 AI 味）**：docs 40/113 含 emoji（881 处，重灾区在 `docs/archive/` 与
   design-review/audit 系列）；`audit-report-corrected(-v2)`、`design-review-report(-v2)` 等
   v1/v2 并存对合并或标注；CHANGELOG 中的会话叙事体（"变异验证"等）改为条目体。
3. **死重清理**：`archive/` tag → 删除；`plugins/`、`data/` 残留、`deliverables/` 归档。
4. **失真修复**：ci.yml 六项；如有行为变化同步 ci-gates.md。
5. **补测**：sso 路由 400 分支。
6. **提交**：按主题分批（口径合并为一批、清理为一批、CI 为一批），每批独立可回滚。

> 执行记录与本台账的勾选状态，在本文件内就地更新。
