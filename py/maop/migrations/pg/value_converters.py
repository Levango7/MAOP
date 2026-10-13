"""Cross-backend value converters for the SQLite → PG migration (M2.2 B2).

The legacy conversion path (:func:`sqlite_to_pg._transform_row`) handles
JSON-encoded TEXT → JSONB and column renames. Two value shapes need real
*conversion* rather than pass-through, and they are per-column concerns:

* **multimodal vectors** — SQLite stores a little-endian float32 BLOB (2048 B
  per 512-dim vector, ADR-022 D7), while pgvector takes a text literal
  ``'[v1,v2,...]'``. Inserting the raw BLOB into a ``vector`` column fails
  with ``DatatypeMismatch: ... bytea`` (verified locally), so the converter
  is mandatory, not cosmetic.
* **multimodal metadata** — kept as JSON text on SQLite and needs to be JSONB
  on PG, same as the legacy ``_JSON_COLUMNS`` registry (registered there).

A registry keyed by ``(table, column)`` keeps this out of the shared
per-row path: converters are only consulted for the pairs that declared one,
and a wrong-typed value raises instead of silently corrupting the target.
"""

from __future__ import annotations

import struct
from collections.abc import Callable
from typing import Any

__all__ = [
    "VALUE_CONVERTERS",
    "blob_to_vector_literal",
    "register_converter",
    "run_converter",
]

#: Signature: value in → converted value out (may raise on bad input).
Converter = Callable[[Any], Any]


def blob_to_vector_literal(value: Any) -> str:
    """Convert a float32 little-endian BLOB into a pgvector text literal.

    Round-trips bit-exactly: ``repr(float)`` spans every float32 value, and
    pgvector parses it back to the same float32. Raises ``ValueError`` for
    lengths that are not a whole number of float32s — a truncated BLOB must
    never be silently zero-padded into the target column.
    """
    if value is None:
        raise ValueError("cannot convert a NULL blob to a vector literal")
    if isinstance(value, str):
        # ValueError (not TypeError): text is the right *shape* but the wrong
        # *content* for a BLOB column — callers fix the source data, not the
        # call site.
        raise ValueError(  # noqa: TRY004
            "expected a float32 BLOB, got text — the SQLite multimodal store "
            "writes vectors as BLOBs"
        )
    if not isinstance(value, (bytes, bytearray, memoryview)):
        raise TypeError(f"unsupported vector payload type: {type(value).__name__}")
    buf = bytes(value)
    if len(buf) % 4 != 0:
        raise ValueError(f"vector BLOB length {len(buf)} not divisible by 4")
    floats = struct.unpack(f"<{len(buf) // 4}f", buf)
    return "[" + ",".join(repr(f) for f in floats) + "]"


#: ``(table, column)`` → converter. Empty by default; populated by
#: :func:`register_converter`. Read by ``sqlite_to_pg.copy_table``.
VALUE_CONVERTERS: dict[tuple[str, str], Converter] = {}


def register_converter(table: str, column: str) -> Callable[[Converter], Converter]:
    """Decorator registering a converter for one ``(table, column)`` pair."""
    def decorator(fn: Converter) -> Converter:
        VALUE_CONVERTERS[(table, column)] = fn
        return fn
    return decorator


#: The only converter today: the multimodal space's vector column.
register_converter("multimodal_entries", "vector")(blob_to_vector_literal)


def run_converter(table: str, column: str, value: Any) -> Any:
    """Apply the registered converter for ``(table, column)``, if any."""
    conv = VALUE_CONVERTERS.get((table, column))
    if conv is None:
        return value
    return conv(value)
