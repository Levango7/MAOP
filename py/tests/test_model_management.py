"""Tests for MAOP.model module — Model Management Phase 1."""

import pytest
from pathlib import Path

from maop.model.schema import (
    ModelDef, ProviderDef, BudgetConfig, EffectiveModel, ProviderType, QualityTier, SelectionStrategy,
)
from maop.model.registry import ModelRegistry
from maop.model.selector import ModelSelector
from maop.model.fallback import FallbackManager
from maop.model.quota import QuotaEnforcer
from maop.model.budget import BudgetGuard


# Fixtures

@pytest.fixture
def MAOP_ROOT():
    # tests/ is under py/, config/ is under MAOP root (two levels up from py/)
    return Path(__file__).resolve().parent.parent.parent


@pytest.fixture
def registry(MAOP_ROOT):
    return ModelRegistry(project_root=MAOP_ROOT)


@pytest.fixture
def selector(registry):
    return ModelSelector(registry)


@pytest.fixture
def fallback_mgr(registry):
    return FallbackManager(registry)


@pytest.fixture
def quota_enforcer(registry):
    return QuotaEnforcer(registry)


@pytest.fixture
def budget_guard(MAOP_ROOT, tmp_path):
    # Use tmp_path for budget ledger to avoid polluting real data
    return BudgetGuard(root_dir=tmp_path, config=BudgetConfig(daily_limit=5.0, monthly_limit=100.0))


# ── Schema tests ──────────────────────────────────────────────

class TestSchema:
    def test_provider_def_defaults(self):
        p = ProviderDef()
        assert p.type == ProviderType.OPENAI_COMPATIBLE
        assert p.enabled is True
        assert p.timeout_s == 120

    def test_model_def_defaults(self):
        m = ModelDef(name="test")
        assert m.name == "test"
        assert m.context_window == 32768
        assert m.capabilities == []

    def test_effective_model(self):
        em = EffectiveModel(model_name="yi-large", provider="stepfun")
        assert em.model_name == "yi-large"
        assert em.fallback_chain == []
        assert em.policy_name == "default"

    def test_quality_tier_order(self):
        assert QualityTier.EXCELLENT != QualityTier.GOOD

    def test_selection_strategy_values(self):
        assert SelectionStrategy.BEST_QUALITY_WITHIN_BUDGET.value == "best_quality_within_budget"


# ── Registry tests ────────────────────────────────────────────

class TestModelRegistry:
    def test_loads_models_yaml(self, registry):
        assert len(registry.config.models) > 0
        assert "yi-large" in registry.config.models
        assert "step-3.7-flash" in registry.config.models

    def test_loads_providers(self, registry):
        assert "stepfun" in registry.config.providers
        assert "minimax" in registry.config.providers

    def test_loads_policies(self, registry):
        assert "default" in registry.config.policies
        assert "codegen" in registry.config.policies

    def test_loads_budget(self, registry):
        assert registry.config.budget.daily_limit > 0
        assert registry.config.budget.monthly_limit > 0

    def test_loads_quota(self, registry):
        assert "stepfun" in registry.config.quota
        assert registry.config.quota["stepfun"].requests_per_minute > 0

    def test_get_model(self, registry):
        m = registry.get_model("yi-large")
        assert m is not None
        assert m.provider == "stepfun"
        assert "codegen" in m.capabilities

    def test_get_model_not_found(self, registry):
        assert registry.get_model("nonexistent") is None

    def test_models_by_capability(self, registry):
        models = registry.models_by_capability("codegen")
        assert len(models) > 0
        assert all("codegen" in m.capabilities for m in models)

    def test_best_model_best_quality(self, registry):
        m = registry.best_model("codegen", strategy="best_quality")
        assert m is not None
        assert m.quality_tier == QualityTier.EXCELLENT

    def test_best_model_cheapest(self, registry):
        m = registry.best_model("codegen", strategy="cheapest")
        assert m is not None
        # Cheapest should have lowest cost
        all_models = registry.models_by_capability("codegen")
        min_cost = min(x.cost_per_1k_input + x.cost_per_1k_output for x in all_models)
        assert m.cost_per_1k_input + m.cost_per_1k_output == min_cost

    def test_best_model_with_max_cost(self, registry):
        m = registry.best_model("codegen", strategy="best_quality", max_cost=0.001)
        if m:
            assert m.cost_per_1k_input + m.cost_per_1k_output <= 0.001

    def test_resolve_agent_model_exact(self, registry):
        m = registry.resolve_agent_model("yi-large")
        assert m is not None
        assert m.name == "yi-large"

    def test_resolve_agent_model_descriptive(self, registry):
        m = registry.resolve_agent_model("step-3.7-flash (via proxy)")
        assert m is not None
        assert m.name == "step-3.7-flash"

    def test_resolve_agent_model_unknown(self, registry):
        assert registry.resolve_agent_model("unknown-model") is None

    def test_stats(self, registry):
        s = registry.stats()
        assert s["total_models"] > 0
        assert s["enabled_models"] > 0
        assert "by_quality" in s
        assert "by_latency" in s

    def test_reload(self, registry):
        cfg1 = registry.config
        cfg2 = registry.reload()
        assert len(cfg2.models) == len(cfg1.models)


class TestProviderRegistry:
    def test_list_providers(self, registry):
        providers = registry.providers.list_providers()
        assert len(providers) > 0
        assert any(p["name"] == "stepfun" for p in providers)

    def test_is_enabled(self, registry):
        assert registry.providers.is_enabled("stepfun") is True

    def test_check_health_builtin(self, registry):
        assert registry.providers.check_health("local") is True

    def test_mark_unhealthy(self, registry):
        registry.providers.mark_unhealthy("stepfun", "test error")
        assert registry.providers.is_healthy("stepfun") is False
        registry.providers.mark_healthy("stepfun")
        assert registry.providers.is_healthy("stepfun") is True


# ── Selector tests ────────────────────────────────────────────

class TestModelSelector:
    def test_select_with_agent_model(self, selector):
        em = selector.select(capability="codegen", agent_model="yi-large")
        assert em.model_name == "yi-large"
        assert em.provider == "stepfun"
        assert em.cost_estimate >= 0

    def test_select_by_capability(self, selector):
        em = selector.select(capability="codegen")
        assert em.model_name != "unknown"
        assert em.provider != ""

    def test_select_with_policy(self, selector):
        em = selector.select(capability="codegen", policy_name="codegen")
        assert em.policy_name == "codegen"

    def test_select_fallback_chain(self, selector):
        em = selector.select(capability="codegen")
        # Fallback chain should not include primary
        assert em.model_name not in em.fallback_chain

    def test_select_for_routing_key(self, selector):
        em = selector.select_for_routing_key("codegen", agent_model="yi-large")
        assert em.model_name == "yi-large"

    def test_select_unknown_capability(self, selector):
        em = selector.select(capability="nonexistent")
        # Should fall back to built-in or unknown
        assert em.model_name in ("built-in", "unknown")


# ── Fallback tests ────────────────────────────────────────────

class TestFallbackManager:
    def test_get_chain(self, fallback_mgr, selector):
        em = selector.select(capability="codegen")
        chain = fallback_mgr.get_chain(em)
        assert em.model_name in chain

    def test_record_success(self, fallback_mgr):
        fallback_mgr.record_failure("yi-large")
        fallback_mgr.record_success("yi-large")
        assert fallback_mgr._failure_counts.get("yi-large", 0) == 0

    def test_record_failure(self, fallback_mgr):
        fallback_mgr.record_failure("yi-large")
        fallback_mgr.record_failure("yi-large")
        assert fallback_mgr._failure_counts["yi-large"] == 2

    def test_should_fallback_on_error(self, fallback_mgr):
        assert fallback_mgr.should_fallback("some error") is True

    def test_should_fallback_on_timeout(self, fallback_mgr):
        assert fallback_mgr.should_fallback("TIMEOUT after 30s") is True

    def test_should_not_fallback_if_disabled(self, fallback_mgr):
        assert fallback_mgr.should_fallback("error", policy_fallback_on_error=False) is False

    def test_failure_stats(self, fallback_mgr):
        fallback_mgr.record_failure("model-a")
        stats = fallback_mgr.get_failure_stats()
        assert "model-a" in stats


# ── Quota tests ───────────────────────────────────────────────

class TestQuotaEnforcer:
    def test_check_within_quota(self, quota_enforcer):
        assert quota_enforcer.check("stepfun", tokens=100) is True

    def test_check_and_consume(self, quota_enforcer):
        assert quota_enforcer.check_and_consume("stepfun", tokens=100) is True
        usage = quota_enforcer.usage("stepfun")
        assert usage["requests_used"] == 1
        assert usage["tokens_used"] == 100

    def test_usage_all(self, quota_enforcer):
        usages = quota_enforcer.usage_all()
        assert len(usages) > 0
        assert any(u["provider"] == "stepfun" for u in usages)

    def test_quota_exceeded(self, quota_enforcer):
        # Exhaust request quota
        quota = quota_enforcer._get_quota("opencode")
        for _ in range(quota.requests_per_minute):
            quota_enforcer.consume("opencode")
        assert quota_enforcer.check("opencode") is False


# ── Budget tests ──────────────────────────────────────────────

class TestBudgetGuard:
    def test_can_spend_within_budget(self, budget_guard):
        assert budget_guard.can_spend(0.01) is True

    def test_cannot_spend_over_daily(self, budget_guard):
        # Record near-limit spending
        budget_guard.record("test", "test", cost=4.99)
        assert budget_guard.can_spend(0.05) is False

    def test_record_and_stats(self, budget_guard):
        budget_guard.record("yi-large", "stepfun", cost=0.005, tokens_in=1000, tokens_out=500)
        stats = budget_guard.stats()
        assert stats["daily_spend"] >= 0.005
        assert stats["daily_remaining"] < 5.0

    def test_alert_threshold(self, budget_guard):
        # Spend to 80% of daily limit
        budget_guard.record("test", "test", cost=4.0)
        stats = budget_guard.stats()
        assert stats["alerted"] is True

    def test_hard_stop_disabled(self, tmp_path):
        guard = BudgetGuard(root_dir=tmp_path, config=BudgetConfig(hard_stop=False))
        guard.record("test", "test", cost=100.0)
        assert guard.can_spend(100.0) is True
