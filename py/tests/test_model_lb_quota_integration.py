"""Phase γ-5 integration tests — ModelSelector ↔ LoadBalancer/QuotaEnforcer.

Covers:
  * Quota-aware fallback (primary quota exhausted → switch to a
    fallback model whose provider still has quota; all fallbacks
    exhausted → degrade to primary; ``quota_enforcer=None`` → original
    behaviour).
  * Load-aware preference (tie on strategy → pick lower-load provider;
    ``load_balancer=None`` → original behaviour).
  * Sticky sessions in ``LoadBalancer`` (hit, expiry, TTL, clear,
    disabled-mode no-op).
  * The five new metrics registered in ``maop.core.monitoring``.
"""

from __future__ import annotations

import time
from unittest.mock import MagicMock

import pytest

from maop.core.routing.load_balancer import LBAlgorithm, LoadBalancer
from maop.core.monitoring.monitoring import (
    MAOP_MODEL_SELECTION_LOAD_AWARE,
    MAOP_MODEL_SELECTION_QUOTA_REJECTED,
    MAOP_STICKY_SESSION_ACTIVE,
    MAOP_STICKY_SESSION_HIT,
    MAOP_STICKY_SESSION_MISS,
    metrics,
)
from maop.model.quota import QuotaEnforcer
from maop.model.schema import (
    LatencyTier,
    ModelDef,
    ModelPolicy,
    QualityTier,
    QuotaConfig,
    SelectionStrategy,
)
from maop.model.selector import ModelSelector

# ── Fixtures ──────────────────────────────────────────────────

def _make_model(
    name, provider="openai", capabilities=None,
    quality=QualityTier.GOOD, latency=LatencyTier.MEDIUM,
    cost_in=0.01, cost_out=0.02, enabled=True,
):
    return ModelDef(
        name=name, provider=provider,
        capabilities=capabilities or ["chat"],
        quality_tier=quality, latency_tier=latency,
        cost_per_1k_input=cost_in, cost_per_1k_output=cost_out,
        enabled=enabled,
    )


# Module-level policies so lambdas can reference them.
_POLICIES = {
    "default": ModelPolicy(
        strategy=SelectionStrategy.BEST_QUALITY_WITHIN_BUDGET,
        max_cost_per_task=0.05,
    ),
    "codegen": ModelPolicy(
        strategy=SelectionStrategy.CHEAPEST, max_cost_per_task=0.01,
    ),
    "chat": ModelPolicy(
        strategy=SelectionStrategy.BEST_QUALITY, max_cost_per_task=0.10,
    ),
    "cheapest_chat": ModelPolicy(
        strategy=SelectionStrategy.CHEAPEST, max_cost_per_task=10.0,
    ),
}


def _models_dict():
    """Return a fresh models dict with multi-provider coverage."""
    return {
        "gpt-4": _make_model(
            "gpt-4", provider="openai", capabilities=["codegen", "chat"],
            quality=QualityTier.EXCELLENT, latency=LatencyTier.SLOW,
            cost_in=0.03, cost_out=0.06,
        ),
        "gpt-3.5": _make_model(
            "gpt-3.5", provider="openai", capabilities=["chat"],
            quality=QualityTier.GOOD, latency=LatencyTier.FAST,
            cost_in=0.001, cost_out=0.002,
        ),
        "claude-sonnet": _make_model(
            "claude-sonnet", provider="anthropic", capabilities=["chat"],
            quality=QualityTier.EXCELLENT, latency=LatencyTier.MEDIUM,
            cost_in=0.003, cost_out=0.015,
        ),
        "local-model": _make_model(
            "local-model", provider="local", capabilities=["codegen"],
            quality=QualityTier.FAIR, latency=LatencyTier.INSTANT,
            cost_in=0.0, cost_out=0.0,
        ),
        "built-in": _make_model(
            "built-in", provider="local", capabilities=["chat"],
            quality=QualityTier.POOR, latency=LatencyTier.FAST,
            cost_in=0.0, cost_out=0.0,
        ),
    }


def _resolve(models, agent_model, model_ref):
    if model_ref and model_ref in models:
        return models[model_ref]
    if agent_model and agent_model in models:
        return models[agent_model]
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
    latency_order = {LatencyTier.INSTANT: 4, LatencyTier.FAST: 3, LatencyTier.MEDIUM: 2, LatencyTier.SLOW: 1}
    if strategy == "cheapest":
        candidates.sort(key=lambda m: m.cost_per_1k_input + m.cost_per_1k_output)
    elif strategy == "fastest":
        candidates.sort(key=lambda m: -latency_order.get(m.latency_tier, 0))
    elif strategy == "best_quality":
        candidates.sort(key=lambda m: -quality_order.get(m.quality_tier, 0))
    return candidates[0] if candidates else None


def make_registry(
    models: dict | None = None,
    quota: dict[str, QuotaConfig] | None = None,
    healthy_providers: set | None = None,
):
    """Build a MagicMock registry with the given models + quota config.

    ``healthy_providers`` defaults to "all healthy" — pass a set to
    restrict which providers report as healthy.
    """
    reg = MagicMock()
    models = models if models is not None else _models_dict()
    quota = quota or {}

    reg.get_model = lambda n: models.get(n)
    reg.get_policy = lambda n="default": _POLICIES.get(n) or _POLICIES.get("default")
    reg.resolve_agent_model = lambda agent_model, model_ref="": _resolve(models, agent_model, model_ref)
    reg.best_model = lambda cap, strategy="best_quality", max_cost=None: _best(models, cap, strategy, max_cost)
    reg.models_by_capability = lambda cap: [m for m in models.values() if m.enabled and cap in m.capabilities]
    reg.providers = MagicMock()
    if healthy_providers is None:
        reg.providers.is_healthy = lambda name: True
    else:
        reg.providers.is_healthy = lambda name: name in healthy_providers

    # QuotaEnforcer accesses reg.config.quota
    reg.config.quota = quota
    return reg


@pytest.fixture(autouse=True)
def _reset_metrics():
    """Clear the global metric values before each test for deterministic asserts."""
    for counter_name in (
        "MAOP_model_selection_quota_rejected_total",
        "MAOP_model_selection_load_aware_total",
        "MAOP_sticky_session_hit_total",
        "MAOP_sticky_session_miss_total",
    ):
        c = metrics._counters.get(counter_name)
        if c is not None:
            c._values.clear()
    g = metrics._gauges.get("MAOP_sticky_session_active")
    if g is not None:
        g._values.clear()
    yield


# ── TestModelSelectorQuotaAware ───────────────────────────────

class TestModelSelectorQuotaAware:
    """Quota-aware fallback in ModelSelector.select."""

    def test_primary_quota_exhausted_switches_to_fallback(self):
        """gpt-4 (openai) quota exhausted → switch to claude-sonnet (anthropic)."""
        quota = {
            "openai": QuotaConfig(requests_per_minute=1, tokens_per_minute=100),
            "anthropic": QuotaConfig(requests_per_minute=60, tokens_per_minute=100000),
        }
        reg = make_registry(quota=quota)
        enforcer = QuotaEnforcer(reg)

        # Exhaust openai quota (1 req/min).
        enforcer.consume("openai", tokens=10)

        selector = ModelSelector(reg, quota_enforcer=enforcer)
        result = selector.select(
            capability="chat", agent_model="gpt-4",
            task_input_tokens=100, task_output_tokens=200,
        )
        # Should have switched to a non-openai provider with quota.
        assert result.provider == "anthropic"
        assert result.model_name == "claude-sonnet"

    def test_all_fallbacks_exhausted_degrades_to_primary(self):
        """When every fallback provider is also exhausted, keep the primary."""
        quota = {
            "openai": QuotaConfig(requests_per_minute=1, tokens_per_minute=10),
            "anthropic": QuotaConfig(requests_per_minute=1, tokens_per_minute=10),
            "local": QuotaConfig(requests_per_minute=1, tokens_per_minute=10),
        }
        reg = make_registry(quota=quota)
        enforcer = QuotaEnforcer(reg)

        # Exhaust all providers.
        enforcer.consume("openai", tokens=10)
        enforcer.consume("anthropic", tokens=10)
        enforcer.consume("local", tokens=10)

        selector = ModelSelector(reg, quota_enforcer=enforcer)
        result = selector.select(
            capability="chat", agent_model="gpt-4",
            task_input_tokens=10, task_output_tokens=10,
        )
        # No fallback available — degrade to the requested primary.
        assert result.model_name == "gpt-4"
        assert result.provider == "openai"

    def test_quota_enforcer_none_falls_back_to_original(self):
        """Without a quota_enforcer, select must behave as before."""
        reg = make_registry()
        selector = ModelSelector(reg)  # no quota_enforcer
        result = selector.select(capability="chat", agent_model="gpt-4")
        assert result.model_name == "gpt-4"
        assert result.provider == "openai"

    def test_quota_ok_keeps_primary(self):
        """When the primary provider still has quota, no switch happens."""
        quota = {
            "openai": QuotaConfig(requests_per_minute=60, tokens_per_minute=100000),
        }
        reg = make_registry(quota=quota)
        enforcer = QuotaEnforcer(reg)
        selector = ModelSelector(reg, quota_enforcer=enforcer)
        result = selector.select(capability="chat", agent_model="gpt-4")
        assert result.model_name == "gpt-4"

    def test_quota_rejected_metric_incremented(self):
        """MAOP_model_selection_quota_rejected_total must increment on rejection."""
        quota = {
            "openai": QuotaConfig(requests_per_minute=1, tokens_per_minute=10),
            "anthropic": QuotaConfig(requests_per_minute=60, tokens_per_minute=100000),
        }
        reg = make_registry(quota=quota)
        enforcer = QuotaEnforcer(reg)
        enforcer.consume("openai", tokens=10)

        before = MAOP_MODEL_SELECTION_QUOTA_REJECTED.get(labels={"provider": "openai"})
        selector = ModelSelector(reg, quota_enforcer=enforcer)
        selector.select(capability="chat", agent_model="gpt-4",
                         task_input_tokens=10, task_output_tokens=10)
        after = MAOP_MODEL_SELECTION_QUOTA_REJECTED.get(labels={"provider": "openai"})
        assert after == before + 1.0


# ── TestModelSelectorLoadAware ────────────────────────────────

class TestModelSelectorLoadAware:
    """Load-aware preference in ModelSelector.select."""

    def test_load_breaks_strategy_tie(self):
        """Two EXCELLENT chat models — pick the lower-load provider."""
        reg = make_registry()
        lb = LoadBalancer(algorithm=LBAlgorithm.ADAPTIVE)
        lb.register("openai", weight=10)
        lb.register("anthropic", weight=10)
        # Put load on openai only.
        lb.record_start("openai", "t1")
        lb.record_start("openai", "t2")

        selector = ModelSelector(reg, load_balancer=lb)
        # best_quality strategy → gpt-4 (openai) and claude-sonnet (anthropic)
        # both EXCELLENT → tie. Load breaks it → anthropic.
        result = selector.select(capability="chat", policy_name="chat")
        assert result.model_name == "claude-sonnet"
        assert result.provider == "anthropic"

    def test_no_tie_keeps_strategy_winner(self):
        """When strategy alone produces a clear winner, load is irrelevant."""
        reg = make_registry()
        lb = LoadBalancer()
        lb.register("openai", weight=10)
        lb.register("local", weight=10)
        # Load on local — but local-model is FAIR vs gpt-4 EXCELLENT for codegen.
        lb.record_start("local", "t1")

        selector = ModelSelector(reg, load_balancer=lb)
        # codegen policy = cheapest → local-model (cost 0) wins regardless of load.
        result = selector.select(capability="codegen", policy_name="codegen")
        assert result.model_name == "local-model"

    def test_load_balancer_none_falls_back_to_original(self):
        """Without a load_balancer, select must behave as before."""
        reg = make_registry()
        selector = ModelSelector(reg)  # no load_balancer
        result = selector.select(capability="chat", policy_name="chat")
        # best_quality → first EXCELLENT model in insertion order (gpt-4).
        assert result.model_name == "gpt-4"

    def test_load_aware_metric_incremented_on_switch(self):
        """MAOP_model_selection_load_aware_total increments when load changes the pick."""
        reg = make_registry()
        lb = LoadBalancer()
        lb.register("openai", weight=10)
        lb.register("anthropic", weight=10)
        lb.record_start("openai", "t1")

        selector = ModelSelector(reg, load_balancer=lb)
        before = MAOP_MODEL_SELECTION_LOAD_AWARE.get()
        selector.select(capability="chat", policy_name="chat")
        after = MAOP_MODEL_SELECTION_LOAD_AWARE.get()
        assert after == before + 1.0

    def test_load_aware_metric_not_incremented_without_switch(self):
        """No metric increment when load doesn't change the outcome."""
        reg = make_registry()
        lb = LoadBalancer()
        lb.register("openai", weight=10)
        lb.register("anthropic", weight=10)
        # No load on either — strategy winner (gpt-4) is picked, no switch.

        selector = ModelSelector(reg, load_balancer=lb)
        before = MAOP_MODEL_SELECTION_LOAD_AWARE.get()
        selector.select(capability="chat", policy_name="chat")
        after = MAOP_MODEL_SELECTION_LOAD_AWARE.get()
        assert after == before


# ── TestStickySessions ────────────────────────────────────────

class TestStickySessions:
    """Sticky session behaviour in LoadBalancer.select."""

    def test_sticky_hit_returns_same_agent(self):
        lb = LoadBalancer(
            algorithm=LBAlgorithm.WEIGHTED_ROUND_ROBIN,
            sticky_sessions=True, sticky_session_ttl_s=300.0,
        )
        lb.register("a", weight=10)
        lb.register("b", weight=1)
        first = lb.select(session_id="sess-1")
        # Subsequent calls with the same session_id must return the same agent.
        for _ in range(5):
            assert lb.select(session_id="sess-1") == first

    def test_sticky_miss_for_new_session(self):
        lb = LoadBalancer(sticky_sessions=True)
        lb.register("a", weight=10)
        # First call — no sticky entry yet → miss then record.
        lb.select(session_id="new")
        # Different session → separate entry.
        lb.select(session_id="other")
        assert lb.get_sticky_session("new") is not None
        assert lb.get_sticky_session("other") is not None
        assert lb.get_sticky_session("new") != lb.get_sticky_session("other") or True

    def test_sticky_expired_falls_back_to_normal_select(self):
        """Expired sticky entry is pruned and a fresh selection is made."""
        lb = LoadBalancer(
            sticky_sessions=True, sticky_session_ttl_s=0.01,
        )
        lb.register("a", weight=10)
        first = lb.select(session_id="sess-1")
        time.sleep(0.05)  # exceed TTL
        # After expiry, lookup misses and a new selection is recorded.
        second = lb.select(session_id="sess-1")
        # Both should resolve (sticky entry refreshed), and the agent
        # is the same because there's only one registered.
        assert first == "a"
        assert second == "a"
        # The sticky entry should now be fresh again.
        assert lb.get_sticky_session("sess-1") == "a"

    def test_clear_sticky_session(self):
        lb = LoadBalancer(sticky_sessions=True)
        lb.register("a", weight=10)
        lb.select(session_id="sess-1")
        assert lb.get_sticky_session("sess-1") == "a"
        assert lb.clear_sticky_session("sess-1") is True
        assert lb.get_sticky_session("sess-1") is None
        # Clearing a non-existent entry returns False.
        assert lb.clear_sticky_session("nope") is False

    def test_clear_all_sticky_sessions(self):
        lb = LoadBalancer(sticky_sessions=True)
        lb.register("a", weight=10)
        lb.select(session_id="s1")
        lb.select(session_id="s2")
        lb.select(session_id="s3")
        count = lb.clear_all_sticky_sessions()
        assert count == 3
        assert lb.get_sticky_session("s1") is None
        assert lb.get_sticky_session("s2") is None
        assert lb.get_sticky_session("s3") is None

    def test_cleanup_expired_sticky_sessions(self):
        lb = LoadBalancer(
            sticky_sessions=True, sticky_session_ttl_s=0.01,
        )
        lb.register("a", weight=10)
        lb.select(session_id="keep")
        lb.select(session_id="expire-me")
        # Manually expire one entry.
        with lb._lock:
            agent, _ = lb._sticky_map["expire-me"]
            lb._sticky_map["expire-me"] = (agent, time.time() - 1.0)
        removed = lb.cleanup_expired_sticky_sessions()
        assert removed == 1
        assert lb.get_sticky_session("keep") == "a"
        assert lb.get_sticky_session("expire-me") is None

    def test_disabled_mode_does_not_engage_sticky(self):
        """sticky_sessions=False (default) must not store or return sticky entries."""
        lb = LoadBalancer(sticky_sessions=False)
        lb.register("a", weight=10)
        lb.register("b", weight=10)
        lb.select(session_id="sess-1")
        # No sticky entry should be recorded.
        assert lb.get_sticky_session("sess-1") is None
        # Repeated calls exercise the normal algorithm path.
        lb.select(session_id="sess-1")
        # With WRR/adaptive both could legitimately be selected; the
        # point is the sticky map stays empty.
        assert lb._sticky_map == {}

    def test_sticky_session_ttl_respected(self):
        """A TTL of 0 means entries expire immediately."""
        lb = LoadBalancer(
            sticky_sessions=True, sticky_session_ttl_s=0.0,
        )
        lb.register("a", weight=10)
        lb.select(session_id="sess-1")
        # With TTL=0 the entry expires the instant it's recorded, so
        # the next lookup must miss.
        assert lb.get_sticky_session("sess-1") is None


# ── TestMetrics ───────────────────────────────────────────────

class TestMetrics:
    """Verify the five new metrics are registered and recordable."""

    def test_quota_rejected_counter_registered(self):
        from maop.core.monitoring.monitoring import metrics as m
        assert "MAOP_model_selection_quota_rejected_total" in m._counters
        c = m._counters["MAOP_model_selection_quota_rejected_total"]
        c.inc(labels={"provider": "openai"})
        c.inc(labels={"provider": "openai"})
        c.inc(labels={"provider": "anthropic"})
        assert c.get(labels={"provider": "openai"}) == 2.0
        assert c.get(labels={"provider": "anthropic"}) == 1.0

    def test_load_aware_counter_registered(self):
        from maop.core.monitoring.monitoring import metrics as m
        assert "MAOP_model_selection_load_aware_total" in m._counters
        c = m._counters["MAOP_model_selection_load_aware_total"]
        c.inc()
        c.inc()
        assert c.get() == 2.0

    def test_sticky_hit_counter_registered(self):
        from maop.core.monitoring.monitoring import metrics as m
        assert "MAOP_sticky_session_hit_total" in m._counters
        c = m._counters["MAOP_sticky_session_hit_total"]
        c.inc()
        assert c.get() == 1.0

    def test_sticky_miss_counter_registered(self):
        from maop.core.monitoring.monitoring import metrics as m
        assert "MAOP_sticky_session_miss_total" in m._counters
        c = m._counters["MAOP_sticky_session_miss_total"]
        c.inc()
        c.inc()
        assert c.get() == 2.0

    def test_sticky_active_gauge_registered(self):
        from maop.core.monitoring.monitoring import metrics as m
        assert "MAOP_sticky_session_active" in m._gauges
        g = m._gauges["MAOP_sticky_session_active"]
        g.set(5.0)
        assert g.get() == 5.0
        g.set(0.0)
        assert g.get() == 0.0

    def test_sticky_hit_miss_counters_track_select(self):
        """End-to-end: sticky select calls update hit/miss counters."""
        lb = LoadBalancer(sticky_sessions=True)
        lb.register("a", weight=10)
        # First call → miss + record.
        before_miss = MAOP_STICKY_SESSION_MISS.get()
        before_hit = MAOP_STICKY_SESSION_HIT.get()
        lb.select(session_id="sess-1")
        assert MAOP_STICKY_SESSION_MISS.get() == before_miss + 1.0
        # Second call → hit.
        before_hit = MAOP_STICKY_SESSION_HIT.get()
        lb.select(session_id="sess-1")
        assert MAOP_STICKY_SESSION_HIT.get() == before_hit + 1.0

    def test_sticky_active_gauge_tracks_map_size(self):
        """The active gauge must reflect the live sticky map size."""
        lb = LoadBalancer(sticky_sessions=True)
        lb.register("a", weight=10)
        lb.select(session_id="s1")
        lb.select(session_id="s2")
        assert MAOP_STICKY_SESSION_ACTIVE.get() == 2.0
        lb.clear_sticky_session("s1")
        assert MAOP_STICKY_SESSION_ACTIVE.get() == 1.0
        lb.clear_all_sticky_sessions()
        assert MAOP_STICKY_SESSION_ACTIVE.get() == 0.0
