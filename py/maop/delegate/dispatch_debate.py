"""MAOP 对抗辩论型 Multi-Agent 调度核心（design-debate-agent.md §2.2-§2.4）。

本模块实现对抗辩论（Adversarial Debate）多 Agent 机制的编排器
（ :class:`DebateDispatcher` ），复用现有
:class:`maop.delegate.dispatch_core.Dispatcher` 并行分派各 agent，并通过
:func:`maop.core.scheduling.failure_detector.get_supervisor` 取得监督者
做僵局裁决兜底。

数据模型与异常已提取到 :mod:`maop.delegate.debate_models`，裁决节点
（ :class:`Adjudicator` ）已提取到 :mod:`maop.delegate.debate_verdict` 。
本模块 re-exports 这些符号以保持向后兼容。

设计要点
--------
- **同题多角度并行分析**：对同一问题并行分派多个 agent，各自从不同视角
  独立给出结论与推理链。
- **互相质疑与反驳**：每轮辩论中，各 agent 可看到其他 agent 的结论与
  推理链，并针对其薄弱点提出质疑。
- **置信度加权投票**：每个 agent 结论附带 [0.0, 1.0] 置信度，最终共识
  采用置信度加权投票。
- **未达阈值追加轮次**：当加权共识度低于阈值且未达最大轮次，自动追加
  辩论轮次。
- **裁决节点收敛**：当达到最大轮次仍未收敛，由监督者裁决节点做最终
  判定（``get_supervisor()`` 兜底）。
- **成本控制（R-4）**：辩论成本超过阈值时终止。
- **轨迹清理（R-5）**：辩论结束后清理中间轨迹。
- **超时粒度（R-6）**：每轮超时 + 总超时双重控制。
- **EventBus API 统一**：使用 ``core.reliability.event_bus.EventBus`` 的
  ``publish(Event)`` API（非 ``emit()``）。
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from typing import Any

from maop.delegate.debate_models import (
    CrossExamination,
    DebateConfig,
    DebateCostExceededError,
    DebatePosition,
    DebateRole,
    DebateRound,
    DebateTimeoutError,
    DebateVerdict,
    InsufficientParticipantsError,
    Stance,
)
from maop.delegate.debate_verdict import Adjudicator

logger = logging.getLogger(__name__)


# ── 辩论调度器（DebateDispatcher）──────────────────────────────────


class DebateDispatcher:
    """对抗辩论调度器（design-debate-agent.md §2.2.2 ``DebateOrchestrator``）。

    复用现有 :class:`Dispatcher.dispatch()` 并行分派各 agent，再交由
    :class:`Adjudicator` 收敛。

    Parameters
    ----------
    dispatcher : Dispatcher
        复用现有 Dispatcher.dispatch() 并行分派各 agent。
    adjudicator : Adjudicator | None
        裁决节点。当 None 时懒构造（使用监督者兜底）。
    config : DebateConfig | None
        辩论运行时配置。当 None 时使用默认配置。
    event_bus : EventBus | None
        事件总线。当 None 时使用全局单例 ``get_event_bus()``。
    """

    def __init__(
        self,
        dispatcher: Any,
        *,
        adjudicator: Adjudicator | None = None,
        config: DebateConfig | None = None,
        event_bus: Any | None = None,
    ) -> None:
        self._dispatcher = dispatcher
        self._adjudicator = adjudicator or Adjudicator()
        self._config = config or DebateConfig()
        self._event_bus = event_bus
        # 辩论历史（内存缓存，持久化由调用方或上层模块负责）。
        self._history: dict[str, DebateVerdict] = {}

    def _resolve_event_bus(self) -> Any | None:
        """惰性解析事件总线单例。"""
        if self._event_bus is not None:
            return self._event_bus
        try:
            from maop.core.reliability.event_bus import get_event_bus

            return get_event_bus()
        except Exception as exc:  # pragma: no cover — 防御性兜底
            logger.debug("[debate] get_event_bus failed: %s", exc)
            return None

    async def _publish_event(self, topic: str, data: dict[str, Any]) -> None:
        """通过 EventBus 发布事件（统一使用 publish(Event) API）。"""
        bus = self._resolve_event_bus()
        if bus is None:
            return
        try:
            from maop.core.reliability.event_bus import Event

            await bus.publish(Event(topic=topic, data=data, source="debate"))
        except Exception as exc:  # pragma: no cover — EventBus 失败不应阻断辩论
            logger.debug("[debate] publish event %s failed: %s", topic, exc)

    def _assign_roles(self, participants: list[str]) -> dict[DebateRole, list[str]]:
        """角色分配（design-debate-agent.md §2.4.1）。

        简化版 round_robin 策略：第一个为 Proposer，其余为 Critic。
        至少需要 3 个参与方（1 Proposer + 2 Critic）。
        """
        if len(participants) < 3:
            raise InsufficientParticipantsError(
                f"debate requires ≥3 participants (1 Proposer + 2 Critic), "
                f"got {len(participants)}",
            )
        return {
            DebateRole.PROPOSER: [participants[0]],
            DebateRole.CRITIC: participants[1:],
        }

    def _check_consensus(self, round_: DebateRound) -> float:
        """计算加权共识度（design-debate-agent.md §2.4.3 公式）。

        consensus_score = ( Σ conf_i × stance_weight_i / Σ conf_i + 1 ) / 2

        归一化使结果落在 [0, 1]，0.5 表示完全对立僵持。
        """
        positions = [p for p in round_.positions if p.confidence > 0]
        if not positions:
            return 0.0
        total_conf = sum(p.confidence for p in positions)
        if total_conf <= 0:
            return 0.0
        weighted = sum(p.confidence * p.stance_weight for p in positions)
        return (weighted / total_conf + 1.0) / 2.0

    def _build_prompt(
        self,
        question: str,
        context: dict[str, Any],
        agent_id: str,
        role: DebateRole,
        round_index: int,
        prev_round: DebateRound | None,
    ) -> str:
        """为单个 agent 构造辩论 prompt。

        Round 1：各 agent 仅看到 question + context。
        Round N>1：各 agent 额外看到 prev_round 中其他 agent 的结论与对本方
        的质疑，需在回应中修正结论或反驳质疑。
        """
        role_desc = {
            DebateRole.PROPOSER: "你是提议者（Proposer），请提出方案并附置信度。",
            DebateRole.CRITIC: "你是质疑者（Critic），请从特定视角质疑并附置信度。",
            DebateRole.VERDICT: "你是裁决者（Verdict），请做最终判定。",
        }[role]
        parts = [role_desc, f"\n问题: {question}"]
        if context:
            import json

            parts.append(f"上下文: {json.dumps(context, ensure_ascii=False, default=str)}")
        if prev_round is not None and round_index > 1:
            parts.append("\n上一轮各方结论:")
            for p in prev_round.positions:
                if p.agent_id == agent_id:
                    continue
                parts.append(
                    f"  - agent={p.agent_id}, stance={p.stance.value}, "
                    f"confidence={p.confidence:.2f}, argument={p.argument}",
                )
            critiques_against_me = [
                ce for ce in prev_round.cross_examinations
                if ce.target_id == agent_id
            ]
            if critiques_against_me:
                parts.append("\n对本方的质疑:")
                for ce in critiques_against_me:
                    parts.append(f"  - 来自 {ce.examiner_id}: {ce.critique}")
            parts.append("\n请回应质疑并修正/反驳，给出新的结论与置信度。")
        parts.append(
            "\n请以 JSON 返回: {\"stance\": \"support|oppose|amend\", "
            "\"argument\": \"...\", \"confidence\": 0.0-1.0, "
            "\"evidence\": [\"...\"], \"critiques\": [\"...\"]}",
        )
        return "\n".join(parts)

    def _parse_agent_response(
        self,
        agent_id: str,
        role: DebateRole,
        round_index: int,
        response_text: str,
        tokens_used: int,
    ) -> DebatePosition:
        """解析 agent 返回的 JSON 为 DebatePosition。

        解析失败时退化为 SUPPORT + 0.5 置信度。
        """
        import json

        stance = Stance.SUPPORT
        argument = response_text[:500] if response_text else ""
        confidence = 0.5
        evidence: list[str] = []
        critiques: list[str] = []
        try:
            # 尝试提取 JSON（agent 可能包裹在 markdown ```json 中）
            text = response_text.strip()
            if text.startswith("```"):
                text = text.split("```", 2)[1] if "```" in text[3:] else text
                text = text.removeprefix("json")
            # 找第一个 { 和最后一个 }
            start = text.find("{")
            end = text.rfind("}")
            if start != -1 and end != -1 and end > start:
                data = json.loads(text[start : end + 1])
                stance = Stance(data.get("stance", "support"))
                argument = str(data.get("argument", argument))
                confidence = float(data.get("confidence", 0.5))
                confidence = max(0.0, min(1.0, confidence))
                evidence = list(data.get("evidence", []))
                critiques = list(data.get("critiques", []))
        except Exception as exc:  # pragma: no cover — 解析失败兜底
            logger.debug(
                "[debate] parse agent %s response failed: %s", agent_id, exc,
            )
        return DebatePosition(
            agent_id=agent_id,
            role=role,
            stance=stance,
            argument=argument,
            evidence=evidence,
            confidence=confidence,
            critiques=critiques,
            round_index=round_index,
            tokens_used=tokens_used,
        )

    async def _dispatch_agent(
        self,
        agent_id: str,
        prompt: str,
        routing_key: str,
        trace_id: str,
    ) -> tuple[str, int]:
        """分派单个 agent，返回 (response_text, tokens_used)。

        超时（R-6 单 agent 超时）视为弃权，返回空字符串。
        复用现有 Dispatcher.dispatch()，因此熔断、预算、guardrail、SLA
        计量全部生效。
        """
        try:
            result = await asyncio.wait_for(
                self._dispatcher.dispatch(
                    agent_id,
                    prompt,
                    routing_key=routing_key,
                    trace_id=trace_id,
                ),
                timeout=self._config.agent_timeout_s,
            )
            # DispatchResult.result.output / .text / .stdout
            inner = getattr(result, "result", None)
            text = ""
            if inner is not None:
                text = (
                    getattr(inner, "output", None)
                    or getattr(inner, "text", None)
                    or getattr(inner, "stdout", None)
                    or ""
                )
            tokens = int(getattr(result, "tokens_used", 0) or 0)
            return str(text), tokens
        except asyncio.TimeoutError:
            logger.warning(
                "[debate] agent %s timed out after %ss (abstained)",
                agent_id, self._config.agent_timeout_s,
            )
            return "", 0
        except Exception as exc:  # pragma: no cover — agent 调度失败兜底
            logger.warning("[debate] agent %s dispatch failed: %s", agent_id, exc)
            return "", 0

    async def _run_round(
        self,
        question: str,
        context: dict[str, Any],
        round_index: int,
        prev_round: DebateRound | None,
        role_map: dict[DebateRole, list[str]],
        routing_key: str,
        trace_id: str,
    ) -> DebateRound:
        """执行单轮辩论（design-debate-agent.md §2.2.2 ``_run_round``）。"""
        round_start = time.time()
        # 构造所有 agent 的 prompt 与分派任务
        tasks: list[asyncio.Task[tuple[str, int, str, DebateRole]]] = []
        for role, agents in role_map.items():
            for agent_id in agents:
                prompt = self._build_prompt(
                    question, context, agent_id, role, round_index, prev_round,
                )

                async def _run(
                    aid: str = agent_id,
                    pmt: str = prompt,
                    rl: DebateRole = role,
                ) -> tuple[str, int, str, DebateRole]:
                    text, tokens = await self._dispatch_agent(
                        aid, pmt, routing_key, trace_id,
                    )
                    return text, tokens, aid, rl

                tasks.append(asyncio.create_task(_run()))
        # 整轮超时兜底（R-6）
        try:
            results = await asyncio.wait_for(
                asyncio.gather(*tasks),
                timeout=self._config.round_timeout_s,
            )
        except asyncio.TimeoutError:
            logger.warning(
                "[debate] round %d timed out after %ss",
                round_index, self._config.round_timeout_s,
            )
            # 取消未完成任务，已完成的保留
            for t in tasks:
                if not t.done():
                    t.cancel()
            results = []
            for t in tasks:
                if t.done() and not t.cancelled():
                    try:
                        results.append(t.result())
                    except Exception:  # pragma: no cover
                        # 已取消任务的 result() 异常是预期的，静默忽略
                        pass
        # 构造 positions
        positions: list[DebatePosition] = []
        round_tokens = 0
        for text, tokens, agent_id, role in results:
            pos = self._parse_agent_response(
                agent_id, role, round_index, text, tokens,
            )
            positions.append(pos)
            round_tokens += tokens
        # 构造 cross_examinations（从 critiques 提取）
        cross_exams: list[CrossExamination] = []
        for pos in positions:
            for crit in pos.critiques:
                # 找一个非本方的 agent 作为 target
                target = next(
                    (p.agent_id for p in positions if p.agent_id != pos.agent_id),
                    "",
                )
                if target:
                    cross_exams.append(CrossExamination(
                        examiner_id=pos.agent_id,
                        target_id=target,
                        critique=crit,
                        round_index=round_index,
                    ))
        round_ = DebateRound(
            round_number=round_index,
            positions=positions,
            cross_examinations=cross_exams,
            tokens_used=round_tokens,
            duration_s=round(time.time() - round_start, 3),
        )
        round_.consensus_score = self._check_consensus(round_)
        round_.reached_consensus = round_.consensus_score >= self._config.consensus_threshold
        return round_

    def _check_unanimous_early_exit(self, round_: DebateRound) -> bool:
        """检查是否全员一致支持且平均置信度 ≥ 0.85（允许 Round 1 即退出）。"""
        if not self._config.early_exit_on_unanimous:
            return False
        if not round_.positions:
            return False
        all_support = all(p.stance == Stance.SUPPORT for p in round_.positions)
        if not all_support:
            return False
        avg_conf = sum(p.confidence for p in round_.positions) / len(round_.positions)
        return avg_conf >= 0.85

    async def run_debate(
        self,
        question: str,
        participants: list[str],
        *,
        context: dict[str, Any] | None = None,
        routing_key: str = "",
        trace_id: str = "",
        config_override: DebateConfig | None = None,
    ) -> DebateVerdict:
        """发起一场辩论并返回最终裁决。

        流程：
          1. 角色分配：从 participants 选出 1 个 Proposer + N 个 Critic。
          2. Round 1：并行 dispatch 各 agent，收集 DebatePosition。
          3. 收敛检查：计算加权共识度。
          4. 未达阈值且 round < max_rounds → 注入质疑与回应，进入下一轮。
          5. 达阈值 → 返回 Verdict(consensus=True)。
          6. 达 max_rounds 仍未收敛 → 转交 Adjudicator.adjudicate() 裁决。
          7. 成本超限（R-4）→ 提前终止并标记 cost_terminated。
        """
        cfg = config_override or self._config
        ctx = context or {}
        debate_id = uuid.uuid4().hex[:12]
        # 角色分配
        role_map = self._assign_roles(participants)
        # 发布辩论开始事件
        await self._publish_event("debate.started", {
            "debate_id": debate_id,
            "question": question[:200],
            "participants": participants,
            "config": cfg.model_dump(),
        })
        rounds: list[DebateRound] = []
        total_tokens = 0
        cost_terminated = False
        prev_round: DebateRound | None = None
        try:
            for round_index in range(1, cfg.max_rounds + 1):
                # 成本控制（R-4）
                if total_tokens >= cfg.max_debate_tokens:
                    logger.warning(
                        "[debate] %s cost exceeded %d tokens, terminating",
                        debate_id, cfg.max_debate_tokens,
                    )
                    cost_terminated = True
                    break
                # 执行一轮
                round_ = await self._run_round(
                    question, ctx, round_index, prev_round, role_map,
                    routing_key, trace_id,
                )
                rounds.append(round_)
                total_tokens += round_.tokens_used
                prev_round = round_
                # 发布轮次完成事件
                await self._publish_event("debate.round_completed", {
                    "debate_id": debate_id,
                    "round_number": round_index,
                    "consensus_score": round_.consensus_score,
                    "tokens_used": round_.tokens_used,
                })
                # 收敛检查
                if round_.reached_consensus and round_index >= cfg.min_rounds:
                    logger.info(
                        "[debate] %s reached consensus at round %d (score=%.3f)",
                        debate_id, round_index, round_.consensus_score,
                    )
                    break
                # 全员一致提前退出（非强制 min_rounds 场景）
                if (
                    self._check_unanimous_early_exit(round_)
                    and round_index >= cfg.min_rounds
                ):
                    logger.info(
                        "[debate] %s unanimous early exit at round %d",
                        debate_id, round_index,
                    )
                    break
            # 构造 verdict
            if rounds and rounds[-1].reached_consensus and not cost_terminated:
                verdict = self._build_consensus_verdict(
                    debate_id, rounds, participants, total_tokens,
                )
            else:
                # 未收敛 → 裁决节点
                if cost_terminated:
                    verdict = self._build_cost_terminated_verdict(
                        debate_id, rounds, participants, total_tokens,
                    )
                else:
                    verdict = await self._adjudicator.adjudicate(
                        debate_id, rounds, participants,
                    )
                    verdict.rounds = rounds
                    verdict.participants = participants
                    verdict.total_tokens = total_tokens
            # 缓存历史
            self._history[debate_id] = verdict
            # 发布辩论完成事件
            await self._publish_event("debate.completed", {
                "debate_id": debate_id,
                "consensus": verdict.consensus,
                "final_confidence": verdict.final_confidence,
                "total_tokens": total_tokens,
                "cost_terminated": cost_terminated,
            })
            return verdict
        except InsufficientParticipantsError:
            raise
        except Exception as exc:  # pragma: no cover — 防御性兜底
            logger.exception("[debate] %s failed", debate_id)
            return DebateVerdict(
                debate_id=debate_id,
                consensus=False,
                low_confidence=True,
                participants=participants,
                rounds=rounds,
                adjudication_reason=f"debate failed: {exc}",
                total_tokens=total_tokens,
            )

    def _build_consensus_verdict(
        self,
        debate_id: str,
        rounds: list[DebateRound],
        participants: list[str],
        total_tokens: int,
    ) -> DebateVerdict:
        """构造共识达成的 verdict。"""
        last = rounds[-1]
        # 取置信度最高且 SUPPORT/AMEND 的方为 winner
        supporting = [
            p for p in last.positions
            if p.stance in (Stance.SUPPORT, Stance.AMEND)
        ]
        if supporting:
            winner_pos = max(supporting, key=lambda p: p.confidence)
        elif last.positions:
            winner_pos = max(last.positions, key=lambda p: p.confidence)
        else:
            winner_pos = DebatePosition(agent_id="", argument="")
        dissenting = [
            p.agent_id for p in last.positions
            if p.agent_id != winner_pos.agent_id and p.stance == Stance.OPPOSE
        ]
        return DebateVerdict(
            debate_id=debate_id,
            consensus=True,
            low_confidence=last.consensus_score < 0.85,
            winner=winner_pos.agent_id,
            reasoning=winner_pos.argument,
            final_confidence=last.consensus_score,
            winning_stance=winner_pos.stance,
            participants=participants,
            rounds=rounds,
            dissenting_opinions=dissenting,
            total_tokens=total_tokens,
        )

    def _build_cost_terminated_verdict(
        self,
        debate_id: str,
        rounds: list[DebateRound],
        participants: list[str],
        total_tokens: int,
    ) -> DebateVerdict:
        """构造因成本超限终止的 verdict（R-4）。"""
        last = rounds[-1] if rounds else None
        winner_id = ""
        reasoning = "cost exceeded; degraded to single-agent decision"
        final_confidence = 0.0
        winning_stance = Stance.SUPPORT
        if last and last.positions:
            best = max(last.positions, key=lambda p: p.confidence)
            winner_id = best.agent_id
            reasoning = best.argument
            final_confidence = best.confidence
            winning_stance = best.stance
        return DebateVerdict(
            debate_id=debate_id,
            consensus=False,
            low_confidence=True,
            winner=winner_id,
            reasoning=reasoning,
            final_confidence=final_confidence,
            winning_stance=winning_stance,
            participants=participants,
            rounds=rounds,
            adjudication_reason=f"cost exceeded {total_tokens} tokens",
            cost_terminated=True,
            total_tokens=total_tokens,
        )

    # ── 查询接口 ────────────────────────────────────────────────

    def get_verdict(self, debate_id: str) -> DebateVerdict | None:
        """查询某场辩论的裁决结果。"""
        return self._history.get(debate_id)

    def get_history(self, limit: int = 20) -> list[DebateVerdict]:
        """查询近期辩论历史。"""
        return list(self._history.values())[-limit:]

    # ── 轨迹清理（R-5）──────────────────────────────────────────

    def cleanup_traces(self, *, retention_days: int | None = None) -> int:
        """清理超期辩论轨迹（R-5）。

        辩论结束后清理中间轨迹，保留 ``retention_days`` 天的记录。
        返回被清理的记录数。
        """
        days = retention_days or self._config.retention_days
        cutoff = time.time() - days * 86400
        to_remove = [
            did for did, v in self._history.items() if v.created_at < cutoff
        ]
        for did in to_remove:
            del self._history[did]
        if to_remove:
            logger.info(
                "[debate] cleaned up %d expired traces (retention=%d days)",
                len(to_remove), days,
            )
        return len(to_remove)


__all__ = [
    "Adjudicator",
    "CrossExamination",
    "DebateConfig",
    "DebateCostExceededError",
    "DebateDispatcher",
    "DebatePosition",
    "DebateRole",
    "DebateRound",
    "DebateTimeoutError",
    "DebateVerdict",
    "InsufficientParticipantsError",
    "Stance",
]
