"""路由决策审计日志（RoutingAuditLogger）单元测试.

覆盖：
  - 记录单条事件
  - 查询全部 / 按 agent 过滤
  - limit 限制
  - 持久化（跨实例）
  - context JSON 序列化/反序列化
  - 自动填充 id / timestamp
  - 并发写入线程安全
  - count 统计
  - limit=0 返回空
  - 保留注入的 id/timestamp
"""

from __future__ import annotations

import threading
from pathlib import Path

import pytest

from maop.core.agent.router.routing_audit import (
    RoutingAuditEvent,
    RoutingAuditLogger,
)
from tests.thread_join_guard import join_all

# ── Fixtures ─────────────────────────────────────────────────────


@pytest.fixture
def audit_logger(tmp_path: Path) -> RoutingAuditLogger:
    """使用隔离 tmp_path DB 的审计日志记录器."""
    return RoutingAuditLogger(db_path=tmp_path / "routing_audit.db")


def _make_event(**kwargs: object) -> RoutingAuditEvent:
    """构造审计事件，默认 route_selected."""
    defaults: dict[str, object] = {
        "event_type": "route_selected",
        "agent_name": "claude-code",
        "strategy": "cost_optimized",
        "reason": "lowest cost",
        "context": {"candidates": 3},
    }
    defaults.update(kwargs)
    return RoutingAuditEvent(**defaults)  # type: ignore[arg-type]


# ── 记录与查询 ───────────────────────────────────────────────────


class TestRoutingAuditLog:
    def test_log_single_event(self, audit_logger: RoutingAuditLogger) -> None:
        """记录单条事件后可查询到."""
        audit_logger.log(_make_event())
        events = audit_logger.query()
        assert len(events) == 1
        assert events[0].event_type == "route_selected"
        assert events[0].agent_name == "claude-code"
        assert events[0].strategy == "cost_optimized"

    def test_query_filter_by_agent(self, audit_logger: RoutingAuditLogger) -> None:
        """按 agent_name 过滤查询."""
        audit_logger.log(_make_event(agent_name="agent_a"))
        audit_logger.log(_make_event(agent_name="agent_b"))
        audit_logger.log(_make_event(agent_name="agent_a"))
        a_events = audit_logger.query(agent_name="agent_a")
        assert len(a_events) == 2
        assert all(e.agent_name == "agent_a" for e in a_events)

    def test_query_limit(self, audit_logger: RoutingAuditLogger) -> None:
        """limit 限制返回条数."""
        for i in range(10):
            audit_logger.log(_make_event(reason=f"event-{i}"))
        events = audit_logger.query(limit=3)
        assert len(events) == 3

    def test_query_limit_zero_returns_empty(
        self, audit_logger: RoutingAuditLogger,
    ) -> None:
        """limit=0 返回空列表."""
        audit_logger.log(_make_event())
        assert audit_logger.query(limit=0) == []

    def test_query_ordered_by_timestamp_desc(
        self, audit_logger: RoutingAuditLogger,
    ) -> None:
        """查询结果按时间戳倒序（最新在前）."""
        # 注入递增的 timestamp
        audit_logger.log(_make_event(
            id="e1", timestamp="2026-01-01T00:00:00+00:00", reason="first",
        ))
        audit_logger.log(_make_event(
            id="e2", timestamp="2026-01-02T00:00:00+00:00", reason="second",
        ))
        events = audit_logger.query()
        assert len(events) == 2
        assert events[0].id == "e2"  # 最新在前
        assert events[1].id == "e1"


# ── context JSON 序列化 ──────────────────────────────────────────


class TestRoutingAuditContext:
    def test_context_roundtrip(self, audit_logger: RoutingAuditLogger) -> None:
        """context dict 序列化/反序列化保持一致."""
        ctx = {"candidates": 5, "excluded": ["a", "b"], "nested": {"k": 1}}
        audit_logger.log(_make_event(context=ctx))
        events = audit_logger.query()
        assert events[0].context == ctx

    def test_context_empty(self, audit_logger: RoutingAuditLogger) -> None:
        """空 context 正确处理."""
        audit_logger.log(_make_event(context={}))
        events = audit_logger.query()
        assert events[0].context == {}


# ── 自动填充 id / timestamp ──────────────────────────────────────


class TestRoutingAuditAutoFill:
    def test_auto_fill_id_and_timestamp(
        self, audit_logger: RoutingAuditLogger,
    ) -> None:
        """未提供 id/timestamp 时自动填充."""
        event = _make_event()
        assert event.id == ""
        assert event.timestamp == ""
        audit_logger.log(event)
        # log 后 event 被填充
        assert event.id != ""
        assert event.timestamp != ""
        # 查询回来一致
        events = audit_logger.query()
        assert events[0].id == event.id
        assert events[0].timestamp == event.timestamp

    def test_preserve_provided_id_and_timestamp(
        self, audit_logger: RoutingAuditLogger,
    ) -> None:
        """提供 id/timestamp 时保留原值."""
        audit_logger.log(_make_event(
            id="custom-id", timestamp="2026-09-12T10:00:00+00:00",
        ))
        events = audit_logger.query()
        assert events[0].id == "custom-id"
        assert events[0].timestamp == "2026-09-12T10:00:00+00:00"


# ── 持久化（跨实例）─────────────────────────────────────────────


class TestRoutingAuditPersistence:
    def test_persistence_across_instances(
        self, tmp_path: Path,
    ) -> None:
        """新实例连接同一 DB 可读到历史记录."""
        db_path = tmp_path / "persist.db"
        logger1 = RoutingAuditLogger(db_path=db_path)
        logger1.log(_make_event(agent_name="alpha", reason="first"))
        logger1.log(_make_event(agent_name="beta", reason="second"))

        # 新实例同一 DB
        logger2 = RoutingAuditLogger(db_path=db_path)
        events = logger2.query()
        assert len(events) == 2
        agents = {e.agent_name for e in events}
        assert agents == {"alpha", "beta"}


# ── 并发写入 ─────────────────────────────────────────────────────


class TestRoutingAuditConcurrency:
    @pytest.mark.timeout(240)
    def test_concurrent_writes_thread_safe(
        self, audit_logger: RoutingAuditLogger,
    ) -> None:
        """多线程并发写入不丢数据、不损坏 DB."""
        threads = 10
        per_thread = 20
        barrier = threading.Barrier(threads)

        def worker(idx: int) -> None:
            barrier.wait()
            for i in range(per_thread):
                audit_logger.log(_make_event(
                    agent_name=f"agent_{idx}",
                    reason=f"event-{i}",
                ))

        ts = [threading.Thread(target=worker, args=(i,)) for i in range(threads)]
        for t in ts:
            t.start()
        join_all(ts, 120.0)

        total = audit_logger.count()
        assert total == threads * per_thread


# ── count 统计 ───────────────────────────────────────────────────


class TestRoutingAuditCount:
    def test_count_all(self, audit_logger: RoutingAuditLogger) -> None:
        """count 返回全部条数."""
        for i in range(5):
            audit_logger.log(_make_event(reason=f"r{i}"))
        assert audit_logger.count() == 5

    def test_count_by_agent(self, audit_logger: RoutingAuditLogger) -> None:
        """count 按 agent 过滤."""
        audit_logger.log(_make_event(agent_name="a"))
        audit_logger.log(_make_event(agent_name="a"))
        audit_logger.log(_make_event(agent_name="b"))
        assert audit_logger.count(agent_name="a") == 2
        assert audit_logger.count(agent_name="b") == 1
        assert audit_logger.count(agent_name="c") == 0


# ── 多种事件类型 ────────────────────────────────────────────────


class TestRoutingAuditEventTypes:
    def test_all_event_types(self, audit_logger: RoutingAuditLogger) -> None:
        """记录各种事件类型并查询."""
        for et in [
            "route_selected", "route_failed", "fallback_switched",
            "concurrent_rejected", "rate_limited",
        ]:
            audit_logger.log(_make_event(event_type=et))
        events = audit_logger.query()
        assert len(events) == 5
        types = {e.event_type for e in events}
        assert types == {
            "route_selected", "route_failed", "fallback_switched",
            "concurrent_rejected", "rate_limited",
        }