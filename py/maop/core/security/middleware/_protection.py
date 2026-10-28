"""速率限制 / IP 白黑名单 / 请求体限长 / 审计日志 —— 四道防护中间件.

原 ``middleware.py`` 的保护块（2026-10-28 拆分）。四者相互独立，
按 setup 层给出的顺序挂载。
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from typing import Any, cast

from fastapi import Request, Response
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

logger = logging.getLogger(__name__)


class RateLimitMiddleware(BaseHTTPMiddleware):
    """FastAPI middleware for per-IP rate limiting.

    Uses Redis-backed sliding-window counter when ``MAOP_REDIS_URL`` is set
    (or ``redis_url`` param passed), falling back to an in-memory token bucket
    when Redis is unavailable or not configured.

    .. note::
        **Multi-process behavior**:
        - Redis backend: shared across all workers, consistent rate limiting.
        - In-memory fallback: per-process only; actual threshold is
          ``rate × worker_count`` under uvicorn/gunicorn multi-worker.

    .. note::
        **Skip paths**: health checks, Prometheus metrics, and static assets
        are excluded from rate limiting by default so monitoring probes are
        never throttled.
    """

    _REDIS_LUA_SLIDING_WINDOW = """
        local key = KEYS[1]
        local now = tonumber(ARGV[1])
        local window = tonumber(ARGV[2])
        local limit = tonumber(ARGV[3])
        -- Remove entries outside the window
        redis.call('ZREMRANGEBYSCORE', key, '-inf', now - window)
        local count = redis.call('ZCARD', key)
        if count >= limit then
            local ttl = redis.call('TTL', key)
            return {-1, ttl}
        end
        redis.call('ZADD', key, now, now .. ':' .. math.random(1,1000000))
        redis.call('EXPIRE', key, window)
        return {count + 1, 0}
    """

    def __init__(
        self,
        app: Any,
        *,
        rate: float = 30.0,        # Requests per second
        burst: int = 60,           # Max burst
        enabled: bool = True,
        key_func: Callable | None = None,  # Custom key extraction (default: client IP)
        redis_url: str | None = None,
        skip_paths: list[str] | None = None,
    ):
        super().__init__(app)
        self.enabled = enabled
        self.rate = rate
        self.burst = burst
        self.key_func = key_func or self._default_key
        self.redis_url = redis_url or __import__("os").environ.get("MAOP_REDIS_URL")
        self._redis: Any = None
        self._redis_ttl = max(1, int(burst / rate)) + 5  # seconds to keep keys
        self._skip_paths = set(skip_paths or [
            "/api/health", "/", "/api/prometheus", "/metrics",
            "/api/docs", "/openapi.json",
        ])
        self._skip_extensions = (".css", ".js", ".ico", ".png", ".svg", ".woff2", ".woff", ".ttf")
        self._buckets: dict[str, Any] = {}
        self._request_count = 0  # for periodic cleanup
        # B20: 保护 _buckets/_request_count 的并发读写。
        self._lock = threading.Lock()
        self._redis_available = self._init_redis()

    def _init_redis(self) -> bool:
        """Best-effort Redis connection. Returns True if available."""
        if not self.redis_url:
            return False
        try:
            import redis  # type: ignore
            self._redis = redis.Redis.from_url(self.redis_url, socket_connect_timeout=1, socket_timeout=1)
            self._redis.ping()
            logger.info("RateLimitMiddleware: Redis backend connected (%s)", self.redis_url)
            return True
        except Exception as exc:  # pragma: no cover — Redis is optional
            logger.warning("RateLimitMiddleware: Redis unavailable (%s), falling back to in-memory", exc)
            self._redis = None
            return False

    @staticmethod
    def _default_key(request: Request) -> str:
        """Extract client IP as rate limit key.

        When MAOP_TRUST_PROXY is enabled, use X-Forwarded-For header
        to get the real client IP behind a reverse proxy.
        """
        import os

        if os.environ.get("MAOP_TRUST_PROXY", "0") == "1":
            xff = request.headers.get("x-forwarded-for", "")
            if xff:
                # XFF can contain multiple IPs, take the first (original client)
                return xff.split(",")[0].strip()  # type: ignore
        return request.client.host if request.client else "unknown"

    def _should_skip(self, path: str) -> bool:
        """Return True if the path should bypass rate limiting."""
        return path in self._skip_paths or any(
            path.endswith(ext) for ext in self._skip_extensions
        )

    def _redis_check(self, key: str) -> tuple[bool, float]:
        """Check rate limit via Redis sliding window.

        Returns (allowed, retry_after_seconds).
        """
        assert self._redis is not None
        now = time.time()
        window = int(self._redis_ttl)
        count, ttl = self._redis.eval(
            self._REDIS_LUA_SLIDING_WINDOW,
            1,
            f"ratelimit:{key}",
            now,
            window,
            self.burst,
        )
        if count == -1:
            return False, float(ttl or 0)
        return True, 0.0

    def _memory_check(self, key: str) -> tuple[bool, float]:
        """Check rate limit via in-memory token bucket.

        Returns (allowed, retry_after_seconds).
        """
        now = time.monotonic()

        # B20: 加锁保护 _buckets/_request_count 的并发读写。
        with self._lock:
            # Periodic cleanup: remove stale buckets every 200 requests
            self._request_count += 1
            if self._request_count % 200 == 0:
                stale_cutoff = now - 300  # 5 minutes
                stale_keys = [k for k, v in self._buckets.items() if v["last"] < stale_cutoff]
                for k in stale_keys:
                    del self._buckets[k]

            if key not in self._buckets:
                self._buckets[key] = {"tokens": float(self.burst), "last": now}

            bucket = self._buckets[key]
            elapsed = now - bucket["last"]
            bucket["tokens"] = min(self.burst, bucket["tokens"] + elapsed * self.rate)
            bucket["last"] = now

            if bucket["tokens"] < 1.0:
                retry_after = (1.0 - bucket["tokens"]) / self.rate
                return False, retry_after

            bucket["tokens"] -= 1.0
        return True, 0.0

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        if not self.enabled:
            return cast(Response, await call_next(request))

        # Skip health checks, metrics, static assets
        path = request.url.path
        if self._should_skip(path):
            return cast(Response, await call_next(request))

        key = self.key_func(request)

        # Prefer Redis backend; fall back to in-memory transparently.
        if self._redis_available and self._redis is not None:
            try:
                allowed, retry_after = self._redis_check(key)
            except Exception as exc:  # pragma: no cover — Redis transient failure
                logger.warning("RateLimitMiddleware: Redis check failed (%s), falling back to in-memory", exc)
                self._redis_available = False
                allowed, retry_after = self._memory_check(key)
        else:
            allowed, retry_after = self._memory_check(key)

        if not allowed:
            return JSONResponse(
                status_code=429,
                content={
                    "error": "Rate limit exceeded",
                    "retry_after_s": round(retry_after, 2),
                },
                headers={"Retry-After": str(int(retry_after) + 1)},
            )

        return cast(Response, await call_next(request))


class AuditMiddleware(BaseHTTPMiddleware):
    """Records every API request as an audit log entry.

    Fields captured: timestamp, method, path, user identity/roles, tenant_id,
    status_code, latency_ms, client_ip, user_agent.
    Output goes to a structured ``maop.audit`` logger (JSON-ish log record),
    which operators can ship to SIEM / log aggregation.
    """

    def __init__(
        self,
        app: Any,
        *,
        enabled: bool = True,
        exclude_paths: list[str] | None = None,
    ):
        super().__init__(app)
        self.enabled = enabled
        self.exclude_paths = set(exclude_paths or ["/api/health", "/", "/api/docs", "/openapi.json"])
        self._logger = logging.getLogger("maop.audit")

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        if not self.enabled:
            return cast(Response, await call_next(request))

        path = request.url.path
        if path in self.exclude_paths:
            return cast(Response, await call_next(request))

        start = time.monotonic()
        response = cast(Response, await call_next(request))
        latency_ms = round((time.monotonic() - start) * 1000.0, 2)

        self._logger.info(
            "audit: method=%s path=%s status=%s latency_ms=%s "
            "identity=%s roles=%s tenant=%s ip=%s ua=%s",
            request.method,
            path,
            getattr(response, "status_code", 0),
            latency_ms,
            getattr(request.state, "auth_identity", "") or "",
            ",".join(getattr(request.state, "auth_roles", []) or []),
            getattr(request.state, "tenant_id", "") or "",
            (request.client.host if request.client else ""),
            request.headers.get("user-agent", ""),
        )
        return response


class IPAllowlistMiddleware(BaseHTTPMiddleware):
    """Global IP allowlist/blocklist middleware.

    Operates before auth: requests from blocked IPs are rejected with 403
    regardless of credentials. Requests from IPs not on the allowlist (when
    allowlist is non-empty) are also rejected with 403. This provides a
    network-level gate independent of per-API-key IP restrictions.

    Config via env vars:
      MAOP_IP_BLOCKLIST   comma-separated CIDRs / IPs to block
      MAOP_IP_ALLOWLIST   comma-separated CIDRs / IPs to allow (empty = allow all)
    """

    def __init__(
        self,
        app: Any,
        *,
        enabled: bool = True,
        blocklist: list[str] | None = None,
        allowlist: list[str] | None = None,
    ):
        super().__init__(app)
        self.enabled = enabled
        self.blocklist = blocklist or []
        self.allowlist = allowlist or []

    def _matches(self, ip: str, patterns: list[str]) -> bool:
        import ipaddress
        try:
            addr = ipaddress.ip_address(ip)
        except ValueError:
            return False
        for pat in patterns:
            pat = pat.strip()
            if not pat:
                continue
            try:
                if "/" in pat:
                    if addr in ipaddress.ip_network(pat, strict=False):
                        return True
                elif addr == ipaddress.ip_address(pat):
                    return True
            except ValueError:
                continue  # silent-ok: scan-continue — 单个非法网段/地址模式跳过继续匹配后续
        return False

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        if not self.enabled:
            return cast(Response, await call_next(request))

        client_ip = request.client.host if request.client else ""
        # Trust-proxy awareness for real client IP.
        from maop.config.settings import trust_proxy_enabled

        if trust_proxy_enabled():
            xff = request.headers.get("x-forwarded-for", "")
            if xff:
                client_ip = xff.split(",")[0].strip()

        if client_ip and self._matches(client_ip, self.blocklist):
            return JSONResponse(status_code=403, content={"error": "IP blocked"})

        if self.allowlist and client_ip and not self._matches(client_ip, self.allowlist):
            return JSONResponse(status_code=403, content={"error": "IP not allowed"})

        return cast(Response, await call_next(request))


class RequestSizeLimitMiddleware(BaseHTTPMiddleware):
    """Rejects requests whose body exceeds a configured byte limit.

    FastAPI/Starlette do not enforce a request-body size cap by default,
    making the service vulnerable to large-body DoS. This middleware reads
    the ``Content-Length`` header and short-circuits oversized requests
    before the body is parsed.

    Config:
      max_bytes: maximum allowed body size in bytes (default 10 MiB)
    """

    def __init__(
        self,
        app: Any,
        *,
        enabled: bool = True,
        max_bytes: int = 10 * 1024 * 1024,
    ):
        super().__init__(app)
        self.enabled = enabled
        self.max_bytes = max_bytes

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        if not self.enabled:
            return cast(Response, await call_next(request))

        content_length = request.headers.get("content-length")
        if content_length is not None:
            try:
                size = int(content_length)
            except ValueError:
                size = 0
            if size > self.max_bytes:
                return JSONResponse(
                    status_code=413,
                    content={
                        "error": "Request body too large",
                        "max_bytes": self.max_bytes,
                    },
                )

        return cast(Response, await call_next(request))
