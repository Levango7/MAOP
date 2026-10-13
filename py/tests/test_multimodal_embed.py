"""F2-02 B1 — multimodal embedding providers and image input validation."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from maop.core.memory.multimodal_embed import (
    MULTIMODAL_DIM,
    ClipEmbedding,
    HashModalityEmbedding,
    ImageValidationError,
    PrecomputedEmbedding,
    validate_image_input,
)

FIXTURE_DIR = Path(__file__).parent / "fixtures"
IMAGE_DIR = FIXTURE_DIR / "multimodal"
GOLD = FIXTURE_DIR / "clip_gold.json"

PNG_MAGIC = b"\x89PNG\r\n\x1a\n"


def _png_bytes() -> bytes:
    """The same deterministic PNG the gold fixture was built from."""
    return (IMAGE_DIR / "red_square.png").read_bytes()


# ── fallback provider ───────────────────────────────────────


def test_hash_fallback_is_deterministic_and_marked_non_semantic():
    emb = HashModalityEmbedding()
    assert emb.dimension == MULTIMODAL_DIM
    assert emb.is_semantic is False
    a = emb.embed("hello world")
    b = emb.embed("hello world")
    assert a == b
    assert a != emb.embed("different text")
    assert len(a) == MULTIMODAL_DIM
    norm = sum(v * v for v in a) ** 0.5
    assert norm == pytest.approx(1.0, abs=1e-6)


def test_hash_fallback_image_vectors_differ_by_bytes(tmp_path):
    emb = HashModalityEmbedding()
    p1 = tmp_path / "a.png"
    p2 = tmp_path / "b.png"
    p1.write_bytes(PNG_MAGIC + b"one")
    p2.write_bytes(PNG_MAGIC + b"two")
    assert emb.embed_image(p1) != emb.embed_image(p2)


# ── image input validation ──────────────────────────────────


def test_validate_rejects_bad_extension(tmp_path):
    bad = tmp_path / "payload.exe"
    bad.write_bytes(b"MZ" + b"\x00" * 64)
    with pytest.raises(ImageValidationError, match="unsupported image extension"):
        validate_image_input(bad)


def test_validate_rejects_svg(tmp_path):
    """SVG passes ImageStore's whitelist but is excluded here (Pillow can't rasterise)."""
    svg = tmp_path / "vector.svg"
    svg.write_text("<svg xmlns='http://www.w3.org/2000/svg'/>")
    with pytest.raises(ImageValidationError, match="unsupported image extension"):
        validate_image_input(svg)


def test_validate_rejects_oversize_bytes():
    with pytest.raises(ImageValidationError, match="too large"):
        validate_image_input(PNG_MAGIC + b"\x00" * 32, ext=".png", max_bytes=16)


def test_validate_rejects_empty_payload():
    with pytest.raises(ImageValidationError, match="empty image payload"):
        validate_image_input(b"", ext=".png")


def test_validate_rejects_missing_path(tmp_path):
    with pytest.raises(ImageValidationError, match="not readable"):
        validate_image_input(tmp_path / "nope.png")


def test_validate_rejects_escape_from_allowed_root(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    outside = tmp_path / "outside.png"
    outside.write_bytes(PNG_MAGIC + b"x")
    with pytest.raises(ImageValidationError, match="escapes allowed root"):
        validate_image_input(outside, allowed_root=root)


def test_validate_accepts_path_and_bytes():
    data, ext = validate_image_input(IMAGE_DIR / "red_square.png")
    assert ext == ".png"
    assert data.startswith(PNG_MAGIC)
    data2, ext2 = validate_image_input(data, ext=".png")
    assert data2 == data and ext2 == ".png"


def test_validate_bytes_without_ext_uses_pillow_sniff():
    pytest.importorskip("PIL")
    _data, ext = validate_image_input(_png_bytes())
    assert ext == ".png"


def test_magic_sniff_works_without_pillow(monkeypatch):
    """No Pillow: raw bytes are still accepted via magic bytes (zero-dep).

    This is the CI/dev-install path — ``[multimodal]`` is not installed
    there, and image inputs must not require it. Before the magic-byte
    sniff existed this raised (2026-10-13 CI red on 5 platforms).
    """
    import builtins

    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name.startswith("PIL"):
            raise ImportError("no PIL")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    data, ext = validate_image_input(PNG_MAGIC + b"\x00" * 8)
    assert ext == ".png" and data.startswith(PNG_MAGIC)


def test_unknown_magic_bytes_still_rejected_without_pillow(monkeypatch):
    """Bytes in no known format still fail loudly (no silent accept)."""
    import builtins

    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name.startswith("PIL"):
            raise ImportError("no PIL")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    with pytest.raises(ImageValidationError, match="cannot determine image format"):
        validate_image_input(b"\x00\x01\x02\x03 definitely not an image")


def test_webp_requires_the_container_form_not_mere_riff():
    """A RIFF payload that is not WEBP must not be accepted as an image."""
    from maop.core.memory.multimodal_embed import _sniff_magic_ext

    assert _sniff_magic_ext(b"RIFF\x00\x00\x00\x00WAVEfmt ") == ""
    webp = b"RIFF" + b"\x00" * 4 + b"WEBP" + b"VP8 "
    assert _sniff_magic_ext(webp) == ".webp"


# ── precomputed provider (CI's real-CLIP stand-in) ──────────


def test_precomputed_serves_fixture_vectors_and_is_semantic():
    emb = PrecomputedEmbedding.from_json(GOLD)
    assert emb.is_semantic is True
    assert emb.dimension == 512
    vec = emb.embed("a red square")
    assert len(vec) == 512
    key = hashlib.sha256(_png_bytes()).hexdigest()
    assert emb.embed_image(_png_bytes(), ext=".png") == emb._images[key]


def test_precomputed_rejects_unknown_inputs():
    emb = PrecomputedEmbedding.from_json(GOLD)
    with pytest.raises(KeyError, match="not present in the precomputed fixture"):
        emb.embed("a text that is not in the fixture")
    with pytest.raises(KeyError, match="not present in the precomputed fixture"):
        emb.embed_image(PNG_MAGIC + b"unknown-bytes", ext=".png")


def test_gold_fixture_shape():
    raw = json.loads(GOLD.read_text(encoding="utf-8"))
    assert raw["dimension"] == 512
    assert raw["model_id"] == "openai/clip-vit-base-patch32"
    assert len(raw["texts"]) == 5
    assert len(raw["images"]) == 3
    for vec in list(raw["texts"].values()) + list(raw["images"].values()):
        assert len(vec) == 512


def test_clip_revision_is_pinned():
    """B615 guard: the pin must be a 40-hex commit, not a branch name.

    bandit only inspects literals, so a constant could silently become
    ``"main"`` and re-introduce non-reproducible weights; this asserts the
    shape, and the fixture records the same revision (gold vectors provably
    come from it).
    """
    import re

    from maop.core.memory.multimodal_embed import DEFAULT_CLIP_REVISION

    assert re.fullmatch(r"[0-9a-f]{40}", DEFAULT_CLIP_REVISION), (
        f"DEFAULT_CLIP_REVISION must be a 40-hex commit, got {DEFAULT_CLIP_REVISION!r}"
    )
    raw = json.loads(GOLD.read_text(encoding="utf-8"))
    assert raw.get("revision") == DEFAULT_CLIP_REVISION, (
        "gold fixture revision drifted from the pinned revision — regenerate the "
        "fixture (F:/tmp/gen_clip_fixture.py) when bumping the pin"
    )


# ── CLIP provider (local-only; CI has no torch) ─────────────


class _FakeTensor:
    """Minimal stand-in exposing the tensor ops ``_to_vectors`` uses."""

    def __init__(self, rows: list[list[float]]) -> None:
        self._rows = rows

    def detach(self) -> _FakeTensor:
        return self

    def to(self, _dtype: object) -> _FakeTensor:
        return self

    def cpu(self) -> _FakeTensor:
        return self

    def numpy(self) -> list[list[float]]:
        return self._rows


class _FakeOutput:
    """Mimics ``BaseModelOutputWithPooling`` (or a bare tensor when no pooler)."""

    def __init__(self, rows: list[list[float]], *, with_pooler: bool) -> None:
        if with_pooler:
            self.pooler_output = _FakeTensor(rows)
        else:
            self._rows = rows

    def detach(self) -> _FakeTensor:  # bare-tensor shape
        return _FakeTensor(self._rows)

    def to(self, _dtype: object) -> _FakeTensor:
        return _FakeTensor(self._rows)

    def cpu(self) -> _FakeTensor:
        return _FakeTensor(self._rows)

    def numpy(self) -> list[list[float]]:
        return self._rows


def _clip_shell() -> ClipEmbedding:
    """A ClipEmbedding instance without touching transformers/torch."""
    import types

    emb = ClipEmbedding.__new__(ClipEmbedding)
    emb._torch = types.SimpleNamespace(float32="float32")
    return emb


def test_clip_import_error_mentions_multimodal_extra(monkeypatch):
    """Without the extra the error must name the install path (no silent fallback)."""
    import builtins

    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name in ("transformers", "torch"):
            raise ImportError(f"no {name}")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    with pytest.raises(ImportError, match=r"\[multimodal\] extra"):
        ClipEmbedding()


def test_clip_to_vectors_handles_pooler_output_shape():
    """transformers 5.x returns BaseModelOutputWithPooling — pooler wins."""
    emb = _clip_shell()
    out = _FakeOutput([[0.1, 0.2, 0.3]], with_pooler=True)
    assert emb._to_vectors(out) == [[0.1, 0.2, 0.3]]


def test_clip_to_vectors_handles_bare_tensor_shape():
    """Older transformers returns the projected tensor directly."""
    emb = _clip_shell()
    out = _FakeOutput([[0.4, 0.5]], with_pooler=False)
    assert emb._to_vectors(out) == [[0.4, 0.5]]


def test_clip_empty_batches_short_circuit():
    """Empty batch calls must not touch the processor or the model."""
    emb = _clip_shell()
    assert emb.embed_batch([]) == []
    assert emb.embed_image_batch([]) == []


def _load_clip_or_skip():
    pytest.importorskip("transformers", reason="[multimodal] extra not installed")
    pytest.importorskip("torch", reason="[multimodal] extra not installed")
    try:
        return ClipEmbedding()
    except (ImportError, RuntimeError) as exc:  # offline cache missing, etc.
        pytest.skip(f"CLIP model unavailable in this environment: {exc}")


@pytest.mark.timeout(300)  # 模型加载 ~150s；并行全量跑时 60s 全局超时会误红
def test_clip_matches_gold_vectors():
    """Self-check against frozen real-CLIP vectors.

    Catches silent semantic drift (e.g. an unprojected pooler output that
    still happens to be 512-dim) that a dimension check cannot see.
    """
    emb = _load_clip_or_skip()
    gold = json.loads(GOLD.read_text(encoding="utf-8"))
    vec = emb.embed("a red square")
    ref = gold["texts"]["a red square"]
    cos = sum(a * b for a, b in zip(vec, ref)) / (
        (sum(a * a for a in vec) ** 0.5) * (sum(b * b for b in ref) ** 0.5)
    )
    assert cos > 0.99, f"CLIP output drifted from gold fixture (cos={cos:.4f})"


@pytest.mark.timeout(300)  # 模型加载 ~150s；与上一条同因
def test_clip_image_batch_single_entry_matches_single():
    emb = _load_clip_or_skip()
    data = _png_bytes()
    single = emb.embed_image(data, ext=".png")
    batch = emb.embed_image_batch([data, data], ext=".png")
    assert len(batch) == 2
    cos = sum(a * b for a, b in zip(single, batch[0])) / (
        (sum(a * a for a in single) ** 0.5) * (sum(b * b for b in batch[0]) ** 0.5)
    )
    assert cos > 0.999
