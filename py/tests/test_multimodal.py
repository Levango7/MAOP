"""Tests for maop.core.multimodal — modality handlers, unified interface, model router.

F2-02: verifies that each modality handler normalizes inputs into the
correct OpenAI-compatible content-part shape, that UnifiedModelInterface
assembles multi-modality messages correctly and delegates to the provider,
and that ModelRouter ranks models by capability / cost / latency.
"""

from __future__ import annotations

import base64
from collections.abc import AsyncGenerator
from typing import Any
from unittest.mock import AsyncMock

import pytest

from maop.core.llm_provider import LLMResponse
from maop.core.multimodal.modality_handlers import (
    AudioHandler,
    BaseModalityHandler,
    ImageHandler,
    ModalityHandlerRegistry,
    ModalityInput,
    ModalityType,
    TextHandler,
    VideoHandler,
)
from maop.core.multimodal.model_router import (
    ModelCapability,
    ModelRouter,
    RouteResult,
    RoutingCriteria,
    TaskType,
)
from maop.core.multimodal.unified_interface import (
    MultimodalRequest,
    MultimodalResponse,
    UnifiedModelInterface,
)

# ── Mock provider ─────────────────────────────────────────────


class _MockProvider:
    """Minimal duck-typed provider for UnifiedModelInterface tests.

    Implements the subset of BaseLLMProvider that UnifiedModelInterface
    calls: ``chat``, ``chat_stream``, ``name``.
    """

    def __init__(self, name: str = "mock") -> None:
        self.name = name
        self.chat = AsyncMock(side_effect=self._chat_impl)
        self._last_messages: list[dict[str, Any]] | None = None

    async def _chat_impl(
        self,
        messages: list[dict[str, Any]],
        model: str = "",
        *,
        temperature: float = 0.7,
        max_tokens: int = 4096,
        **kwargs: Any,
    ) -> LLMResponse:
        self._last_messages = messages
        return LLMResponse(
            content="mock response",
            model=model or "mock-model",
            finish_reason="stop",
            prompt_tokens=10,
            completion_tokens=5,
            total_tokens=15,
            latency_ms=42,
            provider=self.name,
        )

    async def chat_stream(
        self,
        messages: list[dict[str, Any]],
        model: str = "",
        *,
        temperature: float = 0.7,
        max_tokens: int = 4096,
        **kwargs: Any,
    ) -> AsyncGenerator[str, None]:
        for token in ("mock", " ", "stream"):
            yield token


# ── TestModalityHandlers ──────────────────────────────────────


class TestTextHandler:
    def test_plain_text(self):
        handler = TextHandler()
        part = handler.handle(ModalityInput(modality=ModalityType.TEXT, data="hello"))
        assert part == {"type": "text", "text": "hello"}

    def test_non_string_coerced(self):
        handler = TextHandler()
        part = handler.handle(ModalityInput(modality=ModalityType.TEXT, data=123))
        assert part == {"type": "text", "text": "123"}

    def test_none_becomes_empty(self):
        handler = TextHandler()
        part = handler.handle(ModalityInput(modality=ModalityType.TEXT))
        assert part == {"type": "text", "text": ""}


class TestImageHandler:
    def test_url_passthrough(self):
        handler = ImageHandler()
        url = "https://example.com/cat.png"
        part = handler.handle(ModalityInput(modality=ModalityType.IMAGE, data=url))
        assert part == {"type": "image_url", "image_url": {"url": url}}

    def test_bytes_inlined_as_data_url(self):
        handler = ImageHandler()
        raw = b"\x89PNG\r\n\x1a\n"  # PNG header bytes
        part = handler.handle(
            ModalityInput(modality=ModalityType.IMAGE, data=raw, mime_type="image/png")
        )
        url = part["image_url"]["url"]
        assert url.startswith("data:image/png;base64,")
        b64_part = url.split(",", 1)[1]
        assert base64.b64decode(b64_part) == raw

    def test_file_path_inlined(self, tmp_path):
        handler = ImageHandler()
        img = tmp_path / "test.png"
        raw = b"\x89PNG fake data"
        img.write_bytes(raw)
        part = handler.handle(ModalityInput(modality=ModalityType.IMAGE, data=str(img)))
        url = part["image_url"]["url"]
        assert url.startswith("data:image/png;base64,")

    def test_metadata_forwarded(self):
        handler = ImageHandler()
        part = handler.handle(
            ModalityInput(
                modality=ModalityType.IMAGE,
                data="https://example.com/x.png",
                metadata={"detail": "high"},
            )
        )
        assert part["image_url"]["detail"] == "high"

    def test_invalid_type_raises(self):
        handler = ImageHandler()
        with pytest.raises(TypeError):
            handler.handle(ModalityInput(modality=ModalityType.IMAGE, data=12345))


class TestAudioHandler:
    def test_bytes_inlined(self):
        handler = AudioHandler()
        raw = b"RIFF audio data"
        part = handler.handle(
            ModalityInput(modality=ModalityType.AUDIO, data=raw, mime_type="audio/wav")
        )
        assert part["type"] == "input_audio"
        assert part["input_audio"]["format"] == "wav"
        assert base64.b64decode(part["input_audio"]["data"]) == raw

    def test_url_passthrough(self):
        handler = AudioHandler()
        part = handler.handle(
            ModalityInput(
                modality=ModalityType.AUDIO, data="https://example.com/a.mp3"
            )
        )
        assert part["input_audio"]["url"] == "https://example.com/a.mp3"

    def test_file_path(self, tmp_path):
        handler = AudioHandler()
        audio = tmp_path / "clip.wav"
        audio.write_bytes(b"audio bytes")
        part = handler.handle(ModalityInput(modality=ModalityType.AUDIO, data=str(audio)))
        assert part["type"] == "input_audio"
        assert "data" in part["input_audio"]


class TestVideoHandler:
    def test_url_passthrough(self):
        handler = VideoHandler()
        url = "https://example.com/v.mp4"
        part = handler.handle(ModalityInput(modality=ModalityType.VIDEO, data=url))
        assert part == {"type": "video_url", "video_url": {"url": url}}

    def test_bytes_inlined(self):
        handler = VideoHandler()
        raw = b"video data"
        part = handler.handle(
            ModalityInput(modality=ModalityType.VIDEO, data=raw, mime_type="video/mp4")
        )
        url = part["video_url"]["url"]
        assert url.startswith("data:video/mp4;base64,")

    def test_oversized_bytes_raises(self):
        handler = VideoHandler()
        # Bypass the inline limit by monkeypatching the module constant.
        import maop.core.multimodal.modality_handlers as mh

        original = mh._MAX_INLINE_BYTES
        mh._MAX_INLINE_BYTES = 10
        try:
            with pytest.raises(ValueError, match="exceeds inline limit"):
                handler.handle(
                    ModalityInput(
                        modality=ModalityType.VIDEO, data=b"x" * 100, mime_type="video/mp4"
                    )
                )
        finally:
            mh._MAX_INLINE_BYTES = original


class TestModalityHandlerRegistry:
    def test_default_handlers(self):
        reg = ModalityHandlerRegistry()
        assert set(reg.supported_modalities()) == {
            ModalityType.TEXT,
            ModalityType.IMAGE,
            ModalityType.AUDIO,
            ModalityType.VIDEO,
        }

    def test_handle_text(self):
        reg = ModalityHandlerRegistry()
        part = reg.handle(ModalityInput(modality=ModalityType.TEXT, data="hi"))
        assert part == {"type": "text", "text": "hi"}

    def test_custom_handler_registration(self):
        reg = ModalityHandlerRegistry()

        class _CustomHandler(BaseModalityHandler):
            modality = ModalityType.TEXT

            def handle(self, inp: ModalityInput) -> dict[str, Any]:
                return {"type": "text", "text": f"custom:{inp.data}"}

        reg.register(ModalityType.TEXT, _CustomHandler())
        part = reg.handle(ModalityInput(modality=ModalityType.TEXT, data="x"))
        assert part["text"] == "custom:x"

    def test_unknown_modality_raises(self):
        reg = ModalityHandlerRegistry()
        # Construct a ModalityInput with a modality not in the registry.
        inp = ModalityInput(modality=ModalityType.TEXT, data="x")
        reg._handlers.clear()  # type: ignore[attr-defined]
        with pytest.raises(KeyError):
            reg.handle(inp)


# ── TestUnifiedModelInterface ─────────────────────────────────


class TestUnifiedModelInterface:
    def test_single_text_collapses_to_string(self):
        provider = _MockProvider()
        ui = UnifiedModelInterface(provider)
        req = MultimodalRequest(
            inputs=[ModalityInput(modality=ModalityType.TEXT, data="hello")],
            model="m",
        )
        messages = ui.build_messages(req)
        assert len(messages) == 1
        assert messages[0] == {"role": "user", "content": "hello"}

    def test_multimodal_assembles_parts(self):
        provider = _MockProvider()
        ui = UnifiedModelInterface(provider)
        req = MultimodalRequest(
            inputs=[
                ModalityInput(modality=ModalityType.IMAGE, data="https://x.com/a.png"),
                ModalityInput(modality=ModalityType.TEXT, data="describe this"),
            ],
            model="gpt-4o",
        )
        messages = ui.build_messages(req)
        assert len(messages) == 1
        content = messages[0]["content"]
        assert isinstance(content, list)
        assert content[0]["type"] == "image_url"
        assert content[1] == {"type": "text", "text": "describe this"}

    def test_context_messages_prepended(self):
        provider = _MockProvider()
        ui = UnifiedModelInterface(provider)
        req = MultimodalRequest(
            inputs=[ModalityInput(modality=ModalityType.TEXT, data="hi")],
            context_messages=[{"role": "system", "content": "be nice"}],
        )
        messages = ui.build_messages(req)
        assert messages[0] == {"role": "system", "content": "be nice"}
        assert messages[1] == {"role": "user", "content": "hi"}

    async def test_invoke_delegates_to_provider(self):
        provider = _MockProvider()
        ui = UnifiedModelInterface(provider)
        req = MultimodalRequest(
            inputs=[ModalityInput(modality=ModalityType.TEXT, data="hello")],
            model="test-model",
        )
        resp = await ui.invoke(req)
        assert isinstance(resp, MultimodalResponse)
        assert resp.content == "mock response"
        assert resp.used_model == "test-model"
        assert resp.input_modalities == ["text"]
        assert resp.provider == "mock"
        provider.chat.assert_awaited_once()

    async def test_invoke_with_image(self):
        provider = _MockProvider()
        ui = UnifiedModelInterface(provider)
        req = MultimodalRequest(
            inputs=[
                ModalityInput(modality=ModalityType.IMAGE, data="https://x.com/a.png"),
                ModalityInput(modality=ModalityType.TEXT, data="what is this?"),
            ],
            model="gpt-4o",
        )
        resp = await ui.invoke(req)
        assert resp.input_modalities == ["image", "text"]
        # Verify the provider received a list content (not collapsed string).
        sent_messages = provider._last_messages
        assert sent_messages is not None
        assert isinstance(sent_messages[-1]["content"], list)

    async def test_invoke_stream(self):
        provider = _MockProvider()
        ui = UnifiedModelInterface(provider)
        req = MultimodalRequest(
            inputs=[ModalityInput(modality=ModalityType.TEXT, data="hi")],
        )
        tokens = [t async for t in ui.invoke_stream(req)]
        assert tokens == ["mock", " ", "stream"]

    def test_from_text_helper(self):
        provider = _MockProvider()
        ui, req = UnifiedModelInterface.from_text(provider, "hello", model="m")
        assert isinstance(ui, UnifiedModelInterface)
        assert req.inputs[0].data == "hello"
        assert req.model == "m"

    def test_from_inputs_helper(self):
        provider = _MockProvider()
        inputs = [
            ModalityInput(modality=ModalityType.TEXT, data="a"),
            ModalityInput(modality=ModalityType.IMAGE, data="https://x.com/a.png"),
        ]
        _ui, req = UnifiedModelInterface.from_inputs(provider, inputs, model="m")
        assert len(req.inputs) == 2

    def test_supported_modalities(self):
        provider = _MockProvider()
        ui = UnifiedModelInterface(provider)
        assert ModalityType.TEXT in ui.supported_modalities

    def test_empty_inputs(self):
        provider = _MockProvider()
        ui = UnifiedModelInterface(provider)
        req = MultimodalRequest(inputs=[], model="m")
        messages = ui.build_messages(req)
        assert messages == [{"role": "user", "content": ""}]


# ── TestModelRouter ───────────────────────────────────────────


class TestModelRouter:
    def _make_router(self) -> ModelRouter:
        router = ModelRouter()
        router.register(ModelCapability(
            name="cheap-text",
            modalities={ModalityType.TEXT},
            tasks={TaskType.TEXT_GENERATION},
            cost_per_1k_input=0.1, cost_per_1k_output=0.2,
            avg_latency_ms=200, quality_tier=3.0,
        ))
        router.register(ModelCapability(
            name="vision-pro",
            modalities={ModalityType.TEXT, ModalityType.IMAGE},
            tasks={TaskType.IMAGE_UNDERSTANDING, TaskType.TEXT_GENERATION},
            cost_per_1k_input=2.5, cost_per_1k_output=10.0,
            avg_latency_ms=800, quality_tier=10.0,
        ))
        router.register(ModelCapability(
            name="vision-budget",
            modalities={ModalityType.TEXT, ModalityType.IMAGE},
            tasks={TaskType.IMAGE_UNDERSTANDING},
            cost_per_1k_input=0.5, cost_per_1k_output=2.0,
            avg_latency_ms=500, quality_tier=1.0,
        ))
        return router

    def test_text_only_selects_cheap(self):
        router = self._make_router()
        best, ranked = router.route(RoutingCriteria(
            task_type=TaskType.TEXT_GENERATION,
            modalities={ModalityType.TEXT},
        ))
        assert best is not None
        # cheap-text is the only one supporting text_generation at low cost
        assert best.model == "cheap-text"
        # vision-pro also supports TEXT_GENERATION, so 2 candidates survive.
        assert len(ranked) == 2

    def test_image_task_filters_by_modality(self):
        router = self._make_router()
        best, ranked = router.route(RoutingCriteria(
            task_type=TaskType.IMAGE_UNDERSTANDING,
            modalities={ModalityType.IMAGE, ModalityType.TEXT},
        ))
        assert best is not None
        # cheap-text is disqualified (no IMAGE modality)
        names = [r.model for r in ranked]
        assert "cheap-text" not in names
        assert "vision-pro" in names
        assert "vision-budget" in names

    def test_cost_constraint_filters(self):
        router = self._make_router()
        best, ranked = router.route(RoutingCriteria(
            task_type=TaskType.IMAGE_UNDERSTANDING,
            modalities={ModalityType.IMAGE, ModalityType.TEXT},
            max_cost_per_1k=3.0,  # vision-budget (2.5) passes, vision-pro (12.5) fails
        ))
        names = [r.model for r in ranked]
        assert "vision-budget" in names
        assert "vision-pro" not in names
        assert best.model == "vision-budget"

    def test_latency_constraint_filters(self):
        router = self._make_router()
        _best, ranked = router.route(RoutingCriteria(
            task_type=TaskType.IMAGE_UNDERSTANDING,
            modalities={ModalityType.IMAGE, ModalityType.TEXT},
            max_latency_ms=600,
        ))
        names = [r.model for r in ranked]
        assert "vision-budget" in names  # 500ms < 600
        assert "vision-pro" not in names  # 800ms > 600

    def test_no_candidates_returns_none(self):
        router = self._make_router()
        best, ranked = router.route(RoutingCriteria(
            task_type=TaskType.VIDEO_ANALYSIS,
            modalities={ModalityType.VIDEO},
        ))
        assert best is None
        assert ranked == []

    def test_disabled_model_excluded(self):
        router = self._make_router()
        cap = router.get("cheap-text")
        assert cap is not None
        cap.enabled = False
        _best, ranked = router.route(RoutingCriteria(
            task_type=TaskType.TEXT_GENERATION,
            modalities={ModalityType.TEXT},
        ))
        names = [r.model for r in ranked]
        assert "cheap-text" not in names

    def test_prefer_quality_favors_high_tier(self):
        router = self._make_router()
        best, _ = router.route(RoutingCriteria(
            task_type=TaskType.IMAGE_UNDERSTANDING,
            modalities={ModalityType.IMAGE, ModalityType.TEXT},
            prefer_quality=True,
        ))
        # vision-pro has quality_tier 9.0 vs vision-budget 6.0
        assert best.model == "vision-pro"

    def test_prefer_speed_favors_low_latency(self):
        router = self._make_router()
        best, _ = router.route(RoutingCriteria(
            task_type=TaskType.IMAGE_UNDERSTANDING,
            modalities={ModalityType.IMAGE, ModalityType.TEXT},
            prefer_speed=True,
        ))
        # vision-budget has 500ms vs vision-pro 800ms
        assert best.model == "vision-budget"

    def test_ranked_results_sorted_descending(self):
        router = self._make_router()
        _, ranked = router.route(RoutingCriteria(
            task_type=TaskType.IMAGE_UNDERSTANDING,
            modalities={ModalityType.IMAGE, ModalityType.TEXT},
        ))
        scores = [r.score for r in ranked]
        assert scores == sorted(scores, reverse=True)

    def test_route_model_name_convenience(self):
        router = self._make_router()
        name = router.route_model_name(RoutingCriteria(
            task_type=TaskType.TEXT_GENERATION,
            modalities={ModalityType.TEXT},
        ))
        assert name is not None
        assert isinstance(name, str)

    def test_unregister(self):
        router = self._make_router()
        assert router.unregister("cheap-text") is True
        assert router.unregister("nonexistent") is False
        assert router.get("cheap-text") is None

    def test_multimodal_task_matches(self):
        """A model advertising MULTIMODAL matches any task type."""
        router = ModelRouter()
        router.register(ModelCapability(
            name="omni",
            modalities={ModalityType.TEXT, ModalityType.IMAGE, ModalityType.AUDIO},
            tasks={TaskType.MULTIMODAL},
            cost_per_1k_input=1.0, cost_per_1k_output=1.0,
            avg_latency_ms=300, quality_tier=8.0,
        ))
        best, _ = router.route(RoutingCriteria(
            task_type=TaskType.AUDIO_TRANSCRIPTION,  # not in tasks, but MULTIMODAL is
            modalities={ModalityType.AUDIO},
        ))
        assert best is not None
        assert best.model == "omni"

    def test_route_result_dataclass(self):
        router = self._make_router()
        best, _ = router.route(RoutingCriteria(
            task_type=TaskType.TEXT_GENERATION,
            modalities={ModalityType.TEXT},
        ))
        assert isinstance(best, RouteResult)
        assert hasattr(best, "score")
        assert hasattr(best, "capability")