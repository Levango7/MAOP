# MAOP 全局审查报告（v5.1.0）

> 审查日期：2026-08-14 · 审查对象：F:\Nexus\MAOP（master @ 8fdd389）
> 审查范围：产品方向 → 框架/模块 → 功能 → 后端 → 前端 → AI/Agent → 测试 → 部署 → 文档
> 审查方式：4 个并行探索代理 + 人工抽查关键 P0 结论（均已验证属实）

---

## 一、执行摘要

MAOP 是一个**"外壳扎实、核心偏薄、文档先行"**的项目。安全侧与工程基建已达一线水平（无硬编码密钥、无 `shell=True`/`eval`、AST 白名单求值器、JWT/PBKDF2 规范、CI 覆盖 SAST/依赖/镜像扫描/多平台矩阵），但存在三个相互关联的系统性问题：

1. **文档宣称与代码现实系统性背离**——覆盖率宣称 85%、实际 18.5%；README 写 107+ 模块、实际 289；schema 文档写 53 表、实际 117 表。这不是偶发漂移，而是"文档先于实现"的工程文化。
2. **假功能/半成品**——刚发布的 v5.1.0 企业版 6 大功能中，4 个页面端点错配或未实现（点击即 404）；模型选择/降级/配额"三件套"在主执行路径是死代码。
3. **架构债未收尾**——78 组字节级重复文件（旧扁平 `core/*.py` vs 新子包）、三套并存的记忆系统、40+ 个超 500 行的"上帝模块"。

**总体健康度**：约 **6.5/10**。可运行、可交付，但"产品定位""核心 AI 能力""文档诚信"三件事需要优先纠偏。

---

## 二、产品方向审查（最高优先）

### 2.1 现状

- **双版架构**（ADR-016）：单代码库 + 运行时 Edition 检测，Personal（MIT/SQLite/开箱即用）+ Enterprise（Commercial/RBAC/SSO/审计/PG/Redis/Vault）。架构设计本身清晰、FeatureFlag 统一 gate，是加分项。
- **版本演进**：v5.0.0（废弃清理）→ v5.1.0（今日发布，企业版 6 功能 + 6 新功能，一次性塞入 12 个功能）。
- **路线图**：`ROADMAP.md`（版本单一真相源）+ `docs/archive/plans/prd-three-phase-roadmap.md`（21 个月三阶段，Draft）+ `docs/product-design-rfc-001.md`（前端产品设计演进，Draft）。

### 2.2 四个核心产品问题

**① 定位漂移：自称"多智能体编排平台"，实为"多进程 CLI 调度器"**

主执行路径是 `subprocess` 调用外部 CLI（codex/cline/cursor 等 31 个第三方 agent），LLM provider 仅在 chat/analyzer/react_loop 使用。这意味着 MAOP 的核心价值是**"把多个外部 agent CLI 编排成 Plan-Execute-Verify 流程"**，而非"承载自研 agent 的运行时"。产品叙事（Multi-Agent Orchestration Platform、Agent 自演化、生态）与真实能力（CLI 编排器）之间存在显著落差。

**② 功能优先级错位：为填版本号做功能，而非按用户价值排序**

v5.1.0 一天塞入 12 个功能，但其中企业版 4 个页面端点错配（ApiKeys/Licenses/Quotas/WorkflowEditor 执行）、模型选择/降级/配额三件套是死代码。真正有用户价值的"成本告警配置通道"（本次 P3 审计唯一确认的功能缺口）反而长期缺失。这暴露出"功能清单驱动"而非"用户问题驱动"的研发节奏。

**③ 路线图过度规划：21 个月三阶段与单人开发现实脱节**

PRD 假设"阶段一启动时团队 6-8 人、阶段二 10-12 人、阶段三 15-20 人"，且阶段一（分布式执行/pgvector/记忆统一/可观测性）尚未启动，却已规划到阶段三的联邦学习与 Agent Marketplace。对当前实际（单人 vibe-coding）而言，这份 21 个月路线图是"战略性空转"，建议收缩为"3 个月可交付的增量路线图"。

**④ 产品差异化记忆点未兑现**

RFC-001 自身诊断精准：MAOP 独有的 **Plan-Execute-Verify 循环** 与 **Agent 演化时间线** 这两个"别处没有"的概念，在 UI 上只是普通列表，完全没"讲成故事"。但 RFC 状态仍是 Draft，迭代 A（信息架构）只完成约 1/3，迭代 B（差异化/Wow moment）未启动。

### 2.3 产品方向建议（按优先级）

| 优先级 | 建议 | 依据 |
|---|---|---|
| P0 | **诚实定义产品定位**：对外明确"MAOP = 多外部 agent CLI 的编排与治理层"，弱化"自研 agent 运行时/自演化生态"的叙事 | 核心 AI 能力是薄封装 |
| P0 | **冻结新功能，先修假功能**：停止堆功能，把 v5.1.0 已宣称的 12 个功能全部做到"点开不 404" | 4 页端点错配 |
| P1 | **收缩路线图到 3 个月**：只保留"分布式执行（如果真有并发需求）+ 记忆统一 + 可观测性"三件事，砍掉阶段二/三的联邦学习/生态 | PRD 与现实脱节 |
| P1 | **兑现差异化**：执行 RFC-001 迭代 B，把 Plan-Execute-Verify 和演化时间线做成产品记忆点 | 唯一的竞争壁垒 |

---

## 三、框架与模块架构

| 问题 | 严重度 | 证据 |
|---|---|---|
| 78 组字节级重复文件（156 个）——旧扁平 `core/*.py` 与新子包 `core/{agent,reliability,memory,...}/*.py` 一一重复 | **P0** | 如 `core/circuit_breaker.py` ≡ `core/reliability/circuit_breaker.py`（各 717 行）；旧路径 import 计数≈0，纯死代码 |
| `mcp_hub` 分叉两版 | **P1** | `core/mcp_hub.py`(1091 行) vs `core/mcp/mcp_hub.py`(1151 行)，已产生漂移 |
| 三套独立记忆系统 | **P1** | 顶层 `memory/`、`core/memory/`（三层认知记忆）、`core/agent/memory_ctx/`，语义重叠 |
| 40+ 上帝模块（>500 行） | **P2** | 最大 `three_layer_memory.py`(1393)、`evolution_loop.py`(1347)、`mcp_hub.py`(1151) |

---

## 四、功能完整性

| 功能 | 状态 | 问题 |
|---|---|---|
| ApiKeys 管理 | **P0 假功能** | 前端全页调 `/api/auth/api-keys`，后端是 `/api/api-keys`；后端无 PUT 编辑端点 |
| License 管理 | **P0 假功能** | 前端 `/api/license/*`（单数），后端 `/api/licenses/*`（复数）；生成端点 `/license/generate` vs 后端 `/create` |
| 配额管理 | **P0 假功能** | 前端 `/api/tenant/{id}/usage/trend` 等，后端在 `/api/quotas/*`，tenant.py 无这些路径 |
| 工作流编辑器执行 | **P1 未实现** | `WorkflowEditor.vue` 调 `/api/dag/execute`，`dag.py` 只有 auto-split/health |
| Evolve metrics / SkillEditor / SkillMarket | **P1 未实现** | 前端调用不存在的端点 |
| 成本告警配置 | ✅ 已补（本次） | env/API/UI 三通道已落地 |

**结论**：v5.1.0 的"企业版 6 大功能"与"6 大新功能"中，约 **1/3 是"有 UI 无后端"的假功能**。

---

## 五、后端代码

| 问题 | 严重度 | 证据 |
|---|---|---|
| 139 处静默吞异常（`except ...: pass/continue`） | **P1** | `cli.py:70`、`evolve.py:443`（回滚失败被吞，配置可能丢失）、`maop_loop.py:389/710` |
| 动态 SQL 拼接缺白名单 | **P2** | `db_backup.py:207` 的 `VACUUM INTO '{backup_path}'`、`subagent_db.py:326` 的 `ALTER TABLE ADD COLUMN {col}` |
| `NotImplementedError` 占位 8 处 + 空函数体 72 处 | **P2** | `vector/__init__.py:111`、`notification/channels.py:120` |

**正面**：安全侧扎实——无硬编码密钥、无 `shell=True`/`eval`、JWT 用 PBKDF2 + httpOnly cookie、`safe_eval` AST 白名单 default-deny。

---

## 六、前端 UI 与交互

| 问题 | 严重度 | 证据 |
|---|---|---|
| 4 个企业版页面端点错配（见 §四） | **P0** | 点击即 404 |
| 交互一致性未收敛：ListPageLayout 复用 10/32 视图，FilterBar 仅 2 视图复用 | **P2** | RFC-001 迭代 C 只做了 1/3 |
| 硬编码颜色：Monitor.vue 16 处、Observability.vue 25 处 | **P2** | 未走 CSS var |
| 硬编码文案：Docs/Licenses/ApiKeys 中英文内联兜底 | **P2** | 未走 i18n |
| 死路由文件：ControlPanel.vue / Chat.vue / EvolutionHistory.vue 已移除路由但残留 | **P2** | 死代码 |

**正面**：信息架构已按 RFC-001 重排为 6 组；统一组件基座（ListPageLayout/DetailDrawer/FilterBar/EmptyState）已建立。

---

## 七、AI 调用与 Agent 能力

| 问题 | 严重度 | 证据 |
|---|---|---|
| **ModelSelector / QuotaEnforcer / FallbackManager 主路径死代码** | **P0** | `dispatcher.py:596` 仅在 `self._model_selector is not None` 时选择（恒 False）；`ModelSelector(` 全局仅 dashboard 端点 + docstring 示例实例化 |
| LLM Provider 异常被吞成 `"[LLM Error]"` 字符串 | **P1** | `llm_provider.py:208-219`，依赖前缀嗅探降级 |
| 自演化是"阈值规则"非"LLM 自演化" | **P1** | `agent_evolution.py:109-195`，auto_apply 多数只写记忆不改配置 |
| `agent_performance` 主路径从不调 `record()` | **P1** | 自适应路由无数据源 |
| Verify 阶段只做 exit_code+非空+正则，无语义验收 | **P2** | `maop_verify.py:48-129` |
| 无工具白名单/权限校验 | **P2** | 任何入库命令均可执行（虽有 shlex.split + shell=False） |
| 31 个 agent 全是第三方 CLI 的 subprocess 封装，无内部 agent 实现 | **P1** | `agents.yaml` |

**结论**：编排/可靠性/可观测"外壳"是真实实现，但核心 AI 智能层是薄封装——**"优秀的多进程调度器 + 半成品的 Agent 智能层"**，成熟度约 6/10。

---

## 八、测试体系

| 问题 | 严重度 | 证据 |
|---|---|---|
| **覆盖率宣称 85% vs 实际 18.5%** | **P0** | `check_coverage_ratchet.py` `FLOOR=18.0`；ROADMAP v4.4.2 宣称"CI 阈值 ≥ 85%" |
| 性能/稳定性测试未入 CI 门禁 | **P1** | `ci.yml` 仅 `-m "not slow"`，k6/locust/reliability 从不触发 |
| Playwright e2e 未起后端，依赖 `/api` 的用例无法真实跑通 | **P2** | `webServer` 只起 vite dev，CI e2e job 无后端 service |

**正面**：单测 200+ 文件、contract 6 文件、ratchet 脚本机制正确（只升不降）。

---

## 九、运维部署

| 问题 | 严重度 | 证据 |
|---|---|---|
| `--profile monitoring` 断线：prometheus `depends_on` otel-collector（属 otel profile），单独启动报错 | **P1** | `docker-compose.yml` |
| 生产告警规则未挂载：prometheus 引用 `alerts.yml`/`slo-alerts.yml` 但未 mount | **P1** | `docker-compose.prod.yml` |
| alertmanager 所有 receiver 被注释，无真实告警出口 | **P2** | `alertmanager.yml` |
| 个人版空 JWT 密钥 + agent-exec/queue-worker 无 healthcheck | **P2** | `docker-compose.yml` |

**正面**：生产 compose 密钥全 `:?` 强制注入、无硬编码；Vault/Patroni/PG/Redis 健康检查齐全；`/api/health` + `/api/prometheus` + OTel 已真实接线。

---

## 十、文档

| 问题 | 严重度 | 证据 |
|---|---|---|
| `database-schema.md` 宣称 53 表，实际 117 张 `CREATE TABLE` | **P1** | 差 2.2 倍 |
| 覆盖率 85%（ROADMAP）vs 18.5%（代码） | **P0** | 见 §八 |
| `docs/archive/` 11 份 + 根级 20+ 计划/评审 md 冗余堆积 | **P3** | 历史审查报告未清理 |

**正面**：版本号全线一致（均为 5.1.0）；ADR 体系完整（16 篇）。

---

## 十一、问题总表（按严重度罗列）

### P0（阻断，立即修）

| # | 问题 | 维度 |
|---|---|---|
| 1 | 企业版 4 页端点错配/未实现：ApiKeys、Licenses、Quotas、WorkflowEditor 执行 | 功能/前端 |
| 2 | ModelSelector/QuotaEnforcer/FallbackManager 主路径死代码 | AI/Agent |
| 3 | 覆盖率宣称 85% vs 实际 18.5%（文档诚信） | 测试/文档 |
| 4 | 78 组字节级重复文件（旧扁平 core/*.py） | 架构 |

### P1（高，本阶段修）

| # | 问题 | 维度 |
|---|---|---|
| 5 | 139 处静默吞异常 | 后端 |
| 6 | 三套记忆系统并存 | 架构 |
| 7 | 假功能端点：Evolve metrics / SkillEditor / SkillMarket | 功能 |
| 8 | 自演化是规则非 LLM、agent_performance 无数据源 | AI/Agent |
| 9 | LLM Provider 异常吞成字符串 | AI/Agent |
| 10 | 监控 profile 断线 + 生产告警规则未挂载 | 部署 |
| 11 | schema 文档过期 2 倍 | 文档 |

### P2（中，排期修）

| # | 问题 | 维度 |
|---|---|---|
| 12 | 40+ 上帝模块、mcp_hub 分叉 | 架构 |
| 13 | 交互一致性（ListPageLayout/FilterBar 复用率低） | 前端 |
| 14 | 硬编码颜色/文案 | 前端 |
| 15 | 性能/稳定性测试未入 CI、e2e 未起后端 | 测试 |
| 16 | 动态 SQL 拼接缺白名单、无工具白名单 | 安全 |
| 17 | alertmanager 无告警出口、个人版空 JWT | 部署 |

---

## 十二、解决方案

### 方案 A：修假功能（对齐"宣称"与"现实"）

1. 建立**前后端契约测试**（contract test）覆盖所有前端调用的端点，让"有 UI 无后端"在 CI 即失败。
2. 逐个修复 4 个企业版页面的端点契约（ApiKeys/Licenses/Quotas/WorkflowEditor），或降级为"明确标注 Coming Soon"而非静默 404。
3. 把模型选择/降级/配额三件套真正接入主执行路径（Dispatcher 注入 `model_selector`），或干脆删除并诚实标注"未实现"。

### 方案 B：清架构债

1. 删除旧扁平 `core/*.py` 死代码层（保留 re-export shim 兜底），消除 78 组重复文件。
2. 记忆系统三合一：以 `core/memory/`（三层认知记忆）为唯一实现，迁移并删除另两套。

### 方案 C：修复文档诚信

1. 建立**文档与代码自动对账**：覆盖率、模块数、表数、API 列表用脚本生成，禁止手写静态数字。
2. 更正 ROADMAP 覆盖率宣称，或在 ratchet 基线里补充"85% 为历史目标、当前 18.5% 为现实起点"的诚实说明。

### 方案 D：产品方向纠偏

1. 对外明确"CLI 编排与治理层"定位。
2. 冻结新功能，执行 RFC-001 迭代 B（兑现差异化）。
3. 收缩路线图到 3 个月增量。

---

## 十三、分阶段规划

### Phase 0 — 止血（本周，1-2 天）

- 修复 4 个企业版页面端点契约（ApiKeys/Licenses/Quotas/WorkflowEditor）。
- 更正覆盖率/模块数/schema 三处文档漂移。
- 补前后端契约测试，纳入 CI 门禁。

### Phase 1 — 诚实化（1-2 周）

- 决策并落地模型选择三件套：接入主路径 或 删除标注未实现。
- 清理 78 组重复文件 + 统一记忆系统（三合一）。
- 139 处静默吞异常加结构化日志。
- 修复监控 profile 断线 + 挂载生产告警规则。

### Phase 2 — 收敛（2-4 周）

- 前端交互收敛（ListPageLayout/FilterBar 全量迁移）+ 硬编码颜色/文案清零。
- 自演化/agent_performance 二选一：补数据源做真智能，或降级为"显式规则引擎"并诚实命名。
- e2e 起后端、性能测试入 CI。

### Phase 3 — 战略重定位（与用户对齐后启动）

- 重新定义产品定位与 3 个月路线图（替代 21 个月 PRD）。
- 兑现差异化：Plan-Execute-Verify + 演化时间线的"讲成故事"。
- 若确认有真实并发需求，再启动阶段一（分布式执行），否则搁置。

---

## 附：各维度健康度评分

| 维度 | 评分 | 一句话 |
|---|---|---|
| 产品方向 | 5/10 | 定位漂移、路线图过度规划、差异化未兑现 |
| 框架/模块 | 5/10 | 78 组重复文件、三套记忆、上帝模块 |
| 功能完整性 | 5/10 | 1/3 企业版功能是假功能 |
| 后端代码 | 7/10 | 安全扎实，异常处理与死代码待清 |
| 前端 UI/交互 | 6/10 | 基座已建、迁移 1/3、端点错配致命 |
| AI/Agent 能力 | 5/10 | 外壳扎实、核心智能薄封装 |
| 测试体系 | 7/10 | 规模够、但覆盖率宣称失实、e2e 未闭环 |
| 运维部署 | 7/10 | 生产配置规范，监控/告警链路未闭环 |
| 文档 | 5/10 | ADR 体系好，但数字系统性过期 |
| **综合** | **6.5/10** | 可交付，需先纠偏产品定位与文档诚信 |
