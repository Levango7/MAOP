# MAOP Agent 自演化使用指南

## 1. 架构概述

MAOP 自演化系统实现了一个七段闭环的持续改进机制，从错误观测到知识巩固形成完整反馈回路。系统包含两条并行演化的闭环实现：

- **EvolutionLoop**（错误驱动）：基于 ErrorLedger 错误热点驱动的七段闭环，用于错误自愈与配置调整。
- **PerformanceEvolutionLoop**（性能指标驱动）：基于 OTel traces 性能指标 + AB/SPRT 序贯统计检验的闭环，用于 prompt/参数/路由的 A/B 验证与自动提升。

### 1.1 七段闭环

```
OBSERVE → HEAL → SUGGEST → EVALUATE → APPLY → VALIDATE → CONSOLIDATE
```

| 阶段 | 说明 | 数据源 |
|------|------|--------|
| OBSERVE | 从 ErrorLedger 读取未愈错误热点 | `error_ledger` 表 |
| HEAL | 调用 SelfHealEngine 尝试自动修复已知错误模式 | SelfHealEngine |
| SUGGEST | 生成改进建议（LLM 或规则驱动） | ImprovementSuggester |
| EVALUATE | 评估建议（auto_applicable 或 human_gate） | StrategyEngine |
| APPLY | 应用批准的建议（或 dry-run 预览） | ConfigMutator |
| VALIDATE | 验证应用后指标是否改善 | ErrorLedger 热点对比 |
| CONSOLIDATE | 将有效模式写入 Semantic Memory 供后续复用 | MemoryFacade |

### 1.2 核心模块

| 模块文件 | 类/职责 |
|----------|---------|
| `py/maop/core/evolution/evolution_loop.py` | `EvolutionLoop` — 错误驱动七段闭环主循环 |
| `py/maop/core/evolution/evolution_perf_loop.py` | `PerformanceEvolutionLoop` — 性能指标驱动闭环（含 LLM 驱动 + human_gate） |
| `py/maop/core/evolution/suggester.py` | `ImprovementSuggester` — LLM 路径 + 规则回退的建议生成器 |
| `py/maop/core/evolution/evaluator.py` | `PerformanceEvaluator` — 从 OTel traces 计算延迟/成功率/成本等指标 |
| `py/maop/core/evolution/ab_test.py` | `ABTestFramework` — 序贯统计检验（SPRT）+ 流量分流 |
| `py/maop/core/evolution/auto_deployer.py` | `AutoDeployer` — 优胜提升/劣化回滚（ChangeTracker 快照） |
| `py/maop/core/evolution/narrative.py` | `EvolutionNarrative` — LoopReport → Markdown/JSON 叙事 |
| `py/maop/core/evolution/evolution_phases.py` | `EvolutionPhasesMixin` — 七段闭环各阶段实现 |
| `py/maop/core/evolution/evolution_loop_types.py` | 类型定义（`LoopReport`, `PhaseResult`, `EvolutionSuggestion`, `LoopPhase`） |
| `py/maop/core/evolution/evolution_strategies.py` | `StrategyEngine` — 建议评估与配置变更执行 |
| `py/maop/core/evolution/evolution_agent.py` | `EvolutionAgentMixin` — Agent 专属进化 + 全量进化 |
| `py/maop/core/evolution/evolution_analyzers.py` | `EvolutionAnalyzersMixin` — 10 维度统一分析器 |
| `py/maop/core/evolution/evolution_collectors.py` | `EvolutionCollectorsMixin` — 数据采集 mixin |
| `py/maop/core/evolution/prompt_version.py` | Prompt 版本管理（`prompt_versions` 表） |
| `py/maop/core/evolution/skill_version.py` | Skill 版本管理 |
| `py/maop/core/evolution/regression.py` | 回归检测 |

### 1.3 类型定义

核心类型定义在 `evolution_loop_types.py` 中：

- `LoopPhase`（Enum）：七段闭环阶段枚举（`OBSERVE`/`HEAL`/`SUGGEST`/`EVALUATE`/`APPLY`/`VALIDATE`/`CONSOLIDATE`）
- `PhaseResult`：单阶段执行结果（phase/success/duration_s/details/error）
- `EvolutionSuggestion`：统一进化建议模型（兼容三套历史实现，含 `category`/`mutation_type`/`severity`/`auto_applicable`/`target_name`/`mutation_params` 等字段）
- `LoopReport`：完整周期报告（cycle_id/phases/errors_observed/heal_attempts/suggestions_generated/suggestions_applied/validation_improved/dry_run/snapshot_id/rolled_back 等）

## 2. 配置说明

### 2.1 构造函数参数配置

自演化系统通过构造函数参数配置（非环境变量）。各核心类的参数如下：

**EvolutionLoop**（错误驱动闭环）：

```python
EvolutionLoop(
    root_dir="/path/to/MAOP",
    strategy_name="balanced",      # conservative/aggressive/balanced/cost_aware
    auto_consolidate=True,         # 是否在每轮后运行 memory consolidation
    heal_threshold=2,              # 触发 heal 尝试的最小错误复发次数
    suggest_threshold=3,           # 未愈错误升级为建议的最小复发次数
)
```

**PerformanceEvolutionLoop**（性能驱动闭环）：

```python
PerformanceEvolutionLoop(
    root_dir="/path/to/MAOP",
    interval_s=3600.0,             # 自动循环周期（秒），run_forever 时使用
    human_gate=False,              # 人工 gate 模式：AB 显著后不自动 promote，仅标记 pending_approval
    enable_llm=True,               # ImprovementSuggester 是否启用 LLM 路径
    sprt_config=None,              # AB 实验的 SPRT 参数，None 用默认
)
```

**ImprovementSuggester**（建议生成器）：

```python
ImprovementSuggester(
    root_dir="/path/to/MAOP",
    model="",                      # 调用的模型名（空表示由 LLMProviderFactory 自动选择）
    enable_llm=True,               # 是否启用 LLM 路径，False 时始终走规则回退
)
```

### 2.2 dry-run 与 auto_rollback

`EvolutionLoop.run_cycle()` 支持两个关键参数：

- `dry_run=True`：运行所有阶段但 APPLY 仅记录预拟变更而不实际执行，用于预览影响。
- `auto_rollback=True`：当 VALIDATE 显示无改进时，自动回滚到 APPLY 前的 ChangeTracker 快照。

### 2.3 数据库表

自演化系统使用 SQLite 持久化，所有表通过 `get_db_path()` 定位到统一数据库文件：

| 表名 | 用途 | 所属模块 |
|------|------|----------|
| `evolution_cycles` | EvolutionLoop 周期记录（错误驱动） | `evolution_loop.py` |
| `evolution_perf_cycles` | PerformanceEvolutionLoop 周期记录（性能驱动） | `evolution_perf_loop.py` |
| `evolution_deployments` | AutoDeployer 部署记录（promote/rollback 审计） | `auto_deployer.py` |
| `ab_experiments` | AB 实验配置（variants/min_samples/confidence_level） | `ab_test.py` |
| `ab_assignments` | AB 流量分配记录（entity_id → variant） | `ab_test.py` |
| `ab_metrics` | AB 指标记录（per-variant success/failure） | `ab_test.py` |
| `prompt_versions` | Prompt 版本管理 | `prompt_version.py` |
| `error_ledger` | 错误热点账本（OBSERVE 阶段数据源） | `error_ledger.py` |
| `promoted_rules` | 已提升的自动修复规则 | `error_ledger.py` |

## 3. LLM 路径激活

### 3.1 启用方式

LLM 路径通过 `ImprovementSuggester(enable_llm=True)` 启用。`PerformanceEvolutionLoop` 默认 `enable_llm=True`，会传递给内部的 `ImprovementSuggester`。

### 3.2 工作流程

`ImprovementSuggester.suggest()` 方法的执行流程：

1. 若 `enable_llm=True`，先尝试 `_llm_suggest()` 调用 LLM 生成建议
2. LLM 返回 JSON 数组格式的候选建议，解析为 `EvolutionSuggestion` 列表
3. 如果 LLM 失败、返回无效 JSON 或空列表，自动回退到 `_rule_based()` 规则路径
4. 持久化建议（best-effort）

### 3.3 LLM Prompt 模板

Prompt 模板定义在 `suggester.py` 的 `_SUGGESTION_PROMPT` 常量中，要求 LLM 返回 1-3 个候选改进建议的 JSON 数组，每个候选包含：

- `mutation_type`：`adjust_timeout` / `change_routing` / `switch_model` / `adjust_prompt` / `adjust_retries` / `adjust_cache`
- `severity`：`HIGH` / `MEDIUM` / `LOW`
- `description`：一行人类可读的原因
- `target_name`：目标 agent 或路由键
- `mutation_params`：具体参数对象（如 `{"timeout_s": 120}`）
- `auto_applicable`：是否可自动应用无需人工审核

### 3.4 规则回退路径

`_rule_based()` 方法基于阈值的确定性建议生成，在离线/无 LLM 环境下使用：

- 成功率 < 80% → 建议增加重试（`adjust_retries`）
- 平均延迟 > 5000ms → 建议调整 timeout（`adjust_timeout`）
- 平均成本 > $0.02 → 建议切换更便宜的模型（`switch_model`）

### 3.5 LLM Provider 接线

`_get_factory()` 方法惰性获取 `LLMProviderFactory`（来自 `maop.core.agent.llm_chat.llm_provider`），通过 `chat_with_fallback()` 自动解析默认模型并走 fallback 链。若 factory 不可用（无 root_dir 或导入失败），返回 None 并走规则回退。

## 4. 演化周期说明

### 4.1 EvolutionLoop.run_cycle()（错误驱动七段闭环）

每个周期执行完整的七段闭环：

1. **OBSERVE**：从 `ErrorLedger.get_hotspots()` 读取未愈错误热点（count ≥ heal_threshold）
2. **HEAL**：对每个热点模式调用 `SelfHealEngine.run_all(trigger_condition=pattern)` 尝试自动修复
3. **SUGGEST**：将未愈错误转换为 `EvolutionSuggestion`（error_pattern_rule / change_routing / disable_agent）
4. **EVALUATE**：`StrategyEngine.evaluate()` 决定哪些建议应该应用（基于 strategy_name 策略）
5. **APPLY**：`StrategyEngine.apply()` 应用批准的变更（dry_run 模式仅记录预拟变更）
6. **VALIDATE**：对比 APPLY 前后的 ErrorLedger 热点数量，判断是否改进
7. **CONSOLIDATE**：`MemoryFacade.consolidate()` 将有效模式写入 Semantic Memory 供后续复用

**回滚机制**：若 `auto_rollback=True` 且 VALIDATE 检测到无改进且 APPLY 实际应用了变更，自动调用 `ChangeTracker.rollback()` 恢复到 APPLY 前的快照。

**周期报告**：`LoopReport` 包含所有阶段的 `PhaseResult` 列表、汇总指标（errors_observed/heal_attempts/suggestions_generated/suggestions_applied/validation_improved）以及 dry_run/snapshot_id/rolled_back 状态。

### 4.2 PerformanceEvolutionLoop.run_evolution_cycle()（性能驱动闭环）

性能驱动的闭环执行四步流程：

1. **评估**：`PerformanceEvaluator.evaluate()` 分别计算 baseline 和 candidate 的 `PerformanceMetrics`，`compare()` 计算 `MetricDelta`
2. **建议**：`ImprovementSuggester.suggest_sync()` 基于 candidate 指标 + delta 上下文生成改进建议
3. **AB/SPRT**：将 baseline/candidate 的 success 样本喂给 `ABTestFramework.record()`，调用 `evaluate_sprt()` 获取序贯统计决策
4. **部署决策**：
   - 若 SPRT 显著且 winner=treatment：
     - `human_gate=True`：标记 `pending_approval`，等待人工批准
     - `human_gate=False`：调用 `AutoDeployer.promote()` 自动提升
   - 若 SPRT 显著且 winner=control：调用 `AutoDeployer.rollback()` 自动回滚

**周期报告**：`EvolutionCycleReport` 包含 baseline/candidate 指标、delta、SPRT 决策、winner、promoted/rolled_back/pending_approval 状态。

### 4.3 PerformanceMetrics 指标

`PerformanceEvaluator` 从 trace 列表计算以下指标：

- `sample_count` / `success_count` / `failure_count` / `success_rate`
- `avg_latency_ms` / `p50_latency_ms` / `p95_latency_ms` / `p99_latency_ms` / `max_latency_ms`
- `total_cost_usd` / `avg_cost_usd`
- `total_tokens` / `avg_tokens`
- `by_agent` / `by_model`：按 agent/model 分组的成功率

## 5. API 参考

所有 API 端点定义在 `py/maop/dashboard/routers/evolution_experiment.py` 中，前缀为 `/api/evolution`。

### 5.1 评估与建议

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/api/evolution/evaluate` | 评估一组 trace 的性能指标（可选 baseline 对比） |
| POST | `/api/evolution/suggest` | 基于指标生成候选改进建议（LLM/规则驱动） |

### 5.2 AB 实验 + SPRT

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/api/evolution/ab/create` | 创建 AB 实验（需 admin） |
| POST | `/api/evolution/ab/record` | 记录一个样本并返回当前 SPRT 状态 |
| GET | `/api/evolution/ab/evaluate/{experiment}` | 评估指定实验的 SPRT 决策 |
| GET | `/api/evolution/ab/list` | 列出所有 AB 实验 |

### 5.3 部署

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/api/evolution/deploy/promote` | 提升优胜 variant（需 admin） |
| POST | `/api/evolution/deploy/rollback` | 回滚到快照（需 admin） |
| GET | `/api/evolution/deploy/history` | 获取部署历史（可选 experiment 过滤） |

### 5.4 性能演化循环

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/api/evolution/run` | 触发一轮性能演化循环（需 admin） |
| GET | `/api/evolution/cycles` | 获取演化循环历史（可选 experiment/limit 参数） |
| GET | `/api/evolution/pending` | 人工 gate：获取待批准的提升列表 |
| POST | `/api/evolution/approve` | 人工 gate：批准指定实验的提升（需 admin） |

### 5.5 演化叙事

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/evolution/narrative/{cycle_id}` | 获取指定演化周期的人类可读叙事（`?format=markdown\|json`） |

叙事章节包括：周期摘要、阶段详情、关键指标变化、建议列表、应用结果、验证结论、下一步建议。

### 5.6 Skill 编辑器（待落地）

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/evolution/skills` | 列出 Skill 原子（当前返回空，Skill 系统待落地） |
| POST | `/api/evolution/skills/composite` | 保存 composite Skill（返回 501，后端待实现） |

## 6. 前端可视化

前端演化页面位于 `dashboard-enterprise/src/views/EvolutionHistory.vue`，路由 `/evolution/history`，包含 3 个 tab：

### 6.1 历史 tab（history）

- 顶部统计卡片：总周期数、提升次数、回滚次数、待审批数
- 演化循环历史表格（DataTable）：cycle_id / 实验 / SPRT 决策 / winner / 提升状态 / 耗时
- 点击行展开：`EvolutionTimeline` 组件显示七段闭环各阶段时间线 + 建议详情展开

### 6.2 Prompt 对比 tab（compare）

- 选择两个演化周期
- diff 高亮对比 prompt/配置变化
- 使用 `useTextDiff.js` composable 计算 diff

### 6.3 叙事 tab（narrative）

- 选择演化周期
- 调用 `/api/evolution/narrative/{cycle_id}?format=markdown` 获取 Markdown 叙事
- 使用 `useMarkdown.js` composable 渲染为 HTML

### 6.4 相关组件

| 组件 | 路径 | 说明 |
|------|------|------|
| `EvolutionHistory.vue` | `src/views/EvolutionHistory.vue` | 演化历史主页面（3 tab） |
| `EvolutionTimeline.vue` | `src/components/EvolutionTimeline.vue` | 七段闭环阶段时间线组件 |
| `useMarkdown.js` | `src/composables/useMarkdown.js` | Markdown 渲染 composable |
| `useTextDiff.js` | `src/composables/useTextDiff.js` | 文本 diff 计算 composable |
| `view-evolution-history.js` | `src/i18n/view-evolution-history.js` | i18n 文案 |

## 7. 测试

### 7.1 后端测试

| 测试文件 | 测试数 | 说明 |
|----------|--------|------|
| `py/tests/test_evolution_f201.py` | 43 | 单元测试（EvolutionLoop / PerformanceEvolutionLoop / Evaluator / Suggester / ABTest / AutoDeployer） |
| `py/tests/test_evolution_e2e.py` | 23 | E2E/LLM 路径集成测试（含 Mock LLM） |
| `py/tests/test_evolution_narrative.py` | 30 | 叙事模块测试（Markdown/JSON 格式化） |

运行命令：

```bash
# 自演化相关测试
PYTHONPATH=py python -m pytest py/tests/test_evolution_f201.py py/tests/test_evolution_e2e.py py/tests/test_evolution_narrative.py --no-cov -q

# 全量测试
PYTHONPATH=py python -m pytest py/tests/ --no-cov -q
```

### 7.2 前端测试

| 测试文件 | 测试数 | 说明 |
|----------|--------|------|
| `src/__tests__/EvolutionHistory.test.js` | 5 | EvolutionHistory 组件测试（tab 切换/数据加载/统计卡片） |
| `src/__tests__/EvolutionTimeline.test.js` | 4 | EvolutionTimeline 组件测试（阶段渲染/时间线） |

运行命令：

```bash
cd dashboard-enterprise && npm test
```

### 7.3 代码质量检查

```bash
# ruff（lint）
python -m ruff check py/

# mypy（类型检查）
cd py && python -m mypy maop/ --ignore-missing-imports

# 前端构建
cd dashboard-enterprise && npm run build
```