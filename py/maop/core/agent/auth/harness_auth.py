"""Harness 授权提供者 — 管理模型厂商官方配套工具的 LICENSE 授权。

ZCode 定位为 "GLM-5.2/5.3 官方 Harness"，DeepSeek 亦有官方 Harness。
这些工具的计费绑定到模型账号，需要 LICENSE 授权方式，而非独立 API key。

本模块提供 LICENSE 的注册 / 验证 / 绑定 / 吊销 / 过期检查能力，
所有状态持久化到 SQLite（表 ``harness_licenses``，``license_key`` 为主键），
并通过 :class:`threading.RLock` 保证线程安全。

设计要点：
    * ``license_key`` 为业务主键，重复注册将覆盖原记录（INSERT OR REPLACE）。
    * ``model_account`` 表示 license 绑定的模型账号；未绑定时为空串。
    * ``expires_at`` 为空串表示永不过期；非空时按 ISO 格式（``YYYY-MM-DD`` 或
      ISO 8601）解析，过期自动将 status 置为 ``expired``。
    * ``status`` 取值：``active`` / ``revoked`` / ``expired``。
"""
from __future__ import annotations

import json
import logging
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from maop.core.backends.db_utils import ConnectionPool, get_pool

logger = logging.getLogger(__name__)

# ── 常量 ────────────────────────────────────────────────────────────

_LICENSE_TABLE = "harness_licenses"


# ── Pydantic 模型 ──────────────────────────────────────────────────


class HarnessLicense(BaseModel):
    """Harness LICENSE 凭证模型。

    描述一个模型厂商官方配套工具（如 ZCode / DeepSeek Harness）的授权 license。

    Attributes:
        license_key: license 密钥（业务主键）。
        product_name: 产品名，如 "ZCode" / "DeepSeek Harness"。
        vendor: 厂商名，如 "Zhipu" / "DeepSeek"。
        model_account: 绑定的模型账号；未绑定时为空串。
        issued_at: 签发时间（ISO 格式字符串）。
        expires_at: 过期时间（ISO 格式字符串）；空串表示永不过期。
        status: 状态：``active`` / ``revoked`` / ``expired``。
        max_concurrent: 最大并发数。
        metadata: 附加元数据（任意键值对）。
    """

    license_key: str = Field(..., min_length=1)
    product_name: str = Field(..., min_length=1)
    vendor: str = Field(..., min_length=1)
    model_account: str = ""
    issued_at: str = ""
    expires_at: str = ""
    status: str = "active"
    max_concurrent: int = 1
    metadata: dict[str, Any] = Field(default_factory=dict)


# ── 授权提供者 ──────────────────────────────────────────────────────


class HarnessAuthProvider:
    """Harness LICENSE 授权提供者 — SQLite 持久化 + 线程安全。

    所有公开方法均通过 :class:`threading.RLock` 保护，可安全地在多线程
    环境下并发调用。底层依赖 :class:`ConnectionPool`（WAL + busy_timeout）。
    """

    def __init__(self, db_path: Path | None = None) -> None:
        """初始化授权提供者。

        Parameters:
            db_path: SQLite 数据库路径；为 ``None`` 时使用默认 auth 模块路径。
        """
        if db_path is None:
            from maop.core.backends.db_utils import get_db_path
            db_path = get_db_path("auth")
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._pool: ConnectionPool = get_pool(self.db_path)
        self._lock = threading.RLock()
        self._init_db()

    # ── 建表 ───────────────────────────────────────────────────────

    def _init_db(self) -> None:
        """创建 harness_licenses 表（若不存在）。"""
        conn = self._pool.acquire()
        try:
            conn.execute(
                f"""
                CREATE TABLE IF NOT EXISTS {_LICENSE_TABLE} (
                    license_key     TEXT PRIMARY KEY,
                    product_name    TEXT NOT NULL,
                    vendor          TEXT NOT NULL,
                    model_account   TEXT DEFAULT '',
                    issued_at       TEXT DEFAULT '',
                    expires_at      TEXT DEFAULT '',
                    status          TEXT DEFAULT 'active',
                    max_concurrent  INTEGER DEFAULT 1,
                    metadata        TEXT DEFAULT '{{}}'
                )
                """
            )
            conn.commit()
        finally:
            self._pool.release(conn)

    # ── 行 ↔ 模型转换 ─────────────────────────────────────────────

    @staticmethod
    def _row_to_license(row: Any) -> HarnessLicense:
        """将 SQLite 行转换为 HarnessLicense 模型。"""
        metadata_str = row["metadata"] or "{}"
        try:
            metadata = json.loads(metadata_str)
        except (json.JSONDecodeError, TypeError):
            metadata = {}
        return HarnessLicense(
            license_key=row["license_key"],
            product_name=row["product_name"],
            vendor=row["vendor"],
            model_account=row["model_account"] or "",
            issued_at=row["issued_at"] or "",
            expires_at=row["expires_at"] or "",
            status=row["status"] or "active",
            max_concurrent=row["max_concurrent"] if row["max_concurrent"] is not None else 1,
            metadata=metadata,
        )

    # ── 过期解析 ───────────────────────────────────────────────────

    @staticmethod
    def _parse_expiry(expires_at: str) -> datetime | None:
        """解析过期时间字符串为 datetime；空串或解析失败返回 None。

        支持多种格式：ISO 8601（含时区）、``YYYY-MM-DD``、
        ``YYYY-MM-DDTHH:MM:SS``。解析失败时记录警告并返回 None。
        """
        if not expires_at:
            return None
        # 尝试多种格式，从最完整到最简单。
        formats = [
            "%Y-%m-%dT%H:%M:%S%z",   # ISO 8601 带时区
            "%Y-%m-%dT%H:%M:%S",     # ISO 8601 不带时区
            "%Y-%m-%dT%H:%M:%S.%f",  # 带微秒
            "%Y-%m-%d",              # 仅日期
        ]
        for fmt in formats:
            try:
                # 入参格式可能本就不含时区，下一行显式判断 dt.tzinfo is None
                # 并按本地时间转 UTC；强行要求 %z 会让无时区的合法输入解析失败。
                dt = datetime.strptime(expires_at, fmt)  # noqa: DTZ007
                # 不带时区的视为本地时间，统一转 UTC 以便比较。
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                return dt
            except ValueError:
                continue
        # 兜底：尝试 datetime.fromisoformat（Python 3.11+ 支持更多格式）。
        try:
            dt = datetime.fromisoformat(expires_at)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt
        except ValueError:
            logger.warning("无法解析过期时间字符串: %s", expires_at)
            return None

    # ── 公开 API ───────────────────────────────────────────────────

    def register_license(self, license: HarnessLicense) -> None:
        """注册 license（已存在则覆盖）。

        Parameters:
            license: 要注册的 HarnessLicense 实例。
        """
        with self._lock:
            metadata_str = json.dumps(license.metadata, ensure_ascii=False)
            conn = self._pool.acquire()
            try:
                conn.execute(
                    f"INSERT OR REPLACE INTO {_LICENSE_TABLE} "
                    f"(license_key, product_name, vendor, model_account, "
                    f"issued_at, expires_at, status, max_concurrent, metadata) "
                    f"VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        license.license_key,
                        license.product_name,
                        license.vendor,
                        license.model_account,
                        license.issued_at,
                        license.expires_at,
                        license.status,
                        license.max_concurrent,
                        metadata_str,
                    ),
                )
                conn.commit()
            finally:
                self._pool.release(conn)
            logger.info(
                "[harness_auth] 注册 license key=%s product=%s vendor=%s",
                license.license_key, license.product_name, license.vendor,
            )

    def validate_license(self, license_key: str) -> HarnessLicense | None:
        """验证 license 有效性并返回 license 信息。

        有效指：存在且 status 为 ``active`` 且未过期。
        若已过期但 status 仍为 ``active``，会自动将 status 置为 ``expired``。

        Parameters:
            license_key: license 密钥。

        Returns:
            有效则返回 HarnessLicense，否则返回 None。
        """
        with self._lock:
            conn = self._pool.acquire()
            try:
                row = conn.execute(
                    f"SELECT * FROM {_LICENSE_TABLE} WHERE license_key = ?",
                    (license_key,),
                ).fetchone()
            finally:
                self._pool.release(conn)
            if row is None:
                return None
            lic = self._row_to_license(row)
            # 已吊销 → 无效。
            if lic.status == "revoked":
                return None
            # 检查过期。
            if lic.expires_at:
                is_expired, _ = self.check_expiry(license_key)
                if is_expired:
                    return None
            return lic

    def check_binding(self, license_key: str, model_account: str) -> bool:
        """检查 license 是否绑定到指定模型账号。

        Parameters:
            license_key: license 密钥。
            model_account: 要检查的模型账号。

        Returns:
            绑定匹配返回 True，否则返回 False。
        """
        with self._lock:
            conn = self._pool.acquire()
            try:
                row = conn.execute(
                    f"SELECT model_account FROM {_LICENSE_TABLE} WHERE license_key = ?",
                    (license_key,),
                ).fetchone()
            finally:
                self._pool.release(conn)
            if row is None:
                return False
            bound = row["model_account"] or ""
            return bool(bound) and bound == model_account

    def bind_license(self, license_key: str, model_account: str) -> None:
        """绑定 license 到模型账号。

        Parameters:
            license_key: license 密钥。
            model_account: 要绑定的模型账号。

        Raises:
            KeyError: license 不存在。
        """
        with self._lock:
            conn = self._pool.acquire()
            try:
                cur = conn.execute(
                    f"UPDATE {_LICENSE_TABLE} SET model_account = ? "
                    f"WHERE license_key = ?",
                    (model_account, license_key),
                )
                conn.commit()
                affected = cur.rowcount
            finally:
                self._pool.release(conn)
            if affected == 0:
                raise KeyError(f"license 不存在: {license_key}")
            logger.info(
                "[harness_auth] 绑定 license key=%s -> account=%s",
                license_key, model_account,
            )

    def unbind_license(self, license_key: str) -> None:
        """解绑 license（清空 model_account）。

        Parameters:
            license_key: license 密钥。

        Raises:
            KeyError: license 不存在。
        """
        with self._lock:
            conn = self._pool.acquire()
            try:
                cur = conn.execute(
                    f"UPDATE {_LICENSE_TABLE} SET model_account = '' "
                    f"WHERE license_key = ?",
                    (license_key,),
                )
                conn.commit()
                affected = cur.rowcount
            finally:
                self._pool.release(conn)
            if affected == 0:
                raise KeyError(f"license 不存在: {license_key}")
            logger.info("[harness_auth] 解绑 license key=%s", license_key)

    def revoke_license(self, license_key: str) -> None:
        """吊销 license（将 status 置为 ``revoked``）。

        Parameters:
            license_key: license 密钥。

        Raises:
            KeyError: license 不存在。
        """
        with self._lock:
            conn = self._pool.acquire()
            try:
                cur = conn.execute(
                    f"UPDATE {_LICENSE_TABLE} SET status = 'revoked' "
                    f"WHERE license_key = ?",
                    (license_key,),
                )
                conn.commit()
                affected = cur.rowcount
            finally:
                self._pool.release(conn)
            if affected == 0:
                raise KeyError(f"license 不存在: {license_key}")
            logger.info("[harness_auth] 吊销 license key=%s", license_key)

    def list_licenses(self) -> list[HarnessLicense]:
        """列出所有 license。

        Returns:
            所有 license 列表（按 license_key 排序）。
        """
        with self._lock:
            conn = self._pool.acquire()
            try:
                rows = conn.execute(
                    f"SELECT * FROM {_LICENSE_TABLE} ORDER BY license_key"
                ).fetchall()
            finally:
                self._pool.release(conn)
            return [self._row_to_license(row) for row in rows]

    def check_expiry(self, license_key: str) -> tuple[bool, int]:
        """检查 license 是否过期。

        Parameters:
            license_key: license 密钥。

        Returns:
            二元组 ``(is_expired, remaining_days)``：
            * license 不存在 → ``(True, 0)``。
            * 永不过期（expires_at 为空）→ ``(False, -1)``，-1 表示无限制。
            * 已过期 → ``(True, 0)``，并自动将 status 置为 ``expired``。
            * 未过期 → ``(False, 剩余天数)``，剩余天数 >= 0。
            * 过期时间无法解析 → ``(False, -1)``（视为永不过期）。
        """
        with self._lock:
            conn = self._pool.acquire()
            try:
                row = conn.execute(
                    f"SELECT * FROM {_LICENSE_TABLE} WHERE license_key = ?",
                    (license_key,),
                ).fetchone()
                if row is None:
                    return (True, 0)
                expires_at = row["expires_at"] or ""
                if not expires_at:
                    # 永不过期。
                    return (False, -1)
                expiry_dt = self._parse_expiry(expires_at)
                if expiry_dt is None:
                    # 解析失败，视为永不过期。
                    return (False, -1)
                now_dt = datetime.now(timezone.utc)
                delta = expiry_dt - now_dt
                remaining_days = delta.days
                if remaining_days < 0:
                    # 已过期，自动更新 status。
                    conn.execute(
                        f"UPDATE {_LICENSE_TABLE} SET status = 'expired' "
                        f"WHERE license_key = ?",
                        (license_key,),
                    )
                    conn.commit()
                    return (True, 0)
                return (False, remaining_days)
            finally:
                self._pool.release(conn)

    def close(self) -> None:
        """关闭底层连接池。"""
        self._pool.close_all()