"""Tests for the B4 retrieval-quality harness (M2.2 B4).

Three things are being pinned here, in order of importance:

1. **The metric is not vacuous.** The same dataset scores ~0.87 with real CLIP
   vectors and ~0.10 on the deterministic hash fallback. Those two numbers, and
   the gap between them, are the whole point: if a future change made the
   fallback "semantic by accident" — or made the harness secretly use the CLIP
   fixture for both rows — this test fails.
2. **NDCG's definition** (gain normalisation, the ``log2(i+1)`` discount with
   ``i`` from 1, IDCG truncated to the same *k*, zero-grade ⇒ 0.0) is checked
   against hand-computed values, so the off-by-one variants cannot slip in.
3. **The frozen fixture cannot rot silently**: revision pinned to
   ``DEFAULT_CLIP_REVISION`` and every image key matching the PNG bytes on disk
   (the keys are byte hashes, so a Pillow re-encode would orphan them).

The live-CLIP cross-check (fixture vectors == real provider vectors) is
``slow``-marked: it loads ~600 MB of weights and only runs where they are
cached, so it stays out of the default suite (CI has no torch at all) while
remaining available for a deliberate local verification.
"""

from __future__ import annotations

import json
import math
from pathlib import Path

import pytest

from maop.core.memory.multimodal_embed import (
    DEFAULT_CLIP_REVISION,
    HashModalityEmbedding,
    PrecomputedEmbedding,
)
from maop.core.memory.multimodal_store import MultimodalStore
from maop.core.memory.retrieval_eval import (
    EvalDataset,
    EvalQuery,
    evaluate_retrieval,
    format_report_markdown,
    load_dataset,
    ndcg_at_k,
)

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "clip_eval.json"
IMAGE_DIR = Path(__file__).resolve().parent / "fixtures" / "multimodal_eval"

# Discrimination thresholds — measured on the frozen fixture (2026-10-14):
#   precomputed CLIP: mean NDCG@5 = 0.8661 (min 0.7623)
#   hash fallback:    mean NDCG@5 = 0.1005 (max 0.3190)
# The margins below are deliberately wide (≈15% relative on the CLIP side,
# 2.5x on the hash side) so a legitimate model/weight change does not flake,
# while a broken harness (constant scores, swapped rows, reader using the
# wrong provider) trips them.
CLIP_MIN_MEAN = 0.75
CLIP_MIN_PER_QUERY = 0.70
HASH_MAX_MEAN = 0.25
MIN_MARGIN = 0.5


@pytest.fixture(scope="module")
def dataset() -> EvalDataset:
    return load_dataset(FIXTURE)


@pytest.fixture()
def store_factory(tmp_path: Path):
    """Fresh store per evaluation, in an isolated DB (never the real data dir)."""

    def _make(embedding):
        return lambda: MultimodalStore(
            db_path=tmp_path / "multimodal.db", embedding=embedding
        )

    return _make


# ── NDCG definition ─────────────────────────────────────────────


def test_ndcg_perfect_ranking_is_one() -> None:
    labels = {"a": 2, "b": 1, "c": 0}
    assert ndcg_at_k(["a", "b", "c"], labels, k=3) == pytest.approx(1.0)


def test_ndcg_worst_ranking_is_zero() -> None:
    """Genuinely worst: every relevant entry falls beyond the cut-off.

    (A "reversed" ranking is *not* zero — the relevant items are still inside
    k, so they still earn discounted gain. Only pushing them past the cut-off
    earns nothing.)
    """
    labels = {"a": 2, "b": 1, "c": 0}
    assert ndcg_at_k(["c", "c", "a"], labels, k=2) == pytest.approx(0.0)
    assert ndcg_at_k(["c", "b", "a"], labels, k=3) > 0.0


def test_ndcg_matches_hand_computed_value() -> None:
    """Hand-computed: ranked [2, 0, 1] over ideal [2, 1, 0] at k=3.

    DCG  = 2/log2(2) + 0 + 1/log2(4) = 2 + 0.5 = 2.5
    IDCG = 2/log2(2) + 1/log2(3)     = 2 + 0.63093 = 2.63093
    """
    labels = {"a": 2, "b": 1, "c": 0}
    expected = 2.5 / (2 + 1 / math.log2(3))
    assert ndcg_at_k(["a", "c", "b"], labels, k=3) == pytest.approx(expected)


def test_ndcg_idcg_is_truncated_to_the_same_k() -> None:
    """IDCG must use the same cut-off, not the full ideal ranking.

    With k=2 the ideal is [2, 1]; using the full ranking's IDCG would deflate
    every score and make cross-k comparison meaningless.
    """
    labels = {"a": 2, "b": 1, "c": 1}
    assert ndcg_at_k(["a", "b"], labels, k=2) == pytest.approx(1.0)
    # …and a query whose best answer sits beyond the cut-off cannot reach 1.0.
    assert ndcg_at_k(["b", "c"], labels, k=2) < 1.0


def test_ndcg_zero_relevance_returns_zero() -> None:
    """An unanswerable query scores 0.0 (datasets are validated to have none)."""
    assert ndcg_at_k(["a", "b"], {"a": 0, "b": 0}, k=2) == 0.0


def test_ndcg_rejects_non_positive_k() -> None:
    with pytest.raises(ValueError, match="k must be positive"):
        ndcg_at_k(["a"], {"a": 1}, k=0)


def test_ndcg_ignores_ids_past_the_cutoff() -> None:
    labels = {"a": 2, "b": 1, "c": 0, "d": 0}
    assert ndcg_at_k(["d", "d", "d", "a"], labels, k=1) == pytest.approx(0.0)
    assert ndcg_at_k(["a", "d", "d", "d"], labels, k=1) == pytest.approx(1.0)


# ── dataset integrity ───────────────────────────────────────────


def test_dataset_loads_with_labels_and_provenance(dataset: EvalDataset) -> None:
    assert len(dataset.image_paths) == 12
    assert len(dataset.queries) == 8
    assert dataset.model_id == "openai/clip-vit-base-patch32"
    assert dataset.revision == DEFAULT_CLIP_REVISION
    kinds = {q.kind for q in dataset.queries}
    assert kinds == {"exact", "paraphrase"}


def test_dataset_rejects_labels_for_unknown_entries() -> None:
    bad = EvalDataset(
        name="bad",
        image_paths={"a.png": IMAGE_DIR / "red_circle.png"},
        queries=(EvalQuery(text="q", labels={"nope.png": 2}),),
    )
    with pytest.raises(ValueError, match="unknown entries"):
        bad.validate()


def test_dataset_rejects_a_query_without_a_perfect_answer() -> None:
    """A query with no grade>=2 is unanswerable — reject it loudly.

    Otherwise its IDCG is 0, it scores 0.0, and a broken labelling file hides
    inside an average that still looks plausible.
    """
    bad = EvalDataset(
        name="bad",
        image_paths={"a.png": IMAGE_DIR / "red_circle.png"},
        queries=(EvalQuery(text="q", labels={"a.png": 1}),),
    )
    with pytest.raises(ValueError, match="no grade>=2"):
        bad.validate()


def test_dataset_rejects_duplicate_queries_and_negative_grades() -> None:
    base = {
        "name": "bad",
        "image_paths": {"a.png": IMAGE_DIR / "red_circle.png"},
    }
    with pytest.raises(ValueError, match="duplicate query"):
        EvalDataset(
            queries=(
                EvalQuery(text="q", labels={"a.png": 2}),
                EvalQuery(text="q", labels={"a.png": 2}),
            ),
            **base,
        ).validate()
    with pytest.raises(ValueError, match="negative"):
        EvalDataset(
            name="bad",
            image_paths={
                "a.png": IMAGE_DIR / "red_circle.png",
                "b.png": IMAGE_DIR / "red_square.png",
            },
            queries=(
                EvalQuery(text="q", labels={"a.png": -1, "b.png": 2}),
            ),
        ).validate()


def test_dataset_validates_on_load(tmp_path: Path) -> None:
    """load_dataset() validates too — a broken fixture cannot be evaluated."""
    payload = {
        "corpus": [{"id": "a.png", "colour": "red", "shape": "circle", "sha256": "x"}],
        "queries": [{"text": "q", "kind": "exact", "labels": {"missing.png": 2}}],
    }
    path = tmp_path / "clip_eval.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError):
        load_dataset(path)


# ── discrimination: the metric must separate semantic from noise ──


def test_precomputed_clip_scores_high(dataset: EvalDataset, store_factory) -> None:
    provider = PrecomputedEmbedding.from_json(FIXTURE)
    report = evaluate_retrieval(store_factory(provider), dataset)
    assert report.provider_id == "clip-precomputed-fixture"
    assert report.is_semantic is True
    assert report.mean_ndcg >= CLIP_MIN_MEAN
    assert report.min_ndcg >= CLIP_MIN_PER_QUERY


def test_hash_fallback_scores_near_random(dataset: EvalDataset, store_factory) -> None:
    provider = HashModalityEmbedding()
    report = evaluate_retrieval(store_factory(provider), dataset)
    assert report.provider_id == "hash-fallback"
    assert report.is_semantic is False
    assert report.mean_ndcg <= HASH_MAX_MEAN


def test_semantic_beats_fallback_by_a_wide_margin(
    dataset: EvalDataset, store_factory
) -> None:
    """The discrimination assertion: same dataset, same code path, two providers.

    If this ever fails, either the harness stopped measuring the embedding
    space (e.g. it silently uses the CLIP fixture for both rows) or the
    fallback became semantic — both are regressions worth a red build.
    """
    clip = evaluate_retrieval(
        store_factory(PrecomputedEmbedding.from_json(FIXTURE)), dataset
    )
    fallback = evaluate_retrieval(store_factory(HashModalityEmbedding()), dataset)
    assert clip.mean_ndcg - fallback.mean_ndcg >= MIN_MARGIN


def test_report_is_deterministic(dataset: EvalDataset, store_factory) -> None:
    """Ordering ties break on (-score, id) and the data is fixed ⇒ identical."""
    first = evaluate_retrieval(
        store_factory(PrecomputedEmbedding.from_json(FIXTURE)), dataset
    )
    second = evaluate_retrieval(
        store_factory(PrecomputedEmbedding.from_json(FIXTURE)), dataset
    )
    assert first.mean_ndcg == second.mean_ndcg
    assert [q.top for q in first.queries] == [q.top for q in second.queries]


def test_report_marks_the_non_semantic_row(dataset: EvalDataset, store_factory) -> None:
    """A reader must not be able to mistake a fallback row for a semantic one."""
    report = evaluate_retrieval(store_factory(HashModalityEmbedding()), dataset)
    table = format_report_markdown([report])
    assert "`hash-fallback`" in table
    assert "**no**" in table
    semantic = evaluate_retrieval(
        store_factory(PrecomputedEmbedding.from_json(FIXTURE)), dataset
    )
    assert "| yes |" in format_report_markdown([semantic])


def test_queries_are_not_indexed_as_entries(dataset: EvalDataset) -> None:
    """Self-hit guard: no corpus entry shares a query's text.

    The hash provider scores exactly 1.0 on an identical string, so indexing
    the queries would hand it a free #1 and destroy the discrimination test.
    """
    assert not (set(dataset.image_paths) & {q.text for q in dataset.queries})


# ── fixture integrity (anti-rot) ────────────────────────────────


def test_fixture_revision_matches_the_pinned_model() -> None:
    """Model pin moved but fixture did not ⇒ the numbers describe another model."""
    payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
    assert payload["revision"] == DEFAULT_CLIP_REVISION


def test_fixture_image_hashes_match_the_png_bytes() -> None:
    """Vector keys are byte hashes — a Pillow re-encode would orphan them."""
    import hashlib

    payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
    for entry in payload["corpus"]:
        path = IMAGE_DIR / entry["id"]
        assert path.is_file(), f"{entry['id']} missing on disk"
        assert hashlib.sha256(path.read_bytes()).hexdigest() == entry["sha256"]


def test_fixture_dimension_is_the_clip_space() -> None:
    payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
    assert payload["dimension"] == 512
    assert len(next(iter(payload["images"].values()))) == 512


# ── live CLIP cross-check (slow: weights, skipped without them) ──


@pytest.mark.slow
def test_frozen_vectors_match_the_live_provider() -> None:
    """fixture vectors == real CLIP vectors (proves the fixture is not stale).

    Skipped — never failed — when torch/weights are unavailable: CI installs
    no ML extra by design, and a missing optional dependency is not a test
    failure. Run locally with the multimodal extra and cached weights.
    """
    pytest.importorskip("torch", reason="CLIP requires the [multimodal] extra")
    pytest.importorskip("transformers", reason="CLIP requires the [multimodal] extra")
    from maop.core.memory.multimodal_embed import ClipEmbedding

    try:
        live = ClipEmbedding()
    except Exception as exc:  # weights not cached / not reachable
        pytest.skip(f"CLIP weights unavailable: {exc}")

    payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
    mismatches: list[str] = []
    for entry in payload["corpus"]:
        path = IMAGE_DIR / entry["id"]
        expected = payload["images"][entry["sha256"]]
        actual = live.embed_image(path)
        cosine = sum(a * b for a, b in zip(actual, expected))
        if cosine < 1 - 1e-4:
            mismatches.append(f"{entry['id']} cosine={cosine:.6f}")
    for text, expected in payload["texts"].items():
        actual = live.embed(text)
        cosine = sum(a * b for a, b in zip(actual, expected))
        if cosine < 1 - 1e-4:
            mismatches.append(f"text {text!r} cosine={cosine:.6f}")
    assert not mismatches, "frozen fixture drifted from the live model: " + "; ".join(
        mismatches
    )


# ── CLI entry point ─────────────────────────────────────────────


def test_cli_eval_multimodal_reports_the_fallback(capsys: pytest.CaptureFixture) -> None:
    """``maop eval multimodal`` is the production entry point for this harness."""
    from maop.cli_eval import cmd_eval

    report = cmd_eval(["multimodal", "--provider", "hash"])
    assert report is not None
    assert report.provider_id == "hash-fallback"
    out = capsys.readouterr().out
    assert "mean NDCG" in out
    assert "hash-fallback" in out
    assert "NON-semantic" in out
