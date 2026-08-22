"""Tests for maop.core.scheduling.supervisor — proactive multi-agent supervision.

Covers:
  - Data models (HealthProbe, SupervisorRule, DispatchDecision, ActionRecord)
  - Enums (SupervisorAction, AlertLevel, AgentOperationalStatus)
  - RuleEngine: condition matching (OR / AND / cooldown / unknown keys)
  - Default rule set
  - HealthChecker: ping (no dispatcher → reachable=True), metrics probe
  - Supervisor six capabilities: patrol / warn / replace / degrade /
    terminate / upgrade
  - Patrol loop (start / stop)
  - check_before_dispatch / check_after_dispatch
  - get_retry_strategy (dynamic)
  - adjudicate (debate stalemate)
  - Backward compatibility: get_supervisor() None vs Supervisor instance
  - Dashboard API endpoints (GET /api/supervisor/status, /rules, /actions;
    POST /api/supervisor/action, /patrol, /rules)
  - Engine integration (check_before_dispatch blocks terminated agent)
  - LoopExecutor integration (dynamic retry strategy)
"""

from __future__ import annotations

import asyncio

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from maop.core.scheduling.failure_detector import (
    FailurePatternDetector,
    get_supervisor,
    set_supervisor,
)
from maop.core.scheduling.supervisor import (
    ActionRecord,
    AgentOperationalStatus,
    AlertLevel,
    DispatchDecision,
    HealthChecker,
    HealthProbe,
    RuleEngine,
    Supervisor,
    SupervisorAction,
    SupervisorRule,
    TerminateRefusedError,
    default_rules,
)

# ── Fixtures ──────────────────────────────────────────────────────


@pytest.fixture
def supervisor():
    """Fresh Supervisor with a small window for fast test transitions."""
    s = Supervisor(
        window_size=10,
        failure_rate_threshold=0.30,
        timeout_threshold=1.0,
        recovery_consecutive_successes=3,
        patrol_interval_s=0.05,  # fast for tests
    )
    set_supervisor(s)
    yield s
    set_supervisor(None)


@pytest.fixture
def supervisor_with_bus():
    """Supervisor wired to a real EventBus so events are captured."""
    from maop.core.reliability.event_bus import EventBus

    bus = EventBus()
    s = Supervisor(
        window_size=10,
        failure_rate_threshold=0.30,
        timeout_threshold=1.0,
        recovery_consecutive_successes=3,
        patrol_interval_s=0.05,
        event_bus=bus,
    )
    set_supervisor(s)
    yield s, bus
    set_supervisor(None)


# ── 1. Data models & enums ────────────────────────────────────────


def test_supervisor_action_enum_values():
    assert SupervisorAction.PATROL.value == "patrol"
    assert SupervisorAction.ALERT.value == "alert"
    assert SupervisorAction.REPLACE.value == "replace"
    assert SupervisorAction.DEGRADE.value == "degrade"
    assert SupervisorAction.TERMINATE.value == "terminate"
    assert SupervisorAction.UPGRADE.value == "upgrade"
    assert SupervisorAction.NONE.value == "none"


def test_alert_level_enum_values():
    assert AlertLevel.INFO.value == "info"
    assert AlertLevel.WARNING.value == "warning"
    assert AlertLevel.ERROR.value == "error"
    assert AlertLevel.CRITICAL.value == "critical"


def test_agent_operational_status_enum_values():
    statuses = {s.value for s in AgentOperationalStatus}
    assert statuses == {
        "normal", "degraded", "drained", "recovering",
        "replaced", "terminated", "upgrading",
    }


def test_health_probe_defaults():
    p = HealthProbe(agent_id="a1", reachable=True)
    assert p.agent_id == "a1"
    assert p.reachable is True
    assert p.latency_ms == 0.0
    assert p.failure_rate == 0.0
    assert p.resource_usage == {}
    assert p.probed_at > 0


def test_supervisor_rule_defaults():
    r = SupervisorRule(
        rule_id="r1", name="n", action=SupervisorAction.ALERT,
        condition={"failure_rate_gt": 0.1},
    )
    assert r.alert_level == AlertLevel.WARNING
    assert r.cooldown_s == 60.0
    assert r.priority == 0
    assert r.enabled is True
    assert r.action_params == {}


def test_dispatch_decision_defaults():
    d = DispatchDecision(allow=True)
    assert d.allow is True
    assert d.reason == ""
    assert d.fallback_agent is None
    assert d.degraded is False


def test_action_record_serialization():
    r = ActionRecord(
        action_id="x", action=SupervisorAction.DEGRADE,
        agent_id="a", reason="test", created_at=1.0,
    )
    d = r.model_dump()
    assert d["action"] == "degrade"
    assert d["triggered_by"] == "patrol"
    assert d["reverted_at"] is None


# ── 2. RuleEngine ─────────────────────────────────────────────────


def test_rule_engine_failure_rate_gt_match():
    engine = RuleEngine([
        SupervisorRule(
            rule_id="r1", name="n", action=SupervisorAction.ALERT,
            condition={"failure_rate_gt": 0.1}, cooldown_s=0,
        ),
    ])
    probe = HealthProbe(agent_id="a", reachable=True, failure_rate=0.2)
    matched = engine.evaluate(probe)
    assert len(matched) == 1
    assert matched[0].rule_id == "r1"


def test_rule_engine_no_match():
    engine = RuleEngine([
        SupervisorRule(
            rule_id="r1", name="n", action=SupervisorAction.ALERT,
            condition={"failure_rate_gt": 0.5}, cooldown_s=0,
        ),
    ])
    probe = HealthProbe(agent_id="a", reachable=True, failure_rate=0.2)
    assert engine.evaluate(probe) == []


def test_rule_engine_and_semantics():
    engine = RuleEngine([
        SupervisorRule(
            rule_id="r1", name="n", action=SupervisorAction.ALERT,
            condition={
                "all": [
                    {"failure_rate_gt": 0.1},
                    {"avg_latency_gt": 10.0},
                ],
            },
            cooldown_s=0,
        ),
    ])
    # Both match.
    p1 = HealthProbe(agent_id="a", reachable=True, failure_rate=0.2, avg_latency=15.0)
    assert len(engine.evaluate(p1)) == 1
    # Only one matches.
    p2 = HealthProbe(agent_id="a", reachable=True, failure_rate=0.2, avg_latency=5.0)
    assert engine.evaluate(p2) == []


def test_rule_engine_or_semantics():
    engine = RuleEngine([
        SupervisorRule(
            rule_id="r1", name="n", action=SupervisorAction.ALERT,
            condition={
                "failure_rate_gt": 0.5,
                "avg_latency_gt": 10.0,
            },
            cooldown_s=0,
        ),
    ])
    # Either matches.
    p = HealthProbe(agent_id="a", reachable=True, failure_rate=0.1, avg_latency=15.0)
    assert len(engine.evaluate(p)) == 1


def test_rule_engine_breaker_open_condition():
    engine = RuleEngine([
        SupervisorRule(
            rule_id="r1", name="n", action=SupervisorAction.ALERT,
            condition={"breaker_open": True}, cooldown_s=0,
        ),
    ])
    p = HealthProbe(agent_id="a", reachable=True, breaker_open=True)
    assert len(engine.evaluate(p)) == 1
    p2 = HealthProbe(agent_id="a", reachable=True, breaker_open=False)
    assert engine.evaluate(p2) == []


def test_rule_engine_reachable_false_condition():
    engine = RuleEngine([
        SupervisorRule(
            rule_id="r1", name="n", action=SupervisorAction.ALERT,
            condition={"reachable": False}, cooldown_s=0,
        ),
    ])
    p = HealthProbe(agent_id="a", reachable=False)
    assert len(engine.evaluate(p)) == 1
    p2 = HealthProbe(agent_id="a", reachable=True)
    assert engine.evaluate(p2) == []


def test_rule_engine_resource_usage_condition():
    engine = RuleEngine([
        SupervisorRule(
            rule_id="r1", name="n", action=SupervisorAction.ALERT,
            condition={
                "resource_usage_gt": {"cpu_percent": 90.0, "memory_percent": 85.0},
            },
            cooldown_s=0,
        ),
    ])
    p = HealthProbe(
        agent_id="a", reachable=True,
        resource_usage={"cpu_percent": 50.0, "memory_percent": 90.0},
    )
    assert len(engine.evaluate(p)) == 1


def test_rule_engine_cooldown():
    engine = RuleEngine([
        SupervisorRule(
            rule_id="r1", name="n", action=SupervisorAction.ALERT,
            condition={"failure_rate_gt": 0.1}, cooldown_s=1000,
        ),
    ])
    p = HealthProbe(agent_id="a", reachable=True, failure_rate=0.2)
    # First match.
    assert len(engine.evaluate(p)) == 1
    # Second within cooldown → no match.
    assert engine.evaluate(p) == []


def test_rule_engine_disabled_rule():
    engine = RuleEngine([
        SupervisorRule(
            rule_id="r1", name="n", action=SupervisorAction.ALERT,
            condition={"failure_rate_gt": 0.1}, enabled=False,
        ),
    ])
    p = HealthProbe(agent_id="a", reachable=True, failure_rate=0.2)
    assert engine.evaluate(p) == []


def test_rule_engine_priority_order():
    rules = [
        SupervisorRule(
            rule_id="low", name="n", action=SupervisorAction.ALERT,
            condition={"failure_rate_gt": 0.1}, priority=1, cooldown_s=0,
        ),
        SupervisorRule(
            rule_id="high", name="n", action=SupervisorAction.ALERT,
            condition={"failure_rate_gt": 0.1}, priority=10, cooldown_s=0,
        ),
    ]
    engine = RuleEngine(rules)
    # Higher priority should be first.
    assert engine.rules[0].rule_id == "high"


def test_default_rules_count():
    rules = default_rules()
    # 5 rules (failure_rate.warning, latency.warning, latency.degrade,
    # breaker.open, timeout.high, resource.high) = 6
    assert len(rules) >= 5
    actions = {r.action for r in rules}
    assert SupervisorAction.ALERT in actions
    assert SupervisorAction.DEGRADE in actions


# ── 3. HealthChecker ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_health_checker_ping_no_dispatcher():
    """Without a dispatcher, ping defaults to reachable=True."""
    checker = HealthChecker()
    probe = await checker.check("agent_a")
    assert probe.agent_id == "agent_a"
    assert probe.reachable is True
    assert probe.latency_ms == 0.0


@pytest.mark.asyncio
async def test_health_checker_with_detector():
    """Metrics probe reads from the detector."""
    detector = FailurePatternDetector(window_size=10)
    detector.record_result("a", success=False, latency=0.5)
    detector.record_result("a", success=False, latency=0.5)
    checker = HealthChecker(detector=detector)
    probe = await checker.check("a")
    assert probe.failure_rate == 1.0
    assert probe.avg_latency == pytest.approx(0.5)


@pytest.mark.asyncio
async def test_health_checker_check_all():
    checker = HealthChecker()
    probes = await checker.check_all(["a", "b", "c"])
    assert len(probes) == 3
    assert {p.agent_id for p in probes} == {"a", "b", "c"}


@pytest.mark.asyncio
async def test_health_checker_check_sample_forwards_to_all():
    """v1: check_sample forwards to check_all (per [F-4])."""
    checker = HealthChecker()
    probes = await checker.check_sample(["a", "b", "c"], sample_size=2)
    assert len(probes) == 2


@pytest.mark.asyncio
async def test_health_checker_check_adaptive_forwards_to_all():
    """v1: check_adaptive forwards to check_all (per [F-4])."""
    checker = HealthChecker()
    probes = await checker.check_adaptive(["a", "b"])
    assert len(probes) == 2


# ── 4. Supervisor six capabilities ────────────────────────────────


@pytest.mark.asyncio
async def test_warn_publishes_event(supervisor_with_bus):
    sup, bus = supervisor_with_bus
    await sup.warn("a", reason="test_warn", level=AlertLevel.WARNING)
    # Event should be in bus history.
    history = bus.get_history(topic="supervisor.alert")
    assert len(history) >= 1
    assert history[-1].data["agent_id"] == "a"
    assert history[-1].data["reason"] == "test_warn"


@pytest.mark.asyncio
async def test_replace_marks_agent_replaced(supervisor):
    record = await supervisor.replace(
        "a", "b", reason="test", routing_key="rk",
    )
    assert record.action == SupervisorAction.REPLACE
    assert record.agent_id == "a"
    # Agent a should be drained (weight=0) and marked REPLACED.
    assert supervisor.get_weight("a") == 0.0
    status = supervisor.get_supervisor_status()
    agent_a = next(a for a in status["agents"] if a["agent_id"] == "a")
    assert agent_a["operational_status"] == "replaced"


@pytest.mark.asyncio
async def test_replace_publishes_event(supervisor_with_bus):
    sup, bus = supervisor_with_bus
    await sup.replace("a", "b", reason="test", routing_key="rk")
    history = bus.get_history(topic="agent_replaced")
    assert len(history) >= 1
    assert history[-1].data["replacement"] == "b"


@pytest.mark.asyncio
async def test_degrade_reduces_weight(supervisor):
    # Record some success so agent has weight 1.0.
    supervisor.record_result("a", success=True, latency=0.1)
    original_weight = supervisor.get_weight("a")
    assert original_weight == 1.0
    record = await supervisor.degrade("a", factor=0.5, reason="test")
    assert record.action == SupervisorAction.DEGRADE
    assert supervisor.get_weight("a") == pytest.approx(0.5)
    # Operational status should be DEGRADED.
    status = supervisor.get_supervisor_status()
    agent_a = next(a for a in status["agents"] if a["agent_id"] == "a")
    assert agent_a["operational_status"] == "degraded"


@pytest.mark.asyncio
async def test_degrade_factor_clamping(supervisor):
    """Factor outside (0,1) is clamped."""
    supervisor.record_result("a", success=True, latency=0.1)
    await supervisor.degrade("a", factor=2.0, reason="test")  # clamps to 0.99
    assert supervisor.get_weight("a") == pytest.approx(0.99)


@pytest.mark.asyncio
async def test_degrade_publishes_event(supervisor_with_bus):
    sup, bus = supervisor_with_bus
    sup.record_result("a", success=True, latency=0.1)
    await sup.degrade("a", factor=0.5, reason="test")
    history = bus.get_history(topic="agent_degraded")
    assert len(history) >= 1


@pytest.mark.asyncio
async def test_terminate_blocks_dispatch(supervisor):
    record = await supervisor.terminate("a", reason="test")
    assert record.action == SupervisorAction.TERMINATE
    # check_before_dispatch should return allow=False.
    decision = supervisor.check_before_dispatch("a")
    assert decision.allow is False
    assert "terminated" in decision.reason or "disabled" in decision.reason


@pytest.mark.asyncio
async def test_terminate_publishes_critical_event(supervisor_with_bus):
    sup, bus = supervisor_with_bus
    await sup.terminate("a", reason="test")
    history = bus.get_history(topic="agent_terminated")
    assert len(history) >= 1
    assert history[-1].data["level"] == "critical"


@pytest.mark.asyncio
async def test_terminate_with_force_bypasses_safety(supervisor):
    """force=True bypasses the sole-agent safety check."""
    record = await supervisor.terminate("a", reason="test", force=True)
    assert record.action == SupervisorAction.TERMINATE
    assert record.params["force_bypass_safety"] is True


@pytest.mark.asyncio
async def test_upgrade_marks_upgrading(supervisor):
    record = await supervisor.upgrade("a", "v1.3", reason="test")
    assert record.action == SupervisorAction.UPGRADE
    status = supervisor.get_supervisor_status()
    agent_a = next(a for a in status["agents"] if a["agent_id"] == "a")
    assert agent_a["operational_status"] == "upgrading"


@pytest.mark.asyncio
async def test_upgrade_publishes_started_event(supervisor_with_bus):
    sup, bus = supervisor_with_bus
    await sup.upgrade("a", "v1.3", reason="test")
    history = bus.get_history(topic="agent_upgrade.started")
    assert len(history) >= 1
    assert history[-1].data["target_version"] == "v1.3"


@pytest.mark.asyncio
async def test_upgrade_rollback_on_failure_rate(supervisor_with_bus):
    """Auto-rollback when new version failure_rate > 0.15."""
    sup, bus = supervisor_with_bus
    await sup.upgrade("a", "v1.3", reason="test")
    # Simulate high failure rate.
    for _ in range(10):
        sup.record_result("a", success=False, latency=0.1)
    await sup._check_upgrade_health("a")
    history = bus.get_history(topic="agent_upgrade.rolled_back")
    assert len(history) >= 1


@pytest.mark.asyncio
async def test_patrol_no_agents(supervisor):
    """Patrol with no registered agents returns empty list."""
    probes = await supervisor.patrol()
    assert probes == []


@pytest.mark.asyncio
async def test_patrol_with_agents(supervisor):
    """Patrol probes known agents."""
    supervisor.record_result("a", success=True, latency=0.1)
    supervisor.record_result("b", success=False, latency=0.5)
    probes = await supervisor.patrol()
    assert len(probes) == 2
    assert {p.agent_id for p in probes} == {"a", "b"}


@pytest.mark.asyncio
async def test_patrol_unreachable_terminate(supervisor):
    """3 consecutive unreachable patrols trigger terminate."""
    # Use a health checker that always reports unreachable.
    class UnreachableChecker(HealthChecker):
        async def check(self, agent_id: str) -> HealthProbe:
            return HealthProbe(agent_id=agent_id, reachable=False)
    supervisor._health_checker = UnreachableChecker()
    supervisor.record_result("a", success=True, latency=0.1)
    # 3 patrols.
    for _ in range(3):
        await supervisor.patrol()
    # Agent should be terminated.
    decision = supervisor.check_before_dispatch("a")
    assert decision.allow is False


# ── 5. Patrol loop ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_start_stop_patrol_loop(supervisor):
    """Patrol loop can be started and stopped."""
    supervisor.record_result("a", success=True, latency=0.1)
    await supervisor.start_patrol_loop()
    assert supervisor.patrol_running is True
    # Let it run a tiny bit.
    await asyncio.sleep(0.1)
    await supervisor.stop_patrol_loop()
    assert supervisor.patrol_running is False


@pytest.mark.asyncio
async def test_patrol_loop_idempotent_start(supervisor):
    """Starting an already-running loop is a no-op."""
    await supervisor.start_patrol_loop()
    await supervisor.start_patrol_loop()  # should not raise
    assert supervisor.patrol_running is True
    await supervisor.stop_patrol_loop()


@pytest.mark.asyncio
async def test_stop_patrol_loop_when_not_started(supervisor):
    """Stopping when not started is a no-op."""
    await supervisor.stop_patrol_loop()
    assert supervisor.patrol_running is False


# ── 6. check_before/after_dispatch ────────────────────────────────


def test_check_before_dispatch_allows_normal(supervisor):
    """Normal agent is allowed."""
    supervisor.record_result("a", success=True, latency=0.1)
    decision = supervisor.check_before_dispatch("a")
    assert decision.allow is True
    assert decision.degraded is False


def test_check_before_dispatch_blocks_drained(supervisor):
    """Drained agent (weight=0) is blocked."""
    supervisor.record_result("a", success=True, latency=0.1)
    # Force drain by recording many failures.
    for _ in range(10):
        supervisor.record_result("a", success=False, latency=0.5)
    decision = supervisor.check_before_dispatch("a")
    assert decision.allow is False


@pytest.mark.asyncio
async def test_check_before_dispatch_blocks_terminated(supervisor):
    await supervisor.terminate("a", reason="test")
    decision = supervisor.check_before_dispatch("a")
    assert decision.allow is False


@pytest.mark.asyncio
async def test_check_before_dispatch_degraded_flag(supervisor):
    supervisor.record_result("a", success=True, latency=0.1)
    await supervisor.degrade("a", factor=0.5, reason="test")
    decision = supervisor.check_before_dispatch("a")
    assert decision.allow is True
    assert decision.degraded is True


def test_check_after_dispatch_records_result(supervisor):
    """check_after_dispatch forwards to record_result."""
    supervisor.check_after_dispatch("a", success=True, latency=0.5)
    health = supervisor.get_agent_health("a")
    assert health is not None
    assert health.total_recorded == 1
    assert health.avg_latency == pytest.approx(0.5)


# ── 7. Dynamic retry strategy ─────────────────────────────────────


def test_get_retry_strategy_normal(supervisor):
    """Normal agent gets default strategy."""
    supervisor.record_result("a", success=True, latency=0.1)
    strategy = supervisor.get_retry_strategy(
        "a", default_max_attempts=3, default_backoff_ms=2000,
    )
    assert strategy["max_attempts"] == 3
    assert strategy["backoff_ms"] == 2000
    assert strategy["skip_agent"] is False


@pytest.mark.asyncio
async def test_get_retry_strategy_degraded(supervisor):
    """Degraded agent gets reduced attempts."""
    supervisor.record_result("a", success=True, latency=0.1)
    await supervisor.degrade("a", factor=0.5, reason="test")
    strategy = supervisor.get_retry_strategy(
        "a", default_max_attempts=3, default_backoff_ms=2000,
    )
    assert strategy["max_attempts"] == 2  # 3 - 1
    assert strategy["backoff_ms"] == 4000  # 2000 * 2
    assert strategy["skip_agent"] is False


@pytest.mark.asyncio
async def test_get_retry_strategy_terminated(supervisor):
    """Terminated agent is skipped."""
    await supervisor.terminate("a", reason="test")
    strategy = supervisor.get_retry_strategy(
        "a", default_max_attempts=3, default_backoff_ms=2000,
    )
    assert strategy["skip_agent"] is True
    assert strategy["max_attempts"] == 0


# ── 8. Adjudicate ─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_adjudicate_empty_rounds(supervisor):
    result = await supervisor.adjudicate("d1", [])
    assert result["consensus"] is False
    assert result["low_confidence"] is True
    assert result["winner"] is None


@pytest.mark.asyncio
async def test_adjudicate_picks_highest_confidence(supervisor):
    rounds = [[
        {"agent_id": "a", "confidence": 0.6},
        {"agent_id": "b", "confidence": 0.8},
    ]]
    result = await supervisor.adjudicate("d1", rounds)
    assert result["winner"]["agent_id"] == "b"
    assert result["low_confidence"] is False  # 0.8 >= 0.70


@pytest.mark.asyncio
async def test_adjudicate_low_confidence(supervisor):
    rounds = [[
        {"agent_id": "a", "confidence": 0.5},
    ]]
    result = await supervisor.adjudicate("d1", rounds)
    assert result["low_confidence"] is True


# ── 9. Status & query ─────────────────────────────────────────────


def test_get_supervisor_status_shape(supervisor):
    supervisor.record_result("a", success=True, latency=0.1)
    status = supervisor.get_supervisor_status()
    assert "agents" in status
    assert "patrol" in status
    assert "pending_alerts" in status
    assert "recent_actions" in status
    assert "config" in status
    assert "rules" in status
    assert len(status["agents"]) == 1


def test_get_actions_filter_by_agent(supervisor):
    """Action history can be filtered by agent_id."""
    # Force two actions on different agents.
    asyncio.run(supervisor.degrade("a", factor=0.5, reason="t"))
    asyncio.run(supervisor.degrade("b", factor=0.5, reason="t"))
    actions_a = supervisor.get_actions(agent_id="a")
    assert all(a.agent_id == "a" for a in actions_a)
    assert len(actions_a) == 1
    actions_all = supervisor.get_actions()
    assert len(actions_all) == 2


# ── 10. Backward compatibility ────────────────────────────────────


def test_get_supervisor_returns_none_when_unconfigured():
    """When no supervisor set, get_supervisor() returns None."""
    set_supervisor(None)
    assert get_supervisor() is None


def test_supervisor_inherits_failure_detector():
    """Supervisor is a FailurePatternDetector (polymorphism)."""
    s = Supervisor()
    assert isinstance(s, FailurePatternDetector)
    # All passive APIs still work.
    s.record_result("a", success=True, latency=0.1)
    assert s.get_weight("a") == 1.0
    stats = s.get_stats()
    assert "agents" in stats


def test_supervisor_set_supervisor_polymorphism():
    """set_supervisor makes get_failure_detector return the Supervisor."""
    s = Supervisor()
    set_supervisor(s)
    from maop.core.scheduling.failure_detector import get_failure_detector

    detector = get_failure_detector()
    assert detector is s
    assert isinstance(detector, Supervisor)
    set_supervisor(None)


# ── 11. Dashboard API ─────────────────────────────────────────────


@pytest.fixture
def app_with_supervisor():
    """FastAPI app with the supervisor router and a configured Supervisor."""
    s = Supervisor()
    set_supervisor(s)
    app = FastAPI()
    from maop.dashboard.routers.supervisor import router

    app.include_router(router)
    yield app, s
    set_supervisor(None)


@pytest.fixture
def app_without_supervisor():
    """FastAPI app with the supervisor router but no Supervisor configured."""
    set_supervisor(None)
    app = FastAPI()
    from maop.dashboard.routers.supervisor import router

    app.include_router(router)
    yield app
    set_supervisor(None)


def test_api_status_returns_404_when_unconfigured(app_without_supervisor):
    client = TestClient(app_without_supervisor)
    response = client.get("/api/supervisor/status")
    assert response.status_code == 404


def test_api_status_returns_snapshot(app_with_supervisor):
    app, sup = app_with_supervisor
    sup.record_result("a", success=True, latency=0.1)
    client = TestClient(app)
    response = client.get("/api/supervisor/status")
    assert response.status_code == 200
    data = response.json()
    assert "agents" in data
    assert "patrol" in data


def test_api_rules_list(app_with_supervisor):
    app, _ = app_with_supervisor
    client = TestClient(app)
    response = client.get("/api/supervisor/rules")
    assert response.status_code == 200
    data = response.json()
    assert "rules" in data
    assert len(data["rules"]) >= 5


def test_api_actions_list(app_with_supervisor):
    app, sup = app_with_supervisor
    # Trigger an action.
    asyncio.run(sup.degrade("a", factor=0.5, reason="test"))
    client = TestClient(app)
    response = client.get("/api/supervisor/actions")
    assert response.status_code == 200
    data = response.json()
    assert "actions" in data
    assert len(data["actions"]) >= 1


def test_api_action_manual_degrade(app_with_supervisor):
    app, sup = app_with_supervisor
    sup.record_result("a", success=True, latency=0.1)
    client = TestClient(app)
    # Inject admin role via dependency override is complex; instead we
    # patch the request state. TestClient does not easily set auth state,
    # so we test the 403 path here and the success path via direct call.
    response = client.post(
        "/api/supervisor/action",
        json={
            "agent_id": "a",
            "action": "degrade",
            "params": {"factor": 0.5},
            "reason": "manual",
        },
    )
    # Without admin role → 403.
    assert response.status_code == 403


def test_api_patrol_manual(app_with_supervisor):
    app, sup = app_with_supervisor
    sup.record_result("a", success=True, latency=0.1)
    client = TestClient(app)
    response = client.post("/api/supervisor/patrol")
    # Without admin role → 403.
    assert response.status_code == 403


# ── 12. Engine integration ────────────────────────────────────────


@pytest.mark.asyncio
async def test_engine_supervisor_none_when_unconfigured():
    """Engine._get_supervisor returns None when no supervisor set."""
    from maop.engine import Engine

    set_supervisor(None)
    engine = Engine()
    assert engine._get_supervisor() is None


@pytest.mark.asyncio
async def test_engine_supervisor_returns_instance(supervisor):
    from maop.engine import Engine

    engine = Engine()
    sup = engine._get_supervisor()
    assert sup is supervisor


@pytest.mark.asyncio
async def test_engine_skips_terminated_agent(supervisor):
    """Engine skips a terminated agent and returns SKIPPED."""
    from maop.engine import Engine
    from maop.engine_types import WorkflowStep

    await supervisor.terminate("a", reason="test")
    engine = Engine()
    step = WorkflowStep(id="s1", type="agent", agent="a", task="t")
    result = await engine._execute_step(
        step, context={}, results={}, workdir="", trace_id="t1",
    )
    from maop.engine_types import StepStatus

    assert result.status == StepStatus.SKIPPED
    assert "Supervisor blocked" in result.error


# ── 13. LoopExecutor integration ──────────────────────────────────


def test_loop_executor_supervisor_none_when_unconfigured():
    """ExecuteMixin._get_supervisor returns None when no supervisor set."""
    from maop.loop_executor import ExecuteMixin

    set_supervisor(None)
    mixin = ExecuteMixin()
    assert mixin._get_supervisor() is None


def test_loop_executor_supervisor_returns_instance(supervisor):
    from maop.loop_executor import ExecuteMixin

    mixin = ExecuteMixin()
    sup = mixin._get_supervisor()
    assert sup is supervisor


# ── 14. TerminateRefusedError ─────────────────────────────────────


def test_terminate_refused_error_message():
    err = TerminateRefusedError("a", "rk", "reason text")
    assert err.agent_id == "a"
    assert err.routing_key == "rk"
    assert "a" in str(err)
    assert "reason text" in str(err)


# ── 15. EventBus API unification (C-1 fix) ────────────────────────


@pytest.mark.asyncio
async def test_failure_detector_publishes_via_publish_api():
    """FailurePatternDetector._publish_event uses publish(Event), not emit()."""
    from maop.core.reliability.event_bus import EventBus

    bus = EventBus()
    detector = FailurePatternDetector(event_bus=bus)
    # Trigger a drain by recording many failures.
    for _ in range(10):
        detector.record_result("a", success=False, latency=0.5)
    # _publish_event uses fire-and-forget (ensure_future) when a running
    # loop exists; yield control so the publish task completes before we
    # inspect the bus history.
    await asyncio.sleep(0.05)
    # The drain event should be in history (published via publish()).
    history = bus.get_history(topic="agent_drained")
    assert len(history) >= 1
    assert history[-1].data["agent_id"] == "a"
    assert history[-1].data["level"] == "error"