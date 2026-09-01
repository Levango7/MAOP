"""ModelSelector — Select the best model for a task based on policy, capability, and budget.

Phase γ-5 extension: the selector can optionally consider
``LoadBalancer`` active-task load and ``QuotaEnforcer`` remaining
quota when picking a model. Both dependencies are optional; when
either is ``None`` the selector falls back to the original
capability/strategy/health-based behaviour.
"""

from __future__ import annotations

import contextlib
import logging
import time
from typing import TYPE_CHECKING, Any

from maop.core.monitoring.monitoring import (
    MAOP_MODEL_SELECTION_LOAD_AWARE,
    MAOP_MODEL_SELECTION_QUOTA_REJECTED,
    MAOP_ROUTING_DECISION_DURATION_MS,
    MAOP_ROUTING_DECISION_TOTAL,
)
from maop.core.monitoring.otel import get_tracer
from maop.core.monitoring.otel import span as otel_span
from maop.core.routing.routing_decision import (
    RoutingDecisionRecord,
    get_active_span_context,
    record_decision_safe,
)
from maop.model.registry import ModelRegistry
from maop.model.schema import (
    EffectiveModel,
    LatencyTier,
    ModelDef,
    QualityTier,
)

if TYPE_CHECKING:
    from maop.core.routing.load_balancer import LoadBalancer
    from maop.model.quota import QuotaEnforcer

logger = logging.getLogger(__name__)

# Quality/latency scoring maps
_QUALITY_SCORE = {
    QualityTier.EXCELLENT: 4,
    QualityTier.GOOD: 3,
    QualityTier.FAIR: 2,
    QualityTier.POOR: 1,
}
_LATENCY_SCORE = {
    LatencyTier.INSTANT: 4,
    LatencyTier.FAST: 3,
    LatencyTier.MEDIUM: 2,
    LatencyTier.SLOW: 1,
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

    Phase γ-5: pass optional ``load_balancer`` and ``quota_enforcer`` to
    enable load-aware tie-breaking and quota-aware fallback.
    """

    def __init__(
        self,
        registry: ModelRegistry,
        *,
        load_balancer: LoadBalancer | None = None,
        quota_enforcer: QuotaEnforcer | None = None,
    ) -> None:
        self._registry = registry
        self._load_balancer = load_balancer
        self._quota_enforcer = quota_enforcer
        # Phase γ-4: stashed by _select_impl for the span wrapper to read
        # when building the decision record. Initialised to neutral values
        # so they're always present even on the early-return paths.
        self._last_quota_check: str = "skipped"
        self._last_fallback_used: bool = False

    def select(
        self,
        capability: str = "",
        agent_model: str = "",
        policy_name: str = "default",
        task_input_tokens: int = 1000,
        task_output_tokens: int = 2000,
        model_ref: str = "",
        trace_id: str = "",
    ) -> EffectiveModel:
        """Select the best model for a dispatch.

        Resolution order:
        1. If model_ref or agent_model is specified and found in registry, use it.
        2. Otherwise, query registry by capability under the policy strategy.
           When a ``load_balancer`` is configured, load (active tasks) is
           used as a tie-breaker among strategy-equivalent candidates.
        3. Build fallback chain from remaining candidates.
        4. When a ``quota_enforcer`` is configured, if the resolved
           primary's provider quota is exhausted, walk the fallback
           candidates for the first provider with remaining quota.

        Phase γ-4: ``trace_id`` is optional and used for OTel span
        correlation + decision-record persistence.
        """
        tracer = get_tracer("maop.routing.model_selector")
        _start = time.monotonic()
        with otel_span(
            tracer,
            "routing.model_selector.select",
            trace_id=trace_id,
            attributes={
                "routing.capability": capability,
                "routing.policy_name": policy_name,
                "routing.agent_model": agent_model,
                "routing.model_ref": model_ref,
            },
        ) as _span:
            effective = self._select_impl(
                capability=capability,
                agent_model=agent_model,
                policy_name=policy_name,
                task_input_tokens=task_input_tokens,
                task_output_tokens=task_output_tokens,
                model_ref=model_ref,
            )

            # Phase γ-4: set span attributes + persist decision record.
            _set_span_attr(_span, "routing.selected_model", effective.model_name)
            _set_span_attr(_span, "routing.selected_provider", effective.provider)
            _set_span_attr(_span, "routing.fallback_count", len(effective.fallback_chain))
            _set_span_attr(_span, "routing.quota_check_result", self._last_quota_check)
            _set_span_attr(_span, "routing.fallback_used", 1 if self._last_fallback_used else 0)
            _record_selector_decision(
                trace_id=trace_id,
                capability=capability,
                policy_name=policy_name,
                strategy=effective.policy_name or policy_name,
                effective=effective,
                agent_model=agent_model,
                duration_ms=(time.monotonic() - _start) * 1000.0,
                quota_enforcer_present=self._quota_enforcer is not None,
                load_balancer_present=self._load_balancer is not None,
                quota_check_result=self._last_quota_check,
                fallback_used=self._last_fallback_used,
            )
            return effective

    def _select_impl(
        self,
        *,
        capability: str,
        agent_model: str,
        policy_name: str,
        task_input_tokens: int,
        task_output_tokens: int,
        model_ref: str,
    ) -> EffectiveModel:
        """Original :meth:`select` body, factored out for span wrapping."""
        policy = self._registry.get_policy(policy_name)
        strategy = policy.strategy.value if policy else "best_quality_within_budget"
        max_cost = policy.max_cost_per_task if policy else None

        # Step 1: Try agent-specified model (model_ref first, then agent_model)
        primary_model = None
        if agent_model or model_ref:
            primary_model = self._registry.resolve_agent_model(
                agent_model,
                model_ref=model_ref,
            )

        # Step 1.5: Default provider preference (Phase 2 — OmniRoute default exit)
        # When default_provider is set, prefer its models as primary for any
        # capability they support, before falling back to legacy strategy-based
        # selection. This makes the default provider the primary LLM exit.
        if primary_model is None and capability and self._registry.get_default_provider():
            default_provider = self._registry.get_default_provider()
            candidates = [
                m
                for m in self._registry.models_by_provider(default_provider)
                if capability in m.capabilities and self._registry.providers.is_healthy(m.provider)
            ]
            if candidates:
                # Sort by strategy to pick the best one from the default provider
                candidates.sort(key=lambda m: self._strategy_key(m, strategy))
                primary_model = candidates[0]
                logger.debug(
                    "[selector] Selected %s from default provider %s for capability=%s",
                    primary_model.name,
                    default_provider,
                    capability,
                )

        # Step 2: If not found, select by capability
        if primary_model is None and capability:
            primary_model = self._registry.best_model(
                capability,
                strategy=strategy,
                max_cost=max_cost,
            )

        # Step 3: If still not found, try default model
        if primary_model is None:
            primary_model = self._registry.get_model("built-in")

        if primary_model is None:
            # Last resort: return an empty effective model
            return EffectiveModel(
                model_name="unknown",
                provider="local",
                cli_model_arg=agent_model or "auto",
                policy_name=policy_name,
            )

        # ── Load-aware tie-breaking (Phase γ-5) ──────────────
        # When the strategy produces a tie among the top candidates, prefer
        # the provider with the lower active load. Only active when a
        # ``load_balancer`` is configured; without one we keep the original
        # strategy-only behaviour.
        if self._load_balancer is not None and capability:
            tie_chosen = self._apply_load_aware_tiebreak(
                primary_model,
                capability,
                strategy,
                max_cost,
            )
            if tie_chosen is not None:
                primary_model = tie_chosen

        # ── Quota-aware fallback (Phase γ-5) ─────────────────
        # When a ``quota_enforcer`` is configured and the selected primary's
        # provider has exhausted its quota, walk the fallback chain for the
        # first healthy provider that still has quota. If none remain, we
        # degrade to the (over-quota) primary rather than failing the request.
        # The QuotaEnforcer is now genuinely consulted — previously this branch
        # was a documented no-op, so quota exhaustion never triggered a switch.
        quota_check_result = "skipped"
        fallback_used = False
        if self._quota_enforcer is not None and capability:
            est_tokens = task_input_tokens + task_output_tokens
            if not self._quota_enforcer.check(primary_model.provider, tokens=est_tokens):
                quota_check_result = "rejected"
                try:
                    MAOP_MODEL_SELECTION_QUOTA_REJECTED.inc(
                        labels={"provider": primary_model.provider}
                    )
                except Exception:  # metric is best-effort
                    logger.debug("quota_rejected metric inc failed", exc_info=True)
                # Walk fallback candidates for the first with remaining quota.
                switched = None
                for name in self._build_fallback_chain(
                    primary_model, capability, strategy, max_cost
                ):
                    cand = self._registry.get_model(name)
                    if cand is None:
                        continue
                    if not self._registry.providers.is_healthy(cand.provider):
                        continue
                    if self._quota_enforcer.check(cand.provider, tokens=est_tokens):
                        switched = cand
                        break
                if switched is not None:
                    primary_model = switched
                    fallback_used = True
                    quota_check_result = "switched"
                else:
                    quota_check_result = "all_exhausted"

        # Estimate cost
        cost_estimate = (
            primary_model.cost_per_1k_input * task_input_tokens / 1000
            + primary_model.cost_per_1k_output * task_output_tokens / 1000
        )

        # Build fallback chain (for the returned effective model)
        fallback_chain = self._build_fallback_chain(
            primary_model,
            capability,
            strategy,
            max_cost,
        )

        # Stash for the decision-record helper (read by the span wrapper).
        self._last_quota_check = quota_check_result
        self._last_fallback_used = fallback_used

        return EffectiveModel(
            model_name=primary_model.name,
            provider=primary_model.provider,
            cli_model_arg=primary_model.name,
            cost_estimate=round(cost_estimate, 6),
            fallback_chain=fallback_chain,
            policy_name=policy_name,
        )

    def _apply_load_aware_tiebreak(
        self,
        primary: ModelDef,
        capability: str,
        strategy: str,
        max_cost: float | None,
    ) -> ModelDef | None:
        """Among strategy-equivalent top candidates, pick the lower-load provider.

        Returns the model to use only when load actually changes the pick
        (the strategy winner is not already the lowest-load option); otherwise
        returns ``None`` so the caller keeps ``primary``. The
        ``MAOP_MODEL_SELECTION_LOAD_AWARE`` metric is incremented exactly when
        load changes the outcome.
        """
        lb = self._load_balancer
        if lb is None:
            return None
        cand_pool = [
            m
            for m in self._registry.models_by_capability(capability)
            if m.enabled and self._registry.providers.is_healthy(m.provider)
        ]
        if max_cost is not None:
            cand_pool = [
                m for m in cand_pool if m.cost_per_1k_input + m.cost_per_1k_output <= max_cost
            ]
        if not cand_pool:
            return None
        cand_pool.sort(key=lambda m: self._strategy_key(m, strategy))
        best_key = self._strategy_key(cand_pool[0], strategy)
        tie_group = [m for m in cand_pool if self._strategy_key(m, strategy) == best_key]
        if len(tie_group) < 2:
            return None
        # Break the tie by active load (lower first); stable sort preserves
        # the strategy order among equal-load providers.
        tie_group.sort(key=lambda m: lb.get_load(m.provider))
        chosen = tie_group[0]
        if chosen.name == primary.name:
            return None
        try:
            MAOP_MODEL_SELECTION_LOAD_AWARE.inc()
        except Exception:  # metric is best-effort
            logger.debug("load_aware metric inc failed", exc_info=True)
        return chosen

    def _strategy_key(self, model: ModelDef, strategy: str) -> tuple:
        """Return a sort key for the strategy (smaller = better)."""
        if strategy == "cheapest":
            return (model.cost_per_1k_input + model.cost_per_1k_output,)
        if strategy == "fastest":
            return (-_LATENCY_SCORE.get(model.latency_tier, 0),)
        if strategy == "best_quality":
            return (-_QUALITY_SCORE.get(model.quality_tier, 0),)
        # best_quality_within_budget (default)
        return (
            -_QUALITY_SCORE.get(model.quality_tier, 0),
            -_LATENCY_SCORE.get(model.latency_tier, 0),
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
        chain = [
            m.name
            for m in candidates
            if m.name != primary.name and self._registry.providers.is_healthy(m.provider)
        ]
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
            "codegen": "codegen",
            "chat": "chat",
            "search": "search",
            "review": "review",
            "planning": "planning",
            "verify": "verify",
            "refactor": "codegen",
            "explain": "chat",
            "quickfix": "codegen",
            "fileops": "fileops",
            "docgen": "codegen",
            "techdoc": "codegen",
            "mcp": "mcp",
            "memory": "memory",
            "pipeline": "pipeline",
        }
        capability = key_to_capability.get(routing_key, routing_key)
        # Use routing key as policy name if no policy specified
        pn = policy_name or routing_key
        return self.select(
            capability=capability,
            agent_model=agent_model,
            policy_name=pn,
        )


# ── Phase γ-4: span / decision-record helpers ─────────────────


def _set_span_attr(s: Any, key: str, value: Any) -> None:
    """Best-effort ``set_attribute`` on a (possibly no-op) span."""
    with contextlib.suppress(Exception):
        s.set_attribute(key, value)


def _record_selector_decision(
    *,
    trace_id: str,
    capability: str,
    policy_name: str,
    strategy: str,
    effective: EffectiveModel,
    agent_model: str,
    duration_ms: float,
    quota_enforcer_present: bool,
    load_balancer_present: bool,
    quota_check_result: str = "skipped",
    fallback_used: bool = False,
) -> None:
    """Persist a :class:`RoutingDecisionRecord` for ``ModelSelector.select``.

    ``quota_check_result`` / ``fallback_used`` come from the selector
    instance's ``_last_quota_check`` / ``_last_fallback_used`` attrs,
    which are stashed by ``_select_impl`` so this helper can report
    whether the primary was kept or a fallback was substituted.
    """
    otel_trace_id, span_id, parent_span_id = get_active_span_context()
    effective_trace = trace_id or otel_trace_id

    if effective.model_name == "unknown":
        explanation = (
            f"No model found for capability='{capability}' "
            f"(strategy={strategy}, policy={policy_name}). "
            f"Returning unknown placeholder."
        )
    elif fallback_used:
        explanation = (
            f"Selected model '{effective.model_name}' (provider: "
            f"{effective.provider}) via {strategy} strategy after quota "
            f"fallback. Quota: {quota_check_result}."
        )
    else:
        load_note = "load-aware" if load_balancer_present else "strategy-only"
        explanation = (
            f"Selected model '{effective.model_name}' (provider: "
            f"{effective.provider}) via {strategy} strategy ({load_note}). "
            f"Quota: {quota_check_result}. No fallback needed."
        )

    try:
        MAOP_ROUTING_DECISION_TOTAL.inc(labels={"stage": "model_selector"})
        MAOP_ROUTING_DECISION_DURATION_MS.observe(duration_ms)
    except Exception as exc:  # noqa: BLE001 — instrumentation must not abort selection
        # Observability must never take down the routing path: a metric
        # recording failure (e.g. unknown label key / collector error) is
        # logged at WARNING so it is actually visible and debuggable, rather
        # than silently dropped at debug level, but selection still proceeds.
        logger.warning(
            "Failed to record routing decision metrics "
            "(MAOP_ROUTING_DECISION_TOTAL / DURATION_MS) for stage='model_selector': %s",
            exc,
        )
        logger.debug("routing decision metric detail", exc_info=True)

    record_decision_safe(
        RoutingDecisionRecord(
            trace_id=effective_trace,
            span_id=span_id,
            parent_span_id=parent_span_id,
            timestamp=time.time(),
            stage="model_selector",
            input_summary={
                "capability": capability,
                "policy_name": policy_name,
                "agent_model": agent_model,
                "strategy": strategy,
            },
            output_summary={
                "selected_model": effective.model_name,
                "selected_provider": effective.provider,
                "fallback_count": len(effective.fallback_chain),
                "cost_estimate": effective.cost_estimate,
            },
            explanation=explanation,
            duration_ms=duration_ms,
            attributes={
                "selected_model": effective.model_name,
                "selected_provider": effective.provider,
                "strategy": strategy,
                "quota_check_result": quota_check_result,
                "fallback_used": fallback_used,
            },
        )
    )
