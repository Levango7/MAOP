"""PostgreSQL integration for the multimodal space (M2.2 B2, PR-4).

Runs against a real PG with pgvector (CI: ``pgvector/pgvector:pg16`` service
container). Everything here is a real DDL/search path — no mocks:

* ``ensure_schema`` creates ``multimodal_entries`` + per-modality partial
  HNSW indexes (ADR-022 D2);
* real vectors are written as float32→vector literals;
* cross-modal search ranks the matching image first (real CLIP vectors come
  from the frozen fixture — same vectors B1 uses on CI);
* the modality filter and stats agree with the SQLite backend's shape.

Skipped without ``psycopg2`` / a reachable PG so the default unit suite
stays green everywhere else.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

pytestmark = pytest.mark.integration

REPO = Path(__file__).resolve().parents[2]
DEFAULT_DATABASE_URL = "postgresql://postgres:test@localhost:5432/maop_test"
GOLD = REPO / "py" / "tests" / "fixtures" / "clip_gold.json"
IMAGE_DIR = REPO / "py" / "tests" / "fixtures" / "multimodal"

MODALITIES = ("red", "blue", "green")


def _dsn() -> str:
    return os.environ.get("DATABASE_URL", DEFAULT_DATABASE_URL)


def _can_connect() -> bool:
    try:
        import psycopg2
    except ImportError:
        return False
    try:
        import psycopg2

        with psycopg2.connect(_dsn(), connect_timeout=3):
            return True
    except Exception:
        return False


_HAS_PG = _can_connect()
_skip = pytest.mark.skipif(not _HAS_PG, reason="PostgreSQL + psycopg2 not available")


def _gold() -> dict:
    return json.loads(GOLD.read_text(encoding="utf-8"))


def _pg_vector(vec: list[float]) -> str:
    return "[" + ",".join(repr(float(v)) for v in vec) + "]"


@pytest.fixture()
def backend(pytestconfig):
    pytest.importorskip("psycopg2", reason="psycopg2 not installed")
    if not _HAS_PG:
        pytest.skip("PostgreSQL not reachable")
    import psycopg2

    from maop.core.memory.multimodal_embed import HashModalityEmbedding
    from maop.core.memory.multimodal_pg import PgMultimodalBackend

    # Per-xdist-worker schema: the PG service container is shared by every
    # worker, so a shared table would let one worker's clear() wipe another's
    # data (seen as an xdist-only failure, 2026-10-13). ``search_path`` keeps
    # ``public`` last so the database-wide ``vector`` extension stays visible
    # (extensions are schema-scoped — installing one inside the worker schema
    # is not possible).
    dsn = _dsn()
    worker = getattr(pytestconfig, "workerinput", {}).get("workerid", "main")
    schema = f"mm_{worker.replace('-', '_')}"
    with psycopg2.connect(dsn, connect_timeout=5) as conn:
        conn.autocommit = True
        with conn.cursor() as cur:
            cur.execute(f'CREATE SCHEMA IF NOT EXISTS "{schema}"')
    search_path = f"{schema},public"
    dsn = (
        f"{dsn}?options=-c%20search_path%3D"
        + search_path.replace(",", "%2C")
    )
    be = PgMultimodalBackend(
        dsn, embedding=HashModalityEmbedding(), ensure_schema=True,
    )
    # The fixture's own DSN carries the per-worker search_path (SQLAlchemy's
    # rendered URL masks the password, so it cannot be reused here) — tests
    # that introspect the schema connect through this instead of the bare
    # base DSN, which would see a stale ``public`` table.
    be.fixture_dsn = dsn  # type: ignore[attr-defined]
    yield be
    be.clear()


# ── schema ───────────────────────────────────────────────────


@_skip
def test_ensure_schema_creates_table_and_modality_indexes(backend):
    """Real PG: the table exists with the spec's columns + modality indexes.

    Queries run through the *backend's own* engine URL — i.e. the same
    per-worker search_path the fixture set up — so a stale ``public`` table
    from an earlier DDL generation cannot answer for the worker's table.
    """
    dsn = backend.fixture_dsn
    import psycopg2

    # pg_indexes/pg catalog views span every schema, so the queries are
    # scoped to the search_path's first schema (the worker's own).
    schema = dsn.split("search_path%3D")[1].split("%2C")[0].strip()
    with psycopg2.connect(dsn, connect_timeout=5) as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name = 'multimodal_entries' AND table_schema = %s",
            (schema,),
        )
        cols = {r[0] for r in cur.fetchall()}
        assert {"id", "text", "vector", "metadata", "created_at"} <= cols, cols
        cur.execute(
            "SELECT indexdef FROM pg_indexes "
            "WHERE tablename = 'multimodal_entries' AND schemaname = %s",
            (schema,),
        )
        idx = "\n".join(r[0] for r in cur.fetchall())
        # No ANN index by design (approximate ordering); modality and
        # created_at B-tree indexes instead.
        assert "hnsw" not in idx.lower(), idx
        assert "ivfflat" not in idx.lower(), idx
        assert "modality" in idx
        cur.execute(
            "SELECT extversion FROM pg_extension WHERE extname='vector'")
        row = cur.fetchone()
    assert row, "pgvector extension must be present in the service image"


def test_ensure_schema_is_idempotent(backend):
    backend.ensure_schema()
    backend.ensure_schema()
    assert backend.count() >= 0


# ── storage & search ────────────────────────────────────────


def test_write_and_cross_modal_rank(backend):
    gold = _gold()
    for color in MODALITIES:
        png = IMAGE_DIR / f"{color}_square.png"
        vec = gold["images"][_sha(png.read_bytes())]
        assert backend.index_image(
            f"img-{color}", png, caption=color, vector=vec,
        ) == f"img-{color}"
    for text in (f"a {c} square" for c in MODALITIES):
        backend.index_text(
            f"txt-{text}", text, vector=gold["texts"][text],
        )

    stats = backend.stats()
    assert stats["modalities"].get("image") == 3, stats
    assert stats["modalities"].get("text") == 3, stats
    assert stats["backend"] == "pg"

    # Search by the *real CLIP* query vector from the gold fixture — the
    # service embedding provider here is the deterministic fallback (no
    # torch in CI), so ranking only reflects CLIP geometry when the query
    # vector itself is a real one.
    query_vec = gold["texts"]["a red square"]
    hits = backend.search_vector(query_vec, top=6)
    assert hits, "cross-modal search returned nothing"
    # The identical text is closest; among images the red one leads.
    assert hits[0].id == "txt-a red square", [h.id for h in hits]

    image_hits = [h for h in hits
                  if h.metadata.get("modality") == "image"]
    assert [h.id for h in image_hits] == ["img-red", "img-blue", "img-green"], [
        (h.id, round(h.score, 4)) for h in hits
    ]

    # The text-query path (embedding via the fallback provider) still runs
    # and returns rows from both modalities — it just ranks on the
    # fallback's geometry, which is not what the semantic assertion uses.
    text_hits = backend.search("a red square", top=6)
    assert {h.metadata.get("modality") for h in text_hits} >= {"text", "image"}


def test_search_is_exact_without_ann_index(backend):
    """Determinism: repeated identical queries return the identical order.

    Guards the "no ANN index" decision — an approximate index would make
    this flaky, which is exactly why it was rejected. The query vector is a
    real CLIP one (gold fixture) so ordering is meaningful too.
    """
    gold = _gold()
    png = IMAGE_DIR / "red_square.png"
    backend.index_image("img-red", png, caption="red",
                        vector=gold["images"][_sha(png.read_bytes())])
    query_vec = gold["texts"]["a red square"]
    runs = [
        [h.id for h in backend.search_vector(query_vec, top=5, modality="image")]
        for _ in range(3)
    ]
    assert runs[0] == runs[1] == runs[2], runs
    assert runs[0] == ["img-red"]


def test_modality_filter_on_pg(backend):
    gold = _gold()
    png = IMAGE_DIR / "red_square.png"
    backend.index_image("img-red", png, caption="red",
                        vector=gold["images"][_sha(png.read_bytes())])
    backend.index_text("txt-red", "a red square",
                       vector=gold["texts"]["a red square"])
    query_vec = gold["texts"]["a red square"]
    images = backend.search_vector(query_vec, top=5, modality="image")
    assert [h.id for h in images] == ["img-red"]
    assert images[0].metadata["modality"] == "image"
    texts = backend.search_vector(query_vec, top=5, modality="text")
    assert [h.id for h in texts] == ["txt-red"]


def test_image_query_finds_image(backend):
    gold = _gold()
    png = IMAGE_DIR / "red_square.png"
    vec = gold["images"][_sha(png.read_bytes())]
    backend.index_image("img-red", png, caption="red", vector=vec)
    hits = backend.search_vector(vec, top=3, modality="image")
    assert hits and hits[0].id == "img-red"
    assert hits[0].score == pytest.approx(1.0, abs=1e-6)


def test_batch_write_is_atomic_enough_to_read(backend):
    gold = _gold()
    items = []
    for color in MODALITIES:
        png = IMAGE_DIR / f"{color}_square.png"
        items.append({
            "id": f"b-img-{color}", "image": str(png), "modality": "image",
            "vector": gold["images"][_sha(png.read_bytes())],
        })
    items.append({"id": "b-txt", "text": "batch entry",
                  "vector": [0.1] * 512})
    stored = backend.index_many(items)
    assert sorted(stored) == ["b-img-blue", "b-img-green", "b-img-red", "b-txt"]
    assert backend.count() == 4


def test_delete_and_modality_of(backend):
    gold = _gold()
    png = IMAGE_DIR / "red_square.png"
    backend.index_image("img-red", png, caption="red",
                        vector=gold["images"][_sha(png.read_bytes())])
    assert backend.modality_of("img-red") == "image"
    assert backend.delete("img-red") is True
    assert backend.modality_of("img-red") == ""
    assert backend.delete("nope") is False


def _sha(data: bytes) -> str:
    import hashlib

    return hashlib.sha256(data).hexdigest()
