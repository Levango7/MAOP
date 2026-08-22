# MAOP 监督者型 Multi-Agent（Supervisor）设计方案

> 文档状态：设计阶段（未实施）
> 关联任务：Task 337
> 设计范围：监督者型（Supervisor）多 Agent 架构 — 主动巡检 / 预警 / 替换 / 降级 / 终止 / 升级
> 约束：本文件仅做设计，不含代码实施
> 关联文档：`docs/design-debate-agent.md`（对抗辩论型，监督者作为裁决兜底）、`docs/design-blackboard.md`（黑板架构）

---

## 第1章 需求分析

### 1.1 现状分析

#### 1.1.1 现有 FailurePatternDetector 能力盘点

MAOP 当前在 `py/maop/core/scheduling/failure_detector.py` 实现了被动故障检测器 `FailurePatternDetector`，其能力边界如下：

- **滑动窗口跟踪**：每个 agent 维护一个 `deque[_Outcome]`（默认 `window_size=50`），记录最近 N 次任务结果（success / failure + latency）。统计量（`failure_rate`、`avg_latency`、`timeout_rate`）基于窗口计算，旧行为自动老化。
- **Drain 摘流**：当窗口失败率超过 `failure_rate_threshold`（默认 0.30），强制 `weight=0.0`，发布 `agent_drained` 事件（level=error）。
- **Recovery 灰度回切**：摘流后需累计 `recovery_consecutive_successes`（默认 5）次连续成功，沿恢复阶梯 `[0.3, 0.6, 1.0]` 逐步回切；恢复期间任一失败重置连续成功计数（但不立即重新摘流，除非窗口失败率再次超阈）。
- **Prometheus 指标**：通过 `MetricsCollector` 暴露 5 个 gauge —— `maop_agent_failure_rate`、`maop_agent_weight`、`maop_agent_status`（0=normal/1=drained/2=recovering）、`maop_agent_timeout_rate`、`maop_agent_avg_latency_seconds`。
- **EventBus 集成**：可选注入 `EventBus`，在 drain / recovering / recovered 状态变迁时发布通知事件，支持异步 fire-and-forget 与同步回退两种模式。
- **线程安全**：`threading.Lock` 保护 per-agent 状态，内省端点可并发读取。
- **单例管理**：`get_failure_detector()` / `set_failure_detector()` 提供进程级单例，供调度路径与测试注入共用。

#### 1.1.2 现有不足

`FailurePatternDetector` 的核心定位是**被动响应式故障检测器**——它仅在 `record_result()` 被外部调度路径调用时更新状态，无任何主动能力。对照监督者型架构所需的六大能力，其缺口如下。

表：FailurePatternDetector 能力缺口对照表

| 编号 | 监督者目标能力 | 现状 | 缺口 | 证据位置 |
|------|----------------|------|------|----------|
| S-1 | 巡检（Patrol） | 无 | 无主动巡检循环，长期无任务的 agent 处于异常状态（进程失活、熔断开启）也无法被发现 | `failure_detector.py:record_result()` 仅响应外部调用，无定时器/后台任务 |
| S-2 | 预警（Alert） | 部分 | 仅在 drain / recovery 状态变迁时发事件，无基于阈值规则的主动预警（如延迟劣化、超时率上升但未达摘流阈值时无预警） | `_update_state_locked()` 仅在 drain 边界发事件 |
| S-3 | 替换（Replace） | 无 | 摘流仅置权重为 0，不将流量切到备用 agent，故障 agent 仍占注册表 | `drain` 仅修改 `state.weight`，无路由切换 |
| S-4 | 降级（Degrade） | 部分 | 摘流是二值的（weight=0 或恢复阶梯），无中间态降级（如 weight=0.5 限流、缩短超时、限制并发） | 恢复阶梯固定为 `[0.3, 0.6, 1.0]`，无自定义降级因子 |
| S-5 | 终止（Terminate） | 无 | 摘流不等同终止——agent 仍可被显式调用，无"标记不可用 + 审计"的硬终止能力 | 无 terminate 方法，无 `disabled` 状态标记 |
| S-6 | 升级（Upgrade） | 无 | 恢复是被动等待连续成功，无主动灰度升级（如将 agent 切到新版本、逐步放量） | `recovery` 仅在被动记录成功时推进，无版本切换 |

#### 1.1.3 与辩论型 / 黑板架构的关系定位

`docs/design-debate-agent.md` 已将监督者作为辩论裁决兜底提及（第 5 点："裁决节点收敛：当达到最大轮次仍未收敛，由监督者（Supervisor）裁决节点做最终判定"），并在 2.2.5 节给出了 `Supervisor` 的接口草图（`patrol()` / `warn()` / `adjudicate()` / `replace()` / `degrade()` / `terminate()` / `upgrade()`）。

但辩论型文档中监督者仅作为**裁决兜底节点**出现，其六大主动能力（巡检 / 预警 / 替换 / 降级 / 终止 / 升级）的完整设计、巡检调度策略、与执行引擎/循环执行器的集成方案、Dashboard API 均未展开。本文档将监督者型作为**独立的多 Agent 架构模式**完整设计，使其既能独立运行（常态主动监督），也能作为辩论裁决节点被调用（僵局兜底），两种模式共享同一 `Supervisor` 实现。

与黑板架构（`docs/design-blackboard.md`）的关系：黑板架构是**多知识源协作求解范式**，监督者型是**控制平面管控范式**。二者正交——监督者可监控黑板控制器本身（如知识源执行超时、黑板条目写入冲突率），黑板的知识源也可作为被监督的 agent 接受巡检。本文档不重复黑板架构设计，仅在 2.3.4 节说明监督者可触发演化循环（与黑板共享 EvolutionLoop 集成点）。

### 1.2 目标能力

本设计将 `FailurePatternDetector` 升级为主动监督者 `Supervisor`，在保留全部被动检测能力的基础上，新增六大主动能力，形成"被动检测为底座、主动巡检为常态、管控动作为手段"的三层监督体系。

1. **巡检（Patrol）**：定期主动检查所有已注册 agent 的健康状态、性能指标、资源占用。与被动 `record_result()` 互补——即使某 agent 长期无任务，巡检也能发现其处于异常状态（进程失活、熔断开启、连接池耗尽）。巡检发现劣化时自动触发预警。

2. **预警（Alert）**：基于阈值规则主动推送预警，经 EventBus 发布 `supervisor.alert` 事件。预警分级（info / warning / error / critical），支持多维度阈值（失败率、延迟、超时率、资源占用、熔断状态）。预警不等同摘流——劣化但未达摘流阈值时发预警，达摘流阈值时摘流并发 critical 预警。

3. **替换（Replace）**：将持续故障的 agent 替换为备用 agent。替换操作更新路由注册表（将 `routing_key → agent_a` 改为 `routing_key → agent_b`），原 agent 标记为 `replaced` 并保留审计记录。替换支持灰度（先切 10% 流量到备用 agent，验证后逐步放量）。

4. **降级（Degrade）**：降低 agent 权重或限制其请求速率。降级是连续的（`weight *= factor`，factor ∈ (0, 1)），区别于摘流的二值切换。降级可附加限制：缩短超时、限制并发、禁用某些 routing_key。降级可逆——agent 恢复后逐步取消限制。

5. **终止（Terminate）**：安全终止严重故障的 agent。终止是比摘流更强的动作：摘流仅置权重为 0 但 agent 仍注册可被显式调用；终止则标记 `disabled=True`，调度路径跳过该 agent，并发布 `agent_terminated` 事件。终止带审计标记与原因记录，支持人工复核后重启。

6. **升级（Upgrade）**：将 agent 升级到新版本（灰度发布）。升级流程：注册新版本 agent → 切流量到新版本 → 验证新版本健康 → 下线旧版本。升级过程受监督者巡检保护，若新版本劣化（窗口失败率 > 0.15 或平均延迟 > 旧版本 1.5 倍）则自动回退。首版仅支持"全切"换流（rollout=1.0），加权灰度切量（10% → 50% → 100%）延后至路由层升级支持加权分流后实现。

### 1.3 用户场景

#### 1.3.1 场景一：长期空闲 agent 失活发现

**场景**：agent A 负责处理 `routing_key="rare_event"` 的低频任务，平均每小时仅 1 次调用。某次基础设施滚动重启后 agent A 的进程失活，但因任务低频，被动 `record_result()` 长时间未被调用，失活未被发现。

**监督者介入**：
- `Supervisor.patrol()` 每 60 秒巡检所有已注册 agent，发现 agent A 的健康探针超时（进程无响应）。
- 触发 `warn(agent_a, "process_unresponsive")`，发布 `supervisor.alert` 事件（level=warning）。
- 连续 3 次巡检仍失活，触发 `terminate(agent_a, "process_unresponsive_after_3_patrols")`，标记 disabled，发布 `agent_terminated` 事件。
- 若配置了备用 agent B，进一步触发 `replace(agent_a, agent_b, "rare_event")`，路由注册表更新。

#### 1.3.2 场景二：延迟劣化预警与渐进降级

**场景**：agent B 处理 `routing_key="codegen"` 的高频任务，某时段其 p99 延迟从 5s 上升至 18s（未达 30s 超时阈值，故 `timeout_rate` 不变，`failure_rate` 也未上升），被动检测器无任何反应。

**监督者介入**：
- `Supervisor.patrol()` 巡检发现 agent B 的 `avg_latency` 超过预警阈值 15s。
- 触发 `warn(agent_b, "latency_degradation")`，发布预警事件（level=warning），Dashboard 实时显示。
- 延迟持续上升超过 25s，触发 `degrade(agent_b, factor=0.5, reason="latency_degradation")`，权重减半，附加并发限制从 10 降至 5。
- agent B 延迟恢复至 8s，巡检发现后逐步取消降级（权重恢复、并发限制放宽）。

#### 1.3.3 场景三：灰度升级与自动回退

**场景**：agent C 当前版本 v1.2，需升级到 v1.3 以修复 bug。直接全量切换风险高，理想情况下需灰度发布。

**监督者介入**：
- 运维通过 `POST /api/supervisor/action` 发起 `upgrade(agent_c, target_version="v1.3")`。
- 监督者注册 v1.3 版本 agent C'，将路由全切到 C'（首版仅支持全切，加权灰度切量延后至路由层升级后）。
- `patrol()` 加强对 C' 的巡检频率（从 60s 缩短至 15s），持续观察其失败率与延迟。
- C' 健康达标持续 5 分钟，监督者下线 v1.2。
- 若中途 C' 窗口失败率 > 0.15 或平均延迟 > v1.2 的 1.5 倍，监督者自动回退：流量全切回 v1.2，终止 C'，发布 `agent_upgrade.rolled_back` 事件。

#### 1.3.4 场景四：辩论僵局裁决（与辩论型联动）

**场景**：对抗辩论型（`design-debate-agent.md`）中，3 轮辩论未达共识阈值（加权共识度 < 0.70），需监督者裁决。

**监督者介入**：
- `DebateOrchestrator` 调用 `Supervisor.adjudicate(debate_id, rounds)`。
- 监督者取末轮各 agent 结论，以各 agent 历史可信度（成功率 / 历史裁决正确率）为权重，加权采纳置信度最高且历史可信度最高的一方结论。
- 对本轮表现显著低于历史均值的 agent，触发 `degrade()` 或 `terminate()`。
- 返回 `Verdict(consensus=False, low_confidence=True, adjudication_reason=...)`，标记建议人工复核。

#### 1.3.5 场景五：执行引擎前置检查阻断

**场景**：`Engine.run()` 即将执行一个含 agent D 的 workflow step，但 agent D 已被监督者标记为 `disabled`。

**监督者介入**：
- `Engine._execute_step()` 在派发前调用 `Supervisor.check_before_dispatch(agent_d)`。
- 监督者返回 `DispatchDecision(allow=False, reason="agent_disabled", fallback="agent_d_backup")`。
- 引擎跳过 agent D，改用 fallback agent（若配置），或返回 `StepStatus.SKIPPED` 并附原因。
- 避免向已知不可用的 agent 派发任务，节省超时等待时间。

---

## 第2章 设计方案

### 2.1 架构结构图

#### 2.1.1 监督者主循环

监督者型架构以 `Supervisor` 为核心，后台巡检循环定期主动扫描所有 agent 健康，基于规则评估触发预警或管控动作，动作执行后复检形成闭环。

图：监督者型 Multi-Agent 架构流程图

```
                          ┌──────────────────────────────────────────────────────────────────────────────────────────────────────────┐
                          │  Patrol Loop（巡检主循环，后台 asyncio.Task）                                                          │
                          │  每 patrol_interval_s 秒触发一次                                                                       │
                          └──────────────────────────────────────────────────────────────────────────────────────────────────────────┘
                                                            │
                                                            ▼
                          ┌──────────────────────────────────────────────────────────────────────────────────────────────────────────┐
                          │  HealthChecker.check_all() / check_sample()                                                            │
                          │  - 全量巡检：扫描所有已注册 agent                                                                        │
                          │  - 抽样巡检：按优先级/近期异常度抽样                                                                      │
                          │  - 对每个 agent 执行健康探针（ping / metrics / resource）                                                │
                          └──────────────────────────────────────────────────────────────────────────────────────────────────────────┘
                                                            │
                                                            ▼
                          ┌──────────────────────────────────────────────────────────────────────────────────────────────────────────┐
                          │  RuleEngine.evaluate(agent_health)                                                                     │
                          │  对每个 agent 的健康快照逐条评估 SupervisorRule                                                          │
                          │  - 规则匹配：阈值条件 → 触发动作                                                                         │
                          │  - 规则优先级：critical > error > warning > info                                                         │
                          │  - 抑制窗口：同一 agent 同一规则在 cooldown 内不重复触发                                                 │
                          └──────────────────────────────────────────────────────────────────────────────────────────────────────────┘
                                                            │
                                  ┌─────────────────────────┼─────────────────────────┐
                                  ▼                         ▼                         ▼
                ┌──────────────────────────┐  ┌──────────────────────────┐  ┌──────────────────────────┐
                │  预警（Alert）            │  │  管控动作（Action）       │  │  无动作（Pass）          │
                │  - EventBus.publish(     │  │  - Replace 替换           │  │  - 健康正常              │
                │    "supervisor.alert")   │  │  - Degrade 降级           │  │  - 仅更新 metrics        │
                │  - 分级推送              │  │  - Terminate 终止         │  │                            │
                └──────────────────────────┘  │  - Upgrade 升级           │  └──────────────────────────┘
                                              └──────────────┬───────────┘
                                                             │
                                                             ▼
                          ┌──────────────────────────────────────────────────────────────────────────────────────────────────────────┐
                          │  复检（Re-check）                                                                                       │
                          │  动作执行后下一轮巡检重点复检受影响 agent，确认动作生效                                                │
                          │  - 替换后复检备用 agent 健康                                                                            │
                          │  - 降级后复检延迟是否改善                                                                                │
                          │  - 升级后复检新版本指标                                                                                  │
                          └──────────────────────────────────────────────────────────────────────────────────────────────────────────┘
                                                            │
                                                            ▼
                          ┌──────────────────────────────────────────────────────────────────────────────────────────────────────────┐
                          │  被动检测保留（FailurePatternDetector 继承）                                                            │
                          │  record_result() 仍由调度路径调用，与主动巡检互补                                                        │
                          │  - 主动巡检发现"无任务但异常"的 agent                                                                    │
                          │  - 被动检测跟踪"有任务且失败"的 agent                                                                    │
                          └──────────────────────────────────────────────────────────────────────────────────────────────────────────┘
```

#### 2.1.2 与现有系统嵌套关系

图：监督者与现有系统集成示意图

```
  ┌─────────────────────────────────────────────────────────────────────────────────────────────────┐
  │  Engine.run() / _execute_step()                                                                 │
  │  ┌─ check_before_dispatch(agent) ◀── 新增：派发前向监督者查询 agent 是否可用                    │
  │  ├─ step 执行                                                                                   │
  │  └─ check_after_dispatch(agent, result) ◀── 新增：派发后向监督者记录结果（复用 record_result）  │
  └─────────────────────────────────────────────────────────────────────────────────────────────────┘
                                       │ check_before/after
                                       ▼
  ┌─────────────────────────────────────────────────────────────────────────────────────────────────┐
  │  Supervisor（主动监督者，继承 FailurePatternDetector）                                           │
  │  ┌─ 被动层（保留）─────────────────────────┐  ┌─ 主动层（新增）─────────────────────────────┐  │
  │  │  record_result()                        │  │  patrol()          巡检主循环                │  │
  │  │  get_weight() / get_stats()             │  │  warn()            预警推送                  │  │
  │  │  drain / recovery（滑动窗口）           │  │  replace/degrade/terminate/upgrade 管控动作 │  │
  │  │  check_before_dispatch() / after()      │  │  adjudicate()      辩论裁决（被辩论型调用） │  │
  │  └────────────────────────────────────────┘  └─────────────────────────────────────────────┘  │
  └─────────────────────────────────────────────────────────────────────────────────────────────────┘
       │                                    │                              │
       ▼                                    ▼                              ▼
  ┌──────────────┐          ┌──────────────────────────┐    ┌──────────────────────────┐
  │  scheduling  │          │  EventBus                │    │  EvolutionLoop           │
  │  .py 路由    │          │  .publish(               │    │  监督者可触发演化        │
  │  weight 查询 │          │    "supervisor.alert")   │    │  (如批量劣化触发自愈)    │
  └──────────────┘          │  .publish(               │    └──────────────────────────┘
                            │    "agent_replaced")     │
                            └──────────────────────────┘

  ┌─────────────────────────────────────────────────────────────────────────────────────────────────┐
  │  LoopExecutor._execute_with_retry()                                                             │
  │  原：固定 iterative_max_attempts 重试                                                            │
  │  新增：向监督者查询动态重试策略（基于 agent 当前健康决定重试次数 / 是否切换 fallback）           │
  └─────────────────────────────────────────────────────────────────────────────────────────────────┘
```

### 2.2 核心类设计

#### 2.2.1 数据模型（Pydantic）

```
代码示例：监督者核心数据模型（Python）

from __future__ import annotations
from enum import Enum
from pydantic import BaseModel, Field
from typing import Any


class SupervisorAction(str, Enum):
    """监督者可执行的管控动作。"""
    PATROL = "patrol"        # 巡检：主动扫描健康
    ALERT = "alert"          # 预警：推送预警事件
    REPLACE = "replace"      # 替换：将 agent 路由切到备用 agent
    DEGRADE = "degrade"      # 降级：降低权重 / 限制并发 / 缩短超时
    TERMINATE = "terminate"  # 终止：标记不可用 + 审计
    UPGRADE = "upgrade"      # 升级：灰度切到新版本
    NONE = "none"            # 无动作（健康正常）


class AlertLevel(str, Enum):
    """预警级别。"""
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"
    CRITICAL = "critical"


class AgentOperationalStatus(str, Enum):
    """agent 运行状态（扩展自原 normal/drained/recovering）。"""
    NORMAL = "normal"            # 正常
    DEGRADED = "degraded"        # 已降级（权重被限制但未摘流）
    DRAINED = "drained"          # 已摘流（weight=0，被动检测触发）
    RECOVERING = "recovering"    # 灰度回切中
    REPLACED = "replaced"        # 已被替换（路由切走，保留审计）
    TERMINATED = "terminated"    # 已终止（disabled=True）
    UPGRADING = "upgrading"      # 升级中（灰度切量）


class HealthProbe(BaseModel):
    """单次健康探针结果。"""
    agent_id: str
    reachable: bool                       # 进程是否可达
    latency_ms: float = 0.0               # 探针响应延迟
    failure_rate: float = 0.0             # 当前窗口失败率
    avg_latency: float = 0.0              # 当前窗口平均延迟
    timeout_rate: float = 0.0             # 当前窗口超时率
    breaker_open: bool = False            # 熔断器是否开启
    resource_usage: dict[str, Any] = Field(default_factory=dict)  # CPU/内存/连接池等
    probed_at: float                      # 探针时间戳


class SupervisorRule(BaseModel):
    """监督规则：阈值条件 → 触发动作。

    一条规则定义：当某 agent 的 HealthProbe 满足 condition 时，
    触发 action，附带 action_params。同 agent 同规则在 cooldown_s
    内不重复触发（抑制风暴）。
    """
    rule_id: str
    name: str
    description: str = ""
    action: SupervisorAction
    alert_level: AlertLevel = AlertLevel.WARNING
    condition: dict[str, Any]             # 阈值条件（见 2.2.5）
    action_params: dict[str, Any] = Field(default_factory=dict)
    cooldown_s: float = 60.0              # 同 agent 同规则抑制窗口
    priority: int = 0                     # 高优先级先评估
    enabled: bool = True


class DispatchDecision(BaseModel):
    """派发前检查结果：引擎据此次策决定是否派发。"""
    allow: bool
    reason: str = ""
    fallback_agent: str | None = None     # 不允许时建议的备用 agent
    degraded: bool = False                # 是否以降级模式派发


class ActionRecord(BaseModel):
    """管控动作审计记录。"""
    action_id: str
    action: SupervisorAction
    agent_id: str
    reason: str
    params: dict[str, Any] = Field(default_factory=dict)
    triggered_by: str = "patrol"          # patrol / debate / manual / engine
    created_at: float
    reverted_at: float | None = None      # 若动作被回退
```

#### 2.2.2 Supervisor 主类

`Supervisor` 继承 `FailurePatternDetector`，保留全部被动检测 API（`record_result()` / `get_weight()` / `get_stats()` / drain / recovery），外部既有调用方（`scheduling.py` 路由、`dispatch_core.py` 权重查询）零改动。新增主动巡检循环与六大管控动作。

```
代码示例：Supervisor 接口设计（Python）

class Supervisor(FailurePatternDetector):
    """主动监督者：在 FailurePatternDetector 基础上新增巡检/预警/管控动作。

    架构层次
    --------
    - 被动层（继承）：record_result / get_weight / drain / recovery —— 保留不变
    - 主动层（新增）：patrol 循环 / warn 预警 / replace / degrade / terminate / upgrade
    - 裁决层（新增）：adjudicate —— 供辩论型僵局兜底调用

    Parameters
    ----------
    health_checker : HealthChecker
        健康检查器，执行探针并返回 HealthProbe。
    rules : list[SupervisorRule]
        监督规则集合，按优先级排序后评估。
    patrol_interval_s : float
        巡检周期（默认 60 秒）。
    patrol_strategy : str
        巡检策略："full"（全量）/ "sample"（抽样）/ "adaptive"（自适应）。
    patrol_timeout_s : float
        单次巡检超时（默认 10 秒）。
    event_bus : EventBus | None
        事件总线，用于预警发布（继承自父类，复用 _publish_event）。
    """

    def __init__(
        self,
        *,
        health_checker: HealthChecker | None = None,
        rules: list[SupervisorRule] | None = None,
        patrol_interval_s: float = 60.0,
        patrol_strategy: str = "full",
        patrol_timeout_s: float = 10.0,
        **kwargs: Any,                   # 透传父类参数（window_size 等）
    ) -> None: ...

    # ── 主动巡检 ────────────────────────────────────────────────

    async def patrol(self) -> list[HealthProbe]:
        """主动巡检：扫描所有已注册 agent 的当前健康快照。

        流程：
          1. 按 patrol_strategy 选定待巡检 agent 集合。
          2. 并发执行 HealthChecker.check(agent_id)，受 patrol_timeout_s 约束。
          3. 对每个 HealthProbe 逐条评估 SupervisorRule。
          4. 命中规则则触发对应动作（warn / replace / degrade / terminate / upgrade）。
          5. 返回本次巡检的所有 HealthProbe（供 API 查询与复检）。

        与被动 record_result 互补——即使某 agent 长期无任务，
        patrol 也能发现其处于异常状态。
        """
        ...

    async def start_patrol_loop(self) -> None:
        """启动后台巡检循环（asyncio.Task）。

        每 patrol_interval_s 秒触发一次 patrol()。循环异常不退出
        （捕获并记录，下轮继续）。

        启动时机与生命周期（补充 [R-1]）：
          巡检循环需在 MAOP 主事件循环已启动的前提下运行，否则
          `asyncio.create_task()` 会抛 `RuntimeError: no running event loop`。
          本设计采用以下两种启动方式之一（由配置 `patrol_loop_start_mode`
          决定）：
          - **懒启动（lazy，默认）**：在 `Engine.run()` 首次调用时，若
            监督者已配置且巡检循环未启动，则通过
            `asyncio.create_task(supervisor.start_patrol_loop())` 懒启动。
            优点是无需修改应用 startup 钩子，缺点是首个 workflow 触发巡检
            有微小延迟。
          - **startup 启动**：在 FastAPI 应用 startup 事件
            （`@app.on_event("startup")` 或 lifespan）中显式调用
            `await supervisor.start_patrol_loop()`。优点是巡检与业务解耦，
            缺点是需修改应用启动代码。
          巡检循环在应用 shutdown 事件中调用 `stop_patrol_loop()` 优雅停止，
          避免任务泄漏。同步上下文（如单元测试）中不启动巡检循环，
          由测试显式调用 `patrol()` 驱动。
        """
        ...

    async def stop_patrol_loop(self) -> None:
        """停止巡检循环（优雅关闭时调用）。"""
        ...

    # ── 预警 ────────────────────────────────────────────────────

    async def warn(
        self,
        agent_id: str,
        reason: str,
        *,
        level: AlertLevel = AlertLevel.WARNING,
        extra: dict[str, Any] | None = None,
    ) -> None:
        """推送预警事件到 EventBus。

        事件 topic="supervisor.alert"，payload 含 agent_id / reason /
        level / extra / source="supervisor"。level=critical 时同步
        确保事件落库（不走 fire-and-forget）。
        """
        ...

    # ── 管控动作 ────────────────────────────────────────────────

    async def replace(
        self,
        agent_id: str,
        replacement: str,
        reason: str,
        *,
        rollout: float = 1.0,            # 灰度比例，1.0=立即全切
        routing_key: str = "",
    ) -> ActionRecord:
        """将 agent_id 的路由替换为 replacement。

        流程：
          1. 验证 replacement 已注册且健康。
          2. 更新路由注册表：调用 `py/maop/core/routing/routing_decision.py`
             中的路由更新 API，将 `routing_key → agent_id` 映射改为
             `routing_key → replacement`（按 rollout 灰度，首版仅支持
             rollout=1.0 全切，见 [F-3]）。
          3. 原 agent 标记 operational_status=REPLACED，保留审计记录。
          4. 发布 agent_replaced 事件。
          5. 下一轮巡检重点复检 replacement。

        路由注册表定位（补充 [F-2]）：
          路由映射存储在 `py/maop/core/routing/routing_decision.py` 中
          （`dispatch_core.py:33` 导入 `RoutingDecisionRecord` 与
          `AgentResolver`）。replace 通过该模块暴露的 API 更新
          `routing_key → agent` 映射，不直接修改 `dispatch_core.py` 内部
          状态。具体调用 API 在实施阶段根据 `routing_decision.py` 的实际
          接口确定（若现有模块无更新接口，需在该模块新增
          `update_routing_mapping(routing_key, new_agent)` 方法）。
        """
        ...

    async def degrade(
        self,
        agent_id: str,
        factor: float,
        reason: str,
        *,
        max_concurrency: int | None = None,
        timeout_s: float | None = None,
    ) -> ActionRecord:
        """降低 agent 权重并附加限制。

        weight *= factor（factor ∈ (0, 1)），区别于 drain 的二值切换。
        可选附加：max_concurrency 限制并发、timeout_s 缩短超时。
        标记 operational_status=DEGRADED。降级可逆（recover 后逐步取消）。
        """
        ...

    async def terminate(self, agent_id: str, reason: str) -> ActionRecord:
        """安全终止 agent。

        比 drain 更强：标记 disabled=True，调度路径跳过该 agent，
        发布 agent_terminated 事件（level=critical）。带审计标记，
        支持人工复核后通过 upgrade() 或显式重启恢复。

        边界检查（补充 [R-2]）：
          terminate 前检查该 agent 是否为某 routing_key 的唯一可用 agent
          （通过 `routing_decision.py` 查询 routing_key → agent 映射，
          排除已 disabled / drained 的 agent）。若是，则：
          - 若配置了 fallback agent，提示先执行 replace() 切换路由再
            terminate。
          - 若未配置 fallback，拒绝 terminate 并抛
            `TerminateRefusedError(agent_id, routing_key)`，附原因
            "agent is the only available agent for routing_key=X,
            configure fallback or replace first"。
          此检查避免 terminate 后 routing_key 无可用 agent 导致请求全部
          失败。手动通过 API 强制 terminate（`force=True` 参数）可跳过此
          检查，但审计记录标记 `force_bypass_safety=True` 供事后复核。
        """
        ...

    async def upgrade(
        self,
        agent_id: str,
        target_version: str,
        reason: str,
        *,
        rollout_steps: list[float] | None = None,  # 默认 [1.0]（首版仅全切）
    ) -> ActionRecord:
        """将 agent 灰度升级到 target_version。

        流程：
          1. 注册新版本 agent（同 agent_id，version=target_version）。
          2. 按 rollout_steps 逐步切流量。
          3. 每步加强巡检复检新版本健康。
          4. 新版本劣化则自动回退（全切回旧版本，终止新版本）。
          5. 全量切流成功后下线旧版本。

        灰度切量方案（补充 [F-3]）：
          现有 `RoutingDecisionRecord` 与 `AgentResolver` 是
          `routing_key → agent` 的单值映射，不支持"10% 到 v1.3 + 90% 到 v1.2"
          的加权分流。**首版仅支持"全切"（rollout=1.0）**：upgrade 一次性
          将路由从旧版本切到新版本，rollout_steps 参数首版固定为 [1.0]，
          传入 [0.1, 0.5, 1.0] 等灰度阶梯将被忽略并记录 warning 日志。
          加权灰度切量延后至路由层升级支持加权分流后实现。

        回退触发条件（补充 [R-3]）：
          "新版本劣化"的量化判定标准（任一满足即触发自动回退）：
          - 新版本窗口失败率 > 0.15（`failure_rate > 0.15`）。
          - 新版本平均延迟 > 旧版本平均延迟 × 1.5
            （`avg_latency_new > avg_latency_old * 1.5`）。
          - 新版本连续 2 次巡检 `reachable=False`。
          回退时将路由全切回旧版本，终止新版本 agent，发布
          `agent_upgrade.rolled_back` 事件（附回退原因与劣化指标快照）。
        """
        ...

    # ── 派发前/后检查（供 Engine 集成）──────────────────────────

    def check_before_dispatch(self, agent_id: str) -> DispatchDecision:
        """派发前检查：返回是否允许派发及建议 fallback。

        - agent 标记 disabled（terminated）→ allow=False，附 fallback。
        - agent 权重为 0（drained）→ allow=False，附 fallback。
        - agent 降级中 → allow=True, degraded=True。
        - agent 正常 → allow=True。
        """
        ...

    def check_after_dispatch(
        self,
        agent_id: str,
        success: bool,
        latency: float = 0.0,
    ) -> None:
        """派发后记录结果（复用父类 record_result）。

        保留为显式方法以便 Engine 调用方语义清晰，
        内部直接转发 self.record_result(agent_id, success, latency)。
        """
        ...

    # ── 辩论裁决（供辩论型集成）──────────────────────────────────

    async def adjudicate(
        self,
        debate_id: str,
        rounds: list[Any],              # list[DebateRound]，惰性导入避免循环依赖
    ) -> Any:                            # Verdict
        """辩论僵局裁决（详见 design-debate-agent.md 2.2.5）。

        策略：
          1. 取最后一轮各 agent 结论。
          2. 以各 agent 历史可信度为权重加权采纳一方结论。
          3. 对本轮表现显著低于历史均值的 agent 触发 degrade/terminate。
          4. 返回 Verdict(consensus=False, low_confidence=True)。
        """
        ...

    # ── 动态重试策略（供 LoopExecutor 集成）─────────────────────

    def get_retry_strategy(self, agent_id: str) -> dict[str, Any]:
        """返回针对 agent 的动态重试策略。

        返回：
          {
            "max_attempts": int,         # 基于当前健康动态调整
            "backoff_ms": int,           # 退避间隔
            "skip_agent": bool,          # 是否跳过该 agent 直接走 fallback
            "fallback_agent": str|None,  # 建议的 fallback
          }

        - agent 降级中 → max_attempts 减少（避免重试放大劣化）。
        - agent terminated → skip_agent=True。
        - agent 正常 → 返回默认配置（与现有 lc.iterative_max_attempts 一致）。
        """
        ...

    # ── 状态查询 ────────────────────────────────────────────────

    def get_supervisor_status(self) -> dict[str, Any]:
        """返回监督者全景状态（供 Dashboard /api/supervisor/status）。

        含：所有 agent 健康快照 + operational_status + 最近巡检时间 +
            pending_alerts + 最近动作记录 + 巡检循环是否运行中。
        """
        ...

    def get_actions(
        self,
        agent_id: str | None = None,
        limit: int = 50,
    ) -> list[ActionRecord]:
        """查询管控动作历史（可按 agent 过滤）。"""
        ...
```

#### 2.2.3 HealthChecker

`HealthChecker` 负责对单个 agent 执行健康探针，返回 `HealthProbe`。探针类型可配置，默认组合 ping + metrics + resource。

```
代码示例：HealthChecker 接口设计（Python）

class HealthChecker:
    """健康检查器：对 agent 执行探针并返回 HealthProbe。

    Parameters
    ----------
    probe_timeout_s : float
        单次探针超时（默认 5 秒）。
    probe_types : list[str]
        探针类型组合，默认 ["ping", "metrics", "resource"]。
        - ping：轻量可达性检查（进程是否响应）。
        - metrics：拉取 agent 暴露的指标（失败率/延迟/超时率）。
        - resource：拉取资源占用（CPU/内存/连接池）。
    registry : AgentRegistry
        已注册 agent 清单（用于遍历巡检目标）。
    """

    def __init__(
        self,
        *,
        probe_timeout_s: float = 5.0,
        probe_types: list[str] | None = None,
        registry: Any = None,
    ) -> None: ...

    async def check(self, agent_id: str) -> HealthProbe:
        """对单个 agent 执行健康探针。

        并发执行各 probe_type，任一超时则对应字段置默认值，
        reachable 由 ping 探针决定。整体受 probe_timeout_s 约束。
        """
        ...

    async def check_all(self) -> list[HealthProbe]:
        """对所有已注册 agent 并发巡检（受全局并发限制）。"""
        ...

    async def check_sample(
        self,
        sample_size: int,
        priority_weight: bool = True,
    ) -> list[HealthProbe]:
        """抽样巡检：按优先级/近期异常度选取 sample_size 个 agent。

        近期异常度高的 agent 被选概率更高（优先复检）。
        """
        ...

    async def check_adaptive(self) -> list[HealthProbe]:
        """自适应巡检：根据系统负载动态调整巡检集合。

        - 系统空闲：全量巡检。
        - 系统繁忙：仅巡检近期异常 agent + 高优先级 agent。
        """
        ...
```

**探针实现方案（补充 [F-1]）**：

当前 agent 未暴露独立的健康探针 HTTP 端点，`Dispatcher.dispatch()` 通过 LLM driver 执行任务。本设计不要求 agent 新增 HTTP 健康端点，而是复用现有基础设施实现三类探针：

表：探针实现方案对照表

| 探针类型 | 实现方案 | 数据来源 | 字段映射 |
|----------|----------|----------|----------|
| ping | dispatch 一个轻量任务 `task='__health_ping__'` 并测量往返延迟 | `Dispatcher.dispatch(agent_id, task='__health_ping__')` | `reachable`（成功/失败）、`latency_ms`（往返耗时） |
| metrics | 读取 `FailurePatternDetector.get_stats(agent_id)` 获取窗口统计 | 父类 `get_stats()` 返回的 `failure_rate` / `avg_latency` / `timeout_rate` | `failure_rate`、`avg_latency`、`timeout_rate`、`breaker_open`（由 weight==0 或 status 判定） |
| resource | 读取 agent 暴露的 Prometheus 指标 | `MetricsCollector` 中 `maop_agent_*` 系列 gauge | `resource_usage`（CPU/内存/连接池，若 agent 未暴露则置空 dict） |

**ping 探针约束**：

- `__health_ping__` 是约定保留任务名，agent driver 收到该 task 应立即返回空结果（不执行业务逻辑），仅用于可达性检测。
- ping 探针超时由 `probe_timeout_s`（默认 5s）约束，超时则 `reachable=False`。
- ping 探针复用 `Dispatcher.dispatch()` 路径，因此熔断器、预算检查、guardrail 全部生效，不会绕过安全防线。

**metrics 探针约束**：

- 直接调用父类 `get_stats(agent_id)`，无 I/O 开销，仅读内存滑动窗口。
- 若 agent 从未被 `record_result()` 记录过（新注册），`get_stats()` 返回默认零值，`failure_rate=0.0` / `avg_latency=0.0`。

**resource 探针约束**：

- 通过 `MetricsCollector` 读取 agent 暴露的 Prometheus 指标（如 `maop_agent_avg_latency_seconds`）。
- 若 agent 未暴露资源指标（如 CPU/内存），`resource_usage` 置空 dict，不触发 `rule.resource.high` 规则。
- resource 探针不依赖 agent 主动上报，而是从监督者侧的 `MetricsCollector` 拉取，避免增加 agent 负担。

#### 2.2.4 SupervisorAction 枚举

`SupervisorAction` 已在 2.2.1 定义。其语义对照如下。

表：SupervisorAction 动作语义对照表

| 动作 | 触发方 | 对 agent 的影响 | 可逆性 | 审计级别 |
|------|--------|------------------|--------|----------|
| PATROL | 巡检循环 / 手动 | 无副作用，仅采集健康快照 | N/A | info |
| ALERT | 规则引擎 / 手动 | 无副作用，仅推送事件 | N/A | info |
| REPLACE | 规则引擎 / 辩论裁决 / 手动 | 路由切走，标记 REPLACED | 可逆（切回） | warning |
| DEGRADE | 规则引擎 / 辩论裁决 / 手动 | 权重降低，附加限制 | 可逆（恢复） | warning |
| TERMINATE | 规则引擎 / 辩论裁决 / 手动 | 标记 disabled，调度跳过 | 需人工复核 | critical |
| UPGRADE | 手动 / 演化循环 | 灰度切新版本 | 可自动回退 | warning |
| NONE | 规则引擎 | 无动作 | N/A | N/A |

#### 2.2.5 SupervisorRule

`SupervisorRule` 已在 2.2.1 定义。`condition` 字段是阈值条件的声明式描述，由 `RuleEngine` 评估。条件结构如下。

```
代码示例：SupervisorRule 条件结构（Python）

# condition 是一个 dict，支持以下键（任一满足即触发，多键为 OR 语义；
# 若需 AND 语义，使用 "all" 键嵌套）。

condition_example_failure_rate = {
    "failure_rate_gt": 0.20,        # 失败率 > 20%（未达 drain 阈值 30%，先预警）
}

condition_example_latency = {
    "avg_latency_gt": 15.0,         # 平均延迟 > 15s
}

condition_example_breaker = {
    "breaker_open": True,           # 熔断器开启
}

condition_example_unreachable = {
    "reachable": False,             # 进程不可达
}

condition_example_combined = {
    "all": [                        # AND 语义：同时满足
        {"failure_rate_gt": 0.10},
        {"avg_latency_gt": 10.0},
    ],
}

condition_example_resource = {
    "resource_usage_gt": {          # 资源占用超阈
        "cpu_percent": 90.0,
        "memory_percent": 85.0,
    },
}
```

**内置规则集**：监督者开箱即用一组默认规则，覆盖常见劣化场景。

表：内置默认规则集参数说明表

| rule_id | 触发条件 | 动作 | 级别 | cooldown |
|---------|----------|------|------|----------|
| `rule.failure_rate.warning` | `failure_rate > 0.15` | ALERT | warning | 60s |
| `rule.failure_rate.drain` | `failure_rate > 0.30` | 继承父类 drain | error | 0s |
| `rule.latency.warning` | `avg_latency > 15.0` | ALERT | warning | 60s |
| `rule.latency.degrade` | `avg_latency > 25.0` | DEGRADE(factor=0.5) | error | 120s |
| `rule.breaker.open` | `breaker_open == True` | ALERT | error | 30s |
| `rule.unreachable.terminate` | `reachable == False` 连续 3 次巡检 | TERMINATE | critical | 300s |
| `rule.timeout.high` | `timeout_rate > 0.20` | DEGRADE(factor=0.7) | warning | 90s |
| `rule.resource.high` | `cpu > 90% or memory > 85%` | ALERT | warning | 60s |

### 2.3 与现有系统集成方案

#### 2.3.1 engine.py 集成

在 `Engine._execute_step()` 中插入监督者检查点：派发前查询 `check_before_dispatch()`，派发后调用 `check_after_dispatch()`。

```
代码示例：Engine._execute_step 集成监督者（Python）

class Engine:
    # ... 现有代码不变 ...

    async def _execute_step(
        self,
        step: WorkflowStep,
        context: dict[str, Any],
        results: dict[str, StepResult],
        workdir: str,
        trace_id: str,
    ) -> StepResult:
        start = time.monotonic()

        # 新增：派发前检查（仅对 AGENT 类型 step）
        if step.type == StepType.AGENT and step.agent:
            supervisor = self._get_supervisor()  # 懒加载，可能为 None
            if supervisor is not None:
                decision = supervisor.check_before_dispatch(step.agent)
                if not decision.allow:
                    if decision.fallback_agent:
                        # 改用 fallback agent，记录原因
                        logger.info(
                            "[engine] agent %s not allowed (%s), fallback to %s",
                            step.agent, decision.reason, decision.fallback_agent,
                        )
                        step = step.model_copy(update={"agent": decision.fallback_agent})
                    else:
                        return StepResult(
                            id=step.id, status=StepStatus.SKIPPED,
                            error=f"Supervisor blocked dispatch: {decision.reason}",
                            agent=step.agent,
                        )

        # ... 现有执行逻辑 ...

        # 新增：派发后记录结果（仅对 AGENT 类型且执行完成）
        if step.type == StepType.AGENT and step.agent and supervisor is not None:
            supervisor.check_after_dispatch(
                step.agent,
                success=(sr.status == StepStatus.SUCCESS),
                latency=(time.monotonic() - start),
            )

        return sr
```

**集成约束**：
- `check_before_dispatch()` 是同步方法（仅读内存状态，不阻塞），不影响引擎吞吐。
- 监督者未配置（`supervisor is None`）时行为完全退化为现状，保证向后兼容。
- fallback 切换不绕过熔断/预算/guardrail——切换后的 agent 仍走完整 `_dispatch_impl()` 路径。

#### 2.3.2 loop_executor.py 集成

将 `ExecuteMixin._execute_with_retry()` 中固定的 `iterative_max_attempts` 替换为向监督者查询的动态策略。

```
代码示例：_execute_with_retry 动态重试策略（Python）

class ExecuteMixin:
    # ... 现有代码不变 ...

    async def _execute_with_retry(
        self,
        task: str,
        fallback_chain: list[str],
        routing_key: str,
        workdir: str,
        timeout: int,
        retry: bool,
        trace_id: str,
    ) -> MaopResult | None:
        lc = self._loop_config
        supervisor = self._get_supervisor()  # 懒加载，可能为 None
        agents = fallback_chain
        result = None

        for attempt, agent in enumerate(agents):
            # 新增：向监督者查询动态重试策略
            if supervisor is not None:
                strategy = supervisor.get_retry_strategy(agent)
                if strategy.get("skip_agent"):
                    logger.info("Supervisor skip agent %s: %s", agent, strategy)
                    continue
                max_iterations = strategy.get("max_iterations", lc.iterative_max_attempts)
                backoff_ms = strategy.get("backoff_ms", lc.iterative_backoff_ms)
            else:
                max_iterations = lc.iterative_max_attempts if retry else 1
                backoff_ms = lc.iterative_backoff_ms

            if attempt > 0:
                await asyncio.sleep(lc.retry_backoff_ms / 1000)

            for iteration in range(max_iterations):
                if iteration > 0:
                    await asyncio.sleep(backoff_ms / 1000)
                # ... 现有 dispatch 调用不变 ...
```

**集成约束**：
- 监督者未配置时 `get_retry_strategy()` 不被调用，`max_iterations` 退化为现有逻辑。
- 动态策略仅调整重试次数与退避，不改变 fallback chain 遍历顺序（监督者若需切 agent 通过 `check_before_dispatch` 在引擎层处理）。

#### 2.3.3 event_bus.py 集成

监督者通过 `EventBus.publish(Event)` 发布预警与动作事件。新增事件 topic 如下。

表：监督者新增事件 topic 说明表

| 事件 topic | 触发时机 | payload 关键字段 | level |
|------------|----------|------------------|-------|
| `supervisor.alert` | 规则触发预警 | agent_id, reason, level, extra | info ~ critical |
| `supervisor.patrol.completed` | 每轮巡检完成 | patrol_id, agents_checked, issues_found | info |
| `agent_replaced` | replace() 执行 | agent_id, replacement, rollout, routing_key | warning |
| `agent_degraded` | degrade() 执行 | agent_id, factor, max_concurrency, timeout_s | warning |
| `agent_terminated` | terminate() 执行 | agent_id, reason | critical |
| `agent_upgrade.started` | upgrade() 开始 | agent_id, target_version, rollout_steps | info |
| `agent_upgrade.rolled_back` | upgrade 自动回退 | agent_id, target_version, reason | warning |
| `agent_upgrade.completed` | upgrade 全量完成 | agent_id, target_version | info |

**与现有事件的兼容**：父类的 `agent_drained` / `agent_recovering` / `agent_recovered` 事件保留不变，监督者新增事件使用不同 topic 前缀（`supervisor.*` / `agent_replaced` 等），不冲突。

**EventBus API 统一（修正 [C-1]）**：

当前代码存在两个 EventBus 实现，API 不一致：

- `maop.enterprise.notification.event_bus.EventBus`：提供 `emit(topic, payload)` 方法（`failure_detector.py` 当前使用）。
- `maop.core.reliability.event_bus.EventBus`：提供 `publish(Event)` / `publish_sync(Event)` 方法（`engine.py` / 黑板架构使用），**无 `emit()` 方法**。

监督者继承 `FailurePatternDetector`，若注入 `core.reliability.event_bus.EventBus` 实例则父类 `_publish_event` 中的 `emit()` 调用会抛 `AttributeError`。本设计统一采用 `core.reliability.event_bus.EventBus` 的 `publish(Event)` API，具体修正：

1. **统一 API**：监督者及其父类一律使用 `core.reliability.event_bus.EventBus.publish(Event)` 发布事件，不再使用 `emit()`。
2. **`_publish_event` 方法修正**：将 `failure_detector.py:480` 的 `self._event_bus.emit(event_type, full_payload, tenant_id=self._tenant_id)` 修正为：

   ```
   代码示例：_publish_event 修正后实现（Python）

   from maop.core.reliability.event_bus import Event

   async def _publish_event(self, event_type: str, payload: dict, level: str = "info") -> None:
       if self._event_bus is None:
           return
       full_payload = {**payload, "level": level, "source": "failure_detector"}
       event = Event(topic=event_type, data=full_payload, source="failure_detector")
       try:
           import asyncio
           try:
               asyncio.get_running_loop()
               asyncio.ensure_future(self._event_bus.publish(event))
           except RuntimeError:
               await self._event_bus.publish(event)
       except Exception:
           logger.exception("Failed to publish event %s", event_type)
   ```

3. **TYPE_CHECKING 导入修正**：将 `failure_detector.py:49` 的 `from maop.enterprise.notification.event_bus import EventBus` 更新为 `from maop.core.reliability.event_bus import EventBus`，与 `engine.py:30` 的导入保持一致。
4. **`get_event_bus()` 来源**：监督者注入的 EventBus 实例统一来自 `maop.core.reliability.event_bus.get_event_bus()`，返回 `core` 版实现。

#### 2.3.4 evolution_loop.py 集成

监督者可在巡检发现批量劣化时触发演化循环（如多个 agent 同时异常，触发自愈建议生成）。

```
代码示例：监督者触发演化循环（Python）

class Supervisor:
    # ... 其他方法不变 ...

    async def _maybe_trigger_evolution(
        self,
        patrol_result: list[HealthProbe],
    ) -> None:
        """巡检后评估是否需触发演化循环。

        条件：本轮巡检发现劣化 agent 数量超过阈值（如 ≥ 3 个
        且 failure_rate > 0.2），则向 EvolutionLoop 提交一个
        "supervisor_triggered_heal" 建议，由演化循环的 HEAL 阶段处理。

        约束：触发频率受 cooldown 限制（默认 10 分钟一次），
        避免巡检与演化循环形成正反馈风暴。
        """
        ...
```

**集成约束**：监督者不直接调用 `EvolutionLoop.run_cycle()`（避免递归），而是通过 EventBus 发布 `supervisor.evolution.suggested` 事件，由演化循环的调度器订阅并决定是否触发。这保持了两者的解耦。

### 2.4 巡检调度设计

#### 2.4.1 巡检周期

表：巡检周期参数说明表

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `patrol_interval_s` | 60.0 | 巡检主循环周期。生产环境建议 30–60s，测试环境可缩至 5s |
| `patrol_timeout_s` | 10.0 | 单次巡检（单个 agent 探针）超时，超时则该 agent 标记探针失败 |
| `probe_timeout_s` | 5.0 | 单个探针类型（ping/metrics/resource）超时 |
| `patrol_concurrency` | 10 | 单轮巡检内并发探针数上限，避免巡检压垮下游 |

**周期选择依据**：
- 60s 周期平衡了发现延迟与巡检开销。最坏情况下 agent 失活到被发现 ≤ 60s + 探针超时 10s = 70s。
- 对升级中的 agent，巡检周期自动缩短至 `patrol_interval_s / 4`（15s），加强复检。
- 巡检循环异常不退出（捕获并记录，下轮继续），保证监督者常驻。

#### 2.4.2 巡检策略

三种巡检策略可选，通过 `patrol_strategy` 配置：

1. **全量巡检（full）**：每轮扫描所有已注册 agent。适用于 agent 数量 ≤ 50 的场景。发现延迟一致，开销与 agent 数量线性相关。

2. **抽样巡检（sample）**：每轮按 `sample_size` 抽样，近期异常度高的 agent 被选概率更高。适用于 agent 数量 > 50 的场景。配合 `sample_size` 与 `priority_weight` 参数。抽样策略下，每个 agent 保证在 `full_patrol_cycle`（默认 5 轮）内被巡检至少一次。

3. **自适应巡检（adaptive）**：根据系统负载动态调整。系统空闲时全量，系统繁忙时仅巡检近期异常 + 高优先级 agent。负载判定基于 `MetricsCollector` 的全局吞吐量与延迟。

表：巡检策略对照表

| 策略 | 发现延迟 | 巡检开销 | 适用规模 | 配置参数 |
|------|----------|----------|----------|----------|
| full | 一致（= patrol_interval_s） | O(N) × probe | N ≤ 50 | patrol_interval_s |
| sample | 一致（≤ full_patrol_cycle × patrol_interval_s） | O(sample_size) × probe | N > 50 | sample_size, full_patrol_cycle |
| adaptive | 动态（空闲快/繁忙慢） | 动态 | 任意 | load_threshold_high/low |

**首版实现范围（补充 [F-4]）**：

三种策略中 adaptive 需读取 `MetricsCollector` 全局吞吐量判定负载，sample 需维护异常度排序与抽样概率，实现复杂度较高。**首版仅实现 full 策略**，`patrol_strategy` 参数首版仅接受 `"full"`，传入 `"sample"` 或 `"adaptive"` 将记录 warning 日志并降级为 full。sample / adaptive 策略延后至 agent 规模超过 50 后按需实现。`HealthChecker.check_sample()` 与 `check_adaptive()` 方法签名保留（供未来实现），首版内部直接转发到 `check_all()`。

#### 2.4.3 巡检超时与容错

- **单探针超时**：`probe_timeout_s`（默认 5s），超时则该探针类型返回默认值，不影响其他探针。
- **单 agent 巡检超时**：`patrol_timeout_s`（默认 10s），超时则该 agent 的 `HealthProbe.reachable=False`，触发 `rule.unreachable.terminate` 评估。
- **整轮巡检超时**：无硬超时（受 `patrol_concurrency` 并发限制自然收敛），但每轮耗时记入 metrics，超阈值告警。
- **巡检循环异常**：`patrol()` 内部捕获所有异常并记录，不向上抛出，保证循环不退出。连续 N 轮巡检失败则发布 `supervisor.patrol.failing` 事件。

### 2.5 API 设计

遵循现有 dashboard 路由模式：FastAPI `APIRouter(prefix=...)` + `handle_api_errors` 装饰器，GET 端点免鉴权（同 `scheduling.py` 策略），写端点需管理员鉴权。

#### 2.5.1 Dashboard API

```
代码示例：监督者 API 路由（Python）

from fastapi import APIRouter
from pydantic import BaseModel

router = APIRouter(prefix="/api/supervisor", tags=["supervisor"])


class SupervisorActionRequest(BaseModel):
    """手动管控动作请求体。"""
    agent_id: str
    action: str                      # replace|degrade|terminate|upgrade
    params: dict[str, Any] = {}
    reason: str


@router.get("/status")
async def api_supervisor_status() -> dict[str, Any]:
    """返回监督者全景状态。

    响应体：
      {
        "agents": [ {agent_id, failure_rate, avg_latency, weight,
                     operational_status, last_probe_at}, ... ],
        "patrol": {running: bool, last_patrol_at, next_patrol_at,
                   patrol_interval_s, patrol_strategy},
        "pending_alerts": [ {agent_id, reason, level, at}, ... ],
        "recent_actions": [ {action_id, action, agent_id, reason, at}, ... ],
        "config": {window_size, failure_rate_threshold, ...}
      }
    """
    ...


@router.get("/rules")
async def api_supervisor_rules() -> list[dict[str, Any]]:
    """返回当前生效的监督规则集。"""
    ...


@router.post("/rules")
async def api_supervisor_rules_update(rules: list[dict[str, Any]]) -> dict[str, Any]:
    """更新监督规则集（管理员，热生效）。"""
    ...


@router.get("/actions")
async def api_supervisor_actions(
    agent_id: str | None = None,
    limit: int = 50,
) -> list[dict[str, Any]]:
    """查询管控动作历史（可按 agent 过滤）。"""
    ...


@router.post("/patrol")
async def api_supervisor_patrol() -> dict[str, Any]:
    """手动触发一次巡检（管理员）。返回本次巡检发现的劣化项。"""
    ...


@router.post("/action")
async def api_supervisor_action(body: SupervisorActionRequest) -> dict[str, Any]:
    """手动对 agent 执行管控动作（管理员）。

    请求体示例：
      {
        "agent_id": "agent_x",
        "action": "degrade",
        "params": {"factor": 0.5, "max_concurrency": 5},
        "reason": "手动降级：延迟劣化"
      }
    """
    ...
```

#### 2.5.2 端点清单

表：监督者 API 端点清单

| 方法 | 路径 | 鉴权 | 说明 |
|------|------|------|------|
| GET | `/api/supervisor/status` | 否 | 监督者全景状态（含所有 agent 健康） |
| GET | `/api/supervisor/rules` | 否 | 查询当前监督规则集 |
| POST | `/api/supervisor/rules` | 是 | 更新监督规则集（热生效） |
| GET | `/api/supervisor/actions` | 否 | 查询管控动作历史 |
| POST | `/api/supervisor/patrol` | 是 | 手动触发一次巡检 |
| POST | `/api/supervisor/action` | 是 | 手动执行管控动作 |

---

## 第3章 文件清单

### 3.1 新增文件

表：新增文件清单

| 文件路径 | 职责 |
|----------|------|
| `py/maop/core/scheduling/supervisor.py` | `Supervisor` 监督者核心实现：继承 `FailurePatternDetector`，新增 patrol/warn/replace/degrade/terminate/upgrade/adjudicate/check_before_dispatch/get_retry_strategy |
| `py/maop/core/scheduling/health_checker.py` | `HealthChecker` 健康检查器：探针执行（ping/metrics/resource）、全量/抽样/自适应巡检策略 |
| `py/maop/core/scheduling/supervisor_rules.py` | `SupervisorRule` 规则模型 + `RuleEngine` 规则评估引擎 + 内置默认规则集 |
| `py/maop/dashboard/routers/supervisor.py` | 监督者 API 路由（`/api/supervisor/*`） |
| `py/tests/test_supervisor.py` | `Supervisor` 测试套件：巡检/预警/六大动作/规则评估/集成检查 |

### 3.2 修改文件

表：修改文件清单

| 文件路径 | 修改内容 | 兼容性 |
|----------|----------|--------|
| `py/maop/core/scheduling/failure_detector.py` | 模块底部新增 `get_supervisor()` / `set_supervisor()` 单例访问函数；`AgentHealth` 扩展 `operational_status` 字段；**[C-1] 修正 `_publish_event` 方法：`emit()` → `publish(Event)`，TYPE_CHECKING 导入从 `enterprise.notification.event_bus` 更新为 `core.reliability.event_bus`** | `get_failure_detector()` 保留，`Supervisor` 继承故既有调用零改动；`operational_status` 默认值兼容原 `status`；`_publish_event` 修正后统一使用 `core.reliability.event_bus.EventBus`，与 `engine.py` 导入一致 |
| `py/maop/dashboard/_register_routes.py` | 注册 `supervisor` 路由 | 新增注册行，既有路由不变 |
| `py/maop/engine.py` | `_execute_step()` 插入 `check_before_dispatch` / `check_after_dispatch` 调用 | 监督者未配置时退化为现状（`supervisor is None` 分支） |
| `py/maop/loop_executor.py` | `_execute_with_retry()` 替换固定重试为 `get_retry_strategy` 动态策略 | 监督者未配置时退化为现有 `lc.iterative_max_attempts` |

### 3.3 不变文件

表：不变文件清单

| 文件路径 | 不动原因 |
|----------|----------|
| `py/maop/core/reliability/event_bus.py` | EventBus 无需扩展，监督者复用现有 `publish` / `subscribe` 机制，仅新增事件 topic |
| `py/maop/delegate/dispatch_core.py` | 调度路径不变，监督者通过 `check_before_dispatch` 在引擎层拦截，不侵入 Dispatcher |
| `py/maop/core/evolution/evolution_loop.py` | 演化循环不变，监督者通过 EventBus 发布建议事件，由演化循环订阅决定是否触发 |
| `py/maop/core/scheduling/scheduling.py` | 调度权重查询不变，`get_failure_detector()` 因继承关系自动获得 `Supervisor` 实例 |

---

## 第4章 设计决策与风险

### 4.1 关键决策

表：关键设计决策对照表

| 编号 | 决策 | 依据 |
|------|------|------|
| D-1 | `Supervisor` 继承而非组合 `FailurePatternDetector` | 既有 `get_failure_detector()` 单例与 `scheduling.py` 路由需零改动；继承保留全部被动检测 API，新增能力为纯增量；与 `design-debate-agent.md` D-2 决策一致 |
| D-2 | 巡检循环为后台 `asyncio.Task` 而非定时器线程 | MAOP 主循环已是 asyncio，复用事件循环避免线程切换开销；巡检探针本身是 async I/O，天然适合协程 |
| D-3 | 六大能力为独立方法而非统一 `act(action)` | 各动作参数与流程差异大（replace 需灰度、upgrade 需版本管理、terminate 需审计），独立方法签名清晰、类型安全；统一入口经 API 层 `action` 字段分发 |
| D-4 | 规则引擎声明式 condition 而非硬编码 | 运行时可通过 API 热更新规则，无需重启；声明式 condition 可序列化持久化；与黑板架构的触发规则范式一致 |
| D-5 | 派发前检查为同步方法 | `check_before_dispatch` 仅读内存状态（operational_status / weight），不涉及 I/O，同步避免协程切换开销；引擎热路径不受影响 |
| D-6 | 监督者触发演化经 EventBus 而非直接调用 | 避免巡检循环与演化循环的递归耦合；EventBus 已有可靠分发与死信跟踪，演化循环可决定是否响应 |
| D-7 | 升级自动回退而非人工确认 | 升级灰度期间监督者加强巡检，新版本劣化自动回退符合"自愈优先"原则；回退事件审计，人工可事后复核 |

### 4.2 与辩论型的关系

监督者型与对抗辩论型（`design-debate-agent.md`）的关系是**正交互补**：

- **独立运行模式**：监督者型可独立部署，常态下通过巡检循环主动监控所有 agent，不依赖辩论机制。此模式下监督者是唯一的控制平面，适用于不需要多角度决策的场景（如纯故障自愈）。
- **辩论裁决模式**：当辩论型部署时，`DebateOrchestrator` 在僵局时调用 `Supervisor.adjudicate()`，监督者作为裁决节点基于历史可信度加权采纳一方结论。此模式下监督者同时承担常态巡检与僵局裁决双重角色。
- **共享实例**：两种模式共享同一 `Supervisor` 单例（通过 `get_supervisor()` 获取），避免双实例状态不一致。`adjudicate()` 方法是惰性实现的——未配置辩论型时该方法不被调用，不产生开销。
- **动作互通**：监督者在裁决中触发的 `degrade` / `terminate` 动作，与巡检触发的动作走同一执行路径，动作审计记录统一存储，Dashboard 统一展示。

### 4.3 风险与缓解

表：风险与缓解措施对照表

| 风险 | 影响 | 缓解 |
|------|------|------|
| 巡检开销压垮下游 | 每 60s 对所有 agent 发探针，agent 数量多时探针请求可能压垮 agent 或其依赖 | `patrol_concurrency` 并发限制（默认 10）；抽样/自适应策略减少巡检集合；探针设计为轻量（ping 不执行业务逻辑） |
| 误判导致误摘流/误终止 | 探针偶发超时（网络抖动）被误判为 agent 失活，触发 terminate | `rule.unreachable.terminate` 要求连续 3 次巡检不可达才终止；terminate 带审计与原因，人工可复核恢复；降级/摘流可逆 |
| 替换过程状态丢失 | 将 agent A 替换为 agent B 时，A 的 in-flight 任务可能丢失 | replace() 先摘流 A（weight=0）等待 in-flight 完成（grace period），再将路由切到 B；grace period 可配置 |
| 升级灰度期间两版本不一致 | v1.2 与 v1.3 并存期间，状态/缓存可能不兼容 | 升级文档要求新版本向后兼容旧版本的状态格式；灰度期间两版本独立状态，不共享缓存；回退时旧版本状态未变更 |
| 巡检与演化正反馈风暴 | 巡检发现批量劣化触发演化，演化变更又引发新劣化，循环放大 | `_maybe_trigger_evolution` 有 10 分钟 cooldown；演化循环自身有 CONSOLIDATE 阶段做变更收敛；监控两循环触发频率告警 |
| 规则热更新误配置 | API 热更新规则时配置错误（如阈值写反），导致误触发 | 规则更新前做 schema 校验与 dry-run 评估；更新后保留上一版本，异常时自动回退规则集 |
| 监督者自身单点 | 监督者作为控制平面单点，其故障导致全系统失去主动监控 | 监督者无状态（状态在 agent 与 EventBus），进程重启后从 agent 注册表恢复；分布式部署时多实例 leader 选举（未来扩展） |

### 4.4 兼容性

#### 4.4.1 与现有 failure_detector.py 的向后兼容

- **继承保留**：`Supervisor` 继承 `FailurePatternDetector`，`record_result()` / `get_weight()` / `get_stats()` / `reset()` / drain / recovery 全部保留，签名不变。
- **单例兼容**：`get_failure_detector()` 保留返回 `FailurePatternDetector` 实例（实际为 `Supervisor` 实例，因继承关系多态生效）。`get_supervisor()` 新增，返回同一对象。
- **事件兼容**：父类的 `agent_drained` / `agent_recovering` / `agent_recovered` 事件保留，监督者新增事件使用不同 topic 前缀，不冲突。
- **指标兼容**：父类的 5 个 Prometheus gauge 保留，监督者新增 `maop_supervisor_patrol_duration_seconds` / `maop_supervisor_actions_total` 等指标。
- **配置兼容**：监督者未配置（`Supervisor` 未实例化或 `patrol_loop` 未启动）时，`Engine` 与 `LoopExecutor` 的监督者集成点走 `supervisor is None` 分支，行为完全退化为现状。

#### 4.4.2 降级运行

- **个人版（无 Redis）**：监督者单进程运行，巡检循环在主事件循环中，不依赖 Redis。
- **监督者禁用**：通过配置 `supervisor_enabled=False` 完全禁用监督者，`get_supervisor()` 返回 `None`，所有集成点退化为现状。
- **仅被动模式**：配置 `patrol_loop_enabled=False` 保留 `Supervisor` 实例但不启动巡检循环，此时监督者仅作为被动检测器（等同原 `FailurePatternDetector`）+ 辩论裁决节点（若辩论型启用）。

---

## 第5章 验收标准

### 5.1 功能验收

1. **巡检发现失活**：agent A 进程终止后，`patrol()` 在 ≤ `patrol_interval_s + patrol_timeout_s` 内发现 `HealthProbe.reachable=False`，发布 `supervisor.alert` 事件。
2. **预警分级推送**：`avg_latency` 超过 15s 时发布 warning 级 `supervisor.alert`；超过 25s 时触发 `degrade` 并发 error 级事件；`reachable=False` 连续 3 次触发 `terminate` 并发 critical 级事件。
3. **替换路由切换**：`replace(agent_a, agent_b, routing_key="codegen")` 后，路由注册表 `codegen → agent_b`，agent_a 标记 `REPLACED`，发布 `agent_replaced` 事件。
4. **降级可逆**：`degrade(agent_b, factor=0.5)` 后 `get_weight(agent_b)` 返回原权重 × 0.5；agent_b 恢复后巡检逐步取消降级，权重恢复。
5. **终止阻断派发**：`terminate(agent_c)` 后 `check_before_dispatch(agent_c)` 返回 `allow=False`；`Engine._execute_step()` 跳过 agent_c 并附原因。
6. **升级切流与回退**：`upgrade(agent_d, "v1.3")` 首版按 `[1.0]` 全切流（加权灰度切量延后）；中途新版本窗口失败率 > 0.15 或 avg_latency > 旧版本 1.5 倍则自动回退，发布 `agent_upgrade.rolled_back` 事件。
7. **辩论裁决联动**：`adjudicate(debate_id, rounds)` 返回 `Verdict(consensus=False, low_confidence=True)`，`adjudication_reason` 非空，对低劣 agent 触发 `degrade`。
8. **动态重试策略**：agent 降级中时 `get_retry_strategy()` 返回的 `max_iterations` 小于默认值；agent terminated 时 `skip_agent=True`。

### 5.2 性能验收

1. **巡检开销**：100 个 agent 全量巡检单轮耗时 ≤ 15s（`patrol_concurrency=10`，单探针 ≤ 5s）。
2. **派发前检查零阻塞**：`check_before_dispatch()` 耗时 ≤ 0.1ms（纯内存读取，无 I/O）。
3. **巡检循环不退出**：连续运行 24h，巡检循环无异常退出；单轮巡检异常被捕获并记录，下轮继续。
4. **规则评估开销**：单 agent 评估 10 条规则耗时 ≤ 1ms。

### 5.3 兼容性验收

1. **被动检测保留**：`Supervisor` 实例的 `record_result()` / `get_weight()` / `get_stats()` 行为与原 `FailurePatternDetector` 一致（相同输入相同输出）。
2. **监督者禁用退化**：`supervisor_enabled=False` 时 `Engine` 与 `LoopExecutor` 行为与现状完全一致（集成点走 `None` 分支）。
3. **单例多态**：`get_failure_detector()` 返回的对象 `isinstance(_, Supervisor)` 为 True，既有调用方无需改动即获得新能力暴露。
4. **事件不冲突**：监督者新增事件 topic（`supervisor.*` / `agent_replaced` / `agent_degraded` / `agent_terminated` / `agent_upgrade.*`）与父类事件 topic（`agent_drained` / `agent_recovering` / `agent_recovered`）无重叠。
5. **指标不冲突**：监督者新增 Prometheus 指标名前缀 `maop_supervisor_*`，与父类 `maop_agent_*` 无重叠。