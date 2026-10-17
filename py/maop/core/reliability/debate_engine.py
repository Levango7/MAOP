"""MAOP Blackboard — Multi-Agent Debate Consensus Engine.

Complements the blackboard trigger-rule paradigm with a **deliberate debate
loop**: multiple agents examine the same question on the blackboard and vote.
Consensus is reached when the weighted confidence exceeds a threshold or a
quorum is met.

Classes
-------
- ``DebateBallot``: a single agent's opinion in a debate.
- ``DebateResult``: outcome of a debate round.
- ``DebateEngine``: runs one or more debate rounds on the blackboard.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, cast

from maop.core.reliability.blackboard import Blackboard, BlackboardEntry

logger = logging.getLogger(__name__)


# ── Ballot ────────────────────────────────────────────────────────


class Stance(str, Enum):
    """Vote stance in a debate."""

    AGREE = "agree"
    DISAGREE = "disagree"
    ABSTAIN = "abstain"


@dataclass
class DebateBallot:
    """A single agent's vote.

    Attributes
    ----------
    agent : str
        Agent identifier (name).
    stance : Stance
        agree / disagree / abstain.
    confidence : float
        [0.0, 1.0] — how certain the agent is.
    rationale : str
        Free-text reasoning (optional).
    metadata : dict[str, Any]
        Extra data (e.g. model used, token cost).
    """

    agent: str
    stance: Stance
    confidence: float = 1.0
    rationale: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)
    timestamp: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )

    @property
    def weighted_confidence(self) -> float:
        """Signed confidence: +confidence for agree, -confidence for disagree."""
        match self.stance:
            case Stance.AGREE:
                return self.confidence
            case Stance.DISAGREE:
                return -self.confidence
            case _:
                return 0.0


# ── Debate result ─────────────────────────────────────────────────


@dataclass
class DebateResult:
    """Outcome of a debate session."""

    debate_id: str
    question_domain: str
    question_entry_id: str
    ballots: list[DebateBallot] = field(default_factory=list)
    final_stance: Stance = Stance.ABSTAIN
    consensus_score: float = 0.0
    rounds: int = 1
    converged: bool = False
    duration_ms: float = 0.0

    def summarize(self) -> dict[str, Any]:
        agree = sum(1 for b in self.ballots if b.stance == Stance.AGREE)
        disagree = sum(1 for b in self.ballots if b.stance == Stance.DISAGREE)
        abstain = sum(1 for b in self.ballots if b.stance == Stance.ABSTAIN)
        return {
            "debate_id": self.debate_id,
            "question_entry_id": self.question_entry_id,
            "final_stance": self.final_stance.value,
            "consensus_score": round(self.consensus_score, 4),
            "converged": self.converged,
            "rounds": self.rounds,
            "ballot_count": len(self.ballots),
            "vote_breakdown": {
                "agree": agree,
                "disagree": disagree,
                "abstain": abstain,
            },
        }


# ── Debate participant ────────────────────────────────────────────

# A participant is callable with ``(bb, question_entry)`` and returns
# a ``DebateBallot``.

DebateFn = Callable[
    [Blackboard, BlackboardEntry],
    Awaitable[DebateBallot] | DebateBallot,
]


# ── DebateEngine ──────────────────────────────────────────────────


class DebateEngine:
    """Runs multi-agent debate on a blackboard question.

    Usage::

        engine = DebateEngine(
            bb=blackboard,
            consensus_threshold=0.6,
            quorum=2,
        )
        engine.register_participant("agent_a", agent_a_vote)
        engine.register_participant("agent_b", agent_b_vote)
        engine.register_participant("agent_c", agent_c_vote)
        result = await engine.debate(question_entry)
    """

    def __init__(
        self,
        bb: Blackboard,
        *,
        consensus_threshold: float = 0.6,
        quorum: int = 2,
        max_rounds: int = 3,
        concurrency: int = 4,
    ) -> None:
        """
        Parameters
        ----------
        bb : Blackboard
            Shared blackboard.
        consensus_threshold : float
            Weighted-confidence magnitude required to declare consensus.
            Range (0.0, 1.0].
        quorum : int
            Minimum ballots required before consensus can be declared.
        max_rounds : int
            Maximum debate rounds.
        concurrency : int
            Max concurrent ballots per round.
        """
        self._bb = bb
        self._threshold = consensus_threshold
        self._quorum = quorum
        self._max_rounds = max_rounds
        self._sem = asyncio.Semaphore(concurrency)
        self._participants: dict[str, DebateFn] = {}

    # ── Participant registry ────────────────────────────────────

    def register_participant(self, name: str, fn: DebateFn) -> None:
        self._participants[name] = fn

    def unregister_participant(self, name: str) -> None:
        self._participants.pop(name, None)

    @property
    def participants(self) -> list[str]:
        return list(self._participants.keys())

    # ── Debate lifecycle ────────────────────────────────────────

    async def debate(self, question_entry: BlackboardEntry) -> DebateResult:
        """Run a full debate around *question_entry*.

        Returns the final ``DebateResult`` after convergence or ``max_rounds``.
        """
        import time

        debate_id = uuid.uuid4().hex[:12]
        start = time.monotonic()
        all_ballots: list[DebateBallot] = []
        prev_stance = Stance.ABSTAIN
        converged = False
        final_stance = Stance.ABSTAIN
        score = 0.0

        for round_idx in range(1, self._max_rounds + 1):
            round_ballots = await self._run_round(question_entry, debate_id, round_idx)
            all_ballots.extend(round_ballots)
            final_stance, score = self._tally(all_ballots)

            if (
                len(all_ballots) >= self._quorum
                and abs(score) >= self._threshold
            ):
                converged = True
                logger.info(
                    "[debate %s] round %d: consensus %s (%.3f)",
                    debate_id, round_idx, final_stance.value, score,
                )
                break

            # Detect stance flip → not converging
            if round_idx > 1 and final_stance != prev_stance:
                logger.debug(
                    "[debate %s] round %d: stance changed %s → %s",
                    debate_id, round_idx, prev_stance.value, final_stance.value,
                )
            prev_stance = final_stance

        duration = (time.monotonic() - start) * 1000.0

        result = DebateResult(
            debate_id=debate_id,
            question_domain=question_entry.domain,
            question_entry_id=question_entry.entry_id,
            ballots=all_ballots,
            final_stance=final_stance,
            consensus_score=score,
            rounds=round_idx,
            converged=converged,
            duration_ms=duration,
        )
        logger.info(
            "[debate %s] finished: converged=%s, stance=%s, ballots=%d, rounds=%d, %.1fms",
            debate_id, converged, final_stance.value, len(all_ballots), round_idx, duration,
        )
        return result

    async def _run_round(
        self,
        question: BlackboardEntry,
        debate_id: str,
        round_idx: int,
    ) -> list[DebateBallot]:
        """Gather one round of ballots from all participants."""
        sem = self._sem

        async def _collect(name: str, fn: DebateFn) -> DebateBallot | None:
            async with sem:
                try:
                    ballot = fn(self._bb, question)
                    if asyncio.iscoroutine(ballot):
                        ballot = await ballot
                    return cast("DebateBallot | None", ballot)
                except Exception as exc:
                    logger.error(
                        "[debate %s] participant %s round %d error: %s",
                        debate_id, name, round_idx, exc,
                    )
                    return None

        tasks = [
            _collect(name, fn) for name, fn in self._participants.items()
        ]
        results = await asyncio.gather(*tasks, return_exceptions=False)
        return [b for b in results if b is not None]

    @staticmethod
    def _tally(ballots: list[DebateBallot]) -> tuple[Stance, float]:
        """Compute weighted consensus from ballots.

        Returns ``(final_stance, score)`` where score is in [-1, 1].
        """
        if not ballots:
            return Stance.ABSTAIN, 0.0

        total = sum(b.weighted_confidence for b in ballots)
        avg = total / len(ballots)

        if avg > 0:
            return Stance.AGREE, min(avg, 1.0)
        elif avg < 0:
            return Stance.DISAGREE, max(avg, -1.0)
        return Stance.ABSTAIN, 0.0

    # ── Convenience: write result to blackboard ─────────────────

    async def debate_and_record(
        self,
        question_entry: BlackboardEntry,
        result_domain: str = "decision",
        contributor: str = "debate_engine",
    ) -> DebateResult:
        """Debate + write the consensus as a new blackboard entry."""
        result = await self.debate(question_entry)
        summary = result.summarize()
        await self._bb.write(
            domain=result_domain,
            content={
                "debate_id": result.debate_id,
                "question_entry_id": result.question_entry_id,
                "final_stance": result.final_stance.value,
                "consensus_score": result.consensus_score,
                "converged": result.converged,
                "vote_breakdown": summary["vote_breakdown"],
                "ballots": [
                    {
                        "agent": b.agent,
                        "stance": b.stance.value,
                        "confidence": b.confidence,
                        "rationale": b.rationale,
                    }
                    for b in result.ballots
                ],
            },
            contributor=contributor,
            metadata={
                "state": "done" if result.converged else "inconclusive",
                "debate_id": result.debate_id,
                "rounds": result.rounds,
            },
        )
        return result
