"""MAOP Rate Limiter - Token bucket and sliding window rate limiting.

Two algorithms:
  1. TokenBucket: Steady-state rate limiting with burst allowance
  2. SlidingWindow: Precise request count limit in a time window

Both are in-memory with optional SQLite persistence for distributed scenarios.
"""

from __future__ import annotations

import logging
import threading
import time

from pydantic import BaseModel

logger = logging.getLogger(__name__)

# ── Models ──────────────────────────────────────────────────────


class RateLimitResult(BaseModel):
    """Result of a rate limit check."""
    allowed: bool = True
    remaining: int = 0
    retry_after_s: float = 0.0
    limit: int = 0
    window_s: float = 0.0


class RateLimiterConfig(BaseModel):
    """Configuration for rate limiting."""
    algorithm: str = "token_bucket"   # token_bucket | sliding_window
    rate: float = 10.0               # Requests per second (token bucket) or per window (sliding)
    burst: int = 20                  # Max burst size (token bucket)
    window_s: float = 60.0           # Window size in seconds (sliding window)
    max_requests: int = 600          # Max requests per window (sliding window)


# ── Token Bucket ────────────────────────────────────────────────

class TokenBucket:
    """Token bucket rate limiter.

    Tokens are added at a steady rate (rate per second).
    Each request consumes one token.
    Burst allows temporary spikes above the steady rate.
    """

    def __init__(self, rate: float = 10.0, burst: int = 20):
        self.rate = rate
        self.burst = burst
        self._tokens: float = float(burst)
        self._last_refill: float = time.monotonic()
        self._lock = threading.Lock()

    def _refill(self) -> None:
        """Refill tokens based on elapsed time."""
        now = time.monotonic()
        elapsed = now - self._last_refill
        self._tokens = min(self.burst, self._tokens + elapsed * self.rate)
        self._last_refill = now

    def check(self, tokens: int = 1) -> RateLimitResult:
        """Check if a request is allowed. Does NOT consume tokens."""
        with self._lock:
            self._refill()
            allowed = self._tokens >= tokens
            remaining = max(0, int(self._tokens) - (tokens if allowed else 0))
            retry_after = 0.0 if allowed else (tokens - self._tokens) / self.rate

            return RateLimitResult(
                allowed=allowed,
                remaining=remaining,
                retry_after_s=round(retry_after, 3),
                limit=self.burst,
                window_s=1.0 / self.rate if self.rate > 0 else 0,
            )

    def consume(self, tokens: int = 1) -> RateLimitResult:
        """Consume tokens if available. Returns result indicating success."""
        with self._lock:
            self._refill()
            allowed = self._tokens >= tokens
            if allowed:
                self._tokens -= tokens
                remaining = int(self._tokens)
                retry_after = 0.0
            else:
                remaining = 0
                retry_after = (tokens - self._tokens) / self.rate

            return RateLimitResult(
                allowed=allowed,
                remaining=remaining,
                retry_after_s=round(retry_after, 3),
                limit=self.burst,
                window_s=1.0 / self.rate if self.rate > 0 else 0,
            )


# ── Sliding Window ──────────────────────────────────────────────

class SlidingWindow:
    """Sliding window rate limiter.

    Tracks request timestamps and counts requests within the window.
    More precise than token bucket but uses more memory.
    """

    def __init__(self, max_requests: int = 600, window_s: float = 60.0):
        self.max_requests = max_requests
        self.window_s = window_s
        self._timestamps: list[float] = []
        self._lock = threading.Lock()

    def _cleanup(self) -> None:
        """Remove timestamps outside the current window."""
        cutoff = time.monotonic() - self.window_s
        self._timestamps = [t for t in self._timestamps if t > cutoff]

    def check(self) -> RateLimitResult:
        """Check if a request is allowed. Does NOT record the request."""
        with self._lock:
            self._cleanup()
            count = len(self._timestamps)
            allowed = count < self.max_requests
            remaining = max(0, self.max_requests - count - 1)
            retry_after = 0.0
            if not allowed and self._timestamps:
                oldest = self._timestamps[0]
                retry_after = max(0, oldest + self.window_s - time.monotonic())

            return RateLimitResult(
                allowed=allowed,
                remaining=remaining,
                retry_after_s=round(retry_after, 3),
                limit=self.max_requests,
                window_s=self.window_s,
            )

    def consume(self) -> RateLimitResult:
        """Record a request if allowed."""
        with self._lock:
            self._cleanup()
            count = len(self._timestamps)
            allowed = count < self.max_requests

            if allowed:
                self._timestamps.append(time.monotonic())
                remaining = self.max_requests - count - 1
                retry_after = 0.0
            else:
                remaining = 0
                retry_after = 0.0
                if self._timestamps:
                    oldest = self._timestamps[0]
                    retry_after = max(0, oldest + self.window_s - time.monotonic())

            return RateLimitResult(
                allowed=allowed,
                remaining=remaining,
                retry_after_s=round(retry_after, 3),
                limit=self.max_requests,
                window_s=self.window_s,
            )


# ── Multi-key Rate Limiter ─────────────────────────────────────

class RateLimiter:
    """Rate limiter supporting multiple keys (e.g., per-user, per-IP).

    Creates separate limiters for each key on demand.
    """

    def __init__(self, config: RateLimiterConfig | None = None):
        self.config = config or RateLimiterConfig()
        self._limiters: dict[str, TokenBucket | SlidingWindow] = {}
        self._lock = threading.Lock()

    def _get_limiter(self, key: str) -> TokenBucket | SlidingWindow:
        """Get or create a limiter for a key."""
        if key not in self._limiters:
            if self.config.algorithm == "sliding_window":
                self._limiters[key] = SlidingWindow(
                    max_requests=self.config.max_requests,
                    window_s=self.config.window_s,
                )
            else:
                self._limiters[key] = TokenBucket(
                    rate=self.config.rate,
                    burst=self.config.burst,
                )
        return self._limiters[key]

    def check(self, key: str = "default") -> RateLimitResult:
        """Check if a request is allowed for a key."""
        with self._lock:
            limiter = self._get_limiter(key)
            return limiter.check()

    def consume(self, key: str = "default") -> RateLimitResult:
        """Consume a rate limit token for a key."""
        with self._lock:
            limiter = self._get_limiter(key)
            return limiter.consume()

    def reset(self, key: str | None = None) -> None:
        """Reset rate limit for a key, or all keys if key is None."""
        with self._lock:
            if key is None:
                self._limiters.clear()
            else:
                self._limiters.pop(key, None)

    def active_keys(self) -> list[str]:
        """List keys with active rate limiters."""
        with self._lock:
            return list(self._limiters.keys())
