"""Tests for default_provider / default_model configuration.

Phase 2: OmniRoute upgraded to default LLM exit.
Tests cover:
  - ModelRegistryConfig stores default_provider / default_model fields
  - ModelRegistry.get_default_model() / get_default_provider() accessors
  - ModelSelector Step 1.5: default provider preference
  - LLMProviderFactory._get_default_model() helper
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from maop.model.selector import ModelSelector
from maop.model.schema import (
    ModelDef, ModelRegistryConfig, ModelPolicy,
    SelectionStrategy, QualityTier, LatencyTier,
)


# ── Helpers ──────────────────────────────────────────────────

def _make_model(
    name: str,
    provider: str = "omniroute",
    capabilities: list[str] | None = None,
    quality: QualityTier = QualityTier.GOOD,
    latency: LatencyTier = LatencyTier.MEDIUM,
    cost_in: float = 0.001,
    cost_out: float = 0.001,
    enabled: bool = True,
) -> ModelDef:
    return ModelDef(
        name=name,
        provider=provider,
        capabilities=capabilities or ["codegen"],
        quality_tier=quality,
        latency_tier=latency,
        cost_per_1k_input=cost_in,
        cost_per_1k_output=cost_out,
        enabled=enabled,
    )


_POLICIES = {
    "default": ModelPolicy(
        strategy=SelectionStrategy.BEST_QUALITY_WITHIN_BUDGET,
        max_cost_per_task=0.05,
    ),
}


def _build_registry(
    models: dict[str, ModelDef],
    default_provider: str = "",
    default_model: str = "",
    healthy_providers: set[str] | None = None,
) -> MagicMock:
    """Build a mock ModelRegistry with the given models and defaults.

    Mirrors the real ModelRegistry.best_model behaviour: filters by
    capability, cost, and provider health, then ranks by strategy.
    """
    reg = MagicMock()
    healthy = healthy_providers if healthy_providers is not None else set()

    def _is_healthy(name: str) -> bool:
        # When healthy set is empty, all providers are considered healthy
        # (mirrors ProviderRegistry default: unknown = healthy).
        return len(healthy) == 0 or name in healthy

    def _best_impl(cap, strategy="best_quality", max_cost=None):
        candidates = [m for m in models.values() if m.enabled and cap in m.capabilities]
        if not candidates:
            return None
        # Filter by provider health (mirrors real best_model)
        healthy_candidates = [m for m in candidates if _is_healthy(m.provider)]
        if healthy_candidates:
            candidates = healthy_candidates
        # Filter by cost
        if max_cost is not None:
            candidates = [m for m in candidates
                          if m.cost_per_1k_input + m.cost_per_1k_output <= max_cost]
            if not candidates:
                return None
        quality_order = {QualityTier.EXCELLENT: 4, QualityTier.GOOD: 3,
                         QualityTier.FAIR: 2, QualityTier.POOR: 1}
        latency_order = {LatencyTier.INSTANT: 4, LatencyTier.FAST: 3,
                         LatencyTier.MEDIUM: 2, LatencyTier.SLOW: 1}
        if strategy == "cheapest":
            candidates.sort(key=lambda m: m.cost_per_1k_input + m.cost_per_1k_output)
        elif strategy == "fastest":
            candidates.sort(key=lambda m: -latency_order.get(m.latency_tier, 0))
        elif strategy == "best_quality":
            candidates.sort(key=lambda m: -quality_order.get(m.quality_tier, 0))
        elif strategy == "best_quality_within_budget":
            candidates.sort(
                key=lambda m: (-quality_order.get(m.quality_tier, 0),
                               -latency_order.get(m.latency_tier, 0))
            )
        return candidates[0] if candidates else None

    reg.get_default_provider = lambda: default_provider
    reg.get_default_model = lambda name="": models.get(default_model) if default_model else None
    reg.get_model = lambda n: models.get(n)
    reg.get_policy = lambda n="default": _POLICIES.get(n) or _POLICIES.get("default")
    reg.resolve_agent_model = lambda agent_model, model_ref="": None
    reg.models_by_provider = lambda p: [m for m in models.values() if m.enabled and m.provider == p]
    reg.models_by_capability = lambda cap: [m for m in models.values() if m.enabled and cap in m.capabilities]
    reg.best_model = _best_impl
    reg.providers = MagicMock()
    reg.providers.is_healthy = _is_healthy
    return reg


# ── ModelRegistryConfig field tests ─────────────────────────

class TestModelRegistryConfigDefaults:
    """Test ModelRegistryConfig stores default_provider / default_model."""

    def test_config_stores_default_provider(self):
        config = ModelRegistryConfig(
            default_provider="omniroute",
            default_model="omniroute-auto-coding",
        )
        assert config.default_provider == "omniroute"
        assert config.default_model == "omniroute-auto-coding"

    def test_config_defaults_to_empty(self):
        config = ModelRegistryConfig()
        assert config.default_provider == ""
        assert config.default_model == ""


# ── ModelSelector Step 1.5 tests ────────────────────────────

class TestDefaultProviderPreference:
    """Test ModelSelector Step 1.5: default provider preference."""

    def test_default_provider_preferred_for_capability(self):
        """When default_provider is set, its models are preferred as primary."""
        # omniroute model is FAIR quality; other provider's model is EXCELLENT.
        # Without default_provider, best_quality would pick the EXCELLENT one.
        # With default_provider=omniroute, Step 1.5 should pick the omniroute one.
        models = {
            "omniroute-auto-coding": _make_model(
                "omniroute-auto-coding", provider="omniroute",
                capabilities=["codegen"], quality=QualityTier.FAIR,
            ),
            "other-premium": _make_model(
                "other-premium", provider="other",
                capabilities=["codegen"], quality=QualityTier.EXCELLENT,
            ),
        }
        reg = _build_registry(
            models, default_provider="omniroute",
            healthy_providers={"omniroute", "other"},
        )
        selector = ModelSelector(reg)
        result = selector.select(capability="codegen", policy_name="default")
        assert result.model_name == "omniroute-auto-coding"
        assert result.provider == "omniroute"

    def test_no_default_provider_uses_legacy_selection(self):
        """When default_provider is empty, legacy strategy-based selection is used."""
        models = {
            "omniroute-auto-coding": _make_model(
                "omniroute-auto-coding", provider="omniroute",
                capabilities=["codegen"], quality=QualityTier.FAIR,
            ),
            "other-premium": _make_model(
                "other-premium", provider="other",
                capabilities=["codegen"], quality=QualityTier.EXCELLENT,
            ),
        }
        reg = _build_registry(
            models, default_provider="",
            healthy_providers={"omniroute", "other"},
        )
        selector = ModelSelector(reg)
        result = selector.select(capability="codegen", policy_name="default")
        # Legacy best_quality_within_budget should pick EXCELLENT
        assert result.model_name == "other-premium"

    def test_default_provider_no_capability_match_falls_through(self):
        """If default provider has no model for the capability, fall through to legacy."""
        models = {
            "omniroute-auto-reasoning": _make_model(
                "omniroute-auto-reasoning", provider="omniroute",
                capabilities=["reasoning"], quality=QualityTier.EXCELLENT,
            ),
            "other-coder": _make_model(
                "other-coder", provider="other",
                capabilities=["codegen"], quality=QualityTier.GOOD,
            ),
        }
        reg = _build_registry(
            models, default_provider="omniroute",
            healthy_providers={"omniroute", "other"},
        )
        selector = ModelSelector(reg)
        # Requesting "codegen" but omniroute only has "reasoning"
        result = selector.select(capability="codegen", policy_name="default")
        # Should fall through to legacy and pick other-coder
        assert result.model_name == "other-coder"

    def test_default_provider_skipped_when_agent_model_found(self):
        """Step 1.5 is skipped when Step 1 resolves an agent model."""
        models = {
            "omniroute-auto-coding": _make_model(
                "omniroute-auto-coding", provider="omniroute",
                capabilities=["codegen"], quality=QualityTier.EXCELLENT,
            ),
            "explicit-model": _make_model(
                "explicit-model", provider="other",
                capabilities=["codegen"], quality=QualityTier.GOOD,
            ),
        }
        reg = _build_registry(
            models, default_provider="omniroute",
            healthy_providers={"omniroute", "other"},
        )
        # Make resolve_agent_model return the explicit model
        reg.resolve_agent_model = lambda agent_model, model_ref="": models.get(agent_model)
        selector = ModelSelector(reg)
        result = selector.select(
            capability="codegen", agent_model="explicit-model", policy_name="default",
        )
        # Should use the explicitly specified model, not the default provider
        assert result.model_name == "explicit-model"

    def test_default_provider_unhealthy_skipped(self):
        """If default provider is unhealthy, its models are skipped in Step 1.5."""
        models = {
            "omniroute-auto-coding": _make_model(
                "omniroute-auto-coding", provider="omniroute",
                capabilities=["codegen"], quality=QualityTier.EXCELLENT,
            ),
            "other-coder": _make_model(
                "other-coder", provider="other",
                capabilities=["codegen"], quality=QualityTier.GOOD,
            ),
        }
        reg = _build_registry(
            models, default_provider="omniroute",
            healthy_providers={"other"},  # omniroute NOT healthy
        )
        selector = ModelSelector(reg)
        result = selector.select(capability="codegen", policy_name="default")
        # omniroute unhealthy -> fall through to legacy -> pick other-coder
        assert result.model_name == "other-coder"


# ── ModelRegistry.get_default_model / get_default_provider ──

class TestRegistryDefaultAccessors:
    """Test ModelRegistry default provider/model accessor methods.

    Uses ModelRegistry.__new__ to bypass __init__ (which loads yaml).
    """

    def test_get_default_provider_returns_configured(self):
        from maop.model.registry import ModelRegistry
        reg = ModelRegistry.__new__(ModelRegistry)
        reg._config = ModelRegistryConfig(default_provider="omniroute")
        assert reg.get_default_provider() == "omniroute"

    def test_get_default_provider_returns_empty_when_not_configured(self):
        from maop.model.registry import ModelRegistry
        reg = ModelRegistry.__new__(ModelRegistry)
        reg._config = ModelRegistryConfig()
        assert reg.get_default_provider() == ""

    def test_get_default_model_returns_configured(self):
        from maop.model.registry import ModelRegistry
        reg = ModelRegistry.__new__(ModelRegistry)
        m = _make_model("omniroute-auto-coding", provider="omniroute", capabilities=["codegen"])
        reg._config = ModelRegistryConfig(
            default_model="omniroute-auto-coding",
            models={"omniroute-auto-coding": m},
        )
        result = reg.get_default_model()
        assert result is not None
        assert result.name == "omniroute-auto-coding"

    def test_get_default_model_returns_none_when_not_configured(self):
        from maop.model.registry import ModelRegistry
        reg = ModelRegistry.__new__(ModelRegistry)
        reg._config = ModelRegistryConfig()
        assert reg.get_default_model() is None

    def test_get_default_model_returns_none_when_model_not_found(self):
        from maop.model.registry import ModelRegistry
        reg = ModelRegistry.__new__(ModelRegistry)
        reg._config = ModelRegistryConfig(default_model="nonexistent-model")
        assert reg.get_default_model() is None


# ── LLMProviderFactory._get_default_model ───────────────────

class TestLLMProviderFactoryDefaultModel:
    """Test LLMProviderFactory._get_default_model() helper."""

    def test_get_default_model_returns_configured(self):
        from maop.core.llm_provider import LLMProviderFactory
        factory = LLMProviderFactory()
        factory._loaded = True  # bypass _ensure_loaded
        factory._default_model = "omniroute-auto-coding"
        assert factory._get_default_model() == "omniroute-auto-coding"

    def test_get_default_model_returns_empty_when_not_configured(self):
        from maop.core.llm_provider import LLMProviderFactory
        factory = LLMProviderFactory()
        factory._loaded = True
        assert factory._get_default_model() == ""

    def test_parse_models_yaml_loads_defaults(self):
        """_parse_models_yaml loads default_provider/default_model from yaml data."""
        from maop.core.llm_provider import LLMProviderFactory
        factory = LLMProviderFactory()
        data = {
            "default_provider": "omniroute",
            "default_model": "omniroute-auto-coding",
            "providers": {},
            "models": {},
        }
        factory._parse_models_yaml(data)
        assert factory._default_provider == "omniroute"
        assert factory._default_model == "omniroute-auto-coding"

    def test_parse_models_yaml_defaults_to_empty(self):
        """_parse_models_yaml defaults to empty strings when fields absent."""
        from maop.core.llm_provider import LLMProviderFactory
        factory = LLMProviderFactory()
        factory._parse_models_yaml({"providers": {}, "models": {}})
        assert factory._default_provider == ""
        assert factory._default_model == ""