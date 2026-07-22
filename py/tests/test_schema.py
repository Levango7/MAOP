"""Comprehensive tests for MAOP.model.schema — Pydantic models & enums."""

from __future__ import annotations

import pytest

from maop.model.schema import (
    ProviderType, LatencyTier, QualityTier, SelectionStrategy,
    ProviderDef, ModelDef, ModelPolicy, BudgetConfig, QuotaConfig,
    ModelRegistryConfig, EffectiveModel,
)


# ── Enum Tests ────────────────────────────────────────────────

class TestProviderType:
    """Tests for ProviderType enum."""

    def test_values(self):
        assert ProviderType.OPENAI_COMPATIBLE.value == "openai-compatible"
        assert ProviderType.CUSTOM.value == "custom"
        assert ProviderType.BUILTIN.value == "builtin"

    def test_from_value(self):
        assert ProviderType("openai-compatible") == ProviderType.OPENAI_COMPATIBLE
        assert ProviderType("builtin") == ProviderType.BUILTIN

    def test_invalid_value(self):
        with pytest.raises(ValueError):
            ProviderType("invalid")

    def test_is_str_enum(self):
        assert isinstance(ProviderType.BUILTIN, str)


class TestLatencyTier:
    """Tests for LatencyTier enum."""

    def test_values(self):
        assert LatencyTier.INSTANT.value == "instant"
        assert LatencyTier.FAST.value == "fast"
        assert LatencyTier.MEDIUM.value == "medium"
        assert LatencyTier.SLOW.value == "slow"

    def test_from_value(self):
        assert LatencyTier("fast") == LatencyTier.FAST


class TestQualityTier:
    """Tests for QualityTier enum."""

    def test_values(self):
        assert QualityTier.EXCELLENT.value == "excellent"
        assert QualityTier.GOOD.value == "good"
        assert QualityTier.FAIR.value == "fair"
        assert QualityTier.POOR.value == "poor"

    def test_from_value(self):
        assert QualityTier("excellent") == QualityTier.EXCELLENT


class TestSelectionStrategy:
    """Tests for SelectionStrategy enum."""

    def test_values(self):
        assert SelectionStrategy.CHEAPEST.value == "cheapest"
        assert SelectionStrategy.FASTEST.value == "fastest"
        assert SelectionStrategy.BEST_QUALITY.value == "best_quality"
        assert SelectionStrategy.BEST_QUALITY_WITHIN_BUDGET.value == "best_quality_within_budget"


# ── ProviderDef Tests ────────────────────────────────────────

class TestProviderDef:
    """Tests for ProviderDef model."""

    def test_defaults(self):
        p = ProviderDef()
        assert p.type == ProviderType.OPENAI_COMPATIBLE
        assert p.base_url == ""
        assert p.api_key_env == ""
        assert p.timeout_s == 120
        assert p.max_retries == 3
        assert p.health_check_url == ""
        assert p.enabled is True

    def test_custom_values(self):
        p = ProviderDef(
            type=ProviderType.BUILTIN,
            base_url="http://localhost:8080",
            api_key_env="MY_KEY",
            timeout_s=60,
            max_retries=5,
            enabled=False,
        )
        assert p.type == ProviderType.BUILTIN
        assert p.base_url == "http://localhost:8080"
        assert p.timeout_s == 60
        assert p.max_retries == 5
        assert p.enabled is False

    def test_from_dict_with_string_type(self):
        p = ProviderDef(**{"type": "builtin", "api_key_env": "KEY"})
        assert p.type == ProviderType.BUILTIN

    def test_serialization(self):
        p = ProviderDef(type=ProviderType.CUSTOM, base_url="http://x")
        d = p.model_dump()
        assert d["type"] == ProviderType.CUSTOM
        assert d["base_url"] == "http://x"


# ── ModelDef Tests ───────────────────────────────────────────

class TestModelDef:
    """Tests for ModelDef model."""

    def test_defaults(self):
        m = ModelDef()
        assert m.name == ""
        assert m.provider == ""
        assert m.context_window == 32768
        assert m.max_output == 8192
        assert m.cost_per_1k_input == 0.0
        assert m.cost_per_1k_output == 0.0
        assert m.capabilities == []
        assert m.latency_tier == LatencyTier.MEDIUM
        assert m.quality_tier == QualityTier.GOOD
        assert m.enabled is True

    def test_custom_values(self):
        m = ModelDef(
            name="gpt-4", provider="openai",
            context_window=128000, max_output=4096,
            cost_per_1k_input=0.03, cost_per_1k_output=0.06,
            capabilities=["codegen", "chat"],
            latency_tier=LatencyTier.SLOW,
            quality_tier=QualityTier.EXCELLENT,
        )
        assert m.name == "gpt-4"
        assert m.context_window == 128000
        assert "codegen" in m.capabilities

    def test_capabilities_default_factory(self):
        m1 = ModelDef()
        m2 = ModelDef()
        m1.capabilities.append("chat")
        assert m2.capabilities == []  # independent lists

    def test_from_dict_with_string_enums(self):
        m = ModelDef(**{
            "name": "test",
            "quality_tier": "excellent",
            "latency_tier": "fast",
        })
        assert m.quality_tier == QualityTier.EXCELLENT
        assert m.latency_tier == LatencyTier.FAST


# ── ModelPolicy Tests ────────────────────────────────────────

class TestModelPolicy:
    """Tests for ModelPolicy model."""

    def test_defaults(self):
        p = ModelPolicy()
        assert p.strategy == SelectionStrategy.BEST_QUALITY_WITHIN_BUDGET
        assert p.max_cost_per_task == 0.05
        assert p.prefer_low_latency is False
        assert p.fallback_on_error is True
        assert p.fallback_on_timeout is True
        assert p.fallback_on_quota_exceeded is True

    def test_custom(self):
        p = ModelPolicy(
            strategy=SelectionStrategy.CHEAPEST,
            max_cost_per_task=0.02,
            prefer_low_latency=True,
            fallback_on_error=False,
        )
        assert p.strategy == SelectionStrategy.CHEAPEST
        assert p.max_cost_per_task == 0.02
        assert p.prefer_low_latency is True
        assert p.fallback_on_error is False


# ── BudgetConfig Tests ───────────────────────────────────────

class TestBudgetConfig:
    """Tests for BudgetConfig model."""

    def test_defaults(self):
        b = BudgetConfig()
        assert b.daily_limit == 5.0
        assert b.monthly_limit == 100.0
        assert b.alert_threshold == 0.8
        assert b.hard_stop is True

    def test_custom(self):
        b = BudgetConfig(daily_limit=10.0, monthly_limit=200.0, alert_threshold=0.9, hard_stop=False)
        assert b.daily_limit == 10.0
        assert b.hard_stop is False


# ── QuotaConfig Tests ────────────────────────────────────────

class TestQuotaConfig:
    """Tests for QuotaConfig model."""

    def test_defaults(self):
        q = QuotaConfig()
        assert q.requests_per_minute == 60
        assert q.tokens_per_minute == 100000

    def test_custom(self):
        q = QuotaConfig(requests_per_minute=30, tokens_per_minute=50000)
        assert q.requests_per_minute == 30
        assert q.tokens_per_minute == 50000


# ── ModelRegistryConfig Tests ────────────────────────────────

class TestModelRegistryConfig:
    """Tests for ModelRegistryConfig model."""

    def test_defaults(self):
        c = ModelRegistryConfig()
        assert c.providers == {}
        assert c.models == {}
        assert c.policies == {}
        assert c.quota == {}
        assert isinstance(c.budget, BudgetConfig)

    def test_with_values(self):
        model = ModelDef(name="test")
        provider = ProviderDef()
        policy = ModelPolicy()
        budget = BudgetConfig(daily_limit=3.0)
        quota = QuotaConfig()

        c = ModelRegistryConfig(
            providers={"p": provider},
            models={"m": model},
            policies={"default": policy},
            budget=budget,
            quota={"p": quota},
        )
        assert "p" in c.providers
        assert "m" in c.models
        assert c.budget.daily_limit == 3.0

    def test_dict_default_factory_independence(self):
        c1 = ModelRegistryConfig()
        c2 = ModelRegistryConfig()
        c1.models["x"] = ModelDef(name="x")
        assert "x" not in c2.models


# ── EffectiveModel Tests ─────────────────────────────────────

class TestEffectiveModel:
    """Tests for EffectiveModel model."""

    def test_required_fields(self):
        m = EffectiveModel(model_name="gpt-4", provider="openai")
        assert m.model_name == "gpt-4"
        assert m.provider == "openai"
        assert m.cli_model_arg == ""
        assert m.cost_estimate == 0.0
        assert m.fallback_chain == []
        assert m.policy_name == "default"

    def test_full(self):
        m = EffectiveModel(
            model_name="gpt-4", provider="openai",
            cli_model_arg="gpt-4", cost_estimate=0.05,
            fallback_chain=["gpt-3.5", "local"],
            policy_name="codegen",
        )
        assert m.cli_model_arg == "gpt-4"
        assert m.cost_estimate == 0.05
        assert len(m.fallback_chain) == 2
        assert m.policy_name == "codegen"

    def test_fallback_chain_default_factory(self):
        m1 = EffectiveModel(model_name="a", provider="p")
        m2 = EffectiveModel(model_name="b", provider="p")
        m1.fallback_chain.append("x")
        assert m2.fallback_chain == []

    def test_missing_required_field(self):
        with pytest.raises(Exception):
            EffectiveModel(provider="openai")  # missing model_name
