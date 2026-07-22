"""MAOP Provider Health Check — Runtime health verification for LLM providers.

Performs actual API calls to verify provider connectivity and model availability.
Supports OpenAI-compatible /v1/models endpoint and custom health check URLs.

Usage::

    from maop.core.provider_health import ProviderHealthChecker

    checker = ProviderHealthChecker(registry=model_registry)
    result = await checker.check("openai")
    results = await checker.check_all()
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, cast

from pydantic import BaseModel

logger = logging.getLogger(__name__)


class HealthResult(BaseModel):
    provider: str
    healthy: bool = False
    latency_ms: int = 0
    models_available: list[str] = []
    error: str = ""
    checked_at: str = ""


class ProviderHealthChecker:
    """Runtime health checker for LLM providers."""

    def __init__(self, registry: Any = None, vault: Any = None) -> None:
        self._registry = registry
        self._vault = vault

    async def check(self, provider_name: str) -> HealthResult:
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc).isoformat()

        if self._registry is None:
            return HealthResult(
                provider=provider_name, healthy=False,
                error="No registry configured", checked_at=now,
            )

        pdef = self._registry.providers.get(provider_name)
        if pdef is None:
            return HealthResult(
                provider=provider_name, healthy=False,
                error="Provider not found", checked_at=now,
            )

        if not pdef.enabled:
            return HealthResult(
                provider=provider_name, healthy=False,
                error="Provider disabled", checked_at=now,
            )

        from maop.model.schema import ProviderType
        if pdef.type == ProviderType.BUILTIN:
            return HealthResult(
                provider=provider_name, healthy=True,
                latency_ms=0, checked_at=now,
            )

        api_key = self._resolve_key(provider_name, pdef)
        if not api_key:
            return HealthResult(
                provider=provider_name, healthy=False,
                error="No API key configured", checked_at=now,
            )

        check_url = pdef.health_check_url
        if not check_url and pdef.base_url:
            check_url = pdef.base_url.rstrip("/") + "/models"

        if not check_url:
            has_key = bool(api_key)
            return HealthResult(
                provider=provider_name, healthy=has_key,
                latency_ms=0, error="" if has_key else "No health check URL",
                checked_at=now,
            )

        try:
            import httpx
        except ImportError:
            has_key = bool(api_key)
            return HealthResult(
                provider=provider_name, healthy=has_key,
                error="httpx not installed, key-only check",
                checked_at=now,
            )

        start = time.monotonic()
        try:
            headers: dict[str, str] = {"Authorization": f"Bearer {api_key}"}
            headers.update(pdef.extra_headers)
            async with httpx.AsyncClient(timeout=pdef.timeout_s) as client:
                resp = await client.get(check_url, headers=headers)
            latency_ms = int((time.monotonic() - start) * 1000)

            if resp.status_code == 200:
                models = self._parse_models(resp.json())
                self._registry.providers.mark_healthy(provider_name)
                return HealthResult(
                    provider=provider_name, healthy=True,
                    latency_ms=latency_ms, models_available=models,
                    checked_at=now,
                )
            else:
                error = f"HTTP {resp.status_code}"
                self._registry.providers.mark_unhealthy(provider_name, error)
                return HealthResult(
                    provider=provider_name, healthy=False,
                    latency_ms=latency_ms, error=error, checked_at=now,
                )
        except asyncio.TimeoutError:
            latency_ms = int((time.monotonic() - start) * 1000)
            self._registry.providers.mark_unhealthy(provider_name, "timeout")
            return HealthResult(
                provider=provider_name, healthy=False,
                latency_ms=latency_ms, error="Timeout", checked_at=now,
            )
        except Exception as exc:
            latency_ms = int((time.monotonic() - start) * 1000)
            self._registry.providers.mark_unhealthy(provider_name, str(exc))
            return HealthResult(
                provider=provider_name, healthy=False,
                latency_ms=latency_ms, error=str(exc), checked_at=now,
            )

    async def check_all(self) -> list[HealthResult]:
        if self._registry is None:
            return []
        providers = self._registry.providers.list_providers()
        tasks = [self.check(p["name"]) for p in providers]
        return await asyncio.gather(*tasks)

    def _resolve_key(self, provider_name: str, pdef: Any) -> str | None:
        if self._vault:
            key = self._vault.retrieve(provider_name)
            if key:
                return cast(str | None, key)
        return cast(str | None, self._registry.providers.get_api_key(provider_name))

    def _parse_models(self, data: dict | list) -> list[str]:
        if isinstance(data, list):
            data = data[0] if data else {}
        if isinstance(data, dict):
            models = data.get("data", [])
            if isinstance(models, list):
                return [m.get("id", str(m)) if isinstance(m, dict) else str(m) for m in models]
        return []
