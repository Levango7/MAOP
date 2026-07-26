"""Tests for MAOP.core.rate_limiter — TokenBucket, SlidingWindow, RateLimiter."""

from __future__ import annotations

import time

from maop.core.rate_limiter import (
    RateLimiter,
    RateLimiterConfig,
    RateLimitResult,
    SlidingWindow,
    TokenBucket,
)


class TestRateLimitResult:
    def test_defaults(self):
        r = RateLimitResult()
        assert r.allowed is True
        assert r.remaining == 0
        assert r.retry_after_s == 0.0


class TestRateLimiterConfig:
    def test_defaults(self):
        c = RateLimiterConfig()
        assert c.algorithm == "token_bucket"
        assert c.rate == 10.0
        assert c.burst == 20
        assert c.window_s == 60.0
        assert c.max_requests == 600


# ── TokenBucket ───────────────────────────────────────────────────


class TestTokenBucket:
    def test_init_full_tokens(self):
        tb = TokenBucket(rate=10, burst=20)
        assert tb._tokens == 20.0

    def test_check_allowed_with_full_bucket(self):
        tb = TokenBucket(rate=10, burst=5)
        result = tb.check()
        assert result.allowed is True
        assert result.limit == 5

    def test_consume_reduces_tokens(self):
        tb = TokenBucket(rate=10, burst=5)
        r1 = tb.consume()
        assert r1.allowed is True
        assert r1.remaining == 4
        r2 = tb.consume()
        assert r2.allowed is True
        assert r2.remaining == 3

    def test_consume_until_exhausted(self):
        tb = TokenBucket(rate=0.01, burst=3)
        results = [tb.consume() for _ in range(5)]
        allowed = [r.allowed for r in results]
        assert allowed == [True, True, True, False, False]

    def test_check_does_not_consume(self):
        tb = TokenBucket(rate=0.01, burst=3)
        tb.check()
        tb.check()
        # Tokens not consumed by check
        r = tb.consume()
        assert r.allowed is True

    def test_refill_over_time(self):
        tb = TokenBucket(rate=100, burst=2)
        tb.consume()
        tb.consume()
        assert tb.consume().allowed is False
        time.sleep(0.05)  # 100 * 0.05 = 5 tokens refilled
        assert tb.consume().allowed is True

    def test_retry_after_when_denied(self):
        tb = TokenBucket(rate=10, burst=1)
        tb.consume()
        result = tb.consume()
        assert result.allowed is False
        assert result.retry_after_s > 0

    def test_remaining_zero_when_denied(self):
        tb = TokenBucket(rate=1, burst=1)
        tb.consume()
        result = tb.consume()
        assert result.allowed is False
        assert result.remaining == 0

    def test_consume_multiple_tokens(self):
        tb = TokenBucket(rate=1, burst=5)
        result = tb.consume(tokens=3)
        assert result.allowed is True
        assert result.remaining == 2

    def test_consume_more_than_burst(self):
        tb = TokenBucket(rate=1, burst=2)
        result = tb.consume(tokens=5)
        assert result.allowed is False


# ── SlidingWindow ─────────────────────────────────────────────────


class TestSlidingWindow:
    def test_check_empty_allowed(self):
        sw = SlidingWindow(max_requests=10, window_s=60)
        result = sw.check()
        assert result.allowed is True
        assert result.limit == 10

    def test_consume_records(self):
        sw = SlidingWindow(max_requests=3, window_s=60)
        for _ in range(3):
            assert sw.consume().allowed is True
        result = sw.consume()
        assert result.allowed is False

    def test_check_does_not_record(self):
        sw = SlidingWindow(max_requests=2, window_s=60)
        sw.check()
        sw.check()
        assert sw.consume().allowed is True
        assert sw.consume().allowed is True
        assert sw.consume().allowed is False

    def test_window_expiry(self):
        sw = SlidingWindow(max_requests=1, window_s=0.05)
        assert sw.consume().allowed is True
        assert sw.consume().allowed is False
        time.sleep(0.06)
        assert sw.consume().allowed is True

    def test_remaining_decreases(self):
        sw = SlidingWindow(max_requests=5, window_s=60)
        r1 = sw.consume()
        r2 = sw.consume()
        assert r1.remaining == 4
        assert r2.remaining == 3

    def test_retry_after_when_denied(self):
        sw = SlidingWindow(max_requests=1, window_s=1.0)
        sw.consume()
        result = sw.consume()
        assert result.allowed is False
        assert result.retry_after_s > 0

    def test_cleanup_removes_old(self):
        sw = SlidingWindow(max_requests=10, window_s=0.01)
        sw.consume()
        time.sleep(0.02)
        sw._cleanup()
        assert len(sw._timestamps) == 0


# ── RateLimiter (multi-key) ───────────────────────────────────────


class TestRateLimiter:
    def test_default_token_bucket(self):
        rl = RateLimiter()
        result = rl.consume("user1")
        assert result.allowed is True

    def test_sliding_window_algorithm(self):
        cfg = RateLimiterConfig(algorithm="sliding_window", max_requests=2, window_s=60)
        rl = RateLimiter(config=cfg)
        assert rl.consume("k").allowed is True
        assert rl.consume("k").allowed is True
        assert rl.consume("k").allowed is False

    def test_separate_keys_independent(self):
        rl = RateLimiter(config=RateLimiterConfig(rate=0.01, burst=1))
        assert rl.consume("a").allowed is True
        assert rl.consume("b").allowed is True
        assert rl.consume("a").allowed is False

    def test_check_does_not_consume(self):
        rl = RateLimiter()
        rl.check("k")
        rl.check("k")
        assert rl.consume("k").allowed is True

    def test_reset_single_key(self):
        rl = RateLimiter(config=RateLimiterConfig(rate=0.01, burst=1))
        rl.consume("a")
        rl.reset("a")
        assert "a" not in rl.active_keys()

    def test_reset_all(self):
        rl = RateLimiter()
        rl.consume("a")
        rl.consume("b")
        rl.reset()
        assert rl.active_keys() == []

    def test_active_keys(self):
        rl = RateLimiter()
        rl.consume("x")
        rl.consume("y")
        keys = rl.active_keys()
        assert "x" in keys
        assert "y" in keys

    def test_default_key(self):
        rl = RateLimiter()
        result = rl.consume()
        assert result.allowed is True
        assert "default" in rl.active_keys()
