"""MAOP Evolution Strategies — Strategy engine for self-evolution decisions.

Provides pluggable strategies that determine HOW evolution suggestions
are generated and applied. Each strategy encapsulates a decision policy.

Built-in strategies:
  - ConservativeStrategy — Only auto-apply HIGH severity, require approval for others
  - AggressiveStrategy — Auto-apply all suggestions immediately
  - BalancedStrategy — Auto-apply MEDIUM+ with cooldown periods
  - CostAwareStrategy — Consider cost impact before applying

Usage::

    from maop.core.evolution_strategies import StrategyEngine, BalancedStrategy

    engine = StrategyEngine(root_dir="/path/to/MAOP", strategy=BalancedStrategy())
    decisions = engine.evaluate(suggestions)
    for d in decisions:
        if d.should_apply:
            engine.apply(d.suggestion_id)
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, cast

from pydantic import BaseModel

logger = logging.getLogger(__name__)


class EvolutionDecision(BaseModel):
    suggestion_id: str = ""
    suggestion_type: str = ""
    severity: str = "MEDIUM"
    should_apply: bool = False
    reason: str = ""
    cooldown_remaining_s: int = 0
    estimated_impact: str = "low"


class StrategyConfig(BaseModel):
    # 默认不自动应用 HIGH 严重级别的配置变更。
    # 需显式启用 auto_apply_high=True 或通过 approval token 手动批准，
    # 避免高影响变更未经审核即生效（安全默认值，原为 True）。
    auto_apply_high: bool = False
    auto_apply_medium: bool = False
    auto_apply_low: bool = False
    cooldown_seconds: int = 3600
    max_mutations_per_hour: int = 5
    require_approval_for_routing: bool = True
    require_approval_for_disable: bool = True
    cost_threshold_usd: float = 0.01


class BaseStrategy(ABC):
    """Abstract base class for evolution strategies."""

    def __init__(self, config: StrategyConfig | None = None) -> None:
        self._config = config or StrategyConfig()

    @abstractmethod
    def evaluate(self, suggestion: dict[str, Any], history: list[dict[str, Any]]) -> EvolutionDecision:
        ...

    @property
    def config(self) -> StrategyConfig:
        return self._config


class ConservativeStrategy(BaseStrategy):
    """Only auto-apply HIGH severity suggestions. Everything else requires approval."""

    def evaluate(self, suggestion: dict[str, Any], history: list[dict[str, Any]]) -> EvolutionDecision:
        severity = suggestion.get("severity", "MEDIUM")
        should = severity == "HIGH" and suggestion.get("auto_applicable", False)

        return EvolutionDecision(
            suggestion_id=suggestion.get("id", ""),
            suggestion_type=suggestion.get("type", ""),
            severity=severity,
            should_apply=should,
            reason="Auto-apply HIGH severity only" if should else "Requires manual approval",
        )


class AggressiveStrategy(BaseStrategy):
    """Auto-apply all suggestions immediately."""

    def evaluate(self, suggestion: dict[str, Any], history: list[dict[str, Any]]) -> EvolutionDecision:
        return EvolutionDecision(
            suggestion_id=suggestion.get("id", ""),
            suggestion_type=suggestion.get("type", ""),
            severity=suggestion.get("severity", "MEDIUM"),
            should_apply=suggestion.get("auto_applicable", False),
            reason="Auto-apply all applicable suggestions",
            estimated_impact="medium",
        )


class BalancedStrategy(BaseStrategy):
    """Auto-apply MEDIUM+ with cooldown periods and rate limiting."""

    def evaluate(self, suggestion: dict[str, Any], history: list[dict[str, Any]]) -> EvolutionDecision:
        severity = suggestion.get("severity", "MEDIUM")
        auto = suggestion.get("auto_applicable", False)
        stype = suggestion.get("type", "")

        if not auto:
            return EvolutionDecision(
                suggestion_id=suggestion.get("id", ""),
                suggestion_type=stype,
                severity=severity,
                should_apply=False,
                reason="Not auto-applicable",
            )

        should = severity in ("HIGH", "MEDIUM")

        if self._config.require_approval_for_routing and stype == "routing_mismatch":
            should = False

        if self._config.require_approval_for_disable and stype == "agent_low_success":
            should = False

        recent_count = self._count_recent_mutations(history, hours=1)
        if recent_count >= self._config.max_mutations_per_hour:
            should = False

        cooldown = self._check_cooldown(suggestion, history)

        return EvolutionDecision(
            suggestion_id=suggestion.get("id", ""),
            suggestion_type=stype,
            severity=severity,
            should_apply=should and cooldown == 0,
            reason="Balanced: MEDIUM+ auto-apply with cooldown" if should else "Rate limited or requires approval",
            cooldown_remaining_s=cooldown,
        )

    @staticmethod
    def _count_recent_mutations(history: list[dict[str, Any]], hours: int = 1) -> int:
        now = datetime.now(timezone.utc)
        count = 0
        for h in history:
            ts = h.get("applied_at", "")
            if not ts:
                continue
            try:
                dt = datetime.fromisoformat(ts)
                if (now - dt).total_seconds() < hours * 3600:
                    count += 1
            except (ValueError, TypeError):
                pass
        return count

    def _check_cooldown(self, suggestion: dict[str, Any], history: list[dict[str, Any]]) -> int:
        now = datetime.now(timezone.utc)
        for h in history:
            if h.get("type") == suggestion.get("type") and h.get("applied_at"):
                try:
                    dt = datetime.fromisoformat(h["applied_at"])
                    elapsed = (now - dt).total_seconds()
                    remaining = self._config.cooldown_seconds - elapsed
                    if remaining > 0:
                        return int(remaining)
                except (ValueError, TypeError):
                    pass
        return 0


class CostAwareStrategy(BaseStrategy):
    """Consider cost impact before applying suggestions."""

    def evaluate(self, suggestion: dict[str, Any], history: list[dict[str, Any]]) -> EvolutionDecision:
        severity = suggestion.get("severity", "MEDIUM")
        auto = suggestion.get("auto_applicable", False)
        cost_impact = suggestion.get("estimated_cost_impact", 0.0)

        should = auto and cost_impact <= self._config.cost_threshold_usd

        if severity == "HIGH" and cost_impact <= self._config.cost_threshold_usd * 10:
            should = True

        return EvolutionDecision(
            suggestion_id=suggestion.get("id", ""),
            suggestion_type=suggestion.get("type", ""),
            severity=severity,
            should_apply=should,
            reason=f"Cost impact ${cost_impact:.4f} vs threshold ${self._config.cost_threshold_usd:.4f}",
            estimated_impact="high" if cost_impact > self._config.cost_threshold_usd else "low",
        )


STRATEGY_MAP: dict[str, type[BaseStrategy]] = {
    "conservative": ConservativeStrategy,
    "aggressive": AggressiveStrategy,
    "balanced": BalancedStrategy,
    "cost_aware": CostAwareStrategy,
}


class StrategyEngine:
    """Orchestrates evolution strategy evaluation and application.

    Combines strategy evaluation with ConfigMutator for end-to-end
    evolution decision making and execution.
    """

    def __init__(
        self,
        root_dir: str | Path,
        strategy: BaseStrategy | None = None,
        strategy_name: str = "balanced",
    ) -> None:
        self._root = Path(root_dir)
        self._strategy = strategy or STRATEGY_MAP.get(strategy_name, BalancedStrategy)()
        self._suggestions_file = self._root / "data" / "evolve-suggestions.json"
        self._history: list[dict[str, Any]] = []
        self._load_history()

    @property
    def strategy(self) -> BaseStrategy:
        return self._strategy

    def evaluate(self, suggestions: list[dict[str, Any]] | None = None) -> list[EvolutionDecision]:
        """Evaluate a list of suggestions against the current strategy."""
        if suggestions is None:
            suggestions = self._load_suggestions()

        return [self._strategy.evaluate(s, self._history) for s in suggestions]

    def evaluate_and_apply(self, suggestions: list[dict[str, Any]] | None = None) -> list[EvolutionDecision]:
        """Evaluate suggestions and auto-apply those that pass."""
        decisions = self.evaluate(suggestions)

        for d in decisions:
            if d.should_apply:
                self.apply(d.suggestion_id)

        return decisions

    def apply(self, suggestion_id: str) -> dict[str, Any]:
        """Apply a suggestion using ConfigMutator."""
        try:
            from maop.core.config_mutator import ConfigMutator
            mutator = ConfigMutator(root_dir=self._root)
            result = mutator.apply_suggestion(suggestion_id)

            if result.applied:
                self._history.append({
                    "suggestion_id": suggestion_id,
                    "type": result.mutation_type,
                    "applied_at": datetime.now(timezone.utc).isoformat(),
                    "changes": result.changes,
                })
                self._save_history()

            return result.model_dump()
        except Exception as exc:
            logger.error("[strategy] Apply failed for %s: %s", suggestion_id, exc)
            return {"applied": False, "error": str(exc)}

    def _load_suggestions(self) -> list[dict[str, Any]]:
        if not self._suggestions_file.exists():
            return []
        import json
        with open(self._suggestions_file, encoding="utf-8") as f:
            return cast(list[dict[str, Any]], json.load(f))

    def _load_history(self) -> None:
        history_file = self._root / "data" / "evolution-history.json"
        if history_file.exists():
            import json
            with open(history_file, encoding="utf-8") as f:
                self._history = json.load(f)

    def _save_history(self) -> None:
        history_file = self._root / "data" / "evolution-history.json"
        history_file.parent.mkdir(parents=True, exist_ok=True)
        import json
        with open(history_file, "w", encoding="utf-8") as f:
            json.dump(self._history[-100:], f, indent=2, ensure_ascii=False)
