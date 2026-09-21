"""QuotaBucket 白盒测试.

覆盖：QuotaEntry 构造 / CRUD / 三桶消耗 / 退还 / 剩余查询 / 重置 / 自动重置。
每个测试使用 tmp_path 隔离 SQLite，不触碰真实 DB。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from maop.core.agent.billing.quota_bucket import (
    ConsumeResult,  # noqa: F401
    QuotaBucket,
    QuotaEntry,
)

# ── Fixtures ─────────────────────────────────────────────────────


@pytest.fixture
def bucket(tmp_path: Path) -> QuotaBucket:
    """使用隔离 tmp_path DB 的全新 QuotaBucket。"""
    return QuotaBucket(db_path=tmp_path / "quota.db")


# ── 1. TestQuotaEntry ────────────────────────────────────────────


class TestQuotaEntry:
    """QuotaEntry 构造与默认值。"""

    def test_default_values(self) -> None:
        """默认构造所有字段为 0 / monthly / 空。"""
        entry = QuotaEntry()
        assert entry.free_quota == 0
        assert entry.free_used == 0
        assert entry.prepaid_quota == 0
        assert entry.prepaid_used == 0
        assert entry.postpaid_quota == 0
        assert entry.postpaid_used == 0
        assert entry.reset_period == "monthly"
        assert entry.last_reset == ""

    def test_custom_values(self) -> None:
        """自定义值构造。"""
        entry = QuotaEntry(
            free_quota=1000,
            prepaid_quota=5000,
            postpaid_quota=10000,
            reset_period="daily",
        )
        assert entry.free_quota == 1000
        assert entry.prepaid_quota == 5000
        assert entry.postpaid_quota == 10000
        assert entry.reset_period == "daily"

    def test_negative_quota_rejected(self) -> None:
        """负 quota 应被 Pydantic 拒绝。"""
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            QuotaEntry(free_quota=-1)

    def test_negative_used_rejected(self) -> None:
        """负 used 应被 Pydantic 拒绝。"""
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            QuotaEntry(free_used=-1)


# ── 2. TestQuotaBucketCRUD ───────────────────────────────────────


class TestQuotaBucketCRUD:
    """设置 / 获取 / 不存在。"""

    def test_get_nonexistent_returns_empty(self, bucket: QuotaBucket) -> None:
        """获取不存在的 Agent 返回空 QuotaEntry。"""
        entry = bucket.get_quota("ghost")
        assert entry.free_quota == 0
        assert entry.prepaid_quota == 0
        assert entry.postpaid_quota == 0

    def test_set_then_get(self, bucket: QuotaBucket) -> None:
        """设置后可获取。"""
        bucket.set_quota("agent_a", QuotaEntry(free_quota=100, prepaid_quota=200))
        entry = bucket.get_quota("agent_a")
        assert entry.free_quota == 100
        assert entry.prepaid_quota == 200
        assert entry.postpaid_quota == 0

    def test_set_quota_preserves_used(self, bucket: QuotaBucket) -> None:
        """set_quota 已存在 Agent 时保留 used 值。"""
        bucket.set_quota("agent_a", QuotaEntry(free_quota=100))
        bucket.consume("agent_a", 30)
        # 更新 quota 配置
        bucket.set_quota("agent_a", QuotaEntry(free_quota=200))
        entry = bucket.get_quota("agent_a")
        assert entry.free_quota == 200
        assert entry.free_used == 30  # 保留已用

    def test_set_quota_new_uses_provided_used(self, bucket: QuotaBucket) -> None:
        """set_quota 新 Agent 时使用 quota 中的 used 值。"""
        bucket.set_quota("agent_a", QuotaEntry(free_quota=100, free_used=20))
        entry = bucket.get_quota("agent_a")
        assert entry.free_quota == 100
        assert entry.free_used == 20


# ── 3. TestQuotaBucketConsume ────────────────────────────────────


class TestQuotaBucketConsume:
    """三桶消耗：纯免费 / 纯预付 / 纯后付 / 混合 / 不足 / 零消耗。"""

    def test_consume_pure_free(self, bucket: QuotaBucket) -> None:
        """纯免费桶消耗。"""
        bucket.set_quota("a", QuotaEntry(free_quota=100))
        result = bucket.consume("a", 30)
        assert result.success
        assert result.consumed == 30
        assert result.from_free == 30
        assert result.from_prepaid == 0
        assert result.from_postpaid == 0
        assert result.remaining_total == 70

    def test_consume_pure_prepaid(self, bucket: QuotaBucket) -> None:
        """纯预付桶消耗。"""
        bucket.set_quota("a", QuotaEntry(prepaid_quota=100))
        result = bucket.consume("a", 40)
        assert result.success
        assert result.from_free == 0
        assert result.from_prepaid == 40
        assert result.from_postpaid == 0

    def test_consume_pure_postpaid(self, bucket: QuotaBucket) -> None:
        """纯后付桶消耗。"""
        bucket.set_quota("a", QuotaEntry(postpaid_quota=100))
        result = bucket.consume("a", 50)
        assert result.success
        assert result.from_free == 0
        assert result.from_prepaid == 0
        assert result.from_postpaid == 50

    def test_consume_mixed_free_then_prepaid(self, bucket: QuotaBucket) -> None:
        """混合消耗：免费不足，溢出到预付。"""
        bucket.set_quota("a", QuotaEntry(free_quota=30, prepaid_quota=100))
        result = bucket.consume("a", 50)
        assert result.success
        assert result.from_free == 30
        assert result.from_prepaid == 20
        assert result.from_postpaid == 0

    def test_consume_mixed_all_three(self, bucket: QuotaBucket) -> None:
        """混合消耗：免费 + 预付 + 后付。"""
        bucket.set_quota("a", QuotaEntry(free_quota=10, prepaid_quota=20, postpaid_quota=30))
        result = bucket.consume("a", 50)
        assert result.success
        assert result.from_free == 10
        assert result.from_prepaid == 20
        assert result.from_postpaid == 20
        assert result.remaining_total == 10

    def test_consume_insufficient(self, bucket: QuotaBucket) -> None:
        """额度不足返回失败，不部分扣减。"""
        bucket.set_quota("a", QuotaEntry(free_quota=10, prepaid_quota=10))
        result = bucket.consume("a", 100)
        assert not result.success
        assert result.consumed == 0
        assert "insufficient" in result.reason
        # 验证未扣减
        remaining = bucket.get_remaining("a")
        assert remaining["free"] == 10
        assert remaining["prepaid"] == 10

    def test_consume_zero_amount(self, bucket: QuotaBucket) -> None:
        """零消耗返回成功。"""
        bucket.set_quota("a", QuotaEntry(free_quota=100))
        result = bucket.consume("a", 0)
        assert result.success
        assert result.consumed == 0

    def test_consume_negative_amount(self, bucket: QuotaBucket) -> None:
        """负消耗返回失败。"""
        bucket.set_quota("a", QuotaEntry(free_quota=100))
        result = bucket.consume("a", -10)
        assert not result.success
        assert "negative" in result.reason

    def test_consume_exact_total(self, bucket: QuotaBucket) -> None:
        """消耗恰好等于总剩余。"""
        bucket.set_quota("a", QuotaEntry(free_quota=30, prepaid_quota=70))
        result = bucket.consume("a", 100)
        assert result.success
        assert result.remaining_total == 0

    def test_consume_persists(self, bucket: QuotaBucket) -> None:
        """消耗后持久化到 DB。"""
        bucket.set_quota("a", QuotaEntry(free_quota=100))
        bucket.consume("a", 40)
        # 新实例读取同一 DB
        bucket2 = QuotaBucket(db_path=bucket._db_path)
        entry = bucket2.get_quota("a")
        assert entry.free_used == 40


# ── 4. TestQuotaBucketRefund ─────────────────────────────────────


class TestQuotaBucketRefund:
    """退还 / 自动桶 / 指定桶。"""

    def test_refund_to_specified_bucket(self, bucket: QuotaBucket) -> None:
        """退还到指定桶。"""
        bucket.set_quota("a", QuotaEntry(free_quota=100))
        bucket.consume("a", 50)
        bucket.refund("a", 20, bucket="free")
        entry = bucket.get_quota("a")
        assert entry.free_used == 30

    def test_refund_auto_to_postpaid(self, bucket: QuotaBucket) -> None:
        """auto 退还优先到后付桶（与消耗顺序相反）。"""
        bucket.set_quota("a", QuotaEntry(free_quota=10, postpaid_quota=100))
        bucket.consume("a", 50)  # free=10, postpaid=40
        bucket.refund("a", 20, bucket="auto")
        entry = bucket.get_quota("a")
        assert entry.postpaid_used == 20  # 40 - 20

    def test_refund_auto_to_prepaid(self, bucket: QuotaBucket) -> None:
        """auto 退到预付桶（后付为 0 时）。"""
        bucket.set_quota("a", QuotaEntry(free_quota=10, prepaid_quota=100))
        bucket.consume("a", 50)  # free=10, prepaid=40
        bucket.refund("a", 20, bucket="auto")
        entry = bucket.get_quota("a")
        assert entry.prepaid_used == 20

    def test_refund_auto_to_free(self, bucket: QuotaBucket) -> None:
        """auto 退到免费桶（后付/预付均为 0 时）。"""
        bucket.set_quota("a", QuotaEntry(free_quota=100))
        bucket.consume("a", 50)
        bucket.refund("a", 20, bucket="auto")
        entry = bucket.get_quota("a")
        assert entry.free_used == 30

    def test_refund_auto_no_used_no_op(self, bucket: QuotaBucket) -> None:
        """auto 退还时若无 used，无操作。"""
        bucket.set_quota("a", QuotaEntry(free_quota=100))
        bucket.refund("a", 20, bucket="auto")  # 无 used
        entry = bucket.get_quota("a")
        assert entry.free_used == 0

    def test_refund_clamps_to_zero(self, bucket: QuotaBucket) -> None:
        """退还量超过 used 时钳制到 0。"""
        bucket.set_quota("a", QuotaEntry(free_quota=100))
        bucket.consume("a", 30)
        bucket.refund("a", 100, bucket="free")
        entry = bucket.get_quota("a")
        assert entry.free_used == 0

    def test_refund_invalid_bucket_raises(self, bucket: QuotaBucket) -> None:
        """无效桶名抛 ValueError。"""
        bucket.set_quota("a", QuotaEntry(free_quota=100))
        with pytest.raises(ValueError):
            bucket.refund("a", 10, bucket="invalid")

    def test_refund_zero_amount_no_op(self, bucket: QuotaBucket) -> None:
        """退还 0 无操作。"""
        bucket.set_quota("a", QuotaEntry(free_quota=100))
        bucket.consume("a", 30)
        bucket.refund("a", 0)
        entry = bucket.get_quota("a")
        assert entry.free_used == 30


# ── 5. TestQuotaBucketRemaining ──────────────────────────────────


class TestQuotaBucketRemaining:
    """各桶剩余 / 总剩余。"""

    def test_get_remaining_fresh(self, bucket: QuotaBucket) -> None:
        """新设置额度的剩余。"""
        bucket.set_quota("a", QuotaEntry(free_quota=100, prepaid_quota=200, postpaid_quota=300))
        remaining = bucket.get_remaining("a")
        assert remaining == {"free": 100, "prepaid": 200, "postpaid": 300}

    def test_get_remaining_after_consume(self, bucket: QuotaBucket) -> None:
        """消耗后的剩余。"""
        bucket.set_quota("a", QuotaEntry(free_quota=100, prepaid_quota=200))
        bucket.consume("a", 150)
        remaining = bucket.get_remaining("a")
        assert remaining["free"] == 0
        assert remaining["prepaid"] == 150

    def test_get_remaining_nonexistent(self, bucket: QuotaBucket) -> None:
        """不存在的 Agent 返回全 0。"""
        remaining = bucket.get_remaining("ghost")
        assert remaining == {"free": 0, "prepaid": 0, "postpaid": 0}

    def test_check_quota_sufficient(self, bucket: QuotaBucket) -> None:
        """check_quota 足够返回 True。"""
        bucket.set_quota("a", QuotaEntry(free_quota=100))
        assert bucket.check_quota("a", 100)
        assert bucket.check_quota("a", 50)

    def test_check_quota_insufficient(self, bucket: QuotaBucket) -> None:
        """check_quota 不足返回 False。"""
        bucket.set_quota("a", QuotaEntry(free_quota=100))
        assert not bucket.check_quota("a", 101)

    def test_check_quota_zero_always_true(self, bucket: QuotaBucket) -> None:
        """check_quota 0 或负始终 True。"""
        bucket.set_quota("a", QuotaEntry(free_quota=0))
        assert bucket.check_quota("a", 0)
        assert bucket.check_quota("a", -10)


# ── 6. TestQuotaBucketReset ──────────────────────────────────────


class TestQuotaBucketReset:
    """重置单桶 / 重置全部 / 自动重置。"""

    def test_reset_single_bucket_free(self, bucket: QuotaBucket) -> None:
        """重置免费桶。"""
        bucket.set_quota("a", QuotaEntry(free_quota=100, prepaid_quota=100))
        bucket.consume("a", 50)  # free=50, prepaid=0
        bucket.reset_quota("a", bucket="free")
        entry = bucket.get_quota("a")
        assert entry.free_used == 0
        # prepaid 不受影响
        assert entry.prepaid_used == 0

    def test_reset_single_bucket_prepaid(self, bucket: QuotaBucket) -> None:
        """重置预付桶。"""
        bucket.set_quota("a", QuotaEntry(free_quota=10, prepaid_quota=100))
        bucket.consume("a", 50)  # free=10, prepaid=40
        bucket.reset_quota("a", bucket="prepaid")
        entry = bucket.get_quota("a")
        assert entry.prepaid_used == 0
        assert entry.free_used == 10  # 不受影响

    def test_reset_all_buckets(self, bucket: QuotaBucket) -> None:
        """重置全部桶。"""
        bucket.set_quota("a", QuotaEntry(free_quota=10, prepaid_quota=10, postpaid_quota=10))
        bucket.consume("a", 25)  # free=10, prepaid=10, postpaid=5
        bucket.reset_quota("a", bucket="all")
        entry = bucket.get_quota("a")
        assert entry.free_used == 0
        assert entry.prepaid_used == 0
        assert entry.postpaid_used == 0

    def test_reset_updates_last_reset(self, bucket: QuotaBucket) -> None:
        """重置更新 last_reset 时间戳。"""
        bucket.set_quota("a", QuotaEntry(free_quota=100))
        bucket.consume("a", 10)
        bucket.reset_quota("a")
        entry = bucket.get_quota("a")
        assert entry.last_reset != ""

    def test_reset_nonexistent_no_op(self, bucket: QuotaBucket) -> None:
        """重置不存在的 Agent 静默忽略。"""
        bucket.reset_quota("ghost")  # 不抛异常

    def test_reset_invalid_bucket_raises(self, bucket: QuotaBucket) -> None:
        """无效桶名抛 ValueError。"""
        bucket.set_quota("a", QuotaEntry(free_quota=100))
        with pytest.raises(ValueError):
            bucket.reset_quota("a", bucket="invalid")

    def test_auto_reset_never_period(self, bucket: QuotaBucket) -> None:
        """reset_period=never 不自动重置。"""
        bucket.set_quota("a", QuotaEntry(free_quota=100, reset_period="never"))
        bucket.consume("a", 50)
        # 触发 _maybe_auto_reset（通过再次 consume）
        bucket.consume("a", 10)
        entry = bucket.get_quota("a")
        assert entry.free_used == 60  # 未重置

    def test_auto_reset_monthly_same_month(self, bucket: QuotaBucket) -> None:
        """同一月内不重置。"""
        now = datetime.now(timezone.utc)
        bucket.set_quota("a", QuotaEntry(free_quota=100, reset_period="monthly"))
        bucket.consume("a", 30)
        # 手动设置 last_reset 为当前月初
        with bucket._lock:
            from maop.core.backends.db_utils import sqlite_connect
            with sqlite_connect(bucket._db_path) as conn:
                conn.execute(
                    "UPDATE quota_bucket SET last_reset = ? WHERE agent_name = ?",
                    (now.isoformat(), "a"),
                )
        bucket.consume("a", 20)
        entry = bucket.get_quota("a")
        assert entry.free_used == 50  # 未重置

    def test_auto_reset_monthly_different_month(self, bucket: QuotaBucket) -> None:
        """跨月自动重置。"""
        old = datetime.now(timezone.utc) - timedelta(days=35)
        bucket.set_quota("a", QuotaEntry(free_quota=100, reset_period="monthly"))
        bucket.consume("a", 30)
        # 手动设置 last_reset 为上月
        with bucket._lock:
            from maop.core.backends.db_utils import sqlite_connect
            with sqlite_connect(bucket._db_path) as conn:
                conn.execute(
                    "UPDATE quota_bucket SET last_reset = ? WHERE agent_name = ?",
                    (old.isoformat(), "a"),
                )
        # 下次 consume 触发自动重置
        result = bucket.consume("a", 20)
        assert result.success
        entry = bucket.get_quota("a")
        assert entry.free_used == 20  # 重置后只扣 20