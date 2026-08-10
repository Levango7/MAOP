"""MAOP Tenant RLS — Row-Level Security helpers for multi-tenant data isolation.

Provides utilities to enforce that every query/insert against a tenant-scoped
table is automatically filtered by ``tenant_id``.  This is the SQLite analogue
of PostgreSQL RLS policies.

Two enforcement modes
---------------------
1. **Column-based** (default for SQLite): each scoped table has a ``tenant_id``
   column; :meth:`TenantRLS.scoped_select` appends ``WHERE tenant_id = ?``.
2. **Prefix-based**: tables are namespaced as ``tenant_<id>__<table>``; used by
   modules that store per-tenant data in separate tables.

The manager (:class:`~maop.core.tenant.manager.TenantManager`) wires RLS into
its ``scoped_execute`` convenience method so callers cannot accidentally forget
the filter.
"""

from __future__ import annotations

import logging
import sqlite3
from typing import Any

from maop.core.backends.db_utils import sqlite_connect, validate_identifier

logger = logging.getLogger(__name__)


class RLSError(Exception):
    """Raised when an RLS policy is violated (tenant mismatch / missing scope)."""


class TenantRLS:
    """Row-Level Security policy engine for tenant-scoped tables.

    Parameters
    ----------
    db_path : str | Path
        SQLite database path (shared with :class:`TenantManager`).
    scoped_tables : list[str]
        Tables that must carry a ``tenant_id`` column.  RLS is only enforced
        for tables in this set; others are passed through untouched.
    """

    def __init__(
        self,
        db_path: Any,
        *,
        scoped_tables: list[str] | None = None,
    ) -> None:
        self._db_path = db_path
        self._scoped_tables: set[str] = set(scoped_tables or [])
        self._ensure_columns()

    def register_table(self, table: str) -> None:
        """Add *table* to the scoped set and ensure it has a tenant_id column."""
        validate_identifier(table, "table")
        self._scoped_tables.add(table)
        self._ensure_column(table)

    @property
    def scoped_tables(self) -> frozenset[str]:
        return frozenset(self._scoped_tables)

    def is_scoped(self, table: str) -> bool:
        return table in self._scoped_tables

    # ── column management ───────────────────────────────────────────

    def _ensure_columns(self) -> None:
        for table in list(self._scoped_tables):
            try:
                self._ensure_column(table)
            except sqlite3.Error as exc:
                logger.warning("could not ensure tenant_id on %s: %s", table, exc)

    def _ensure_column(self, table: str) -> None:
        validate_identifier(table, "table")
        with sqlite_connect(self._db_path) as conn:
            cols = conn.execute(f"PRAGMA table_info({table})").fetchall()
            if not cols:
                # Table does not exist yet; RLS will attach when it is created.
                return
            names = {row[1] for row in cols} if cols else set()
            if "tenant_id" not in names:
                conn.execute(
                    f"ALTER TABLE {table} ADD COLUMN tenant_id TEXT NOT NULL DEFAULT ''"
                )
                logger.info("added tenant_id column to %s", table)

    # ── query builders ──────────────────────────────────────────────

    def scoped_select(
        self,
        tenant_id: str,
        table: str,
        *,
        columns: str = "*",
        where: str = "",
        params: tuple[Any, ...] = (),
        order_by: str = "",
        limit: int = 0,
    ) -> tuple[str, tuple[Any, ...]]:
        """Build a SELECT scoped to *tenant_id*.

        Returns ``(sql, params)`` ready for ``conn.execute(sql, params)``.
        If *table* is not in the scoped set, the query is returned untouched
        (caller explicitly opted out).
        """
        validate_identifier(table, "table")
        if table not in self._scoped_tables:
            sql = f"SELECT {columns} FROM {table}"
            if where:
                sql += f" WHERE {where}"
            if order_by:
                sql += f" ORDER BY {order_by}"
            if limit > 0:
                sql += f" LIMIT {limit}"
            return sql, params

        clauses = ["tenant_id = ?"]
        all_params: list[Any] = [tenant_id]
        if where:
            clauses.append(f"({where})")
            all_params.extend(params)
        sql = f"SELECT {columns} FROM {table} WHERE " + " AND ".join(clauses)
        if order_by:
            sql += f" ORDER BY {order_by}"
        if limit > 0:
            sql += f" LIMIT {limit}"
        return sql, tuple(all_params)

    def scoped_insert(
        self,
        tenant_id: str,
        table: str,
        columns: list[str],
        values: list[Any],
    ) -> tuple[str, tuple[Any, ...]]:
        """Build an INSERT that automatically sets ``tenant_id``.

        Raises :class:`RLSError` if ``tenant_id`` appears in *columns* (the
        caller must not set it manually — RLS owns it).
        """
        validate_identifier(table, "table")
        for c in columns:
            validate_identifier(c, "column")
        if "tenant_id" in columns:
            raise RLSError("tenant_id must not be passed explicitly; RLS sets it")
        all_cols = list(columns) + ["tenant_id"]
        all_vals = list(values) + [tenant_id]
        placeholders = ", ".join("?" for _ in all_cols)
        col_list = ", ".join(all_cols)
        sql = f"INSERT INTO {table} ({col_list}) VALUES ({placeholders})"
        return sql, tuple(all_vals)

    def enforce_scope(self, tenant_id: str, table: str, row: dict[str, Any]) -> None:
        """Verify that *row* belongs to *tenant_id*.

        Raises :class:`RLSError` if the row's ``tenant_id`` is missing or
        mismatched.  Used after a raw fetch to confirm no cross-tenant leak.
        """
        if table not in self._scoped_tables:
            return
        row_tenant = row.get("tenant_id")
        if row_tenant is None:
            raise RLSError(f"row from {table} has no tenant_id (unscoped read)")
        if row_tenant != tenant_id:
            raise RLSError(
                f"cross-tenant access: row tenant={row_tenant!r} != requested {tenant_id!r}"
            )

    def tenant_prefix(self, tenant_id: str, table: str) -> str:
        """Return the prefix-namespaced table name for *tenant_id*.

        Useful for modules that store per-tenant data in separate tables:
        ``tenant_<id>__<table>``.  The caller is responsible for creating the
        table; this only computes the name.
        """
        validate_identifier(table, "table")
        safe = "".join(c if c.isalnum() else "_" for c in tenant_id)
        return f"tenant_{safe}__{table}"