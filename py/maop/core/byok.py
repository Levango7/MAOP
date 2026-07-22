"""MAOP BYOK Gateway — Bring Your Own Key provider-agnostic key routing.

Provides a unified interface for routing API keys to LLM providers,
supporting multiple key sources (vault, environment, per-tenant keys)
with automatic failover and key rotation.

Usage::

    from maop.core.byok import BYOKGateway

    gateway = BYOKGateway(root_dir="/path/to/MAOP")
    key = await gateway.resolve("openai", tenant_id="acme")
    provider = gateway.route("openai", model="gpt-4")
"""
from __future__ import annotations

import logging
import os
import time
from typing import Any

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


class KeySource(BaseModel):
    provider: str
    source_type: str = "env"
    key_ref: str = ""
    priority: int = 0
    enabled: bool = True
    metadata: dict[str, Any] = Field(default_factory=dict)


class KeyRoute(BaseModel):
    provider: str
    model: str = "*"
    tenant_id: str = ""
    key_source: str = ""
    fallback_provider: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)


class ResolvedKey(BaseModel):
    provider: str
    key: str
    source: str = ""
    tenant_id: str = ""
    expires_at: float = 0.0


class BYOKGateway:
    """Provider-agnostic API key routing gateway.

    Supports:
      - Multiple key sources per provider (env, vault, direct)
      - Per-tenant key isolation
      - Automatic failover to fallback providers
      - Key rotation tracking
    """

    def __init__(self, root_dir: str | None = None, vault: Any = None) -> None:
        self._vault = vault
        self._sources: dict[str, list[KeySource]] = {}
        self._routes: list[KeyRoute] = []
        self._key_cache: dict[str, ResolvedKey] = {}
        self._key_usage: dict[str, int] = {}

    def register_source(self, source: KeySource) -> None:
        if source.provider not in self._sources:
            self._sources[source.provider] = []
        self._sources[source.provider].append(source)
        self._sources[source.provider].sort(key=lambda s: s.priority, reverse=True)

    def add_route(self, route: KeyRoute) -> None:
        self._routes.append(route)

    async def resolve(
        self,
        provider: str,
        *,
        model: str = "",
        tenant_id: str = "",
    ) -> ResolvedKey | None:
        cache_key = f"{provider}:{model}:{tenant_id}"
        cached = self._key_cache.get(cache_key)
        if cached and (cached.expires_at == 0 or cached.expires_at > time.time()):
            return cached

        route = self._find_route(provider, model, tenant_id)
        if route and route.fallback_provider:
            key = await self._resolve_from_sources(provider, tenant_id)
            if key:
                return key
            logger.info("[byok] Failing over from %s to %s", provider, route.fallback_provider)
            return await self._resolve_from_sources(route.fallback_provider, tenant_id)

        key = await self._resolve_from_sources(provider, tenant_id)
        if key:
            self._key_cache[cache_key] = key
            self._key_usage[provider] = self._key_usage.get(provider, 0) + 1
        return key

    def route(self, provider: str, model: str = "") -> str:
        for r in self._routes:
            if r.provider == provider and (r.model == "*" or r.model == model):
                return r.key_source or provider
        return provider

    def get_usage_stats(self) -> dict[str, int]:
        return dict(self._key_usage)

    async def _resolve_from_sources(self, provider: str, tenant_id: str = "") -> ResolvedKey | None:
        sources = self._sources.get(provider, [])
        for source in sources:
            if not source.enabled:
                continue

            if source.source_type == "env":
                key = os.getenv(source.key_ref, "")
                if key:
                    return ResolvedKey(provider=provider, key=key, source="env", tenant_id=tenant_id)

            elif source.source_type == "vault" and self._vault:
                try:
                    key = self._vault.retrieve(provider)
                    if key:
                        return ResolvedKey(provider=provider, key=key, source="vault", tenant_id=tenant_id)
                except Exception as exc:
                    logger.debug("[byok] Vault lookup failed for %s: %s", provider, exc)

            elif source.source_type == "tenant" and tenant_id:
                if not self._verify_tenant_caller(source, tenant_id):
                    logger.warning("[byok] Tenant key access denied: caller not authorized for tenant=%s provider=%s", tenant_id, provider)
                    continue
                env_key = f"MAOP_KEY_{tenant_id.upper()}_{provider.upper()}"
                key = os.getenv(env_key, "")
                if key:
                    return ResolvedKey(provider=provider, key=key, source="tenant", tenant_id=tenant_id)

            elif source.source_type == "direct":
                logger.error("[byok] 'direct' key source is prohibited — keys must not be stored in plaintext config; use 'env' or 'vault' instead")
                continue

        return None

    def _find_route(self, provider: str, model: str, tenant_id: str) -> KeyRoute | None:
        for r in self._routes:
            if r.provider != provider:
                continue
            if r.model != "*" and r.model != model:
                continue
            if r.tenant_id and r.tenant_id != tenant_id:
                continue
            return r
        return None

    def _verify_tenant_caller(self, source: KeySource, tenant_id: str) -> bool:
        allowed_tenants = source.metadata.get("allowed_tenants", [])
        if not allowed_tenants:
            return True
        return tenant_id in allowed_tenants