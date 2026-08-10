"""MAOP Multimodal Modality Handlers — per-modality input normalization.

Each handler converts a raw modality input (text string, image file, audio
bytes, video URL, …) into the canonical OpenAI-style content-part dict that
``UnifiedModelInterface`` assembles into a ``messages`` array.  Keeping the
conversion logic per-modality (rather than a giant if/elif chain in the
unified interface) makes it trivial to add a new modality: implement
``BaseModalityHandler`` and register it in ``ModalityHandlerRegistry``.

Supported modalities:
  - **text**: plain string → ``{"type": "text", "text": ...}``
  - **image**: file path / URL / raw bytes → ``{"type": "image_url", ...}``
  - **audio**: file path / URL / raw bytes → ``{"type": "input_audio", ...}``
  - **video**: file path / URL → ``{"type": "video_url", ...}``

Image / audio bytes are base64-encoded into a data-URL so the payload is
self-contained and works with any OpenAI-compatible endpoint that accepts
inline multimodal content.

Usage::

    from maop.core.multimodal.modality_handlers import (
        ModalityHandlerRegistry, ModalityType, ModalityInput,
    )

    registry = ModalityHandlerRegistry()
    part = registry.handle(ModalityInput(modality=ModalityType.TEXT, data="hello"))
    # → {"type": "text", "text": "hello"}
"""

from __future__ import annotations

import base64
import logging
import mimetypes
from enum import Enum
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

# ── Defaults ────────────────────────────────────────────────────

_DEFAULT_MIME: dict[str, str] = {
    "image": "image/png",
    "audio": "audio/wav",
    "video": "video/mp4",
}

# Max inline payload size (bytes).  Inputs larger than this are kept as a
# file-path reference instead of being base64-inlined, to avoid blowing up
# the request body / context window.  20 MiB is a conservative ceiling that
# covers most inline-image use-cases while preventing accidental OOM.
_MAX_INLINE_BYTES = 20 * 1024 * 1024


# ── Enums & data models ────────────────────────────────────────


class ModalityType(str, Enum):
    """Supported input modalities for multimodal inference."""

    TEXT = "text"
    IMAGE = "image"
    AUDIO = "audio"
    VIDEO = "video"


class ModalityInput(BaseModel):
    """A single modality input to be normalized into a content part.

    ``data`` may be:
      - ``str``: for text (raw text), or a file path / URL for binary modalities
      - ``bytes``: raw binary payload (image/audio); will be base64-inlined

    ``mime_type`` is optional; when empty the handler infers it from the file
    extension (for paths) or falls back to a sensible default per modality.
    """

    modality: ModalityType
    data: Any = None
    mime_type: str = ""
    # Optional metadata forwarded into the content part (e.g. ``detail`` for
    # OpenAI vision images, ``format`` for audio).
    metadata: dict[str, Any] = Field(default_factory=dict)


# ── Helpers ────────────────────────────────────────────────────


def _guess_mime(path: str | Path, fallback: str) -> str:
    """Infer MIME type from a file path; fall back to *fallback*."""
    guessed, _ = mimetypes.guess_type(str(path))
    return guessed or fallback


def _is_url(value: str) -> bool:
    return value.startswith(("http://", "https://", "data:"))


def _encode_bytes(data: bytes, mime_type: str) -> str:
    """Base64-encode raw bytes into a ``data:{mime_type};base64,...`` URL."""
    b64 = base64.b64encode(data).decode("ascii")
    return f"data:{mime_type};base64,{b64}"


def _read_file_bytes(path: str | Path) -> bytes:
    return Path(path).read_bytes()


# ── Handlers ───────────────────────────────────────────────────


class BaseModalityHandler:
    """Abstract base for per-modality normalization.

    Subclasses implement :meth:`handle` which receives a
    :class:`ModalityInput` and returns an OpenAI-compatible content-part
    dict.
    """

    modality: ModalityType  # set by subclass

    def handle(self, inp: ModalityInput) -> dict[str, Any]:
        raise NotImplementedError


class TextHandler(BaseModalityHandler):
    """Normalize plain text into ``{"type": "text", "text": ...}``."""

    modality = ModalityType.TEXT

    def handle(self, inp: ModalityInput) -> dict[str, Any]:
        text = inp.data
        if text is None:
            text = ""
        if not isinstance(text, str):
            text = str(text)
        return {"type": "text", "text": text}


class ImageHandler(BaseModalityHandler):
    """Normalize an image into ``{"type": "image_url", "image_url": {...}}``.

    Accepts a URL (passed through), a file path (read + base64-inlined if
    small, else referenced by path), or raw ``bytes`` (always inlined).
    """

    modality = ModalityType.IMAGE

    def handle(self, inp: ModalityInput) -> dict[str, Any]:
        data = inp.data
        mime = inp.mime_type or _DEFAULT_MIME["image"]

        if isinstance(data, bytes):
            url = _encode_bytes(data, mime)
        elif isinstance(data, str):
            if _is_url(data):
                url = data
            else:
                # File path
                mime = inp.mime_type or _guess_mime(data, _DEFAULT_MIME["image"])
                size = Path(data).stat().st_size if Path(data).exists() else 0
                if size > 0 and size <= _MAX_INLINE_BYTES:
                    url = _encode_bytes(_read_file_bytes(data), mime)
                else:
                    # Too large to inline — reference by file path.
                    url = str(data)
                    logger.debug(
                        "Image %s (%d bytes) exceeds inline limit; referenced by path",
                        data, size,
                    )
        else:
            raise TypeError(
                f"ImageHandler expects str|bytes, got {type(data).__name__}"
            )

        part: dict[str, Any] = {"type": "image_url", "image_url": {"url": url}}
        # Forward optional metadata (e.g. OpenAI vision ``detail``).
        if inp.metadata:
            part["image_url"].update(inp.metadata)
        return part


class AudioHandler(BaseModalityHandler):
    """Normalize audio into ``{"type": "input_audio", "input_audio": {...}}``.

    Mirrors OpenAI's audio-in format: ``{"data": <b64>, "format": <fmt>}``.
    """

    modality = ModalityType.AUDIO

    def handle(self, inp: ModalityInput) -> dict[str, Any]:
        data = inp.data
        mime = inp.mime_type or _DEFAULT_MIME["audio"]
        # Derive a short format token (e.g. "wav" from "audio/wav").
        fmt = inp.metadata.get("format") or mime.split("/")[-1]

        if isinstance(data, bytes):
            b64 = base64.b64encode(data).decode("ascii")
        elif isinstance(data, str):
            if _is_url(data):
                # URL-based audio — some providers accept a URL directly.
                return {
                    "type": "input_audio",
                    "input_audio": {"url": data, "format": fmt},
                }
            mime = inp.mime_type or _guess_mime(data, _DEFAULT_MIME["audio"])
            fmt = inp.metadata.get("format") or mime.split("/")[-1]
            b64 = base64.b64encode(_read_file_bytes(data)).decode("ascii")
        else:
            raise TypeError(
                f"AudioHandler expects str|bytes, got {type(data).__name__}"
            )

        return {"type": "input_audio", "input_audio": {"data": b64, "format": fmt}}


class VideoHandler(BaseModalityHandler):
    """Normalize video into ``{"type": "video_url", "video_url": {...}}``.

    Video payloads are typically too large to inline, so URLs / file paths
    are passed through as references.  Raw bytes are base64-inlined only if
    under the inline ceiling.
    """

    modality = ModalityType.VIDEO

    def handle(self, inp: ModalityInput) -> dict[str, Any]:
        data = inp.data
        mime = inp.mime_type or _DEFAULT_MIME["video"]

        if isinstance(data, bytes):
            if len(data) <= _MAX_INLINE_BYTES:
                url = _encode_bytes(data, mime)
            else:
                raise ValueError(
                    f"Video payload ({len(data)} bytes) exceeds inline limit "
                    f"({_MAX_INLINE_BYTES} bytes); provide a URL or file path instead"
                )
        elif isinstance(data, str):
            if _is_url(data):
                url = data
            else:
                mime = inp.mime_type or _guess_mime(data, _DEFAULT_MIME["video"])
                size = Path(data).stat().st_size if Path(data).exists() else 0
                if 0 < size <= _MAX_INLINE_BYTES:
                    url = _encode_bytes(_read_file_bytes(data), mime)
                else:
                    url = str(data)
        else:
            raise TypeError(
                f"VideoHandler expects str|bytes, got {type(data).__name__}"
            )

        return {"type": "video_url", "video_url": {"url": url}}


# ── Registry ───────────────────────────────────────────────────


class ModalityHandlerRegistry:
    """Maps :class:`ModalityType` → handler instance.

    A single registry instance is cheap to construct and thread-safe
    (handlers are stateless).  ``UnifiedModelInterface`` holds one
    internally; callers can also instantiate it directly for ad-hoc
    normalization.
    """

    def __init__(self) -> None:
        self._handlers: dict[ModalityType, BaseModalityHandler] = {
            ModalityType.TEXT: TextHandler(),
            ModalityType.IMAGE: ImageHandler(),
            ModalityType.AUDIO: AudioHandler(),
            ModalityType.VIDEO: VideoHandler(),
        }

    def register(self, modality: ModalityType, handler: BaseModalityHandler) -> None:
        """Add or replace the handler for *modality*."""
        self._handlers[modality] = handler

    def get(self, modality: ModalityType) -> BaseModalityHandler:
        handler = self._handlers.get(modality)
        if handler is None:
            raise KeyError(f"No handler registered for modality {modality!r}")
        return handler

    def handle(self, inp: ModalityInput) -> dict[str, Any]:
        """Normalize *inp* into an OpenAI-compatible content-part dict."""
        return self.get(inp.modality).handle(inp)

    def supported_modalities(self) -> list[ModalityType]:
        return list(self._handlers.keys())