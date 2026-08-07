"""MAOP Provider Health Check — Runtime health verification for LLM providers.

Performs actual API calls to verify provider connectivity and model availability.
Supports OpenAI-compatible /v1/models endpoint and custom health check URLs.

Usage::

    from maop.core.routing.provider_health import ProviderHealthChecker

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


def _safe_enum_value(val: Any) -> str:
    """从 enum / MagicMock / str 中提取小写字符串值，无法提取时返回空串。

    用于兼容生产环境（真实 enum）与测试环境（MagicMock）的 provider 类型判断。
    """
    try:
        v = getattr(val, "value", val)
        s = str(v).lower()
        # MagicMock 的字符串形式包含 "magicmock"，过滤掉
        if "magicmock" in s or s.startswith("<"):
            return ""
        return s
    except Exception:
        logger.debug("Silent exception in core/provider_health.py:48", exc_info=True)
        return ""


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

        # 根据 provider 类型选择检查策略（header / URL / HTTP 方法）
        provider_type = _safe_enum_value(pdef.type)
        protocol = _safe_enum_value(pdef.protocol)
        base_url = (pdef.base_url or "").rstrip("/")

        is_anthropic = "claude_code" in protocol or "anthropic" in base_url.lower()
        is_ollama = "ollama" in provider_type or "ollama" in protocol or "ollama" in base_url.lower()

        # 优先使用显式配置的 health_check_url，否则按 provider 类型推导
        check_url = pdef.health_check_url
        if not check_url:
            if is_anthropic:
                check_url = f"{base_url}/messages" if base_url else ""
            elif is_ollama:
                check_url = f"{base_url}/api/tags" if base_url else ""
            else:
                check_url = f"{base_url}/models" if base_url else ""

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

        # 根据 provider 类型构造请求 header
        if is_anthropic:
            # Anthropic 使用 x-api-key + anthropic-version，不走 Bearer token
            headers: dict[str, str] = {
                "x-api-key": api_key or "",
                "anthropic-version": pdef.extra_headers.get("anthropic-version", "2023-06-01"),
                "content-type": "application/json",
            }
        elif is_ollama:
            # Ollama 本地服务，无需鉴权 header
            headers = {}
        else:
            # OpenAI 兼容：Bearer token
            headers = {"Authorization": f"Bearer {api_key}"}
        headers.update(pdef.extra_headers)

        start = time.monotonic()
        try:
            async with httpx.AsyncClient(timeout=pdef.timeout_s) as client:
                if is_anthropic:
                    # Anthropic 没有 list models 端点，发一个 max_tokens=1 的最小请求探测
                    # 200/400 表示鉴权通过（healthy），401/403 表示鉴权失败（unhealthy）
                    probe_payload = {
                        "model": "claude-3-haiku-20240307",
                        "max_tokens": 1,
                        "messages": [{"role": "user", "content": "hi"}],
                    }
                    resp = await client.post(check_url, json=probe_payload, headers=headers)
                else:
                    resp = await client.get(check_url, headers=headers)
            latency_ms = int((time.monotonic() - start) * 1000)

            # Anthropic: 200 或 400 都算 healthy（鉴权通过，参数可能不对）
            if is_anthropic and resp.status_code in (200, 400):
                self._registry.providers.mark_healthy(provider_name)
                return HealthResult(
                    provider=provider_name, healthy=True,
                    latency_ms=latency_ms, checked_at=now,
                )
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
