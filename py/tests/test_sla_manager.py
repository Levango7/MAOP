"""SLAManager 白盒测试.

覆盖：SLA 定义 CRUD / SLA 检查 / 告警回调 / 持久化 / 异常安全 / 线程安全。
每个测试使用 tmp_path 隔离 SQLite，不触碰真实 DB。
"""

from __future__ import annotations

import threading
from pathlib import Path

import pytest

from maop.core.agent.ops.health_scorer import HealthScorer
from maop.core.agent.ops.sla_manager import (
    SLADefinition,
    SLAManager,
    SLAStatus,
)


# ── Fixtures ─────────────────────────────────────────────────────


@pytest.fixture
def scorer(tmp_path: Path) -> HealthScorer:
    """使用隔离 tmp_path DB 的全新 HealthScorer。"""
    return HealthScorer(db_path=tmp_path / "health.db", window_size=100)


@pytest.fixture
def sla_manager(scorer: HealthScorer, tmp_path: Path) -> SLAManager:
    """使用隔离 tmp_path DB 的全新 SLAManager。"""
    return SLAManager(health_scorer=scorer, db_path=tmp_path / "sla.db")


# ── 1. SLA 定义 CRUD ─────────────────────────────────────────────


class TestSLADefinition:
    """SLA 定义的设置 / 获取。"""

    def test_define_and_get_sla(self, sla_manager: SLAManager) -> None:
        """定义 SLA 后应能正确获取全部字段。"""
        sla = SLADefinition(
            agent_name="agent-a",
            max_response_time_s=5.0,
            min_success_rate=0.9,
            min_availability=0.95,
            max_cost_per_call=0.5,
        )
        sla_manager.define_sla("agent-a", sla)

        got = sla_manager.get_sla("agent-a")
        assert got is not None
        assert got.agent_name == "agent-a"
        assert got.max_response_time_s == 5.0
        assert got.min_success_rate == 0.9
        assert got.min_availability == 0.95
        assert got.max_cost_per_call == 0.5
        assert got.enabled is True

    def test_get_sla_nonexistent(self, sla_manager: SLAManager) -> None:
        """获取不存在的 SLA 应返回 None。"""
        assert sla_manager.get_sla("nonexistent") is None

    def test_define_sla_overwrites(self, sla_manager: SLAManager) -> None:
        """重复定义同一 Agent SLA 应覆盖旧值（upsert）。"""
        sla_manager.define_sla(
            "agent-a",
            SLADefinition(agent_name="agent-a", max_response_time_s=5.0),
        )
        sla_manager.define_sla(
            "agent-a",
            SLADefinition(agent_name="agent-a", max_response_time_s=3.0),
        )

        got = sla_manager.get_sla("agent-a")
        assert got is not None
        assert got.max_response_time_s == 3.0

    def test_define_sla_aligns_agent_name(self, sla_manager: SLAManager) -> None:
        """define_sla 强制对齐 agent_name。"""
        sla = SLADefinition(agent_name="wrong-name", max_response_time_s=5.0)
        sla_manager.define_sla("correct-name", sla)

        got = sla_manager.get_sla("correct-name")
        assert got is not None
        assert got.agent_name == "correct-name"


# ── 2. SLA 检查 ──────────────────────────────────────────────────


class TestCheckSLA:
    """check_sla 在各种状态下的行为。"""

    def test_check_sla_compliant(
        self, scorer: HealthScorer, sla_manager: SLAManager,
    ) -> None:
        """满足 SLA 时返回 compliant=True。"""
        # 记录快速成功的调用
        for _ in range(100):
            scorer.record_result("agent-a", success=True, response_time_s=0.5)

        sla_manager.define_sla(
            "agent-a",
            SLADefinition(
                agent_name="agent-a",
                max_response_time_s=2.0,
                min_success_rate=0.95,
                min_availability=0.95,
            ),
        )

        status = sla_manager.check_sla("agent-a")
        assert status.compliant is True
        assert status.violations == []
        assert status.current_response_time_s == pytest.approx(0.5)
        assert status.current_success_rate == 1.0

    def test_check_sla_response_time_violation(
        self, scorer: HealthScorer, sla_manager: SLAManager,
    ) -> None:
        """响应时间超限应记录违反项。"""
        scorer.record_result("agent-a", success=True, response_time_s=15.0)

        sla_manager.define_sla(
            "agent-a",
            SLADefinition(
                agent_name="agent-a",
                max_response_time_s=5.0,
                min_success_rate=0.0,  # 不检查成功率
                min_availability=0.0,  # 不检查可用性
            ),
        )

        status = sla_manager.check_sla("agent-a")
        assert status.compliant is False
        assert len(status.violations) == 1
        assert "响应时间" in status.violations[0]

    def test_check_sla_success_rate_violation(
        self, scorer: HealthScorer, sla_manager: SLAManager,
    ) -> None:
        """成功率低于阈值应记录违反项。"""
        # 5 成功 + 5 失败 = 50% 成功率
        for _ in range(5):
            scorer.record_result("agent-a", success=True, response_time_s=0.5)
        for _ in range(5):
            scorer.record_result("agent-a", success=False, response_time_s=0.5)

        sla_manager.define_sla(
            "agent-a",
            SLADefinition(
                agent_name="agent-a",
                max_response_time_s=100.0,  # 不检查响应时间
                min_success_rate=0.95,
                min_availability=0.0,  # 不检查可用性
            ),
        )

        status = sla_manager.check_sla("agent-a")
        assert status.compliant is False
        assert len(status.violations) == 1
        assert "成功率" in status.violations[0]

    def test_check_sla_no_definition(
        self, sla_manager: SLAManager,
    ) -> None:
        """无 SLA 定义的 Agent 视为合规。"""
        status = sla_manager.check_sla("no-sla-agent")
        assert status.compliant is True
        assert status.violations == []

    def test_check_sla_disabled(
        self, scorer: HealthScorer, sla_manager: SLAManager,
    ) -> None:
        """disabled 的 SLA 视为合规。"""
        scorer.record_result("agent-a", success=False, response_time_s=100.0)

        sla_manager.define_sla(
            "agent-a",
            SLADefinition(
                agent_name="agent-a",
                max_response_time_s=1.0,
                min_success_rate=0.99,
                min_availability=0.99,
                enabled=False,
            ),
        )

        status = sla_manager.check_sla("agent-a")
        assert status.compliant is True
        assert status.violations == []

    def test_check_all_sla(
        self, scorer: HealthScorer, sla_manager: SLAManager,
    ) -> None:
        """check_all_sla 检查所有已定义 SLA 的 Agent。"""
        scorer.record_result("agent-a", success=True, response_time_s=0.5)
        scorer.record_result("agent-b", success=False, response_time_s=20.0)

        sla_manager.define_sla(
            "agent-a",
            SLADefinition(agent_name="agent-a", max_response_time_s=5.0),
        )
        sla_manager.define_sla(
            "agent-b",
            SLADefinition(
                agent_name="agent-b",
                max_response_time_s=5.0,
                min_success_rate=0.95,
                min_availability=0.95,
            ),
        )

        statuses = sla_manager.check_all_sla()
        assert len(statuses) == 2
        status_map = {s.agent_name: s for s in statuses}
        assert status_map["agent-a"].compliant is True
        assert status_map["agent-b"].compliant is False


# ── 3. 告警回调 ──────────────────────────────────────────────────


class TestAlertCallbacks:
    """告警回调注册与通知。"""

    def test_register_and_alert(
        self, scorer: HealthScorer, sla_manager: SLAManager,
    ) -> None:
        """不合规时触发告警回调。"""
        scorer.record_result("agent-a", success=False, response_time_s=20.0)

        sla_manager.define_sla(
            "agent-a",
            SLADefinition(
                agent_name="agent-a",
                max_response_time_s=5.0,
                min_success_rate=0.95,
                min_availability=0.95,
            ),
        )

        received: list[SLAStatus] = []
        sla_manager.register_alert_callback(lambda s: received.append(s))

        statuses = sla_manager.check_and_alert()
        assert len(statuses) == 1
        assert statuses[0].compliant is False
        assert len(received) == 1
        assert received[0].agent_name == "agent-a"

    def test_no_alert_when_compliant(
        self, scorer: HealthScorer, sla_manager: SLAManager,
    ) -> None:
        """合规时不触发告警回调。"""
        scorer.record_result("agent-a", success=True, response_time_s=0.5)

        sla_manager.define_sla(
            "agent-a",
            SLADefinition(agent_name="agent-a", max_response_time_s=5.0),
        )

        received: list[SLAStatus] = []
        sla_manager.register_alert_callback(lambda s: received.append(s))

        sla_manager.check_and_alert()
        assert len(received) == 0

    def test_callback_exception_safe(
        self, scorer: HealthScorer, sla_manager: SLAManager,
    ) -> None:
        """单个回调抛异常不影响其他回调和检查结果。"""
        scorer.record_result("agent-a", success=False, response_time_s=20.0)

        sla_manager.define_sla(
            "agent-a",
            SLADefinition(
                agent_name="agent-a",
                max_response_time_s=5.0,
                min_success_rate=0.95,
                min_availability=0.95,
            ),
        )

        received: list[SLAStatus] = []

        def bad_callback(status: SLAStatus) -> None:
            raise RuntimeError("callback failed")

        def good_callback(status: SLAStatus) -> None:
            received.append(status)

        sla_manager.register_alert_callback(bad_callback)
        sla_manager.register_alert_callback(good_callback)

        statuses = sla_manager.check_and_alert()
        assert len(statuses) == 1  # 检查结果不受影响
        assert len(received) == 1  # 好回调仍被调用


# ── 4. 持久化 ────────────────────────────────────────────────────


class TestPersistence:
    """SLA 定义持久化。"""

    def test_persistence_reload(
        self, scorer: HealthScorer, tmp_path: Path,
    ) -> None:
        """SLA 定义持久化到 SQLite，重新加载后保留。"""
        db_path = tmp_path / "sla.db"
        mgr1 = SLAManager(health_scorer=scorer, db_path=db_path)
        mgr1.define_sla(
            "agent-a",
            SLADefinition(
                agent_name="agent-a",
                max_response_time_s=3.0,
                min_success_rate=0.9,
                min_availability=0.95,
                enabled=False,
            ),
        )

        # 用同一 DB 路径创建新实例，验证定义已持久化
        mgr2 = SLAManager(health_scorer=scorer, db_path=db_path)
        got = mgr2.get_sla("agent-a")
        assert got is not None
        assert got.max_response_time_s == 3.0
        assert got.min_success_rate == 0.9
        assert got.min_availability == 0.95
        assert got.enabled is False


# ── 5. 线程安全 ──────────────────────────────────────────────────


class TestThreadSafety:
    """多线程并发操作。"""

    def test_concurrent_define_and_check(
        self, scorer: HealthScorer, sla_manager: SLAManager,
    ) -> None:
        """多线程并发定义和检查 SLA 不应抛异常。"""
        num_threads = 10

        def worker(tid: int) -> None:
            agent = f"agent-{tid}"
            sla_manager.define_sla(
                agent,
                SLADefinition(agent_name=agent, max_response_time_s=5.0),
            )
            scorer.record_result(agent, success=True, response_time_s=0.5)
            sla_manager.check_sla(agent)

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(num_threads)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # 所有 Agent 的 SLA 都应已定义
        statuses = sla_manager.check_all_sla()
        assert len(statuses) == num_threads
        # 全部合规
        assert all(s.compliant for s in statuses)