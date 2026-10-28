"""MAOP Middleware — FastAPI middleware for Auth and Rate Limiting.

2026-10-28 三块拆分（文件规模门禁：1188 行 → 本子包；ALLOWED_OVERSIZE 归零）：
本包是**唯一对外门面**，公开名与拆分前 ``maop.core.security.middleware``
完全一致（导入路径不变、调用方零改动）：

  1. AuthMiddleware: API Key / JWT authentication for protected routes
  2. RateLimitMiddleware: Per-IP or per-key rate limiting
  3. setup_middleware / setup_security_middleware: one-call stacks

模块布局：
  - ``_auth.py``        — AuthMiddleware + owner-mode helpers + require_admin
  - ``_scope.py``       — ScopeMiddleware（路由级 scope 授权）
  - ``_csp.py``         — CSPMiddleware + connect-src 信任策略审计
  - ``_protection.py``  — RateLimit / Audit / IPAllowlist / RequestSizeLimit
  - ``_setup.py``       — 两个 one-call 装配函数
"""

from __future__ import annotations

from maop.core.security.middleware._auth import (
    AuthMiddleware,
    _disabled_auth_roles,
    _is_loopback_host,
    require_admin,
)
from maop.core.security.middleware._csp import (
    CSPMiddleware,
    _validate_connect_src,
)
from maop.core.security.middleware._protection import (
    AuditMiddleware,
    IPAllowlistMiddleware,
    RateLimitMiddleware,
    RequestSizeLimitMiddleware,
)
from maop.core.security.middleware._scope import ScopeMiddleware
from maop.core.security.middleware._setup import (
    setup_middleware,
    setup_security_middleware,
)

__all__ = [
    "AuditMiddleware",
    "AuthMiddleware",
    "CSPMiddleware",
    "IPAllowlistMiddleware",
    "RateLimitMiddleware",
    "RequestSizeLimitMiddleware",
    "ScopeMiddleware",
    "require_admin",
    "setup_middleware",
    "setup_security_middleware",
]
