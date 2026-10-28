"""CSP & security response headers middleware（含 connect-src 信任策略审计）.

原 ``middleware.py`` 的 CSP 块（2026-10-28 拆分）。
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any, cast

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware

logger = logging.getLogger(__name__)

# t88-L9: Dangerous connect-src tokens that weaken CSP.
# These are logged at WARNING but not rejected, because some
# deployments legitimately need them.
_CSP_DANGEROUS_TOKENS: dict[str, str] = {
    "*": "wildcard '*' allows connections to ANY origin — effectively disables connect-src",
    "http:": "plain HTTP scheme allows unencrypted connections (MITM risk)",
    "ws:": "plain WebSocket scheme allows unencrypted WS (MITM risk)",
    "data:": "data: URIs in connect-src allow inline content injection",
    "blob:": "blob: URIs in connect-src allow dynamic content sources",
}


def _validate_connect_src(connect_src: str, *, production: bool = False) -> None:
    """Validate CSP ``connect-src`` value for dangerous patterns.

    This is a **trust-policy audit**. The CSP ``connect-src`` directive controls
    where the browser can issue ``fetch()``, ``XMLHttpRequest``, and WebSocket
    connections. A permissive value undermines the entire CSP XSS mitigation.

    Trust policy
    ------------
    - ``'self'`` (default): trusted — same-origin only.
    - ``'none'``: trusted — most restrictive.
    - ``https://host`` / ``wss://host``: trusted — encrypted, explicit host.
    - Dangerous tokens (see ``_CSP_DANGEROUS_TOKENS``):
        * Non-production: logged at WARNING but NOT rejected, because dev
          environments may need ``http:`` for localhost or ``*`` for a public
          API gateway.
        * Production (``production=True``): **fail-closed** — raises
          ``ValueError`` so the server refuses to start with a weakened CSP.
    - Empty string: rejected (would produce ``connect-src`` with no sources,
      which defaults to ``default-src`` — confusing and likely unintended).

    Args:
        connect_src: The raw ``connect-src`` value (e.g. ``"'self'"``,
            ``"'self' wss://api.example.com"``).
        production: When True, dangerous tokens cause a hard failure instead
            of a warning.

    Raises:
        ValueError: If ``connect_src`` is empty, or (when production=True)
            when a dangerous token is present.
    """
    if production is None:
        from maop.config.settings import is_production

        production = is_production()

    if not connect_src or not connect_src.strip():
        raise ValueError(
            "CSP connect-src must not be empty — an empty value produces "
            "'connect-src' with no sources, which falls back to default-src "
            "and is likely unintended. Use 'none' to explicitly block all "
            "connections."
        )

    # Tokenize on whitespace and check each token.
    tokens = connect_src.split()
    for token in tokens:
        # Check exact-match dangerous tokens (e.g. "*", "http:")
        if token in _CSP_DANGEROUS_TOKENS:
            msg = (
                f"[CSP] connect-src contains dangerous token {token!r}: "
                f"{_CSP_DANGEROUS_TOKENS[token]}. "
            )
            if production:
                raise ValueError(msg + "Production CSP must not contain dangerous tokens. Use explicit https:// hosts.")
            logger.warning(msg + "This weakens the Content-Security-Policy. Ensure this is intentional (e.g. dev environment).")
        # Check prefix-match for http:// and ws:// (scheme + explicit host)
        elif token.startswith("http://"):
            msg = (
                f"[CSP] connect-src allows plain HTTP origin {token!r} — "
                "connections to this host are unencrypted (MITM risk). "
            )
            if production:
                raise ValueError(msg + "Production CSP must not allow plain HTTP origins. Prefer https://.")
            logger.warning(msg + "Prefer https:// in production.")
        elif token.startswith("ws://"):
            msg = (
                f"[CSP] connect-src allows plain WebSocket origin {token!r} — "
                "connections to this host are unencrypted (MITM risk). "
            )
            if production:
                raise ValueError(msg + "Production CSP must not allow plain WebSocket origins. Prefer wss://.")
            logger.warning(msg + "Prefer wss:// in production.")


class CSPMiddleware(BaseHTTPMiddleware):
    """Content-Security-Policy and security response headers.

    Sets CSP header on all responses to mitigate XSS, clickjacking,
    MIME-sniffing, and other injection attacks.

    The policy is tuned for the MAOP Dashboard frontend which uses:
      - External scripts from /js/       -> script-src 'self'
      - External CSS from /style.css     -> style-src  'self'
      - WebSocket to /ws (same origin)   -> connect-src 'self'
      - Chart.js (may use data: URIs)    -> img-src    'self' data:

    Inline event handlers (onclick) have been migrated to addEventListener
    in app-events.js, so script-src no longer needs 'unsafe-inline'.
    Inline style attributes have been migrated to CSS classes in style.css,
    so style-src no longer needs 'unsafe-inline'.

    Config (env vars):
      MAOP_CSP=0              Disable CSP entirely
      MAOP_CSP_REPORT_ONLY=1  Use Content-Security-Policy-Report-Only header
      MAOP_CSP_REPORT_URI     Reporting endpoint URL
      MAOP_CSP_CONNECT_SRC    Override connect-src (e.g. "'self' wss://other.host")
    """

    def __init__(
        self,
        app: Any,
        *,
        enabled: bool = True,
        report_only: bool = False,
        report_uri: str | None = None,
        connect_src: str = "'self'",
        extra_directives: str = "",
        production: bool = False,
    ):
        super().__init__(app)
        self.enabled = enabled
        self.report_only = report_only
        self.report_uri = report_uri

        # t88-L9: validate connect-src before building the policy.
        # connect-src governs XHR/fetch/WebSocket destinations — a
        # misconfigured value can punch a hole through the CSP. In production
        # mode, dangerous tokens cause a hard failure (fail-closed).
        _validate_connect_src(connect_src, production=production)

        # Build CSP policy string
        directives = [
            "default-src 'self'",
            "script-src 'self'",
            "style-src 'self'",
            "img-src 'self' data:",
            "font-src 'self'",
            f"connect-src {connect_src}",
            "object-src 'none'",
            "base-uri 'self'",
            "form-action 'self'",
            "frame-ancestors 'none'",
        ]
        if report_uri:
            directives.append(f"report-uri {report_uri}")
        if extra_directives:
            directives.append(extra_directives)
        self._csp_value = "; ".join(directives)

        # Security headers applied to every response
        self._security_headers = {
            "Cache-Control": "no-store",
            "X-Content-Type-Options": "nosniff",
            "X-Frame-Options": "DENY",
            "Referrer-Policy": "strict-origin-when-cross-origin",
            "Permissions-Policy": "camera=(), microphone=(), geolocation=(), payment=()",
            "Strict-Transport-Security": "max-age=31536000; includeSubDomains; preload",
            "X-DNS-Prefetch-Control": "off",
            "X-Permitted-Cross-Domain-Policies": "none",
            "Cross-Origin-Opener-Policy": "same-origin",
            "Cross-Origin-Resource-Policy": "same-origin",
        }

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        response = cast(Response, await call_next(request))
        if not self.enabled:
            return response

        # Set CSP header (report-only vs enforce)
        header_name = (
            "Content-Security-Policy-Report-Only"
            if self.report_only
            else "Content-Security-Policy"
        )
        response.headers[header_name] = self._csp_value

        # Additional security headers
        for key, val in self._security_headers.items():
            response.headers.setdefault(key, val)

        return response
