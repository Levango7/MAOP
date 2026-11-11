"""黑板 KS 派发与死信计数的**端到端**钉子（2026-11-10 接线审计）。

为什么单独一个文件：这里每条测试都必须经过**真实调用链**，而不是直接调计数器。

- KS 计数：``run_until_convergence`` → ``_cycle`` → ``_dispatch`` → 插桩点
- 死信计数：``nack`` 超限 / ``recover_unacked`` 超时回收 → ``_move_to_dead_letter``

只断言 ``record_ks_dispatch`` 自身能加数是不够的——那测的是计数器算术，
测不到插桩点是否可达。插桩若挂在不可达分支上，那种测试照样绿。

知识源用仓内的 ``CallableKnowledgeSource`` 包装裸回调，避免在本文件里再造子类。
"""

from __future__ import annotations

import asyncio
import time
from pathlib import Path

import pytest

from maop.core.reliability.bb_health import get_health_monitor, set_health_monitor
from maop.core.reliability.bb_trigger_engine import (
    BlackboardConvergenceController,
    TriggerRule,
)
from maop.core.reliability.blackboard import Blackboard
from maop.core.reliability.ks_agent_bridge import CallableKnowledgeSource
from maop.core.reliability.message_queue import MessageQueue


@pytest.fixture(autouse=True)
def _clean_monitor():
    set_health_monitor(None)
    yield
    set_health_monitor(None)


def _run_cycle(ks, predicate=None) -> dict:
    """驱动一次真实收敛循环，返回 solve report。"""

    async def _go() -> dict:
        bb = Blackboard()
        await bb.write("evidence", {"fact": 1}, "tester")
        ctrl = BlackboardConvergenceController(bb, max_iterations=2)
        ctrl.register_rule(
            TriggerRule(
                name="fire_once",
                ks=ks,
                predicate=predicate or (lambda _bb, _e: True),
                max_dispatches=1,
            )
        )
        return await ctrl.run_until_convergence()

    return asyncio.run(_go())


class TestKsDispatchCountedEndToEnd:
    def test_success_path(self) -> None:
        hits: list[str] = []

        async def _work(bb, entry) -> None:
            hits.append(entry.entry_id)

        ks = CallableKnowledgeSource(name="ok_ks", executor=_work)
        report = _run_cycle(ks)

        assert hits, "知识源根本没被执行，测试前提不成立"
        assert report["converged"] is False  # 无判据 ⇒ 靠 max_iterations 退出
        d = get_health_monitor().snapshot()["dispatches"]
        assert d["total"] == len(hits), "真派发次数与健康面记数不一致"
        assert d["errors"] == 0

    def test_failure_path(self) -> None:
        async def _boom(bb, entry) -> None:
            raise RuntimeError("knowledge source exploded")

        ks = CallableKnowledgeSource(name="boom_ks", executor=_boom)
        _run_cycle(ks)  # KS 异常被 engine 吞掉，solve 本身不失败

        # 注意：rule.mark_dispatched 只在**成功**分支调用，所以 max_dispatches=1
        # 并不能限制失败重试——max_iterations=2 的循环里它每轮都重试，共 2 次。
        # 这里钉住的是实际行为（失败也计入 total，且计入 errors）。
        d = get_health_monitor().snapshot()["dispatches"]
        assert d["total"] == 2, "失败派发未被计入 total"
        assert d["errors"] == 2, "KS 抛异常却没记 error —— error_rate 会永远是 0"
        assert d["error_rate"] == pytest.approx(1.0)

    def test_zero_dispatch_stays_zero(self) -> None:
        """没有规则可触发时不许凭空造数。"""

        async def _never(bb, entry) -> None:
            raise AssertionError("不该被执行")

        ks = CallableKnowledgeSource(name="never_ks", executor=_never)
        _run_cycle(ks, predicate=lambda _bb, _e: False)

        assert get_health_monitor().snapshot()["dispatches"]["total"] == 0


class TestDeadLetterCountedEndToEnd:
    def test_nack_exhausted_path(self, tmp_path: Path) -> None:
        """nack 重试超限 → 直接入死信。"""
        mq = MessageQueue(db_path=tmp_path / "q.db")
        mq.enqueue("t", {"x": 1}, ack_timeout_s=0.01, max_retries=0)
        msg = mq.dequeue("t")
        assert msg is not None
        mq.nack(msg.id, error="force dead letter")

        assert get_health_monitor().snapshot()["dead_letters"] >= 1, (
            "入死信未上报健康面 —— dead_letters 会永远显示 0，死信积压看不出来"
        )

    def test_timeout_reclaim_path(self, tmp_path: Path) -> None:
        """ack 超时后回收且重试耗尽 —— 生产里更常见的入死信路径。"""
        mq = MessageQueue(db_path=tmp_path / "q.db")
        mq.enqueue("t", {"x": 1}, ack_timeout_s=0.01, max_retries=0)
        got = mq.dequeue("t")
        assert got is not None
        time.sleep(0.05)  # 不 ack，等它超时
        mq.recover_unacked(timeout_buffer=0.0)

        assert get_health_monitor().snapshot()["dead_letters"] >= 1, (
            "超时回收路径入死信未上报健康面"
        )