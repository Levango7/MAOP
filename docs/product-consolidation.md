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
（`core/agent_*`、`core/three_layer_memory`、`core/multimodal` 等）在代码树中不存在，一并按实核结果更正。 <!-- docs-gate: skip=删除/更正台账，故意点名已不存在的路径 -->

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
| 控制面 | `dashboard/`（61 个顶层 router 模块，另有 `__init__.py`；含子目录共 79 个 .py）、`control/`、`concurrency.py` | FastAPI 服务与统一控制 |

`core/` 现为 **5 个顶层 .py + 16 个子包**（2026-09-29 实测；目录共 18 个，但 `core/cache/`、
`core/data/` 不含 `__init__.py`，按 lint job 守卫 `py/scripts/doc_reconcile.py` 的口径
"只统计真实包"不计入）。README 架构表写的 "5 files + 16 subpackages" **核对无误，不需要改**
—— 本文档初稿曾按"目录数 18"判它过时，是误判，已更正。

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
| 数据库 | SQLite + WAL + busy_timeout（env 可覆盖 `MAOP_SQLITE_BUSY_TIMEOUT_MS`）；表去重后 **130 个** CREATE TABLE 表名（含 1 个重建用临时表 `_subagents_new` 与 1 个版本账本表 `_migrations`；其中 53 个来自 PG 迁移 `migrations/pg/versions/001_initial_schema.py`）。统计方法：正则扫描 `py/maop/**/*.py` 全部 CREATE TABLE 形式 |
| 迁移 | 根部 `alembic.ini(.template)` + `py/maop/migrations/` |
| 企业升配 | PostgreSQL / Redis / Vault / RabbitMQ(可选) / etcd(planned)，见 README 能力对比表 |
| 后端 | Python >= 3.10；FastAPI 0.141.1；Pydantic 2.x + pydantic-settings 2.15；uvicorn 0.53；httpx；PyJWT >= 2.13 |
| 前端 | Vue 3.5 / vue-router 5.3 / Pinia 4.0 / Vite 8 / vitest 3.2.7（实测 lock）/ Playwright 1.63 |
| 测试与门禁 | pytest 10019 passed（-n 4 全量，忽略 e2e）、覆盖率 84%；ruff；前端 typecheck + coverage 门禁（2026-09-27 起进 CI） |

已知失真：`docs/database-schema.md` 原写"101 张 distinct 表"（2026-08-26 口径），已按上述方法重算为
130 并更新；ci.yml 的 vitest@5 假注释等 7 处失真已修（2026-09-29，见 6.2）。

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

### 6.1 删（死重）—— 已执行 2026-09-29

> 本表列出的"已删除对象"路径会触发 `py/scripts/check_docs_consistency.py` 的
> "路径不存在"提示，属预期——本表即是它们的删除记录，不必修复。

| 对象 | 现状 | 处置 | 依据/风险 | 状态 |
|------|------|------|-----------|------|
| `archive/` | 107 文件 / 1.2 MB，**全部被 git 跟踪**，零运行时引用 | 先 `git tag archive-v4.0-before-removal`，再 `git rm -r archive/` | 方案早已写好（`docs/audits/archive-cleanup-plan.md`，2026-08-16）但从未执行；删前已复核零运行时引用 | 已删（tag 已建；107 文件暂存删除；README/DESIGN_RULES/contributing/agents.yaml/maop.ps1/server.py 引用已同步） |
| `plugins/` | 空目录，0 跟踪文件 | 删除 | `PluginManager` 启动自建（`plugin_manager.py` 内 `mkdir(exist_ok=True)`） | 已删（目录已移除） |
| `data/` + `logs/` 链式日志 | 39 + 19 个链式拼接文件名（每次轮转把旧名当前缀） | 清理；先查轮转命名 bug | 链式名是 bug 征兆；根因 = `rotate_logs()` 从不跳过已轮转备份，备份被再轮转，源文件被重建为空 | 已修（log_rotate.py 跳过 `_ROTATED_RE` 命中项 + 2 条回归测试，8 passed）+ 残留移出（未跟踪文件，暂存仓库外可恢复区） |
| 根 `otel-collector-config.yaml` | 被 `deploy/otel-collector.yaml` 取代 | 删除 | P2-M-02 起 compose 挂载 deploy 版（见 `docker-compose.yml` 挂载注释），全仓零其他引用 | 已删 <!-- docs-gate: skip=删除/更正台账，故意点名已不存在的路径 --> |
| 根 `scripts/migrate_bridge_to_proxy.py` | 一次性迁移脚本 | 删除 | 重命名迁移 2026-07-26 已完成（`9bf19e58`），无调用方 | 已删 <!-- docs-gate: skip=删除/更正台账，故意点名已不存在的路径 --> |
| 根 `scripts/smoke_test_agents.py` | 首 import 即断 | 删除 | 功能已被 `py/tests/` 覆盖 | 已删 <!-- docs-gate: skip=删除/更正台账，故意点名已不存在的路径 --> |
| 根 `scripts/doc_reconcile.py` | README API 校验（朴素子串）+ 版本同步检查；CI 从未执行它 | 能力并入 `check_api_contract.py`（README 引用，prefix 感知）与 `check_config_drift.py`（版本同步）后删除 | CI lint job `working-directory: py`，实际执行的是 `py/scripts/doc_reconcile.py`（core 计数守卫） | 已删（能力已并入并各带负例实测） |
| `py/scripts/check_docs_consistency.py`（本轮新发现） | 全仓零引用；1449 条发现里约半数本是假阳性（MAOS 跨仓、包内/前端简写、`docs/archive/` 快照、配置默认值占位符） | 治假阳性后接入 CI：新 `docs-gate` 作业跑 `--gate`，范围由文档索引第 1–6 章决定，豁免必须写理由 | 宁可为"人工工具"而非伪装的覆盖；本轮它扫出 README 真死引用 1 处（`core/security/tenant.py` 已于 2026-09-25 合并进 `core/tenant/`） | 已接线（2026-09-29，附 `py/tests/test_docs_consistency_gate.py` 22 条守卫；顺带修掉 gitignore 深层目录匹配 bug 与计数口径未对齐 doc_reconcile 两处真 bug） <!-- docs-gate: skip=删除/更正台账，故意点名已不存在的路径 --> |
| `deliverables/` | 21 文件 / 4.6 MB（仅 9 个被跟踪） | 保留被引用文件与进行中 soak 输出；3 个可再生成产物移出 | `coverage-core-evidence.json` / `soak-1h-output.txt` / `soak-test-data-archive-20260831.csv` 均可重跑再生成 | 已清理（3 移出；9 跟踪 + 活跃输出保留） <!-- docs-gate: skip=删除/更正台账，故意点名已不存在的路径 --> |

### 6.2 改（失真与不一致）

| 对象 | 问题 | 处置 | 状态 |
|------|------|------|------|
| `technical-whitepaper.md` | "内置 31 个第三方 CLI 适配器"与 README 口径冲突；`\| MCP 工具市场 \| \`E\`core/mcp_*\` \|` 乱码；子系统表模块路径不存在（`core/agent_*`、`core/three_layer_memory`、`core/multimodal`） | 按 1.4 口径改写；修乱码；路径实核后更正（多模态标注为路线图项） | 已改 |
| `docs/database-schema.md` | 表计数过时（101 vs 实测 130） | 按第 4 节方法重算并更新首尾两处 | 已改 |
| `README.md`（**本条为误判，撤回**） | 初稿称架构表 "16 subpackages" 过时、应为 18 —— 18 是目录数；守卫口径是"含 `__init__.py` 的真包"= 16，README 原值正确（`py/scripts/doc_reconcile.py` 复验通过） | 不改（一度改成 18，已回滚） | 已复核 |
| `product-design-rfc-001.md` | 状态仍是 Draft + "待你回复" | 更正为 Implemented，新增"实施结果"节回答原 3 问 | 已改 |
| `.github/workflows/ci.yml` | 已知 6 处：假称 vitest@5（实 3.2.7）；`publish` job 只在 push.branches 触发、无 tags = 死代码；`pull_request.branches` 缺 develop；monitoring/deploy 被范围分类器归为 docs-only；Node 24 与 docker node:20 漂移；trivy 缓存注释过期。另有 push 腿回退 `head~1` 会截断新分支变更集 | 逐项修复，修完在 ci-gates.md 记录语义 | 已改（2026-09-29，7 项全修：注释改实况 / publish 标注不可达+启用路径 / 补 develop / 分类面加 `deploy/`+`monitoring/`+`alertmanager/`+三个根 conf 并带用例 / node:24-alpine 对齐并实测镜像构建 / trivy 注释更正 / 移除 head~1 回退） |
| `docs/README.md` | 索引称 api-reference "448 个端点（计数待复核）"；`_盘点_` 报告（2026-08-11）为 346 端点/41 router，现 router 模块 61 个（顶层 .py 共 62 个，含子目录 79） | 以代码重算端点计数后统一两处口径 | 已改（2026-09-29：口径落在 `api-reference.md`「端点计数」节 —— OpenAPI `paths`×方法，Personal 482 / 企业 554，不含 `/api/v1/*` 别名；README 索引与盘点报告已改为指向该口径） |
| `py/maop/dashboard/routers/sso.py` | 新增的 400 分支（缺 state）零测试覆盖 | 补路由级测试 | 已改（2026-09-29：`py/tests/test_sso_router_callback_guard.py`，4 例 —— 缺 state→400 / 缺 code→400 / IdP error→400 / Personal 版 404；企业包依赖，CI 按惯例 collect_ignore，本机跑） |
| `py/maop/dashboard/routers/relay_platform.py` | 三个写端点（注册含 `api_key`、删除、价格对比）**没有任何守卫**，文件甚至没 import `require_admin`，而 docstring 声称本 router 负责"权限检查"；同级 model_gateway / hooks 的写端点逐行调用 | 补 `require_admin(request)` + 鉴权回归测试 | 已改（2026-09-29：`test_relay_platform.py` 新增 3 条，非 admin→403 / 被拒注册不落库 / GET 口径不变，30 passed；`check_admin_coverage.py` 发现数 17→14） |
| `maop/config/settings.py` + `docs/configuration.md` | 两处都声明配置优先级里有 `config/settings.yaml` 这一层，但全仓无代码读取它（Pydantic Settings 只接 env 与 `.env`）—— 文档与注释承诺了能力没有的东西 | 按实核改为 env > `.env` > Field defaults，并在 docstring 写明不存在 YAML 层 | 已改（2026-09-29） <!-- docs-gate: skip=更正记录，点名当时写错的路径 --> |
| 当前权威文档的路径断言 | 新门禁扫出真错：`performance-benchmarks.md` 7 个重构前扁平模块路径、`contributing.md` 2 处、`troubleshooting.md` 的 `system.py` 与不存在的 `config/sso.yaml`、`user-guide.md` 的 `config/tenants.yaml`、`privacy-policy.md` 的 `data/.api-key`、`cla.md` 的 `docs/cla-employers.md`、`technical-whitepaper.md` 两处表格游离引号 | 逐条按代码实核更正（SSO 走 `POST /api/sso/providers` 的 `config` 字段；租户走 `/api/tenants/*`；密钥文件实为 `data/.enc_key`） | 已改（2026-09-29） <!-- docs-gate: skip=更正记录，点名当时写错的路径 --> |

### 6.3 留（不动）

循环主干、`core/` 子包、双版架构与 ADR、前端骨架与规范文档、PRD/HLD/ROADMAP 三层体系——
它们是产品的承重墙，本轮只做上面列的收口，不做重构。

### 6.4 补（缺失）

| 缺口 | 说明 |
|------|------|
| 定位成文 | 本文即交付（README 口径 + 白皮书更正后，定位在仓库内首次一致） |
| SSO 路由级测试 | 见 6.2 |
| 覆盖率地板 | 前端覆盖率门禁 2026-09-27 刚进 CI，尚无 ratchet 历史；观察 2-3 个 PR 后定基线 |
| 文档一致性门禁 | `check_docs_consistency.py --gate` 已接入新 `docs-gate` 作业（范围来自文档索引第 1–6 章，豁免必须写理由并逐条打印），守卫 `test_docs_consistency_gate.py` 22 条 |

---

## 7. 执行顺序

1. **口径统一**（已完成 2026-09-29）：technical-whitepaper 更正（1.4 口径 + 模块路径）→
   README 子包计数（复核后原值正确，见 6.2）→ database-schema 重算 130 → RFC-001 状态 →
   docs/README 索引。
2. **文档收口（去 AI 味）**（已完成 2026-09-29，`docs/archive/` 与 `deliverables/` 除外）：
   先重算口径——活跃 docs 实为 **26 个文件 / 400 处** emoji（`✅❌⚠️🔴🟡🟢` 等严格 emoji 集；
   此前"40/113、881 处"混入了框线符（`─│┌` 等）与 `✓`，粗算作废），已全部清零，覆盖
   `design-review-report(-v2)`、`audit-report-corrected(-v2)`、`evaluation-report`、
   `prd-sso-integration`、ADR-008/016/017/019、`enterprise/*`、`quickstart`、`migration-5.0` 等；
   根级 `ROADMAP`/`SECURITY`/`CHANGELOG` 同步清零，README 只清 `⚠️`、特性矩阵的 `✓/✗/○` 属排版记号保留。
   v1/v2 并存对（`audit-report-corrected(-v2)`、`design-review-report(-v2)`）此前已加 SUPERSEDED
   标注并在 docs/README 建索引，本轮复核确认无需再动。CHANGELOG 顶部会话叙事体（8 节 / 175 行）
   改写为 `## [Unreleased] - 2026-09-29` 日期化条目体（Fixed/Added/Changed），保留证据
   （测试名、变异验证、已知限制），去掉"过程记录/自我复盘"叙事。
   `deliverables/`（21 文件）随第 3 步"保留/删除"决策后再处理。
   同批遗留的 `docs/archive/`（15 文件 / 476 处 emoji，357 行）已于 2026-09-29 清理轮补完：
   正文删除、纯 emoji 单元格按表头换成文字，`design-system-legacy.md` 因"emoji 是文档内容本身"
   豁免；改动经逐行核对（行数 / 每行 `|` 数 / 只动含 emoji 的行）确认无结构性破坏。
   `deliverables/` 的 emoji 也已清零（4 个被跟踪文件；该目录整体在 `.gitignore` 内，只有历史
   跟踪的 9 个文件可入库）。此批核对时发现 `docs/archive` 那批把 `✓` 也吞了 2 处（与其声明
   不符，语义未受损），已随该批恢复并把清理字符类改为显式跳过 `✓`/`✗`。
   至此"去 AI 味"口径覆盖：活跃 docs + 根级文档 + docs/archive + deliverables。
3. **死重清理（已完成 2026-09-29）**：`archive/` tag → `git rm -r archive/`（107 文件）；
   根 `otel-collector-config.yaml`、`migrate_bridge_to_proxy.py`、`smoke_test_agents.py`、 <!-- docs-gate: skip=删除/更正台账，故意点名已不存在的路径 -->
   `scripts/doc_reconcile.py` 删除；空 `plugins/` 移除；链式日志残留（39+19）**先修
   `log_rotate` bug 再移出**；`deliverables/` 3 个可再生成产物移出。明细与依据见 6.1。
   未跟踪文件的移出是**可恢复**操作（暂存于仓库外 `F:/Nexus/.maop-cleanup-trash-20260929/`，
   含 MANIFEST.md），跟踪文件的删除以 `archive-v4.0-before-removal` tag 与 git 历史兜底。
4. **失真修复**：ci.yml 七项（已完成 2026-09-29，见 6.2）；行为变化已同步 `docs/ci-gates.md`。
5. **补测**：sso 路由 400 分支（已完成，见 6.2）。
6. **提交（清理轮 4 批已提交 2026-09-29，本地不推送）**：`d8e1738` log_rotate 修复；
   `e722754` 死重清理 + 全仓引用同步；`89908c0` 脚本能力整合与硬化；`f59ca35` 台账与 CHANGELOG。
7. **三项挂账收口（2026-09-29 本轮，待逐批确认后提交）**：relay_platform 写端点鉴权（+3 条
   鉴权测试）；`docs/archive/` emoji 清零；`check_docs_consistency` 治假阳性并接入 CI（新
   `docs-gate` 作业 + 22 条守卫）。同轮顺带修掉 `config/settings.yaml` 幽灵加载层与 9 处 <!-- docs-gate: skip=更正记录，点名当时写错的路径 -->
   当前权威文档的路径错。建议批次：批 E 鉴权（router + 测试）、批 F 文档一致性门禁
   （脚本 + ci.yml + ci-gates.md + 守卫测试 + 文档路径订正 + 豁免标记）、批 G 归档 emoji 清理。

> 执行记录与本台账的勾选状态，在本文件内就地更新。
