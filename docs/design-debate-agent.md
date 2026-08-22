# MAOP 对抗辩论型 Multi-Agent 设计方案

> 文档状态：设计阶段（未实施）
> 关联任务：Task 331
> 设计范围：对抗辩论（Debate）多 Agent 机制 + 监督者（Supervisor）裁决兜底
> 约束：本文件仅做设计，不含代码实施

---

## 第1章 需求分析

### 1.1 现状分析

#### 1.1.1 现有多 Agent 协作模式

MAOP 当前的多 Agent 协作属于**分工协作（Division of Labor）**模式，核心实现位于 `py/maop/delegate/dispatch_core.py`。其特征如下：

1. **单任务单 Agent 路由**：`Dispatcher.dispatch()` 接收一个 `agent` 名与一个 `task`，经配置解析 → 熔断检查 → 驱动执行 → 结果记录，将任务路由到**唯一**目标 agent。路由依据是 `routing_key` 与 `AgentConfig.capabilities` 的匹配，本质是"把合适的事交给合适的人"，而非"让多个人审视同一件事"。

2. **递归委派链**：`Dispatcher.delegate_to_subagent()` 支持 A → B → C 的递归委派，通过 `max_depth` 防止无限递归。但这是**串行接力**——下游 agent 接收上游 agent 的输出继续处理，并非对同一原始问题的并行多角度审视。

3. **进化决策单策略评估**：`EvolutionLoop.run_cycle()` 的七阶段循环（OBSERVE → HEAL → SUGGEST → EVALUATE → APPLY → VALIDATE → CONSOLIDATE）中，EVALUATE 阶段由 `StrategyEngine` 调用**单一**策略实例（Conservative / Aggressive / Balanced / CostAware 四选一）对建议逐条评估。`BalancedStrategy.evaluate()` 仅依据 severity、cooldown、速率限制做布尔判定（`should_apply`），无多视角交叉验证。

4. **故障检测被动响应**：`FailurePatternDetector`（`py/maop/core/scheduling/failure_detector.py`）采用滑动窗口跟踪各 agent 失败率，支持 drain（摘流，权重→0）与 recovery（灰度回切，0.3→0.6→1.0 阶梯恢复）。但它是**被动**的——仅在 `record_result()` 被外部调用时更新状态，无主动巡检、无预警推送、无对辩论僵局的裁决能力。

#### 1.1.2 现有不足

| 编号 | 不足点 | 影响 | 证据位置 |
|------|--------|------|----------|
| G-1 | 缺少同题多角度审视机制 | 高风险决策（路由变更、故障定级）仅由单 agent / 单策略拍板，易因单一视角盲区产生误判 | `dispatch_core.py:dispatch()` 单 agent 路由；`evolution_strategies.py:StrategyEngine.evaluate()` 单策略 |
| G-2 | 缺少 agent 间互相质疑与反驳 | 无法暴露推理链中的隐含假设错误、证据不足、逻辑跳跃 | 现有代码无任何 agent 间结论交叉比对逻辑 |
| G-3 | 决策无置信度量化与加权 | `EvolutionDecision` 仅有 `should_apply: bool` 与 `estimated_impact: str`，无连续置信度，无法做"多数可信、少数存疑"的分级处理 | `evolution_strategies.py:36-43` |
| G-4 | 未达共识时无追加轮次收敛 | 决策一次性完成，分歧被静默吞掉 | `StrategyEngine.evaluate_and_apply()` 一次评估即应用 |
| G-5 | 故障检测器无主动巡检与裁决 | `FailurePatternDetector` 仅被动记录，无法在辩论僵持时主动介入裁决（替换/降级/终止/升级 agent） | `failure_detector.py:record_result()` 仅响应外部调用 |
| G-6 | 进化建议缺乏对抗验证即应用 | APPLY 阶段直接执行 `ConfigMutator.apply_suggestion()`，虽有 auto_rollback 兜底，但回滚成本高于事前辩论 | `evolution_loop.py:179` APPLY 阶段 |

### 1.2 目标能力

本设计旨在为 MAOP 引入**对抗辩论（Adversarial Debate）**多 Agent 机制，并配套**监督者（Supervisor）裁决兜底**，形成"辩论收敛为主、监督者裁决为辅"的双层可靠决策体系。目标能力如下：

1. **同题多角度并行分析**：对同一问题（路由变更决策、代码审查结论、故障根因判定等），并行分派多个 agent，各自从不同角色视角（如可靠性、性能、成本、安全）独立给出结论与推理链。

2. **互相质疑与反驳**：每轮辩论中，各 agent 可看到其他 agent 的结论与推理链，并针对其薄弱点（隐含假设、证据缺口、逻辑跳跃）提出质疑，被质疑方需回应或修正。

3. **置信度加权投票**：每个 agent 结论附带 [0.0, 1.0] 置信度，综合历史成功率、证据强度、推理链完整度计算。最终共识采用置信度加权投票，而非简单多数决。

4. **未达阈值追加轮次**：当加权共识度低于阈值（如 0.70）且未达最大轮次（如 3 轮），自动追加辩论轮次；每轮注入上一轮的质疑与回应，推动收敛。

5. **裁决节点收敛**：当达到最大轮次仍未收敛，或出现平票僵局，由**监督者（Supervisor）**裁决节点做最终判定。监督者由 `FailurePatternDetector` 升级而来，具备主动巡检能力，可基于各 agent 历史可信度、当前健康状态裁决，并在必要时对持续表现低劣的 agent 执行替换/降级/终止/升级。

6. **可观测与可审计**：每场辩论的全过程（各轮发言、质疑、置信度演变、裁决依据）持久化，供 dashboard 回放与事后审计。

### 1.3 用户场景

#### 1.3.1 路由变更决策

**场景**：自适应路由系统检测到 agent A 对 `routing_key="codegen"` 的成功率持续下降，`ImprovementSuggester` 生成"将 codegen 路由从 agent A 切换到 agent B"的建议。

**辩论流程**：
- Proposer（提议者）提出切换方案，附迁移成本与预期成功率提升。
- Critic_A（可靠性质疑者）质疑：agent B 近 7 天是否有超时峰值？切换是否引入新单点？
- Critic_B（性能质疑者）质疑：agent B 的 p99 延迟是否优于 agent A？灰度比例建议多少？
- 若 Critic 指出 agent B 存在超时风险且置信度高于 Proposer，则追加轮次要求 Proposer 给出降级兜底方案。
- 收敛后输出"切换 + 灰度 20% 起步 + agent B 超时回退到 agent C"的复合方案。

#### 1.3.2 代码审查

**场景**：对一次 PR 变更，需判断是否可合并。

**辩论流程**：
- Proposer 给出"可合并"结论与覆盖的测试用例。
- Critic_A（安全质疑者）检查是否有注入风险、权限越界。
- Critic_B（兼容质疑者）检查 API 签名变更是否破坏下游调用方。
- 若安全质疑者发现隐患且置信度高，输出"阻断合并 + 隐患清单"。

#### 1.3.3 故障根因分析

**场景**：线上错误率突增，需定位根因。

**辩论流程**：
- Proposer 给出"疑似 DB 连接池耗尽"假设与证据。
- Critic_A 质疑：时间线是否吻合？连接池指标是否确实打满？
- Critic_B 提出替代假设"疑似上游流量突增"，附流量曲线证据。
- 两假设置信度接近时追加轮次，要求各自补充排他性证据。
- 若仍僵持，监督者基于各 agent 历史根因定位准确率裁决采纳其一，并标记"低置信度结论，建议人工复核"。

---

## 第2章 设计方案

### 2.1 架构结构图

#### 2.1.1 辩论主流程

```
图：对抗辩论多 Agent 架构流程图

                          ┌──────────────────────────────────────────────────────────────────────────────────────────────────────────────────────┐
                          │  问题输入 (Question)                                                                                                       │
                          │  - 路由变更建议 / 代码审查 PR / 故障根因假设                                                                              │
                          │  - 附上下文：指标、历史、配置快照                                                                                         │
                          └──────────────────────────────────────────────────────────────────────────────────────────────────────────────────────┘
                                                            │
                                                            ▼
                          ┌──────────────────────────────────────────────────────────────────────────────────────────────────────────────────────┐
                          │  DebateOrchestrator（辩论编排器）                                                                                         │
                          │  - 角色分配（RoleAssignment）                                                                                              │
                          │  - 轮次控制（max_rounds / consensus_threshold）                                                                           │
                          │  - 置信度计算（ConfidenceCalculator）                                                                                     │
                          └──────────────────────────────────────────────────────────────────────────────────────────────────────────────────────┘
                                                            │
                ┌───────────────────────────────────────────┼───────────────────────────────────────────┐
                ▼                                           ▼                                           ▼
   ┌────────────────────────┐                 ┌────────────────────────┐                 ┌────────────────────────┐
   │  Round 1               │                 │  Round 2               │                 │  Round N (≤ max)       │
   │  ────────              │                 │  ────────              │                 │  ────────              │
   │  Proposer 提议         │                 │  注入 R1 质疑+回应     │                 │  注入 R(N-1) 质疑+回应 │
   │  Critic_A 质疑         │  ── 收敛检查 ──▶│  各方修正/反驳         │  ── 收敛检查 ──▶│  各方修正/反驳         │
   │  Critic_B 质疑         │                 │  重算置信度            │                 │  重算置信度            │
   │  各方附置信度          │                 │  各方附置信度          │                 │  各方附置信度          │
   └────────────────────────┘                 └────────────────────────┘                 └────────────────────────┘
                │                                           │                                           │
                └───────────────────────────────────────────┴───────────────────────────────────────────┘
                                                            │
                                                            ▼
                          ┌──────────────────────────────────────────────────────────────────────────────────────────────────────────────────────┐
                          │  收敛检查（ConsensusCheck）                                                                                               │
                          │  加权共识度 = Σ(confidence_i × vote_i) / Σ(confidence_i)                                                                  │
                          │  - 共识度 ≥ threshold（默认 0.70）→ 达成共识，输出 Verdict                                                                │
                          │  - 共识度 < threshold 且 round < max_rounds → 追加轮次                                                                   │
                          │  - 共识度 < threshold 且 round = max_rounds → 转监督者裁决                                                              │
                          └──────────────────────────────────────────────────────────────────────────────────────────────────────────────────────┘
                                                            │
                                              ┌─────────────┴─────────────┐
                                              ▼                           ▼
                          ┌──────────────────────────────┐  ┌──────────────────────────────────────────────────────────────────────┐
                          │  达成共识                    │  │  未达共识 → 监督者裁决（Supervisor Verdict）                        │
                          │  Verdict(consensus=True)     │  │  - 基于各 agent 历史可信度加权采纳                              │
                          │  - 结论                      │  │  - 标记 low_confidence=True，建议人工复核                       │
                          │  - 置信度                    │  │  - 对持续低劣 agent 触发 替换/降级/终止/升级                     │
                          │  - 推理链摘要                │  └──────────────────────────────────────────────────────────────────────┘
                          └──────────────────────────────┘                              │
                                                            │                               ▼
                                                            ▼          ┌──────────────────────────────────────────────────────────────────────┐
                          ┌──────────────────────────────────────────┐ │  Supervisor 动作闭环                                               │
                          │  共识输出（ConsensusOutput）              │ │  巡检 → 预警 → [替换 | 降级 | 终止 | 升级] → 复检                  │
                          │  - 最终结论                              │ └──────────────────────────────────────────────────────────────────────┘
                          │  - 置信度                                │
                          │  - 参与方与各方立场                      │
                          │  - 辩论轨迹（debate_id 可回放）          │
                          └──────────────────────────────────────────┘
```

#### 2.1.2 与现有系统嵌套关系

```
图：辩论机制与现有系统集成示意图

  ┌─────────────────────────────────────────────────────────────────────────────────────────────────┐
  │  EvolutionLoop.run_cycle()                                                                      │
  │  OBSERVE → HEAL → SUGGEST → [DEBATE] → EVALUATE → APPLY → VALIDATE → CONSOLIDATE               │
  │                                   ▲ 新增        │                                              │
  │                                   │              │ EVALUATE 消费 DEBATE 产出                    │
  │                                   │              ▼ 的 Verdict（高置信度建议才进入 APPLY）       │
  │                          ┌────────┴───────────────────┐                                     │
  │                          │  DebateOrchestrator         │                                     │
  │                          │  对 suggestions 逐条辩论    │                                     │
  │                          └─────────────────────────────┘                                     │
  └─────────────────────────────────────────────────────────────────────────────────────────────────┘

  ┌─────────────────────────────────────────────────────────────────────────────────────────────────┐
  │  Dispatcher                                                                                    │
  │  dispatch()          ← 单 agent 路由（保持不变）                                               │
  │  dispatch_debate()   ← 新增：同题多 agent 并行 + 辩论收敛                                       │
  │  delegate_to_subagent() ← 递归委派（保持不变）                                                 │
  └─────────────────────────────────────────────────────────────────────────────────────────────────┘

  ┌─────────────────────────────────────────────────────────────────────────────────────────────────┐
  │  FailurePatternDetector（被动）  ──升级──▶  Supervisor（主动监督者）                            │
  │  - record_result()              保留         - patrol() 巡检（定时主动扫描各 agent 健康）       │
  │  - drain / recovery             保留         - warn() 预警推送                                 │
  │  - get_weight()                 保留         - replace/degrade/terminate/upgrade 裁决动作      │
  │                                             - adjudicate() 辩论僵局裁决                        │
  └─────────────────────────────────────────────────────────────────────────────────────────────────┘
```

### 2.2 核心类设计

#### 2.2.1 数据模型（Pydantic）

```
代码示例：辩论核心数据模型（Python）

from __future__ import annotations
from enum import Enum
from pydantic import BaseModel, Field

class DebateRole(str, Enum):
    PROPOSER = "proposer"      # 提议者：提出初始方案
    CRITIC = "critic"          # 质疑者：从特定视角质疑
    VERDICT = "verdict"        # 裁决者：最终收敛

class Stance(str, Enum):
    SUPPORT = "support"        # 支持
    OPPOSE = "oppose"          # 反对
    AMEND = "amend"            # 修正后有条件支持

class AgentOpinion(BaseModel):
    """单个 agent 在一轮辩论中的发言。"""
    agent_id: str
    role: DebateRole
    stance: Stance
    conclusion: str                       # 结论摘要
    reasoning_chain: list[str]            # 推理链（可审计的步骤列表）
    evidence: list[str] = Field(default_factory=list)   # 证据引用（指标名/日志片段/配置项）
    confidence: float = Field(ge=0.0, le=1.0)           # 自评置信度
    critiques: list[str] = Field(default_factory=list)  # 对其他 agent 的质疑点
    round_index: int = 0

class DebateRound(BaseModel):
    """一轮辩论的完整记录。"""
    round_index: int
    opinions: list[AgentOpinion]
    consensus_score: float = 0.0          # 本轮加权共识度
    reached_consensus: bool = False
    duration_s: float = 0.0

class Verdict(BaseModel):
    """辩论最终裁决。"""
    debate_id: str
    consensus: bool                       # 是否达成共识（True=辩论收敛，False=监督者裁决）
    low_confidence: bool = False          # 监督者裁决时标记，建议人工复核
    final_conclusion: str
    final_confidence: float
    winning_stance: Stance
    participants: list[str]               # 参与 agent 列表
    rounds: list[DebateRound]             # 完整辩论轨迹（可回放）
    supervisor_action: str = ""           # 监督者采取的动作（如 "degrade:agent_x"）
    adjudication_reason: str = ""         # 监督者裁决依据
    created_at: float

class ConsensusOutput(BaseModel):
    """对外暴露的共识输出（剥离内部轨迹的精简视图）。"""
    verdict: Verdict
    summary: str                          # 人类可读摘要
    actionable: bool                      # 是否可执行（高置信度且 consensus=True）
```

#### 2.2.2 DebateOrchestrator

`DebateOrchestrator` 是辩论机制的编排核心，负责角色分配、轮次调度、收敛检查与监督者兜底转交。

```
代码示例：DebateOrchestrator 接口设计（Python）

class DebateOrchestrator:
    """对抗辩论编排器。

    Parameters
    ----------
    dispatcher : Dispatcher
        复用现有 Dispatcher.dispatch() 并行分派各 agent。
    supervisor : Supervisor
        监督者实例，用于僵局裁决与 agent 动作执行。
    max_rounds : int
        最大辩论轮次（默认 3）。
    consensus_threshold : float
        加权共识度达成阈值（默认 0.70）。
    role_assignment : RoleAssignment
        角色分配策略（见 2.4.1）。
    confidence_calc : ConfidenceCalculator
        置信度计算器（见 2.4.3）。
    """

    def __init__(
        self,
        dispatcher: Dispatcher,
        supervisor: Supervisor,
        *,
        max_rounds: int = 3,
        consensus_threshold: float = 0.70,
        role_assignment: RoleAssignment | None = None,
        confidence_calc: ConfidenceCalculator | None = None,
    ) -> None: ...

    async def run_debate(
        self,
        question: str,
        context: dict[str, Any],
        *,
        participants: list[str] | None = None,
        trace_id: str = "",
    ) -> Verdict:
        """发起一场辩论并返回最终裁决。

        流程：
          1. 角色分配：从 participants 选出 1 个 Proposer + N 个 Critic。
          2. Round 1：并行 dispatch 各 agent，收集 AgentOpinion。
          3. 收敛检查：计算加权共识度。
          4. 未达阈值且 round < max_rounds → 注入质疑与回应，进入下一轮。
          5. 达阈值 → 返回 Verdict(consensus=True)。
          6. 达 max_rounds 仍未收敛 → 转交 supervisor.adjudicate() 裁决。
        """
        ...

    def _check_consensus(self, round_: DebateRound) -> float:
        """计算加权共识度 = Σ(conf_i × stance_weight_i) / Σ(conf_i)。

        stance_weight: SUPPORT=+1, AMEND=+0.5, OPPOSE=-1。
        返回值归一化到 [0, 1]。
        """
        ...

    async def _run_round(
        self,
        question: str,
        context: dict[str, Any],
        round_index: int,
        prev_round: DebateRound | None,
    ) -> DebateRound:
        """执行单轮辩论。

        Round 1：各 agent 仅看到 question + context。
        Round N>1：各 agent 额外看到 prev_round 中其他 agent 的结论与对本方的质疑，
                  需在回应中修正结论或反驳质疑。
        """
        ...
```

#### 2.2.3 DebateRound

`DebateRound` 作为 Pydantic 模型已在 2.2.1 定义。其运行时行为由 `DebateOrchestrator._run_round()` 驱动，本身不含主动逻辑，仅承载一轮辩论的不可变快照，便于持久化与回放。

#### 2.2.4 Verdict

`Verdict` 同样在 2.2.1 定义。关键语义：

- `consensus=True`：辩论自然收敛，`final_confidence` 为加权共识度，`supervisor_action` 为空。
- `consensus=False`：达最大轮次未收敛，由监督者裁决。`low_confidence=True`，`adjudication_reason` 记录裁决依据，`supervisor_action` 记录对 agent 执行的动作（如 `degrade:agent_x`、`replace:agent_a→agent_b`）。

#### 2.2.5 Supervisor（监督者裁决节点）

由 `FailurePatternDetector` 升级而来，保留全部原有被动检测能力，新增主动巡检与裁决。

```
代码示例：Supervisor 接口设计（Python）

class SupervisorAction(str, Enum):
    # 注：SupervisorAction 枚举定义在 `py/maop/core/scheduling/supervisor.py` 中，
    # 辩论型复用监督者型定义（见 design-supervisor-agent.md 2.2.1 节），
    # 此处仅作引用说明，不重复定义。
    PATROL = "patrol"       # 巡检：主动扫描各 agent 健康
    ALERT = "alert"         # 预警：健康劣化预警推送
    REPLACE = "replace"     # 替换：将某 agent 的路由永久切到备用 agent
    DEGRADE = "degrade"     # 降级：降低权重 / 限制并发 / 缩短超时
    TERMINATE = "terminate" # 终止：摘流并标记不可用（等同 drain，但带审计标记）
    UPGRADE = "upgrade"     # 升级：恢复权重 / 放宽限制（等同 recovery 加速）
    NONE = "none"           # 无动作

class Supervisor(FailurePatternDetector):
    """主动监督者：在 FailurePatternDetector 基础上新增巡检/预警/裁决。

    新增能力
    --------
    - patrol()        : 定时主动巡检各 agent 健康（不依赖外部 record_result 调用）。
    - warn()          : 健康劣化预警推送（经 EventBus）。
    - adjudicate()    : 辩论僵局裁决，基于历史可信度加权采纳一方结论。
    - replace/degrade/terminate/upgrade : 对 agent 执行管控动作并审计。
    """

    async def patrol(self) -> list[AgentHealth]:
        """主动巡检：扫描所有已注册 agent 的当前健康快照。

        与被动 record_result 互补——即使某 agent 长期无任务，
        patrol 也能发现其处于异常状态（如进程失活、熔断开启）。
        巡检发现劣化时自动调用 warn()。
        """
        ...

    async def warn(self, agent_id: str, reason: str) -> None:
        """推送预警事件到 EventBus（level=warning）。

        EventBus API 统一使用 `core.reliability.event_bus.EventBus` 的
        `publish(Event)` 方法（非 `emit()`），调用形如：
            await event_bus.publish(Event(
                topic="supervisor.alert",
                data={"agent_id": agent_id, "reason": reason, "level": "warning"},
                source="debate",
            ))
        其中 `event_bus` 由 `get_event_bus()` 返回全局单例。
        """
        ...

    async def adjudicate(
        self,
        debate_id: str,
        rounds: list[DebateRound],
    ) -> Verdict:
        """辩论僵局裁决。

        策略：
          1. 取最后一轮各 agent 结论。
          2. 以各 agent 历史可信度（成功率 / 历史裁决正确率）为权重，
             加权采纳置信度最高且历史可信度最高的一方结论。
          3. 对本轮表现显著低于历史均值的 agent，触发 degrade/terminate。
          4. 标记 low_confidence=True，adjudication_reason 记录加权明细。

        terminate 边界处理：
          - terminate 前检查该 agent 是否为某 routing_key 的唯一可用 agent，
            若是则降级为 degrade（而非 terminate），避免该 routing_key
            无可用 agent 导致调度死锁。
          - 判定依据：遍历该 agent 服务的所有 routing_key，若存在任一
            routing_key 的可用 agent 集合（剔除已 disabled 者）仅含该 agent，
            则该 agent 视为"唯一可用"，terminate 降级为 degrade。
        """
        ...

    async def replace(self, agent_id: str, replacement: str, reason: str) -> None: ...
    async def degrade(self, agent_id: str, factor: float, reason: str) -> None: ...
    async def terminate(self, agent_id: str, reason: str) -> None: ...
    async def upgrade(self, agent_id: str, reason: str) -> None: ...
```

### 2.3 与现有系统集成方案

#### 2.3.1 dispatch_core.py 新增 dispatch_debate()

在 `Dispatcher` 类上新增异步方法 `dispatch_debate()`，复用现有 `dispatch()` 并行分派多个 agent，再交由 `DebateOrchestrator` 收敛。

```
代码示例：dispatch_debate 方法签名（Python）

class Dispatcher:
    # ... 现有方法保持不变 ...

    async def dispatch_debate(
        self,
        question: str,
        participants: list[str],
        *,
        context: dict[str, Any] | None = None,
        routing_key: str = "",
        trace_id: str = "",
        max_rounds: int = 3,
        consensus_threshold: float = 0.70,
    ) -> Verdict:
        """对同一问题发起多 agent 对抗辩论并返回裁决。

        实现要点：
          1. 懒加载 DebateOrchestrator（首次调用时构造，复用 self 与 Supervisor 单例）。
          2. 各 agent 的发言通过 self.dispatch(agent, task=prompt, ...) 获取，
             prompt 由 DebateOrchestrator 按角色与轮次组装。
          3. 全程复用现有熔断、SLA、预算、并发信号量（_semaphore）保护，
             不绕过任何可靠性机制。
          4. 返回 Verdict，调用方据 consensus / low_confidence 决定是否采纳。
        """
        ...
```

**集成约束**：
- `dispatch_debate()` 内部对每个 participant 的调用仍走 `_dispatch_impl()`，因此熔断器、预算检查、guardrail、SLA 记量全部生效，辩论不引入绕过安全防线的旁路。
- 并行分派使用 `asyncio.gather()`，受 `self._semaphore` 全局并发限制约束，避免辩论放大下游 LLM API 压力。

#### 2.3.2 evolution_loop.py 插入辩论环节

在 `EvolutionLoop.run_cycle()` 的 SUGGEST 与 EVALUATE 之间插入 **DEBATE** 阶段，对生成的建议逐条辩论，仅高置信度共识建议进入 EVALUATE/APPLY。

```
代码示例：EvolutionLoop 新增 DEBATE 阶段（Python）

class LoopPhase(str, Enum):
    OBSERVE = "observe"
    HEAL = "heal"
    SUGGEST = "suggest"
    DEBATE = "debate"          # 新增
    EVALUATE = "evaluate"
    APPLY = "apply"
    VALIDATE = "validate"
    CONSOLIDATE = "consolidate"

class EvolutionLoop:
    def run_cycle(self, dry_run=False, auto_rollback=True) -> LoopReport:
        # ... OBSERVE / HEAL / SUGGEST 保持不变 ...

        # 新增 DEBATE 阶段：对 suggestions 逐条辩论
        debate = self._phase_debate(suggest.details.get("suggestions", []))
        report.phases.append(debate)
        # 仅采纳 consensus=True 且 final_confidence ≥ 阈值的建议
        debated_suggestions = debate.details.get("accepted_suggestions", [])

        # EVALUATE 消费辩论后的高置信度建议
        evaluate = self._phase_evaluate(debated_suggestions)
        # ... 后续 APPLY / VALIDATE / CONSOLIDATE 保持不变 ...

    def _phase_debate(self, suggestions: list[dict]) -> PhaseResult:
        """对每条建议发起辩论，过滤低置信度结论。

        - 对 severity=HIGH 的建议强制辩论（即使 BalancedStrategy 本会自动应用）。
        - 辩论 consensus=False 的建议标记为 "debate_blocked"，不进入 APPLY。
        - 辩论 low_confidence=True 的建议标记为 "needs_human_review"。
        """
        ...
```

**回滚兼容**：DEBATE 阶段插入需修改 `evolution_loop.py:run_cycle()` 方法体，在 `_phase_suggest()` 和 `_phase_evaluate()` 调用之间插入 `_phase_debate()` 调用，并将 `_phase_evaluate()` 入参改为辩论后的建议列表 `debated_suggestions`。`LoopPhase` 枚举新增 DEBATE 成员（在 `evolution_loop_types.py` 中），既有枚举值不变。这是方法体修改而非签名修改，既有调用方不受影响。当 `DebateOrchestrator` 未配置（如个人版降级运行）时，`_phase_debate()` 直接透传 suggestions，行为退化为现状，保证向后兼容。

**轨迹清理**：辩论轨迹保留 30 天，超期记录归档到冷存储后删除。清理任务在 EvolutionLoop 的 CONSOLIDATE 阶段执行——CONSOLIDATE 阶段原有逻辑不变，新增调用 `debate.persistence.cleanup_expired(retention_days=30)` 清理超期辩论记录。清理任务受 CONSOLIDATE 阶段既有频率约束（每轮演化循环执行一次），避免高频清理抢占主流程资源。

#### 2.3.3 failure_detector.py 升级为 Supervisor

`Supervisor` 继承 `FailurePatternDetector`，原 `record_result()` / `get_weight()` / `get_stats()` / drain / recovery 全部保留，外部既有调用方（如 `scheduling.py` 路由、`dispatch_core.py` 权重查询）零改动。新增的 `patrol()` / `adjudicate()` / `replace()` 等方法仅在辩论路径与定时巡检任务中调用。

**单例管理**：保留 `get_failure_detector()` 单例函数，新增 `get_supervisor()` 返回 `Supervisor` 实例（若已升级则返回同一对象，否则懒构造）。`scheduling.py` 路由既有 `get_failure_detector()` 调用因继承关系自动获得新能力暴露。

### 2.4 辩论策略设计

#### 2.4.1 角色分配

```
代码示例：角色分配策略接口（Python）

class RoleAssignment:
    """辩论角色分配器。

    策略
    ----
    - capability_based : 按 AgentConfig.capabilities 匹配视角。
        Proposer 选 capabilities 含 "proposal" 或与问题域最匹配的 agent。
        Critic 按预设视角池分配：["reliability","performance","cost","security"]，
        每个 Critic 选 capabilities 含对应视角的 agent。
    - explicit : 调用方在 participants 中显式标注角色（附 role 字段）。
    - round_robin : 无明确能力标注时轮转分配，保证多样性。
    """

    def assign(
        self,
        question: str,
        participants: list[str],
        context: dict[str, Any],
    ) -> dict[DebateRole, list[str]]:
        """返回 {DebateRole: [agent_id, ...]} 映射。

        约束：
          - 恰好 1 个 Proposer。
          - 至少 2 个 Critic（保证对抗性）。
          - 参与方不足 3 个时抛 InsufficientParticipantsError，
            调用方决定降级为单 agent dispatch。
        """
        ...
```

**视角池**：可靠性（reliability）、性能（performance）、成本（cost）、安全（security）、兼容性（compatibility）。每场辩论按问题类型选取 2–3 个最相关视角分配 Critic，避免无关视角稀释焦点。

#### 2.4.2 轮次控制

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `max_rounds` | 3 | 最大辩论轮次。Round 1 为初始发言，Round 2/3 为质疑-回应收敛 |
| `consensus_threshold` | 0.70 | 加权共识度达此值则提前结束 |
| `min_rounds` | 1 | 最少轮次（防止 Round 1 偶然高共识导致未经审视即采纳，对 HIGH severity 强制 ≥ 2） |
| `agent_timeout_s` | 60 | 单 agent 超时，超时方视为弃权（confidence=0），不等待慢 agent |
| `round_timeout_s` | 120 | 整轮超时兜底，所有 agent 总耗时超此值则强制结束本轮，未完成 agent 视为弃权 |
| `max_debate_tokens` | 50000 | 单场辩论总 token 上限，超限后提前终止并降级为单 agent 决策 |
| `early_exit_on_unanimous` | True | 全员一致支持且平均置信度 ≥ 0.85 时允许 Round 1 即退出（非 HIGH severity） |

**收敛判定**：每轮结束后调用 `_check_consensus()`，共识度单调非降则继续，若连续两轮共识度下降（发散）则提前转监督者裁决，避免无效消耗。

**成本控制**：单场辩论设置总 token 上限 `max_debate_tokens`（默认 50000），辩论过程中累计各 agent 各轮的 token 消耗，超限后提前终止辩论并降级为单 agent 决策（取历史可信度最高的 agent 直接 dispatch），同时在 `Verdict.adjudication_reason` 中记录"成本超限降级"。该机制避免高频辩论下 LLM 调用放大 N×M 倍导致的成本失控。

**超时粒度**：超时判定粒度为"单 agent 超时"——每个 agent 有独立的 `agent_timeout_s`，单个 agent 超时即视为弃权（confidence=0），不等待慢 agent 完成后再判定整轮。`round_timeout_s` 作为整轮兜底超时，防止所有 agent 都接近超时导致整轮耗时过长。两者关系：`agent_timeout_s` < `round_timeout_s`，单 agent 超时优先触发。

#### 2.4.3 置信度计算

```
代码示例：置信度计算器接口（Python）

class ConfidenceCalculator:
    """综合置信度计算。

    confidence = w1 × historical_accuracy      # 该 agent 历史结论正确率
               + w2 × evidence_strength        # 证据强度（引用的证据数量与可信度）
               + w3 × reasoning_completeness   # 推理链完整度（步骤数 / 缺口数）
               + w4 × self_assessment          # agent 自评置信度（需校准去过度自信）

    默认权重 w1=0.35, w2=0.25, w3=0.20, w4=0.20，Σ=1.0。
    """

    def compute(
        self,
        agent_id: str,
        opinion: AgentOpinion,
        history: list[dict[str, Any]],
    ) -> float:
        """返回校准后的置信度 ∈ [0, 1]。"""
        ...

    def _calibrate_self_assessment(
        self,
        agent_id: str,
        raw: float,
        history: list[dict[str, Any]],
    ) -> float:
        """去过度自信校准。

        若该 agent 历史自评均值显著高于实际正确率（偏差 > 0.2），
        对其自评施加惩罚因子，抑制"嘴硬但常错"的 agent。
        """
        ...
```

**加权共识度公式**：

```
consensus_score = ( Σ_i confidence_i × stance_weight_i ) / ( Σ_i confidence_i ) + 1 ) / 2

其中 stance_weight: SUPPORT=+1, AMEND=+0.5, OPPOSE=-1
归一化使结果落在 [0, 1]，0.5 表示完全对立僵持
```

### 2.5 API 设计

遵循现有 dashboard 路由模式：FastAPI `APIRouter(prefix=...)` + `handle_api_errors` 装饰器，GET 端点免鉴权（同 `scheduling.py` 策略），写端点需管理员鉴权。

#### 2.5.1 辩论 API

```
代码示例：辩论 API 路由（Python）

router = APIRouter(prefix="/api/debate", tags=["debate"])

@router.post("/run")
async def api_debate_run(body: DebateRunRequest) -> Verdict:
    """发起一场辩论。

    请求体：
      {
        "question": "将 codegen 路由从 agent_a 切换到 agent_b 是否安全？",
        "participants": ["agent_a", "agent_b", "agent_c"],
        "context": {"metric": "...", "config_snapshot": "..."},
        "routing_key": "codegen",
        "max_rounds": 3,
        "consensus_threshold": 0.70
      }
    返回：Verdict
    """
    ...

@router.get("/{debate_id}")
async def api_debate_get(debate_id: str) -> Verdict:
    """查询某场辩论的完整裁决与轨迹（可回放）。"""
    ...

@router.get("/history")
async def api_debate_history(limit: int = 20) -> list[Verdict]:
    """查询近期辩论历史。"""
    ...
```

#### 2.5.2 监督者 API

```
代码示例：监督者 API 路由（Python）

router = APIRouter(prefix="/api/supervisor", tags=["supervisor"])

@router.get("/status")
async def api_supervisor_status() -> dict[str, Any]:
    """返回所有 agent 健康快照 + 监督者巡检状态。

    复用 Supervisor.get_stats()，附加 last_patrol_at / pending_warnings。
    """
    ...

@router.post("/patrol")
async def api_supervisor_patrol() -> dict[str, Any]:
    """手动触发一次巡检（管理员）。返回本次巡检发现的劣化项。"""
    ...

@router.post("/action")
async def api_supervisor_action(body: SupervisorActionRequest) -> dict[str, Any]:
    """手动对 agent 执行管控动作（管理员）。

    请求体：
      {
        "agent_id": "agent_x",
        "action": "degrade",       // replace|degrade|terminate|upgrade
        "params": {"factor": 0.5},
        "reason": "辩论中持续低置信度"
      }
    """
    ...
```

#### 2.5.3 端点清单

| 方法 | 路径 | 鉴权 | 说明 |
|------|------|------|------|
| POST | `/api/debate/run` | 是 | 发起辩论 |
| GET | `/api/debate/{debate_id}` | 否 | 查询辩论裁决 |
| GET | `/api/debate/history` | 否 | 辩论历史 |
| GET | `/api/supervisor/status` | 否 | 监督者状态 |
| POST | `/api/supervisor/patrol` | 是 | 手动巡检 |
| POST | `/api/supervisor/action` | 是 | 手动管控动作 |

---

## 第3章 文件清单

### 3.1 新增文件

| 文件路径 | 职责 |
|----------|------|
| `py/maop/core/debate/__init__.py` | 辩论模块包初始化，导出公共符号 |
| `py/maop/core/debate/models.py` | Pydantic 数据模型：`AgentOpinion`、`DebateRound`、`Verdict`、`ConsensusOutput`、`DebateRole`、`Stance` |
| `py/maop/core/debate/orchestrator.py` | `DebateOrchestrator` 编排器：角色分配、轮次调度、收敛检查、监督者转交 |
| `py/maop/core/debate/roles.py` | `RoleAssignment` 角色分配策略（capability_based / explicit / round_robin） |
| `py/maop/core/debate/confidence.py` | `ConfidenceCalculator` 置信度计算与自评校准 |
| `py/maop/core/debate/persistence.py` | 辩论轨迹持久化（SQLite，复用 `get_db_path("debate")` 模式）；含轨迹清理：保留 30 天，超期记录归档到冷存储后删除，清理任务在 EvolutionLoop 的 CONSOLIDATE 阶段执行 |
| `py/maop/core/scheduling/supervisor.py` | `Supervisor` 监督者：继承 `FailurePatternDetector`，新增 patrol/warn/adjudicate/replace/degrade/terminate/upgrade |
| `py/maop/dashboard/routers/debate.py` | 辩论 API 路由（`/api/debate/*`） |
| `py/maop/dashboard/routers/supervisor.py` | 监督者 API 路由（`/api/supervisor/*`） |
| `tests/unit/core/debate/test_orchestrator.py` | `DebateOrchestrator` 单元测试：收敛、追加轮次、僵局转交 |
| `tests/unit/core/debate/test_confidence.py` | 置信度计算与自评校准测试 |
| `tests/unit/core/debate/test_roles.py` | 角色分配策略测试 |
| `tests/unit/core/scheduling/test_supervisor.py` | `Supervisor` 巡检/裁决/动作测试 |
| `tests/integration/test_debate_e2e.py` | 辩论端到端集成测试（含 evolution_loop DEBATE 阶段） |

### 3.2 修改文件

| 文件路径 | 修改内容 | 兼容性 |
|----------|----------|--------|
| `py/maop/delegate/dispatch_core.py` | `Dispatcher` 类新增 `dispatch_debate()` 异步方法 | 纯新增方法，既有 `dispatch()` / `delegate_to_subagent()` 不变 |
| `py/maop/core/evolution/evolution_loop.py` | `run_cycle()` 在 SUGGEST 与 EVALUATE 间插入 `_phase_debate()` 调用 | `_phase_debate()` 未配置时透传，行为退化现状 |
| `py/maop/core/evolution/evolution_loop_types.py` | `LoopPhase` 枚举新增 `DEBATE = "debate"` | 枚举新增成员，既有值不变 |
| `py/maop/core/scheduling/failure_detector.py` | 模块底部新增 `get_supervisor()` / `set_supervisor()` 单例访问函数 | `get_failure_detector()` 保留，`Supervisor` 继承故既有调用零改动 |
| `py/maop/dashboard/routers/__init__.py` | 注册 `debate` 与 `supervisor` 路由 | 新增注册行 |

### 3.3 不变文件（明确不动）

| 文件路径 | 不动原因 |
|----------|----------|
| `py/maop/delegate/dispatcher.py` | 纯 re-export shim，`dispatch_debate` 经 `dispatch_core` 自动可用 |
| `py/maop/delegate/models.py` | `AgentConfig` / `DispatchResult` 无需扩展 |
| `py/maop/core/evolution/evolution_strategies.py` | 策略引擎不变，辩论在其上游过滤建议，EVALUATE 仍用现有策略做最终 `should_apply` 判定 |

---

## 第4章 设计决策与风险

### 4.1 关键决策

| 编号 | 决策 | 依据 |
|------|------|------|
| D-1 | 以辩论型为主线，监督者型为裁决兜底 | 用户主体明确要求辩论机制（文件名 `design-debate-agent.md`），任务内容要求 Supervisor 升级；两者互补——辩论负责常态收敛，监督者负责僵局裁决与 agent 管控 |
| D-2 | `Supervisor` 继承而非组合 `FailurePatternDetector` | 既有 `get_failure_detector()` 单例与 `scheduling.py` 路由需零改动；继承保留全部被动检测 API，新增能力为纯增量 |
| D-3 | 辩论复用 `Dispatcher.dispatch()` 而非新建旁路 | 确保熔断、预算、guardrail、SLA、并发信号量全部生效，辩论不绕过安全防线 |
| D-4 | DEBATE 阶段插在 SUGGEST 与 EVALUATE 之间 | 辩论过滤低置信度建议后再由现有策略做 `should_apply` 判定，双层过滤且不破坏策略引擎契约 |
| D-5 | 置信度含历史校准项 | 抑制"自评高但常错"的 agent，避免其高自信错误结论主导共识 |

### 4.2 风险与缓解

| 风险 | 影响 | 缓解 |
|------|------|------|
| 辩论放大 LLM 调用量（每场 N agent × M 轮） | 成本与延迟上升 | 受 `self._semaphore` 全局并发限制；`max_rounds` 默认 3；仅 HIGH severity 或显式请求才辩论；设置单场辩论总 token 上限 `max_debate_tokens`（默认 50000），超限后提前终止并降级为单 agent 决策 |
| 辩论发散不收敛 | 达 max_rounds 后转监督者裁决，但仍消耗资源 | 监控连续两轮共识度下降即提前终止转裁决；`agent_timeout_s` 单 agent 超时即弃权不等待慢 agent；`round_timeout_s` 整轮兜底超时 |
| 监督者裁决误判 | 低置信度结论被采纳 | `low_confidence=True` 标记强制建议人工复核；裁决仅决定"采纳哪方"而非"生成新结论" |
| 角色分配参与方不足 | 无法构成对抗 | 抛 `InsufficientParticipantsError`，调用方降级为单 agent `dispatch()`，不阻断主流程 |
| 辩论轨迹持久化膨胀 | 存储增长 | 仅持久化 `Verdict`（含 rounds 摘要），完整原始发言按 `debate_id` 可选落库；辩论轨迹保留 30 天，超期记录归档到冷存储后删除；清理任务在 EvolutionLoop 的 CONSOLIDATE 阶段执行 |
| terminate 后 routing_key 无可用 agent | 调度死锁 | adjudicate() 中 terminate 前检查该 agent 是否为某 routing_key 唯一可用，若是则降级为 degrade 而非 terminate |

---

## 第5章 验收标准（设计层面）

1. **同题多角度**：`dispatch_debate(question, [a, b, c])` 返回的 `Verdict.participants` 含全部参与方，`rounds[0].opinions` 含各方独立结论。
2. **互相质疑**：`rounds[1].opinions[i].critiques` 非空且引用 `rounds[0].opinions[j]` 的结论。
3. **置信度加权**：`Verdict.final_confidence` 等于末轮 `consensus_score`，计算符合 2.4.3 公式。
4. **追加轮次**：当 `consensus_score < threshold` 且 `round < max_rounds` 时 `len(verdict.rounds) > 1`。
5. **监督者兜底**：达 `max_rounds` 未收敛时 `verdict.consensus == False` 且 `verdict.adjudication_reason` 非空。
6. **向后兼容**：未配置辩论时 `EvolutionLoop.run_cycle()` 行为与现状一致（DEBATE 阶段透传）。
7. **安全不绕过**：辩论中任一 agent 触发熔断/预算超限/guardrail 拦截时，该 agent 视为弃权而非旁路放行。
8. **可审计**：`GET /api/debate/{debate_id}` 返回完整 `Verdict` 含全部轮次轨迹。