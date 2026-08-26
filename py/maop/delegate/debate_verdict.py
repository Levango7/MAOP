"""MAOP 对抗辩论裁决节点（design-debate-agent.md §2.2.3）。

从 ``dispatch_debate.py`` 提取，使裁决逻辑独立于辩论调度器。
``dispatch_debate.py`` re-exports :class:`Adjudicator` 以保持向后兼容。
"""

from __future__ import annotations

import logging
from typing import Any

from maop.delegate.debate_models import (
    DebateRound,
    DebateVerdict,
    Stance,
)

logger = logging.getLogger(__name__)


class Adjudicator:
    """裁决节点：可配置为 LLM 裁决或监督者裁决兜底。

    当 LLM 裁决不可用（``llm_judge=None``）时，回退到监督者裁决
    （使用 P0 已实施的 ``get_supervisor()``）。
    """

    def __init__(
        self,
        *,
        llm_judge: Any | None = None,
        supervisor: Any | None = None,
    ) -> None:
        self._llm_judge = llm_judge
        self._supervisor = supervisor

    def _resolve_supervisor(self) -> Any | None:
        """惰性解析监督者单例。"""
        if self._supervisor is not None:
            return self._supervisor
        try:
            from maop.core.scheduling.failure_detector import get_supervisor

            return get_supervisor()
        except Exception as exc:  # pragma: no cover — 防御性兜底
            logger.debug("[adjudicator] get_supervisor failed: %s", exc)
            return None

    async def adjudicate(
        self,
        debate_id: str,
        rounds: list[DebateRound],
        participants: list[str],
    ) -> DebateVerdict:
        """对未收敛的辩论做最终裁决。

        优先使用 LLM 裁决（若配置）；否则回退到监督者裁决。
        """
        # 1. 尝试 LLM 裁决
        if self._llm_judge is not None:
            try:
                result = await self._llm_judge(rounds)
                if isinstance(result, DebateVerdict):
                    return result
            except Exception as exc:  # pragma: no cover — LLM 裁决失败兜底
                logger.warning(
                    "[adjudicator] LLM judge failed, falling back to supervisor: %s",
                    exc,
                )

        # 2. 回退到监督者裁决
        sup = self._resolve_supervisor()
        if sup is not None and hasattr(sup, "adjudicate"):
            try:
                # Supervisor.adjudicate 接受 list[Any]（每轮 positions 列表）
                rounds_for_sup: list[Any] = [
                    [p for p in r.positions] for r in rounds
                ]
                sup_result = await sup.adjudicate(debate_id, rounds_for_sup)
                return self._build_verdict_from_supervisor(
                    debate_id, rounds, participants, sup_result,
                )
            except Exception as exc:  # pragma: no cover — 监督者裁决失败兜底
                logger.warning(
                    "[adjudicator] supervisor adjudicate failed: %s", exc,
                )

        # 3. 最终兜底：取最后一轮置信度最高方
        return self._fallback_verdict(debate_id, rounds, participants)

    def _build_verdict_from_supervisor(
        self,
        debate_id: str,
        rounds: list[DebateRound],
        participants: list[str],
        sup_result: dict[str, Any],
    ) -> DebateVerdict:
        """将监督者裁决结果转为 DebateVerdict。"""
        winner_obj = sup_result.get("winner")
        winner_id = ""
        winning_stance = Stance.SUPPORT
        final_confidence = 0.0
        if winner_obj is not None:
            winner_id = (
                getattr(winner_obj, "agent_id", "")
                or (winner_obj.get("agent_id", "") if isinstance(winner_obj, dict) else "")
            )
            final_confidence = float(
                getattr(winner_obj, "confidence", 0.0)
                or (winner_obj.get("confidence", 0.0) if isinstance(winner_obj, dict) else 0.0)
            )
            stance_val = (
                getattr(winner_obj, "stance", None)
                or (winner_obj.get("stance") if isinstance(winner_obj, dict) else None)
            )
            if stance_val is not None:
                try:
                    winning_stance = Stance(stance_val)
                except (KeyError, ValueError):
                    winning_stance = Stance.SUPPORT
        return DebateVerdict(
            debate_id=debate_id,
            consensus=False,
            low_confidence=bool(sup_result.get("low_confidence", True)),
            winner=winner_id,
            reasoning=str(sup_result.get("adjudication_reason", "")),
            final_confidence=final_confidence,
            winning_stance=winning_stance,
            participants=participants,
            rounds=rounds,
            dissenting_opinions=[p for p in participants if p != winner_id],
            supervisor_action=str(sup_result.get("supervisor_action", "")),
            adjudication_reason=str(sup_result.get("adjudication_reason", "")),
        )

    def _fallback_verdict(
        self,
        debate_id: str,
        rounds: list[DebateRound],
        participants: list[str],
    ) -> DebateVerdict:
        """最终兜底：取最后一轮置信度最高方。"""
        if not rounds:
            return DebateVerdict(
                debate_id=debate_id,
                consensus=False,
                low_confidence=True,
                participants=participants,
                rounds=rounds,
                adjudication_reason="no rounds provided",
            )
        last = rounds[-1]
        if not last.positions:
            return DebateVerdict(
                debate_id=debate_id,
                consensus=False,
                low_confidence=True,
                participants=participants,
                rounds=rounds,
                adjudication_reason="no positions in last round",
            )
        best = max(last.positions, key=lambda p: p.confidence)
        return DebateVerdict(
            debate_id=debate_id,
            consensus=False,
            low_confidence=best.confidence < 0.70,
            winner=best.agent_id,
            reasoning=best.argument,
            final_confidence=best.confidence,
            winning_stance=best.stance,
            participants=participants,
            rounds=rounds,
            dissenting_opinions=[p.agent_id for p in last.positions if p.agent_id != best.agent_id],
            adjudication_reason="fallback: highest confidence in last round",
        )