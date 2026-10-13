"""Tests for :mod:`maop.db_url` — PostgreSQL URL driver resolution.

The behaviour under test is the *version independence* of a driver-less
``postgresql://`` URL: SQLAlchemy 2.0 resolves it to psycopg2 and 2.1+ to
psycopg (v3), so without pinning the same URL works or raises
``ModuleNotFoundError: No module named 'psycopg'`` depending only on the
transitively installed SQLAlchemy. Both branches are exercised by faking
driver availability, so the tests pass on any host regardless of which
driver extras are installed.
"""

from __future__ import annotations

import importlib.util
from unittest.mock import patch

import pytest

from maop.db_url import DRIVER_PREFERENCE, resolve_pg_driver

# ── URLs that must pass through untouched ─────────────────────


@pytest.mark.parametrize(
    "url",
    [
        "",
        "sqlite:///data/maop.db",
        "sqlite:////tmp/maop.db",
        "mysql://user:pw@host/db",
        "postgresql+psycopg2://u:p@h/db",
        "postgresql+psycopg://u:p@h/db",
        "postgresql+asyncpg://u:p@h/db",
        "postgresql+psycopg2://u:p%2Bss@h/db",  # '+' inside the password
        "redis://localhost:6379/0",
        "postgresql+psycopg://u:s3cr3t%2B%2B@h:5432/db?sslmode=require",
    ],
)
def test_urls_returned_unchanged(url: str) -> None:
    """Non-PG schemes and explicitly-driver PG URLs are never rewritten."""
    assert resolve_pg_driver(url) == url


def test_no_driver_installed_returns_url_unchanged() -> None:
    """Without any DBAPI the URL is left alone so the import error still
    names the module the user has to install."""
    with patch("maop.db_url._driver_available", return_value=False):
        assert resolve_pg_driver("postgresql://u:p@h/db") == "postgresql://u:p@h/db"


# ── Driver-less URLs get pinned ───────────────────────────────


def _no_pg_driver() -> bool:
    return all(importlib.util.find_spec(d) is None for d in DRIVER_PREFERENCE)


def test_bare_url_pinned_to_an_importable_driver() -> None:
    """The chosen driver must be importable — that is the whole point."""
    if _no_pg_driver():
        pytest.skip("no PostgreSQL driver installed — nothing to pin (SQLite-only env)")
    resolved = resolve_pg_driver("postgresql://u:p@localhost:5432/db")
    scheme, _, rest = resolved.partition("://")
    assert scheme.startswith("postgresql+")
    driver = scheme.split("+", 1)[1]
    assert driver in DRIVER_PREFERENCE
    assert importlib.util.find_spec(driver) is not None
    # Everything after the scheme is preserved verbatim.
    assert resolved.endswith("://u:p@localhost:5432/db")
    assert rest == "u:p@localhost:5432/db"


def test_prefers_first_available_driver() -> None:
    """psycopg (v3) wins when both are installed; psycopg2 is the fallback."""
    with patch("maop.db_url._driver_available", side_effect=lambda d: d == "psycopg"):
        assert resolve_pg_driver("postgresql://h/db") == "postgresql+psycopg://h/db"
    with patch("maop.db_url._driver_available", side_effect=lambda d: d == "psycopg2"):
        assert resolve_pg_driver("postgresql://h/db") == "postgresql+psycopg2://h/db"


def test_legacy_postgres_scheme_normalized() -> None:
    """libpq's ``postgres://`` alias becomes an explicit postgresql+driver."""
    with patch("maop.db_url._driver_available", side_effect=lambda d: d == "psycopg2"):
        assert resolve_pg_driver("postgres://h/db") == "postgresql+psycopg2://h/db"


def test_resolved_url_is_accepted_by_create_engine() -> None:
    """SQLAlchemy must be able to build an engine from the resolved URL.

    ``create_engine`` imports the DBAPI eagerly, so this proves the pinned
    driver really exists — not merely that the string looks right. Skipped on
    hosts with no PG driver installed (SQLite-only CI envs).
    """
    pytest.importorskip("sqlalchemy", reason="SQLAlchemy not installed")
    url = "postgresql://user:pw@127.0.0.1:1/db"
    resolved = resolve_pg_driver(url)
    if resolved == url:
        pytest.skip("no PostgreSQL driver installed — nothing to pin")
    from sqlalchemy import create_engine

    # No connection is opened; engine creation only resolves the dialect/DBAPI.
    engine = create_engine(resolved)
    try:
        assert engine.url.drivername.startswith("postgresql+")
    finally:
        engine.dispose()
