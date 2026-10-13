"""PostgreSQL URL helpers shared by runtime and migration code.

One problem this module exists for: a *bare* ``postgresql://`` URL does not
identify the DBAPI driver.  SQLAlchemy resolves it by default driver name,
and that default moved — ``postgresql://`` means psycopg2 on SQLAlchemy 2.0
but psycopg (v3) from 2.1 on.  So the same URL and the same installed
dependencies can either work or raise
``ModuleNotFoundError: No module named 'psycopg'`` depending only on the
transitively-installed SQLAlchemy version — a deployment-time coin flip that
no amount of pinning the MAOP extras controls.

:func:`resolve_pg_driver` removes the coin flip: the driver is chosen from
what is actually importable, so ``postgresql://`` behaves identically on
SQLAlchemy 2.0 and 2.1 and on either driver extra.  psycopg (v3) is
preferred when present because it is the driver of the enterprise storage
backend (:mod:`maop.core.backends.backends_pg`); psycopg2 is the fallback of
the ``postgresql`` extra.  When neither is installed the URL is returned
unchanged so the failure still names the module the user must install.
"""

from __future__ import annotations

import importlib.util

__all__ = ["DRIVER_PREFERENCE", "resolve_pg_driver"]


#: Candidate DBAPIs in preference order.  The first one that is importable
#: is pinned into the URL.
DRIVER_PREFERENCE = ("psycopg", "psycopg2")


def _driver_available(driver: str) -> bool:
    """Whether *driver* is importable without importing it."""
    try:
        return importlib.util.find_spec(driver) is not None
    except (ImportError, ValueError):  # pragma: no cover - defensive
        return False


def resolve_pg_driver(url: str) -> str:
    """Pin *url*'s PostgreSQL driver to an importable DBAPI.

    Returns a URL whose scheme is explicit (``postgresql+psycopg2://``,
    ``postgresql+psycopg://``).  Non-PostgreSQL URLs, already-explicit
    drivers (``+psycopg``, ``+psycopg2``, ``+asyncpg``, …) and empty input
    are returned unchanged.

    >>> resolve_pg_driver("sqlite:///x.db")
    'sqlite:///x.db'
    >>> resolve_pg_driver("postgresql+asyncpg://h/db")
    'postgresql+asyncpg://h/db'
    """
    if not url or "://" not in url:
        return url
    scheme, _, rest = url.partition("://")
    if not scheme.startswith("postgres") or "+" in scheme:
        # Non-PG URL, or an explicitly-chosen driver: nothing to decide.
        return url
    for driver in DRIVER_PREFERENCE:
        if _driver_available(driver):
            return f"postgresql+{driver}://{rest}"
    return url
