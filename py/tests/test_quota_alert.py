"""QuotaAlertManager 白盒测试.

覆盖：预警配置 CRUD / 阈值检查 / 回调通知 / 持久化 / 异常安全。
每个测试使用 tmp_path 隔离 SQLite，不触碰真实 DB。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from maop.core.agent.billing.quota_alert import (
    AlertEvent,
    QuotaAlertManager,
    QuotaThreshold,
)
from maop.core.agent.billing.quota_bucket import (
    QuotaBucket,
    QuotaEntry,
)


# ── Fixtures ─────────────────────────────────────────────────────


@pytest.fixture
def bucket(tmp_path: Path) -> QuotaBucket:
    """使用隔离 tmp_path DB 的全新 QuotaBucket。"""
    return QuotaBucket(db_path=tmp_path / "quota.db")


@pytest.fixture
def manager(bucket: QuotaBucket, tmp_path: Path) -> QuotaAlertManager:
    """使用隔离 tmp_path DB 的全新 QuotaAlertManager。"""
    return QuotaAlertManager(quota_bucket=bucket, db_path=tmp_path / "alert.db")


# ── 1. 预警配置 CRUD ──────────────────────────────────────────────


class TestAlertConfig:
    """预警配置的设置 / 获取 / 移除。"""

    def test_set_and_get_alert(self, manager: QuotaAlertManager) -> None:
        """设置预警后应能正确获取全部字段。"""
        threshold = QuotaThreshold(
            agent_name="agent-a",
            free_threshold=100,
            prepaid_threshold=200,
            postpaid_threshold=300,
            total_threshold=500,
        )
        manager.set_alert("agent-a", threshold)

        got = manager.get_alert("agent-a")
        assert got is not None
        assert got.agent_name == "agent-a"
        assert got.free_threshold == 100
        assert got.prepaid_threshold == 200
        assert got.postpaid_threshold == 300
        assert got.total_threshold == 500
        assert got.enabled is True

    def test_remove_alert(self, manager: QuotaAlertManager) -> None:
        """移除预警后获取应返回 None。"""
        manager.set_alert(
            "agent-a",
            QuotaThreshold(agent_name="agent-a", free_threshold=100),
        )
        assert manager.get_alert("agent-a") is not None

        manager.remove_alert("agent-a")
        assert manager.get_alert("agent-a") is None

    def test_get_alert_nonexistent(self, manager: QuotaAlertManager) -> None:
        """获取不存在的预警应返回 None。"""
        assert manager.get_alert("nonexistent") is None

    def test_set_alert_overwrites(self, manager: QuotaAlertManager) -> None:
        """重复设置同一 Agent 预警应覆盖旧值（upsert）。"""
        manager.set_alert(
            "agent-a",
            QuotaThreshold(agent_name="agent-a", free_threshold=100),
        )
        manager.set_alert(
            "agent-a",
            QuotaThreshold(agent_name="agent-a", free_threshold=200),
        )

        got = manager.get_alert("agent-a")
        assert got is not None
        assert got.free_threshold == 200


# ── 2. 阈值检查 ──────────────────────────────────────────────────


class TestCheckAlerts:
    """check_alerts 在各种额度状态下的行为。"""

    def test_check_no_alert(
        self, bucket: QuotaBucket, manager: QuotaAlertManager,
    ) -> None:
        """额度充足时不触发任何预警。"""
        bucket.set_quota(
            "agent-a",
            QuotaEntry(free_quota=1000, prepaid_quota=1000, postpaid_quota=1000),
        )
        manager.set_alert(
            "agent-a",
            QuotaThreshold(
                agent_name="agent-a",
                free_threshold=100,
                prepaid_threshold=100,
                postpaid_threshold=100,
                total_threshold=300,
            ),
        )

        events = manager.check_alerts()
        assert events == []

    def test_check_free_low(
        self, bucket: QuotaBucket, manager: QuotaAlertManager,
    ) -> None:
        """免费桶低于阈值时触发 free_low 预警。"""
        bucket.set_quota("agent-a", QuotaEntry(free_quota=100, free_used=80))  # 剩余 20
        manager.set_alert(
            "agent-a",
            QuotaThreshold(agent_name="agent-a", free_threshold=50),  # 20 <= 50 触发
        )

        events = manager.check_alerts()
        assert len(events) == 1
        assert events[0].alert_type == "free_low"
        assert events[0].agent_name == "agent-a"
        assert events[0].current_value == 20
        assert events[0].threshold == 50
        assert events[0].timestamp  # 非空
        assert events[0].message  # 非空

    def test_check_prepaid_low(
        self, bucket: QuotaBucket, manager: QuotaAlertManager,
    ) -> None:
        """预付桶低于阈值时触发 prepaid_low 预警。"""
        bucket.set_quota("agent-a", QuotaEntry(prepaid_quota=100, prepaid_used=80))  # 剩余 20
        manager.set_alert(
            "agent-a",
            QuotaThreshold(agent_name="agent-a", prepaid_threshold=50),
        )

        events = manager.check_alerts()
        assert len(events) == 1
        assert events[0].alert_type == "prepaid_low"
        assert events[0].current_value == 20
        assert events[0].threshold == 50

    def test_check_postpaid_low(
        self, bucket: QuotaBucket, manager: QuotaAlertManager,
    ) -> None:
        """后付桶低于阈值时触发 postpaid_low 预警。"""
        bucket.set_quota("agent-a", QuotaEntry(postpaid_quota=100, postpaid_used=80))  # 剩余 20
        manager.set_alert(
            "agent-a",
            QuotaThreshold(agent_name="agent-a", postpaid_threshold=50),
        )

        events = manager.check_alerts()
        assert len(events) == 1
        assert events[0].alert_type == "postpaid_low"
        assert events[0].current_value == 20
        assert events[0].threshold == 50

    def test_check_total_low(
        self, bucket: QuotaBucket, manager: QuotaAlertManager,
    ) -> None:
        """总额度低于阈值时触发 total_low 预警。"""
        bucket.set_quota(
            "agent-a",
            QuotaEntry(
                free_quota=100, free_used=80,  # 剩余 20
                prepaid_quota=100, prepaid_used=80,  # 剩余 20
            ),
        )  # 总剩余 40
        manager.set_alert(
            "agent-a",
            QuotaThreshold(agent_name="agent-a", total_threshold=50),  # 40 <= 50 触发
        )

        events = manager.check_alerts()
        assert len(events) == 1
        assert events[0].alert_type == "total_low"
        assert events[0].current_value == 40
        assert events[0].threshold == 50

    def test_check_multiple_alerts(
        self, bucket: QuotaBucket, manager: QuotaAlertManager,
    ) -> None:
        """多个 Agent 同时触发预警。"""
        bucket.set_quota("agent-a", QuotaEntry(free_quota=100, free_used=80))  # 剩余 20
        bucket.set_quota("agent-b", QuotaEntry(prepaid_quota=100, prepaid_used=90))  # 剩余 10
        manager.set_alert(
            "agent-a",
            QuotaThreshold(agent_name="agent-a", free_threshold=50),
        )
        manager.set_alert(
            "agent-b",
            QuotaThreshold(agent_name="agent-b", prepaid_threshold=20),
        )

        events = manager.check_alerts()
        assert len(events) == 2
        types = {e.alert_type for e in events}
        agents = {e.agent_name for e in events}
        assert types == {"free_low", "prepaid_low"}
        assert agents == {"agent-a", "agent-b"}

    def test_check_disabled_alert(
        self, bucket: QuotaBucket, manager: QuotaAlertManager,
    ) -> None:
        """disabled 的预警不触发。"""
        bucket.set_quota("agent-a", QuotaEntry(free_quota=100, free_used=80))  # 剩余 20
        manager.set_alert(
            "agent-a",
            QuotaThreshold(
                agent_name="agent-a",
                free_threshold=50,
                enabled=False,
            ),
        )

        events = manager.check_alerts()
        assert events == []

    def test_check_threshold_zero_not_monitored(
        self, bucket: QuotaBucket, manager: QuotaAlertManager,
    ) -> None:
        """阈值为 0 的桶不监控，即使额度耗尽也不触发。"""
        bucket.set_quota("agent-a", QuotaEntry(free_quota=0))  # 剩余 0
        manager.set_alert(
            "agent-a",
            QuotaThreshold(agent_name="agent-a", free_threshold=0),  # 0 = 不监控
        )

        events = manager.check_alerts()
        assert events == []


# ── 3. 回调通知 ──────────────────────────────────────────────────


class TestCallbacks:
    """回调注册与通知。"""

    def test_register_callback(
        self, bucket: QuotaBucket, manager: QuotaAlertManager,
    ) -> None:
        """注册的回调在 check_and_notify 时被正确调用。"""
        bucket.set_quota("agent-a", QuotaEntry(free_quota=100, free_used=80))  # 剩余 20
        manager.set_alert(
            "agent-a",
            QuotaThreshold(agent_name="agent-a", free_threshold=50),
        )

        received: list[AlertEvent] = []
        manager.register_callback(lambda e: received.append(e))

        events = manager.check_and_notify()
        assert len(events) == 1
        assert len(received) == 1
        assert received[0] is events[0]

    def test_check_and_notify(
        self, bucket: QuotaBucket, manager: QuotaAlertManager,
    ) -> None:
        """check_and_notify 集成检查与通知：多回调均被调用。"""
        bucket.set_quota("agent-a", QuotaEntry(free_quota=100, free_used=80))
        manager.set_alert(
            "agent-a",
            QuotaThreshold(agent_name="agent-a", free_threshold=50),
        )

        received_a: list[AlertEvent] = []
        received_b: list[AlertEvent] = []

        def callback_a(event: AlertEvent) -> None:
            received_a.append(event)

        def callback_b(event: AlertEvent) -> None:
            received_b.append(event)

        manager.register_callback(callback_a)
        manager.register_callback(callback_b)

        events = manager.check_and_notify()
        assert len(events) == 1
        assert len(received_a) == 1
        assert len(received_b) == 1
        assert received_a[0].alert_type == "free_low"
        assert received_b[0].alert_type == "free_low"

    def test_callback_exception_safe(
        self, bucket: QuotaBucket, manager: QuotaAlertManager,
    ) -> None:
        """单个回调抛异常不影响其他回调和检查结果。"""
        bucket.set_quota("agent-a", QuotaEntry(free_quota=100, free_used=80))
        manager.set_alert(
            "agent-a",
            QuotaThreshold(agent_name="agent-a", free_threshold=50),
        )

        received: list[AlertEvent] = []

        def bad_callback(event: AlertEvent) -> None:
            raise RuntimeError("callback failed")

        def good_callback(event: AlertEvent) -> None:
            received.append(event)

        manager.register_callback(bad_callback)
        manager.register_callback(good_callback)

        events = manager.check_and_notify()
        assert len(events) == 1  # 检查结果不受影响
        assert len(received) == 1  # 好回调仍被调用


# ── 4. 持久化 ────────────────────────────────────────────────────


class TestPersistence:
    """预警配置持久化。"""

    def test_persistence(self, bucket: QuotaBucket, tmp_path: Path) -> None:
        """预警配置持久化到 SQLite，重新加载后配置保留。"""
        db_path = tmp_path / "alert.db"
        manager1 = QuotaAlertManager(quota_bucket=bucket, db_path=db_path)
        manager1.set_alert(
            "agent-a",
            QuotaThreshold(
                agent_name="agent-a",
                free_threshold=100,
                prepaid_threshold=200,
                total_threshold=500,
                enabled=False,
            ),
        )

        # 用同一 DB 路径创建新实例，验证配置已持久化
        manager2 = QuotaAlertManager(quota_bucket=bucket, db_path=db_path)
        got = manager2.get_alert("agent-a")
        assert got is not None
        assert got.free_threshold == 100
        assert got.prepaid_threshold == 200
        assert got.total_threshold == 500
        assert got.enabled is False

    def test_persistence_removed(
        self, bucket: QuotaBucket, tmp_path: Path,
    ) -> None:
        """移除的预警在重新加载后确实不存在。"""
        db_path = tmp_path / "alert.db"
        manager1 = QuotaAlertManager(quota_bucket=bucket, db_path=db_path)
        manager1.set_alert(
            "agent-a",
            QuotaThreshold(agent_name="agent-a", free_threshold=100),
        )
        manager1.remove_alert("agent-a")

        manager2 = QuotaAlertManager(quota_bucket=bucket, db_path=db_path)
        assert manager2.get_alert("agent-a") is None