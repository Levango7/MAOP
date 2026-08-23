# 架构增强设计审核报告

> 审核任务：Task 334
> 审核日期：2026-08-22
> 审核范围：三份架构增强设计方案的可行性 / 风险性 / 兼容性审核
> 审核依据：设计文档 + 现有代码集成点验证

---

## 1. 审核概览

表：三份设计综合审核结论对照表

| 设计文档 | 可行性 | 风险性 | 兼容性 | 综合结论 |
|----------|--------|--------|--------|----------|
| design-supervisor-agent.md | ⚠️ 有条件通过 | ⚠️ 有条件通过 | ⚠️ 有条件通过 | ⚠️ 有条件通过 |
| design-debate-agent.md | ⚠️ 有条件通过 | ⚠️ 有条件通过 | ⚠️ 有条件通过 | ⚠️ 有条件通过 |
| design-blackboard.md | ⚠️ 有条件通过 | ✅ 通过 | ⚠️ 有条件通过 | ⚠️ 有条件通过 |

**审核结论摘要**：三份设计在架构层面均无根本性缺陷，核心类设计与集成方案合理。但每份设计均存在若干需在实施前明确或修正的细节问题（共 12 项），涉及 EventBus API 不一致、集成点描述与实际代码偏差、跨文档枚举定义不统一等。建议退回设计阶段修正以下问题后进入执行阶段。

**已验证的现有代码集成点**：

表：现有代码集成点验证结果

| 代码文件 | 设计中引用位置 | 实际验证结果 |
|----------|----------------|--------------|
| `py/maop/core/scheduling/failure_detector.py`（532 行） | 监督者继承基础 | ✅ 存在，`FailurePatternDetector` 类 + `get_failure_detector()` 单例，继承方案可行 |
| `py/maop/delegate/dispatch_core.py`（841 行） | 辩论调度集成点 | ✅ 存在，`Dispatcher` 类 + `dispatch()` + `_semaphore` 并发限制，新增 `dispatch_debate()` 可行 |
| `py/maop/core/reliability/event_bus.py`（417 行） | 黑板/预警发布基础 | ⚠️ 存在，但 API 为 `publish(Event)` / `publish_sync(Event)`，**无 `emit()` 方法**（见 2.3.3 节详述） |
| `py/maop/engine.py`（631 行） | 执行引擎集成点 | ✅ 存在，`_execute_step()` 位于第 375 行，签名与设计文档一致 |
| `py/maop/loop_executor.py`（270 行） | 循环执行器集成点 | ✅ 存在，`_execute_with_retry()` 使用 `lc.iterative_max_attempts`，集成方案可行 |
| `py/maop/core/evolution/evolution_loop.py`（343 行） | 演化循环集成点 | ⚠️ 存在，`run_cycle()` 第 130 行，但 DEBATE 插入需修改方法体（见 3.3.2 节详述） |
| `py/maop/dashboard/_register_routes.py` | 路由注册集成点 | ✅ 存在，黑板/辩论/监督者路由注册可行 |

---

## 2. 监督者型设计审核

### 2.1 可行性审核

#### 2.1.1 技术可行性

**✅ 通过项**：

- **继承方案合理**：`Supervisor(FailurePatternDetector)` 继承保留全部被动检测 API（`record_result()` / `get_weight()` / `get_stats()` / drain / recovery），外部既有调用方零改动。`FailurePatternDetector` 的 `__init__` 参数（`window_size` / `failure_rate_threshold` / `timeout_threshold` / `recovery_consecutive_successes` / `event_bus` / `tenant_id`）通过 `**kwargs` 透传，兼容性良好（design-supervisor-agent.md:348-357）。
- **六大能力设计完整**：巡检（patrol）/ 预警（warn）/ 替换（replace）/ 降级（degrade）/ 终止（terminate）/ 升级（upgrade）均有明确的方法签名、流程描述与数据模型（design-supervisor-agent.md:361-471）。
- **数据模型清晰**：`HealthProbe` / `SupervisorRule` / `DispatchDecision` / `ActionRecord` 四个 Pydantic 模型覆盖巡检结果、规则定义、派发决策、动作审计，字段约束合理。
- **规则引擎设计合理**：`SupervisorRule.condition` 支持声明式阈值条件（`failure_rate_gt` / `avg_latency_gt` / `breaker_open` / `reachable` / `all` AND 嵌套 / `resource_usage_gt`），内置 8 条默认规则覆盖常见劣化场景（design-supervisor-agent.md:675-686）。

**⚠️ 需补充项**：

- **[F-1] HealthChecker 探针实现缺失依据**：`HealthChecker.check()` 需对 agent 执行 ping / metrics / resource 三类探针（design-supervisor-agent.md:584-590），但当前代码中 agent 未暴露健康探针端点。`Dispatcher.dispatch()` 是通过 LLM driver 执行任务，无独立的健康检查接口。设计中未说明探针如何实现——是复用 dispatch 发送轻量 ping 任务？还是要求 agent 新增 HTTP 健康端点？**建议**：设计文档 2.2.3 节补充探针实现方案（如"ping 探针 = dispatch 一个 `task='__health_ping__'` 轻量任务并测延迟"或"要求 agent driver 暴露 `health_check()` 方法"）。
- **[F-2] replace 的"路由注册表"未定位**：`replace()` 流程说"更新路由注册表：routing_key → replacement"（design-supervisor-agent.md:421），但未指明路由注册表的具体代码位置。现有路由在 `py/maop/core/routing/routing_decision.py`（dispatch_core.py:33 导入），设计中未说明如何修改路由映射。**建议**：明确 replace 调用 `routing_decision` 模块的哪个 API 来更新 routing_key → agent 映射。
- **[F-3] upgrade 灰度切量缺乏路由层支持**：`upgrade()` 需"按 rollout_steps 逐步切流量（10% → 50% → 100%）"（design-supervisor-agent.md:460-469），但现有 `RoutingDecisionRecord` 与 `AgentResolver` 无版本切量能力——路由是 routing_key → agent 的单值映射，不支持"10% 到 v1.3 + 90% 到 v1.2"的加权分流。**建议**：设计文档补充路由层加权分流的实现方案，或说明 upgrade 首版仅支持"全切"（rollout=1.0），灰度切量延后。

#### 2.1.2 架构兼容性

**✅ 通过项**：

- 集成点准确：`engine.py:375` 的 `_execute_step()` 签名与设计文档一致，插入 `check_before_dispatch` / `check_after_dispatch` 位置合理。
- `loop_executor.py:198` 的 `_execute_with_retry()` 使用 `lc.iterative_max_attempts`，向监督者查询动态策略的集成方案可行。

#### 2.1.3 实现复杂度

**⚠️ 需评估**：

- **[F-4] 巡检策略三选一增加实现量**：full / sample / adaptive 三种巡检策略（design-supervisor-agent.md:866-880）均需实现。adaptive 策略需读取 `MetricsCollector` 全局吞吐量判定负载，实现复杂度较高。**建议**：首版仅实现 full 策略，sample / adaptive 延后。

### 2.2 风险性审核

#### 2.2.1 引入风险

**⚠️ 需缓解项**：

- **[R-1] 巡检循环与主事件循环的共存风险**：`start_patrol_loop()` 创建后台 `asyncio.Task`（design-supervisor-agent.md:376-382），需确保 MAOP 主事件循环已启动。若在同步上下文中调用（如测试）会抛 `RuntimeError: no running event loop`。设计中未说明巡检循环的启动时机与生命周期管理。**建议**：明确巡检循环在 `Engine.run()` 首次调用时懒启动，或在应用 startup 事件中启动。
- **[R-2] terminate 后 routing_key 无可用 agent 的边界**：`terminate()` 标记 `disabled=True` 后调度跳过该 agent（design-supervisor-agent.md:445-452），若该 agent 是某 routing_key 的唯一可用 agent，该 routing_key 将无可用 agent。设计中未处理此边界。**建议**：terminate 前检查该 agent 是否为某 routing_key 唯一可用，若是则要求先配置 fallback 或拒绝 terminate。
- **[R-3] upgrade 自动回退的劣化判定标准未定义**：设计中说"若新版本劣化则自动回退"（design-supervisor-agent.md:468），但未明确"劣化"的量化标准——是失败率超阈？延迟超阈？置信度？**建议**：补充回退触发条件（如"新版本窗口失败率 > 0.15 或 avg_latency > 旧版本 1.5 倍"）。

#### 2.2.2 缓解措施

**✅ 已有缓解**：

- cooldown 抑制规则风暴（design-supervisor-agent.md:291）。
- 巡检循环异常不退出，捕获并记录下轮继续（design-supervisor-agent.md:862）。
- `_maybe_trigger_evolution` 触发频率受 cooldown 限制（10 分钟一次），避免巡检与演化循环正反馈风暴（design-supervisor-agent.md:838-839）。

### 2.3 兼容性审核

#### 2.3.1 向后兼容

**✅ 通过项**：

- `check_before_dispatch()` 是同步方法仅读内存状态，`supervisor is None` 时行为完全退化为现状（design-supervisor-agent.md:744-745）。
- 新增事件 topic 使用 `supervisor.*` / `agent_replaced` 等前缀，与父类 `agent_drained` / `agent_recovering` / `agent_recovered` 不冲突（design-supervisor-agent.md:816）。

#### 2.3.2 EventBus API 不一致（关键问题）

**⚠️ [C-1] EventBus 存在两个实现且 API 不统一**：

- `failure_detector.py:49` 的 TYPE_CHECKING 导入：`from maop.enterprise.notification.event_bus import EventBus`
- `failure_detector.py:480` 的 `_publish_event` 调用：`self._event_bus.emit(event_type, full_payload, tenant_id=self._tenant_id)`
- 但 `py/maop/core/reliability/event_bus.py` 的 `EventBus` 类**只有 `publish(event: Event)` 和 `publish_sync(event: Event)` 方法，没有 `emit()` 方法**（event_bus.py:196, 340）
- `engine.py:30` 导入的是 `from maop.core.reliability.event_bus import EventBus, get_event_bus`

这意味着存在两个 EventBus 实现：
1. `maop.enterprise.notification.event_bus.EventBus` —— 有 `emit(topic, payload)` 方法（failure_detector 使用）
2. `maop.core.reliability.event_bus.EventBus` —— 有 `publish(Event)` 方法（engine / 黑板架构使用）

**影响**：监督者继承 `FailurePatternDetector`，其 `_publish_event` 调用 `emit()`。若监督者注入的是 `core.reliability.event_bus.EventBus` 实例，则 `emit()` 调用会抛 `AttributeError`。设计文档 2.3.3 节说"复用父类 `_publish_event()` 机制"，但未明确注入哪个 EventBus 实现。

**建议**：设计文档明确监督者使用的 EventBus 实例来源（`get_event_bus()` 返回 core 版还是 enterprise 版），或统一两个 EventBus 实现的 API。

### 2.4 审核结论

**⚠️ 有条件通过**。需修正以下 7 项后进入执行阶段：

| 编号 | 问题 | 严重度 | 修改建议 |
|------|------|--------|----------|
| F-1 | HealthChecker 探针实现缺失依据 | 中 | 补充探针实现方案 |
| F-2 | replace 的路由注册表未定位 | 中 | 明确 routing_decision API 调用 |
| F-3 | upgrade 灰度切量缺乏路由层支持 | 中 | 补充加权分流方案或首版仅全切 |
| F-4 | 三种巡检策略实现复杂度高 | 低 | 首版仅 full，sample/adaptive 延后 |
| R-1 | 巡检循环启动时机未明确 | 中 | 明确懒启动或 startup 启动 |
| R-2 | terminate 后无可用 agent 边界 | 中 | terminate 前检查唯一可用性 |
| R-3 | upgrade 回退判定标准未定义 | 中 | 补充量化回退条件 |
| C-1 | EventBus API 不一致 | 高 | 统一 EventBus 实现或明确注入来源 |

---

## 3. 对抗辩论型设计审核

### 3.1 可行性审核

#### 3.1.1 技术可行性

**✅ 通过项**：

- **DebateOrchestrator 设计合理**：角色分配 → 轮次调度 → 收敛检查 → 监督者兜底的流程清晰（design-debate-agent.md:286-327）。`run_debate()` 方法签名完整，`_check_consensus()` 加权共识度公式明确（`Σ(conf_i × stance_weight_i) / Σ(conf_i)`，stance_weight: SUPPORT=+1, AMEND=+0.5, OPPOSE=-1）。
- **复用 Dispatcher.dispatch() 保证安全防线**：辩论中各 agent 发言通过 `self.dispatch(agent, task=prompt)` 获取，熔断器、预算检查、guardrail、SLA 计量全部生效，不绕过安全防线（design-debate-agent.md:437-439）。
- **置信度计算含历史校准**：`ConfidenceCalculator` 综合历史准确率（w1=0.35）、证据强度（w2=0.25）、推理链完整度（w3=0.20）、自评置信度（w4=0.20），并对"嘴硬但常错"的 agent 施加惩罚因子（design-debate-agent.md:546-577）。
- **数据模型完整**：`AgentOpinion` / `DebateRound` / `Verdict` / `ConsensusOutput` 四个模型覆盖辩论全生命周期，`Verdict` 含完整 `rounds` 轨迹支持回放。

**⚠️ 需补充项**：

- **[F-5] SupervisorAction 枚举与监督者型文档不一致**：辩论型 2.2.5 节定义 `SupervisorAction` 仅 5 个成员（REPLACE / DEGRADE / TERMINATE / UPGRADE / NONE）（design-debate-agent.md:348-353），而监督者型 2.2.1 节定义 7 个成员（多 PATROL / ALERT）（design-supervisor-agent.md:234-242）。两份文档应统一为同一枚举定义。**建议**：以监督者型 7 成员版本为准，辩论型文档引用而非重复定义。

#### 3.1.2 架构兼容性

**✅ 通过项**：

- `dispatch_debate()` 是 `Dispatcher` 类上的纯新增异步方法，既有 `dispatch()` / `delegate_to_subagent()` 不变（design-debate-agent.md:701）。
- `LoopPhase` 枚举位于 `py/maop/core/evolution/evolution_loop_types.py:16`（已验证），新增 `DEBATE = "debate"` 成员不影响既有值。

### 3.2 风险性审核

#### 3.2.1 引入风险

**⚠️ 需缓解项**：

- **[R-4] LLM 调用放大 N×M 倍**：每场辩论 N 个 agent × M 轮（默认 3），LLM 调用量是单 agent 的 N×M 倍。虽有 `self._semaphore` 全局并发限制（默认 10）和 `max_rounds=3` 上限，但高频辩论下成本与延迟仍显著上升。设计中仅"HIGH severity 或显式请求才辩论"作为缓解（design-debate-agent.md:733），但未给出成本预算阈值。**建议**：补充单场辩论的成本上限（如"辩论总 token 数超预算 50% 则提前终止转监督者裁决"）。
- **[R-5] 辩论轨迹持久化膨胀**：`Verdict` 含完整 `rounds` 轨迹，每轮含 N 个 `AgentOpinion`（含 `reasoning_chain` / `evidence` 列表）。高频辩论下 `debate` 表膨胀。设计中提到"设保留期清理"（design-debate-agent.md:737）但未给出具体策略。**建议**：补充保留期（如 30 天）与清理机制（定时任务或演化循环 CONSOLIDATE 阶段触发）。
- **[R-6] round_timeout_s 超时判定依赖整轮完成**：单轮超时 120s，超时方视为弃权（design-debate-agent.md:536）。但若 1 个 agent 慢、其余 agent 快，整轮仍需等待慢 agent 超时。**建议**：改为 per-agent 超时，单个 agent 超时即视为弃权，不等整轮。

#### 3.2.2 缓解措施

**✅ 已有缓解**：

- 连续两轮共识度下降（发散）提前终止转监督者裁决（design-debate-agent.md:539）。
- 参与方不足 3 个抛 `InsufficientParticipantsError`，调用方降级为单 agent dispatch（design-debate-agent.md:521-523）。
- `early_exit_on_unanimous` 全员一致且平均置信度 ≥ 0.85 时允许 Round 1 退出（design-debate-agent.md:537）。

### 3.3 兼容性审核

#### 3.3.1 向后兼容

**✅ 通过项**：

- `dispatch_debate()` 纯新增方法，既有 `dispatch()` 不变。
- `Supervisor` 继承 `FailurePatternDetector`，`get_failure_detector()` 保留，既有调用零改动（design-debate-agent.md:704）。

#### 3.3.2 DEBATE 阶段插入需修改 run_cycle 方法体（关键问题）

**⚠️ [C-2] 设计描述与实际代码偏差**：

设计文档 2.3.2 节说"DEBATE 阶段是纯增量插入，不修改既有阶段签名"（design-debate-agent.md:482），但实际 `evolution_loop.py:130-214` 的 `run_cycle()` 方法体是顺序调用：

```python
observe = self._phase_observe()          # line 145
heal = self._phase_heal(...)             # line 156
suggest = self._phase_suggest(...)       # line 161
evaluate = self._phase_evaluate(suggest.details.get("suggestions", []))  # line 165
apply_result = self._phase_apply(...)    # line 179
validate = self._phase_validate(...)     # line 183
# consolidate                            # line 206
```

插入 DEBATE 需在 line 161（suggest）和 line 165（evaluate）之间**修改 run_cycle 方法体**，插入：

```python
debate = self._phase_debate(suggest.details.get("suggestions", []))
debated_suggestions = debate.details.get("accepted_suggestions", [])
evaluate = self._phase_evaluate(debated_suggestions)  # 改参数
```

这**修改了既有方法的方法体**（虽然不修改阶段方法签名），与"纯增量插入"描述有偏差。`_phase_evaluate()` 的入参从 `suggestions` 变为 `debated_suggestions`。

**影响**：修改 `run_cycle()` 方法体可能影响现有测试（若有测试断言 `run_cycle` 的阶段顺序或 evaluate 入参）。

**建议**：设计文档修正描述为"DEBATE 阶段插入需修改 `run_cycle()` 方法体在 SUGGEST 与 EVALUATE 之间插入 `_phase_debate()` 调用，并将 `_phase_evaluate()` 入参改为辩论后的建议列表。`_phase_debate()` 未配置时透传 suggestions，行为退化为现状"。

### 3.4 审核结论

**⚠️ 有条件通过**。需修正以下 4 项后进入执行阶段：

| 编号 | 问题 | 严重度 | 修改建议 |
|------|------|--------|----------|
| F-5 | SupervisorAction 枚举跨文档不一致 | 中 | 统一为 7 成员版本 |
| R-4 | 辩论成本放大无预算阈值 | 中 | 补充成本上限触发终止 |
| R-5 | 辩论轨迹清理策略缺失 | 低 | 补充保留期与清理机制 |
| R-6 | round_timeout 整轮等待 | 低 | 改为 per-agent 超时 |
| C-2 | DEBATE 插入描述偏差 | 中 | 修正为"需修改 run_cycle 方法体" |

---

## 4. 黑板架构设计审核

### 4.1 可行性审核

#### 4.1.1 技术可行性

**✅ 通过项**：

- **核心四组件设计完整**：`BlackboardEntry`（结构化知识条目）/ `Blackboard`（共享黑板 ABC）/ `KnowledgeSource`（知识源 ABC）/ `TriggerRule`（触发规则 ABC）/ `BlackboardController`（控制器 ABC），职责清晰（design-blackboard.md:159-444）。
- **SQLiteBlackboard 表结构合理**：`blackboard_entries` 表含 `UNIQUE(domain, key, version)` 联合唯一约束保证乐观锁，`idx_bb_domain_state` / `idx_bb_schema_state` 复合索引覆盖查询模式（design-blackboard.md:505-531）。
- **并发控制双重保障**：乐观锁（`version` 字段 + `expected_version` 校验）+ 独占锁（`blackboard_locks` 表 + TTL），防止丢失更新与死锁（design-blackboard.md:292-305）。
- **内置规则工厂**：`on_state` / `on_state_count` / `on_schema_and_state` / `custom` 四种工厂覆盖常见触发模式（design-blackboard.md:392-399）。
- **控制器调度策略完善**：优先级调度（rule.priority + ks.priority 二次排序）、并发分组（read_domains 无交集可并发）、可重入控制、死锁防护（execution_timeout_s）、迭代上限（max_iterations）（design-blackboard.md:446-452）。

**⚠️ 需补充项**：

- **[F-6] EventBus API 误用**：设计文档 2.3.2 节写 `EventBus.publish("blackboard.changed", ...)`（design-blackboard.md:485），但实际 `core.reliability.event_bus.EventBus` 的 API 是 `publish(event: Event)`，接收 `Event` 对象而非 `(topic, data)`。正确调用应为 `await bus.publish(Event(topic="blackboard.changed", data={...}))`。**建议**：修正所有伪代码中的 EventBus 调用为 `publish(Event(...))` 形式。

#### 4.1.2 架构兼容性

**✅ 通过项**：

- 分层复用关系清晰：黑板语义层复用 EventBus（事件分发）+ 三层记忆（持久化后端），不重写基础设施（design-blackboard.md:456-479）。
- `py/maop/dashboard/_register_routes.py` 已验证存在，路由注册集成可行。

### 4.2 风险性审核

#### 4.2.1 引入风险

**✅ 已有充分缓解**：

- 知识源并发写丢失更新 → 乐观锁 + expected_version 校验（design-blackboard.md:803）。
- 触发规则链式扩散无限循环 → max_iterations + 收敛谓词 + 迭代计数告警（design-blackboard.md:804）。
- 知识源执行超时死锁 → execution_timeout_s 强制释放锁 + 标记 FAILED（design-blackboard.md:805）。
- 大量条目查询性能退化 → 复合索引 + 分页（design-blackboard.md:806）。
- 动态注册恶意知识源 → require_admin 鉴权 + 类名白名单校验（design-blackboard.md:807）。

**⚠️ 需补充项**：

- **[R-7] 知识源 read_domains 声明正确性依赖**：并发分组依赖知识源正确声明 `read_domains`（design-blackboard.md:449）。若知识源未声明或声明不全，可能导致本应串行的知识源被并发执行，引发读写冲突。**建议**：控制器在注册知识源时校验 read_domains 非空，并在文档中强调声明完整性要求。
- **[R-8] 白名单机制未详述**：动态注册知识源的安全对策提到"知识源类名白名单校验"（design-blackboard.md:807），但未说明白名单的配置方式与加载时机。**建议**：补充白名单配置项（如 `settings.blackboard_ks_whitelist = ["EntityExtractor", "RelationExtractor", ...]`）与未在白名单内的类注册时的拒绝行为。

### 4.3 兼容性审核

#### 4.3.1 向后兼容

**✅ 通过项**：

- 新增 `blackboard_entries` / `blackboard_locks` 表不修改现有 `episodic_memory` / `memory_entries` 表结构，无破坏性 schema 迁移风险（design-blackboard.md:758）。
- `__init__.py` 仅新增导出，向后兼容（design-blackboard.md:745）。
- 路由注册参照 `knowledge.py` 模式，`APIRouter(prefix="/api/blackboard")` + `handle_api_errors` + `require_admin`，与现有风格一致（design-blackboard.md:627）。

#### 4.3.2 EventBus 复用与跨架构互通问题

**⚠️ [C-3] 黑板与监督者使用不同 EventBus 实现**：

- 黑板架构复用 `core.reliability.event_bus.EventBus`（`publish(Event)` API）（design-blackboard.md:483-488）
- 监督者继承 `failure_detector`，其 `_publish_event` 调用 `emit()` API（enterprise.notification.event_bus.EventBus）
- 若两个 EventBus 实例独立，则黑板发布的 `blackboard.changed` 事件与监督者发布的 `supervisor.alert` 事件**无法互通**——监督者无法订阅黑板变迁事件来监控知识源执行，黑板也无法订阅监督者预警来触发知识源。

**影响**：设计文档 1.1.2 节提到"监督者可监控黑板控制器本身"（design-supervisor-agent.md:48），但若 EventBus 不互通，此跨架构协作无法实现。

**建议**：统一所有组件使用同一个 EventBus 实例（`get_event_bus()` 返回的 core 版单例），并修正 `failure_detector._publish_event` 改用 `publish(Event)` API。

### 4.4 审核结论

**⚠️ 有条件通过**。需修正以下 3 项后进入执行阶段：

| 编号 | 问题 | 严重度 | 修改建议 |
|------|------|--------|----------|
| F-6 | EventBus.publish 伪代码误用 | 中 | 修正为 publish(Event(...)) 形式 |
| R-7 | read_domains 声明正确性依赖 | 低 | 注册时校验非空 + 文档强调 |
| R-8 | 白名单机制未详述 | 低 | 补充白名单配置项 |
| C-3 | 黑板与监督者 EventBus 不互通 | 高 | 统一 EventBus 实例 |

---

## 5. 综合结论与建议

### 5.1 综合结论

三份设计文档在架构层面均**无根本性设计缺陷**，核心类设计合理、集成方案思路正确、向后兼容策略到位。但每份设计均存在需在实施前修正的细节问题，共 **12 项**（高严重度 2 项、中严重度 7 项、低严重度 3 项）。

表：全部待修正问题汇总

| 编号 | 所属设计 | 问题 | 严重度 |
|------|----------|------|--------|
| C-1 | 监督者型 | EventBus API 不一致（emit vs publish） | 高 |
| C-3 | 黑板架构 | 黑板与监督者 EventBus 不互通 | 高 |
| F-1 | 监督者型 | HealthChecker 探针实现缺失依据 | 中 |
| F-2 | 监督者型 | replace 的路由注册表未定位 | 中 |
| F-3 | 监督者型 | upgrade 灰度切量缺乏路由层支持 | 中 |
| F-5 | 辩论型 | SupervisorAction 枚举跨文档不一致 | 中 |
| F-6 | 黑板架构 | EventBus.publish 伪代码误用 | 中 |
| R-1 | 监督者型 | 巡检循环启动时机未明确 | 中 |
| R-2 | 监督者型 | terminate 后无可用 agent 边界 | 中 |
| R-3 | 监督者型 | upgrade 回退判定标准未定义 | 中 |
| C-2 | 辩论型 | DEBATE 插入描述偏差 | 中 |
| F-4 | 监督者型 | 三种巡检策略实现复杂度高 | 低 |
| R-4 | 辩论型 | 辩论成本放大无预算阈值 | 中 |
| R-5 | 辩论型 | 辩论轨迹清理策略缺失 | 低 |
| R-6 | 辩论型 | round_timeout 整轮等待 | 低 |
| R-7 | 黑板架构 | read_domains 声明正确性依赖 | 低 |
| R-8 | 黑板架构 | 白名单机制未详述 | 低 |

### 5.2 优先修正建议

#### 5.2.1 最高优先级（高严重度，阻塞实施）

**[C-1 + C-3] 统一 EventBus 实现与 API**：

这是跨三份设计的共性问题。当前存在两个 EventBus 实现：
- `maop.enterprise.notification.event_bus.EventBus`（`emit(topic, payload)` API）
- `maop.core.reliability.event_bus.EventBus`（`publish(Event)` API）

**修正方案**：
1. 统一使用 `maop.core.reliability.event_bus.EventBus`（`get_event_bus()` 单例）作为全局唯一 EventBus。
2. 修正 `failure_detector.py:458-493` 的 `_publish_event` 方法，将 `self._event_bus.emit(event_type, full_payload, tenant_id=...)` 改为 `await self._event_bus.publish(Event(topic=event_type, data=full_payload))`（或同步包装）。
3. 修正 `failure_detector.py:49` 的 TYPE_CHECKING 导入为 `from maop.core.reliability.event_bus import EventBus`。
4. 三份设计文档统一引用 `core.reliability.event_bus.EventBus` 的 `publish(Event)` API。

#### 5.2.2 次高优先级（中严重度，实施前需明确）

1. **[F-5]** 统一 `SupervisorAction` 枚举为监督者型 7 成员版本，辩论型文档引用而非重复定义。
2. **[C-2]** 修正辩论型文档 2.3.2 节描述，明确"DEBATE 阶段插入需修改 `run_cycle()` 方法体"。
3. **[F-1]** 补充 HealthChecker 探针实现方案。
4. **[F-2]** 明确 replace 调用 routing_decision 模块的 API。
5. **[F-3]** 补充 upgrade 灰度切量的路由层加权分流方案或声明首版仅全切。
6. **[F-6]** 修正黑板文档所有 EventBus 伪代码为 `publish(Event(...))` 形式。
7. **[R-1]** 明确巡检循环启动时机（Engine.run 首次调用懒启动或 app startup）。
8. **[R-2]** 补充 terminate 前唯一可用 agent 检查。
9. **[R-3]** 补充 upgrade 回退的量化判定标准。
10. **[R-4]** 补充辩论成本上限触发终止机制。

#### 5.2.3 低优先级（低严重度，实施中可补充）

1. **[F-4]** 首版仅实现 full 巡检策略，sample / adaptive 延后。
2. **[R-5]** 补充辩论轨迹保留期与清理机制。
3. **[R-6]** round_timeout 改为 per-agent 超时。
4. **[R-7]** 知识源注册时校验 read_domains 非空。
5. **[R-8]** 补充知识源白名单配置项。

### 5.3 审核决定

**三份设计均为 ⚠️ 有条件通过**。存在 2 项高严重度问题（EventBus 统一）阻塞实施，需退回设计阶段修正后重新审核。建议设计阶段优先解决 5.2.1 节的 EventBus 统一问题，再依次处理 5.2.2 / 5.2.3 节问题。

修正完成后，三份设计的架构基础即可进入执行阶段。核心类设计、集成方案思路、向后兼容策略均已到位，待修正项均为细节明确而非架构重构。

---

> 审核人：CodeArts 审核代理（Task 334）
> 审核状态：⚠️ 有条件通过，退回设计阶段修正 12 项问题
> 下一步：设计阶段修正高严重度问题（C-1 + C-3 EventBus 统一）后重新提交审核