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

### P1-3 PreemptableWorkerPool 零实例化 — 结论：**不改**（库级 opt-in API）
- 类存在、`submit_preemptable()` 存在，但**从未被实例化**
- **复核结论（2026-11-11）**：这不是缺陷，是**库级 API**
  - 零产品面：`dashboard/` 无路由、前端无页面、settings 无开关
  - `ROADMAP.md` / `CHANGELOG.md` 均诚实标注「opt-in、默认关、行为不变」
  - 真抢占会**取消运行中的任务**——对 LLM 调用 / 有副作用的 agent 任务是危险
    操作，不适合做成产品默认开关
  - 接线它等于给没有消费者的能力造一个产品旋钮（为接线而接线）
- 位置：`core/reliability/preemptable_worker_pool.py`

### P2-1 事件联邦整块死 — 第一跳已修，子系统仍无消费者
- `FederationBus(` 生产**零实例化**（7 处全在测试）⇒ `attach_federation()` /
  `register_bridge()` 在生产中永不调用
- ✅ **2026-11-11 已修第一跳**：`EventBus` 的裸 `"*"` 订阅此前匹配不到任何事件
  （`subscribe` 文档承诺尾部通配，实现只处理 `前缀.*`），而
  `MAOPEventBusBridge` 正是 `subscribe("*", handler)` —— 桥接→联邦这一跳曾经
  **恒为 0 条**（实测），现已通（2/2），并有反向验证钉子
- ⚠️ **但能力边界没变**：本模块无任何跨进程传输（无 HTTP/WS/Redis/MQ/socket），
  只做进程内内存分发；设计初衷是跨**产品**互通（MAOP ↔ MAOS），不是同产品扩容
- **结论：不立项传输层**；模块 docstring 已写明「未接线 / 仅内存内 / 无跨进程」
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

## 三、处置结果（2026-11-11 全部结项）

| 项 | 状态 | 说明 |
|---|---|---|
| P2-2 中间件安装 | ✅ 已修 | `MetricsMiddleware` / `TraceContextMiddleware` 已 `add_middleware` |
| P2-3 `recover_unacked` | ✅ 已修 | 经查 `dequeue` 每轮会 `_reclaim_unacked`，**不丢消息**；仍补了巡检入口 |
| P1-2 DebateDispatcher | ✅ 已修 | `MAOP_DEBATE_ENABLED`（默认关）+ 容器工厂注册 |
| P1-1 Supervisor | ✅ 已修 | `MAOP_SUPERVISOR_ENABLED` / `_ACTIONS_ARMED`（默认关）；未 arm 时只 warn，且 warn 里带 `suppressed_action` |
| P3 bb_health 计数 | ✅ 已修 | `record_ks_dispatch` 接黑板真实派发点、`record_dead_letter` 接三条入死信分支；monitor 改进程级单例 |
| P2-1 事件联邦 | 🔶 第一跳已修 | 裸 `"*"` 通配已修；**子系统仍无生产消费者，无跨进程传输，不立项**；docstring 已标注 |
| P1-3 PreemptableWorkerPool | ⛔ 不改 | 零产品面、文档已诚实标注 opt-in、真抢占会取消运行中任务 ⇒ 属库级 API |

**三条 P1 的共同形态**：能力建好、代码正确、单元测试也绿，但生产路径上没有任何
调用点，于是静默地什么都不发生。修复方式分两种——**能给出产品面且风险可控的接线
并加开关**（Supervisor / Debate），**没有产品面的明确标注为库级 API**（抢占池 /
联邦）。两者都不该"默认打开"，因为都会改变既有行为。

## 方法边界

- 未评估 `distributed_scheduler/` 的运行期行为；未验证 `recover_unacked` 缺位时
  消息的实际命运（标记为待确认）
- 扫描范围限"能力型模块"，未全域扫描
- 本文档不宣称项目安全
