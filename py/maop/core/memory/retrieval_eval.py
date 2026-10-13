"""Retrieval-quality measurement for the multimodal space (M2.2 B4).

Why this exists
---------------
F2-02 could only be asserted with binary checks ("cross-modal top-1 8/8").
That answers "does it work at all" but not "how good is it", and it cannot
compare two embedding providers. This module adds a reproducible metric:
``NDCG@k`` over a labelled image corpus with graded relevance, run against
any provider through the same storage path.

Design notes
------------
* **Graded** relevance (2 = right colour *and* shape, 1 = right colour only,
  0 = rest). Binary grades cannot express "ranked the half-right answer
  above the wrong one", which is the interesting part.
* The corpus is measured through :meth:`MultimodalStore.search` (pure cosine,
  deterministic ``(-score, id)`` ordering) — **never** through
  ``MultimodalMemory.retrieve``: that returns RRF *rank* scores and mixes in a
  text-memory leg, which would compress the very signal being measured.
* The built-in dataset's query vectors come from real CLIP (frozen fixture);
  the queries themselves are **not** indexed as entries, so no provider can
  self-hit on an identical string (the hash fallback scores exactly 1.0 on its
  own text, which would hand it a free #1).
* Fail loud: a malformed dataset raises. A silently-skipped query would turn a
  broken labelling into a plausible-looking average.
"""

from __future__ import annotations

import json
import math
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

__all__ = [
    "DEFAULT_K",
    "EvalDataset",
    "EvalQuery",
    "evaluate_retrieval",
    "format_report_markdown",
    "load_dataset",
    "ndcg_at_k",
]

#: Default cut-off. The built-in corpus has 12 images, so k=5 keeps the
#: denominator (IDCG) meaningful; larger k would mostly grade zeros.
DEFAULT_K = 5

#: Grades used by the shipped dataset (and the minimum a dataset must have).
GRADE_EXACT = 2
GRADE_PARTIAL = 1


@dataclass(frozen=True)
class EvalQuery:
    """One labelled query: text, per-entry grades, and its intent."""

    text: str
    labels: Mapping[str, int]
    kind: str = "exact"


@dataclass(frozen=True)
class EvalDataset:
    """A labelled corpus plus the queries used to probe it.

    ``image_paths`` maps corpus id → file on disk; ``vector_source`` points at
    the frozen real-CLIP fixture the queries were embedded with (kept for
    provenance so a report can say which model the numbers belong to).
    """

    name: str
    image_paths: Mapping[str, Path]
    queries: tuple[EvalQuery, ...]
    model_id: str = ""
    revision: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def validate(self) -> None:
        """Reject a dataset that cannot produce a meaningful score."""
        if not self.queries:
            raise ValueError(f"dataset {self.name!r} has no queries")
        if not self.image_paths:
            raise ValueError(f"dataset {self.name!r} has no corpus images")
        known = set(self.image_paths)
        seen: set[str] = set()
        for q in self.queries:
            if q.text in seen:
                raise ValueError(f"duplicate query {q.text!r} in {self.name!r}")
            seen.add(q.text)
            unknown = set(q.labels) - known
            if unknown:
                raise ValueError(
                    f"query {q.text!r} labels unknown entries: {sorted(unknown)}"
                )
            if not any(g >= GRADE_EXACT for g in q.labels.values()):
                # Without a grade>=2 entry the query is unanswerable, and its
                # (zero) IDCG would silently join the average.
                raise ValueError(
                    f"query {q.text!r} has no grade>={GRADE_EXACT} label — "
                    "the dataset labelling is broken, not the retriever"
                )
            for entry_id, grade in q.labels.items():
                if grade < 0:
                    raise ValueError(f"query {q.text!r} grades {entry_id!r} negative")


def ndcg_at_k(
    ranked_ids: Iterable[str],
    relevance: Mapping[str, int],
    k: int = DEFAULT_K,
) -> float:
    """Normalised discounted cumulative gain at *k*, in ``[0, 1]``.

    ``DCG = Σ_i rel_i / log2(i + 1)`` with ``i`` counting from 1, and the ideal
    DCG is computed over the **same cut-off** *k* (truncating IDCG to the full
    ranking would bias every score downwards and make cross-*k* comparison
    meaningless). A query with no positive grade returns ``0.0`` — callers are
    expected to have rejected such datasets in :meth:`EvalDataset.validate`.
    """
    if k <= 0:
        raise ValueError(f"k must be positive, got {k}")
    ranked = list(ranked_ids)[:k]
    dcg = 0.0
    for position, entry_id in enumerate(ranked, start=1):
        grade = relevance.get(entry_id, 0)
        if grade:
            dcg += grade / math.log2(position + 1)
    ideal = sorted(relevance.values(), reverse=True)[:k]
    idcg = sum(grade / math.log2(i + 1) for i, grade in enumerate(ideal, start=1) if grade)
    return dcg / idcg if idcg else 0.0


@dataclass(frozen=True)
class QueryScore:
    """Per-query result — the unit a reviewer reads first."""

    text: str
    kind: str
    ndcg: float
    top: tuple[str, ...]


@dataclass(frozen=True)
class ProviderReport:
    """One provider's score over one dataset."""

    provider_id: str
    is_semantic: bool
    mean_ndcg: float
    k: int
    queries: tuple[QueryScore, ...]

    @property
    def min_ndcg(self) -> float:
        return min((q.ndcg for q in self.queries), default=0.0)


def evaluate_retrieval(
    make_store: Callable[[], Any],
    dataset: EvalDataset,
    *,
    k: int = DEFAULT_K,
) -> ProviderReport:
    """Score one provider's ranking over *dataset*.

    ``make_store`` is a zero-argument factory returning a **fresh** store
    already bound to the provider under test (the provider is fixed at store
    construction, so a new store is the only way to swap it). The corpus is
    indexed through the normal write path, then each query is ranked with
    ``search(query, modality="image")``.

    Everything the metric depends on is explicit: the store's ordering is
    ``(-score, id)`` so ties are deterministic, and ``threshold=0.0`` is passed
    so a positive cut-off cannot shrink the candidate set (which would change
    the score without changing the ranking).
    """
    dataset.validate()
    scores: list[QueryScore] = []
    store = make_store()
    provider = getattr(store, "_mm_embedding", None) or store
    provider_id = str(getattr(provider, "provider_id", "") or "unknown")
    is_semantic = bool(getattr(provider, "is_semantic", False))
    try:
        for entry_id, path in sorted(dataset.image_paths.items()):
            store.index_image(entry_id, path)
        for query in dataset.queries:
            hits = store.search(query.text, modality="image", threshold=0.0, top=len(dataset.image_paths))
            ranked = tuple(hit.id for hit in hits)
            scores.append(
                QueryScore(
                    text=query.text,
                    kind=query.kind,
                    ndcg=ndcg_at_k(ranked, query.labels, k=k),
                    top=ranked[:k],
                )
            )
    finally:
        dispose = getattr(store, "dispose", None)
        if callable(dispose):
            dispose()
    mean = sum(s.ndcg for s in scores) / len(scores) if scores else 0.0
    return ProviderReport(
        provider_id=provider_id,
        is_semantic=is_semantic,
        mean_ndcg=mean,
        k=k,
        queries=tuple(scores),
    )


def format_report_markdown(reports: Iterable[ProviderReport]) -> str:
    """Render one or more provider reports as a markdown table.

    ``provider_id``/``is_semantic`` are always shown: a reader must not be able
    to mistake a hash-fallback row for a semantic one (the same reason the B2
    storage report carries a 边界 section).
    """
    lines = [
        "| provider | semantic | mean NDCG@k | min | k |",
        "|---|---|---|---|---|",
    ]
    for r in reports:
        lines.append(
            f"| `{r.provider_id}` | {'yes' if r.is_semantic else '**no**'} "
            f"| {r.mean_ndcg:.4f} | {r.min_ndcg:.4f} | {r.k} |"
        )
    return "\n".join(lines)


def load_dataset(path: Path | str) -> EvalDataset:
    """Load a frozen-CLIP eval fixture (``scripts/build_clip_eval_fixture.py``).

    The fixture carries the corpus hashes, the graded labels and the model
    provenance. Vectors are *not* loaded here — they belong to whichever
    provider the caller is testing; this loader only supplies the labels.
    """
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    image_dir = Path(path).resolve().parent / "multimodal_eval"
    paths = {entry["id"]: image_dir / entry["id"] for entry in raw["corpus"]}
    queries = tuple(
        EvalQuery(text=q["text"], labels=dict(q["labels"]), kind=q.get("kind", "exact"))
        for q in raw["queries"]
    )
    dataset = EvalDataset(
        name=str(raw.get("name", Path(path).stem)),
        image_paths=paths,
        queries=queries,
        model_id=str(raw.get("model_id", "")),
        revision=str(raw.get("revision", "")),
        metadata={
            key: raw[key]
            for key in ("dimension", "pillow_version", "generator")
            if key in raw
        },
    )
    dataset.validate()
    return dataset
