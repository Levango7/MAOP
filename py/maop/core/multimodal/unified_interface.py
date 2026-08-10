"""MAOP Unified Model Interface — single entry point for multimodal inference.

``UnifiedModelInterface`` wraps a :class:`BaseLLMProvider` (from
``maop.core.llm_provider``) and accepts heterogeneous inputs — text, image,
audio, video — in any combination.  Each modality input is normalized by a
:class:`ModalityHandlerRegistry` handler into an OpenAI-compatible content
part; the parts are assembled into a single ``messages`` array and dispatched
to the underlying provider's ``chat`` / ``chat_stream`` methods.

This lets callers write::

    ui = UnifiedModelInterface(provider)
    resp = await ui.invoke(
        inputs=[
            ModalityInput(modality=ModalityType.IMAGE, data="cat.png"),
            ModalityInput(modality=ModalityType.TEXT, data="What animal is this?"),
        ],
        model="gpt-4o",
    )

without caring how each modality is encoded or which content-part schema the
endpoint expects.

Design notes:
  - The unified interface is **transport-agnostic**: it only builds the
    ``messages`` payload and delegates to the provider.  Streaming, retry,
    and auth all live in the provider layer.
  - Multimodal content is placed in a single ``user`` message (the OpenAI
    vision convention).  A preceding system / assistant context can be
    supplied via ``context_messages``.
"""

from __future__ import annotations

import logging
import time
from collections.abc import AsyncGenerator
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, Field

from maop.core.multimodal.modality_handlers import (
    ModalityHandlerRegistry,
    ModalityInput,
    ModalityType,
)

if TYPE_CHECKING:
    from maop.core.llm_provider import BaseLLMProvider, LLMResponse

logger = logging.getLogger(__name__)


# ── Data models ────────────────────────────────────────────────


class MultimodalRequest(BaseModel):
    """A fully-specified multimodal inference request."""

    inputs: list[ModalityInput] = Field(default_factory=list)
    model: str = ""
    temperature: float = 0.7
    max_tokens: int = 4096
    # Preceding context (system / assistant / prior user turns).
    context_messages: list[dict[str, Any]] = Field(default_factory=list)
    # Extra kwargs forwarded to the provider (e.g. ``tools``).
    extra: dict[str, Any] = Field(default_factory=dict)


class MultimodalResponse(BaseModel):
    """Result of a multimodal inference call.

    Wraps the underlying :class:`LLMResponse` and adds the list of
    modalities that were present in the request, so callers can audit
    which input types drove the output.
    """

    content: str = ""
    model: str = ""
    finish_reason: str = ""
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    latency_ms: int = 0
    provider: str = ""
    tool_calls: list[dict[str, Any]] = Field(default_factory=list)
    input_modalities: list[str] = Field(default_factory=list)
    used_model: str = ""


# ── Unified interface ─────────────────────────────────────────


class UnifiedModelInterface:
    """Single entry point for text / image / audio / video inference.

    Parameters
    ----------
    provider : BaseLLMProvider
        The underlying LLM provider (OpenAI-compatible, Anthropic, Ollama, …).
    handler_registry : ModalityHandlerRegistry | None
        Custom handler registry.  When ``None`` a default one with all four
        built-in handlers is used.
    """

    def __init__(
        self,
        provider: BaseLLMProvider,
        *,
        handler_registry: ModalityHandlerRegistry | None = None,
    ) -> None:
        self._provider = provider
        self._handlers = handler_registry or ModalityHandlerRegistry()

    # ── properties ────────────────────────────────────────────

    @property
    def provider(self) -> BaseLLMProvider:
        return self._provider

    @property
    def supported_modalities(self) -> list[ModalityType]:
        return self._handlers.supported_modalities()

    # ── message assembly ──────────────────────────────────────

    def build_messages(self, request: MultimodalRequest) -> list[dict[str, Any]]:
        """Assemble the ``messages`` array for the underlying provider.

        Context messages are placed first, then a single ``user`` message
        whose ``content`` is the list of normalized content parts.  When
        the request contains only a single text input, the content is
        collapsed to a plain string (the format every endpoint accepts).
        """
        messages: list[dict[str, Any]] = list(request.context_messages)

        parts: list[dict[str, Any]] = []
        for inp in request.inputs:
            parts.append(self._handlers.handle(inp))

        if not parts:
            # No inputs — emit an empty user turn so the payload is valid.
            messages.append({"role": "user", "content": ""})
            return messages

        # Optimization: single text-only input → plain string content.
        if len(parts) == 1 and parts[0].get("type") == "text":
            messages.append({"role": "user", "content": parts[0]["text"]})
        else:
            messages.append({"role": "user", "content": parts})

        return messages

    # ── inference ─────────────────────────────────────────────

    async def invoke(self, request: MultimodalRequest) -> MultimodalResponse:
        """Non-streaming multimodal inference.

        Builds the message payload, delegates to ``provider.chat``, and
        wraps the result in a :class:`MultimodalResponse` annotated with
        the input modalities.
        """
        messages = self.build_messages(request)
        modalities = [inp.modality.value for inp in request.inputs]

        start = time.perf_counter()
        llm_resp: LLMResponse = await self._provider.chat(
            messages,
            model=request.model,
            temperature=request.temperature,
            max_tokens=request.max_tokens,
            **request.extra,
        )
        elapsed_ms = int((time.perf_counter() - start) * 1000)

        return MultimodalResponse(
            content=llm_resp.content,
            model=llm_resp.model or request.model,
            finish_reason=llm_resp.finish_reason,
            prompt_tokens=llm_resp.prompt_tokens,
            completion_tokens=llm_resp.completion_tokens,
            total_tokens=llm_resp.total_tokens,
            latency_ms=llm_resp.latency_ms or elapsed_ms,
            provider=llm_resp.provider or self._provider.name,
            tool_calls=llm_resp.tool_calls,
            input_modalities=modalities,
            used_model=llm_resp.model or request.model,
        )

    async def invoke_stream(
        self, request: MultimodalRequest
    ) -> AsyncGenerator[str, None]:
        """Streaming multimodal inference — yields token strings.

        Delegates to ``provider.chat_stream``.  The message payload is
        built exactly as in :meth:`invoke`.
        """
        messages = self.build_messages(request)
        async for token in self._provider.chat_stream(
            messages,
            model=request.model,
            temperature=request.temperature,
            max_tokens=request.max_tokens,
            **request.extra,
        ):
            yield token

    # ── convenience constructors ──────────────────────────────

    @classmethod
    def from_text(
        cls,
        provider: BaseLLMProvider,
        text: str,
        *,
        model: str = "",
        temperature: float = 0.7,
        max_tokens: int = 4096,
        context_messages: list[dict[str, Any]] | None = None,
    ) -> tuple[UnifiedModelInterface, MultimodalRequest]:
        """Build an interface + request from a single text prompt.

        Returns a ``(interface, request)`` tuple so the caller can either
        ``await interface.invoke(request)`` or further mutate the request
        before dispatch.
        """
        ui = cls(provider)
        req = MultimodalRequest(
            inputs=[ModalityInput(modality=ModalityType.TEXT, data=text)],
            model=model,
            temperature=temperature,
            max_tokens=max_tokens,
            context_messages=context_messages or [],
        )
        return ui, req

    @classmethod
    def from_inputs(
        cls,
        provider: BaseLLMProvider,
        inputs: list[ModalityInput],
        *,
        model: str = "",
        temperature: float = 0.7,
        max_tokens: int = 4096,
        context_messages: list[dict[str, Any]] | None = None,
    ) -> tuple[UnifiedModelInterface, MultimodalRequest]:
        """Build an interface + request from a list of modality inputs."""
        ui = cls(provider)
        req = MultimodalRequest(
            inputs=inputs,
            model=model,
            temperature=temperature,
            max_tokens=max_tokens,
            context_messages=context_messages or [],
        )
        return ui, req