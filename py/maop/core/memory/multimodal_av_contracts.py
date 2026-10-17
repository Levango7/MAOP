"""Audio / Video embedding provider contracts (M2.2 B3 — extension surface).

Why this exists
---------------
ADR-016 D5 drew a hard boundary: the *implementations* of audio/video
providers (Whisper, CLAP, ffmpeg) live in the MAOS enterprise repo, not
here. But the **contract** — the shape a provider must have to plug into
:mod:`maop.core.memory.multimodal_spaces` and
:class:`~maop.memory.multimodal.MultimodalMemory` — belongs to the open
core, or the two sides cannot agree on what "a 768-dim audio space" means.

This module defines that contract:

* :class:`AudioEmbeddingProvider` — the shape any audio embedder must
  satisfy (embed from a file path or raw PCM bytes). The enterprise repo
  ships ``WhisperAudioProvider`` and ``ClapAudioProvider`` conforming to
  this; without one, the audio space degrades to
  :class:`~maop.core.memory.multimodal_embed.HashModalityEmbedding`
  (non-semantic, ``stats()["degraded"] == True``).
* :class:`VideoEmbeddingProvider` — the shape any video embedder must
  satisfy (sample ``n`` frames uniformly and pool). The enterprise repo
  ships ``FramePoolVideoProvider`` (ffmpeg + CLIP); without one, the
  video space degrades to the hash fallback.

Additionally :func:`register_builtin_av_spaces** pre-registers an audio-768
and a video-512 space with the multimodal space registry. The personal
edition registers them so capacity probes report "audio-768 registered but
no provider" rather than "unknown space" — the difference matters for the
admin dashboard's capability probe and for the enterprise edition's ``is
audio available here?`` check.

Scope boundary (ADR-016 D5)
---------------------------
No Whisper / CLAP / ffmpeg code belongs here. This module is pure
protocol + a registrator — the skeleton, not the muscles.
"""

from __future__ import annotations

import logging
from abc import abstractmethod
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from maop.core.memory.multimodal_embed import (
    MULTIMODAL_DIM,
    MultimodalEmbeddingProvider,
)
from maop.core.memory.multimodal_spaces import (
    MultimodalSpaceSpec,
    get_multimodal_space,
    register_multimodal_space,
)

logger = logging.getLogger(__name__)

__all__ = [
    "AudioEmbeddingProvider",
    "MODALITY_AUDIO",
    "MODALITY_VIDEO",
    "VideoEmbeddingProvider",
    "get_audio_space",
    "get_video_space",
    "register_builtin_av_spaces",
]

MODALITY_AUDIO = "audio"
MODALITY_VIDEO = "video"

#: Default dimension for the registered audio space. Matches Whisper's
#: encoder output width (768-dim for ``small`` / ``medium`` checkpoints).
AUDIO_SPACE_DIM = 768
AUDIO_SPACE_NAME = f"audio-{AUDIO_SPACE_DIM}"

#: Default dimension for the registered video space. Video embedders in
#: MAOS pool CLIP frame embeddings, so 512 (CLIP-B/32's dim) is the default.
VIDEO_SPACE_DIM = MULTIMODAL_DIM
VIDEO_SPACE_NAME = f"video-{VIDEO_SPACE_DIM}"


# ── Provider protocols ──────────────────────────────────────────────


@runtime_checkable
class AudioEmbeddingProvider(Protocol):
    """The shape an audio embedder must satisfy (MAOS contracts).

    Any Whisper- / CLAP- or wav2vec-based implementer in the enterprise
    repo conforms to this and is accepted by ``MultimodalSpaceSpec`` +
    ``MultimodalMemory`` without further wiring.
    """

    @property
    @abstractmethod
    def provider_id(self) -> str: ...

    @property
    @abstractmethod
    def dimension(self) -> int: ...

    @property
    def is_semantic(self) -> bool:
        """Audio providers that produce semantic embeddings return True."""
        return True

    @abstractmethod
    def embed_audio(self, source: str | Path | bytes, *, sr: int = 16_000) -> list[float]:
        """Embed one audio clip (path or raw PCM).

        Implementers handle resampling to ``sr`` internally — the core
        neverKnows the model's native sample rate.
        """

    def embed(self, source: str | Path | bytes) -> list[float]:
        """Single-text-entry alias (MultimodalEmbeddingProvider compat)."""
        if isinstance(source, str) and not Path(source).suffix:
            # Text-source fallthrough: not every audio provider knows text,
            # so Protocol-level default degrades to a zero vector of the
            # right dimension — never raise, never return the wrong width.
            return [0.0] * self.dimension
        return self.embed_audio(source)


@runtime_checkable
class VideoEmbeddingProvider(Protocol):
    """The shape a video embedder must satisfy (MAOS contracts).

    Any ffmpeg- + CLIP- or video-transformer-based implementer in the
    enterprise repo conforms to this.
    """

    @property
    @abstractmethod
    def provider_id(self) -> str: ...

    @property
    @abstractmethod
    def dimension(self) -> int: ...

    @property
    def is_semantic(self) -> bool:
        return True

    @abstractmethod
    def embed_video(
        self, source: str | Path, *, frames: int = 8, fps: float = 1.0,
    ) -> list[float]:
        """Embed a video clip by uniformly sampling *frames* and pooling."""

    def embed(self, source: str | Path | bytes) -> list[float]:
        if isinstance(source, (str, Path)):
            return self.embed_video(source)
        return [0.0] * self.dimension


# ── Space registration ──────────────────────────────────────────────


def _audio_space_spec() -> MultimodalSpaceSpec:
    """The built-in audio space — registered as capacity, not mandate."""
    return MultimodalSpaceSpec(
        name=AUDIO_SPACE_NAME,
        table_name="multimodal_entries_audio",
        dimension=AUDIO_SPACE_DIM,
        modalities=("audio",),
        backend="auto",
        model_id="openai/whisper-small",
        description=f"Audio embedding space (Whisper {AUDIO_SPACE_DIM}-dim, "
        f"provider injected by MAOS or degrades to hash fallback)",
    )


def _video_space_spec() -> MultimodalSpaceSpec:
    """The built-in video space — registered as capacity, not mandate."""
    return MultimodalSpaceSpec(
        name=VIDEO_SPACE_NAME,
        table_name="multimodal_entries_video",
        dimension=VIDEO_SPACE_DIM,
        modalities=("video",),
        backend="auto",
        model_id="clip-video-frame-pool-512",
        description=f"Video embedding space (CLIP {VIDEO_SPACE_DIM}-dim frame pool, "
        f"provider injected by MAOS or degrades to hash fallback)",
    )


def register_builtin_av_spaces() -> tuple[MultimodalSpaceSpec, MultimodalSpaceSpec]:
    """Register audio + video spaces with the multimodal registry.

    Idempotent: re-registering the same definition is a no-op (the space
    module enforces this). Returns the two specs.

    Safe to call at import time in the personal edition — both spaces
    resolve to the hash fallback until an enterprise provider is injected,
    so the registration is *declarative* (capacity signaling), not a claim
    that audio/video actually works.
    """
    audio = register_multimodal_space(_audio_space_spec())
    video = register_multimodal_space(_video_space_spec())
    logger.debug(
        "[multimodal-av] registered audio=%s video=%s (degraded until a provider is injected)",
        audio.name, video.name,
    )
    return audio, video


def get_audio_space() -> MultimodalSpaceSpec:
    return get_multimodal_space(AUDIO_SPACE_NAME)


def get_video_space() -> MultimodalSpaceSpec:
    return get_multimodal_space(VIDEO_SPACE_NAME)
