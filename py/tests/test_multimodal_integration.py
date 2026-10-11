"""F2-02 B1 — non-mock integration: real SQLite + real CLIP vectors + real RRF.

Design-review requirement (I5): the CI half of the acceptance criterion
asserts **semantic ranking** using genuine CLIP outputs frozen in
``tests/fixtures/clip_gold.json`` and the real PNGs they were computed
from — no mocking of the pipeline, no non-semantic hashes standing in for
model geometry. The numbers behind the assertions (cosine matrix) are part
of the fixture.

Also hosts the interface-surface regression guard for the chat-mode
``long_term_index`` defect fixed in this batch (design review I1).
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

import pytest

from maop.core.memory.multimodal_embed import (
    HashModalityEmbedding,
    PrecomputedEmbedding,
    validate_image_input,
)
from maop.core.memory.multimodal_store import MODALITY_IMAGE, MultimodalStore
from maop.memory.multimodal import (
    MultimodalMemory,
    reset_multimodal_memory,
)

FIXTURE_DIR = Path(__file__).parent / "fixtures"
IMAGE_DIR = FIXTURE_DIR / "multimodal"
GOLD = FIXTURE_DIR / "clip_gold.json"

IMAGES = ("red_square.png", "blue_square.png", "green_square.png")
TEXTS = (
    "a red square",
    "a blue square",
    "a green square",
    "a photo of a cat",
    "a financial report spreadsheet",
)


@pytest.fixture(autouse=True)
def _reset_singleton():
    reset_multimodal_memory()
    yield
    reset_multimodal_memory()


@pytest.fixture()
def memory(tmp_path) -> MultimodalMemory:
    store = MultimodalStore(
        db_path=tmp_path / "multimodal.db",
        embedding=PrecomputedEmbedding.from_json(GOLD),
    )
    return MultimodalMemory(store=store)


def _seed(memory: MultimodalMemory) -> None:
    for name in IMAGES:
        memory.store(IMAGE_DIR / name, modality=MODALITY_IMAGE, entry_id=f"img:{name}")
    for text in TEXTS:
        memory.store(text, entry_id=f"txt:{text}")


# ── pipeline integration (real storage + real vectors) ──────


def test_end_to_end_store_and_stats(memory):
    _seed(memory)
    stats = memory.stats()
    assert stats["total"] == len(IMAGES) + len(TEXTS)
    assert stats["modalities"] == {"image": 3, "text": 5}
    assert stats["degraded"] is False  # fixture provider is real CLIP output
    # model_id reports the real producing model (the vectors ARE CLIP
    # ViT-B/32 outputs); provider_id records where they were served from.
    assert stats["embedding_model"] == "openai/clip-vit-base-patch32"
    assert stats["embedding_provider"] == "clip-precomputed-fixture"


def test_cross_modal_text_query_ranks_matching_image_first(memory):
    """CLIP geometry assertion: "a red square" must outrank the other images.

    This is the CI-executable half of the PRD acceptance: the vectors are
    real CLIP outputs (fixture), the storage/search/fusion are the real
    shipped code paths.
    """
    _seed(memory)
    hits = memory.retrieve("a red square", top=5, modalities=[MODALITY_IMAGE])
    assert hits, "cross-modal retrieval returned nothing"
    assert hits[0]["id"] == "img:red_square.png"
    image_order = [h["id"] for h in hits]
    assert image_order.index("img:red_square.png") < image_order.index(
        "img:blue_square.png"
    )
    assert image_order.index("img:red_square.png") < image_order.index(
        "img:green_square.png"
    )


@pytest.mark.parametrize(
    ("query", "expected"),
    [
        ("a red square", "img:red_square.png"),
        ("a blue square", "img:blue_square.png"),
        ("a green square", "img:green_square.png"),
    ],
)
def test_cross_modal_ranking_matrix(memory, query, expected):
    _seed(memory)
    hits = memory.retrieve(query, top=3, modalities=[MODALITY_IMAGE])
    assert hits[0]["id"] == expected


def test_unfiltered_query_spans_text_and_image_modalities(memory):
    _seed(memory)
    hits = memory.retrieve("a blue square", top=10)
    modalities = {h["modality"] for h in hits}
    assert modalities == {"text", "image"}, "cross-modal query must span modalities"
    # Identical text scores 1.0 in CLIP; the best *image* must still be blue.
    image_hits = [h["id"] for h in hits if h["modality"] == "image"]
    assert image_hits and image_hits[0] == "img:blue_square.png"


def test_store_batch_with_fixture_vectors(memory):
    ids = memory.store_batch([
        {"id": "t1", "text": "a red square"},
        {"id": "i1", "image": IMAGE_DIR / "red_square.png"},
    ])
    assert set(ids) == {"t1", "i1"}
    assert memory.stats()["modalities"] == {"text": 1, "image": 1}


# ── fusion integration (real RRF, injected text leg) ────────


def test_retrieve_fuses_text_leg_and_dedupes_overlapping_ids(memory):
    _seed(memory)
    shared_id = "txt:a red square"  # exists in both legs

    def fake_text_search(query: str, n: int):
        return [
            {"id": shared_id, "score": 0.9, "content": "a red square", "layer": "long_term"},
            {"id": "ep-1", "score": 0.5, "content": "unrelated episode"},
        ]

    memory._text_search = fake_text_search
    hits = memory.retrieve("a red square", top=10)
    by_id = {h["id"]: h for h in hits}
    assert shared_id in by_id
    assert set(by_id[shared_id]["sources"]) == {"multimodal", "text_memory"}
    # agreement across legs lifts it above single-leg hits
    assert hits[0]["id"] == shared_id
    assert by_id["ep-1"]["sources"] == ["text_memory"]
    assert by_id["ep-1"]["modality"] is None


def test_text_leg_failure_degrades_locally(memory):
    _seed(memory)

    def broken(query: str, n: int):
        raise RuntimeError("leg down")

    memory._text_search = broken
    hits = memory.retrieve("a red square", top=5)
    assert hits and all(h["sources"] == ["multimodal"] for h in hits)
    assert memory.stats()["text_memory_leg"] == "injected"


def test_image_query_skips_text_leg(memory):
    _seed(memory)
    calls = []

    def counting(query: str, n: int):
        calls.append(query)
        return []

    memory._text_search = counting
    hits = memory.retrieve(IMAGE_DIR / "red_square.png", top=3)
    assert hits[0]["id"] == "img:red_square.png"
    assert calls == [], "text leg must not fire for image queries"


# ── degraded fallback is visible, never disguised ───────────


def test_fallback_provider_reports_degraded(tmp_path):
    store = MultimodalStore(
        db_path=tmp_path / "mm.db", embedding=HashModalityEmbedding()
    )
    memory = MultimodalMemory(store=store)
    memory.store("some text")
    assert memory.degraded is True
    stats = memory.stats()
    assert stats["degraded"] is True
    assert stats["embedding_model"] == "hash-modality-fallback"


# ── facade entry points (PRD R04) ───────────────────────────


@pytest.mark.parametrize("mode", ["agent", "chat"])
def test_facade_multimodal_api(tmp_path, mode):
    """MemoryFacade.store_multimodal/retrieve_multimodal work in both modes.

    The text-memory leg binds per mode (chat → MemoryManager.long_term_search,
    agent → ThreeLayerMemory alias); with an empty text store the leg
    contributes nothing and the multimodal leg still returns the entry.
    """
    from maop.memory.facade import MemoryFacade

    facade = MemoryFacade(tmp_path / f"root-{mode}", mode=mode)
    entry_id = facade.store_multimodal(
        "deployment runbook for the payments service",
        entry_id="mm-1",
    )
    assert entry_id == "mm-1"
    assert facade.multimodal_stats()["total"] == 1

    hits = facade.retrieve_multimodal("deployment runbook", top=5)
    assert hits and hits[0]["id"] == "mm-1"
    assert hits[0]["sources"] == ["multimodal"]

    # existing text API untouched
    assert facade.stats() is not None


def test_facade_explicit_flag_skips_text_leg(tmp_path):
    from maop.memory.facade import MemoryFacade

    facade = MemoryFacade(tmp_path / "root", mode="agent")
    facade.store_multimodal("note", entry_id="mm-1")
    hits = facade.retrieve_multimodal("note", include_text_memory=False)
    assert hits[0]["sources"] == ["multimodal"]


# ── memory manager fix (chat mode long_term_index) ──────────


def test_long_term_index_actually_writes(tmp_path):
    """Behavioral regression: chat-mode single-entry long-term write works."""
    from maop.memory.manager import MemoryManager

    mgr = MemoryManager(root_dir=tmp_path)
    doc_id = mgr.long_term_index("doc-1", "hello world")
    assert doc_id == "doc-1", "long_term_index must not silently fail"
    assert mgr.vector_search is not None
    hits = mgr.long_term_search("hello world", top=5)
    hit_ids = {
        h.get("id") if isinstance(h, dict) else getattr(h, "id", None) for h in hits
    }
    assert "doc-1" in hit_ids, "stored vector must be retrievable"


def test_vectorsearch_call_surface_matches_real_methods():
    """Interface-surface guard: every ``vector_search.<name>()`` call site in
    ``manager_mixin.py`` must exist on the real ``VectorSearch`` class.

    This is the regression guard for the class of defect fixed in F2-02 B1
    (``.index`` did not exist; MagicMock-based tests hid it).
    """
    from maop.memory.vector_search import VectorSearch

    src = (
        Path(__file__).resolve().parents[1]
        / "maop" / "memory" / "manager_mixin.py"
    ).read_text(encoding="utf-8")
    called = set(re.findall(r"vector_search\.([A-Za-z_][A-Za-z0-9_]*)", src))
    called.discard("")  # property access without call would still be a name
    missing = sorted(name for name in called if not hasattr(VectorSearch, name))
    assert not missing, (
        f"manager_mixin calls non-existent VectorSearch members: {missing}"
    )


# ── fixture integrity (guards the guard) ────────────────────


def test_fixture_image_hashes_match_files():
    """The fixture's image keys must be the real sha256 of the shipped PNGs."""
    raw = json.loads(GOLD.read_text(encoding="utf-8"))
    for name in IMAGES:
        data, _ext = validate_image_input(IMAGE_DIR / name)
        assert hashlib.sha256(data).hexdigest() in raw["images"], name


def test_fixture_sanity_matrix_diagonal():
    """Reviewer-visible proof that the fixture geometry is meaningful."""
    raw = json.loads(GOLD.read_text(encoding="utf-8"))
    matrix = raw["sanity_matrix"]
    for color in ("red", "blue", "green"):
        text = f"a {color} square"
        row = matrix[text]
        best = max(row, key=row.get)
        assert best == f"{color}_square.png", f"{text!r} best match: {best}"
