"""Tests for the multimodal runtime wiring (M2.2 B2 收尾, 2026-10-14).

Covers the path that makes the async ingest queue real in production:
chat upload → ``chat_service.save_image`` → ``maybe_index_uploaded_image`` →
background batch. The interesting assertions are the ones a reviewer asked
for and the first implementation got wrong:

* the upload request must not wait for embedding (asserted as "zero embed
  calls before save_image returns", not as a wall-clock budget — wall clocks
  are flaky under ``-n auto`` on Windows runners);
* a closed queue degrades to a synchronous write instead of dropping;
* re-uploading identical bytes updates one entry (content-derived id), not two;
* a missing upload file is logged and skipped, never raised;
* auto-index defaults follow the provider's semantic-ness, and the tri-state
  env override is honoured with closed-set parsing.
"""

from __future__ import annotations

import hashlib
import time
from pathlib import Path
from typing import Any

import pytest

from maop.dashboard.services import chat_service
from maop.memory import multimodal as mm
from maop.memory.multimodal import (
    auto_index_enabled,
    maybe_index_uploaded_image,
    reset_multimodal_memory,
)

FIXTURE_PNG = (
    Path(__file__).resolve().parent / "fixtures" / "multimodal" / "red_square.png"
).read_bytes()


def _set_auto_index(monkeypatch: pytest.MonkeyPatch, value: str) -> None:
    """Set MAOP_MULTIMODAL_AUTO_INDEX **and refresh the settings singleton**.

    The settings singleton caches env at import time; monkeypatch.setenv alone
    does not invalidate it (same trap the data-dir fixture documents), so
    every flag change in a test must reload.
    """
    monkeypatch.setenv("MAOP_MULTIMODAL_AUTO_INDEX", value)
    from maop.config.settings import reload_settings

    reload_settings()


@pytest.fixture(autouse=True)
def _clean_multimodal():
    reset_multimodal_memory()
    yield
    reset_multimodal_memory()


@pytest.fixture()
def upload_root(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    (root / "data").mkdir(parents=True)
    return root


class RecordingProvider:
    """Hash-fallback provider that records every embed call.

    ``delay`` makes the "embedding is not on the request path" assertion
    unambiguous: any inline embed would show up as elapsed time.
    """

    is_semantic = False
    provider_id = "recording"

    def __init__(self, delay: float = 0.0) -> None:
        from maop.core.memory.multimodal_embed import HashModalityEmbedding

        self._inner = HashModalityEmbedding()
        self.delay = delay
        self.image_calls: list[Any] = []

    @property
    def dimension(self) -> int:
        return self._inner.dimension

    def embed(self, text: str) -> list[float]:
        return self._inner.embed(text)

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        return self._inner.embed_batch(texts)

    def embed_image(self, image: Any, *, ext: str | None = None) -> list[float]:
        self.image_calls.append(image)
        if self.delay:
            time.sleep(self.delay)
        return self._inner.embed_image(image, ext=ext)

    def embed_image_batch(self, images: list[Any]) -> list[list[float]]:
        out = [self.embed_image(img) for img in images]
        return out


@pytest.fixture()
def recording_provider(monkeypatch: pytest.MonkeyPatch) -> RecordingProvider:
    """Inject a recording provider through the factory (the injection seam)."""
    provider = RecordingProvider()
    from maop.core.memory import multimodal_embed

    monkeypatch.setattr(
        multimodal_embed, "resolve_multimodal_provider", lambda **_kw: provider
    )
    return provider


# ── auto-index decision ─────────────────────────────────────────


def test_auto_index_off_by_default_with_hash_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Conftest pins the hash provider; auto then means off (unchanged behaviour)."""
    _set_auto_index(monkeypatch, "")
    assert auto_index_enabled() is False


def test_auto_index_on_when_provider_is_semantic(
    monkeypatch: pytest.MonkeyPatch, recording_provider: RecordingProvider
) -> None:
    recording_provider.is_semantic = True
    _set_auto_index(monkeypatch, "")
    assert auto_index_enabled() is True


def test_auto_index_explicit_overrides(
    monkeypatch: pytest.MonkeyPatch, recording_provider: RecordingProvider
) -> None:
    recording_provider.is_semantic = True
    for value in ("1", "true", "on", "yes"):
        _set_auto_index(monkeypatch, value)
        assert auto_index_enabled() is True, value
    for value in ("0", "false", "off", "no"):
        _set_auto_index(monkeypatch, value)
        assert auto_index_enabled() is False, value


def test_auto_index_unknown_value_falls_back_to_auto(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A typo must not silently mean 'on' — that would write vectors for
    users who believe they turned indexing off."""
    _set_auto_index(monkeypatch, "maybe")
    assert auto_index_enabled() is False  # auto + hash provider = off


# ── upload → index wiring ───────────────────────────────────────


def test_upload_with_auto_index_off_creates_no_multimodal_db(
    upload_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _set_auto_index(monkeypatch, "0")
    image_id = chat_service.save_image(
        upload_root,
        session_id="s1",
        filename="red.png",
        data=FIXTURE_PNG,
        content_type="image/png",
    )
    assert image_id.startswith("img-")
    assert not (upload_root / "data" / "multimodal.db").exists()


def test_upload_with_auto_index_on_is_searchable_after_flush(
    upload_root: Path,
    monkeypatch: pytest.MonkeyPatch,
    recording_provider: RecordingProvider,
) -> None:
    _set_auto_index(monkeypatch, "1")
    image_id = chat_service.save_image(
        upload_root,
        session_id="s1",
        filename="red.png",
        data=FIXTURE_PNG,
        content_type="image/png",
    )
    memory = mm.get_multimodal_memory()
    assert memory.drain_ingest() == 1
    hits = memory.retrieve("a red square", include_text_memory=False)
    assert len(hits) == 1
    hit = hits[0]
    assert hit["metadata"]["image_id"] == image_id
    assert hit["metadata"]["session_id"] == "s1"
    assert hit["metadata"]["source"] == "chat-upload"
    assert hit["metadata"]["filename"] == "red.png"
    assert len(hit["metadata"]["checksum"]) == 64


def test_reupload_of_identical_bytes_updates_one_entry(
    upload_root: Path,
    monkeypatch: pytest.MonkeyPatch,
    recording_provider: RecordingProvider,
) -> None:
    """Content-derived entry id: two uploads of the same file = one entry.

    The image store mints a new id per upload (uuid), so without this the
    multimodal space would accumulate duplicates that surface twice in every
    retrieval result.
    """
    _set_auto_index(monkeypatch, "1")
    image_ids = [
        chat_service.save_image(
            upload_root,
            session_id="s1",
            filename="red.png",
            data=FIXTURE_PNG,
            content_type="image/png",
        )
        for _ in range(2)
    ]
    assert len(set(image_ids)) == 2  # the upload store still mints unique ids
    memory = mm.get_multimodal_memory()
    # Deterministic regardless of whether the background worker already took
    # one of the two items: the end state is one entry, written by the *last*
    # upload (same content ⇒ same content-derived id ⇒ upsert, not append).
    memory.close_ingest(timeout=3.0)
    hits = memory.retrieve("a red square", include_text_memory=False)
    assert len(hits) == 1
    # Which upload's metadata survives is timing-dependent (the worker's final
    # partial batch is written after close()'s drain) — what must hold is that
    # exactly one entry exists and it belongs to one of the two uploads.
    assert hits[0]["metadata"]["image_id"] in set(image_ids)
    assert hits[0]["metadata"]["checksum"] == hashlib.sha256(FIXTURE_PNG).hexdigest()


def test_embedding_is_not_on_the_request_path(
    upload_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Zero embed calls before save_image returns — no wall-clock budget.

    A slow provider (0.3 s per image) would blow any sane request budget if
    embedding ran inline; the assertion is structural so it cannot flake.
    """
    provider = RecordingProvider(delay=0.3)
    from maop.core.memory import multimodal_embed

    monkeypatch.setattr(
        multimodal_embed, "resolve_multimodal_provider", lambda **_kw: provider
    )
    _set_auto_index(monkeypatch, "1")
    started = time.monotonic()
    chat_service.save_image(
        upload_root,
        session_id="s1",
        filename="red.png",
        data=FIXTURE_PNG,
        content_type="image/png",
    )
    elapsed = time.monotonic() - started
    assert provider.image_calls == []  # nothing embedded yet
    assert elapsed < 0.25  # still asserted as a smoke check, not the contract
    # …and the entry really does land afterwards.
    memory = mm.get_multimodal_memory()
    assert memory.drain_ingest() == 1
    assert len(provider.image_calls) == 1


def test_missing_upload_file_is_skipped_not_raised(
    upload_root: Path,
    monkeypatch: pytest.MonkeyPatch,
    recording_provider: RecordingProvider,
) -> None:
    """Upload then delete → the 2 s later flush must skip, not fail the batch.

    This is the exact interleaving the per-item degradation in index_many
    covers: one unreadable image must not drop its batch-mates.
    """
    _set_auto_index(monkeypatch, "1")
    image_id = chat_service.save_image(
        upload_root,
        session_id="s1",
        filename="red.png",
        data=FIXTURE_PNG,
        content_type="image/png",
    )
    deleted = mm._upload_path(upload_root, image_id)
    assert deleted is not None and deleted.is_file()
    deleted.unlink()
    assert maybe_index_uploaded_image(upload_root, image_id, session_id="s1") == ""

    # A healthy sibling still makes it through the same batch.
    other = chat_service.save_image(
        upload_root,
        session_id="s1",
        filename="red2.png",
        data=FIXTURE_PNG,
        content_type="image/png",
    )
    assert maybe_index_uploaded_image(upload_root, other, session_id="s1")
    memory = mm.get_multimodal_memory()
    assert memory.drain_ingest() == 1


def test_closed_queue_degrades_to_synchronous_write(
    upload_root: Path,
    monkeypatch: pytest.MonkeyPatch,
    recording_provider: RecordingProvider,
) -> None:
    """After close_ingest the entry must still land — a closed queue must not
    become a silent drop."""
    _set_auto_index(monkeypatch, "1")
    memory = mm.get_multimodal_memory()
    assert memory.close_ingest(timeout=2.0) == 0
    image_id = chat_service.save_image(
        upload_root,
        session_id="s1",
        filename="red.png",
        data=FIXTURE_PNG,
        content_type="image/png",
    )
    assert image_id
    # Written synchronously: no drain needed.
    assert len(recording_provider.image_calls) == 1
    hits = memory.retrieve("a red square", include_text_memory=False)
    assert len(hits) == 1


def test_shutdown_helper_flushes_without_creating_the_singleton(
    upload_root: Path,
    monkeypatch: pytest.MonkeyPatch,
    recording_provider: RecordingProvider,
) -> None:
    _set_auto_index(monkeypatch, "1")
    # No instance yet: shutdown must be a no-op (and must not build a store).
    assert mm.drain_multimodal_ingest(timeout=1.0) == 0
    assert not (upload_root / "data" / "multimodal.db").exists()

    maybe_index_uploaded_image(
        upload_root, "img-does-not-exist", session_id="s1", image_path=None
    )  # missing file → skipped, no instance created
    assert mm.get_multimodal_memory_if_exists() == []

    image_id = chat_service.save_image(
        upload_root,
        session_id="s1",
        filename="red.png",
        data=FIXTURE_PNG,
        content_type="image/png",
    )
    assert image_id
    flushed = mm.drain_multimodal_ingest(timeout=2.0)
    assert flushed == 1
    assert len(recording_provider.image_calls) == 1


def test_indexing_failure_does_not_break_the_upload(
    upload_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Memory indexing is best-effort: a broken store must not fail uploads."""
    _set_auto_index(monkeypatch, "1")

    def _boom(**_kw):
        raise RuntimeError("store unavailable")

    from maop.core.memory import multimodal_embed

    monkeypatch.setattr(
        multimodal_embed, "resolve_multimodal_provider", _boom
    )
    image_id = chat_service.save_image(
        upload_root,
        session_id="s1",
        filename="red.png",
        data=FIXTURE_PNG,
        content_type="image/png",
    )
    assert image_id.startswith("img-")
    assert (upload_root / "data" / "uploads" / "s1" / f"{image_id}.png").is_file()


def test_multi_worker_mode_steps_aside_for_auto_index(
    monkeypatch: pytest.MonkeyPatch, recording_provider: RecordingProvider
) -> None:
    """MAOP_BACKGROUND_TASKS=0 (workers>1) ⇒ auto indexing off, but loudly.

    The queue is per-process: N workers would each hold a CLIP copy and
    serialise on the shared store. An explicit 'on' still overrides.
    """
    recording_provider.is_semantic = True
    monkeypatch.setenv("MAOP_BACKGROUND_TASKS", "0")
    from maop.config.settings import reload_settings

    reload_settings()
    _set_auto_index(monkeypatch, "")
    assert auto_index_enabled() is False
    # Explicit on wins even in multi-worker mode.
    _set_auto_index(monkeypatch, "1")
    assert auto_index_enabled() is True
