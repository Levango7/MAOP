"""Tests for LLM Provider fallback chain (NAP P5)."""

import pytest

from maop.core.agent.llm_chat.llm_provider import (
    BaseLLMProvider,
    FallbackResult,
    LLMProviderFactory,
    LLMResponse,
    ModelConfig,
    ProviderConfig,
    _is_error_response,
)


class FakeProvider(BaseLLMProvider):
    """Fake provider that returns preset responses."""

    def __init__(self, cfg: ProviderConfig, responses: list[LLMResponse] | None = None) -> None:
        super().__init__(cfg)
        self._responses = responses or []
        self._call_count = 0

    async def chat(self, messages, model="", *, temperature=0.7, max_tokens=4096, **kwargs):
        if self._call_count < len(self._responses):
            resp = self._responses[self._call_count]
            self._call_count += 1
            return resp
        return LLMResponse(content="[LLM Error] no more responses", provider=self._config.name)

    async def chat_stream(self, messages, model="", *, temperature=0.7, max_tokens=4096, **kwargs):
        yield ""


@pytest.fixture
def factory():
    return LLMProviderFactory.__new__(LLMProviderFactory)


def _setup_factory(factory, model_configs, provider_configs=None):
    factory._loaded = True
    factory._root = None
    factory._providers = {}
    factory._provider_configs = provider_configs or {}
    factory._model_configs = model_configs


class TestIsErrorResponse:
    def test_llm_error(self):
        assert _is_error_response(LLMResponse(content="[LLM Error] timeout"))

    def test_claude_error(self):
        assert _is_error_response(LLMResponse(content="[Claude Error] 500"))

    def test_ollama_error(self):
        assert _is_error_response(LLMResponse(content="[Ollama Error] conn refused"))

    def test_empty_content(self):
        assert _is_error_response(LLMResponse(content=""))

    def test_success_not_error(self):
        assert not _is_error_response(LLMResponse(content="Hello world"))

    def test_normal_response(self):
        assert not _is_error_response(LLMResponse(content="The answer is 42"))


class TestFallbackChain:
    @pytest.mark.asyncio
    async def test_primary_succeeds_no_fallback(self, factory):
        _setup_factory(factory, {
            "gpt-4": ModelConfig(name="gpt-4", provider="openai", fallback_model="gpt-3.5"),
        }, {
            "openai": ProviderConfig(name="openai", provider_type="openai-compatible"),
        })
        factory._providers["openai"] = FakeProvider(
            ProviderConfig(name="openai", provider_type="openai-compatible"),
            responses=[LLMResponse(content="Success", provider="openai")],
        )

        result = await factory.chat_with_fallback(
            [{"role": "user", "content": "hi"}], "gpt-4",
        )
        assert isinstance(result, FallbackResult)
        assert result.fell_back is False
        assert result.used_model == "gpt-4"
        assert result.response.content == "Success"

    @pytest.mark.asyncio
    async def test_primary_fails_fallback_succeeds(self, factory):
        _setup_factory(factory, {
            "gpt-4": ModelConfig(name="gpt-4", provider="openai", fallback_model="gpt-3.5"),
            "gpt-3.5": ModelConfig(name="gpt-3.5", provider="openai"),
        }, {
            "openai": ProviderConfig(name="openai", provider_type="openai-compatible"),
        })
        factory._providers["openai"] = FakeProvider(
            ProviderConfig(name="openai", provider_type="openai-compatible"),
            responses=[
                LLMResponse(content="[LLM Error] timeout", provider="openai"),
                LLMResponse(content="Fallback success", provider="openai"),
            ],
        )

        result = await factory.chat_with_fallback(
            [{"role": "user", "content": "hi"}], "gpt-4",
        )
        assert result.fell_back is True
        assert result.used_model == "gpt-3.5"
        assert result.response.content == "Fallback success"
        assert result.fallback_chain == ["gpt-4", "gpt-3.5"]

    @pytest.mark.asyncio
    async def test_multi_level_fallback(self, factory):
        _setup_factory(factory, {
            "model-a": ModelConfig(name="model-a", provider="p1", fallback_model="model-b"),
            "model-b": ModelConfig(name="model-b", provider="p2", fallback_model="model-c"),
            "model-c": ModelConfig(name="model-c", provider="p3"),
        }, {
            "p1": ProviderConfig(name="p1", provider_type="openai-compatible"),
            "p2": ProviderConfig(name="p2", provider_type="openai-compatible"),
            "p3": ProviderConfig(name="p3", provider_type="openai-compatible"),
        })
        factory._providers["p1"] = FakeProvider(
            ProviderConfig(name="p1", provider_type="openai-compatible"),
            responses=[LLMResponse(content="[LLM Error] fail 1", provider="p1")],
        )
        factory._providers["p2"] = FakeProvider(
            ProviderConfig(name="p2", provider_type="openai-compatible"),
            responses=[LLMResponse(content="[Claude Error] fail 2", provider="p2")],
        )
        factory._providers["p3"] = FakeProvider(
            ProviderConfig(name="p3", provider_type="openai-compatible"),
            responses=[LLMResponse(content="Final success", provider="p3")],
        )

        result = await factory.chat_with_fallback(
            [{"role": "user", "content": "hi"}], "model-a",
        )
        assert result.fell_back is True
        assert result.used_model == "model-c"
        assert result.response.content == "Final success"
        assert result.fallback_chain == ["model-a", "model-b", "model-c"]

    @pytest.mark.asyncio
    async def test_all_fail_returns_last_error(self, factory):
        _setup_factory(factory, {
            "model-a": ModelConfig(name="model-a", provider="p1", fallback_model="model-b"),
            "model-b": ModelConfig(name="model-b", provider="p2"),
        }, {
            "p1": ProviderConfig(name="p1", provider_type="openai-compatible"),
            "p2": ProviderConfig(name="p2", provider_type="openai-compatible"),
        })
        factory._providers["p1"] = FakeProvider(
            ProviderConfig(name="p1", provider_type="openai-compatible"),
            responses=[LLMResponse(content="[LLM Error] fail 1", provider="p1")],
        )
        factory._providers["p2"] = FakeProvider(
            ProviderConfig(name="p2", provider_type="openai-compatible"),
            responses=[LLMResponse(content="[LLM Error] fail 2", provider="p2")],
        )

        result = await factory.chat_with_fallback(
            [{"role": "user", "content": "hi"}], "model-a",
        )
        assert result.fell_back is True
        assert "[LLM Error]" in result.response.content

    @pytest.mark.asyncio
    async def test_no_fallback_model_stops(self, factory):
        _setup_factory(factory, {
            "solo": ModelConfig(name="solo", provider="p1"),
        }, {
            "p1": ProviderConfig(name="p1", provider_type="openai-compatible"),
        })
        factory._providers["p1"] = FakeProvider(
            ProviderConfig(name="p1", provider_type="openai-compatible"),
            responses=[LLMResponse(content="[LLM Error] fail", provider="p1")],
        )

        result = await factory.chat_with_fallback(
            [{"role": "user", "content": "hi"}], "solo",
        )
        assert result.fell_back is False
        assert result.used_model == "solo"
        assert "[LLM Error]" in result.response.content

    @pytest.mark.asyncio
    async def test_circular_fallback_stops(self, factory):
        _setup_factory(factory, {
            "a": ModelConfig(name="a", provider="p1", fallback_model="b"),
            "b": ModelConfig(name="b", provider="p2", fallback_model="a"),
        }, {
            "p1": ProviderConfig(name="p1", provider_type="openai-compatible"),
            "p2": ProviderConfig(name="p2", provider_type="openai-compatible"),
        })
        factory._providers["p1"] = FakeProvider(
            ProviderConfig(name="p1", provider_type="openai-compatible"),
            responses=[LLMResponse(content="[LLM Error] fail", provider="p1")],
        )
        factory._providers["p2"] = FakeProvider(
            ProviderConfig(name="p2", provider_type="openai-compatible"),
            responses=[LLMResponse(content="[LLM Error] fail", provider="p2")],
        )

        result = await factory.chat_with_fallback(
            [{"role": "user", "content": "hi"}], "a",
        )
        assert result.fallback_chain == ["a", "b"]

    @pytest.mark.asyncio
    async def test_no_provider_returns_error(self, factory):
        _setup_factory(factory, {
            "missing": ModelConfig(name="missing", provider="nonexistent"),
        })

        result = await factory.chat_with_fallback(
            [{"role": "user", "content": "hi"}], "missing",
        )
        assert "[LLM Error]" in result.response.content
