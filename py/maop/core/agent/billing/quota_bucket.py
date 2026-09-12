"""QuotaBucket — 三桶额度分桶管理器.

每个 Agent 的额度分为三个桶，消耗顺序：**免费 → 预付 → 后付**。

桶语义：
  - **免费桶**（free）：成本 = 0，通常用于试用 / 免费额度。
  - **预付桶**（prepaid）：已购买的额度，按消耗单位扣减。
  - **后付桶**（postpaid）：信用额度上限，按消耗单位扣减，事后结算。

重置周期：
  - ``daily``   — 每日重置（UTC 0 点）
  - ``weekly``  — 每周重置（周一 UTC 0 点）
  - ``monthly`` — 每月重置（每月 1 日 UTC 0 点）
  - ``never``   — 不自动重置

持久化：SQLite 表 ``quota_bucket``，主键 ``agent_name``。
线程安全：``threading.RLock`` 保护内存状态；SQLite 由 WAL + busy_timeout 保证并发。
"""

from __future__ import annotations

import logging
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path

from pydantic import BaseModel, Field

from maop.core.backends.db_utils import get_db_path, sqlite_connect

logger = logging.getLogger(__name__)


# ── Pydantic 模型 ────────────────────────────────────────────────
class QuotaEntry(BaseModel):
    """单个 Agent 的三桶额度配置.

    所有 ``*_used`` 字段表示当前周期内已消耗的额度单位。
    ``reset_period`` 决定 ``_maybe_auto_reset`` 何时将 ``*_used`` 归零。
    """

    free_quota: int = Field(default=0, ge=0, description="免费额度（成本=0）")
    free_used: int = Field(default=0, ge=0, description="免费已用")
    prepaid_quota: int = Field(default=0, ge=0, description="预付额度（已购买）")
    prepaid_used: int = Field(default=0, ge=0, description="预付已用")
    postpaid_quota: int = Field(default=0, ge=0, description="后付额度（信用额度上限）")
    postpaid_used: int = Field(default=0, ge=0, description="后付已用")
    reset_period: str = Field(default="monthly", description="重置周期: daily/weekly/monthly/never")
    last_reset: str = Field(default="", description="上次重置时间 ISO 字符串")


class ConsumeResult(BaseModel):
    """单次消耗结果.

    ``success=False`` 时 ``consumed=0``，``reason`` 描述失败原因。
    ``success=True`` 时 ``consumed`` 等于请求量，``from_*`` 字段记录各桶扣减量。
    """

    success: bool = Field(default=False, description="是否成功")
    consumed: int = Field(default=0, ge=0, description="实际消耗量")
    from_free: int = Field(default=0, ge=0, description="从免费桶扣减")
    from_prepaid: int = Field(default=0, ge=0, description="从预付桶扣减")
    from_postpaid: int = Field(default=0, ge=0, description="从后付桶扣减")
    remaining_total: int = Field(default=0, ge=0, description="扣减后总剩余")
    reason: str = Field(default="", description="失败原因")


# ── QuotaBucket ─────────────────────────────────────────────────
class QuotaBucket:
    """额度分桶管理器.

    管理每个 Agent 的三桶额度配置与消耗。所有状态持久化到 SQLite，
    内存中不缓存 QuotaEntry（每次操作都从 DB 读取，保证多进程一致性）。

    Parameters
    ----------
    db_path : str | Path | None
        SQLite 数据库路径。``None`` 时由 ``get_db_path("quota_bucket")`` 解析。
    """

    def __init__(self, db_path: str | Path | None = None) -> None:
        self._db_path: Path = Path(db_path) if db_path else get_db_path("quota_bucket")
        self._lock = threading.RLock()
        self._init_db()

    # ── 初始化 ──────────────────────────────────────────────────
    def _init_db(self) -> None:
        """初始化 SQLite 表（幂等）。"""
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite_connect(self._db_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS quota_bucket (
                    agent_name TEXT PRIMARY KEY,
                    free_quota INTEGER DEFAULT 0,
                    free_used INTEGER DEFAULT 0,
                    prepaid_quota INTEGER DEFAULT 0,
                    prepaid_used INTEGER DEFAULT 0,
                    postpaid_quota INTEGER DEFAULT 0,
                    postpaid_used INTEGER DEFAULT 0,
                    reset_period TEXT DEFAULT 'monthly',
                    last_reset TEXT DEFAULT ''
                )
            """)

    # ── CRUD ────────────────────────────────────────────────────
    def get_quota(self, agent_name: str) -> QuotaEntry:
        """获取 Agent 的额度信息.

        不存在则返回空 ``QuotaEntry``（所有 quota/used 为 0）。
        """
        with self._lock, sqlite_connect(self._db_path) as conn:
            row = conn.execute(
                "SELECT * FROM quota_bucket WHERE agent_name = ?",
                (agent_name,),
            ).fetchone()
        if row is None:
            return QuotaEntry()
        return self._row_to_entry(row)

    def set_quota(self, agent_name: str, quota: QuotaEntry) -> None:
        """设置 / 更新 Agent 的额度配置（upsert）.

        保留 DB 中已有的 ``*_used`` 值（若该 Agent 已存在），仅更新
        ``*_quota`` / ``reset_period`` / ``last_reset``。若需重置使用量，
        请显式调用 :meth:`reset_quota`。

        若该 Agent 不存在，则插入新记录，``*_used`` 取 ``quota`` 中的值。
        """
        with self._lock, sqlite_connect(self._db_path) as conn:
            existing = conn.execute(
                "SELECT free_used, prepaid_used, postpaid_used FROM quota_bucket WHERE agent_name = ?",
                (agent_name,),
            ).fetchone()
            if existing is None:
                # 新记录：使用 quota 中的 used 值
                conn.execute(
                    """INSERT INTO quota_bucket
                       (agent_name, free_quota, free_used, prepaid_quota, prepaid_used,
                        postpaid_quota, postpaid_used, reset_period, last_reset)
                       VALUES (?,?,?,?,?,?,?,?,?)""",
                    (agent_name, quota.free_quota, quota.free_used,
                     quota.prepaid_quota, quota.prepaid_used,
                     quota.postpaid_quota, quota.postpaid_used,
                     quota.reset_period, quota.last_reset),
                )
            else:
                # 已存在：保留 used 值，仅更新 quota / reset_period / last_reset
                conn.execute(
                    """UPDATE quota_bucket SET
                       free_quota = ?, prepaid_quota = ?, postpaid_quota = ?,
                       reset_period = ?, last_reset = ?
                       WHERE agent_name = ?""",
                    (quota.free_quota, quota.prepaid_quota, quota.postpaid_quota,
                     quota.reset_period, quota.last_reset, agent_name),
                )
        logger.debug(
            "[quota_bucket] set_quota for %s: free=%d prepaid=%d postpaid=%d",
            agent_name, quota.free_quota, quota.prepaid_quota, quota.postpaid_quota,
        )

    # ── 消耗 ────────────────────────────────────────────────────
    def consume(self, agent_name: str, amount: int) -> ConsumeResult:
        """消耗额度.

        按免费 → 预付 → 后付顺序扣减。若三桶总剩余 < amount，返回失败
        （不部分扣减，保证原子性）。

        Parameters
        ----------
        agent_name : str
            Agent 名称。
        amount : int
            请求消耗量。``amount <= 0`` 时返回成功但 consumed=0。

        Returns
        -------
        ConsumeResult
            消耗结果。
        """
        if amount < 0:
            return ConsumeResult(success=False, reason=f"negative amount: {amount}")
        if amount == 0:
            quota = self.get_quota(agent_name)
            return ConsumeResult(
                success=True,
                consumed=0,
                remaining_total=self._calc_remaining(quota),
            )

        with self._lock:
            quota = self.get_quota(agent_name)
            quota = self._maybe_auto_reset(agent_name, quota)

            free_remaining = max(0, quota.free_quota - quota.free_used)
            prepaid_remaining = max(0, quota.prepaid_quota - quota.prepaid_used)
            postpaid_remaining = max(0, quota.postpaid_quota - quota.postpaid_used)
            total_remaining = free_remaining + prepaid_remaining + postpaid_remaining

            if total_remaining < amount:
                return ConsumeResult(
                    success=False,
                    consumed=0,
                    remaining_total=total_remaining,
                    reason=(
                        f"insufficient quota: need {amount}, available {total_remaining}"
                    ),
                )

            # 按免费 → 预付 → 后付顺序扣减
            free_deduct = min(amount, free_remaining)
            remaining_after_free = amount - free_deduct
            prepaid_deduct = min(remaining_after_free, prepaid_remaining)
            postpaid_deduct = remaining_after_free - prepaid_deduct

            # 更新 used 值
            new_free_used = quota.free_used + free_deduct
            new_prepaid_used = quota.prepaid_used + prepaid_deduct
            new_postpaid_used = quota.postpaid_used + postpaid_deduct

            with sqlite_connect(self._db_path) as conn:
                conn.execute(
                    """UPDATE quota_bucket SET
                       free_used = ?, prepaid_used = ?, postpaid_used = ?
                       WHERE agent_name = ?""",
                    (new_free_used, new_prepaid_used, new_postpaid_used, agent_name),
                )

            new_total_remaining = total_remaining - amount
            logger.debug(
                "[quota_bucket] consume %s: amount=%d free=%d prepaid=%d postpaid=%d",
                agent_name, amount, free_deduct, prepaid_deduct, postpaid_deduct,
            )
            return ConsumeResult(
                success=True,
                consumed=amount,
                from_free=free_deduct,
                from_prepaid=prepaid_deduct,
                from_postpaid=postpaid_deduct,
                remaining_total=new_total_remaining,
            )

    # ── 退还 ────────────────────────────────────────────────────
    def refund(self, agent_name: str, amount: int, bucket: str = "auto") -> None:
        """退还额度.

        Parameters
        ----------
        agent_name : str
            Agent 名称。
        amount : int
            退还量。``amount <= 0`` 时无操作。
        bucket : str
            退还到哪个桶：``free`` / ``prepaid`` / ``postpaid`` / ``auto``。
            ``auto`` 表示退到最近扣减的桶（按后付 → 预付 → 免费顺序找
            used > 0 的桶；与 consume 的扣减顺序相反，模拟"撤销最后一次扣减"）。
        """
        if amount <= 0:
            return

        valid_buckets = {"free", "prepaid", "postpaid", "auto"}
        if bucket not in valid_buckets:
            raise ValueError(f"invalid bucket {bucket!r}; expected one of {sorted(valid_buckets)}")

        with self._lock:
            quota = self.get_quota(agent_name)
            if bucket == "auto":
                # 按后付 → 预付 → 免费顺序找 used > 0 的桶
                if quota.postpaid_used > 0:
                    target = "postpaid"
                elif quota.prepaid_used > 0:
                    target = "prepaid"
                elif quota.free_used > 0:
                    target = "free"
                else:
                    logger.debug(
                        "[quota_bucket] refund auto: no used quota for %s, no-op",
                        agent_name,
                    )
                    return
            else:
                target = bucket

            # 扣减 used（不能低于 0）
            if target == "free":
                new_used = max(0, quota.free_used - amount)
            elif target == "prepaid":
                new_used = max(0, quota.prepaid_used - amount)
            else:  # postpaid
                new_used = max(0, quota.postpaid_used - amount)

            with sqlite_connect(self._db_path) as conn:
                if target == "free":
                    conn.execute(
                        "UPDATE quota_bucket SET free_used = ? WHERE agent_name = ?",
                        (new_used, agent_name),
                    )
                elif target == "prepaid":
                    conn.execute(
                        "UPDATE quota_bucket SET prepaid_used = ? WHERE agent_name = ?",
                        (new_used, agent_name),
                    )
                else:
                    conn.execute(
                        "UPDATE quota_bucket SET postpaid_used = ? WHERE agent_name = ?",
                        (new_used, agent_name),
                    )
            logger.debug(
                "[quota_bucket] refund %s: amount=%d bucket=%s",
                agent_name, amount, target,
            )

    # ── 查询 ────────────────────────────────────────────────────
    def get_remaining(self, agent_name: str) -> dict[str, int]:
        """获取各桶剩余额度.

        Returns
        -------
        dict[str, int]
            ``{"free": x, "prepaid": y, "postpaid": z}``
        """
        quota = self.get_quota(agent_name)
        return {
            "free": max(0, quota.free_quota - quota.free_used),
            "prepaid": max(0, quota.prepaid_quota - quota.prepaid_used),
            "postpaid": max(0, quota.postpaid_quota - quota.postpaid_used),
        }

    def check_quota(self, agent_name: str, amount: int) -> bool:
        """检查是否有足够额度（不实际扣减）.

        ``amount <= 0`` 时始终返回 True。
        """
        if amount <= 0:
            return True
        quota = self.get_quota(agent_name)
        total = self._calc_remaining(quota)
        return total >= amount

    # ── 重置 ────────────────────────────────────────────────────
    def reset_quota(self, agent_name: str, bucket: str = "all") -> None:
        """重置额度使用量.

        Parameters
        ----------
        agent_name : str
            Agent 名称。不存在则静默忽略。
        bucket : str
            ``free`` / ``prepaid`` / ``postpaid`` / ``all``。
        """
        valid = {"free", "prepaid", "postpaid", "all"}
        if bucket not in valid:
            raise ValueError(f"invalid bucket {bucket!r}; expected one of {sorted(valid)}")

        now_iso = datetime.now(timezone.utc).isoformat()
        with self._lock, sqlite_connect(self._db_path) as conn:
            existing = conn.execute(
                "SELECT 1 FROM quota_bucket WHERE agent_name = ?",
                (agent_name,),
            ).fetchone()
            if existing is None:
                logger.debug("[quota_bucket] reset_quota: agent %r not found, no-op", agent_name)
                return

            if bucket == "all":
                conn.execute(
                    """UPDATE quota_bucket SET
                       free_used = 0, prepaid_used = 0, postpaid_used = 0,
                       last_reset = ?
                       WHERE agent_name = ?""",
                    (now_iso, agent_name),
                )
            elif bucket == "free":
                conn.execute(
                    "UPDATE quota_bucket SET free_used = 0, last_reset = ? WHERE agent_name = ?",
                    (now_iso, agent_name),
                )
            elif bucket == "prepaid":
                conn.execute(
                    "UPDATE quota_bucket SET prepaid_used = 0, last_reset = ? WHERE agent_name = ?",
                    (now_iso, agent_name),
                )
            else:  # postpaid
                conn.execute(
                    "UPDATE quota_bucket SET postpaid_used = 0, last_reset = ? WHERE agent_name = ?",
                    (now_iso, agent_name),
                )
        logger.debug("[quota_bucket] reset_quota for %s: bucket=%s", agent_name, bucket)

    # ── 内部工具 ────────────────────────────────────────────────
    @staticmethod
    def _row_to_entry(row: sqlite3.Row) -> QuotaEntry:
        """sqlite3.Row → QuotaEntry."""
        return QuotaEntry(
            free_quota=row["free_quota"],
            free_used=row["free_used"],
            prepaid_quota=row["prepaid_quota"],
            prepaid_used=row["prepaid_used"],
            postpaid_quota=row["postpaid_quota"],
            postpaid_used=row["postpaid_used"],
            reset_period=row["reset_period"],
            last_reset=row["last_reset"],
        )

    @staticmethod
    def _calc_remaining(quota: QuotaEntry) -> int:
        """计算三桶总剩余。"""
        return (
            max(0, quota.free_quota - quota.free_used)
            + max(0, quota.prepaid_quota - quota.prepaid_used)
            + max(0, quota.postpaid_quota - quota.postpaid_used)
        )

    def _maybe_auto_reset(self, agent_name: str, quota: QuotaEntry) -> QuotaEntry:
        """检查是否需要自动重置（按 ``reset_period``）.

        若需要重置，将 ``*_used`` 归零并持久化，返回更新后的 QuotaEntry。
        否则返回原 QuotaEntry。

        重置时机：
          - ``daily``   — 上次重置不在今天
          - ``weekly``  — 上次重置不在本周（ISO 周一为周首）
          - ``monthly`` — 上次重置不在本月
          - ``never``   — 不重置
          - ``last_reset`` 为空时视为需要重置（首次初始化）
        """
        if quota.reset_period == "never":
            return quota

        now = datetime.now(timezone.utc)
        if not quota.last_reset:
            # 首次初始化：记录 last_reset 但不归零 used（保留已有使用量）
            # 仅当 used > 0 时才需要记录，否则无意义
            with self._lock, sqlite_connect(self._db_path) as conn:
                conn.execute(
                    "UPDATE quota_bucket SET last_reset = ? WHERE agent_name = ?",
                    (now.isoformat(), agent_name),
                )
            return quota.model_copy(update={"last_reset": now.isoformat()})

        try:
            last = datetime.fromisoformat(quota.last_reset)
        except (ValueError, TypeError):
            # 损坏的 last_reset：视为需要重置
            last = None

        need_reset = False
        if last is None:
            need_reset = True
        elif quota.reset_period == "daily":
            need_reset = last.date() != now.date()
        elif quota.reset_period == "weekly":
            # ISO 周数 + 年份相同则同一周
            need_reset = (last.isocalendar()[0:2] != now.isocalendar()[0:2])
        elif quota.reset_period == "monthly":
            need_reset = (last.year, last.month) != (now.year, now.month)
        else:
            # 未知周期：不重置
            return quota

        if not need_reset:
            return quota

        # 执行重置
        now_iso = now.isoformat()
        with self._lock, sqlite_connect(self._db_path) as conn:
            conn.execute(
                """UPDATE quota_bucket SET
                   free_used = 0, prepaid_used = 0, postpaid_used = 0,
                   last_reset = ?
                   WHERE agent_name = ?""",
                (now_iso, agent_name),
            )
        logger.debug(
            "[quota_bucket] auto-reset for %s (period=%s)",
            agent_name, quota.reset_period,
        )
        return quota.model_copy(
            update={
                "free_used": 0,
                "prepaid_used": 0,
                "postpaid_used": 0,
                "last_reset": now_iso,
            }
        )