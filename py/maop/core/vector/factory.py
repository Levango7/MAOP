"""Vector backend factory (F1-02).

Selects a :class:`VectorBackend` implementation based on the
``MAOP_VECTOR_BACKEND`` environment variable:

- ``sqlite`` (default) → :class:`SqliteVectorBackend`
- ``pg`` / ``postgresql`` → :class:`PgVectorBackend`

The factory is the single entry point for production code; tests can bypass
it and construct backends directly.
"""
from __future__ import annotations

import logging
import os
from typing import Any

from . import VectorBackend
from .pg_backend import PgVectorBackend
from .sqlite_backend import SqliteVectorBackend

logger = logging.getLogger(__name__)

__all__ = ["get_vector_backend", "resolve_backend_name"]

_VALID_BACKENDS = frozenset({"sqlite", "pg", "postgresql"})


def resolve_backend_name(env: dict[str, str] | None = None) -> str:
    """Return the normalised backend name (``"sqlite"`` or ``"pg"``).

    Reads ``MAOP_VECTOR_BACKEND`` from *env* (defaults to ``os.environ``).
    Unknown values fall back to ``"sqlite"`` with a warning, preserving
    the zero-config default.
    """
    raw = (env or os.environ).get("MAOP_VECTOR_BACKEND", "sqlite").strip().lower()
    if raw in ("pg", "postgresql"):
        return "pg"
    if raw != "sqlite" and raw not in _VALID_BACKENDS:
        logger.warning(
            "Unknown MAOP_VECTOR_BACKEND=%r; falling back to 'sqlite'", raw,
        )
    return "sqlite" if raw not in ("pg", "postgresql") else "pg"


def get_vector_backend(
    *,
    backend: str | None = None,
    **kwargs: Any,
) -> VectorBackend:
    """Construct a :class:`VectorBackend` by name or env var.

    Parameters
    ----------
    backend : str | None
        Explicit backend name (``"sqlite"`` / ``"pg"`` / ``"postgresql"``).
        ``None`` → read ``MAOP_VECTOR_BACKEND`` (default ``"sqlite"``).
    **kwargs
        Forwarded to the backend constructor. Unrecognised keys are
        silently ignored by the target backend (its constructor accepts
        ``**`` via explicit params — extra keys raise ``TypeError``).

    Returns
    -------
    VectorBackend
        A ready-to-use backend instance.
    """
    name = (backend or resolve_backend_name()).lower()
    if name in ("pg", "postgresql"):
        logger.debug("[vector-factory] selecting PgVectorBackend")
        return PgVectorBackend(**kwargs)
    logger.debug("[vector-factory] selecting SqliteVectorBackend")
    return SqliteVectorBackend(**kwargs)