"""MAOP Model Router — capability-aware model selection for multimodal tasks.

``ModelRouter`` picks the best model for a given :class:`RoutingCriteria`
(task type, required modalities, cost / latency bounds).  Each registered
model advertises its capabilities via :class:`ModelCapability`; the router
scores candidates on four axes and returns the top-ranked one (plus a
ranked list for fallback chains).

Scoring (higher = better, 0 = disqualified):
  1. **Modality coverage** — does the model support every requested modality?
     Missing a required modality disqualifies the candidate.
  2. **Task fit** — does the model advertise the requested task type?
  3. **Cost** — cheaper is better, normalized against the candidate pool.
  4. **Latency** — faster is better, normalized against the pool.

The router is pure-Python and stateless beyond its registry — no I/O, no
network — so it is safe to call from hot paths and easy to unit-test.

Usage::

    from maop.core.multimodal.model_router import (
        ModelRouter, ModelCapability, RoutingCriteria, TaskType, ModalityType,
    )

    router = ModelRouter()
    router.register(ModelCapability(
        name="gpt-4o", modalities={ModalityType.TEXT, ModalityType.IMAGE},
        tasks={TaskType.TEXT_GENERATION, TaskType.IMAGE_UNDERSTANDING},
        cost_per_1k_input=2.5, cost_per_1k_output=10.0, avg_latency_ms=800,
    ))
    best, ranked = router.route(RoutingCriteria(
        task_type=TaskType.IMAGE_UNDERSTANDING,
        modalities={ModalityType.IMAGE, ModalityType.TEXT},
    ))
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from maop.core.multimodal.modality_handlers import ModalityType

logger = logging.getLogger(__name__)


# ── Enums ──────────────────────────────────────────────────────


class TaskType(str, Enum):
    """Coarse task categories used for model routing."""

    TEXT_GENERATION = "text_generation"
    IMAGE_UNDERSTANDING = "image_understanding"
    IMAGE_GENERATION = "image_generation"
    AUDIO_TRANSCRIPTION = "audio_transcription"
    AUDIO_GENERATION = "audio_generation"
    VIDEO_ANALYSIS = "video_analysis"
    VIDEO_GENERATION = "video_generation"
    EMBEDDING = "embedding"
    # Catch-all for tasks that don't fit the above.
    MULTIMODAL = "multimodal"


# ── Data models ────────────────────────────────────────────────


@dataclass
class ModelCapability:
    """Advertised capabilities and cost/latency profile of one model.

    Use a dataclass (not pydantic) because instances are created in hot
    registration paths and the field-access overhead of pydantic validation
    is unnecessary for trusted internal data.
    """

    name: str
    # Modalities the model can consume as input.
    modalities: set[ModalityType] = field(default_factory=lambda: {ModalityType.TEXT})
    # Task types the model is designed for.
    tasks: set[TaskType] = field(default_factory=lambda: {TaskType.TEXT_GENERATION})
    # Cost in USD per 1K tokens (input / output).
    cost_per_1k_input: float = 0.0
    cost_per_1k_output: float = 0.0
    # Rolling average latency in milliseconds (populated by telemetry).
    avg_latency_ms: float = 0.0
    # Maximum context window in tokens.
    max_context: int = 32768
    # Quality tier (0-10, higher = better).  Used as a tiebreaker.
    quality_tier: float = 5.0
    # Whether the model is currently available (e.g. quota exhausted).
    enabled: bool = True
    # Free-form metadata (e.g. provider name, region).
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def cost_per_1k_total(self) -> float:
        """Combined input+output cost per 1K tokens (rough quality proxy)."""
        return self.cost_per_1k_input + self.cost_per_1k_output


@dataclass
class RoutingCriteria:
    """What the caller wants from the model.

    All fields are optional except ``task_type``; unset bounds mean
    "no constraint on that axis".
    """

    task_type: TaskType = TaskType.TEXT_GENERATION
    # Modalities the request will contain.  A model missing any of these
    # is disqualified.
    modalities: set[ModalityType] = field(default_factory=lambda: {ModalityType.TEXT})
    # Hard upper bound on cost per 1K tokens (input + output).
    max_cost_per_1k: float | None = None
    # Hard upper bound on latency (ms).
    max_latency_ms: float | None = None
    # Minimum required context window.
    min_context: int | None = None
    # When True, prefer quality over cost (quality weight × 2).
    prefer_quality: bool = False
    # When True, prefer low latency over cost.
    prefer_speed: bool = False


@dataclass
class RouteResult:
    """Outcome of a routing decision."""

    model: str
    score: float
    capability: ModelCapability
    reason: str = ""


# ── Router ─────────────────────────────────────────────────────


class ModelRouter:
    """Capability-aware model selector.

    Holds a registry of :class:`ModelCapability` entries and ranks them
    against :class:`RoutingCriteria`.  The router is deterministic: given
    the same registry and criteria it always returns the same ordering
    (ties broken by name for stable testing).
    """

    # Scoring weights (sum = 1.0 for the "soft" axes; modality/task are
    # hard gates that disqualify rather than penalize).
    _W_COST = 0.35
    _W_LATENCY = 0.35
    _W_QUALITY = 0.30

    def __init__(self) -> None:
        self._models: dict[str, ModelCapability] = {}

    # ── registry management ───────────────────────────────────

    def register(self, cap: ModelCapability) -> None:
        """Add or replace a model capability entry."""
        self._models[cap.name] = cap

    def unregister(self, name: str) -> bool:
        return self._models.pop(name, None) is not None

    def get(self, name: str) -> ModelCapability | None:
        return self._models.get(name)

    def list_models(self) -> list[ModelCapability]:
        return list(self._models.values())

    def clear(self) -> None:
        self._models.clear()

    # ── routing ───────────────────────────────────────────────

    def _passes_hard_gates(
        self, cap: ModelCapability, criteria: RoutingCriteria
    ) -> tuple[bool, str]:
        """Check hard constraints.  Returns (passes, reason_if_not)."""
        if not cap.enabled:
            return False, "model disabled"
        missing = criteria.modalities - cap.modalities
        if missing:
            return False, f"missing modalities: {sorted(m.value for m in missing)}"
        if criteria.task_type not in cap.tasks and TaskType.MULTIMODAL not in cap.tasks:
            return False, f"task {criteria.task_type.value} not supported"
        if criteria.max_cost_per_1k is not None:  # noqa: SIM102
            if cap.cost_per_1k_total > criteria.max_cost_per_1k:
                return False, f"cost {cap.cost_per_1k_total} > {criteria.max_cost_per_1k}"
        if criteria.max_latency_ms is not None and cap.avg_latency_ms > 0:  # noqa: SIM102
            if cap.avg_latency_ms > criteria.max_latency_ms:
                return False, f"latency {cap.avg_latency_ms} > {criteria.max_latency_ms}"
        if criteria.min_context is not None and cap.max_context < criteria.min_context:
            return False, f"context {cap.max_context} < {criteria.min_context}"
        return True, ""

    def _score(
        self,
        cap: ModelCapability,
        criteria: RoutingCriteria,
        cost_norm: float,
        latency_norm: float,
    ) -> float:
        """Soft-score a candidate that passed the hard gates.

        ``cost_norm`` / ``latency_norm`` are 0..1 values where 0 = cheapest
        / fastest in the pool and 1 = most expensive / slowest.  We invert
        them (1 - norm) so cheaper/faster scores higher.
        """
        w_cost = self._W_COST
        w_lat = self._W_LATENCY
        w_qual = self._W_QUALITY

        if criteria.prefer_quality:
            w_qual *= 2.0
            w_cost *= 0.5
        if criteria.prefer_speed:
            w_lat *= 2.0
            w_cost *= 0.5

        # Re-normalize after preference adjustments.
        w_sum = w_cost + w_lat + w_qual
        w_cost /= w_sum
        w_lat /= w_sum
        w_qual /= w_sum

        cost_score = 1.0 - cost_norm
        latency_score = 1.0 - latency_norm
        quality_score = cap.quality_tier / 10.0

        return w_cost * cost_score + w_lat * latency_score + w_qual * quality_score

    def route(
        self, criteria: RoutingCriteria
    ) -> tuple[RouteResult | None, list[RouteResult]]:
        """Select the best model for *criteria*.

        Returns ``(best, ranked)`` where ``best`` is ``None`` when no
        candidate passes the hard gates, and ``ranked`` is the full
        scored list (descending) for fallback chains.
        """
        # 1. Hard-gate filter.
        candidates: list[tuple[ModelCapability, str]] = []
        for cap in self._models.values():
            ok, reason = self._passes_hard_gates(cap, criteria)
            if ok:
                candidates.append((cap, ""))
            else:
                logger.debug("Router rejected %s: %s", cap.name, reason)

        if not candidates:
            return None, []

        # 2. Compute normalization baselines from the surviving pool.
        costs = [c.cost_per_1k_total for c, _ in candidates]
        latencies = [c.avg_latency_ms for c, _ in candidates]
        max_cost = max(costs) if costs else 0.0
        min_cost = min(costs) if costs else 0.0
        max_lat = max(latencies) if latencies else 0.0
        min_lat = min(latencies) if latencies else 0.0

        def _norm(value: float, lo: float, hi: float) -> float:
            if hi <= lo:
                return 0.0
            return (value - lo) / (hi - lo)

        # 3. Soft-score + rank.
        results: list[RouteResult] = []
        for cap, _ in candidates:
            cost_norm = _norm(cap.cost_per_1k_total, min_cost, max_cost)
            lat_norm = _norm(cap.avg_latency_ms, min_lat, max_lat)
            score = self._score(cap, criteria, cost_norm, lat_norm)
            results.append(RouteResult(model=cap.name, score=score, capability=cap))

        # Descending by score; tie-break by name for determinism.
        results.sort(key=lambda r: (-r.score, r.model))

        best = results[0]
        best.reason = "selected"
        return best, results

    def route_model_name(self, criteria: RoutingCriteria) -> str | None:
        """Convenience: return just the best model name (or None)."""
        best, _ = self.route(criteria)
        return best.model if best else None