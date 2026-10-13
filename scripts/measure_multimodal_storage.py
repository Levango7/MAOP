"""Measure the real storage cost of the multimodal space on PG vs SQLite.

M2.2 B2 left one NFR unmeasured: ADR-022 D7 states the per-entry budget
(~2 KB of vector + metadata) but only ever measured the **SQLite** path. This
script produces the PostgreSQL side of that accounting against a real
pgvector database, through the real code path (``PgMultimodalBackend`` /
``MultimodalStore``), so the numbers describe what ships rather than what the
DDL suggests.

Usage::

    # PG (needs a reachable pgvector database; schema is created if absent)
    MAOP_DATABASE_URL=postgresql://postgres:test@localhost:5432/maop_test \
        python scripts/measure_multimodal_storage.py --pg --rows 1000

    # SQLite only (no server needed)
    python scripts/measure_multimodal_storage.py --sqlite --rows 1000

The script is read-mostly: it inserts *synthetic* rows into its own schema
(namespace ``quant_<pid>_``) and drops them again, so it can be pointed at a
shared test database without touching real data.
"""

from __future__ import annotations

import argparse
import os
import sys
import tempfile
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1] / "py"
sys.path.insert(0, str(REPO))


#: A real (tiny) PNG used as the image payload — the embedder validates image
#: bytes, so synthetic bytes would be skipped and the vector column (the line
#: item this measurement is about) would stay empty. Reusing one image is fine
#: for size accounting: every entry still carries its own 512-dim vector.
FIXTURE_PNG = (
    Path(__file__).resolve().parents[1] / "py" / "tests" / "fixtures" / "multimodal" / "red_square.png"
).read_bytes()


def _synthetic_items(n: int, prefix: str) -> list[dict]:
    """``n`` text + ``n`` image-shaped entries with deterministic content.

    The image entries carry a real 512-dim float32 vector's worth of payload
    (2048 B), which is the number the budget accounting is about — the SQLite
    and PG backends both encode vectors the same way, so the comparison is
    about storage overhead, not embedding cost.
    """
    items: list[dict] = []
    for i in range(n):
        items.append(
            {
                "id": f"{prefix}-txt-{i}",
                "text": f"synthetic text entry {i} — " + ("lorem ipsum " * 12),
                "modality": "text",
                "metadata": {"source": "quantization", "seq": i},
            }
        )
    for i in range(n):
        items.append(
            {
                "id": f"{prefix}-img-{i}",
                "image": FIXTURE_PNG,  # bytes ⇒ image modality
                "modality": "image",
                "caption": f"synthetic image {i}",
                "ext": ".png",
                "metadata": {
                    "source": "quantization",
                    "seq": i,
                    "checksum": f"{i:064x}",
                    "filename": f"synthetic-{i}.png",
                },
            }
        )
    return items


def _vector_bytes() -> int:
    """Bytes one 512-dim float32 vector occupies (the budget line item)."""
    return 512 * 4


def measure_pg(url: str, rows: int, prefix: str) -> dict:
    """Insert synthetic rows into PG and measure relation/index/row sizes."""
    from maop.core.memory.multimodal_pg import PgMultimodalBackend
    from sqlalchemy import text

    backend = PgMultimodalBackend(dsn=url)
    items = _synthetic_items(rows, prefix)
    started = time.monotonic()
    stored = backend.index_many(items)
    write_s = time.monotonic() - started

    engine = backend._engine
    with engine.connect() as conn:
        row = conn.execute(
            text(
                "SELECT (SELECT count(*) FROM multimodal_entries),"
                "       (SELECT pg_total_relation_size('multimodal_entries')),"
                "       (SELECT pg_relation_size('multimodal_entries')),"
                "       (SELECT coalesce(sum(pg_column_size(t)), 0)"
                "          FROM multimodal_entries t),"
                "       (SELECT coalesce(avg(pg_column_size(t)), 0)"
                "          FROM multimodal_entries t)"
            )
        ).one()
        index_rows = conn.execute(
            text(
                "SELECT indexname, pg_relation_size(indexname::regclass)"
                " FROM pg_indexes WHERE tablename = 'multimodal_entries'"
            )
        ).all()
    total_size, heap_size, sum_column, avg_column = row[1], row[2], row[3], row[4]
    index_size = sum(int(size) for _, size in index_rows)

    # Clean up the synthetic rows (keep the schema — it is the shipped DDL).
    with engine.begin() as conn:
        conn.execute(text("DELETE FROM multimodal_entries WHERE id LIKE :p"), {"p": f"{prefix}-%"})

    return {
        "backend": "postgresql (pgvector)",
        "rows": len(stored),
        "write_s": round(write_s, 2),
        "table_bytes": int(total_size),
        "heap_bytes": int(heap_size),
        "index_bytes": int(index_size),
        "sum_row_bytes": int(sum_column),
        "avg_row_bytes": round(float(avg_column), 1),
        "indexes": [(name, int(size)) for name, size in index_rows],
        "vector_bytes_per_entry": _vector_bytes(),
    }


def measure_sqlite(rows: int, prefix: str) -> dict:
    """Same rows into the SQLite backend; measure the database file."""
    from maop.core.memory.multimodal_store import MultimodalStore

    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "multimodal.db"
        store = MultimodalStore(db_path=db_path)
        items = _synthetic_items(rows, prefix)
        started = time.monotonic()
        stored = store.index_many(items)
        write_s = time.monotonic() - started
        size = db_path.stat().st_size if db_path.exists() else 0
        del store

    return {
        "backend": "sqlite (multimodal.db)",
        "rows": len(stored),
        "write_s": round(write_s, 2),
        "table_bytes": size,
        "heap_bytes": size,
        "index_bytes": 0,  # SQLite stores indexes inside the same file
        "sum_row_bytes": size,
        "avg_row_bytes": round(size / max(1, len(stored)), 1),
        "indexes": [],
        "vector_bytes_per_entry": _vector_bytes(),
    }


def _fmt(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if abs(n) < 1024 or unit == "GB":
            return f"{n:,.1f} {unit}" if unit != "B" else f"{int(n):,} B"
        n /= 1024
    return f"{n:.1f} GB"


def report(results: list[dict]) -> str:
    lines = [
        "| 后端 | 条目数 | 库/表总量 | 堆 | 索引 | 平均行宽 | 向量净载 | 写入耗时 |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for r in results:
        lines.append(
            f"| {r['backend']} | {r['rows']} | {_fmt(r['table_bytes'])} "
            f"| {_fmt(r['heap_bytes'])} | {_fmt(r['index_bytes'])} "
            f"| {r['avg_row_bytes']:,.0f} B | {r['vector_bytes_per_entry']} B/条 "
            f"| {r['write_s']}s |"
        )
    for r in results:
        if r["indexes"]:
            lines.append("")
            lines.append(f"{r['backend']} 索引明细：")
            for name, size in r["indexes"]:
                lines.append(f"- `{name}`：{_fmt(size)}")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pg", action="store_true", help="measure the PG backend")
    parser.add_argument("--sqlite", action="store_true", help="measure the SQLite backend")
    parser.add_argument("--rows", type=int, default=1000, help="entries per modality")
    parser.add_argument(
        "--pg-url",
        default=os.environ.get("MAOP_DATABASE_URL")
        or os.environ.get("MAOP_DB_URL")
        or "postgresql://postgres:test@localhost:5432/maop_test",
        help="PostgreSQL URL (default: $MAOP_DATABASE_URL)",
    )
    args = parser.parse_args()
    if not args.pg and not args.sqlite:
        parser.error("choose --pg and/or --sqlite")

    prefix = f"quant_{os.getpid()}_{int(time.time())}"
    results = []
    if args.pg:
        results.append(measure_pg(args.pg_url, args.rows, prefix))
    if args.sqlite:
        results.append(measure_sqlite(args.rows, prefix))
    print(report(results))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
