# 架构增强设计二次审核报告

> 审核任务：Task 346
> 审核日期：2026-08-22
> 审核范围：对修正后的三份架构增强设计文档进行二次审核（可行性 / 风险性 / 兼容性）
> 审核依据：首次审核报告（`design-review-report.md`，Task 334）记录的 12 项问题 + 修正后设计文档 + 关键标记 grep 验证
> 前置审核：首次审核（Task 334）结论为 ⚠️ 有条件通过，退回设计阶段修正 12 项问题

---

## 1. 审核概览

表：三份设计二次审核综合结论对照表

| 设计文档 | 首次结论 | 二次结论 | 修正项数 | 验证结果 |
|----------|----------|----------|----------|----------|
| design-supervisor-agent.md | ⚠️ 有条件通过 | ✅ 通过 | 8 | 8 项全部已修正，未引入高/中严重度新问题 |
| design-debate-agent.md | ⚠️ 有条件通过 | ✅ 通过 | 7 | 7 项全部已修正，未引入高/中严重度新问题 |
| design-blackboard.md | ⚠️ 有条件通过 | ✅ 通过 | 4 | 4 项全部已修正，未引入高/中严重度新问题 |

**二次审核结论摘要**：三份设计文档针对首次审核的 12 项问题（2 高 + 7 中 + 3 低）已全部修正，修正质量高、方案一致。关键的高严重度问题（C-1 / C-3 EventBus 统一）已跨三份文档协同修正，统一采用 `core.reliability.event_bus.EventBus` 的 `publish(Event)` API 与 `get_event_bus()` 全局单例。二次审核未发现高/中严重度的新引入问题，仅发现 2 项极低严重度的文档表述/设计细节差异，不阻塞实施。**建议三份设计进入执行阶段**。

**验证方法**：对每项修正用 grep 搜索关键标记 + 逐段阅读修正内容 + 跨文档一致性比对。

---

## 2. 首次审核问题修正验证

### 2.1 高严重度问题（2 项）

#### C-1: EventBus API 不一致（emit vs publish）

- **所属文档**：design-supervisor-agent.md
- **首次问题**：`failure_detector.py` 的 `_publish_event` 调用 `emit()`，但 `core.reliability.event_bus.EventBus` 无 `emit()` 方法，存在两个 EventBus 实现 API 不统一。
- **修正状态**：✅ 已修正
- **验证详情**：
  - 第 908-942 行新增"EventBus API 统一（修正 [C-1]）"小节，明确统一方案：
    1. 统一 API：监督者及其父类一律使用 `core.reliability.event_bus.EventBus.publish(Event)`，不再使用 `emit()`。
    2. `_publish_event` 方法修正：给出修正后实现代码（第 920-939 行），将 `emit()` 改为 `publish(Event)`，含同步/异步上下文自适应。
    3. TYPE_CHECKING 导入修正：从 `enterprise.notification.event_bus` 更新为 `core.reliability.event_bus`。
    4. `get_event_bus()` 来源统一为 `core.reliability.event_bus.get_event_bus()`。
  - 第 1137 行修改文件清单中明确标注 `[C-1] 修正 _publish_event 方法：emit() → publish(Event)`。
  - grep 验证：`publish(Event)` 出现 5 处，`emit()` 出现 6 处但均为说明性引用（描述当前问题/修正方案/修改清单），无实际使用残留。

#### C-3: 黑板与监督者 EventBus 不互通

- **所属文档**：design-blackboard.md
- **首次问题**：黑板用 `core.reliability.event_bus.EventBus`（`publish(Event)`），监督者继承 `failure_detector` 用 `emit()`，两个实例独立则事件无法互通。
- **修正状态**：✅ 已修正
- **验证详情**：
  - 第 508-524 行新增"2.3.2.1 统一 EventBus 实例与 API（对应审核项 C-3）"小节，明确统一方案：
    1. 全局唯一实例：统一使用 `core.reliability.event_bus.EventBus`，通过 `get_event_bus()` 获取单例，黑板控制器、监督者、执行引擎均注入同一实例。
    2. 统一 API：所有事件发布统一使用 `publish(Event)` 或 `publish_sync(Event)`。
    3. 监督者侧修正（由监督者型设计文档负责）：明确 `failure_detector.py` 的 `_publish_event` 修正方案，与 C-1 一致。
    4. 跨架构协作：统一后黑板控制器与监督者共享同一 EventBus 实例，可互相订阅对方事件（监督者订阅 `blackboard.changed`，黑板订阅 `supervisor.alert` / `agent_replaced`）。
  - 与 C-1 修正方案完全一致，跨文档协同修正到位。

### 2.2 中严重度问题（7 项）

#### F-1: HealthChecker 探针实现缺失依据

- **所属文档**：design-supervisor-agent.md
- **首次问题**：`HealthChecker.check()` 需对 agent 执行 ping/metrics/resource 三类探针，但设计中未说明探针如何实现。
- **修正状态**：✅ 已修正
- **验证详情**：
  - 第 677-704 行新增"探针实现方案（补充 [F-1]）"小节，给出三类探针的实现方案对照表：
    - ping：dispatch 轻量任务 `task='__health_ping__'` 测往返延迟，复用 `Dispatcher.dispatch()` 路径（熔断/预算/guardrail 全部生效）。
    - metrics：读取父类 `get_stats(agent_id)` 获取窗口统计，无 I/O 开销。
    - resource：通过 `MetricsCollector` 读取 agent 暴露的 Prometheus 指标。
  - 补充 ping/metrics/resource 探针约束说明（保留任务名、超时约束、未暴露指标的处理）。
  - grep 验证：`__health_ping__` / `探针实现方案` 出现 4 处。

#### F-2: replace 的路由注册表未定位

- **所属文档**：design-supervisor-agent.md
- **首次问题**：`replace()` 流程说"更新路由注册表"，但未指明路由注册表的具体代码位置。
- **修正状态**：✅ 已修正
- **验证详情**：
  - 第 447-454 行新增"路由注册表定位（补充 [F-2]）"说明：
    - 路由映射存储在 `py/maop/core/routing/routing_decision.py` 中（`dispatch_core.py:33` 导入 `RoutingDecisionRecord` 与 `AgentResolver`）。
    - replace 通过该模块暴露的 API 更新 `routing_key → agent` 映射，不直接修改 `dispatch_core.py` 内部状态。
    - 若现有模块无更新接口，需新增 `update_routing_mapping(routing_key, new_agent)` 方法。
  - grep 验证：`routing_decision.py` 出现 4 处。

#### F-3: upgrade 灰度切量缺乏路由层支持

- **所属文档**：design-supervisor-agent.md
- **首次问题**：`upgrade()` 需按 rollout_steps 逐步切流量（10% → 50% → 100%），但现有路由不支持加权分流。
- **修正状态**：✅ 已修正
- **验证详情**：
  - 第 515-521 行新增"灰度切量方案（补充 [F-3]）"说明：
    - 明确现有 `RoutingDecisionRecord` 与 `AgentResolver` 是单值映射，不支持加权分流。
    - **首版仅支持"全切"（rollout=1.0）**：upgrade 一次性将路由从旧版本切到新版本，`rollout_steps` 首版固定为 [1.0]，传入灰度阶梯将被忽略并记录 warning。
    - 加权灰度切量延后至路由层升级支持加权分流后实现。
  - 第 64、94、504、1221 行多处一致描述"首版仅支持全切"。
  - grep 验证：`全切` / `rollout=1.0` 出现 10 处。

#### F-5: SupervisorAction 枚举跨文档不一致

- **所属文档**：design-debate-agent.md
- **首次问题**：辩论型定义 `SupervisorAction` 仅 5 成员（REPLACE/DEGRADE/TERMINATE/UPGRADE/NONE），监督者型定义 7 成员（多 PATROL/ALERT），应统一。
- **修正状态**：✅ 已修正
- **验证详情**：
  - 第 348-358 行修正枚举定义为 7 成员版本（PATROL/ALERT/REPLACE/DEGRADE/TERMINATE/UPGRADE/NONE），与监督者型一致。
  - 第 349-351 行明确注释"SupervisorAction 枚举定义在 `py/maop/core/scheduling/supervisor.py` 中，辩论型复用监督者型定义（见 design-supervisor-agent.md 2.2.1 节），此处仅作引用说明，不重复定义。"
  - grep 验证：`PATROL` / `ALERT` 出现 2 处（第 352-353 行），`复用监督者型定义` / `不重复定义` 出现 2 处。

#### F-6: EventBus.publish 伪代码误用

- **所属文档**：design-blackboard.md
- **首次问题**：设计文档写 `EventBus.publish("blackboard.changed", ...)`，但实际 API 是 `publish(event: Event)`，接收 `Event` 对象而非 `(topic, data)`。
- **修正状态**：✅ 已修正
- **验证详情**：
  - 第 526-569 行新增"2.3.2.2 事件定义与发布 API（对应审核项 F-6）"小节，明确：
    - 事件主题 `blackboard.changed`，`data` 含完整字段，`source="blackboard"`。
    - 异步发布代码示例：`await event_bus.publish(Event(topic="blackboard.changed", data={...}, source="blackboard"))`。
    - 同步发布代码示例：`event_bus.publish_sync(Event(...))`。
  - 第 151、268、303、539、676、698 行所有 EventBus 调用均修正为 `publish(Event(...))` 形式。
  - grep 验证：`event_bus.publish(` / `bus.publish(` 出现 8 处，全部为正确形式；`EventBus.publish("`（错误形式）出现 0 处。

#### R-1: 巡检循环启动时机未明确

- **所属文档**：design-supervisor-agent.md
- **首次问题**：`start_patrol_loop()` 创建后台 `asyncio.Task`，未说明启动时机与生命周期管理。
- **修正状态**：✅ 已修正
- **验证详情**：
  - 第 382-398 行新增"启动时机与生命周期（补充 [R-1]）"说明，明确两种启动方式（由配置 `patrol_loop_start_mode` 决定）：
    - **懒启动（lazy，默认）**：在 `Engine.run()` 首次调用时，若监督者已配置且巡检循环未启动，则通过 `asyncio.create_task()` 懒启动。
    - **startup 启动**：在 FastAPI 应用 startup 事件中显式调用 `await supervisor.start_patrol_loop()`。
  - 明确 shutdown 时调用 `stop_patrol_loop()` 优雅停止，同步上下文（如单元测试）中不启动巡检循环。
  - grep 验证：`懒启动` / `startup` / `启动时机` 出现 6 处。

#### R-2: terminate 后无可用 agent 边界

- **所属文档**：design-supervisor-agent.md
- **首次问题**：`terminate()` 标记 `disabled=True` 后，若该 agent 是某 routing_key 的唯一可用 agent，该 routing_key 将无可用 agent。
- **修正状态**：✅ 已修正
- **验证详情**：
  - 第 482-494 行新增"边界检查（补充 [R-2]）"说明：
    - terminate 前检查该 agent 是否为某 routing_key 的唯一可用 agent（通过 `routing_decision.py` 查询，排除已 disabled/drained 的 agent）。
    - 若是唯一可用：若配置了 fallback，提示先执行 `replace()` 切换路由再 terminate；若未配置 fallback，拒绝 terminate 并抛 `TerminateRefusedError(agent_id, routing_key)`。
    - 手动 `force=True` 可跳过检查，但审计记录标记 `force_bypass_safety=True`。
  - grep 验证：`TerminateRefusedError` / `唯一可用` 出现 2 处。
  - 辩论文档 `adjudicate()` 方法（第 408-414 行）也补充了 terminate 边界处理：terminate 前检查唯一可用，若是则降级为 degrade（而非 terminate），与监督者型边界保护目标一致。

#### R-3: upgrade 回退判定标准未定义

- **所属文档**：design-supervisor-agent.md
- **首次问题**：设计说"若新版本劣化则自动回退"，但未明确"劣化"的量化标准。
- **修正状态**：✅ 已修正
- **验证详情**：
  - 第 523-530 行新增"回退触发条件（补充 [R-3]）"说明，明确量化判定标准（任一满足即触发自动回退）：
    - 新版本窗口失败率 > 0.15（`failure_rate > 0.15`）。
    - 新版本平均延迟 > 旧版本平均延迟 × 1.5（`avg_latency_new > avg_latency_old * 1.5`）。
    - 新版本连续 2 次巡检 `reachable=False`。
  - 回退时将路由全切回旧版本，终止新版本 agent，发布 `agent_upgrade.rolled_back` 事件（附回退原因与劣化指标快照）。
  - grep 验证：`failure_rate > 0.15` / `avg_latency.*1.5` / `回退触发条件` 出现 5 处。

#### C-2: DEBATE 插入描述偏差

- **所属文档**：design-debate-agent.md
- **首次问题**：设计说"DEBATE 阶段是纯增量插入，不修改既有阶段签名"，但实际插入需修改 `run_cycle()` 方法体，`_phase_evaluate()` 入参从 `suggestions` 变为 `debated_suggestions`。
- **修正状态**：✅ 已修正
- **验证详情**：
  - 第 505 行修正"回滚兼容"描述为："DEBATE 阶段插入需修改 `evolution_loop.py:run_cycle()` 方法体，在 `_phase_suggest()` 和 `_phase_evaluate()` 调用之间插入 `_phase_debate()` 调用，并将 `_phase_evaluate()` 入参改为辩论后的建议列表 `debated_suggestions`。`LoopPhase` 枚举新增 DEBATE 成员（在 `evolution_loop_types.py` 中），既有枚举值不变。这是方法体修改而非签名修改，既有调用方不受影响。当 `DebateOrchestrator` 未配置时，`_phase_debate()` 直接透传 suggestions，行为退化为现状，保证向后兼容。"
  - 描述准确反映了实际修改范围（方法体修改而非签名修改），并明确了向后兼容退化路径。
  - grep 验证：`需修改.*run_cycle` / `方法体.*run_cycle` 出现 1 处（第 505 行）。

#### R-4: 辩论成本放大无预算阈值

- **所属文档**：design-debate-agent.md
- **首次问题**：高频辩论下成本与延迟显著上升，设计中仅"HIGH severity 或显式请求才辩论"作为缓解，未给出成本预算阈值。
- **修正状态**：✅ 已修正
- **验证详情**：
  - 第 563 行参数表新增 `max_debate_tokens`（默认 50000）：单场辩论总 token 上限，超限后提前终止并降级为单 agent 决策。
  - 第 568 行新增"成本控制"说明：辩论过程中累计各 agent 各轮 token 消耗，超限后提前终止辩论并降级为单 agent 决策（取历史可信度最高的 agent 直接 dispatch），同时在 `Verdict.adjudication_reason` 中记录"成本超限降级"。
  - 第 764 行风险与缓解表也更新了缓解措施。
  - grep 验证：`max_debate_tokens` / `成本超限` / `成本控制` 出现 3 处。

### 2.3 低严重度问题（3 项）

#### F-4: 三种巡检策略实现复杂度高

- **所属文档**：design-supervisor-agent.md
- **首次问题**：full/sample/adaptive 三种巡检策略均需实现，adaptive 策略实现复杂度较高。
- **修正状态**：✅ 已修正
- **验证详情**：
  - 第 1008-1010 行新增"首版实现范围（补充 [F-4]）"说明：
    - **首版仅实现 full 策略**，`patrol_strategy` 参数首版仅接受 `"full"`，传入 `"sample"` 或 `"adaptive"` 将记录 warning 日志并降级为 full。
    - sample / adaptive 策略延后至 agent 规模超过 50 后按需实现。
    - `HealthChecker.check_sample()` 与 `check_adaptive()` 方法签名保留（供未来实现），首版内部直接转发到 `check_all()`。
  - grep 验证：`首版仅实现 full` / `首版仅.*full` 出现 1 处。

#### R-5: 辩论轨迹清理策略缺失

- **所属文档**：design-debate-agent.md
- **首次问题**：高频辩论下 `debate` 表膨胀，设计中提到"设保留期清理"但未给出具体策略。
- **修正状态**：✅ 已修正
- **验证详情**：
  - 第 507 行新增"轨迹清理"说明：辩论轨迹保留 30 天，超期记录归档到冷存储后删除。清理任务在 EvolutionLoop 的 CONSOLIDATE 阶段执行——CONSOLIDATE 阶段原有逻辑不变，新增调用 `debate.persistence.cleanup_expired(retention_days=30)` 清理超期辩论记录。清理任务受 CONSOLIDATE 阶段既有频率约束（每轮演化循环执行一次），避免高频清理抢占主流程资源。
  - 第 718 行文件清单中 `persistence.py` 职责也补充了清理机制说明。
  - 第 768 行风险与缓解表也更新了缓解措施。
  - grep 验证：`保留 30 天` / `cleanup_expired` / `轨迹清理` 出现 3 处。

#### R-6: round_timeout 整轮等待

- **所属文档**：design-debate-agent.md
- **首次问题**：单轮超时 120s，超时方视为弃权，但若 1 个 agent 慢、其余 agent 快，整轮仍需等待慢 agent 超时。
- **修正状态**：✅ 已修正
- **验证详情**：
  - 第 561 行参数表新增 `agent_timeout_s`（默认 60）：单 agent 超时，超时方视为弃权（confidence=0），不等待慢 agent。
  - 第 570 行新增"超时粒度"说明：超时判定粒度为"单 agent 超时"——每个 agent 有独立的 `agent_timeout_s`，单个 agent 超时即视为弃权，不等待慢 agent 完成后再判定整轮。`round_timeout_s` 作为整轮兜底超时。两者关系：`agent_timeout_s` < `round_timeout_s`，单 agent 超时优先触发。
  - 第 765 行风险与缓解表也更新了缓解措施。
  - grep 验证：`agent_timeout_s` / `超时粒度` / `单 agent 超时` 出现 3 处。

#### R-7: read_domains 声明正确性依赖

- **所属文档**：design-blackboard.md
- **首次问题**：并发分组依赖知识源正确声明 `read_domains`，若未声明或声明不全，可能导致本应串行的知识源被并发执行。
- **修正状态**：✅ 已修正
- **验证详情**：
  - 第 466-475 行新增"read_domains 声明与并发校验（对应审核项 R-7）"说明，明确四重校验机制：
    1. **注册时默认值**：未声明 `read_domains`（返回空列表）默认为**只读全部域**（保守策略，等价于 `["*"]`），不与其他知识源并发执行，仅可独占运行。
    2. **调度前并发校验**：控制器在调度前对同轮命中的知识源两两校验 `write_domains` 与 `read_domains` 交集，有交集则不可并发。
    3. **校验失败处理**：运行时检测到并发冲突，控制器记录警告日志并降级为串行执行，确保数据一致性。
    4. **声明完整性要求**：知识源子类必须如实声明，控制器在注册时记录声明信息到 dashboard，建议在单元测试中加入声明完整性断言。
  - grep 验证：`read_domains 声明与并发校验` 出现 2 处。

#### R-8: 白名单机制未详述

- **所属文档**：design-blackboard.md
- **首次问题**：动态注册知识源的安全对策提到"知识源类名白名单校验"，但未说明白名单的配置方式与加载时机。
- **修正状态**：✅ 已修正
- **验证详情**：
  - 第 653-688 行新增"知识源白名单机制（对应审核项 R-8）"说明，明确四方面：
    1. **白名单配置**：配置在 `config/blackboard.yaml` 中，格式为 `allowed_knowledge_sources` 列表（给出示例）。
    2. **注册时校验**：`register_ks` 校验 `type(ks).__name__` 是否在白名单中，不在则拒绝注册并抛 `KnowledgeSourceNotAllowedError`，记录安全事件。
    3. **白名单为空策略**：白名单为空表示允许全部（开发模式）；生产环境必须配置非空白名单，启动时若 `env == "prod"` 且白名单为空，控制器拒绝启动。
    4. **白名单变更审批**：变更后通过 EventBus 发布 `blackboard.whitelist_changed` 事件通知审计模块（给出代码示例），审计模块订阅该事件记录变更轨迹。
  - 第 925 行风险与对策表也更新了对策说明。
  - grep 验证：`blackboard.whitelist_changed` / `白名单` 出现 12 处。

---

## 3. 新引入问题检查

对三份修正后设计文档进行新引入问题检查，重点验证 EventBus 统一方案跨文档一致性、伪代码正确性、枚举定义一致性、边界处理一致性。

### 3.1 EventBus 统一方案跨文档一致性 ✅

三份文档的 EventBus 统一方案完全一致：

| 文档 | 统一方案 | 验证结果 |
|------|----------|----------|
| design-supervisor-agent.md | 统一使用 `core.reliability.event_bus.EventBus` 的 `publish(Event)` API，`get_event_bus()` 返回 core 版单例，修正 `_publish_event` 和 TYPE_CHECKING 导入 | ✅ 第 908-942 行明确，与 engine.py 导入一致 |
| design-debate-agent.md | EventBus API 统一使用 `core.reliability.event_bus.EventBus` 的 `publish(Event)` 方法，`event_bus` 由 `get_event_bus()` 返回全局单例 | ✅ 第 383-390 行明确 |
| design-blackboard.md | 全局唯一实例统一使用 `core.reliability.event_bus.EventBus`，通过 `get_event_bus()` 获取单例，黑板控制器/监督者/执行引擎均注入同一实例 | ✅ 第 508-524 行明确 |

grep 验证：三份文档中 `enterprise.notification.event_bus` 的出现均为说明性引用（描述当前问题/修正方案），无实际导入残留。`emit()` 的出现均为说明性引用，无实际调用残留。

### 3.2 伪代码 EventBus 调用正确性 ✅

黑板文档所有 EventBus 调用均修正为 `publish(Event(...))` 或 `publish_sync(Event(...))` 形式：
- grep `EventBus.publish("`（错误形式 `EventBus.publish("topic", ...)`）出现 0 处。
- grep `event_bus.publish(` / `bus.publish(` 出现 8 处，全部为 `publish(Event(...))` 正确形式。

### 3.3 枚举定义一致性 ✅

`SupervisorAction` 枚举跨文档一致：
- 监督者文档（第 234-242 行）：7 成员（PATROL/ALERT/REPLACE/DEGRADE/TERMINATE/UPGRADE/NONE）。
- 辩论文档（第 348-358 行）：7 成员一致，注释明确"复用监督者型定义，此处仅作引用说明"。

### 3.4 新引入的极低严重度问题（2 项，不阻塞实施）

#### N-1: 辩论文档 SupervisorAction 枚举"不重复定义"注释与代码块列出成员的轻微矛盾

- **严重度**：极低（文档表述）
- **详情**：辩论文档第 349-351 行注释说"此处仅作引用说明，不重复定义"，但代码块中仍列出了 7 个成员的完整定义（第 352-358 行）。
- **影响**：无实质影响。注释意图是"运行时复用监督者型定义"，列出成员是为读者参考方便，语义上已统一。
- **建议**：可保留现状（作为引用说明），或将代码块改为注释形式仅列成员名。实施阶段以监督者型 `supervisor.py` 中的定义为准。

#### N-2: 监督者文档与辩论文档 terminate 边界处理策略差异

- **严重度**：低（设计细节）
- **详情**：
  - 监督者文档 `terminate()` 方法（第 482-494 行）：terminate 前检查唯一可用 agent，若是则拒绝 terminate 并抛 `TerminateRefusedError`，或提示先 `replace()`；`force=True` 可跳过。
  - 辩论文档 `adjudicate()` 方法（第 408-414 行）：terminate 前检查唯一可用 agent，若是则自动降级为 degrade（而非 terminate）。
- **影响**：两份文档的边界处理策略不同，但保护目标一致（都避免 routing_key 无可用 agent 导致调度死锁）。差异源于场景不同——`terminate()` 是通用显式调用（拒绝更安全），`adjudicate()` 是辩论僵局自动裁决（降级为 degrade 更适合自动流程，避免抛异常中断裁决）。
- **建议**：实施阶段统一为"`terminate()` 方法统一抛 `TerminateRefusedError`，`adjudicate()` 捕获该异常后降级为 degrade"，使 terminate 边界逻辑只在一处实现，adjudicate 复用。此为实施细节，不影响设计架构正确性。

### 3.5 其他检查项 ✅

- **向后兼容退化路径**：三份文档均明确"未配置时退化为现状"的退化路径（监督者 `supervisor is None` 分支、辩论 `_phase_debate()` 透传、黑板控制器未启动），未因修正引入破坏性变更。
- **修改文件清单完整性**：三份文档的修改文件清单均更新了修正项（如监督者文档第 1137 行明确标注 `[C-1] 修正 _publish_event`），与正文修正一致。
- **风险与缓解表更新**：辩论文档第 762-769 行、黑板文档第 919-925 行风险与缓解表均同步更新了修正后的缓解措施，与正文一致。

---

## 4. 综合结论

### 4.1 二次审核结论

表：二次审核综合结论

| 设计文档 | 首次问题数 | 已修正数 | 未修正数 | 新引入问题 | 二次结论 |
|----------|-----------|---------|---------|-----------|----------|
| design-supervisor-agent.md | 8 | 8 | 0 | 0 | ✅ 通过 |
| design-debate-agent.md | 7 | 7 | 0 | 1（极低，N-1） | ✅ 通过 |
| design-blackboard.md | 4 | 4 | 0 | 0 | ✅ 通过 |
| **合计** | **12**（2 高 + 7 中 + 3 低） | **12** | **0** | **1 极低 + 1 低**（N-1/N-2，不阻塞） | **✅ 全部通过** |

### 4.2 修正质量评估

- **高严重度问题（C-1 / C-3）修正质量优**：跨三份文档协同修正，EventBus 统一方案完全一致（统一 `core.reliability.event_bus.EventBus` + `publish(Event)` API + `get_event_bus()` 单例），从根本上解决了两个 EventBus 实现不互通的问题，为跨架构协作（监督者订阅黑板事件、黑板订阅监督者预警）奠定了基础。
- **中严重度问题修正质量优**：7 项中严重度问题全部修正，每项均补充了明确的实现方案、量化标准或边界处理，修正内容具体可执行（如 F-1 探针实现给出三类探针的代码来源与字段映射，R-3 回退标准给出三个量化触发条件）。
- **低严重度问题修正质量良**：3 项低严重度问题全部修正，首版实现范围明确（F-4 仅 full 策略），清理机制与超时粒度细化（R-5 保留 30 天 + CONSOLIDATE 清理，R-6 单 agent 超时优先）。
- **跨文档一致性优**：三份文档的 EventBus 统一方案、SupervisorAction 枚举、terminate 边界保护目标均一致，未因各自修正引入新的跨文档矛盾。

### 4.3 审核决定

**三份设计文档二次审核全部 ✅ 通过**。

- 首次审核的 12 项问题（2 高 + 7 中 + 3 低）已全部修正，修正质量高、方案一致。
- 二次审核未发现高/中严重度的新引入问题。
- 仅发现 2 项极低/低严重度的文档表述/设计细节问题（N-1 枚举注释表述、N-2 terminate 边界策略差异），不阻塞实施，可在实施阶段统一。

**建议三份设计文档进入执行阶段**。执行阶段实施时注意：

1. **EventBus 统一**：优先实施 C-1 / C-3 的 EventBus 统一修正（`failure_detector.py` 的 `_publish_event` 方法 + TYPE_CHECKING 导入），这是跨三份设计的共同基础。
2. **terminate 边界统一（N-2）**：实施时将 `terminate()` 统一为抛 `TerminateRefusedError`，`adjudicate()` 捕获后降级为 degrade，使边界逻辑只在一处实现。
3. **SupervisorAction 枚举（N-1）**：实施时以 `py/maop/core/scheduling/supervisor.py` 中的 7 成员定义为准，辩论文档不重复定义。
4. **首版范围控制**：F-3 首版仅全切、F-4 首版仅 full 策略，实施时按此范围控制复杂度。

---

> 审核人：CodeArts 审核代理（Task 346）
> 审核状态：✅ 通过（三份设计二次审核全部通过）
> 下一步：三份设计进入执行阶段，优先实施 EventBus 统一修正（C-1 / C-3）