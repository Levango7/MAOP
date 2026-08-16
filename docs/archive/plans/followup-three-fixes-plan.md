# MAOP 三项遗留修复 — 需求分析与设计方案

> 生成日期：2026-08-14 · 基于 master @ 653f743 实测
> 性质：**方案文档（待审核）**，用户确认后按阶段执行

---

## 第 1 项：自演化「补真智能 or 诚实改名」

### 1.1 现状（实测）

| 组件 | 实际实现 | 数据流 |
|------|---------|--------|
| `core/agent/evolution/agent_evolution.py` | **规则驱动**：`_analyze_performance/reliability/capabilities/preferences/error_lessons` 全部是阈值规则（latency > X ms、failure rate > Y%），无 LLM 建议器 | `evolve()` → 规则分析 → `_apply_suggestion()` |
| `_apply_suggestion()` | **部分真应用**：capability 建议通过 `ConfigMutator` 真正改 `agents.yaml`；preference/reliability 仅记录记忆 + 手动确认 | — |
| `core/agent/lifecycle/agent_performance.py` | `AgentPerformanceTracker` 有 SQLite 存储，被 `route_scorer`（多目标路由）、`maop_plan`（自适应路由）、`agent_strategy_learner` **读取** | **断链**：`record()` 主路径（dispatcher/maop_loop）**从不调用**，数据仅靠 `sync_from_episodic` 手动同步 |

### 1.2 问题本质

"自演化"的**宣称**（PRD F2-01、Evolve UI"Agent 演化时间线"）与实际（规则引擎 + 数据断链）存在落差。但**不是完全假**：capability 自动应用是真功能，路由读取性能数据是真链路，缺的是：

1. **数据采集断链**：主执行路径不 `record()` 执行数据 → 自适应路由和分析依赖的输入是空/手动。
2. **无 LLM 建议器**：分析全是固定阈值规则，无"基于经验的智能建议"。
3. **叙事夸大**：UI/文档用"演化/Evolve"词汇，未说明是规则驱动。

### 1.3 方案选项

| 选项 | 内容 | 工作量 | 风险 |
|------|------|--------|------|
| **A. 补真智能** | 完整实现 PRD F2-01：LLM 建议器 + A/B 测试 + 自动部署闭环 | 大（数周） | 高（行为变更 + 成本 + 需数据支撑） |
| **B. 诚实改名** | 停用"自演化"叙事，UI/文档改为"规则驱动自动调优（Rule-based Auto-tuning）" | 小（改文案） | 低 |
| **C. 折中（推荐）** | **先修数据断链，再可选加 LLM 建议，同步诚实表述** | 中 | 中低 |

### 1.4 推荐方案 C（分三步）

**C1 — 修复性能数据断链（缺陷修复，安全）**
- 在 `dispatcher.dispatch()` 成功/失败路径调用 `AgentPerformanceTracker.record()`（接线主路径）。
- 目标：路由决策和分析器有真实数据输入。
- 验收：一次 dispatch 后 `agent_performance` 表有记录；`test_performance_*` 相关测试通过。

**C2 — LLM 建议器作为可选增强（独立可回退）**
- `agent_evolution` 新增 `LLMSuggester`：调 LLM provider 生成改进建议；**默认关闭**（`MAOP_EVOLVE_LLM_SUGGEST=0`），失败自动降级回规则引擎。
- 验收：开启后 analyze 输出含 LLM 建议；关闭时行为与现状一致。

**C3 — 诚实表述（文案，低风险）**
- Evolve UI 与文档：标题/描述从"Agent 自演化"改为"Agent 自动调优（规则驱动，可选 LLM 建议）"。
- README 能力矩阵标注：自演化 = 规则驱动 + 可选 LLM。

---

## 第 2 项：前端 ListPageLayout 复用率 / 硬编码

### 2.1 现状（实测）

- **ListPageLayout 复用 10/32** 视图：ApiKeys/Licenses/Notifications/Quotas/SkillEditor/SkillMarket/SsoProviders/Tenants/Users/WorkflowEditor。
- 未用的 22 个中，**真正"列表页"**：Agents（1309 行）、Audit、Models、Tools、Tasks、RBAC、Cost（约 7 个）；其余（Overview/Monitor/Observability/KnowledgeGraph/Search/VectorSearch/Chat/Run/Docs/Settings/Logs/Evolve/EvolutionHistory/ControlPanel/ThreeLayerMemory）是**仪表盘/搜索/图表/配置页，不该用列表布局**。
- **硬编码文案 41 处**：全部是 catch 分支的英文兜底（`toast.error(e.message || 'Generate failed')`），主文案已走 i18n。

### 2.2 问题本质

- "复用率低"的准确问题 = **该用列表页布局的 ~7 个视图没用**，不是"22 个全该用"。
- 硬编码 41 处是 i18n 收尾不彻底（catch 兜底漏网）。

### 2.3 方案（按风险递增排序）

**F1 — 硬编码文案清零（机械，安全）**
- 为 41 处兜底文案补 i18n key（`view.xxx.failed` 等），替换硬编码英文。
- 验收：`grep "toast.error('"` 在 views/ 下为 0（除 `e.message` 动态部分）；前端 305 测试全绿。

**F2 — 列表页迁移（先小后大）**
- 第一批（小）：Tools / Models / RBAC / Tasks → 迁移 ListPageLayout。
- 第二批（大）：Audit / Cost / Agents（1309 行，需先拆组件）。
- 验收：每个迁移后对应 vitest 通过；交互与迁移前一致。

**F3 — 明确"非列表页"边界（文档）**
- 在 `docs/frontend-style-guide.md` 写明：哪些视图类型用 ListPageLayout（CRUD 列表）、哪些不用（仪表盘/图表/配置页），防止后续误用/误评。

---

## 第 3 项：产品定位叙事

### 3.1 现状

- README 标题：**"MAOP — Multi-Agent Orchestration Platform"**；能力矩阵宣称"多代理编排"。
- 实际：31 个 agent 全是第三方 CLI 的 subprocess 封装；内置 LLM provider 仅用于 chat/analyzer/react_loop；主执行路径是"CLI 调度 + 编排 + 治理"。

### 3.2 问题本质

"多智能体平台"的叙事暗示了"承载自研 agent 运行时"，但现实是"编排外部 CLI agent"。不是贬义（编排+治理是真实价值），是**叙事与能力错位**。

### 3.3 方案选项

| 选项 | 内容 | 影响 |
|------|------|------|
| A. 维持叙事 + 补真智能 | 长期把 MAOP 建成真正的 agent 运行时（自研 agent 实现），叙事不变 | 数月工程 |
| B. 诚实调整叙事（推荐） | 标题微调为"Multi-Agent Orchestration Platform（多 Agent 编排与治理层）"，README 能力矩阵注明"Agent 运行时 = 外部 CLI 适配器；内置 LLM 用于对话/分析" | 文案 + 文档 |
| C. 完全不改 | 维持现状 | 叙事与能力继续错位 |

### 3.4 推荐

- **本轮（低成本）**：选项 B——README 定位段 + 能力矩阵加 2-3 行诚实说明；`docs/technical-whitepaper.md` 架构描述同步。
- **长期（待你决策）**：选项 A 是战略方向（需排期）；在此之前叙事保持 B 的诚实基调。

---

## 执行顺序与依赖

```
Phase 1（安全，可立即执行）: C1 数据断链修复 + F1 文案清零 + B 定位文档
Phase 2（中等，需验证）   : C2 LLM 建议器(默认关) + F2 第一批列表页迁移
Phase 3（大，需决策）     : F2 第二批(Agents 拆分) + A/B 战略选型
```

**互不阻塞**：三项各自独立，Phase 1 的三件可并行。

---

## 审核清单

| 检查项 | 结论 |
|--------|------|
| 是否破坏现有行为？ | Phase 1 全部非破坏（C1 是补数据记录、F1 是文案、B 是文档）；C2 默认关；F2 需逐个验证 |
| 是否引入测试漂移？ | 每步执行后跑对应 vitest + pytest（吸取 653f743 教训） |
| 是否涉及删除/拆文件？ | 不涉及（避免沙箱 git rm 风险） |
| 验收标准 | 见各方案小节 |

---

## 待你拍板的决策点

1. **第 1 项**：认可"先修数据断链（C1）+ 可选 LLM（C2）+ 诚实表述（C3）"的折中路径？还是直接选 B（纯诚实改名，不补智能）？
2. **第 2 项**：列表页迁移范围——第一批（Tools/Models/RBAC/Tasks）先做，Agents 放第二批？
3. **第 3 项**：接受 README/白皮书的"编排与治理层"诚实表述？（不涉及产品改名）
