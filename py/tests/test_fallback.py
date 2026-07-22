"""Tests for model/fallback.py — FallbackManager."""

from __future__ import annotations

import pytest

from maop.model.fallback import FallbackManager
from maop.model.schema import EffectiveModel


@pytest.fixture
def fm():
    """FallbackManager with a dummy registry (not used in tested methods)."""
    return FallbackManager(registry=None)


@pytest.fixture
def effective():
    return EffectiveModel(
        model_name="gpt-4",
        provider="openai",
        fallback_chain=["gpt-3.5", "claude"],
    )


class TestGetChain:
    def test_primary_plus_fallbacks(self, fm, effective):
        chain = fm.get_chain(effective)
        assert chain == ["gpt-4", "gpt-3.5", "claude"]

    def test_empty_fallback_chain(self, fm):
        em = EffectiveModel(model_name="gpt-4", provider="openai", fallback_chain=[])
        assert fm.get_chain(em) == ["gpt-4"]

    def test_filters_over_failed_models(self, fm, effective):
        # Record 5 failures for gpt-3.5 → should be filtered out
        for _ in range(5):
            fm.record_failure("gpt-3.5")
        chain = fm.get_chain(effective)
        assert "gpt-3.5" not in chain
        assert chain == ["gpt-4", "claude"]

    def test_all_models_failed(self, fm, effective):
        for m in ["gpt-4", "gpt-3.5", "claude"]:
            for _ in range(5):
                fm.record_failure(m)
        assert fm.get_chain(effective) == []


class TestRecordSuccessFailure:
    def test_record_failure_increments(self, fm):
        fm.record_failure("model-a")
        fm.record_failure("model-a")
        assert fm.get_failure_stats()["model-a"] == 2

    def test_record_success_clears(self, fm):
        fm.record_failure("model-a")
        fm.record_failure("model-a")
        fm.record_success("model-a")
        assert "model-a" not in fm.get_failure_stats()

    def test_failure_stats_is_copy(self, fm):
        fm.record_failure("model-a")
        stats = fm.get_failure_stats()
        stats["model-a"] = 999
        assert fm.get_failure_stats()["model-a"] == 1


class TestShouldFallback:
    def test_timeout_error(self, fm):
        assert fm.should_fallback("Connection timeout") is True

    def test_timed_out_error(self, fm):
        assert fm.should_fallback("Request timed out") is True

    def test_quota_error(self, fm):
        assert fm.should_fallback("quota exceeded") is True

    def test_rate_limit_error(self, fm):
        assert fm.should_fallback("rate limit hit") is True

    def test_circuit_breaker_error(self, fm):
        assert fm.should_fallback("circuit breaker open") is True

    def test_generic_error(self, fm):
        assert fm.should_fallback("some random error") is True

    def test_policy_disabled(self, fm):
        assert fm.should_fallback("error", policy_fallback_on_error=False) is False

    def test_timeout_policy_disabled(self, fm):
        assert fm.should_fallback("timeout", policy_fallback_on_timeout=False) is False


class TestReset:
    def test_reset_specific_model(self, fm):
        fm.record_failure("model-a")
        fm.record_failure("model-b")
        fm.reset("model-a")
        assert "model-a" not in fm.get_failure_stats()
        assert "model-b" in fm.get_failure_stats()

    def test_reset_all(self, fm):
        fm.record_failure("model-a")
        fm.record_failure("model-b")
        fm.reset()
        assert fm.get_failure_stats() == {}
