"""MAOP Multimodal Embeddings — image/text providers for F2-02 (M2.2 B1).

Extends the text-only :class:`~maop.core.memory.vector_embed.EmbeddingProvider`
ABC with an image side. CLIP is the reference model: its text and image
encoders project into **one shared 512-dim space**, which is what makes
cross-modal retrieval (text query → images) a plain vector search.

Providers
---------
- :class:`MultimodalEmbeddingProvider` — ABC: ``embed`` (text) + ``embed_image``.
- :class:`ClipEmbedding` — real CLIP via ``transformers`` (``[multimodal]`` extra).
- :class:`HashModalityEmbedding` — deterministic, dependency-free fallback.
  **Not semantic** (``is_semantic = False``); it keeps the pipeline runnable
  where torch is unavailable (e.g. CI) and is always reported as degraded.
- :class:`PrecomputedEmbedding` — serves *real* CLIP vectors frozen into a
  fixture file; lets CI assert genuine cross-modal ranking without torch.

Environment notes
-----------------
- Model weights are downloaded on first use into the HuggingFace cache
  (``HF_HOME`` / ``XDG_CACHE_HOME``), **not** into ``MAOP_DATA_DIR``;
  the CLIP ViT-B/32 checkpoint is ~600 MB.
- Without a proxy, set ``HF_ENDPOINT=https://hf-mirror.com`` (verified
  reachable on this machine; ``huggingface.co`` is not from CN networks).

Safety
------
All image inputs (bytes or path) pass through :func:`validate_image_input`:
extension whitelist and 20 MB cap mirroring
:mod:`maop.core.backends.image_store`, plus a Pillow ``verify()`` decode
check (guard against decode bombs) when Pillow is importable.
"""

from __future__ import annotations

import hashlib
import io
import json
import logging
import math
from abc import abstractmethod
from pathlib import Path
from typing import Any, cast

from maop.core.backends.image_store import ALLOWED_EXTENSIONS, MAX_IMAGE_SIZE
from maop.core.memory.vector_embed import EmbeddingProvider

logger = logging.getLogger(__name__)

__all__ = [
    "DEFAULT_CLIP_MODEL",
    "DEFAULT_CLIP_REVISION",
    "MULTIMODAL_DIM",
    "MULTIMODAL_IMAGE_EXTENSIONS",
    "ClipEmbedding",
    "HashModalityEmbedding",
    "ImageInput",
    "ImageValidationError",
    "MultimodalEmbeddingProvider",
    "PrecomputedEmbedding",
    "validate_image_input",
]

#: CLIP ViT-B/32 projection dimension — the single space text and images share.
MULTIMODAL_DIM = 512

DEFAULT_CLIP_MODEL = "openai/clip-vit-base-patch32"

#: Pinned upstream revision (40-hex commit) of ``DEFAULT_CLIP_MODEL``.
#: Pinning makes embeddings reproducible across upstream updates and is what
#: bandit B615 wants; the guard test ``test_clip_revision_is_pinned`` asserts
#: the 40-hex shape (bandit cannot check a non-literal), and
#: ``tests/fixtures/clip_gold.json`` records the same revision so the frozen
#: gold vectors provably come from this revision. Bumping it means: new
#: weights download (~600 MB unless cached) + regenerate the gold fixture.
DEFAULT_CLIP_REVISION = "3d74acf9a28c67741b2f4f2ea7635f0aaf6f0268"

#: Raster formats only — ``.svg`` is excluded even though ImageStore allows it
#: (Pillow cannot rasterise SVG; better to reject early than fail at decode).
MULTIMODAL_IMAGE_EXTENSIONS: frozenset[str] = frozenset(ALLOWED_EXTENSIONS - {".svg"})

#: A path, or raw image bytes.
ImageInput = str | Path | bytes


class ImageValidationError(ValueError):
    """Raised when an image input fails the whitelist/size/decode checks."""


def _format_to_ext(fmt: str | None) -> str:
    """Map a Pillow format name (e.g. ``"JPEG"``) to a file extension."""
    table = {
        "PNG": ".png",
        "JPEG": ".jpg",
        "GIF": ".gif",
        "WEBP": ".webp",
        "BMP": ".bmp",
    }
    return table.get((fmt or "").upper(), "")


def validate_image_input(
    src: ImageInput,
    *,
    ext: str | None = None,
    allowed_root: Path | str | None = None,
    max_bytes: int = MAX_IMAGE_SIZE,
) -> tuple[bytes, str]:
    """Validate an image input and return ``(data, extension)``.

    Checks, in order: source kind, extension whitelist, size cap, and (when
    Pillow is available) a real decode verification. ``ext`` supplies the
    format hint for raw-bytes input that cannot be sniffed (e.g. in
    installations without Pillow); path inputs always take the extension
    from the filename. ``allowed_root`` is an optional containment guard for
    callers wiring untrusted paths (HTTP uploads should go through
    :class:`~maop.core.backends.image_store.ImageStore`, which owns
    session-scoped path validation).
    """
    if isinstance(src, (bytes, bytearray)):
        data = bytes(src)
        ext = (ext or "").lower()
    else:
        path = Path(src)
        try:
            resolved = path.resolve(strict=True)
        except (OSError, RuntimeError) as exc:
            raise ImageValidationError(f"image path not readable: {path}") from exc
        if allowed_root is not None:
            root = Path(allowed_root).resolve()
            if not resolved.is_relative_to(root):
                raise ImageValidationError(
                    f"image path escapes allowed root: {resolved}"
                )
        if not resolved.is_file():
            raise ImageValidationError(f"image path is not a file: {resolved}")
        ext = resolved.suffix.lower()
        data = resolved.read_bytes()

    if len(data) == 0:
        raise ImageValidationError("empty image payload")
    if len(data) > max_bytes:
        raise ImageValidationError(
            f"image too large: {len(data)} bytes > {max_bytes} bytes cap"
        )

    if ext:
        if ext not in MULTIMODAL_IMAGE_EXTENSIONS:
            raise ImageValidationError(f"unsupported image extension: {ext}")
    else:
        # No extension hint (raw bytes): try the zero-dependency magic-byte
        # sniff first (works without Pillow — CI/dev installs have no
        # pillow in the default extra), then fall back to Pillow's decoder.
        sniffed = _sniff_magic_ext(data) or _sniff_image_ext(data)
        if sniffed:
            ext = sniffed
        else:
            raise ImageValidationError(
                "cannot determine image format from payload bytes "
                "(pass ext=... when neither the magic bytes nor Pillow "
                "can identify it)"
            )
    return data, ext


#: Magic signatures → extension, for the zero-dependency format sniff.
#: WEBP is handled separately (RIFF container + ``WEBP`` at offset 8) so
#: other RIFF payloads (WAV/AVI) are not mis-accepted as images.
_MAGIC_EXTENSIONS: tuple[tuple[bytes, str], ...] = (
    (b"\x89PNG\r\n\x1a\n", ".png"),
    (b"\xff\xd8\xff", ".jpg"),          # JPEG SOI
    (b"GIF87a", ".gif"),
    (b"GIF89a", ".gif"),
    (b"BM", ".bmp"),
)


def _sniff_magic_ext(data: bytes) -> str:
    """Identify the image format from magic bytes; ``""`` when unknown.

    Deliberately covers only the container magic bytes — no decode. That
    keeps it dependency-free (``PIL`` is optional, so CI and slim installs
    must still be able to accept raw image bytes), while a full decode
    verification remains available via :func:`_sniff_image_ext`.
    """
    for magic, ext in _MAGIC_EXTENSIONS:
        if data.startswith(magic):
            return ext
    if len(data) >= 12 and data.startswith(b"RIFF") and data[8:12] == b"WEBP":
        return ".webp"
    return ""


def _sniff_image_ext(data: bytes) -> str:
    """Identify the image format from content. Returns "" when unknown."""
    try:
        from PIL import Image  # type: ignore[import-not-found]

        with Image.open(io.BytesIO(data)) as img:
            ext = _format_to_ext(img.format)
        return ext
    except ImportError:
        logger.debug("Pillow unavailable; skipping image format sniffing")
        return ""
    except Exception as exc:
        raise ImageValidationError(f"image decode failed: {exc}") from exc


def _verify_decodable(data: bytes) -> None:
    """Best-effort decode verification (decode-bomb guard).

    Skips silently when Pillow is not installed — without Pillow nothing
    downstream can decode the image either, so the size cap is the only
    relevant guard in that installation.
    """
    try:
        from PIL import Image  # type: ignore[import-not-found]
    except ImportError:
        logger.debug("Pillow unavailable; skipping image verify()")
        return
    try:
        with Image.open(io.BytesIO(data)) as img:
            img.verify()
    except Exception as exc:
        raise ImageValidationError(f"image failed decode verification: {exc}") from exc


# ── Provider ABC ─────────────────────────────────────────────


class MultimodalEmbeddingProvider(EmbeddingProvider):
    """Text + image embedding provider sharing one vector space.

    Subclasses must set ``_dim`` (the shared dimension) and implement
    :meth:`embed_image`; :meth:`embed` comes from the parent ABC.
    """

    #: Space identity, written into every entry's metadata so a future model
    #: swap is diagnosable ("which model produced this vector?").
    model_id: str = "unknown"

    #: Which provider implementation served the vector (provenance —
    #: distinguishes live encoders from fixture/precomputed ones).
    provider_id: str = "unknown"

    #: False for deterministic non-semantic fallbacks (reported as degraded).
    is_semantic: bool = True

    @abstractmethod
    def embed_image(self, image: ImageInput, *, ext: str | None = None) -> list[float]:
        """Embed a single image (path or raw bytes) into the shared space.

        ``ext`` supplies the format hint for raw bytes when sniffing is not
        possible (installations without Pillow).
        """
        raise NotImplementedError

    def embed_image_batch(
        self,
        images: list[ImageInput],
        *,
        ext: str | None = None,
    ) -> list[list[float]]:
        """Embed multiple images.

        Base implementation is serial; providers that can batch (CLIP) MUST
        override this with a single forward pass — that amortisation is what
        brings per-image latency into the PRD budget (spike: 811 ms single
        vs 53 ms/image at batch=8). ``ext`` is a single shared hint for the
        whole batch (mixed-format batches should pass paths instead).
        """
        return [self.embed_image(img, ext=ext) for img in images]


# ── Hash fallback ────────────────────────────────────────────


class HashModalityEmbedding(MultimodalEmbeddingProvider):
    """Deterministic hash-based fallback — **not semantic** (``is_semantic=False``).

    Text is hashed from its UTF-8 bytes; images are hashed from their raw
    file bytes (never the pixels), so visually identical images with
    different encodings do not collide. This provider keeps the storage and
    fusion pipeline exercisable where torch is unavailable; relevance
    ranking is meaningless by construction, and every consumer can see that
    via ``is_semantic`` / ``stats()["degraded"]``.
    """

    model_id = "hash-modality-fallback"
    provider_id = "hash-fallback"
    is_semantic = False

    def __init__(self, dim: int = MULTIMODAL_DIM) -> None:
        self._dim = dim

    def embed(self, text: str) -> list[float]:
        return self._hash_bytes(text.encode("utf-8"))

    def embed_image(self, image: ImageInput, *, ext: str | None = None) -> list[float]:
        data, _ext = validate_image_input(image, ext=ext)
        return self._hash_bytes(data)

    def _hash_bytes(self, data: bytes) -> list[float]:
        digest = hashlib.sha256(data).digest()
        vec = [float(digest[i % len(digest)]) / 255.0 for i in range(self._dim)]
        norm = math.sqrt(sum(v * v for v in vec))
        if norm < 1e-10:
            return vec
        return [v / norm for v in vec]


# ── CLIP ─────────────────────────────────────────────────────


class ClipEmbedding(MultimodalEmbeddingProvider):
    """CLIP text+image encoders (``[multimodal]`` extra: transformers + torch + pillow).

    Parameters
    ----------
    model_name : str
        HuggingFace model id. Defaults to ``openai/clip-vit-base-patch32``
        (512-dim). See the ADR-022 license note before changing/deploying.
    device : str
        Torch device string; CPU is the supported default.
    """

    def __init__(
        self,
        model_name: str = DEFAULT_CLIP_MODEL,
        *,
        device: str = "cpu",
        revision: str = DEFAULT_CLIP_REVISION,
    ) -> None:
        try:
            import torch  # type: ignore[import-not-found]
            from transformers import (  # type: ignore[import-not-found]
                CLIPModel,
                CLIPProcessor,
            )
        except ImportError as exc:
            raise ImportError(
                "CLIP embedding requires the [multimodal] extra: "
                "pip install maop-orchestrator[multimodal] "
                "(transformers + torch + pillow). If weight download fails, "
                "set HF_ENDPOINT=https://hf-mirror.com."
            ) from exc

        self._torch = torch
        self._device = device
        self.model_id = model_name
        self.provider_id = "clip"
        self.is_semantic = True
        try:
            # transformers stubs type from_pretrained's first arg as
            # PreTrainedModel for some overloads; a model-id string is the
            # documented usage. revision pins the upstream commit (B615).
            self._model = CLIPModel.from_pretrained(
                model_name, revision=revision,
            ).to(device).eval()  # type: ignore[arg-type]
            self._processor = CLIPProcessor.from_pretrained(
                model_name, revision=revision,
            )  # type: ignore[arg-type]
        except Exception as exc:  # OSError, network errors, missing cache…
            raise RuntimeError(
                f"failed to load CLIP model {model_name!r} "
                f"(weights land in HF_HOME/XDG_CACHE_HOME; offline installs "
                f"must pre-seed the cache or set HF_ENDPOINT=https://hf-mirror.com): {exc}"
            ) from exc
        self._dim = int(getattr(self._model.config, "projection_dim", 0) or MULTIMODAL_DIM)

    # ── text side ────────────────────────────────────────────

    def embed(self, text: str) -> list[float]:
        return self.embed_batch([text])[0]

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        inputs = self._processor(
            text=list(texts), return_tensors="pt", padding=True, truncation=True,
        )
        inputs = {k: v.to(self._device) for k, v in inputs.items()}
        with self._torch.no_grad():
            out = self._model.get_text_features(**inputs)
        return self._to_vectors(out)

    # ── image side ───────────────────────────────────────────

    def embed_image(self, image: ImageInput, *, ext: str | None = None) -> list[float]:
        return self.embed_image_batch([image], ext=ext)[0]

    def embed_image_batch(
        self,
        images: list[ImageInput],
        *,
        ext: str | None = None,
    ) -> list[list[float]]:
        """Single forward pass for the whole batch (the PRD budget path)."""
        if not images:
            return []
        from PIL import Image  # type: ignore[import-not-found]

        pil_images = []
        for img in images:
            data, img_ext = validate_image_input(img, ext=ext)
            if img_ext != ".svg":
                _verify_decodable(data)
            with Image.open(io.BytesIO(data)) as opened:
                pil_images.append(opened.convert("RGB"))
        inputs = self._processor(images=pil_images, return_tensors="pt")
        inputs = {k: v.to(self._device) for k, v in inputs.items()}
        with self._torch.no_grad():
            out = self._model.get_image_features(**inputs)
        return self._to_vectors(out)

    # ── shared decoding ──────────────────────────────────────

    def _to_vectors(self, out: Any) -> list[list[float]]:
        """Normalise both transformers return shapes to ``list[list[float]]``.

        Newer transformers returns ``BaseModelOutputWithPooling`` whose
        ``pooler_output`` already holds the projected features; older
        versions return the tensor directly.
        """
        feats = out
        if hasattr(out, "pooler_output") and out.pooler_output is not None:
            feats = out.pooler_output
        if hasattr(feats, "detach"):
            arr = feats.detach().to(self._torch.float32).cpu().numpy()
        else:  # pragma: no cover - defensive: numpy-ish return
            arr = feats
        return [cast(list[float], [float(x) for x in row]) for row in arr]


# ── Precomputed (fixture) ────────────────────────────────────


class PrecomputedEmbedding(MultimodalEmbeddingProvider):
    """Serve **real** CLIP vectors frozen in a JSON fixture.

    Purpose: CI has no torch (ci.yml never installs ML extras), but the
    acceptance criterion wants a non-mock cross-modal ranking assertion.
    The fixture stores genuine CLIP outputs, so ranking assertions here test
    real model geometry — only the encoder call is replaced.

    Unknown inputs raise ``KeyError``: tests must cover their inputs
    explicitly, and the provider must never silently fall back to hashes.
    """

    provider_id = "clip-precomputed-fixture"
    is_semantic = True

    def __init__(
        self,
        texts: dict[str, list[float]],
        images: dict[str, list[float]],
        *,
        model_id: str | None = None,
    ) -> None:
        if not texts and not images:
            raise ValueError("PrecomputedEmbedding needs at least one vector")
        self._texts = texts
        # Keys are sha256 hex digests of the exact image bytes.
        self._images = images
        if model_id:
            self.model_id = model_id
        first = next(iter(texts.values()), None) or next(iter(images.values()))
        self._dim = len(first)

    @classmethod
    def from_json(cls, path: Path | str) -> PrecomputedEmbedding:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
        return cls(
            texts=dict(raw.get("texts", {})),
            images=dict(raw.get("images", {})),
            model_id=raw.get("model_id"),
        )

    def embed(self, text: str) -> list[float]:
        try:
            return self._texts[text]
        except KeyError:
            raise KeyError(
                f"text {text!r} not present in the precomputed fixture; "
                f"add it (or use a real provider)"
            ) from None

    def embed_image(self, image: ImageInput, *, ext: str | None = None) -> list[float]:
        data, _ext = validate_image_input(image, ext=ext)
        key = hashlib.sha256(data).hexdigest()
        try:
            return self._images[key]
        except KeyError:
            raise KeyError(
                f"image sha256 {key[:12]}… not present in the precomputed "
                f"fixture; add it (or use a real provider)"
            ) from None
