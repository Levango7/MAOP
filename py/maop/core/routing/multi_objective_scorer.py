"""MAOP Multi-Objective Scorer — Pareto frontier + TOPSIS ranking.

Phase γ-3: replaces single weighted-sum routing scores with a multi-objective
optimization that respects the trade-offs between competing goals
(success_rate, latency, cost, quota_headroom).

Pipeline
--------
1. ``compute_pareto_frontier`` — non-dominated sort over the candidate set
   (min-max normalized internally so latency_ms / cost_usd can be compared
   on the same scale as the already-normalized 0-1 dimensions).
2. ``compute_topsis_score`` — for each candidate, compute the relative
   closeness to the *ideal* (best on every dimension) and away from the
   *nadir* (worst on every dimension), producing a single score in [0, 1].
3. ``rank_agents`` — keep only Pareto-optimal agents, then sort them by
   their TOPSIS score (descending). Non-Pareto agents are dropped so the
   router never picks a dominated option.

Direction of optimization (越大越好 / 越小越好):
    - success_rate     : maximize  (already in [0, 1])
    - latency_ms       : minimize  (normalized to [0, 1] inside the scorer)
    - cost_usd         : minimize  (normalized to [0, 1] inside the scorer)
    - quota_headroom   : maximize  (already in [0, 1]; 1 = full quota)

Usage::

    from maop.core.routing.multi_objective_scorer import (
        AgentObjectiveVector, ObjectiveWeights, MultiObjectiveScorer,
    )

    candidates = {
        "claude": AgentObjectiveVector(0.95, 2000, 0.05, 0.9),
        "kimi":   AgentObjectiveVector(0.80, 1500, 0.01, 0.7),
    }
    scorer = MultiObjectiveScorer()
    ranking = scorer.rank_agents(candidates)
    # [("claude", 0.78), ("kimi", 0.62)]
"""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


# ── Data Models ────────────────────────────────────────────────────

@dataclass
class ObjectiveWeights:
    """Weights for the four routing objectives.

    The defaults sum to 1.0; ``normalize()`` re-scales any user-provided
    weights so their sum is exactly 1.0 (defensive against drift from the
    strategy learner, which may tune weights at runtime).
    """

    success_rate: float = 0.4
    latency: float = 0.3
    cost: float = 0.2
    quota_headroom: float = 0.1

    def normalize(self) -> ObjectiveWeights:
        """Return a copy re-scaled so the four weights sum to 1.0.

        If all weights are zero (degenerate input), returns the default
        weighting so callers never divide by zero downstream.
        """
        total = self.success_rate + self.latency + self.cost + self.quota_headroom
        if total <= 0:
            return ObjectiveWeights()  # safe defaults
        if math.isclose(total, 1.0):
            return ObjectiveWeights(
                success_rate=self.success_rate,
                latency=self.latency,
                cost=self.cost,
                quota_headroom=self.quota_headroom,
            )
        return ObjectiveWeights(
            success_rate=self.success_rate / total,
            latency=self.latency / total,
            cost=self.cost / total,
            quota_headroom=self.quota_headroom / total,
        )

    @property
    def total(self) -> float:
        return self.success_rate + self.latency + self.cost + self.quota_headroom


@dataclass
class AgentObjectiveVector:
    """Per-agent raw objective values.

    ``latency_ms`` and ``cost_usd`` are raw physical quantities (the scorer
    min-max normalizes them against the candidate set). ``success_rate`` and
    ``quota_headroom`` must already be in [0, 1].
    """

    success_rate: float
    latency_ms: float
    cost_usd: float
    quota_headroom: float


@dataclass
class ParetoFrontierResult:
    """Result of a Pareto non-dominated sort."""

    dominant_agents: list[str] = field(default_factory=list)
    dominated_agents: list[str] = field(default_factory=list)
    frontier_size: int = 0


# ── Scorer ─────────────────────────────────────────────────────────

class MultiObjectiveScorer:
    """Pareto + TOPSIS multi-objective agent ranker.

    Parameters
    ----------
    weights : ObjectiveWeights | None
        Default weights used by :meth:`rank_agents` when no override is
        supplied. May be mutated by the strategy learner between calls;
        ``rank_agents`` always re-normalizes before use.
    """

    # Direction map: True = maximize, False = minimize.  Order MUST match
    # the field order of AgentObjectiveVector / ObjectiveWeights so we can
    # zip them together when normalizing.
    _MAXIMIZE = ("success_rate", "quota_headroom")
    _MINIMIZE = ("latency_ms", "cost_usd")

    def __init__(self, weights: ObjectiveWeights | None = None) -> None:
        self._weights = weights if weights is not None else ObjectiveWeights()

    # ── Pareto ──────────────────────────────────────────────────

    def compute_pareto_frontier(
        self, candidates: dict[str, AgentObjectiveVector]
    ) -> ParetoFrontierResult:
        """Return the non-dominated subset of ``candidates``.

        A candidate ``a`` is *dominated* by ``b`` iff ``b`` is at least as
        good on every dimension and strictly better on at least one.  The
        Pareto frontier is the set of candidates not dominated by any
        other candidate.

        Raw latency / cost are min-max normalized against the candidate
        set first; success_rate and quota_headroom are already in [0, 1].
        """
        if not candidates:
            return ParetoFrontierResult()

        normalized = self._normalize_candidates(candidates)

        dominant: list[str] = []
        dominated: list[str] = []

        for name_a in candidates:
            dominated_by_some = False
            for name_b in candidates:
                if name_a == name_b:
                    continue
                if self.is_dominated(normalized[name_a], normalized[name_b]):
                    dominated_by_some = True
                    break
            if dominated_by_some:
                dominated.append(name_a)
            else:
                dominant.append(name_a)

        return ParetoFrontierResult(
            dominant_agents=dominant,
            dominated_agents=dominated,
            frontier_size=len(dominant),
        )

    def is_dominated(
        self, a: AgentObjectiveVector, b: AgentObjectiveVector
    ) -> bool:
        """Return True iff ``a`` is dominated by ``b``.

        Assumes both vectors are *already normalized* to [0, 1] on every
        dimension (use :meth:`_normalize_candidates` first).  ``a`` is
        dominated by ``b`` when:

            - ``b`` is >= ``a`` on success_rate and quota_headroom, AND
            - ``b`` is <= ``a`` on latency_ms and cost_usd, AND
            - at least one of those inequalities is strict.

        Edge case: identical vectors do not dominate each other (the
        strict-inequality requirement).
        """
        b_better_eq = (
            b.success_rate >= a.success_rate
            and b.quota_headroom >= a.quota_headroom
            and b.latency_ms <= a.latency_ms
            and b.cost_usd <= a.cost_usd
        )
        if not b_better_eq:
            return False
        b_strictly_better = (
            b.success_rate > a.success_rate
            or b.quota_headroom > a.quota_headroom
            or b.latency_ms < a.latency_ms
            or b.cost_usd < a.cost_usd
        )
        return b_strictly_better

    # ── TOPSIS ──────────────────────────────────────────────────

    def compute_topsis_score(
        self,
        vector: AgentObjectiveVector,
        weights: ObjectiveWeights,
        ideal: AgentObjectiveVector,
        nadir: AgentObjectiveVector,
    ) -> float:
        """TOPSIS relative-closeness score in [0, 1] (1 = ideal).

        All inputs must be on the same scale (already normalized to [0, 1]
        on every dimension).  The score is:

            C = D_nadir / (D_ideal + D_nadir)

        where ``D_ideal`` is the weighted Euclidean distance from ``vector``
        to the ideal point, and ``D_nadir`` is the distance to the nadir.
        """
        w = weights.normalize()

        # Convert all four dimensions to a "higher = better" form so we
        # can use uniform weighted Euclidean distance.  For latency / cost
        # we invert (1 - value) because the raw normalized form is
        # "lower = better".
        v_higher = (
            vector.success_rate,
            1.0 - vector.latency_ms,
            1.0 - vector.cost_usd,
            vector.quota_headroom,
        )
        i_higher = (
            ideal.success_rate,
            1.0 - ideal.latency_ms,
            1.0 - ideal.cost_usd,
            ideal.quota_headroom,
        )
        n_higher = (
            nadir.success_rate,
            1.0 - nadir.latency_ms,
            1.0 - nadir.cost_usd,
            nadir.quota_headroom,
        )
        w_vec = (
            w.success_rate,
            w.latency,
            w.cost,
            w.quota_headroom,
        )

        d_ideal_sq = sum(
            weight * (v - i) ** 2
            for v, i, weight in zip(v_higher, i_higher, w_vec)
        )
        d_nadir_sq = sum(
            weight * (v - n) ** 2
            for v, n, weight in zip(v_higher, n_higher, w_vec)
        )

        d_ideal = math.sqrt(d_ideal_sq)
        d_nadir = math.sqrt(d_nadir_sq)

        if d_ideal + d_nadir <= 0:
            # Degenerate: ideal == nadir == vector (all candidates identical).
            # Treat as fully optimal (closeness = 1).
            return 1.0
        return d_nadir / (d_ideal + d_nadir)

    # ── End-to-end ranking ──────────────────────────────────────

    def rank_agents(
        self,
        candidates: dict[str, AgentObjectiveVector],
        weights: ObjectiveWeights | None = None,
    ) -> list[tuple[str, float]]:
        """Full Pareto + TOPSIS ranking.

        1. Compute the Pareto frontier — dominated agents are dropped.
        2. Among the frontier, compute each agent's TOPSIS score against
           the ideal/nadir derived from the *frontier* itself.
        3. Return ``[(agent, score)]`` sorted by score descending.

        Edge cases:
            - Empty input → ``[]``.
            - Single candidate → ``[(name, 1.0)]`` (frontier of one).
            - All candidates Pareto-optimal (identical or non-comparable)
              → all ranked by TOPSIS, ties resolved by original order.
        """
        if not candidates:
            return []

        weights = (weights if weights is not None else self._weights).normalize()

        frontier = self.compute_pareto_frontier(candidates)
        if not frontier.dominant_agents:
            # Should not happen (≥1 candidate ⇒ ≥1 frontier member), but
            # guard defensively: fall back to ranking the full set.
            frontier_names = list(candidates.keys())
        else:
            frontier_names = frontier.dominant_agents

        # Build the normalized frontier subset for ideal/nadir computation.
        frontier_normalized = self._normalize_candidates(
            {n: candidates[n] for n in frontier_names}
        )

        # Ideal = best value on each dim across the frontier.
        # Nadir = worst value on each dim across the frontier.
        # For "minimize" dims (latency_ms, cost_usd), best = min, worst = max.
        # For "maximize" dims (success_rate, quota_headroom), best = max, worst = min.
        dims = frontier_normalized.values()
        ideal = AgentObjectiveVector(
            success_rate=max(d.success_rate for d in dims),
            latency_ms=min(d.latency_ms for d in dims),
            cost_usd=min(d.cost_usd for d in dims),
            quota_headroom=max(d.quota_headroom for d in dims),
        )
        nadir = AgentObjectiveVector(
            success_rate=min(d.success_rate for d in dims),
            latency_ms=max(d.latency_ms for d in dims),
            cost_usd=max(d.cost_usd for d in dims),
            quota_headroom=min(d.quota_headroom for d in dims),
        )

        ranked: list[tuple[str, float]] = []
        for name in frontier_names:
            vec = frontier_normalized[name]
            score = self.compute_topsis_score(vec, weights, ideal, nadir)
            ranked.append((name, round(score, 6)))

        ranked.sort(key=lambda x: x[1], reverse=True)
        return ranked

    # ── Internal: normalization ─────────────────────────────────

    @staticmethod
    def _normalize_candidates(
        candidates: dict[str, AgentObjectiveVector]
    ) -> dict[str, AgentObjectiveVector]:
        """Min-max normalize latency_ms and cost_usd to [0, 1].

        success_rate and quota_headroom are already in [0, 1] and are
        passed through unchanged (clamped defensively).

        When all candidates share the same latency (or cost), the
        dimension collapses to 0 for everyone — avoids div-by-zero.
        """
        if not candidates:
            return {}

        latencies = [v.latency_ms for v in candidates.values()]
        costs = [v.cost_usd for v in candidates.values()]

        lat_min, lat_max = min(latencies), max(latencies)
        cost_min, cost_max = min(costs), max(costs)

        lat_span = (lat_max - lat_min) if lat_max > lat_min else 0.0
        cost_span = (cost_max - cost_min) if cost_max > cost_min else 0.0

        normalized: dict[str, AgentObjectiveVector] = {}
        for name, v in candidates.items():
            lat_n = (v.latency_ms - lat_min) / lat_span if lat_span > 0 else 0.0
            cost_n = (v.cost_usd - cost_min) / cost_span if cost_span > 0 else 0.0
            normalized[name] = AgentObjectiveVector(
                success_rate=max(0.0, min(1.0, v.success_rate)),
                latency_ms=lat_n,
                cost_usd=cost_n,
                quota_headroom=max(0.0, min(1.0, v.quota_headroom)),
            )
        return normalized
