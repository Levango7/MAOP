"""Supervisor mixin 边界行为单元测试（unittest，无 pytest）。

覆盖四个拆分后的 mixin 模块的独立边界行为：
  - SupervisorActionMixin：degrade 因子边界、terminate force 路径、upgrade 回滚（failure_rate / latency）
  - SupervisorPatrolMixin：不可达连续 3 次触发终止、_apply_rule 各 action 路径
  - SupervisorDispatchMixin：degraded 允许 dispatch、retry 策略边界（max_attempts=1）
  - SupervisorStatusMixin：get_supervisor_status 含 degraded/replaced/terminated agent
"""

from __future__ import annotations

import asyncio
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from maop.core.scheduling.failure_detector import FailurePatternDetector
from maop.core.scheduling.supervisor import (
    AlertLevel,
    Supervisor,
    SupervisorAction,
)


class _Probe:
    """最小 HealthProbe 替身，避免导入 pydantic 模型依赖。"""

    def __init__(self, agent_id, reachable=True, failure_rate=0.0, avg_latency=0.0):
        self.agent_id = agent_id
        self.reachable = reachable
        self.failure_rate = failure_rate
        self.avg_latency = avg_latency


class TestSupervisorMixinBoundary(unittest.TestCase):
    def setUp(self):
        self.detector = FailurePatternDetector(window_size=10, recovery_consecutive_successes=2)
        self.sup = Supervisor(detector=self.detector)

    def _run(self, coro):
        return asyncio.run(coro)

    # ── Action mixin: degrade factor boundary ──────────────────────

    def test_degrade_factor_zero_clamped(self):
        self.sup.record_result("a", success=True, latency=0.1)
        record = self._run(self.sup.degrade("a", factor=0.0, reason="edge"))
        # 0.0 被 clamp 到 0.01
        self.assertGreaterEqual(self.sup.get_weight("a"), 0.01)

    def test_degrade_factor_one_clamped(self):
        self.sup.record_result("a", success=True, latency=0.1)
        record = self._run(self.sup.degrade("a", factor=1.0, reason="edge"))
        # 1.0 被 clamp 到 0.99
        self.assertLessEqual(self.sup.get_weight("a"), 0.99)

    def test_degrade_factor_above_one_clamped(self):
        self.sup.record_result("a", success=True, latency=0.1)
        record = self._run(self.sup.degrade("a", factor=2.0, reason="edge"))
        self.assertLessEqual(self.sup.get_weight("a"), 0.99)

    # ── Action mixin: terminate force path ─────────────────────────

    def test_terminate_force_false_safety_no_routing_store(self):
        """无 routing store 时 force=False 不应拒绝（_find_sole_routing_key 返回 None）。"""
        record = self._run(self.sup.terminate("a", reason="t", force=False))
        self.assertEqual(record.action, SupervisorAction.TERMINATE)
        decision = self.sup.check_before_dispatch("a")
        self.assertFalse(decision.allow)

    # ── Action mixin: upgrade rollback on failure_rate ─────────────

    def test_upgrade_rollback_on_failure_rate(self):
        self._run(self.sup.upgrade("a", "v2.0", reason="test"))
        # 连续失败推高 failure_rate > 0.15
        for _ in range(10):
            self.sup.record_result("a", success=False, latency=0.1)
        self._run(self.sup._check_upgrade_health("a"))
        status = self.sup.get_supervisor_status()
        agent_a = next(a for a in status["agents"] if a["agent_id"] == "a")
        self.assertEqual(agent_a["operational_status"], "normal")

    # ── Action mixin: upgrade rollback on latency ──────────────────

    def test_upgrade_rollback_on_latency_increase(self):
        self.sup.record_result("a", success=True, latency=0.1)
        self._run(self.sup.upgrade("a", "v2.0", reason="test"))
        # 升级后 latency 暴涨 > old * 1.5
        for _ in range(5):
            self.sup.record_result("a", success=True, latency=1.0)
        self._run(self.sup._check_upgrade_health("a"))
        status = self.sup.get_supervisor_status()
        agent_a = next(a for a in status["agents"] if a["agent_id"] == "a")
        self.assertEqual(agent_a["operational_status"], "normal")

    # ── Patrol mixin: unreachable 3 strikes -> terminate ───────────

    def test_patrol_unreachable_three_strikes_terminate(self):
        class AlwaysUnreachable:
            async def check_all(self, agent_ids):
                from maop.core.scheduling.supervisor_health import HealthProbe
                return [HealthProbe(agent_id=aid, reachable=False) for aid in agent_ids]

        self.sup._health_checker = AlwaysUnreachable()
        self.sup.record_result("a", success=True, latency=0.1)
        for _ in range(3):
            self._run(self.sup.patrol())
        decision = self.sup.check_before_dispatch("a")
        self.assertFalse(decision.allow)

    # ── Patrol mixin: _apply_rule for each action ──────────────────

    def test_apply_rule_degrade(self):
        self.sup.record_result("a", success=True, latency=0.1)
        from maop.core.scheduling.supervisor_models import SupervisorRule
        rule = SupervisorRule(
            rule_id="r_degrade", name="deg", action=SupervisorAction.DEGRADE,
            condition={"failure_rate_gt": 0.0}, cooldown_s=0,
            action_params={"factor": 0.5},
        )
        probe = _Probe("a", reachable=True, failure_rate=0.5)
        self._run(self.sup._apply_rule(rule, probe))
        self.assertAlmostEqual(self.sup.get_weight("a"), 0.5)

    def test_apply_rule_terminate(self):
        from maop.core.scheduling.supervisor_models import SupervisorRule
        rule = SupervisorRule(
            rule_id="r_term", name="term", action=SupervisorAction.TERMINATE,
            condition={"reachable": False}, cooldown_s=0,
        )
        probe = _Probe("a", reachable=False)
        self._run(self.sup._apply_rule(rule, probe))
        decision = self.sup.check_before_dispatch("a")
        self.assertFalse(decision.allow)

    def test_apply_rule_replace(self):
        from maop.core.scheduling.supervisor_models import SupervisorRule
        rule = SupervisorRule(
            rule_id="r_repl", name="repl", action=SupervisorAction.REPLACE,
            condition={"failure_rate_gt": 0.0}, cooldown_s=0,
            action_params={"replacement": "backup", "routing_key": "rk"},
        )
        probe = _Probe("a", reachable=True, failure_rate=0.5)
        self._run(self.sup._apply_rule(rule, probe))
        self.assertEqual(self.sup.get_weight("a"), 0.0)
        status = self.sup.get_supervisor_status()
        agents = {x["agent_id"]: x for x in status["agents"]}
        self.assertEqual(agents["a"]["operational_status"], "replaced")

    def test_apply_rule_upgrade(self):
        from maop.core.scheduling.supervisor_models import SupervisorRule
        rule = SupervisorRule(
            rule_id="r_upg", name="upg", action=SupervisorAction.UPGRADE,
            condition={"failure_rate_gt": 0.0}, cooldown_s=0,
            action_params={"target_version": "v3.0"},
        )
        probe = _Probe("a", reachable=True, failure_rate=0.5)
        self._run(self.sup._apply_rule(rule, probe))
        status = self.sup.get_supervisor_status()
        agents = {x["agent_id"]: x for x in status["agents"]}
        self.assertEqual(agents["a"]["operational_status"], "upgrading")

    # ── Dispatch mixin: degraded allows dispatch ───────────────────

    def test_check_before_dispatch_degraded_allows(self):
        self.sup.record_result("a", success=True, latency=0.1)
        self._run(self.sup.degrade("a", factor=0.5, reason="t"))
        decision = self.sup.check_before_dispatch("a")
        self.assertTrue(decision.allow)
        self.assertTrue(decision.degraded)

    # ── Dispatch mixin: retry strategy boundary ─────────────────────

    def test_get_retry_strategy_degraded_min_attempts(self):
        self.sup.record_result("a", success=True, latency=0.1)
        self._run(self.sup.degrade("a", factor=0.5, reason="t"))
        # default_max_attempts=1 时 degraded 也至少保留 1 次尝试
        strategy = self.sup.get_retry_strategy("a", default_max_attempts=1, default_backoff_ms=100)
        self.assertEqual(strategy["max_attempts"], 1)

    # ── Status mixin: status snapshot contains agent states ─────────

    def test_get_supervisor_status_includes_degraded_agent(self):
        self.sup.record_result("a", success=True, latency=0.1)
        self._run(self.sup.degrade("a", factor=0.5, reason="t"))
        status = self.sup.get_supervisor_status()
        agents = {a["agent_id"]: a for a in status["agents"]}
        self.assertEqual(agents["a"]["operational_status"], "degraded")

    def test_get_supervisor_status_includes_terminated_agent(self):
        self._run(self.sup.terminate("a", reason="t"))
        status = self.sup.get_supervisor_status()
        agents = {a["agent_id"]: a for a in status["agents"]}
        self.assertEqual(agents["a"]["operational_status"], "terminated")


if __name__ == "__main__":
    unittest.main(verbosity=2)
