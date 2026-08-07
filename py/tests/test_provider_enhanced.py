"""Tests for LLM Provider enhancements — Ollama, CRUD, API Key Vault, Health Check."""

from pathlib import Path

import pytest

from maop.core.security.api_key_vault import ApiKeyVault
from maop.model.registry import ModelRegistry, ProviderRegistry
from maop.model.schema import (
    ModelDef,
    ProtocolType,
    ProviderDef,
    ProviderType,
    ThinkingLevel,
    thinking_to_api_params,
)


class TestOllamaProviderType:
    def test_ollama_provider_type(self):
        assert ProviderType.OLLAMA.value == "ollama"

    def test_ollama_protocol_type(self):
        assert ProtocolType.OLLAMA_CHAT.value == "ollama_chat"

    def test_thinking_ollama_chat(self):
        params = thinking_to_api_params(ThinkingLevel.MEDIUM, ProtocolType.OLLAMA_CHAT)
        assert "options" in params
        assert "num_predict" in params["options"]

    def test_thinking_ollama_chat_low(self):
        params = thinking_to_api_params(ThinkingLevel.LOW, ProtocolType.OLLAMA_CHAT)
        assert params["options"]["num_predict"] == 512

    def test_thinking_ollama_chat_high(self):
        params = thinking_to_api_params(ThinkingLevel.HIGH, ProtocolType.OLLAMA_CHAT)
        assert params["options"]["num_predict"] == 12288


class TestProviderDefOllama:
    def test_ollama_provider_def(self):
        pdef = ProviderDef(
            type=ProviderType.OLLAMA,
            protocol=ProtocolType.OLLAMA_CHAT,
            base_url="http://localhost:11434",
            timeout_s=300,
        )
        assert pdef.type == ProviderType.OLLAMA
        assert pdef.protocol == ProtocolType.OLLAMA_CHAT
        assert pdef.base_url == "http://localhost:11434"


class TestProviderRegistryCRUD:
    def test_add_provider(self):
        reg = ProviderRegistry()
        pdef = ProviderDef(type=ProviderType.OLLAMA, protocol=ProtocolType.OLLAMA_CHAT,
                           base_url="http://localhost:11434")
        result = reg.add("ollama", pdef)
        assert result.type == ProviderType.OLLAMA
        assert reg.get("ollama") is not None

    def test_remove_provider(self):
        reg = ProviderRegistry()
        pdef = ProviderDef()
        reg.add("test-provider", pdef)
        removed = reg.remove("test-provider")
        assert removed is True
        assert reg.get("test-provider") is None

    def test_remove_nonexistent(self):
        reg = ProviderRegistry()
        assert reg.remove("nonexistent") is False


class TestModelRegistryCRUD:
    @pytest.fixture
    def reg(self, tmp_path):
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        (config_dir / "models.yaml").write_text("providers: {}\nmodels: {}\n")
        return ModelRegistry(project_root=str(tmp_path))

    def test_add_provider(self, reg):
        pdef = ProviderDef(type=ProviderType.OLLAMA, protocol=ProtocolType.OLLAMA_CHAT,
                           base_url="http://localhost:11434")
        reg.add_provider("ollama", pdef)
        assert "ollama" in reg.config.providers

    def test_remove_provider(self, reg):
        pdef = ProviderDef()
        reg.add_provider("test-p", pdef)
        reg.remove_provider("test-p")
        assert "test-p" not in reg.config.providers

    def test_remove_provider_with_models(self, reg):
        pdef = ProviderDef()
        reg.add_provider("used-p", pdef)
        mdef = ModelDef(provider="used-p")
        reg.add_model("test-model", mdef)
        with pytest.raises(ValueError, match="used by models"):
            reg.remove_provider("used-p")

    def test_add_model(self, reg):
        mdef = ModelDef(provider="local", family="test")
        reg.add_model("test-model", mdef)
        assert reg.get_model("test-model") is not None

    def test_remove_model(self, reg):
        mdef = ModelDef(provider="local")
        reg.add_model("test-model", mdef)
        removed = reg.remove_model("test-model")
        assert removed is True
        assert reg.get_model("test-model") is None

    def test_remove_model_not_found(self, reg):
        assert reg.remove_model("nonexistent") is False

    def test_save_and_reload(self, reg, tmp_path):
        pdef = ProviderDef(type=ProviderType.OLLAMA, protocol=ProtocolType.OLLAMA_CHAT,
                           base_url="http://localhost:11434")
        reg.add_provider("ollama", pdef)
        mdef = ModelDef(provider="ollama", family="llama", context_window=8192)
        reg.add_model("llama3", mdef)
        reg.save()

        reg2 = ModelRegistry(project_root=str(tmp_path))
        assert "ollama" in reg2.config.providers
        assert reg2.get_model("llama3") is not None


class TestApiKeyVault:
    def test_store_and_retrieve(self, tmp_path):
        vault = ApiKeyVault(root_dir=str(tmp_path))
        vault.store("openai", "sk-test123")
        key = vault.retrieve("openai")
        assert key == "sk-test123"

    def test_delete(self, tmp_path):
        vault = ApiKeyVault(root_dir=str(tmp_path))
        vault.store("openai", "sk-test123")
        deleted = vault.delete("openai")
        assert deleted is True
        assert vault.retrieve("openai") is None

    def test_delete_not_found(self, tmp_path):
        vault = ApiKeyVault(root_dir=str(tmp_path))
        assert vault.delete("nonexistent") is False

    def test_list_providers(self, tmp_path):
        vault = ApiKeyVault(root_dir=str(tmp_path))
        vault.store("openai", "sk-a")
        vault.store("anthropic", "sk-b")
        providers = vault.list_providers()
        assert "openai" in providers
        assert "anthropic" in providers

    def test_update_existing(self, tmp_path):
        vault = ApiKeyVault(root_dir=str(tmp_path))
        vault.store("openai", "sk-old")
        vault.store("openai", "sk-new")
        assert vault.retrieve("openai") == "sk-new"

    def test_retrieve_not_found(self, tmp_path):
        vault = ApiKeyVault(root_dir=str(tmp_path))
        assert vault.retrieve("nonexistent") is None


class TestProviderHealthChecker:
    def test_check_no_registry(self):
        import asyncio

        from maop.core.routing.provider_health import ProviderHealthChecker
        checker = ProviderHealthChecker(registry=None)
        result = asyncio.run(checker.check("openai"))
        assert result.healthy is False
        assert "No registry" in result.error

    def test_check_builtin_provider(self):
        import asyncio

        from maop.core.routing.provider_health import ProviderHealthChecker
        reg = ModelRegistry(project_root=str(Path(__file__).parent.parent.parent / "config" / ".."))
        pdef = ProviderDef(type=ProviderType.BUILTIN, enabled=True)
        reg.add_provider("test-builtin", pdef)
        checker = ProviderHealthChecker(registry=reg)
        result = asyncio.run(checker.check("test-builtin"))
        assert result.healthy is True
