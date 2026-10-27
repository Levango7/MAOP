"""Stage 3/P2：Supervisor 组合拆分验证（unittest，无 pytest）。

覆盖：
  - Supervisor 组合 FailurePatternDetector（不再继承）
  - 被动检测 API 委派：record_result / get_weight / get_stats / get_agent_health / reset
  - 六种控制动作：warn / replace / degrade / terminate / upgrade
  - patrol / check_before_dispatch / check_after_dispatch / get_retry_strategy
  - adjudicate
  - get_supervisor / set_supervisor 独立槽位（与 get_failure_detector 解耦）
"""

from __future__ import annotations

import asyncio
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from maop.core.scheduling.failure_detector import (
    FailurePatternDetector,
    get_failure_detector,
    get_supervisor,
    set_failure_detector,
    set_supervisor,
)
from maop.core.scheduling.supervisor import (
    AlertLevel,
    Supervisor,
    SupervisorAction,
    default_rules,
)


def _setup():
    set_failure_detector(None)
    set_supervisor(None)


def _teardown():
    set_failure_detector(None)
    set_supervisor(None)


class TestSupervisorComposition(unittest.TestCase):
    def setUp(self):
        _setup()
        self.detector = FailurePatternDetector(window_size=10, recovery_consecutive_successes=2)
        set_failure_detector(self.detector)
        self.sup = Supervisor(detector=self.detector)
        set_supervisor(self.sup)

    def tearDown(self):
        _teardown()

    # ── Composition ──────────────────────────────────────────────

    def test_not_inherits_detector(self):
        self.assertFalse(isinstance(self.sup, FailurePatternDetector))
        self.assertIs(self.sup._detector, self.detector)

    def test_passive_api_delegation(self):
        self.sup.record_result("a", success=True, latency=0.1)
        self.assertEqual(self.sup.get_weight("a"), 1.0)
        stats = self.sup.get_stats()
        self.assertIn("agents", stats)
        health = self.sup.get_agent_health("a")
        self.assertIsNotNone(health)
        self.assertEqual(health.agent_id, "a")

    def test_reset_delegation(self):
        self.sup.record_result("a", success=False, latency=0.5)
        self.sup.reset("a")
        self.assertEqual(self.sup.get_weight("a"), 1.0)

    # ── Control actions ──────────────────────────────────────────

    def test_warn(self):
        async def _run():
            await self.sup.warn("a", reason="test", level=AlertLevel.WARNING)
        asyncio.run(_run())
        self.assertEqual(len(self.sup._pending_alerts), 1)

    def test_replace_marks_replaced(self):
        async def _run():
            record = await self.sup.replace("a", "b", reason="t", routing_key="rk")
            return record
        record = asyncio.run(_run())
        self.assertEqual(record.action, SupervisorAction.REPLACE)
        self.assertEqual(self.sup.get_weight("a"), 0.0)
        status = self.sup.get_supervisor_status()
        agent_a = next(a for a in status["agents"] if a["agent_id"] == "a")
        self.assertEqual(agent_a["operational_status"], "replaced")

    def test_degrade_reduces_weight(self):
        async def _run():
            self.sup.record_result("a", success=True, latency=0.1)
            record = await self.sup.degrade("a", factor=0.5, reason="t")
            return record
        record = asyncio.run(_run())
        self.assertEqual(record.action, SupervisorAction.DEGRADE)
        self.assertAlmostEqual(self.sup.get_weight("a"), 0.5)

    def test_terminate_blocks_dispatch(self):
        async def _run():
            record = await self.sup.terminate("a", reason="t")
            return record
        record = asyncio.run(_run())
        self.assertEqual(record.action, SupervisorAction.TERMINATE)
        decision = self.sup.check_before_dispatch("a")
        self.assertFalse(decision.allow)
        self.assertIn("terminated", decision.reason)

    def test_terminate_force_bypasses(self):
        async def _run():
            return await self.sup.terminate("a", reason="t", force=True)
        record = asyncio.run(_run())
        self.assertEqual(record.action, SupervisorAction.TERMINATE)
        self.assertTrue(record.params["force_bypass_safety"])

    def test_upgrade_marks_upgrading(self):
        async def _run():
            record = await self.sup.upgrade("a", "v1.3", reason="t")
            return record
        record = asyncio.run(_run())
        self.assertEqual(record.action, SupervisorAction.UPGRADE)
        status = self.sup.get_supervisor_status()
        agent_a = next(a for a in status["agents"] if a["agent_id"] == "a")
        self.assertEqual(agent_a["operational_status"], "upgrading")

    # ── Patrol ───────────────────────────────────────────────────

    def test_patrol_no_agents(self):
        async def _run():
            return await self.sup.patrol()
        probes = asyncio.run(_run())
        self.assertEqual(probes, [])

    def test_patrol_with_agents(self):
        async def _run():
            self.sup.record_result("a", success=True, latency=0.1)
            self.sup.record_result("b", success=False, latency=0.5)
            return await self.sup.patrol()
        probes = asyncio.run(_run())
        self.assertEqual(len(probes), 2)
        self.assertEqual({p.agent_id for p in probes}, {"a", "b"})

    # ── Dispatch gating ──────────────────────────────────────────

    def test_check_before_dispatch_allows_normal(self):
        self.sup.record_result("a", success=True, latency=0.1)
        decision = self.sup.check_before_dispatch("a")
        self.assertTrue(decision.allow)
        self.assertFalse(decision.degraded)

    def test_check_before_dispatch_blocks_terminated(self):
        asyncio.run(self.sup.terminate("a", reason="t"))
        decision = self.sup.check_before_dispatch("a")
        self.assertFalse(decision.allow)

    def test_check_after_dispatch_records(self):
        self.sup.check_after_dispatch("a", success=True, latency=0.5)
        health = self.sup.get_agent_health("a")
        self.assertIsNotNone(health)
        self.assertEqual(health.total_recorded, 1)

    # ── Retry strategy ───────────────────────────────────────────

    def test_get_retry_strategy_normal(self):
        self.sup.record_result("a", success=True, latency=0.1)
        strategy = self.sup.get_retry_strategy("a", default_max_attempts=3, default_backoff_ms=2000)
        self.assertEqual(strategy["max_attempts"], 3)
        self.assertEqual(strategy["backoff_ms"], 2000)
        self.assertFalse(strategy["skip_agent"])

    def test_get_retry_strategy_degraded(self):
        async def _run():
            self.sup.record_result("a", success=True, latency=0.1)
            await self.sup.degrade("a", factor=0.5, reason="t")
            return self.sup.get_retry_strategy("a", default_max_attempts=3, default_backoff_ms=2000)
        strategy = asyncio.run(_run())
        self.assertEqual(strategy["max_attempts"], 2)
        self.assertEqual(strategy["backoff_ms"], 4000)
        self.assertFalse(strategy["skip_agent"])

    def test_get_retry_strategy_terminated(self):
        asyncio.run(self.sup.terminate("a", reason="t"))
        strategy = self.sup.get_retry_strategy("a", default_max_attempts=3, default_backoff_ms=2000)
        self.assertTrue(strategy["skip_agent"])
        self.assertEqual(strategy["max_attempts"], 0)

    # ── Adjudicate ───────────────────────────────────────────────

    def test_adjudicate_empty(self):
        async def _run():
            return await self.sup.adjudicate("d1", [])
        result = asyncio.run(_run())
        self.assertFalse(result["consensus"])
        self.assertTrue(result["low_confidence"])
        self.assertIsNone(result["winner"])

    def test_adjudicate_picks_highest(self):
        async def _run():
            rounds = [[
                {"agent_id": "a", "confidence": 0.6},
                {"agent_id": "b", "confidence": 0.8},
            ]]
            return await self.sup.adjudicate("d1", rounds)
        result = asyncio.run(_run())
        self.assertEqual(result["winner"]["agent_id"], "b")
        self.assertFalse(result["low_confidence"])

    # ── Supervisor singleton slot (independent of detector slot) ──

    def test_supervisor_slot_independent(self):
        set_supervisor(None)
        self.assertIsNone(get_supervisor())
        s = Supervisor()
        set_supervisor(s)
        self.assertIs(get_supervisor(), s)
        # get_failure_detector() returns an independent FailurePatternDetector.
        fd = get_failure_detector()
        self.assertIsNot(fd, s)
        self.assertIsInstance(fd, FailurePatternDetector)
        set_supervisor(None)
        self.assertIsNone(get_supervisor())

    # ── Rule engine defaults ─────────────────────────────────────

    def test_default_rules(self):
        rules = default_rules()
        self.assertGreaterEqual(len(rules), 5)
        actions = {r.action for r in rules}
        self.assertIn(SupervisorAction.ALERT, actions)
        self.assertIn(SupervisorAction.DEGRADE, actions)


if __name__ == "__main__":
    unittest.main(verbosity=2)
