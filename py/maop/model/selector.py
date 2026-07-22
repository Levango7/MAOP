"""ModelSelector — Select the best model for a task based on policy, capability, and budget."""

from __future__ import annotations

import logging

from maop.model.registry import ModelRegistry
from maop.model.schema import (
    ModelDef, EffectiveModel, QualityTier, LatencyTier,
)

logger = logging.getLogger(__name__)

# Quality/latency scoring maps
_QUALITY_SCORE = {
    QualityTier.EXCELLENT: 4, QualityTier.GOOD: 3,
    QualityTier.FAIR: 2, QualityTier.POOR: 1,
}
_LATENCY_SCORE = {
    LatencyTier.INSTANT: 4, LatencyTier.FAST: 3,
    LatencyTier.MEDIUM: 2, LatencyTier.SLOW: 1,
}


class ModelSelector:
    """Selects the best model for a dispatch based on policy + capability + budget.

    Usage::

        selector = ModelSelector(registry)
        effective = selector.select(
            capability="codegen",
            agent_model="yi-large",
            policy_name="codegen",
        )
        # effective.model_name, effective.provider, effective.cli_model_arg
    """

    def __init__(self, registry: ModelRegistry) -> None:
        self._registry = registry

    def select(
        self,
        capability: str = "",
        agent_model: str = "",
        policy_name: str = "default",
        task_input_tokens: int = 1000,
        task_output_tokens: int = 2000,
        model_ref: str = "",
    ) -> EffectiveModel:
        """Select the best model for a dispatch.

        Resolution order:
        1. If model_ref or agent_model is specified and found in registry, use it.
        2. Otherwise, query registry by capability under the policy strategy.
        3. Build fallback chain from remaining candidates.
        """
        policy = self._registry.get_policy(policy_name)
        strategy = policy.strategy.value if policy else "best_quality_within_budget"
        max_cost = policy.max_cost_per_task if policy else None

        # Step 1: Try agent-specified model (model_ref first, then agent_model)
        primary_model = None
        if agent_model or model_ref:
            primary_model = self._registry.resolve_agent_model(
                agent_model, model_ref=model_ref,
            )

        # Step 2: If not found, select by capability
        if primary_model is None and capability:
            primary_model = self._registry.best_model(
                capability, strategy=strategy, max_cost=max_cost,
            )

        # Step 3: If still not found, try default model
        if primary_model is None:
            primary_model = self._registry.get_model("built-in")

        if primary_model is None:
            # Last resort: return an empty effective model
            return EffectiveModel(
                model_name="unknown", provider="local",
                cli_model_arg=agent_model or "auto",
                policy_name=policy_name,
            )

        # Estimate cost
        cost_estimate = (
            primary_model.cost_per_1k_input * task_input_tokens / 1000
            + primary_model.cost_per_1k_output * task_output_tokens / 1000
        )

        # Build fallback chain
        fallback_chain = self._build_fallback_chain(
            primary_model, capability, strategy, max_cost,
        )

        return EffectiveModel(
            model_name=primary_model.name,
            provider=primary_model.provider,
            cli_model_arg=primary_model.name,
            cost_estimate=round(cost_estimate, 6),
            fallback_chain=fallback_chain,
            policy_name=policy_name,
        )

    def _build_fallback_chain(
        self,
        primary: ModelDef,
        capability: str,
        strategy: str,
        max_cost: float | None,
    ) -> list[str]:
        """Build a fallback chain excluding the primary model."""
        if not capability:
            return []
        candidates = self._registry.models_by_capability(capability)
        # Exclude primary and disabled providers
        chain = [m.name for m in candidates
                 if m.name != primary.name
                 and self._registry.providers.is_healthy(m.provider)]
        # Limit to top 3 fallbacks
        return chain[:3]

    def select_for_routing_key(
        self,
        routing_key: str,
        agent_model: str = "",
        policy_name: str = "",
    ) -> EffectiveModel:
        """Select model for a routing key (maps routing keys to capabilities)."""
        # Routing key to capability mapping
        key_to_capability = {
            "codegen": "codegen", "chat": "chat", "search": "search",
            "review": "review", "planning": "planning", "verify": "verify",
            "refactor": "codegen", "explain": "chat", "quickfix": "codegen",
            "fileops": "fileops", "docgen": "codegen", "techdoc": "codegen",
            "mcp": "mcp", "memory": "memory", "pipeline": "pipeline",
        }
        capability = key_to_capability.get(routing_key, routing_key)
        # Use routing key as policy name if no policy specified
        pn = policy_name or routing_key
        return self.select(
            capability=capability,
            agent_model=agent_model,
            policy_name=pn,
        )
