"""接线审计的三处修复（2026-11-10）。

本文件的每条测试都对应一个**实测过的"实现了但从不生效"**缺陷：

1. ``EventBus.subscribe("*", h)`` —— 文档写 "Supports '*' wildcard at end"，
   但原实现只处理 ``前缀.*``，于是裸 ``"*"`` **收不到任何事件**（实测 0 条）。
   ``MAOPEventBusBridge`` 正是靠它订阅全部事件再转进联邦，所以联邦的**第一跳
   永远是断的**——这比"缺跨进程传输层"更底层。
2. ``BlackboardHealthMonitor.record_ks_dispatch`` —— 实现完整但**零调用**，
   健康面 ``dispatches.total/errors/error_rate`` 恒 0。
3. ``record_dead_letter`` —— 同上，``dead_letters`` 恒 0，死信积压看不出来。

另有一条：monitor 此前由 dashboard service **各自 new 一份**，而计数的真实调用点
在 core 层——两份实例各记各的，core 的数永远到不了 snapshot。现改进程级单例。
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from maop.core.reliability.bb_health import (
    BlackboardHealthMonitor,
    get_health_monitor,
    set_health_monitor,
)
from maop.core.reliability.event_bus import Event, EventBus
from maop.core.reliability.message_queue import MessageQueue


@pytest.fixture(autouse=True)
def _clean_monitor():
    set_health_monitor(None)
    yield
    set_health_monitor(None)


# ── ① 裸 "*" 通配 ────────────────────────────────────────────────


class TestBareWildcardSubscription:
    def test_bare_star_receives_every_topic(self) -> None:
        """subscribe("*", h) 必须收到**所有** topic 的事件。

        这是联邦第一跳的命门：MAOPEventBusBridge.attach_federation 内部
        就是 ``self._bus.subscribe("*", self._on_maop_event)``。
        """
        hits: list[str] = []
        bus = EventBus()
        bus.subscribe("*", lambda e: hits.append(e.topic))

        async def _go() -> None:
            await bus.publish(Event(topic="test.topic", data={}))
            await bus.publish(Event(topic="execution.result", data={}))
            await bus.publish(Event(topic="blackboard.write", data={}))
            await asyncio.sleep(0.02)

        asyncio.run(_go())
        assert hits == ["test.topic", "execution.result", "blackboard.write"], (
            f'裸 "*" 未通配：只收到 {hits}；这正是联邦桥接静默为空的原因'
        )

    def test_prefixed_wildcard_still_works(self) -> None:
        """补裸 "*" 不能破坏既有的 "前缀.*" 语义。"""
        hits: list[str] = []
        bus = EventBus()
        bus.subscribe("execution.*", lambda e: hits.append(e.topic))

        async def _go() -> None:
            await bus.publish(Event(topic="execution.result", data={}))
            await bus.publish(Event(topic="test.topic", data={}))
            await asyncio.sleep(0.02)

        asyncio.run(_go())
        assert hits == ["execution.result"]

    def test_exact_topic_unaffected(self) -> None:
        hits: list[str] = []
        bus = EventBus()
        bus.subscribe("a.b", lambda e: hits.append(e.topic))

        async def _go() -> None:
            await bus.publish(Event(topic="a.b", data={}))
            await bus.publish(Event(topic="a.bc", data={}))
            await asyncio.sleep(0.02)

        asyncio.run(_go())
        assert hits == ["a.b"]


# ── ② monitor 单例 ──────────────────────────────────────────────


class TestMonitorSingleton:
    def test_get_returns_same_instance(self) -> None:
        assert get_health_monitor() is get_health_monitor()

    def test_core_and_dashboard_share_one_monitor(self) -> None:
        """core 层记账必须能被 dashboard 的 snapshot 看到（同一实例）。"""
        from maop.core.reliability.bb_trigger_engine import _health

        assert _health() is get_health_monitor()

    def test_reset_then_recreate(self) -> None:
        first = get_health_monitor()
        set_health_monitor(None)
        assert get_health_monitor() is not first


# ── ③ 计数真的进 snapshot ───────────────────────────────────────


class TestCountersReachSnapshot:
    def test_ks_dispatch_reflected_in_snapshot(self) -> None:
        from maop.core.reliability.bb_trigger_engine import _health

        h = _health()
        h.record_ks_dispatch(success=True)
        h.record_ks_dispatch(success=True)
        h.record_ks_dispatch(success=False)

        d = get_health_monitor().snapshot()["dispatches"]
        assert d["total"] == 3
        assert d["errors"] == 1
        assert d["error_rate"] == pytest.approx(1 / 3, rel=1e-6)

    def test_dead_letter_reflected_in_snapshot(self, tmp_path: Path) -> None:
        """走**真实入死信路径**（nack 超限），而不是直接调 record_dead_letter。"""
        mq = MessageQueue(db_path=tmp_path / "q.db")
        mq.enqueue("t", {"x": 1}, ack_timeout_s=0.01, max_retries=0)
        msg = mq.dequeue("t")
        assert msg is not None
        mq.nack(msg.id, error="force dead letter")

        assert get_health_monitor().snapshot()["dead_letters"] >= 1, (
            "入死信未上报健康面 —— dead_letters 会永远显示 0，死信积压看不出来"
        )

    def test_health_record_failure_does_not_break_dispatch(self, monkeypatch) -> None:
        """记账失败绝不能阻断派发。"""
        from maop.core.reliability import bb_trigger_engine as eng

        def _boom() -> None:
            raise RuntimeError("monitor down")

        monkeypatch.setattr(
            "maop.core.reliability.bb_health.get_health_monitor", _boom,
        )
        # _health() 取不到时返回静默替身，调用不抛
        eng._health().record_ks_dispatch(success=True)

    def test_fresh_monitor_starts_at_zero(self) -> None:
        snap = BlackboardHealthMonitor().snapshot()
        assert snap["dispatches"]["total"] == 0
        assert snap["dead_letters"] == 0
