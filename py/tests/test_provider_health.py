"""Tests for ProviderHealthChecker — LLM provider runtime health verification."""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from maop.core.provider_health import HealthResult, ProviderHealthChecker


def _make_registry(providers_data: dict | None = None, has_vault: bool = False):
    registry = MagicMock()
    if providers_data is None:
        providers_data = {
            "openai": MagicMock(
                enabled=True,
                type=MagicMock(value="openai"),
                base_url="https://api.openai.com/v1",
                health_check_url="",
                timeout_s=10,
                extra_headers={},
            ),
        }
    registry.providers.get.return_value = providers_data.get("openai")
    registry.providers.list_providers.return_value = [
        {"name": k} for k in providers_data
    ]
    registry.providers.get_api_key.return_value = "sk-test-key"
    registry.providers.mark_healthy = MagicMock()
    registry.providers.mark_unhealthy = MagicMock()
    vault = MagicMock() if has_vault else None
    if vault:
        vault.retrieve.return_value = "sk-vault-key"
    return registry, vault


class TestHealthResult:
    def test_defaults(self):
        r = HealthResult(provider="test")
        assert r.provider == "test"
        assert r.healthy is False
        assert r.latency_ms == 0
        assert r.models_available == []
        assert r.error == ""

    def test_with_values(self):
        r = HealthResult(
            provider="openai", healthy=True,
            latency_ms=150, models_available=["gpt-4"],
        )
        assert r.healthy is True
        assert r.latency_ms == 150


class TestProviderHealthChecker:
    @pytest.mark.asyncio
    async def test_no_registry(self):
        checker = ProviderHealthChecker()
        result = await checker.check("openai")
        assert result.healthy is False
        assert "No registry" in result.error

    @pytest.mark.asyncio
    async def test_provider_not_found(self):
        registry = MagicMock()
        registry.providers.get.return_value = None
        checker = ProviderHealthChecker(registry=registry)
        result = await checker.check("nonexistent")
        assert result.healthy is False
        assert "not found" in result.error

    @pytest.mark.asyncio
    async def test_provider_disabled(self):
        pdef = MagicMock(enabled=False)
        registry = MagicMock()
        registry.providers.get.return_value = pdef
        checker = ProviderHealthChecker(registry=registry)
        result = await checker.check("disabled_prov")
        assert result.healthy is False
        assert "disabled" in result.error

    @pytest.mark.asyncio
    async def test_builtin_provider_healthy(self):
        from maop.model.schema import ProviderType
        pdef = MagicMock(enabled=True, type=ProviderType.BUILTIN)
        registry = MagicMock()
        registry.providers.get.return_value = pdef
        checker = ProviderHealthChecker(registry=registry)
        result = await checker.check("builtin")
        assert result.healthy is True
        assert result.latency_ms == 0

    @pytest.mark.asyncio
    async def test_no_api_key(self):
        pdef = MagicMock(
            enabled=True,
            type=MagicMock(value="openai"),
            base_url="https://api.example.com",
            health_check_url="",
            timeout_s=5,
            extra_headers={},
        )
        registry = MagicMock()
        registry.providers.get.return_value = pdef
        registry.providers.get_api_key.return_value = None
        checker = ProviderHealthChecker(registry=registry, vault=None)
        result = await checker.check("nokey")
        assert result.healthy is False
        assert "No API key" in result.error

    @pytest.mark.asyncio
    async def test_vault_key_resolved(self):
        pdef = MagicMock(
            enabled=True,
            type=MagicMock(value="openai"),
            base_url="",
            health_check_url="",
            timeout_s=5,
            extra_headers={},
        )
        registry = MagicMock()
        registry.providers.get.return_value = pdef
        registry.providers.get_api_key.return_value = None
        vault = MagicMock()
        vault.retrieve.return_value = "sk-vault-key"
        checker = ProviderHealthChecker(registry=registry, vault=vault)
        result = await checker.check("vault_prov")
        assert result.healthy is True
        assert result.error == ""

    @pytest.mark.asyncio
    async def test_check_all_empty(self):
        registry = MagicMock()
        registry.providers.list_providers.return_value = []
        checker = ProviderHealthChecker(registry=registry)
        results = await checker.check_all()
        assert results == []

    @pytest.mark.asyncio
    async def test_check_all_returns_results(self):
        registry = MagicMock()
        registry.providers.list_providers.return_value = [
            {"name": "openai"}, {"name": "anthropic"},
        ]
        registry.providers.get.return_value = None
        checker = ProviderHealthChecker(registry=registry)
        results = await checker.check_all()
        assert len(results) == 2

    def test_parse_models_dict(self):
        checker = ProviderHealthChecker()
        data = {"data": [{"id": "gpt-4"}, {"id": "gpt-3.5"}]}
        models = checker._parse_models(data)
        assert models == ["gpt-4", "gpt-3.5"]

    def test_parse_models_list(self):
        checker = ProviderHealthChecker()
        data = [{"data": [{"id": "m1"}]}]
        models = checker._parse_models(data)
        assert models == ["m1"]

    def test_parse_models_empty(self):
        checker = ProviderHealthChecker()
        assert checker._parse_models({}) == []
        assert checker._parse_models([]) == []
