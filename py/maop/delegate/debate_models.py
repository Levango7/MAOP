"""MAOP 对抗辩论数据模型与异常（design-debate-agent.md §2.2.1）。

从 ``dispatch_debate.py`` 提取，使 ``dispatch_debate.py`` 专注于辩论调度器
（``DebateDispatcher``）的编排逻辑。``dispatch_debate.py`` re-exports 本模块
的所有公开符号以保持向后兼容。
"""

from __future__ import annotations

import time
from enum import Enum

from pydantic import BaseModel, Field

# ── 枚举 ──────────────────────────────────────────────────────────


class DebateRole(str, Enum):
    """辩论角色。"""

    PROPOSER = "proposer"  # 提议者：提出初始方案
    CRITIC = "critic"  # 质疑者：从特定视角质疑
    VERDICT = "verdict"  # 裁决者：最终收敛


class Stance(str, Enum):
    """立场。"""

    SUPPORT = "support"  # 支持
    OPPOSE = "oppose"  # 反对
    AMEND = "amend"  # 修正后有条件支持


# ── 数据模型（design-debate-agent.md §2.2.1）──────────────────────


class DebatePosition(BaseModel):
    """单个 agent 在一轮辩论中的发言（任务描述对应 design-debate-agent.md
    §2.2.1 ``AgentOpinion``，命名遵循 P1 任务约定）。"""

    agent_id: str
    role: DebateRole = DebateRole.CRITIC
    stance: Stance = Stance.SUPPORT
    argument: str = ""  # 结论摘要（对应 conclusion）
    reasoning_chain: list[str] = Field(default_factory=list)
    evidence: list[str] = Field(default_factory=list)
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)
    critiques: list[str] = Field(default_factory=list)  # 对其他 agent 的质疑点
    round_index: int = 0
    tokens_used: int = 0  # 该发言消耗的 token 数（成本控制用）

    @property
    def stance_weight(self) -> float:
        """立场权重：SUPPORT=+1, AMEND=+0.5, OPPOSE=-1。"""
        return {
            Stance.SUPPORT: 1.0,
            Stance.AMEND: 0.5,
            Stance.OPPOSE: -1.0,
        }[self.stance]


class CrossExamination(BaseModel):
    """交叉质证记录：一个 agent 对另一个 agent 结论的质疑。"""

    examiner_id: str
    target_id: str
    critique: str
    response: str = ""  # 被质疑方的回应（下一轮填充）
    round_index: int = 0


class DebateRound(BaseModel):
    """一轮辩论的完整记录。"""

    round_number: int  # 1-indexed
    positions: list[DebatePosition] = Field(default_factory=list)
    cross_examinations: list[CrossExamination] = Field(default_factory=list)
    consensus_score: float = 0.0  # 本轮加权共识度
    reached_consensus: bool = False
    duration_s: float = 0.0
    tokens_used: int = 0  # 本轮总 token 消耗


class DebateVerdict(BaseModel):
    """辩论最终裁决（任务描述对应 design-debate-agent.md §2.2.1 ``Verdict``）。"""

    debate_id: str
    consensus: bool  # True=辩论收敛，False=监督者裁决
    low_confidence: bool = False  # 监督者裁决时标记，建议人工复核
    winner: str = ""  # 获胜方 agent_id（或被采纳结论的 agent_id）
    reasoning: str = ""  # 最终结论摘要（对应 final_conclusion）
    final_confidence: float = 0.0
    winning_stance: Stance = Stance.SUPPORT
    participants: list[str] = Field(default_factory=list)
    rounds: list[DebateRound] = Field(default_factory=list)
    dissenting_opinions: list[str] = Field(default_factory=list)  # 异见方 agent_id
    supervisor_action: str = ""  # 监督者采取的动作（如 "degrade:agent_x"）
    adjudication_reason: str = ""  # 监督者裁决依据
    created_at: float = Field(default_factory=time.time)
    cost_terminated: bool = False  # 是否因成本超限终止
    total_tokens: int = 0


class DebateConfig(BaseModel):
    """辩论运行时配置（design-debate-agent.md §2.4.2 轮次控制）。"""

    max_rounds: int = 3
    min_rounds: int = 1
    consensus_threshold: float = 0.70
    agent_timeout_s: float = 60.0  # 单 agent 超时（R-6）
    round_timeout_s: float = 120.0  # 整轮超时兜底（R-6）
    max_debate_tokens: int = 50000  # 单场辩论总 token 上限（R-4）
    early_exit_on_unanimous: bool = True
    retention_days: int = 30  # 轨迹保留天数（R-5）


# ── 异常 ──────────────────────────────────────────────────────────


class InsufficientParticipantsError(Exception):
    """参与方不足，无法构成对抗（至少需要 3 个：1 Proposer + 2 Critic）。"""


class DebateCostExceededError(Exception):
    """辩论成本超过阈值（R-4）。"""


class DebateTimeoutError(Exception):
    """辩论超时（R-6）。"""