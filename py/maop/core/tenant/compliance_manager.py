"""ComplianceManager — GDPR/CCPA cascading user data deletion and export.

G-04+G-07 security fix: implements real cross-data source cascading
deletion and export for user data. Previously, ``delete_user_data`` and
``export_user_data`` were stubs that only logged. Now they reach into
all MAOP data stores:

  * **Agents** — agent configurations owned by the user.
  * **Memory** — short-term and long-term memory entries.
  * **Sessions** — conversation / execution sessions.
  * **Audit logs** — audit entries (optionally retained for compliance).
  * **RBAC grants** — role grants for the user.

Cascade deletion order (to avoid orphaned references):
  1. Sessions (reference agents + memory)
  2. Memory entries (reference agents)
  3. Agent configurations
  4. RBAC grants
  5. Audit entries (optional — retained by default for compliance)

G-07 fix: the ``tenant_id`` is taken from the JWT-authenticated request
state (``request.state.tenant_id``), never from the request body. This
prevents cross-tenant data access via forged body parameters.

This module was extracted from ``compliance.py`` to keep file sizes
manageable. ``compliance.py`` re-exports all public symbols for backward
compatibility.
"""

from __future__ import annotations

import logging
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from maop.core.backends.db_utils import sqlite_connect

logger = logging.getLogger(__name__)


# ── Models ──────────────────────────────────────────────────────


class DeletionReport(BaseModel):
    """Report of a cascading user data deletion."""
    user_id: str
    tenant_id: str = ""
    deleted_at: str = ""
    items_deleted: dict[str, int] = Field(default_factory=dict)
    items_retained: dict[str, int] = Field(default_factory=dict)
    total_deleted: int = 0
    success: bool = True
    error: str = ""


class ExportReport(BaseModel):
    """Report of a user data export."""
    user_id: str
    tenant_id: str = ""
    exported_at: str = ""
    data: dict[str, Any] = Field(default_factory=dict)
    total_items: int = 0
    success: bool = True
    error: str = ""


# ── ComplianceManager ──────────────────────────────────────────


class ComplianceManager:
    """GDPR/CCPA compliance — user data deletion and export.

    Parameters
    ----------
    root_dir : str | Path
        MAOP root directory (contains ``data/maop.db`` etc.).
    retain_audit : bool
        If True (default), audit entries are retained after deletion
        (required by most compliance frameworks for traceability).
    """

    def __init__(
        self,
        root_dir: str | Path,
        *,
        retain_audit: bool = True,
    ) -> None:
        self._root = Path(root_dir)
        self._db_path = self._root / "data" / "maop.db"
        self._retain_audit = retain_audit

    def _connect(self) -> Any:
        return sqlite_connect(self._db_path, foreign_keys=True)

    # ── G-04: Cascading deletion ─────────────────────────────

    def delete_user_data(
        self,
        user_id: str,
        *,
        tenant_id: str = "",
    ) -> DeletionReport:
        """Delete all user data across all data stores (cascade).

        G-04 fix: implements real cross-data-source cascading deletion.
        G-07 fix: ``tenant_id`` must come from JWT (request.state.tenant_id),
        not from the request body. The caller is responsible for passing
        the authenticated tenant_id.

        Parameters
        ----------
        user_id : str
            The user whose data should be deleted.
        tenant_id : str
            The tenant scope (from JWT). If non-empty, only data within
            this tenant is deleted.

        Returns
        -------
        DeletionReport
            Summary of what was deleted and what was retained.
        """
        now = datetime.now(timezone.utc).isoformat()
        report = DeletionReport(user_id=user_id, tenant_id=tenant_id, deleted_at=now)
        items_deleted: dict[str, int] = {}
        items_retained: dict[str, int] = {}

        try:
            with self._connect() as conn:
                # 1. Sessions (conversation / execution sessions)
                items_deleted["sessions"] = self._delete_sessions(
                    conn, user_id, tenant_id,
                )

                # 2. Memory entries (short-term + long-term)
                items_deleted["memory"] = self._delete_memory(
                    conn, user_id, tenant_id,
                )

                # 3. Agent configurations
                items_deleted["agents"] = self._delete_agents(
                    conn, user_id, tenant_id,
                )

                # 4. RBAC grants
                items_deleted["rbac_grants"] = self._delete_rbac_grants(
                    conn, user_id, tenant_id,
                )

                # 5. Audit entries (retained by default for compliance)
                if self._retain_audit:
                    count = self._count_audit(conn, user_id, tenant_id)
                    items_retained["audit_logs"] = count
                    logger.info(
                        "[compliance] Retained %d audit entries for user %s "
                        "(retain_audit=True)", count, user_id,
                    )
                else:
                    items_deleted["audit_logs"] = self._delete_audit(
                        conn, user_id, tenant_id,
                    )

        except Exception as exc:
            logger.error("[compliance] Deletion failed for user %s: %s", user_id, exc)
            report.success = False
            report.error = str(exc)
            return report

        report.items_deleted = items_deleted
        report.items_retained = items_retained
        report.total_deleted = sum(items_deleted.values())
        logger.info(
            "[compliance] Deleted %d items for user %s (tenant=%s): %s",
            report.total_deleted, user_id, tenant_id, items_deleted,
        )
        return report

    # ── G-04: Data export ─────────────────────────────────────

    def export_user_data(
        self,
        user_id: str,
        *,
        tenant_id: str = "",
    ) -> ExportReport:
        """Export all user data across all data stores.

        G-04 fix: implements real cross-data-source export.
        G-07 fix: ``tenant_id`` must come from JWT, not request body.

        Returns
        -------
        ExportReport
            Contains all user data in a structured dict.
        """
        now = datetime.now(timezone.utc).isoformat()
        report = ExportReport(user_id=user_id, tenant_id=tenant_id, exported_at=now)
        data: dict[str, Any] = {}

        try:
            with self._connect() as conn:
                data["sessions"] = self._export_sessions(conn, user_id, tenant_id)
                data["memory"] = self._export_memory(conn, user_id, tenant_id)
                data["agents"] = self._export_agents(conn, user_id, tenant_id)
                data["rbac_grants"] = self._export_rbac_grants(conn, user_id, tenant_id)
                data["audit_logs"] = self._export_audit(conn, user_id, tenant_id)

        except Exception as exc:
            logger.error("[compliance] Export failed for user %s: %s", user_id, exc)
            report.success = False
            report.error = str(exc)
            return report

        report.data = data
        report.total_items = sum(
            len(v) if isinstance(v, list) else 0 for v in data.values()
        )
        logger.info(
            "[compliance] Exported %d items for user %s (tenant=%s)",
            report.total_items, user_id, tenant_id,
        )
        return report

    # ── Per-data-source helpers: deletion ─────────────────────

    def _tenant_filter(self, tenant_id: str) -> str:
        """Build a SQL tenant filter clause."""
        if tenant_id:
            return " AND tenant_id = ?"
        return ""

    def _delete_sessions(self, conn: sqlite3.Connection, user_id: str, tenant_id: str) -> int:
        """Delete user sessions."""
        try:
            query = "DELETE FROM sessions WHERE user_id = ?" + self._tenant_filter(tenant_id)
            params: tuple[Any, ...] = (user_id, tenant_id) if tenant_id else (user_id,)
            cur = conn.execute(query, params)
            return cur.rowcount
        except sqlite3.OperationalError:
            # Table may not exist in all deployments.
            return 0

    def _delete_memory(self, conn: sqlite3.Connection, user_id: str, tenant_id: str) -> int:
        """Delete user memory entries (short-term + long-term)."""
        total = 0
        for table in ("memory_entries", "long_term_memory", "short_term_memory"):
            try:
                query = f"DELETE FROM {table} WHERE user_id = ?" + self._tenant_filter(tenant_id)
                params: tuple[Any, ...] = (user_id, tenant_id) if tenant_id else (user_id,)
                cur = conn.execute(query, params)
                total += cur.rowcount
            except sqlite3.OperationalError:
                pass
        return total

    def _delete_agents(self, conn: sqlite3.Connection, user_id: str, tenant_id: str) -> int:
        """Delete agent configurations owned by the user."""
        try:
            query = "DELETE FROM agents WHERE owner_id = ?" + self._tenant_filter(tenant_id)
            params: tuple[Any, ...] = (user_id, tenant_id) if tenant_id else (user_id,)
            cur = conn.execute(query, params)
            return cur.rowcount
        except sqlite3.OperationalError:
            return 0

    def _delete_rbac_grants(self, conn: sqlite3.Connection, user_id: str, tenant_id: str) -> int:
        """Delete RBAC role grants for the user."""
        try:
            query = "DELETE FROM rbac_grants WHERE user_id = ?" + self._tenant_filter(tenant_id)
            params: tuple[Any, ...] = (user_id, tenant_id) if tenant_id else (user_id,)
            cur = conn.execute(query, params)
            return cur.rowcount
        except sqlite3.OperationalError:
            return 0

    def _delete_audit(self, conn: sqlite3.Connection, user_id: str, tenant_id: str) -> int:
        """Delete audit entries for the user."""
        try:
            query = "DELETE FROM audit_entries WHERE user_id = ?" + self._tenant_filter(tenant_id)
            params: tuple[Any, ...] = (user_id, tenant_id) if tenant_id else (user_id,)
            cur = conn.execute(query, params)
            return cur.rowcount
        except sqlite3.OperationalError:
            return 0

    def _count_audit(self, conn: sqlite3.Connection, user_id: str, tenant_id: str) -> int:
        """Count audit entries for the user (for retention reporting)."""
        try:
            query = "SELECT COUNT(*) FROM audit_entries WHERE user_id = ?" + self._tenant_filter(tenant_id)
            params: tuple[Any, ...] = (user_id, tenant_id) if tenant_id else (user_id,)
            row = conn.execute(query, params).fetchone()
            return row[0] if row else 0
        except sqlite3.OperationalError:
            return 0

    # ── Per-data-source helpers: export ───────────────────────

    def _export_sessions(self, conn: sqlite3.Connection, user_id: str, tenant_id: str) -> list[dict]:
        try:
            query = "SELECT * FROM sessions WHERE user_id = ?" + self._tenant_filter(tenant_id)
            params: tuple[Any, ...] = (user_id, tenant_id) if tenant_id else (user_id,)
            rows = conn.execute(query, params).fetchall()
            return [dict(r) for r in rows]
        except sqlite3.OperationalError:
            return []

    def _export_memory(self, conn: sqlite3.Connection, user_id: str, tenant_id: str) -> list[dict]:
        result: list[dict] = []
        for table in ("memory_entries", "long_term_memory", "short_term_memory"):
            try:
                query = f"SELECT * FROM {table} WHERE user_id = ?" + self._tenant_filter(tenant_id)
                params: tuple[Any, ...] = (user_id, tenant_id) if tenant_id else (user_id,)
                rows = conn.execute(query, params).fetchall()
                for r in rows:
                    entry = dict(r)
                    entry["_source_table"] = table
                    result.append(entry)
            except sqlite3.OperationalError:
                pass
        return result

    def _export_agents(self, conn: sqlite3.Connection, user_id: str, tenant_id: str) -> list[dict]:
        try:
            query = "SELECT * FROM agents WHERE owner_id = ?" + self._tenant_filter(tenant_id)
            params: tuple[Any, ...] = (user_id, tenant_id) if tenant_id else (user_id,)
            rows = conn.execute(query, params).fetchall()
            return [dict(r) for r in rows]
        except sqlite3.OperationalError:
            return []

    def _export_rbac_grants(self, conn: sqlite3.Connection, user_id: str, tenant_id: str) -> list[dict]:
        try:
            query = "SELECT * FROM rbac_grants WHERE user_id = ?" + self._tenant_filter(tenant_id)
            params: tuple[Any, ...] = (user_id, tenant_id) if tenant_id else (user_id,)
            rows = conn.execute(query, params).fetchall()
            return [dict(r) for r in rows]
        except sqlite3.OperationalError:
            return []

    def _export_audit(self, conn: sqlite3.Connection, user_id: str, tenant_id: str) -> list[dict]:
        try:
            query = "SELECT * FROM audit_entries WHERE user_id = ?" + self._tenant_filter(tenant_id)
            params: tuple[Any, ...] = (user_id, tenant_id) if tenant_id else (user_id,)
            rows = conn.execute(query, params).fetchall()
            return [dict(r) for r in rows]
        except sqlite3.OperationalError:
            return []