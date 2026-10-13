"""Tests for the multimodal embedding provider factory (M2.2 B2 收尾).

The factory is the first place that ever *selects* a real provider: before it,
``MultimodalStore`` hard-defaulted to ``HashModalityEmbedding`` so the shipped
multimodal memory silently ran on the non-semantic fallback everywhere. These
tests pin the resolution contract without ever touching the network:

* ``hash`` forces the deterministic fallback (CI-land path, also what the
  autouse conftest fixture pins for every test);
* ``clip`` fails loudly instead of silently degrading;
* auto mode degrades to the fallback with a log, never with an exception;
* the result is cached per process (a ~600 MB model load must not repeat).
"""

from __future__ import annotations

import logging

import pytest

from maop.core.memory import multimodal_embed as mod
from maop.core.memory.multimodal_embed import (
    HashModalityEmbedding,
    reset_multimodal_provider,
    resolve_multimodal_provider,
)


@pytest.fixture(autouse=True)
def _clean_provider_cache():
    reset_multimodal_provider()
    yield
    reset_multimodal_provider()


def _force_auto_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    """Undo the conftest ``MAOP_MULTIMODAL_PROVIDER=hash`` pin.

    The autouse fixture pins hash so the suite never loads CLIP weights; these
    three cases are precisely about *auto* resolution, so they must switch the
    setting back to auto (empty) and refresh the settings singleton.
    """
    monkeypatch.setenv("MAOP_MULTIMODAL_PROVIDER", "")
    from maop.config.settings import reload_settings

    reload_settings()


# ── forced modes ────────────────────────────────────────────────


def test_forced_hash_returns_fallback() -> None:
    provider = resolve_multimodal_provider(force="hash")
    assert provider.provider_id == "hash-fallback"
    assert provider.is_semantic is False


def test_forced_clip_raises_instead_of_degrading(monkeypatch: pytest.MonkeyPatch) -> None:
    """An operator who pinned CLIP must not silently get hash vectors."""

    def _boom(*_a, **_kw):
        raise RuntimeError("weights unavailable")

    monkeypatch.setattr(mod, "ClipEmbedding", _boom)
    with pytest.raises(RuntimeError, match="weights unavailable"):
        resolve_multimodal_provider(force="clip")


def test_unknown_mode_warns_and_falls_back_to_auto() -> None:
    assert mod._provider_mode("bogus") == "auto"
    assert mod._provider_mode("") == "auto"
    assert mod._provider_mode("HASH") == "hash"


# ── auto mode ───────────────────────────────────────────────────


def test_auto_falls_back_when_extra_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    """ImportError (extra not installed) → hash fallback, no exception."""
    _force_auto_mode(monkeypatch)

    def _no_extra(*_a, **_kw):
        raise ImportError("No module named 'torch'")

    monkeypatch.setattr(mod, "ClipEmbedding", _no_extra)
    provider = resolve_multimodal_provider()
    assert isinstance(provider, HashModalityEmbedding)


def test_auto_falls_back_when_model_unusable(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Installed but broken (weights unreachable) → hash + ERROR level log.


    The log level is the distinction that matters: a missing extra is a
    documented state (warning), a failed load on an install that clearly
    intended CLIP is an error an operator should see.
    """
    _force_auto_mode(monkeypatch)

    def _broken(*_a, **_kw):
        raise RuntimeError("cannot reach huggingface.co")

    monkeypatch.setattr(mod, "ClipEmbedding", _broken)
    with caplog.at_level(logging.ERROR):
        provider = resolve_multimodal_provider()
    assert isinstance(provider, HashModalityEmbedding)
    assert any(r.levelno == logging.ERROR for r in caplog.records)


def test_auto_uses_clip_when_constructible(monkeypatch: pytest.MonkeyPatch) -> None:
    _force_auto_mode(monkeypatch)

    class FakeClip(HashModalityEmbedding):
        provider_id = "clip"
        is_semantic = True

    monkeypatch.setattr(mod, "ClipEmbedding", FakeClip)
    provider = resolve_multimodal_provider()
    assert provider.provider_id == "clip"
    assert provider.is_semantic is True


# ── caching ─────────────────────────────────────────────────────


def test_provider_is_cached_per_process(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[int] = []

    class CountingHash(HashModalityEmbedding):
        def __init__(self) -> None:
            super().__init__()
            calls.append(1)

    monkeypatch.setattr(mod, "HashModalityEmbedding", CountingHash)
    first = resolve_multimodal_provider(force="hash")
    second = resolve_multimodal_provider(force="hash")
    assert first is second
    assert len(calls) == 1


def test_reset_drops_the_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(mod, "HashModalityEmbedding", HashModalityEmbedding)
    first = resolve_multimodal_provider(force="hash")
    reset_multimodal_provider()
    assert resolve_multimodal_provider(force="hash") is not first


def test_settings_override_is_honoured(monkeypatch: pytest.MonkeyPatch) -> None:
    """auto mode reads MAOP_MULTIMODAL_PROVIDER through settings (config-drift)."""
    monkeypatch.setenv("MAOP_MULTIMODAL_PROVIDER", "hash")
    from maop.config.settings import reload_settings

    reload_settings()
    provider = resolve_multimodal_provider()
    assert provider.provider_id == "hash-fallback"
