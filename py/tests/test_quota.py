"""Tests for model/quota.py — QuotaEnforcer."""

from __future__ import annotations

import time
from unittest.mock import MagicMock

import pytest

from maop.model.quota import QuotaEnforcer
from maop.model.schema import QuotaConfig


def make_registry(quota_dict: dict[str, QuotaConfig] | None = None):
    """Create a mock registry with the given quota config."""
    reg = MagicMock()
    reg.config.quota = quota_dict or {}
    return reg


@pytest.fixture
def enforcer():
    """QuotaEnforcer with 10 req/min, 10000 tokens/min."""
    quota = QuotaConfig(requests_per_minute=10, tokens_per_minute=10000)
    return QuotaEnforcer(make_registry({"test-provider": quota}))


class TestCheck:
    def test_within_quota(self, enforcer):
        assert enforcer.check("test-provider", tokens=500) is True

    def test_unknown_provider_uses_defaults(self, enforcer):
        # Unknown provider → QuotaConfig() defaults (large limits)
        assert enforcer.check("unknown", tokens=100) is True

    def test_request_limit_exceeded(self, enforcer):
        for _ in range(10):
            enforcer.consume("test-provider", tokens=100)
        assert enforcer.check("test-provider", tokens=100) is False

    def test_token_limit_exceeded(self, enforcer):
        enforcer.consume("test-provider", tokens=9500)
        assert enforcer.check("test-provider", tokens=1000) is False

    def test_zero_tokens(self, enforcer):
        assert enforcer.check("test-provider", tokens=0) is True


class TestConsume:
    def test_consume_increments_request_count(self, enforcer):
        enforcer.consume("test-provider", tokens=100)
        usage = enforcer.usage("test-provider")
        assert usage["requests_used"] == 1
        assert usage["tokens_used"] == 100

    def test_multiple_consumes(self, enforcer):
        enforcer.consume("test-provider", tokens=200)
        enforcer.consume("test-provider", tokens=300)
        usage = enforcer.usage("test-provider")
        assert usage["requests_used"] == 2
        assert usage["tokens_used"] == 500


class TestCheckAndConsume:
    def test_success(self, enforcer):
        assert enforcer.check_and_consume("test-provider", tokens=500) is True
        assert enforcer.usage("test-provider")["requests_used"] == 1

    def test_quota_exceeded_returns_false(self, enforcer):
        for _ in range(10):
            enforcer.consume("test-provider", tokens=100)
        assert enforcer.check_and_consume("test-provider", tokens=100) is False


class TestUsage:
    def test_usage_structure(self, enforcer):
        u = enforcer.usage("test-provider")
        assert u["provider"] == "test-provider"
        assert u["requests_used"] == 0
        assert u["requests_limit"] == 10
        assert u["tokens_used"] == 0
        assert u["tokens_limit"] == 10000
        assert u["window_s"] == 60

    def test_usage_all(self, enforcer):
        # Add a second provider
        quota2 = QuotaConfig(requests_per_minute=5, tokens_per_minute=5000)
        enforcer._registry.config.quota["provider-2"] = quota2
        all_usage = enforcer.usage_all()
        assert len(all_usage) == 2
        providers = {u["provider"] for u in all_usage}
        assert providers == {"test-provider", "provider-2"}


class TestSlidingWindow:
    def test_old_entries_pruned(self, enforcer):
        # Manually insert old entries
        old_time = time.time() - 120  # 2 minutes ago
        enforcer._request_log["test-provider"].append(old_time)
        enforcer._token_log["test-provider"].append((old_time, 5000))
        # After check, old entries should be pruned
        enforcer.check("test-provider", tokens=100)
        assert len(enforcer._request_log["test-provider"]) == 0
