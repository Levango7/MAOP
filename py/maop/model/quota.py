"""QuotaEnforcer — Per-provider rate limiting and quota enforcement."""

from __future__ import annotations

import logging
import time
from collections import defaultdict, deque

from maop.model.registry import ModelRegistry
from maop.model.schema import QuotaConfig

logger = logging.getLogger(__name__)


class QuotaEnforcer:
    """Enforces per-provider request and token quotas.

    Uses sliding window (60s) for rate limiting.

    Usage::

        enforcer = QuotaEnforcer(registry)
        if enforcer.check("stepfun", tokens=500):
            # proceed with request
            enforcer.consume("stepfun", tokens=500)
        else:
            # quota exceeded, try fallback
    """

    def __init__(self, registry: ModelRegistry) -> None:
        self._registry = registry
        self._request_log: dict[str, deque] = defaultdict(deque)  # provider -> timestamps
        self._token_log: dict[str, deque] = defaultdict(deque)  # provider -> (timestamp, tokens)
        self._window_s = 60  # sliding window in seconds

    def _prune(self, provider: str) -> None:
        """Remove entries outside the sliding window."""
        cutoff = time.time() - self._window_s
        rq = self._request_log[provider]
        while rq and rq[0] < cutoff:
            rq.popleft()
        tq = self._token_log[provider]
        while tq and tq[0][0] < cutoff:
            tq.popleft()

    def _get_quota(self, provider: str) -> QuotaConfig:
        return self._registry.config.quota.get(provider, QuotaConfig())

    def check(self, provider: str, tokens: int = 0) -> bool:
        """Check if a request can be sent under quota. Does not consume."""
        self._prune(provider)
        quota = self._get_quota(provider)

        # Check request rate
        if len(self._request_log[provider]) >= quota.requests_per_minute:
            return False

        # Check token rate
        current_tokens = sum(t for _, t in self._token_log[provider])
        if current_tokens + tokens > quota.tokens_per_minute:
            return False

        return True

    def consume(self, provider: str, tokens: int = 0) -> None:
        """Record a request consumption."""
        now = time.time()
        self._request_log[provider].append(now)
        self._token_log[provider].append((now, tokens))

    def check_and_consume(self, provider: str, tokens: int = 0) -> bool:
        """Atomically check and consume."""
        if self.check(provider, tokens):
            self.consume(provider, tokens)
            return True
        return False

    def usage(self, provider: str) -> dict:
        """Get current usage stats for a provider."""
        self._prune(provider)
        quota = self._get_quota(provider)
        req_count = len(self._request_log[provider])
        token_count = sum(t for _, t in self._token_log[provider])
        return {
            "provider": provider,
            "requests_used": req_count,
            "requests_limit": quota.requests_per_minute,
            "tokens_used": token_count,
            "tokens_limit": quota.tokens_per_minute,
            "window_s": self._window_s,
        }

    def usage_all(self) -> list[dict]:
        """Get usage stats for all providers with quota config."""
        return [self.usage(p) for p in self._registry.config.quota]
