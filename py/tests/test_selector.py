"""Comprehensive tests for MAOP.model.selector — ModelSelector."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from maop.model.schema import (
    EffectiveModel,
    LatencyTier,
    ModelDef,
    ModelPolicy,
    QualityTier,
    SelectionStrategy,
)
from maop.model.selector import ModelSelector

# ── Fixtures ──────────────────────────────────────────────────

def _make_model(name, provider="openai", capabilities=None,
                quality=QualityTier.GOOD, latency=LatencyTier.MEDIUM,
                cost_in=0.01, cost_out=0.02, enabled=True):
    return ModelDef(
        name=name, provider=provider,
        capabilities=capabilities or ["chat"],
        quality_tier=quality, latency_tier=latency,
        cost_per_1k_input=cost_in, cost_per_1k_output=cost_out,
        enabled=enabled,
    )


@pytest.fixture
def mock_registry():
    """A mock ModelRegistry with pre-populated models."""
    reg = MagicMock()

    models = {
        "gpt-4": _make_model("gpt-4", capabilities=["codegen", "chat"],
                             quality=QualityTier.EXCELLENT, latency=LatencyTier.SLOW,
                             cost_in=0.03, cost_out=0.06),
        "gpt-3.5": _make_model("gpt-3.5", capabilities=["chat"],
                               quality=QualityTier.GOOD, latency=LatencyTier.FAST,
                               cost_in=0.001, cost_out=0.002),
        "local-model": _make_model("local-model", provider="local",
                                   capabilities=["codegen"],
                                   quality=QualityTier.FAIR, latency=LatencyTier.INSTANT,
                                   cost_in=0.0, cost_out=0.0),
        "built-in": _make_model("built-in", provider="local",
                                capabilities=["chat"],
                                quality=QualityTier.POOR, latency=LatencyTier.FAST,
                                cost_in=0.0, cost_out=0.0),
    }

    reg.get_model = lambda n: models.get(n)
    reg.get_policy = lambda n="default": policies.get(n) or policies.get("default")
    reg.resolve_agent_model = lambda agent_model, model_ref="": _resolve(models, agent_model, model_ref)
    reg.best_model = lambda cap, strategy="best_quality", max_cost=None: _best(models, cap, strategy, max_cost)
    reg.models_by_capability = lambda cap: [m for m in models.values() if m.enabled and cap in m.capabilities]
    reg.providers = MagicMock()
    reg.providers.is_healthy = lambda name: True

    return reg


policies = {
    "default": ModelPolicy(strategy=SelectionStrategy.BEST_QUALITY_WITHIN_BUDGET, max_cost_per_task=0.05),
    "codegen": ModelPolicy(strategy=SelectionStrategy.CHEAPEST, max_cost_per_task=0.01),
    "chat": ModelPolicy(strategy=SelectionStrategy.BEST_QUALITY, max_cost_per_task=0.10),
}


def _resolve(models, agent_model, model_ref):
    if model_ref and model_ref in models:
        return models[model_ref]
    if agent_model and agent_model in models:
        return models[agent_model]
    if agent_model:
        prefix = agent_model.split(" ")[0].split("(")[0].strip()
        if prefix in models:
            return models[prefix]
        if prefix.lower() in models:
            return models[prefix.lower()]
    return None


def _best(models, capability, strategy, max_cost):
    candidates = [m for m in models.values() if m.enabled and capability in m.capabilities]
    if not candidates:
        return None
    if max_cost is not None:
        candidates = [m for m in candidates if m.cost_per_1k_input + m.cost_per_1k_output <= max_cost]
        if not candidates:
            return None
    quality_order = {QualityTier.EXCELLENT: 4, QualityTier.GOOD: 3, QualityTier.FAIR: 2, QualityTier.POOR: 1}
    if strategy == "cheapest":
        candidates.sort(key=lambda m: m.cost_per_1k_input + m.cost_per_1k_output)
    elif strategy == "fastest":
        latency_order = {LatencyTier.INSTANT: 4, LatencyTier.FAST: 3, LatencyTier.MEDIUM: 2, LatencyTier.SLOW: 1}
        candidates.sort(key=lambda m: -latency_order.get(m.latency_tier, 0))
    elif strategy == "best_quality":
        candidates.sort(key=lambda m: -quality_order.get(m.quality_tier, 0))
    return candidates[0] if candidates else None


# ── ModelSelector Tests ──────────────────────────────────────

class TestModelSelectorInit:
    """Tests for ModelSelector initialization."""

    def test_init(self, mock_registry):
        selector = ModelSelector(mock_registry)
        assert selector._registry is mock_registry


class TestSelect:
    """Tests for ModelSelector.select."""

    def test_select_with_agent_model_exact(self, mock_registry):
        selector = ModelSelector(mock_registry)
        result = selector.select(capability="chat", agent_model="gpt-4")
        assert isinstance(result, EffectiveModel)
        assert result.model_name == "gpt-4"
        assert result.provider == "openai"
        assert result.cli_model_arg == "gpt-4"

    def test_select_with_model_ref(self, mock_registry):
        selector = ModelSelector(mock_registry)
        result = selector.select(agent_model="alias", model_ref="gpt-4")
        assert result.model_name == "gpt-4"

    def test_select_by_capability(self, mock_registry):
        selector = ModelSelector(mock_registry)
        result = selector.select(capability="codegen", policy_name="codegen")
        # codegen policy is cheapest → local-model (cost 0)
        assert result.model_name == "local-model"

    def test_select_fallback_to_builtin(self, mock_registry):
        selector = ModelSelector(mock_registry)
        result = selector.select(capability="nonexistent", agent_model="unknown")
        assert result.model_name == "built-in"

    def test_select_no_match_returns_unknown(self, mock_registry):
        # Make built-in not found
        reg = MagicMock()
        reg.get_model = lambda n: None
        reg.get_policy = lambda n="default": policies.get("default")
        reg.resolve_agent_model = lambda am, model_ref="": None
        reg.best_model = lambda cap, **kw: None
        reg.models_by_capability = lambda cap: []
        reg.providers = MagicMock()
        reg.providers.is_healthy = lambda n: True

        selector = ModelSelector(reg)
        result = selector.select(capability="nonexistent", agent_model="unknown")
        assert result.model_name == "unknown"
        assert result.provider == "local"
        assert result.cli_model_arg == "unknown"

    def test_select_cost_estimate(self, mock_registry):
        selector = ModelSelector(mock_registry)
        result = selector.select(
            capability="chat", agent_model="gpt-4",
            task_input_tokens=1000, task_output_tokens=500,
        )
        # cost = 0.03*1000/1000 + 0.06*500/1000 = 0.03 + 0.03 = 0.06
        assert result.cost_estimate == pytest.approx(0.06)

    def test_select_fallback_chain(self, mock_registry):
        selector = ModelSelector(mock_registry)
        result = selector.select(capability="chat", agent_model="gpt-3.5")
        # Fallback chain should include other chat models excluding gpt-3.5
        assert isinstance(result.fallback_chain, list)
        assert "gpt-3.5" not in result.fallback_chain

    def test_select_fallback_chain_limited_to_3(self, mock_registry):
        selector = ModelSelector(mock_registry)
        result = selector.select(capability="chat", agent_model="gpt-4")
        assert len(result.fallback_chain) <= 3

    def test_select_no_capability_no_fallback_chain(self, mock_registry):
        selector = ModelSelector(mock_registry)
        result = selector.select(agent_model="gpt-4")
        assert result.fallback_chain == []

    def test_select_policy_name_in_result(self, mock_registry):
        selector = ModelSelector(mock_registry)
        result = selector.select(capability="chat", policy_name="chat")
        assert result.policy_name == "chat"

    def test_select_default_policy(self, mock_registry):
        selector = ModelSelector(mock_registry)
        result = selector.select(capability="chat")
        assert result.policy_name == "default"

    def test_select_with_max_cost_filter(self, mock_registry):
        selector = ModelSelector(mock_registry)
        # codegen policy has max_cost_per_task=0.01
        result = selector.select(capability="codegen", policy_name="codegen")
        # cheapest with max_cost 0.01 → local-model (0.0)
        assert result.model_name == "local-model"


class TestSelectForRoutingKey:
    """Tests for ModelSelector.select_for_routing_key."""

    def test_codegen_routing(self, mock_registry):
        selector = ModelSelector(mock_registry)
        result = selector.select_for_routing_key("codegen")
        assert isinstance(result, EffectiveModel)

    def test_chat_routing(self, mock_registry):
        selector = ModelSelector(mock_registry)
        result = selector.select_for_routing_key("chat")
        assert isinstance(result, EffectiveModel)

    def test_refactor_maps_to_codegen(self, mock_registry):
        selector = ModelSelector(mock_registry)
        result = selector.select_for_routing_key("refactor")
        assert isinstance(result, EffectiveModel)

    def test_explain_maps_to_chat(self, mock_registry):
        selector = ModelSelector(mock_registry)
        result = selector.select_for_routing_key("explain")
        assert isinstance(result, EffectiveModel)

    def test_quickfix_maps_to_codegen(self, mock_registry):
        selector = ModelSelector(mock_registry)
        result = selector.select_for_routing_key("quickfix")
        assert isinstance(result, EffectiveModel)

    def test_unknown_routing_key_uses_key_as_capability(self, mock_registry):
        selector = ModelSelector(mock_registry)
        result = selector.select_for_routing_key("custom_key")
        assert isinstance(result, EffectiveModel)

    def test_routing_key_with_agent_model(self, mock_registry):
        selector = ModelSelector(mock_registry)
        result = selector.select_for_routing_key("codegen", agent_model="gpt-4")
        assert result.model_name == "gpt-4"

    def test_routing_key_with_explicit_policy(self, mock_registry):
        selector = ModelSelector(mock_registry)
        result = selector.select_for_routing_key("codegen", policy_name="codegen")
        assert isinstance(result, EffectiveModel)


class TestBuildFallbackChain:
    """Tests for ModelSelector._build_fallback_chain."""

    def test_build_fallback_excludes_primary(self, mock_registry):
        selector = ModelSelector(mock_registry)
        primary = _make_model("gpt-4", capabilities=["chat"])
        chain = selector._build_fallback_chain(primary, "chat", "best_quality", None)
        assert "gpt-4" not in chain

    def test_build_fallback_no_capability(self, mock_registry):
        selector = ModelSelector(mock_registry)
        primary = _make_model("gpt-4")
        chain = selector._build_fallback_chain(primary, "", "best_quality", None)
        assert chain == []

    def test_build_fallback_max_three(self, mock_registry):
        selector = ModelSelector(mock_registry)
        primary = _make_model("gpt-4", capabilities=["chat"])
        chain = selector._build_fallback_chain(primary, "chat", "best_quality", None)
        assert len(chain) <= 3

    def test_build_fallback_filters_unhealthy(self, mock_registry):
        # Make one provider unhealthy
        mock_registry.providers.is_healthy = lambda name: name != "openai"
        selector = ModelSelector(mock_registry)
        primary = _make_model("local-model", provider="local", capabilities=["codegen"])
        chain = selector._build_fallback_chain(primary, "codegen", "best_quality", None)
        # Only local-model has codegen, and it's the primary, so chain is empty
        assert chain == []
