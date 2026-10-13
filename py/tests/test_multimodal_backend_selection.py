"""Backend-selection regression guards for the multimodal space (M2.2 B2).

Three env states must behave distinctly (design review B4, 2026-10-13):

1. default (no env)                     → SQLite store at <data_dir>/multimodal.db
2. ``MAOP_VECTOR_BACKEND=pg``           → PgMultimodalBackend
3. ``MAOP_VECTOR_BACKEND=sqlite``       → SQLite even when other env says pg
4. ``MAOP_MULTIMODAL_BACKEND=sqlite``   → SQLite override wins over everything

Plus: the SQLite store's own mismatch probe (a directly-constructed SQLite
store while PG is selected) must warn instead of silently writing the wrong
file — and must stay silent in the default state.
"""

from __future__ import annotations

import logging

import pytest

from maop.core.memory.multimodal_pg import PgMultimodalBackend
from maop.core.memory.multimodal_store import MultimodalStore
from maop.memory import multimodal as svc


@pytest.fixture(autouse=True)
def _reset(monkeypatch):
    """Reset both singletons: the multimodal memory cache and the settings
    singleton (env now flows through settings, so a stale singleton would
    make env-based selection tests read the first-built value)."""
    from maop.config import settings as settings_mod

    monkeypatch.setattr(settings_mod, "_settings", None)
    svc.reset_multimodal_memory()
    yield
    svc.reset_multimodal_memory()


def _clear_env(monkeypatch):
    for key in ("MAOP_VECTOR_BACKEND", "MAOP_DB_BACKEND", "MAOP_MULTIMODAL_BACKEND",
                "MAOP_DATABASE_URL", "MAOP_DB_URL"):
        monkeypatch.delenv(key, raising=False)


def test_default_selects_sqlite(monkeypatch, tmp_path):
    _clear_env(monkeypatch)
    mem = svc.get_multimodal_memory(data_dir=tmp_path)
    assert type(mem.space).__name__ == "MultimodalStore"


def test_vector_backend_pg_selects_pg(monkeypatch, tmp_path):
    _clear_env(monkeypatch)
    monkeypatch.setenv("MAOP_VECTOR_BACKEND", "pg")
    # PgMultimodalBackend construction is deferred: the factory only decides
    # which class; no engine is created here because _build_store passes no
    # engine — so patch the backend class to a probe.
    seen: dict[str, bool] = {}

    class _Probe:
        def __init__(self, *a, **kw):
            seen["constructed"] = True

    monkeypatch.setattr(
        "maop.core.memory.multimodal_pg.PgMultimodalBackend", _Probe
    )
    mem = svc.get_multimodal_memory(data_dir=tmp_path)
    assert seen.get("constructed"), "PG backend must be constructed for pg env"
    assert mem.space is not None


def test_multimodal_backend_override_forces_sqlite(monkeypatch, tmp_path):
    _clear_env(monkeypatch)
    monkeypatch.setenv("MAOP_VECTOR_BACKEND", "pg")
    monkeypatch.setenv("MAOP_MULTIMODAL_BACKEND", "sqlite")
    mem = svc.get_multimodal_memory(data_dir=tmp_path)
    assert type(mem.space).__name__ == "MultimodalStore"


def test_singleton_keys_separate_per_backend(monkeypatch, tmp_path):
    """A sqlite instance and a pg instance must not collide (ADR-019)."""
    _clear_env(monkeypatch)

    class _Probe:
        def __init__(self, *a, **kw):
            pass

    monkeypatch.setattr(
        "maop.core.memory.multimodal_pg.PgMultimodalBackend", _Probe
    )
    sqlite_mem = svc.get_multimodal_memory(data_dir=tmp_path)
    monkeypatch.setenv("MAOP_VECTOR_BACKEND", "pg")
    monkeypatch.setenv("MAOP_DATABASE_URL", "postgresql://u:p@localhost:5432/db")
    pg_mem = svc.get_multimodal_memory(data_dir=tmp_path)
    assert pg_mem is not sqlite_mem, "backend switch must not reuse the sqlite instance"


def test_explicit_dsn_selects_pg_regardless_of_env(monkeypatch, tmp_path):
    _clear_env(monkeypatch)
    monkeypatch.setenv("MAOP_VECTOR_BACKEND", "sqlite")

    class _Probe:
        def __init__(self, *a, **kw):
            pass

    monkeypatch.setattr(
        "maop.core.memory.multimodal_pg.PgMultimodalBackend", _Probe
    )
    mem = svc.get_multimodal_memory(
        data_dir=tmp_path, dsn="postgresql://u:p@localhost:5432/db"
    )
    assert mem.space is not None
    # and the sqlite instance is still cached under its own key
    sqlite_mem = svc.get_multimodal_memory(data_dir=tmp_path)
    assert type(sqlite_mem.space).__name__ == "MultimodalStore"


def test_sqlite_store_probe_only_runs_without_explicit_path(monkeypatch, tmp_path, caplog):
    """The mismatch probe runs only on the implicit default path.

    Constructing with an explicit ``db_path`` means the caller deliberately
    chose SQLite (e.g. a test or a tool); it must NOT warn. The probe covers
    the implicit path only, where the singleton's routing decision could have
    been bypassed.
    """
    _clear_env(monkeypatch)
    monkeypatch.setenv("MAOP_VECTOR_BACKEND", "pg")
    monkeypatch.setattr(
        "maop.core.backends.db_utils.resolve_data_dir", lambda: tmp_path
    )
    with caplog.at_level(logging.WARNING, logger="maop.core.memory.multimodal_store"):
        MultimodalStore()  # implicit path → probe fires
    assert "PostgreSQL" in caplog.text

    caplog.clear()
    with caplog.at_level(logging.WARNING, logger="maop.core.memory.multimodal_store"):
        MultimodalStore(db_path=tmp_path / "explicit.db")  # explicit → silent
    assert "PostgreSQL" not in caplog.text


def test_sqlite_store_silent_by_default(monkeypatch, tmp_path, caplog):
    _clear_env(monkeypatch)
    monkeypatch.setattr(
        "maop.core.backends.db_utils.resolve_data_dir", lambda: tmp_path
    )
    with caplog.at_level(logging.WARNING, logger="maop.core.memory.multimodal_store"):
        MultimodalStore()
    assert "PostgreSQL" not in caplog.text


def test_pg_backend_implements_protocol():
    assert hasattr(PgMultimodalBackend, "index_many")
    assert hasattr(PgMultimodalBackend, "ensure_schema")
