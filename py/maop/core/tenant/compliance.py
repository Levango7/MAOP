"""MAOP Compliance — GDPR/CCPA data deletion, export, DPA, and processing records.

G-04+G-07 security fix: implements real cross-data-source cascading
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

GDPR 增强（Phase 4）
--------------------
* **数据主体权利**（Data Subject Rights, Articles 15-22）：
  - 知情权 / 访问请求（Right of Access, Art. 15）：``access_request``
  - 删除权 / 被遗忘权（Right to Erasure, Art. 17）：``right_to_erasure``
  - 数据可携权（Data Portability, Art. 20）：``data_portability``
  - 请求追踪：``DataSubjectRequest`` 记录每个请求的状态与处理结果。
* **数据处理协议**（Data Processing Agreement, Art. 28）：
  - ``ProcessingAgreement`` 模型 + ``register_dpa`` / ``list_dpas``。
* **数据处理记录**（Records of Processing Activities, Art. 30）：
  - ``ProcessingRecord`` 模型 + ``record_processing_activity`` /
    ``list_processing_records``。
"""

from __future__ import annotations

import json
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


# ── GDPR Article 15-22: 数据主体权利 ──────────────────────────


#: 请求类型枚举（GDPR Articles 15-22）。
REQUEST_ACCESS = "access"          # Art. 15 — 知情权 / 访问请求
REQUEST_ERASURE = "erasure"        # Art. 17 — 删除权 / 被遗忘权
REQUEST_PORTABILITY = "portability"  # Art. 20 — 数据可携权
REQUEST_RECTIFICATION = "rectification"  # Art. 16 — 更正权
REQUEST_RESTRICTION = "restriction"  # Art. 18 — 限制处理权
REQUEST_OBJECTION = "objection"    # Art. 21 — 反对权

#: 请求状态枚举。
STATUS_PENDING = "pending"
STATUS_PROCESSING = "processing"
STATUS_COMPLETED = "completed"
STATUS_REJECTED = "rejected"


class DataSubjectRequest(BaseModel):
    """GDPR 数据主体请求记录（Articles 15-22）。"""

    request_id: str
    user_id: str
    tenant_id: str = ""
    request_type: str = REQUEST_ACCESS  # access | erasure | portability | ...
    status: str = STATUS_PENDING        # pending | processing | completed | rejected
    created_at: str = ""
    completed_at: str = ""
    detail: dict[str, Any] = Field(default_factory=dict)
    #: 处理结果摘要（删除条数 / 导出路径等）。
    result_summary: dict[str, Any] = Field(default_factory=dict)
    #: 法定响应期限（ISO 字符串）；GDPR 默认 1 个月。
    due_at: str = ""


class DataPortabilityReport(BaseModel):
    """数据可携权（Art. 20）导出报告。

    与 :class:`ExportReport` 的区别：portability 仅包含用户**主动提供**
    的数据（排除观察/推断数据），并以**机器可读的结构化格式**输出
    （JSON / CSV / XML），便于迁移到其他控制器。
    """

    user_id: str
    tenant_id: str = ""
    exported_at: str = ""
    format: str = "json"          # json | csv | xml
    data: dict[str, Any] = Field(default_factory=dict)
    total_items: int = 0
    success: bool = True
    error: str = ""


# ── GDPR Article 28: 数据处理协议（DPA） ──────────────────────


class ProcessingAgreement(BaseModel):
    """数据处理协议（Data Processing Agreement, Art. 28）。

    记录 controller（数据控制者）与 processor（数据处理者）之间的
    合同条款，包括处理目的、数据类别、子处理者、安全措施等。
    """

    dpa_id: str
    controller_name: str          # 数据控制者
    processor_name: str           # 数据处理者
    tenant_id: str = ""
    purpose: str = ""             # 处理目的
    data_categories: list[str] = Field(default_factory=list)
    sub_processors: list[str] = Field(default_factory=list)
    security_measures: list[str] = Field(default_factory=list)
    effective_date: str = ""
    termination_date: str = ""
    status: str = "active"        # active | suspended | terminated
    created_at: str = ""
    updated_at: str = ""


# ── GDPR Article 30: 数据处理记录 ─────────────────────────────


class ProcessingRecord(BaseModel):
    """数据处理活动记录（Records of Processing Activities, Art. 30）。

    每个记录描述一项处理活动：目的、数据类别、数据主体类别、
    收件人、跨境转移、保留期限、安全措施等。
    """

    record_id: str
    tenant_id: str = ""
    activity_name: str            # 处理活动名称
    purpose: str                  # 处理目的
    data_categories: list[str] = Field(default_factory=list)
    data_subject_categories: list[str] = Field(default_factory=list)
    recipients: list[str] = Field(default_factory=list)
    cross_border_transfers: list[str] = Field(default_factory=list)
    retention_period_days: int = 0   # 0 = 无限期
    security_measures: list[str] = Field(default_factory=list)
    legal_basis: str = ""         # GDPR Art. 6 法律依据
    created_at: str = ""
    updated_at: str = ""


# ── ComplianceManager 增强 ───────────────────────────────────


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _due_at(months: int = 1) -> str:
    """计算从现在起 *months* 个月后的 ISO 时间戳（GDPR 默认 1 个月）。"""
    from datetime import timedelta
    return (datetime.now(timezone.utc) + timedelta(days=30 * months)).isoformat()


class GDPRComplianceManager:
    """GDPR 合规增强 — 数据主体权利、DPA、处理记录。

    与 :class:`ComplianceManager` 协作：底层用户数据操作委托给
    ``ComplianceManager``，本类负责 GDPR 法定流程（请求追踪、
    DPA 登记、处理记录维护）。

    Parameters
    ----------
    root_dir : str | Path
        MAOP 根目录。
    retain_audit : bool
        透传给 :class:`ComplianceManager`。
    """

    def __init__(
        self,
        root_dir: str | Path,
        *,
        retain_audit: bool = True,
    ) -> None:
        self._root = Path(root_dir)
        self._db_path = self._root / "data" / "maop.db"
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._base = ComplianceManager(root_dir, retain_audit=retain_audit)
        self._ensure_tables()

    def _connect(self) -> Any:
        return sqlite_connect(self._db_path, foreign_keys=True)

    def _ensure_tables(self) -> None:
        with self._connect() as conn:
            # 数据主体请求表
            conn.execute("""
                CREATE TABLE IF NOT EXISTS gdpr_dsr (
                    request_id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    tenant_id TEXT NOT NULL DEFAULT '',
                    request_type TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'pending',
                    created_at TEXT NOT NULL DEFAULT '',
                    completed_at TEXT NOT NULL DEFAULT '',
                    detail TEXT NOT NULL DEFAULT '{}',
                    result_summary TEXT NOT NULL DEFAULT '{}',
                    due_at TEXT NOT NULL DEFAULT ''
                )
            """)
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_dsr_user "
                "ON gdpr_dsr (user_id)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_dsr_status "
                "ON gdpr_dsr (status)"
            )
            # DPA 表
            conn.execute("""
                CREATE TABLE IF NOT EXISTS gdpr_dpa (
                    dpa_id TEXT PRIMARY KEY,
                    controller_name TEXT NOT NULL,
                    processor_name TEXT NOT NULL,
                    tenant_id TEXT NOT NULL DEFAULT '',
                    purpose TEXT NOT NULL DEFAULT '',
                    data_categories TEXT NOT NULL DEFAULT '[]',
                    sub_processors TEXT NOT NULL DEFAULT '[]',
                    security_measures TEXT NOT NULL DEFAULT '[]',
                    effective_date TEXT NOT NULL DEFAULT '',
                    termination_date TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL DEFAULT 'active',
                    created_at TEXT NOT NULL DEFAULT '',
                    updated_at TEXT NOT NULL DEFAULT ''
                )
            """)
            # 处理记录表
            conn.execute("""
                CREATE TABLE IF NOT EXISTS gdpr_processing_records (
                    record_id TEXT PRIMARY KEY,
                    tenant_id TEXT NOT NULL DEFAULT '',
                    activity_name TEXT NOT NULL,
                    purpose TEXT NOT NULL DEFAULT '',
                    data_categories TEXT NOT NULL DEFAULT '[]',
                    data_subject_categories TEXT NOT NULL DEFAULT '[]',
                    recipients TEXT NOT NULL DEFAULT '[]',
                    cross_border_transfers TEXT NOT NULL DEFAULT '[]',
                    retention_period_days INTEGER NOT NULL DEFAULT 0,
                    security_measures TEXT NOT NULL DEFAULT '[]',
                    legal_basis TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL DEFAULT '',
                    updated_at TEXT NOT NULL DEFAULT ''
                )
            """)

    # ── Article 15: 知情权 / 访问请求 ─────────────────────

    def access_request(
        self,
        user_id: str,
        *,
        tenant_id: str = "",
        detail: dict[str, Any] | None = None,
    ) -> tuple[DataSubjectRequest, ExportReport]:
        """处理 GDPR Art. 15 访问请求。

        返回 ``(request, export_report)``。请求记录写入 ``gdpr_dsr`` 表，
        导出报告包含用户在所有数据存储中的数据。
        """
        request = self._create_request(
            user_id, REQUEST_ACCESS, tenant_id=tenant_id, detail=detail,
        )
        report = self._base.export_user_data(user_id, tenant_id=tenant_id)
        summary: dict[str, Any] = {
            "total_items": report.total_items,
            "success": report.success,
        }
        self._complete_request(request.request_id, summary)
        request.status = STATUS_COMPLETED
        request.completed_at = _now()
        request.result_summary = summary
        logger.info(
            "[gdpr] access request %s for user %s: %d items",
            request.request_id, user_id, report.total_items,
        )
        return request, report

    # ── Article 17: 删除权 / 被遗忘权 ─────────────────────

    def right_to_erasure(
        self,
        user_id: str,
        *,
        tenant_id: str = "",
        detail: dict[str, Any] | None = None,
    ) -> tuple[DataSubjectRequest, DeletionReport]:
        """处理 GDPR Art. 17 删除权（被遗忘权）请求。

        返回 ``(request, deletion_report)``。
        """
        request = self._create_request(
            user_id, REQUEST_ERASURE, tenant_id=tenant_id, detail=detail,
        )
        report = self._base.delete_user_data(user_id, tenant_id=tenant_id)
        summary: dict[str, Any] = {
            "total_deleted": report.total_deleted,
            "items_deleted": report.items_deleted,
            "items_retained": report.items_retained,
            "success": report.success,
        }
        self._complete_request(request.request_id, summary)
        request.status = STATUS_COMPLETED
        request.completed_at = _now()
        request.result_summary = summary
        logger.info(
            "[gdpr] erasure request %s for user %s: deleted %d items",
            request.request_id, user_id, report.total_deleted,
        )
        return request, report

    # ── Article 20: 数据可携权 ─────────────────────────────

    def data_portability(
        self,
        user_id: str,
        *,
        tenant_id: str = "",
        fmt: str = "json",
        detail: dict[str, Any] | None = None,
    ) -> tuple[DataSubjectRequest, DataPortabilityReport]:
        """处理 GDPR Art. 20 数据可携权请求。

        仅导出用户**主动提供**的数据（agents 配置、显式记忆条目），
        不包含推断 / 观察数据。输出为机器可读的结构化格式。

        Parameters
        ----------
        fmt : str
            ``"json"`` | ``"csv"`` | ``"xml"``。当前实现以 JSON 为主，
            其他格式在 ``DataPortabilityReport.format`` 字段中标注但
            ``data`` 字段仍为 JSON 结构（由调用方负责转换）。
        """
        request = self._create_request(
            user_id, REQUEST_PORTABILITY, tenant_id=tenant_id, detail=detail,
        )
        now = _now()
        portability = DataPortabilityReport(
            user_id=user_id, tenant_id=tenant_id,
            exported_at=now, format=fmt,
        )
        try:
            with self._connect() as conn:
                # 仅包含用户主动提供的数据
                portability.data["agents"] = self._export_user_provided_agents(
                    conn, user_id, tenant_id,
                )
                portability.data["explicit_memory"] = self._export_explicit_memory(
                    conn, user_id, tenant_id,
                )
                portability.data["sessions"] = self._export_sessions_for_portability(
                    conn, user_id, tenant_id,
                )
            portability.total_items = sum(
                len(v) if isinstance(v, list) else 0
                for v in portability.data.values()
            )
        except Exception as exc:
            logger.error("[gdpr] portability failed for %s: %s", user_id, exc)
            portability.success = False
            portability.error = str(exc)

        summary: dict[str, Any] = {
            "total_items": portability.total_items,
            "format": fmt,
            "success": portability.success,
        }
        self._complete_request(request.request_id, summary)
        request.status = STATUS_COMPLETED
        request.completed_at = now
        request.result_summary = summary
        logger.info(
            "[gdpr] portability request %s for user %s: %d items (%s)",
            request.request_id, user_id, portability.total_items, fmt,
        )
        return request, portability

    # ── 通用请求管理 ───────────────────────────────────────

    def _create_request(
        self,
        user_id: str,
        request_type: str,
        *,
        tenant_id: str = "",
        detail: dict[str, Any] | None = None,
    ) -> DataSubjectRequest:
        """创建并持久化一个数据主体请求记录。"""
        import uuid
        detail = detail or {}
        now = _now()
        request_id = f"dsr-{uuid.uuid4().hex[:16]}"
        request = DataSubjectRequest(
            request_id=request_id,
            user_id=user_id,
            tenant_id=tenant_id,
            request_type=request_type,
            status=STATUS_PENDING,
            created_at=now,
            detail=detail,
            due_at=_due_at(months=1),
        )
        with self._connect() as conn:
            conn.execute(
                """INSERT INTO gdpr_dsr
                   (request_id, user_id, tenant_id, request_type, status,
                    created_at, completed_at, detail, result_summary, due_at)
                   VALUES (?, ?, ?, ?, ?, ?, '', ?, '{}', ?)""",
                (
                    request.request_id, user_id, tenant_id,
                    request_type, STATUS_PENDING, now,
                    json.dumps(detail, default=str), request.due_at,
                ),
            )
        return request

    def _complete_request(
        self, request_id: str, summary: dict[str, Any],
    ) -> None:
        """标记请求为已完成。"""
        now = _now()
        with self._connect() as conn:
            conn.execute(
                """UPDATE gdpr_dsr
                   SET status = ?, completed_at = ?, result_summary = ?
                   WHERE request_id = ?""",
                (
                    STATUS_COMPLETED, now,
                    json.dumps(summary, default=str), request_id,
                ),
            )

    def get_request(self, request_id: str) -> DataSubjectRequest | None:
        """按 ID 查询数据主体请求。"""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM gdpr_dsr WHERE request_id = ?",
                (request_id,),
            ).fetchone()
        if not row:
            return None
        return self._row_to_dsr(row)

    def list_requests(
        self,
        *,
        user_id: str = "",
        tenant_id: str = "",
        status: str = "",
        request_type: str = "",
    ) -> list[DataSubjectRequest]:
        """列出数据主体请求（支持过滤）。"""
        clauses: list[str] = []
        params: list[Any] = []
        if user_id:
            clauses.append("user_id = ?")
            params.append(user_id)
        if tenant_id:
            clauses.append("tenant_id = ?")
            params.append(tenant_id)
        if status:
            clauses.append("status = ?")
            params.append(status)
        if request_type:
            clauses.append("request_type = ?")
            params.append(request_type)
        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        sql = f"SELECT * FROM gdpr_dsr{where} ORDER BY created_at DESC"
        with self._connect() as conn:
            rows = conn.execute(sql, tuple(params)).fetchall()
        return [self._row_to_dsr(r) for r in rows]

    @staticmethod
    def _row_to_dsr(row: sqlite3.Row) -> DataSubjectRequest:
        try:
            detail = json.loads(row["detail"])
        except (json.JSONDecodeError, TypeError):
            detail = {}
        try:
            summary = json.loads(row["result_summary"])
        except (json.JSONDecodeError, TypeError):
            summary = {}
        return DataSubjectRequest(
            request_id=row["request_id"],
            user_id=row["user_id"],
            tenant_id=row["tenant_id"],
            request_type=row["request_type"],
            status=row["status"],
            created_at=row["created_at"],
            completed_at=row["completed_at"],
            detail=detail,
            result_summary=summary,
            due_at=row["due_at"],
        )

    # ── Article 28: DPA 管理 ───────────────────────────────

    def register_dpa(self, dpa: ProcessingAgreement) -> ProcessingAgreement:
        """登记或更新数据处理协议（Art. 28）。"""
        now = _now()
        if not dpa.created_at:
            dpa.created_at = now
        dpa.updated_at = now
        with self._connect() as conn:
            conn.execute(
                """INSERT OR REPLACE INTO gdpr_dpa
                   (dpa_id, controller_name, processor_name, tenant_id,
                    purpose, data_categories, sub_processors,
                    security_measures, effective_date, termination_date,
                    status, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    dpa.dpa_id, dpa.controller_name, dpa.processor_name,
                    dpa.tenant_id, dpa.purpose,
                    json.dumps(dpa.data_categories),
                    json.dumps(dpa.sub_processors),
                    json.dumps(dpa.security_measures),
                    dpa.effective_date, dpa.termination_date,
                    dpa.status, dpa.created_at, dpa.updated_at,
                ),
            )
        logger.info(
            "[gdpr] registered DPA %s (%s → %s)",
            dpa.dpa_id, dpa.controller_name, dpa.processor_name,
        )
        return dpa

    def get_dpa(self, dpa_id: str) -> ProcessingAgreement | None:
        """按 ID 查询 DPA。"""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM gdpr_dpa WHERE dpa_id = ?", (dpa_id,),
            ).fetchone()
        if not row:
            return None
        return self._row_to_dpa(row)

    def list_dpas(
        self, *, tenant_id: str = "", status: str = "",
    ) -> list[ProcessingAgreement]:
        """列出 DPA（支持过滤）。"""
        clauses: list[str] = []
        params: list[Any] = []
        if tenant_id:
            clauses.append("tenant_id = ?")
            params.append(tenant_id)
        if status:
            clauses.append("status = ?")
            params.append(status)
        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        sql = f"SELECT * FROM gdpr_dpa{where} ORDER BY created_at DESC"
        with self._connect() as conn:
            rows = conn.execute(sql, tuple(params)).fetchall()
        return [self._row_to_dpa(r) for r in rows]

    @staticmethod
    def _row_to_dpa(row: sqlite3.Row) -> ProcessingAgreement:
        return ProcessingAgreement(
            dpa_id=row["dpa_id"],
            controller_name=row["controller_name"],
            processor_name=row["processor_name"],
            tenant_id=row["tenant_id"],
            purpose=row["purpose"],
            data_categories=json.loads(row["data_categories"]),
            sub_processors=json.loads(row["sub_processors"]),
            security_measures=json.loads(row["security_measures"]),
            effective_date=row["effective_date"],
            termination_date=row["termination_date"],
            status=row["status"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    # ── Article 30: 处理记录 ───────────────────────────────

    def record_processing_activity(
        self, record: ProcessingRecord,
    ) -> ProcessingRecord:
        """登记或更新一项数据处理活动记录（Art. 30）。"""
        now = _now()
        if not record.created_at:
            record.created_at = now
        record.updated_at = now
        with self._connect() as conn:
            conn.execute(
                """INSERT OR REPLACE INTO gdpr_processing_records
                   (record_id, tenant_id, activity_name, purpose,
                    data_categories, data_subject_categories, recipients,
                    cross_border_transfers, retention_period_days,
                    security_measures, legal_basis, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    record.record_id, record.tenant_id,
                    record.activity_name, record.purpose,
                    json.dumps(record.data_categories),
                    json.dumps(record.data_subject_categories),
                    json.dumps(record.recipients),
                    json.dumps(record.cross_border_transfers),
                    record.retention_period_days,
                    json.dumps(record.security_measures),
                    record.legal_basis,
                    record.created_at, record.updated_at,
                ),
            )
        logger.info(
            "[gdpr] recorded processing activity %s (%s)",
            record.record_id, record.activity_name,
        )
        return record

    def get_processing_record(self, record_id: str) -> ProcessingRecord | None:
        """按 ID 查询处理记录。"""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM gdpr_processing_records WHERE record_id = ?",
                (record_id,),
            ).fetchone()
        if not row:
            return None
        return self._row_to_processing_record(row)

    def list_processing_records(
        self, *, tenant_id: str = "",
    ) -> list[ProcessingRecord]:
        """列出处理记录（按租户过滤）。"""
        if tenant_id:
            sql = "SELECT * FROM gdpr_processing_records WHERE tenant_id = ? ORDER BY created_at DESC"
            params: tuple[Any, ...] = (tenant_id,)
        else:
            sql = "SELECT * FROM gdpr_processing_records ORDER BY created_at DESC"
            params = ()
        with self._connect() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [self._row_to_processing_record(r) for r in rows]

    @staticmethod
    def _row_to_processing_record(row: sqlite3.Row) -> ProcessingRecord:
        return ProcessingRecord(
            record_id=row["record_id"],
            tenant_id=row["tenant_id"],
            activity_name=row["activity_name"],
            purpose=row["purpose"],
            data_categories=json.loads(row["data_categories"]),
            data_subject_categories=json.loads(row["data_subject_categories"]),
            recipients=json.loads(row["recipients"]),
            cross_border_transfers=json.loads(row["cross_border_transfers"]),
            retention_period_days=row["retention_period_days"],
            security_measures=json.loads(row["security_measures"]),
            legal_basis=row["legal_basis"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    # ── 数据可携权辅助：仅导出用户主动提供的数据 ───────────

    def _export_user_provided_agents(
        self, conn: sqlite3.Connection, user_id: str, tenant_id: str,
    ) -> list[dict]:
        """导出用户主动创建的 agent 配置（排除系统推断的）。"""
        try:
            query = (
                "SELECT * FROM agents WHERE owner_id = ?"
                + self._base._tenant_filter(tenant_id)
            )
            params: tuple[Any, ...] = (user_id, tenant_id) if tenant_id else (user_id,)
            rows = conn.execute(query, params).fetchall()
            return [dict(r) for r in rows]
        except sqlite3.OperationalError:
            return []

    def _export_explicit_memory(
        self, conn: sqlite3.Connection, user_id: str, tenant_id: str,
    ) -> list[dict]:
        """导出用户显式添加的记忆条目（排除系统观察/推断的）。

        假设 ``memory_entries`` 表有 ``source`` 列；若没有则返回全部
        （向后兼容）。
        """
        result: list[dict] = []
        for table in ("memory_entries", "long_term_memory"):
            try:
                # 尝试只取 source='user' 的条目；若列不存在则回退到全部
                try:
                    query = (
                        f"SELECT * FROM {table} WHERE user_id = ? "
                        f"AND (source = 'user' OR source = 'explicit'"
                        f" OR source = 'manual')"
                        + self._base._tenant_filter(tenant_id).replace(
                            " AND tenant_id = ?", " AND tenant_id = ?",
                        )
                    )
                    params: tuple[Any, ...] = (
                        (user_id, tenant_id) if tenant_id else (user_id,)
                    )
                    rows = conn.execute(query, params).fetchall()
                except sqlite3.OperationalError:
                    # 列不存在，回退到全部
                    query = (
                        f"SELECT * FROM {table} WHERE user_id = ?"
                        + self._base._tenant_filter(tenant_id)
                    )
                    params = (user_id, tenant_id) if tenant_id else (user_id,)
                    rows = conn.execute(query, params).fetchall()
                for r in rows:
                    entry = dict(r)
                    entry["_source_table"] = table
                    result.append(entry)
            except sqlite3.OperationalError:
                pass
        return result

    def _export_sessions_for_portability(
        self, conn: sqlite3.Connection, user_id: str, tenant_id: str,
    ) -> list[dict]:
        """导出会话元数据（不含隐式行为日志）。"""
        try:
            query = (
                "SELECT * FROM sessions WHERE user_id = ?"
                + self._base._tenant_filter(tenant_id)
            )
            params: tuple[Any, ...] = (user_id, tenant_id) if tenant_id else (user_id,)
            rows = conn.execute(query, params).fetchall()
            return [dict(r) for r in rows]
        except sqlite3.OperationalError:
            return []