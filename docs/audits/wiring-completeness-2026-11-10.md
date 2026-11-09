# 接线完整性审计（2026-11-10）

> 方法：两个机械扫描脚本产候选（方法级「定义了但生产零调用」+ 类级「导出了但生产零实例化」），
> **候选一律逐条读上下文定性**——机械口径只产候选，不定缺陷。
> 扫描范围限能力型模块：`scheduling` / `delegate` / `reliability` / `coordination` /
> `orchestration` / `observability`。

## 为什么做这件事

本轮修复过程中反复撞到**同一族缺陷**——能力建好了、接线没做，而且**不报错、只静默失效**：

| 已修（本轮） | 形态 |
|---|---|
| `MaopLoadingBar` | 组件 + 事件通道都在，缺触发器（零调用点）→ PR #253 |
| `acquire` / `release` + `_active_count` | 存在但零调用方 ⇒ 负载均衡恒字典序、`max_concurrent` 从未强制 → PR #256 |
| `set_priority_queue` / `dispatch_priority` / `drain_pending` | 存在但零调用方 ⇒ PRIORITY 只是枚举里的一个名字 → PR #259 |
| `update_progress` / `complete` / `fail`（长任务） | 存在但零入口 ⇒ 提交的任务永停 pending、SSE 推一条即断 → PR #254 |

四条不是巧合，是**系统性**的。所以做一次扫清，而不是等下一个人再踩。

## 一、确证的真缺口

### P1-1 主动 Supervisor 整块不可达
- `Supervisor(...)` **生产零实例化**；`set_supervisor()` **生产零调用**（测试侧 21 处）
  ⇒ `get_supervisor()` 恒 `None`（"passive-only mode"）
- 后果：**patrol 循环永不启动**（`start_patrol_loop` 亦零调用）；6 个控制动作
  （warn / replace / degrade / terminate / upgrade）永不自动发生；
  `/api/supervisor/*` 全部 404
- 代码是**诚实的**（`None` 分支与 404 文案都写明"需 Instantiate 并 set_supervisor()"），
  但能力对用户不可达——除非有人手工在代码里开启
- 位置：`maop/core/scheduling/supervisor.py`、`core/scheduling/failure_detector.py:551-570`、
  `dashboard/routers/supervisor.py:57,65`

### P1-2 DebateDispatcher 零注册
- 演化循环**接受** `debate_dispatcher` 构造参数并在 `_debate_enabled` 时使用，
  但**全仓没有任何一处传它**（`debate_dispatcher=` 生产零命中）
- ⇒ `self._debate_dispatcher` 恒 `None` ⇒ 辩论在演化循环里永不运行；
  `/api/debate/*` 404（"Instantiate a DebateDispatcher and register it"）
- 位置：`core/evolution/evolution_loop.py:129,186,264`、`dashboard/routers/debate.py:71-96`

### P1-3 PreemptableWorkerPool 零实例化
- 类存在、`__init__` 导出存在、`submit_preemptable()` 存在，但**从未被实例化**
- ⇒ γ-3 抢占机制整块死，`submit_preemptable` 不可达
- 位置：`core/reliability/preemptable_worker_pool.py`

### P2-1 事件联邦整块死
- `EventFederation(` 生产零实例化 ⇒ `attach_federation()` / `register_bridge()` 永不调用
- ⇒ 跨实例事件联邦（`FederationBus` / `MAOPEventBusBridge` / `DictEventBridge`）不可达
- 位置：`core/reliability/event_federation.py`

### P2-2 HTTP 指标与 trace 上下文提取从未安装 ✅ 本次已修
- `MetricsMiddleware`、`TraceContextMiddleware` 全仓**只在**：自身定义、
  `observability/__init__.py` 导出、测试——**没有任何 `app.add_middleware`**，
  而两者 docstring 都写着 "Installed via `app.add_middleware(...)`"
  ⇒ **文档声明了、代码没做**
- 后果：真实流量的 HTTP 请求数/延迟不进 `/api/prometheus`；请求头里的
  `traceparent` / W3C trace context 永不被提取（分布式追踪在 HTTP 边界断链）
- **真机正反对照**：修前 `GET /api/prometheus` 只有 `# HELP`/`# TYPE` 两行、
  **没有任何 `maop_requests_total` 数据序列**；修后打 5 个请求即出现
  `maop_requests_total{method=GET|path=/api/agents|status=200} 5.0`
- 修法：在 `dashboard/_middleware_stack.py` 的安全栈之后装配两者
  （Starlette `add_middleware` 前置插入 ⇒ 最后添加的最外层、最先执行 ⇒ 执行顺序
  `TraceContext → Metrics → CSP → Auth → …`，使**被鉴权拒绝的 401 也计入指标**——
  那恰是最该被看到的请求）。开关：`MAOP_HTTP_METRICS=0` 可关。
- 钉子测试：`py/tests/test_observability_middleware_wiring.py`（7 条）——
  钉的是"**在栈上**"与"真的记到指标里"，**不是"类能实例化"**：
  这次缺陷的本体就是缺那一行 `add_middleware`，只断言类存在的测试在缺陷期间一样绿。

### P2-3 `recover_unacked` 零调用
- 消息队列的 in-flight 回收函数存在，但**无人调用**
- ⇒ 消费者崩溃后未 ack 的消息永不回到队列
- **待确认**：是否真会丢消息，取决于有无其他补偿路径（本轮未验证）
- 位置：`core/reliability/message_queue.py:753`

### P3 blackboard 健康计数两条零调用
- `record_ks_dispatch()` / `record_dead_letter()` **连测试都零调用**
  （同文件另两条有测试）⇒ 黑板的 KS 派发与死信计数恒为 0，健康面看不出来
- 位置：`core/reliability/bb_health.py:72,87`

## 二、逐条定性的假阳性（列出以防后人重复劳动）

| 候选 | 为何不是缺口 |
|---|---|
| `DispatchPriorityMixin` / `DispatchRecordingMixin` / `SupervisorActionMixin` / `SupervisorDispatchMixin` / `SupervisorPatrolMixin` / `SupervisorStatusMixin` | mixin，由宿主类（Dispatcher / Supervisor）实例化 |
| `MaopDagWorkflow` | 以**类对象**注册给 Temporal Worker（`workflows=[MaopDagWorkflow]`），非构造调用 |
| `NodeAffinity` / `TaskAssignment` | Pydantic 模型，经 `model_validate` 构造，不走 `Cls(` |
| `register_action` / `register_verifier`（self_heal） | 只是 `HealAction.CUSTOM` 的扩展点；**内置动作**（清缓存等）与 DB 种子规则都在，引擎可独立工作 |
| `InMemoryAgentStore` / `InMemoryMCPRegistry` | 内存实现，生产用 DB 版；测试用内存版做隔离 |
| `set_state`（circuit_breaker）/ `registered_ks` / `completed_steps` / `failure_detector()` | 访问器/状态注入，测试侧有调用；非"能力入口" |

**方法论提醒**：类对象注入（中间件、workflow、依赖注入字符串）会让"零实例化"扫描出现假阳性；
反之，它也会**漏掉**真缺口——`MetricsMiddleware` 就属于"类对象形式根本没装上"，
靠人工补查才定论。**机械扫描必须配人工复核，两个方向都会错。**

## 三、建议处置顺序

1. ✅ **P2-2 中间件安装**——本次已修（纯增量可观测，无可达性/行为风险；
   且属"修文档与代码的不一致"）
2. **P2-3 `recover_unacked` 接线**——先确认有无其他补偿路径（是否真会丢消息），
   再决定接在哪
3. **P1-2 DebateDispatcher 注册**——需决定"谁创建、何时创建"，属产品行为
4. **P1-3 PreemptableWorkerPool**——与 P1-1 同类：需决定默认策略
5. **P1-1 Supervisor**——**不建议擅自默认开启**：它会 replace / terminate agent，
   是真实的行为变更，须产品决策（但至少应让 dashboard 页面给出"如何开启"的
   可见指引，而不是只有 API 404）

## 方法边界

- 未评估 `distributed_scheduler/` 的运行期行为；未验证 `recover_unacked` 缺位时
  消息的实际命运（标记为待确认）
- 扫描范围限"能力型模块"，未全域扫描
- 本文档不宣称项目安全
