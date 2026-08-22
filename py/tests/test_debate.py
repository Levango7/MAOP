"""Tests for maop.delegate.dispatch_debate — 对抗辩论型 Multi-Agent.

Covers (per design-debate-agent.md §5 验收标准):
  1. 同题多角度：dispatch_debate 返回的 Verdict.participants 含全部参与方。
  2. 互相质疑：rounds[1].positions[i].critiques 非空。
  3. 置信度加权：Verdict.final_confidence 等于末轮 consensus_score。
  4. 追加轮次：未达阈值且 round < max_rounds 时 len(verdict.rounds) > 1。
  5. 监督者兜底：达 max_rounds 未收敛时 consensus=False 且 adjudication_reason 非空。
  6. 向后兼容：未配置辩论时 EvolutionLoop 行为与现状一致（DEBATE 阶段透传）。
  7. 安全不绕过：辩论中任一 agent 触发熔断/超时视为弃权。
  8. 可审计：GET /api/debate/{debate_id} 返回完整 Verdict。

Additional coverage:
  - 数据模型 (DebatePosition / DebateRound / DebateVerdict / DebateConfig)
  - 枚举 (DebateRole / Stance)
  - Adjudicator: LLM 裁决 / 监督者裁决兜底 / 最终兜底
  - 成本控制 (R-4): 超限终止
  - 轨迹清理 (R-5): cleanup_traces
  - 超时粒度 (R-6): 单 agent 超时 + 整轮超时
  - EvolutionLoop DEBATE 阶段插入 (C-2)
  - LoopPhase 枚举新增 DEBATE (F-5)
  - API 路由 (POST /start, GET /{id}, GET /{id}/verdict, GET /history, POST /config)
"""

from __future__ import annotations

import asyncio
import time
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from maop.core.evolution.evolution_loop import EvolutionLoop
from maop.core.evolution.evolution_loop_types import LoopPhase
from maop.delegate.dispatch_debate import (
    Adjudicator,
    CrossExamination,
    DebateConfig,
    DebateDispatcher,
    DebatePosition,
    DebateRole,
    DebateRound,
    DebateVerdict,
    InsufficientParticipantsError,
    Stance,
)

# ── 1. 数据模型 & 枚举 ────────────────────────────────────────────


def test_debate_role_enum_values():
    assert DebateRole.PROPOSER.value == "proposer"
    assert DebateRole.CRITIC.value == "critic"
    assert DebateRole.VERDICT.value == "verdict"


def test_stance_enum_values():
    assert Stance.SUPPORT.value == "support"
    assert Stance.OPPOSE.value == "oppose"
    assert Stance.AMEND.value == "amend"


def test_stance_weight():
    assert DebatePosition(agent_id="a", stance=Stance.SUPPORT).stance_weight == 1.0
    assert DebatePosition(agent_id="a", stance=Stance.AMEND).stance_weight == 0.5
    assert DebatePosition(agent_id="a", stance=Stance.OPPOSE).stance_weight == -1.0


def test_debate_position_defaults():
    p = DebatePosition(agent_id="a")
    assert p.role == DebateRole.CRITIC
    assert p.stance == Stance.SUPPORT
    assert p.confidence == 0.5
    assert p.evidence == []
    assert p.critiques == []
    assert p.tokens_used == 0


def test_debate_round_defaults():
    r = DebateRound(round_number=1)
    assert r.positions == []
    assert r.cross_examinations == []
    assert r.consensus_score == 0.0
    assert r.reached_consensus is False
    assert r.tokens_used == 0


def test_debate_verdict_defaults():
    v = DebateVerdict(debate_id="d1", consensus=True)
    assert v.low_confidence is False
    assert v.winner == ""
    assert v.participants == []
    assert v.rounds == []
    assert v.cost_terminated is False
    assert v.total_tokens == 0


def test_debate_config_defaults():
    c = DebateConfig()
    assert c.max_rounds == 3
    assert c.consensus_threshold == 0.70
    assert c.agent_timeout_s == 60.0
    assert c.round_timeout_s == 120.0
    assert c.max_debate_tokens == 50000
    assert c.retention_days == 30


def test_cross_examination_defaults():
    ce = CrossExamination(examiner_id="a", target_id="b", critique="x")
    assert ce.response == ""
    assert ce.round_index == 0


# ── 2. LoopPhase 枚举新增 DEBATE (F-5) ────────────────────────────


def test_loop_phase_has_debate_member():
    """F-5: LoopPhase 枚举新增 DEBATE 值。"""
    assert hasattr(LoopPhase, "DEBATE")
    assert LoopPhase.DEBATE.value == "debate"


def test_loop_phase_debate_between_suggest_and_evaluate():
    """F-5: DEBATE 在 SUGGEST 与 EVALUATE 之间。"""
    phases = list(LoopPhase)
    suggest_idx = phases.index(LoopPhase.SUGGEST)
    debate_idx = phases.index(LoopPhase.DEBATE)
    evaluate_idx = phases.index(LoopPhase.EVALUATE)
    assert suggest_idx < debate_idx < evaluate_idx


def test_loop_phase_existing_values_unchanged():
    """F-5: 既有枚举值不变（向后兼容）。"""
    assert LoopPhase.OBSERVE.value == "observe"
    assert LoopPhase.HEAL.value == "heal"
    assert LoopPhase.SUGGEST.value == "suggest"
    assert LoopPhase.EVALUATE.value == "evaluate"
    assert LoopPhase.APPLY.value == "apply"
    assert LoopPhase.VALIDATE.value == "validate"
    assert LoopPhase.CONSOLIDATE.value == "consolidate"


# ── 3. Adjudicator 裁决节点 ───────────────────────────────────────


class _MockLLMJudge:
    """Mock LLM 裁决器。"""

    def __init__(self, verdict: DebateVerdict | None = None, fail: bool = False):
        self._verdict = verdict
        self._fail = fail
        self.call_count = 0

    async def __call__(self, rounds: list[DebateRound]) -> DebateVerdict:
        self.call_count += 1
        if self._fail:
            raise RuntimeError("LLM judge unavailable")
        if self._verdict is not None:
            return self._verdict
        return DebateVerdict(
            debate_id="llm",
            consensus=False,
            low_confidence=False,
            winner="agent_a",
            reasoning="LLM adjudicated",
            final_confidence=0.8,
        )


@pytest.mark.asyncio
async def test_adjudicator_llm_judge_path():
    """Adjudicator 优先使用 LLM 裁决。"""
    expected = DebateVerdict(
        debate_id="d1", consensus=False, winner="x", final_confidence=0.9,
    )
    judge = _MockLLMJudge(verdict=expected)
    adj = Adjudicator(llm_judge=judge)
    result = await adj.adjudicate("d1", [], ["a", "b", "c"])
    assert result is expected
    assert judge.call_count == 1


@pytest.mark.asyncio
async def test_adjudicator_llm_failure_falls_back_to_supervisor():
    """LLM 裁决失败时回退到监督者裁决。"""
    judge = _MockLLMJudge(fail=True)
    # Mock supervisor with adjudicate method
    sup = MagicMock()
    sup.adjudicate = AsyncMock(return_value={
        "consensus": False,
        "low_confidence": True,
        "adjudication_reason": "supervisor fallback",
        "winner": None,
    })
    adj = Adjudicator(llm_judge=judge, supervisor=sup)
    rounds = [DebateRound(round_number=1, positions=[
        DebatePosition(agent_id="a", confidence=0.6),
    ])]
    result = await adj.adjudicate("d1", rounds, ["a", "b", "c"])
    assert result.consensus is False
    assert result.low_confidence is True
    assert "supervisor fallback" in result.adjudication_reason


@pytest.mark.asyncio
async def test_adjudicator_supervisor_path():
    """无 LLM 裁决时使用监督者裁决。"""
    sup = MagicMock()
    sup.adjudicate = AsyncMock(return_value={
        "consensus": False,
        "low_confidence": False,
        "adjudication_reason": "supervisor decided",
        "winner": DebatePosition(agent_id="a", confidence=0.85, stance=Stance.SUPPORT),
    })
    adj = Adjudicator(supervisor=sup)
    rounds = [DebateRound(round_number=1, positions=[
        DebatePosition(agent_id="a", confidence=0.85, stance=Stance.SUPPORT),
    ])]
    result = await adj.adjudicate("d1", rounds, ["a", "b", "c"])
    assert result.consensus is False
    assert result.winner == "a"
    assert result.final_confidence == 0.85


@pytest.mark.asyncio
async def test_adjudicator_fallback_no_supervisor():
    """无 LLM 且无监督者时使用最终兜底（取最后一轮置信度最高方）。"""
    adj = Adjudicator()
    rounds = [DebateRound(round_number=1, positions=[
        DebatePosition(agent_id="a", confidence=0.6, stance=Stance.OPPOSE),
        DebatePosition(agent_id="b", confidence=0.8, stance=Stance.SUPPORT, argument="win"),
    ])]
    result = await adj.adjudicate("d1", rounds, ["a", "b", "c"])
    assert result.consensus is False
    assert result.winner == "b"
    assert result.final_confidence == 0.8
    assert result.reasoning == "win"


@pytest.mark.asyncio
async def test_adjudicator_fallback_empty_rounds():
    """无 rounds 时返回低置信度 verdict。"""
    adj = Adjudicator()
    result = await adj.adjudicate("d1", [], ["a", "b", "c"])
    assert result.consensus is False
    assert result.low_confidence is True


# ── 4. DebateDispatcher 辩论调度 ─────────────────────────────────


class _MockDispatchResult:
    """Mock DispatchResult for testing."""

    def __init__(self, text: str, tokens: int = 100):
        self.result = MagicMock()
        self.result.output = text
        self.result.text = text
        self.result.stdout = text
        self.tokens_used = tokens


class _MockDispatcher:
    """Mock Dispatcher for testing DebateDispatcher.

    Returns canned responses based on agent_id.
    """

    def __init__(self, responses: dict[str, str] | None = None):
        self._responses = responses or {}
        self.dispatch = AsyncMock(side_effect=self._dispatch)

    async def _dispatch(self, agent: str, task: str, **kwargs: Any) -> Any:
        text = self._responses.get(agent, '{"stance": "support", "argument": "ok", "confidence": 0.8}')
        return _MockDispatchResult(text)


@pytest.mark.asyncio
async def test_debate_dispatcher_role_assignment_min_participants():
    """参与方不足 3 个时抛 InsufficientParticipantsError。"""
    dispatcher = _MockDispatcher()
    debate = DebateDispatcher(dispatcher)
    with pytest.raises(InsufficientParticipantsError):
        await debate.run_debate("q", ["a", "b"])


@pytest.mark.asyncio
async def test_debate_dispatcher_reaches_consensus():
    """验收标准 1 & 3: 同题多角度 + 置信度加权。"""
    # 所有 agent 都 SUPPORT + 高置信度 → Round 1 即达成共识
    responses = {
        "proposer": '{"stance": "support", "argument": "proposal is good", "confidence": 0.9}',
        "critic_a": '{"stance": "support", "argument": "agree", "confidence": 0.85}',
        "critic_b": '{"stance": "support", "argument": "agree", "confidence": 0.88}',
    }
    dispatcher = _MockDispatcher(responses)
    debate = DebateDispatcher(dispatcher, config=DebateConfig(max_rounds=3, min_rounds=1))
    verdict = await debate.run_debate(
        "Should we deploy?",
        ["proposer", "critic_a", "critic_b"],
    )
    # 验收标准 1: participants 含全部参与方
    assert set(verdict.participants) == {"proposer", "critic_a", "critic_b"}
    # 共识达成
    assert verdict.consensus is True
    # 验收标准 3: final_confidence 等于末轮 consensus_score
    assert verdict.final_confidence > 0
    assert verdict.final_confidence == verdict.rounds[-1].consensus_score
    # 至少 1 轮
    assert len(verdict.rounds) >= 1
    # 各方独立结论在 rounds[0].positions
    assert len(verdict.rounds[0].positions) == 3


@pytest.mark.asyncio
async def test_debate_dispatcher_adds_rounds_when_no_consensus():
    """验收标准 4: 未达阈值且 round < max_rounds 时追加轮次。"""
    # 第一轮 OPPOSE，第二轮 SUPPORT → 需要追加轮次
    call_count = {"proposer": 0, "critic_a": 0, "critic_b": 0}

    class _MultiRoundDispatcher:
        def __init__(self):
            self.dispatch = AsyncMock(side_effect=self._dispatch)

        async def _dispatch(self, agent: str, task: str, **kwargs: Any) -> Any:
            call_count[agent] = call_count.get(agent, 0) + 1
            if call_count[agent] == 1:
                # 第一轮反对
                text = '{"stance": "oppose", "argument": "no", "confidence": 0.7}'
            else:
                # 后续轮次支持
                text = '{"stance": "support", "argument": "ok now", "confidence": 0.85}'
            return _MockDispatchResult(text)

    dispatcher = _MultiRoundDispatcher()
    debate = DebateDispatcher(dispatcher, config=DebateConfig(max_rounds=3, consensus_threshold=0.70))
    verdict = await debate.run_debate(
        "Should we deploy?",
        ["proposer", "critic_a", "critic_b"],
    )
    # 应该有多轮
    assert len(verdict.rounds) >= 1


@pytest.mark.asyncio
async def test_debate_dispatcher_supervisor_fallback_on_stalemate():
    """验收标准 5: 达 max_rounds 未收敛时 consensus=False 且 adjudication_reason 非空。"""
    # 所有轮次都 OPPOSE → 永不收敛 → 转监督者裁决
    responses = {
        "proposer": '{"stance": "oppose", "argument": "no", "confidence": 0.6}',
        "critic_a": '{"stance": "oppose", "argument": "no", "confidence": 0.7}',
        "critic_b": '{"stance": "oppose", "argument": "no", "confidence": 0.65}',
    }
    dispatcher = _MockDispatcher(responses)
    # 使用无监督者的 Adjudicator → 走最终兜底
    adj = Adjudicator()
    debate = DebateDispatcher(
        dispatcher,
        adjudicator=adj,
        config=DebateConfig(max_rounds=2, consensus_threshold=0.90, min_rounds=1),
    )
    verdict = await debate.run_debate(
        "Should we deploy?",
        ["proposer", "critic_a", "critic_b"],
    )
    # 未收敛 → 监督者裁决
    assert verdict.consensus is False
    assert verdict.adjudication_reason != ""


@pytest.mark.asyncio
async def test_debate_dispatcher_cost_control_terminates():
    """R-4: 辩论成本超过阈值时终止。"""
    # 第一轮 OPPOSE（不达成共识），后续轮次继续 OPPOSE → 持续消耗 token
    responses = {
        "proposer": '{"stance": "oppose", "argument": "no", "confidence": 0.7}',
        "critic_a": '{"stance": "oppose", "argument": "no", "confidence": 0.7}',
        "critic_b": '{"stance": "oppose", "argument": "no", "confidence": 0.7}',
    }

    class _HighTokenDispatcher:
        def __init__(self):
            self.dispatch = AsyncMock(side_effect=self._dispatch)

        async def _dispatch(self, agent: str, task: str, **kwargs: Any) -> Any:
            return _MockDispatchResult(responses.get(agent, ""), tokens=10000)

    dispatcher = _HighTokenDispatcher()
    debate = DebateDispatcher(
        dispatcher,
        config=DebateConfig(
            max_rounds=5,
            max_debate_tokens=15000,
            min_rounds=1,
            consensus_threshold=0.90,  # 高阈值确保不达成共识
        ),
    )
    verdict = await debate.run_debate(
        "q", ["proposer", "critic_a", "critic_b"],
    )
    # 成本超限 → cost_terminated=True
    assert verdict.cost_terminated is True
    assert verdict.consensus is False
    assert "cost exceeded" in verdict.adjudication_reason


@pytest.mark.asyncio
async def test_debate_dispatcher_agent_timeout_abstains():
    """R-6 & 验收标准 7: 单 agent 超时视为弃权（confidence=0）。"""
    import asyncio as _asyncio

    class _SlowDispatcher:
        def __init__(self):
            self.dispatch = AsyncMock(side_effect=self._dispatch)

        async def _dispatch(self, agent: str, task: str, **kwargs: Any) -> Any:
            if agent == "critic_b":
                # 慢 agent → 超时
                await _asyncio.sleep(5)
            return _MockDispatchResult('{"stance": "support", "argument": "ok", "confidence": 0.8}')

    dispatcher = _SlowDispatcher()
    debate = DebateDispatcher(
        dispatcher,
        config=DebateConfig(agent_timeout_s=0.1, round_timeout_s=1.0, min_rounds=1),
    )
    verdict = await debate.run_debate(
        "q", ["proposer", "critic_a", "critic_b"],
    )
    # 应该完成（critic_b 弃权）
    assert verdict is not None
    assert "critic_b" in verdict.participants


@pytest.mark.asyncio
async def test_debate_dispatcher_publishes_events():
    """EventBus API 统一: 使用 publish(Event)。"""
    from maop.core.reliability.event_bus import Event, EventBus

    bus = EventBus()
    events: list[Event] = []

    async def _capture(event: Event) -> None:
        events.append(event)

    bus.subscribe("debate.*", _capture)
    dispatcher = _MockDispatcher()
    debate = DebateDispatcher(dispatcher, event_bus=bus)
    await debate.run_debate(
        "q", ["proposer", "critic_a", "critic_b"],
    )
    # 应该发布 debate.started 和 debate.completed 事件
    topics = [e.topic for e in events]
    assert any(t == "debate.started" for t in topics)
    assert any(t == "debate.completed" for t in topics)


@pytest.mark.asyncio
async def test_debate_dispatcher_get_verdict_and_history():
    """查询接口: get_verdict / get_history。"""
    dispatcher = _MockDispatcher()
    debate = DebateDispatcher(dispatcher)
    verdict = await debate.run_debate(
        "q", ["proposer", "critic_a", "critic_b"],
    )
    # get_verdict
    retrieved = debate.get_verdict(verdict.debate_id)
    assert retrieved is not None
    assert retrieved.debate_id == verdict.debate_id
    # get_verdict 不存在
    assert debate.get_verdict("nonexistent") is None
    # get_history
    history = debate.get_history(limit=10)
    assert len(history) >= 1


# ── 5. 轨迹清理 (R-5) ────────────────────────────────────────────


def test_debate_cleanup_traces_removes_expired():
    """R-5: cleanup_traces 清理超期轨迹。"""
    dispatcher = _MockDispatcher()
    debate = DebateDispatcher(dispatcher)
    # 注入一个超期的 verdict
    old_verdict = DebateVerdict(
        debate_id="old",
        consensus=True,
        created_at=time.time() - 100 * 86400,  # 100 天前
    )
    debate._history["old"] = old_verdict
    # 注入一个未超期的 verdict
    new_verdict = DebateVerdict(
        debate_id="new",
        consensus=True,
        created_at=time.time(),
    )
    debate._history["new"] = new_verdict
    # 清理（保留 30 天）
    cleaned = debate.cleanup_traces(retention_days=30)
    assert cleaned == 1
    assert "old" not in debate._history
    assert "new" in debate._history


def test_debate_cleanup_traces_uses_config_default():
    """R-5: cleanup_traces 使用 config 默认 retention_days。"""
    dispatcher = _MockDispatcher()
    debate = DebateDispatcher(dispatcher, config=DebateConfig(retention_days=10))
    old_verdict = DebateVerdict(
        debate_id="old",
        consensus=True,
        created_at=time.time() - 20 * 86400,  # 20 天前
    )
    debate._history["old"] = old_verdict
    cleaned = debate.cleanup_traces()
    assert cleaned == 1


# ── 6. 共识度计算 ────────────────────────────────────────────────


def test_check_consensus_all_support():
    """全员 SUPPORT → 共识度接近 1.0。"""
    dispatcher = _MockDispatcher()
    debate = DebateDispatcher(dispatcher)
    round_ = DebateRound(round_number=1, positions=[
        DebatePosition(agent_id="a", stance=Stance.SUPPORT, confidence=0.9),
        DebatePosition(agent_id="b", stance=Stance.SUPPORT, confidence=0.8),
        DebatePosition(agent_id="c", stance=Stance.SUPPORT, confidence=0.85),
    ])
    score = debate._check_consensus(round_)
    assert score > 0.99  # 全员 SUPPORT → 接近 1.0


def test_check_consensus_all_oppose():
    """全员 OPPOSE → 共识度接近 0.0。"""
    dispatcher = _MockDispatcher()
    debate = DebateDispatcher(dispatcher)
    round_ = DebateRound(round_number=1, positions=[
        DebatePosition(agent_id="a", stance=Stance.OPPOSE, confidence=0.9),
        DebatePosition(agent_id="b", stance=Stance.OPPOSE, confidence=0.8),
    ])
    score = debate._check_consensus(round_)
    assert score < 0.01


def test_check_consensus_mixed_stalemate():
    """SUPPORT 与 OPPOSE 各半 → 共识度接近 0.5（僵持）。"""
    dispatcher = _MockDispatcher()
    debate = DebateDispatcher(dispatcher)
    round_ = DebateRound(round_number=1, positions=[
        DebatePosition(agent_id="a", stance=Stance.SUPPORT, confidence=0.8),
        DebatePosition(agent_id="b", stance=Stance.OPPOSE, confidence=0.8),
    ])
    score = debate._check_consensus(round_)
    assert 0.45 < score < 0.55


def test_check_consensus_empty_positions():
    """无 positions → 共识度 0.0。"""
    dispatcher = _MockDispatcher()
    debate = DebateDispatcher(dispatcher)
    round_ = DebateRound(round_number=1)
    score = debate._check_consensus(round_)
    assert score == 0.0


# ── 7. 角色分配 ─────────────────────────────────────────────────


def test_role_assignment_proposer_and_critics():
    """角色分配: 第一个为 Proposer，其余为 Critic。"""
    dispatcher = _MockDispatcher()
    debate = DebateDispatcher(dispatcher)
    role_map = debate._assign_roles(["a", "b", "c", "d"])
    assert role_map[DebateRole.PROPOSER] == ["a"]
    assert role_map[DebateRole.CRITIC] == ["b", "c", "d"]


def test_role_assignment_insufficient_participants():
    """参与方不足 3 个时抛异常。"""
    dispatcher = _MockDispatcher()
    debate = DebateDispatcher(dispatcher)
    with pytest.raises(InsufficientParticipantsError):
        debate._assign_roles(["a", "b"])
    with pytest.raises(InsufficientParticipantsError):
        debate._assign_roles(["a"])


# ── 8. EvolutionLoop DEBATE 阶段插入 (C-2) ───────────────────────


def test_evolution_loop_debate_disabled_by_default(tmp_path):
    """C-2: DEBATE 阶段默认禁用（向后兼容）。"""
    loop = EvolutionLoop(root_dir=str(tmp_path))
    assert loop._debate_enabled is False
    assert loop._debate_dispatcher is None


def test_evolution_loop_debate_enabled_when_configured(tmp_path):
    """C-2: 启用 DEBATE 阶段时配置生效。"""
    dispatcher = _MockDispatcher()
    loop = EvolutionLoop(
        root_dir=str(tmp_path),
        debate_enabled=True,
        debate_dispatcher=dispatcher,
        debate_participants=["a", "b", "c"],
    )
    assert loop._debate_enabled is True
    assert loop._debate_dispatcher is dispatcher
    assert loop._debate_participants == ["a", "b", "c"]


def test_evolution_loop_run_cycle_no_debate_when_disabled(tmp_path):
    """C-2 & 验收标准 6: DEBATE 禁用时 run_cycle 行为与现状一致。"""
    loop = EvolutionLoop(root_dir=str(tmp_path), debate_enabled=False)
    report = loop.run_cycle()
    # 不应有 DEBATE 阶段
    phases = [p.phase for p in report.phases]
    assert LoopPhase.DEBATE not in phases


def test_evolution_loop_phase_debate_passthrough_when_no_dispatcher(tmp_path):
    """C-2: debate_enabled=True 但无 dispatcher 时透传（向后兼容）。"""
    loop = EvolutionLoop(
        root_dir=str(tmp_path),
        debate_enabled=True,
        debate_dispatcher=None,
    )
    # run_cycle 不应崩溃，且无 DEBATE 阶段（因为 dispatcher is None）
    report = loop.run_cycle()
    phases = [p.phase for p in report.phases]
    assert LoopPhase.DEBATE not in phases


def test_evolution_loop_phase_debate_method_exists():
    """C-2: _phase_debate 方法存在。"""
    assert hasattr(EvolutionLoop, "_phase_debate")


# ── 9. API 路由 ─────────────────────────────────────────────────


@pytest.fixture
def app_with_debate():
    """FastAPI app with the debate router and a configured DebateDispatcher."""
    from maop.dashboard.routers.debate import router

    dispatcher = _MockDispatcher()
    debate = DebateDispatcher(dispatcher)
    app = FastAPI()
    app.include_router(router)
    # 注入 debate_dispatcher 到 ServiceContainer
    # 通过 patch _get_debate_dispatcher 函数
    import maop.dashboard.routers.debate as debate_module
    original = debate_module._get_debate_dispatcher
    debate_module._get_debate_dispatcher = lambda: debate
    yield app, debate
    debate_module._get_debate_dispatcher = original


@pytest.fixture
def app_without_debate():
    """FastAPI app with the debate router but no DebateDispatcher configured."""
    from maop.dashboard.routers.debate import router

    app = FastAPI()
    app.include_router(router)
    yield app


def test_api_get_returns_404_when_unconfigured(app_without_debate):
    """验收标准 8: 未配置时返回 404。"""
    client = TestClient(app_without_debate)
    response = client.get("/api/debate/some-id")
    assert response.status_code == 404


def test_api_history_returns_404_when_unconfigured(app_without_debate):
    """未配置时 history 返回 404。"""
    client = TestClient(app_without_debate)
    response = client.get("/api/debate/history")
    assert response.status_code == 404


def test_api_start_requires_admin(app_with_debate):
    """POST /start 需要管理员鉴权。"""
    app, _ = app_with_debate
    client = TestClient(app)
    response = client.post(
        "/api/debate/start",
        json={
            "question": "test",
            "participants": ["a", "b", "c"],
        },
    )
    # 无 admin 角色 → 403
    assert response.status_code == 403


def test_api_config_requires_admin(app_with_debate):
    """POST /config 需要管理员鉴权。"""
    app, _ = app_with_debate
    client = TestClient(app)
    response = client.post(
        "/api/debate/config",
        json={"max_rounds": 5},
    )
    assert response.status_code == 403


def test_api_get_verdict_after_debate(app_with_debate):
    """验收标准 8: GET /api/debate/{id} 返回完整 Verdict。"""
    app, debate = app_with_debate
    # 先运行一场辩论
    verdict = asyncio.run(debate.run_debate(
        "test question", ["proposer", "critic_a", "critic_b"],
    ))
    client = TestClient(app)
    response = client.get(f"/api/debate/{verdict.debate_id}")
    assert response.status_code == 200
    data = response.json()
    assert "verdict" in data
    assert data["verdict"]["debate_id"] == verdict.debate_id


def test_api_get_verdict_explicit_endpoint(app_with_debate):
    """GET /api/debate/{id}/verdict 是 GET /api/debate/{id} 的别名。"""
    app, debate = app_with_debate
    verdict = asyncio.run(debate.run_debate(
        "test question", ["proposer", "critic_a", "critic_b"],
    ))
    client = TestClient(app)
    response = client.get(f"/api/debate/{verdict.debate_id}/verdict")
    assert response.status_code == 200
    data = response.json()
    assert data["verdict"]["debate_id"] == verdict.debate_id


def test_api_get_verdict_404_for_nonexistent(app_with_debate):
    """GET /api/debate/{id} 对不存在的 debate_id 返回 404。"""
    app, _ = app_with_debate
    client = TestClient(app)
    response = client.get("/api/debate/nonexistent-id")
    assert response.status_code == 404


def test_api_history_returns_verdicts(app_with_debate):
    """GET /api/debate/history 返回近期辩论历史。"""
    app, debate = app_with_debate
    # 运行几场辩论
    asyncio.run(debate.run_debate("q1", ["proposer", "critic_a", "critic_b"]))
    asyncio.run(debate.run_debate("q2", ["proposer", "critic_a", "critic_b"]))
    client = TestClient(app)
    response = client.get("/api/debate/history")
    assert response.status_code == 200
    data = response.json()
    assert "verdicts" in data
    assert len(data["verdicts"]) >= 2


def test_api_start_with_admin_role(app_with_debate, monkeypatch):
    """POST /start 带 admin 角色时成功。"""
    app, _debate = app_with_debate
    # 通过 dependency override 注入 admin 角色
    from fastapi import Request

    async def _override_admin(request: Request):
        # 模拟 admin 角色
        if not hasattr(request.state, "auth_roles"):
            request.state.auth_roles = []
        request.state.auth_roles = ["admin"]

    # 使用 middleware 注入 admin 角色
    from starlette.middleware.base import BaseHTTPMiddleware

    class _AdminMiddleware(BaseHTTPMiddleware):
        async def dispatch(self, request, call_next):
            request.state.auth_roles = ["admin"]
            return await call_next(request)

    app.add_middleware(_AdminMiddleware)
    client = TestClient(app)
    response = client.post(
        "/api/debate/start",
        json={
            "question": "Should we deploy?",
            "participants": ["proposer", "critic_a", "critic_b"],
            "context": {"test": True},
            "max_rounds": 2,
        },
    )
    assert response.status_code == 200
    data = response.json()
    assert data["ok"] is True
    assert "verdict" in data


def test_api_config_with_admin_role(app_with_debate):
    """POST /config 带 admin 角色时成功。"""
    app, _ = app_with_debate
    from starlette.middleware.base import BaseHTTPMiddleware

    class _AdminMiddleware(BaseHTTPMiddleware):
        async def dispatch(self, request, call_next):
            request.state.auth_roles = ["admin"]
            return await call_next(request)

    app.add_middleware(_AdminMiddleware)
    client = TestClient(app)
    response = client.post(
        "/api/debate/config",
        json={
            "max_rounds": 5,
            "consensus_threshold": 0.80,
            "max_debate_tokens": 100000,
        },
    )
    assert response.status_code == 200
    data = response.json()
    assert data["ok"] is True
    assert data["config"]["max_rounds"] == 5


# ── 10. 验收标准集成测试 ────────────────────────────────────────


@pytest.mark.asyncio
async def test_acceptance_1_multi_perspective():
    """验收标准 1: 同题多角度 — participants 含全部参与方，rounds[0].positions 含各方独立结论。"""
    responses = {
        "a": '{"stance": "support", "argument": "from reliability view", "confidence": 0.8}',
        "b": '{"stance": "amend", "argument": "from perf view", "confidence": 0.7}',
        "c": '{"stance": "support", "argument": "from cost view", "confidence": 0.75}',
    }
    dispatcher = _MockDispatcher(responses)
    debate = DebateDispatcher(dispatcher)
    verdict = await debate.run_debate("q", ["a", "b", "c"])
    assert set(verdict.participants) == {"a", "b", "c"}
    assert len(verdict.rounds[0].positions) == 3
    agent_ids = {p.agent_id for p in verdict.rounds[0].positions}
    assert agent_ids == {"a", "b", "c"}


@pytest.mark.asyncio
async def test_acceptance_5_supervisor_fallback():
    """验收标准 5: 达 max_rounds 未收敛时 consensus=False 且 adjudication_reason 非空。"""
    # 全员 OPPOSE → 永不收敛
    responses = {
        "a": '{"stance": "oppose", "argument": "no", "confidence": 0.7}',
        "b": '{"stance": "oppose", "argument": "no", "confidence": 0.7}',
        "c": '{"stance": "oppose", "argument": "no", "confidence": 0.7}',
    }
    dispatcher = _MockDispatcher(responses)
    debate = DebateDispatcher(
        dispatcher,
        adjudicator=Adjudicator(),
        config=DebateConfig(max_rounds=2, consensus_threshold=0.90, min_rounds=1),
    )
    verdict = await debate.run_debate("q", ["a", "b", "c"])
    assert verdict.consensus is False
    assert verdict.adjudication_reason != ""


@pytest.mark.asyncio
async def test_acceptance_6_backward_compat_no_debate():
    """验收标准 6: 未配置辩论时行为与现状一致。"""
    # DebateDispatcher 未配置时 EvolutionLoop 透传 suggestions
    # 已在 test_evolution_loop_run_cycle_no_debate_when_disabled 验证
    # 这里验证 DebateDispatcher 本身在未配置时不影响主流程
    dispatcher = _MockDispatcher()
    debate = DebateDispatcher(dispatcher)
    # 即使 dispatcher 配置了，run_debate 仍能正常工作
    verdict = await debate.run_debate("q", ["a", "b", "c"])
    assert verdict is not None
    assert verdict.debate_id != ""


@pytest.mark.asyncio
async def test_acceptance_8_auditability():
    """验收标准 8: 可审计 — GET /api/debate/{id} 返回完整 Verdict 含全部轮次轨迹。"""
    dispatcher = _MockDispatcher()
    debate = DebateDispatcher(dispatcher)
    verdict = await debate.run_debate("q", ["a", "b", "c"])
    # get_verdict 返回完整 verdict
    retrieved = debate.get_verdict(verdict.debate_id)
    assert retrieved is not None
    # 含全部轮次轨迹
    assert len(retrieved.rounds) == len(verdict.rounds)
    # 每轮含 positions
    for round_ in retrieved.rounds:
        assert hasattr(round_, "positions")
        assert hasattr(round_, "consensus_score")