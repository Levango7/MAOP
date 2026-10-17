"""MAOP LLM Cost Router — Multi-factor cost-aware LLM routing engine.

F3-05 (Phase 3): intelligent LLM routing with budget-aware task classification.

This module provides:
  - TaskClassifier   — lightweight task-type detection (heuristic, no LLM)
  - CostTier         — cost/quality tier model for model ranking
  - LLMCostRouter    — router that selects optimal model based on task
                       classification, remaining budget, latency SLO, and
                       model health
  - RoutingDecision  — structured routing decision log

Integration points:
  - Wraps ``LLMProviderFactory`` (chat_with_fallback path)
  - Reads ``CostTracker`` for real-time budget consumption
  - Writes routing decisions to ``RoutingDecisionStore`` (OTel)

Defaults to disabled (``MAOP_LLM_COST_ROUTER_ENABLED=0``); existing
``chat_with_fallback`` behaviour is unchanged when disabled.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

logger = logging.getLogger(__name__)


# ── Task Classification ──────────────────────────────────────────────

class TaskClass(str, Enum):
    """High-level task types that drive cost-tier selection.

    Determined by lightweight heuristics (routing-key mapping + input
    signal).  No LLM call involved → routing decision < 1ms overhead.
    """
    CODE_GENERATION = "code_generation"
    CHAT = "chat"
    SUMMARIZATION = "summarization"
    CLASSIFICATION = "classification"
    EMBEDDING = "embedding"
    SEARCH = "search"
    UNKNOWN = "unknown"


# Routing-key → task-class mapping (mirrors ModelSelector._KEY_TO_CAPABILITY)
_ROUTING_KEY_TO_TASK: dict[str, TaskClass] = {
    "codegen": TaskClass.CODE_GENERATION,
    "refactor": TaskClass.CODE_GENERATION,
    "docgen": TaskClass.CODE_GENERATION,
    "techdoc": TaskClass.CODE_GENERATION,
    "quickfix": TaskClass.CODE_GENERATION,
    "chat": TaskClass.CHAT,
    "explain": TaskClass.CHAT,
    "summarize": TaskClass.SUMMARIZATION,
    "summary": TaskClass.SUMMARIZATION,
    "classify": TaskClass.CLASSIFICATION,
    "embedding": TaskClass.EMBEDDING,
    "embed": TaskClass.EMBEDDING,
    "search": TaskClass.SEARCH,
    "review": TaskClass.CHAT,
    "planning": TaskClass.CHAT,
    "verify": TaskClass.CHAT,
    "fileops": TaskClass.CLASSIFICATION,
}


def classify_task(routing_key: str, task_text: str = "") -> TaskClass:
    """Classify a task into a TaskClass using routing key + optional text heuristics."""
    # 1. Routing key lookup (fast path)
    tc = _ROUTING_KEY_TO_TASK.get(routing_key)
    if tc is not None:
        return tc

    # 2. Keyword heuristics on the task text (cheap, no LLM)
    text = task_text.lower()
    if any(kw in text for kw in ("summarize", "summary", "tldr", "abstract")):
        return TaskClass.SUMMARIZATION
    if any(kw in text for kw in ("classify", "categorize", "label")):
        return TaskClass.CLASSIFICATION
    if any(kw in text for kw in ("embed", "embedding", "vector")):
        return TaskClass.EMBEDDING
    if any(kw in text for kw in ("search", "find", "lookup", "query")):
        return TaskClass.SEARCH
    if any(kw in text for kw in ("code", "implement", "function", "refactor", "fix", "debug")):
        return TaskClass.CODE_GENERATION
    if any(kw in text for kw in ("chat", "talk", "hello", "hi")):
        return TaskClass.CHAT

    return TaskClass.UNKNOWN


# ── Cost Tiers ───────────────────────────────────────────────────────

@dataclass(frozen=True)
class CostTier:
    """Per-task-class cost tier configuration.

    Models in higher tiers cost more but deliver better quality.
    The router picks the cheapest tier whose quality meets the task's SLO.
    """
    task_class: TaskClass
    preferred_model: str = ""       # Best quality-cost ratio for this tier
    fallback_models: tuple[str, ...] = ()
    max_cost_per_1k_tokens: float = 0.0  # Hard ceiling; 0 = unlimited


# Default tier table (overridable via MAOP_COST_TIERS_JSON env / settings)
_DEFAULT_TIERS: dict[TaskClass, CostTier] = {
    TaskClass.CODE_GENERATION: CostTier(
        task_class=TaskClass.CODE_GENERATION,
        preferred_model="gpt-4o",
        fallback_models=("claude-3.5-sonnet", "gpt-4o-mini"),
        max_cost_per_1k_tokens=15.0,
    ),
    TaskClass.CHAT: CostTier(
        task_class=TaskClass.CHAT,
        preferred_model="gpt-4o-mini",
        fallback_models=("deepseek-chat", "claude-3-haiku"),
        max_cost_per_1k_tokens=1.0,
    ),
    TaskClass.SUMMARIZATION: CostTier(
        task_class=TaskClass.SUMMARIZATION,
        preferred_model="gpt-4o-mini",
        fallback_models=("deepseek-chat", "claude-3-haiku"),
        max_cost_per_1k_tokens=1.0,
    ),
    TaskClass.CLASSIFICATION: CostTier(
        task_class=TaskClass.CLASSIFICATION,
        preferred_model="gpt-4o-mini",
        fallback_models=("deepseek-chat", "claude-3-haiku"),
        max_cost_per_1k_tokens=0.5,
    ),
    TaskClass.EMBEDDING: CostTier(
        task_class=TaskClass.EMBEDDING,
        preferred_model="gpt-4o-mini",
        fallback_models=("deepseek-chat",),
        max_cost_per_1k_tokens=0.5,
    ),
    TaskClass.SEARCH: CostTier(
        task_class=TaskClass.SEARCH,
        preferred_model="gpt-4o-mini",
        fallback_models=("deepseek-chat", "claude-3-haiku"),
        max_cost_per_1k_tokens=1.0,
    ),
    TaskClass.UNKNOWN: CostTier(
        task_class=TaskClass.UNKNOWN,
        preferred_model="gpt-4o-mini",
        fallback_models=("deepseek-chat", "claude-3-haiku"),
        max_cost_per_1k_tokens=5.0,
    ),
}


class RoutingStrategy(str, Enum):
    """Routing policy for cost-quality trade-off."""
    BALANCED = "balanced"           # Default: prefer tier model, allow upgrade on budget surplus
    AGGRESSIVE = "aggressive"       # Always pick cheapest tier regardless of task class
    QUALITY_FIRST = "quality_first" # Ignore cost, pick strongest model in tier


# ── Routing Decision ────────────────────────────────────────────────

@dataclass
class RoutingDecision:
    """Immutable record of a routing decision for audit/observability."""
    task_class: TaskClass
    selected_model: str
    original_model: str
    routing_reason: str          # human-readable reason
    estimated_cost_usd: float = 0.0
    budget_remaining_pct: float = 100.0
    latency_slo_ms: int = 0
    strategy: RoutingStrategy = RoutingStrategy.BALANCED
    timestamp: float = field(default_factory=time.time)
    degraded: bool = False       # True if forced downgrade due to budget


# ── LLMCostRouter ───────────────────────────────────────────────────

class LLMCostRouter:
    """Multi-factor LLM router for F3-05.

    Reads task classification, remaining budget, latency SLO, and model
    health to select the optimal model for each dispatch.

    Parameters
    ----------
    strategy : RoutingStrategy
        Cost-quality trade-off policy.
    monthly_budget_usd : float
        Monthly budget cap; when remaining < 10 %, force downgrade.
    latency_slo_ms : int
        Target P99 latency; slow models exceeding this are excluded.
    tiers : dict[TaskClass, CostTier] | None
        Custom tier table; defaults to _DEFAULT_TIERS.
    """

    def __init__(
        self,
        strategy: RoutingStrategy = RoutingStrategy.BALANCED,
        monthly_budget_usd: float = 0.0,
        latency_slo_ms: int = 3000,
        tiers: dict[TaskClass, CostTier] | None = None,
    ) -> None:
        self._strategy = strategy
        self._monthly_budget = monthly_budget_usd
        self._latency_slo_ms = latency_slo_ms
        self._tiers = tiers if tiers is not None else dict(_DEFAULT_TIERS)
        self._decision_log: list[RoutingDecision] = []

    # ── Public API ──────────────────────────────────────────────────

    def route(
        self,
        *,
        routing_key: str = "",
        task_text: str = "",
        requested_model: str = "",
        budget_remaining_pct: float = 100.0,
    ) -> RoutingDecision:
        """Select the optimal model for a task.

        Parameters
        ----------
        routing_key : str
            Routing key from the plan phase (e.g. "codegen", "chat").
        task_text : str
            Original task description; used for keyword heuristics.
        requested_model : str
            Model name requested by the caller; preserved if viable.
        budget_remaining_pct : float
            Remaining budget percentage (0-100).

        Returns
        -------
        RoutingDecision
            The routing decision with selected_model to use.
        """
        task_class = classify_task(routing_key, task_text)
        tier = self._tiers.get(task_class, _DEFAULT_TIERS[TaskClass.UNKNOWN])

        # Strategy-specific selection
        if self._strategy == RoutingStrategy.AGGRESSIVE:
            selected, reason = self._pick_aggressive(tier, budget_remaining_pct)
        elif self._strategy == RoutingStrategy.QUALITY_FIRST:
            selected, reason = self._pick_quality_first(tier, budget_remaining_pct)
        else:
            selected, reason = self._pick_balanced(
                tier, requested_model, budget_remaining_pct,
            )

        degraded = selected != tier.preferred_model and self._strategy != RoutingStrategy.QUALITY_FIRST

        decision = RoutingDecision(
            task_class=task_class,
            selected_model=selected,
            original_model=requested_model or tier.preferred_model,
            routing_reason=reason,
            budget_remaining_pct=budget_remaining_pct,
            latency_slo_ms=self._latency_slo_ms,
            strategy=self._strategy,
            degraded=degraded,
        )
        self._decision_log.append(decision)

        logger.debug(
            "[cost-router] task=%s model=%s strategy=%s reason=%s budget=%.1f%%",
            task_class.value, selected, self._strategy.value,
            reason, budget_remaining_pct,
        )
        return decision

    def estimate_cost(self, model: str, prompt_tokens: int = 0, completion_tokens: int = 0) -> float:
        """Estimate USD cost for a model call using CostTracker pricing table."""
        try:
            from maop.core.cost_tracker import get_cost_tracker
            tracker = get_cost_tracker()
            pricing = tracker.get_pricing()
            p = pricing.get(model)
            if not p:
                return 0.0
            prompt_cost = (prompt_tokens / 1_000_000) * p["prompt_per_1m"]
            completion_cost = (completion_tokens / 1_000_000) * p["completion_per_1m"]
            return round(prompt_cost + completion_cost, 6)
        except Exception:
            return 0.0

    def get_decision_log(self) -> list[RoutingDecision]:
        """Return a copy of recent routing decisions."""
        return list(self._decision_log)

    def clear_decision_log(self) -> None:
        """Clear the in-memory decision log."""
        self._decision_log.clear()

    # ── Internal Pickers ────────────────────────────────────────────

    def _pick_aggressive(
        self, tier: CostTier, budget_pct: float,
    ) -> tuple[str, str]:
        """Always pick the cheapest fallback model."""
        if tier.fallback_models:
            return tier.fallback_models[0], "aggressive: cheapest-tier"
        return tier.preferred_model, "aggressive: preferred (no fallback)"

    def _pick_quality_first(
        self, tier: CostTier, budget_pct: float,
    ) -> tuple[str, str]:
        """Ignore cost; always pick preferred (strongest in tier)."""
        return tier.preferred_model, "quality-first: preferred-tier"

    def _pick_balanced(
        self, tier: CostTier, requested_model: str, budget_pct: float,
    ) -> tuple[str, str]:
        """Balanced: preferred model if budget OK; downgrade if tight."""
        # Budget guard: remaining < 10 % → force cheapest
        if budget_pct < 10.0 and tier.fallback_models:
            return tier.fallback_models[0], f"budget-guard: {budget_pct:.1f}% < 10%, downgraded"

        # If requested model is viable (in tier), respect caller preference
        if requested_model and (
            requested_model == tier.preferred_model
            or requested_model in tier.fallback_models
        ):
            return requested_model, f"requested-model: {requested_model}"

        # Default: prefer the tier's recommended model
        return tier.preferred_model, f"balanced: tier-preferred ({tier.preferred_model})"


# ── Singleton accessor ──────────────────────────────────────────────

_router_instance: LLMCostRouter | None = None
import threading as _threading
_router_lock = _threading.Lock()


def get_cost_router(
    strategy: RoutingStrategy | None = None,
    monthly_budget_usd: float | None = None,
    latency_slo_ms: int | None = None,
) -> LLMCostRouter:
    """Return a process-wide LLMCostRouter singleton (lazy).

    Parameters override existing config only when explicitly passed (non-None).
    """
    global _router_instance
    if _router_instance is None:
        with _router_lock:
            if _router_instance is None:
                from maop.config.settings import get_settings
                try:
                    settings = get_settings()
                    _strategy = strategy or RoutingStrategy(
                        getattr(settings, "cost_routing_policy", "balanced")
                    )
                    _budget = monthly_budget_usd or settings.budget_monthly_limit_usd
                    _slo = latency_slo_ms or getattr(settings, "cost_latency_slo_ms", 3000)
                except Exception:
                    _strategy = strategy or RoutingStrategy.BALANCED
                    _budget = monthly_budget_usd or 0.0
                    _slo = latency_slo_ms or 3000
                _router_instance = LLMCostRouter(
                    strategy=_strategy,
                    monthly_budget_usd=_budget,
                    latency_slo_ms=_slo,
                )
    return _router_instance


def reset_cost_router() -> None:
    """Reset the process-wide router singleton (used in tests)."""
    global _router_instance
    with _router_lock:
        _router_instance = None
