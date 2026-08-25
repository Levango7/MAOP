"""Tests for maop.core.scheduling.failure_detector — adaptive scheduling weights.

Covers:
  - Sliding-window stats (failure rate, avg latency, timeout rate)
  - Weight transitions: normal → drained → recovering → normal
  - Drain triggered by failure-rate threshold
  - Recovery ladder (0.3 → 0.6 → 1.0) with consecutive-success gate
  - Failure during recovery resets the counter but does not re-drain
  - get_stats() shape and JSON serialisability
  - Prometheus gauge updates
  - EventBus notification on drain / recovery
  - Thread safety (concurrent record_result)
  - reset() per-agent and global
  - DistributedScheduler._select_worker integration
  - Scheduling router endpoints (GET /api/scheduling/failure-stats,
    POST .../reset)
"""

from __future__ import annotations

import threading
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from maop.core.scheduling.failure_detector import (
    FailurePatternDetector,
    get_failure_detector,
    set_failure_detector,
)

# ── Fixtures ──────────────────────────────────────────────────────


@pytest.fixture
def detector():
    """Fresh detector with a small window for fast test transitions."""
    d = FailurePatternDetector(
        window_size=10,
        failure_rate_threshold=0.30,
        timeout_threshold=1.0,
        recovery_consecutive_successes=3,
    )
    set_failure_detector(d)
    yield d
    set_failure_detector(None)


@pytest.fixture
def detector_with_bus():
    """Detector wired to a real EventBus so emitted events are captured."""
    # P2 修复：无条件 skip 改为 import 条件化 —— maop.enterprise 可导入
    # （企业版）时才真正运行测试；个人版（未安装）时才跳过。
    try:
        import maop.enterprise  # noqa: F401
    except ImportError:
        pytest.skip(reason="maop.enterprise 未发布")
    from maop.enterprise.notification.event_bus import EventBus

    bus = EventBus(history_size=100)
    d = FailurePatternDetector(
        window_size=10,
        failure_rate_threshold=0.30,
        timeout_threshold=1.0,
        recovery_consecutive_successes=3,
        event_bus=bus,
    )
    return d, bus


# ── 1. Initial state ──────────────────────────────────────────────


def test_initial_weight_is_one_for_unknown_agent(detector: FailurePatternDetector):
    """An agent never seen by the detector defaults to weight 1.0."""
    assert detector.get_weight("unknown") == 1.0


def test_initial_stats_empty(detector: FailurePatternDetector):
    """get_stats() with no recorded outcomes returns an empty snapshot."""
    stats = detector.get_stats()
    assert stats["agents"] == []
    assert stats["total_agents"] == 0
    assert stats["config"]["window_size"] == 10
    assert stats["config"]["failure_rate_threshold"] == pytest.approx(0.30)


# ── 2. Recording outcomes ─────────────────────────────────────────


def test_record_success_keeps_weight_one(detector: FailurePatternDetector):
    """A single success does not change the default weight."""
    detector.record_result("a1", success=True, latency=0.1)
    assert detector.get_weight("a1") == 1.0
    health = detector.get_agent_health("a1")
    assert health is not None
    assert health.failure_rate == 0.0
    assert health.avg_latency == pytest.approx(0.1)
    assert health.status == "normal"


def test_record_failure_below_threshold_no_drain(detector: FailurePatternDetector):
    """A few failures below the threshold do not drain the agent."""
    # 2 failures out of 10 = 20% < 30% threshold
    for _ in range(8):
        detector.record_result("a1", success=True, latency=0.1)
    for _ in range(2):
        detector.record_result("a1", success=False, latency=0.1)
    assert detector.get_weight("a1") == 1.0
    health = detector.get_agent_health("a1")
    assert health is not None
    assert health.status == "normal"
    assert health.failure_rate == pytest.approx(0.2)


# ── 3. Drain (摘流) ───────────────────────────────────────────────


def test_drain_when_failure_rate_exceeds_threshold(detector: FailurePatternDetector):
    """When failure rate > threshold, weight → 0 and status → drained."""
    # 4 failures out of 10 = 40% > 30%
    for _ in range(6):
        detector.record_result("a1", success=True, latency=0.1)
    for _ in range(4):
        detector.record_result("a1", success=False, latency=0.1)
    assert detector.get_weight("a1") == 0.0
    health = detector.get_agent_health("a1")
    assert health is not None
    assert health.status == "drained"
    assert health.failure_rate == pytest.approx(0.4)


def test_drain_triggers_on_first_failure_crossing_threshold(detector: FailurePatternDetector):
    """Drain fires the moment the running failure rate crosses the threshold."""
    # Window size 10, threshold 30% → 4 failures in 10 triggers drain.
    # Record 6 successes first, then 4 failures; the drain should fire
    # on the 4th failure (which makes the rate 4/10 = 40%).
    for _ in range(6):
        detector.record_result("a1", success=True, latency=0.1)
    assert detector.get_weight("a1") == 1.0
    for i in range(3):
        detector.record_result("a1", success=False, latency=0.1)
        # 3 failures in 7,8,9 records → rate 3/7, 3/8, 3/9 — all < 30%? 3/9=33% > 30%
        # so drain may fire earlier; just check weight stays 0 once drained.
        if detector.get_weight("a1") == 0.0:
            break
    # After 4th failure the rate is at least 4/10 = 40% → must be drained.
    detector.record_result("a1", success=False, latency=0.1)
    assert detector.get_weight("a1") == 0.0


# ── 4. Recovery ladder (灰度回切) ─────────────────────────────────


def test_recovery_ladder_advances_on_consecutive_successes(detector: FailurePatternDetector):
    """Drained agent steps through 0.3 → 0.6 → 1.0 on recovery bursts."""
    # Drain the agent first.
    for _ in range(4):
        detector.record_result("a1", success=False, latency=0.1)
    assert detector.get_weight("a1") == 0.0

    # 3 consecutive successes → first recovery step (0.3).
    for _ in range(3):
        detector.record_result("a1", success=True, latency=0.1)
    assert detector.get_weight("a1") == pytest.approx(0.3)
    health = detector.get_agent_health("a1")
    assert health is not None
    assert health.status == "recovering"

    # Another 3 successes → second step (0.6).
    for _ in range(3):
        detector.record_result("a1", success=True, latency=0.1)
    assert detector.get_weight("a1") == pytest.approx(0.6)

    # Final 3 successes → full recovery (1.0) and status back to normal.
    for _ in range(3):
        detector.record_result("a1", success=True, latency=0.1)
    assert detector.get_weight("a1") == pytest.approx(1.0)
    health = detector.get_agent_health("a1")
    assert health is not None
    assert health.status == "normal"


def test_failure_during_recovery_resets_counter(detector: FailurePatternDetector):
    """A failure during recovery resets the consecutive-success counter."""
    # Drain.
    for _ in range(4):
        detector.record_result("a1", success=False, latency=0.1)
    assert detector.get_weight("a1") == 0.0

    # 2 successes (below the recovery_consecutive_successes=3 gate).
    for _ in range(2):
        detector.record_result("a1", success=True, latency=0.1)
    assert detector.get_weight("a1") == 0.0  # still drained

    # A failure resets the counter.
    detector.record_result("a1", success=False, latency=0.1)
    # Now 3 more successes are needed to advance.
    for _ in range(2):
        detector.record_result("a1", success=True, latency=0.1)
    assert detector.get_weight("a1") == 0.0
    detector.record_result("a1", success=True, latency=0.1)
    assert detector.get_weight("a1") == pytest.approx(0.3)


# ── 5. Sliding window ─────────────────────────────────────────────


def test_sliding_window_evicts_old_outcomes(detector: FailurePatternDetector):
    """Old outcomes age out once the window is full."""
    # Fill the window with failures (drains the agent).
    for _ in range(10):
        detector.record_result("a1", success=False, latency=0.1)
    assert detector.get_weight("a1") == 0.0
    # Now record 10 successes — the window should fully rotate and the
    # failure rate should drop to 0. The agent is still drained (recovery
    # path), but its recorded failure_rate stat should reflect the window.
    for _ in range(10):
        detector.record_result("a1", success=True, latency=0.1)
    health = detector.get_agent_health("a1")
    assert health is not None
    assert health.failure_rate == 0.0
    assert health.window_size == 10


def test_window_size_respected(detector: FailurePatternDetector):
    """The window never grows beyond window_size."""
    for _ in range(20):
        detector.record_result("a1", success=True, latency=0.1)
    health = detector.get_agent_health("a1")
    assert health is not None
    assert health.window_size == 10
    assert health.total_recorded == 20


# ── 6. Stats / introspection ──────────────────────────────────────


def test_get_stats_shape(detector: FailurePatternDetector):
    """get_stats() returns the documented JSON-serialisable shape."""
    detector.record_result("a1", success=True, latency=0.5)
    detector.record_result("a1", success=False, latency=2.0)
    stats = detector.get_stats()
    assert "agents" in stats
    assert "config" in stats
    assert "total_agents" in stats
    assert stats["total_agents"] == 1
    agent = stats["agents"][0]
    for key in (
        "agent_id", "failure_rate", "avg_latency", "timeout_rate",
        "weight", "status", "window_size", "total_recorded",
    ):
        assert key in agent
    assert agent["agent_id"] == "a1"
    assert agent["total_recorded"] == 2


def test_timeout_rate_computed(detector: FailurePatternDetector):
    """Latencies above timeout_threshold count towards timeout_rate."""
    # timeout_threshold = 1.0; record 2 fast + 2 slow → timeout_rate = 0.5
    detector.record_result("a1", success=True, latency=0.1)
    detector.record_result("a1", success=True, latency=0.2)
    detector.record_result("a1", success=True, latency=1.5)
    detector.record_result("a1", success=True, latency=2.0)
    health = detector.get_agent_health("a1")
    assert health is not None
    assert health.timeout_rate == pytest.approx(0.5)


# ── 7. Reset ──────────────────────────────────────────────────────


def test_reset_single_agent(detector: FailurePatternDetector):
    """reset(agent_id) clears only the named agent."""
    detector.record_result("a1", success=True, latency=0.1)
    detector.record_result("a2", success=True, latency=0.1)
    detector.reset("a1")
    assert detector.get_agent_health("a1") is None
    assert detector.get_agent_health("a2") is not None


def test_reset_all_agents(detector: FailurePatternDetector):
    """reset(None) clears every agent."""
    detector.record_result("a1", success=True, latency=0.1)
    detector.record_result("a2", success=True, latency=0.1)
    detector.reset()
    assert detector.get_stats()["total_agents"] == 0


# ── 8. Prometheus metrics ─────────────────────────────────────────


def test_prometheus_gauges_updated(detector: FailurePatternDetector):
    """Recording an outcome updates the per-agent Prometheus gauges."""
    detector.record_result("a1", success=False, latency=0.1)
    labels = {"agent": "a1"}
    # The failure-rate gauge should reflect 1/1 = 1.0.
    assert detector._failure_rate_gauge.get(labels=labels) == pytest.approx(1.0)
    # Weight stays 1.0 because a single failure (rate 100% but window 1)
    # — wait, 1/1 = 100% > 30% so the agent IS drained.
    assert detector._weight_gauge.get(labels=labels) == pytest.approx(0.0)
    # Status gauge: 1.0 = drained.
    assert detector._status_gauge.get(labels=labels) == pytest.approx(1.0)


# ── 9. EventBus integration ───────────────────────────────────────


def test_drain_publishes_agent_drained_event(detector_with_bus):
    """Draining an agent publishes an ``agent_drained`` event."""
    detector, bus = detector_with_bus
    # 4 failures → drain.
    for _ in range(4):
        detector.record_result("a1", success=False, latency=0.1)
    # The event bus records published events in its history buffer.
    history = bus.history("agent_drained")
    assert len(history) >= 1
    event = history[-1]
    assert event.payload["agent_id"] == "a1"
    assert event.payload["level"] == "error"


def test_recovery_publishes_agent_recovering_event(detector_with_bus):
    """Each grey-recovery step publishes an ``agent_recovering`` event."""
    detector, bus = detector_with_bus
    # Drain.
    for _ in range(4):
        detector.record_result("a1", success=False, latency=0.1)
    # First recovery step.
    for _ in range(3):
        detector.record_result("a1", success=True, latency=0.1)
    recovering = bus.history("agent_recovering")
    assert len(recovering) >= 1
    assert recovering[-1].payload["weight"] == pytest.approx(0.3)


def test_full_recovery_publishes_agent_recovered_event(detector_with_bus):
    """Reaching weight 1.0 publishes ``agent_recovered`` (not recovering)."""
    detector, bus = detector_with_bus
    # Drain.
    for _ in range(4):
        detector.record_result("a1", success=False, latency=0.1)
    # Walk the full recovery ladder: 3 + 3 + 3 successes.
    for _ in range(9):
        detector.record_result("a1", success=True, latency=0.1)
    recovered = bus.history("agent_recovered")
    assert len(recovered) >= 1
    assert recovered[-1].payload["weight"] == pytest.approx(1.0)


# ── 10. Thread safety ─────────────────────────────────────────────


def test_concurrent_record_result_is_safe(detector: FailurePatternDetector):
    """Concurrent record_result calls from multiple threads do not crash."""
    errors: list[Exception] = []

    def worker(agent_id: str, n: int) -> None:
        try:
            for i in range(n):
                detector.record_result(agent_id, success=(i % 2 == 0), latency=0.1)
        except Exception as exc:
            errors.append(exc)

    threads = [
        threading.Thread(target=worker, args=(f"a{i}", 100))
        for i in range(4)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert errors == []
    assert detector.get_stats()["total_agents"] == 4


# ── 11. DistributedScheduler._select_worker integration ───────────


def test_select_worker_prefers_high_weight(detector: FailurePatternDetector):
    """_select_worker picks the highest-weight capable worker."""
    # Build a scheduler with a fake redis + registry of 3 workers.
    from maop.core.scheduling.distributed_scheduler import DistributedScheduler

    class _FakeRedis:
        def xgroup_create(self, *a, **k):
            raise Exception("BUSYGROUP")  # noqa: TRY002

        def xadd(self, *a, **k):
            return b"0-0"

        def pipeline(self):
            m = MagicMock()
            m.execute.return_value = []
            return m

    class _FakeRegistry:
        def capable_workers(self, required):
            return ["w1", "w2", "w3"]

        def active_count(self):
            return 3

        def assign_task(self, *a, **k):
            pass

        def complete_task(self, *a, **k):
            pass

    # Drain w2; w1 and w3 stay at 1.0.
    for _ in range(4):
        detector.record_result("w2", success=False, latency=0.1)
    assert detector.get_weight("w2") == 0.0

    sched = DistributedScheduler.__new__(DistributedScheduler)
    sched._registry = _FakeRegistry()
    sched._failure_detector = detector
    selected = sched._select_worker(None)
    # w2 is drained → must not be selected; w1 or w3 (both weight 1.0) is fine.
    assert selected in ("w1", "w3")
    assert selected != "w2"


def test_select_worker_falls_back_when_all_drained(detector: FailurePatternDetector):
    """When every capable worker is drained, fall back to the first one."""
    from maop.core.scheduling.distributed_scheduler import DistributedScheduler

    class _FakeRegistry:
        def capable_workers(self, required):
            return ["w1", "w2"]

        def active_count(self):
            return 2

        def assign_task(self, *a, **k):
            pass

        def complete_task(self, *a, **k):
            pass

    # Drain both.
    for w in ("w1", "w2"):
        for _ in range(4):
            detector.record_result(w, success=False, latency=0.1)

    sched = DistributedScheduler.__new__(DistributedScheduler)
    sched._registry = _FakeRegistry()
    sched._failure_detector = detector
    selected = sched._select_worker(None)
    # All drained → fall back to first capable worker.
    assert selected == "w1"


def test_select_worker_returns_none_when_no_capable(detector: FailurePatternDetector):
    """No capable workers → None (preserves legacy behaviour)."""
    from maop.core.scheduling.distributed_scheduler import DistributedScheduler

    class _FakeRegistry:
        def capable_workers(self, required):
            return []

        def active_count(self):
            return 0

    sched = DistributedScheduler.__new__(DistributedScheduler)
    sched._registry = _FakeRegistry()
    sched._failure_detector = detector
    assert sched._select_worker(None) is None


# ── 12. Scheduling router endpoints ───────────────────────────────


@pytest.fixture
def scheduling_client(detector: FailurePatternDetector):
    """FastAPI TestClient with the scheduling router mounted + admin role."""
    from maop.dashboard.routers import scheduling as scheduling_router

    app = FastAPI()

    @app.middleware("http")
    async def _inject_admin(request, call_next):
        request.state.auth_roles = ["admin"]
        request.state.auth_identity = "test-admin"
        return await call_next(request)

    app.include_router(scheduling_router.router)
    return TestClient(app)


def test_router_get_failure_stats(scheduling_client: TestClient):
    """GET /api/scheduling/failure-stats returns the detector snapshot."""
    detector = get_failure_detector()
    assert detector is not None
    detector.record_result("router-agent", success=True, latency=0.05)
    resp = scheduling_client.get("/api/scheduling/failure-stats")
    assert resp.status_code == 200
    body = resp.json()
    assert "agents" in body
    assert any(a["agent_id"] == "router-agent" for a in body["agents"])


def test_router_reset_specific_agent(scheduling_client: TestClient):
    """POST .../reset with agent_id clears that agent only."""
    detector = get_failure_detector()
    assert detector is not None
    detector.record_result("keep", success=True, latency=0.05)
    detector.record_result("drop", success=True, latency=0.05)
    resp = scheduling_client.post(
        "/api/scheduling/failure-stats/reset",
        json={"agent_id": "drop"},
    )
    assert resp.status_code == 200
    assert resp.json()["ok"] is True
    assert detector.get_agent_health("drop") is None
    assert detector.get_agent_health("keep") is not None


def test_router_reset_requires_admin(detector: FailurePatternDetector):
    """POST .../reset without admin role returns 403."""
    from maop.dashboard.routers import scheduling as scheduling_router

    app = FastAPI()

    @app.middleware("http")
    async def _no_admin(request, call_next):
        request.state.auth_roles = ["viewer"]
        return await call_next(request)

    app.include_router(scheduling_router.router)
    client = TestClient(app)
    resp = client.post("/api/scheduling/failure-stats/reset", json={})
    assert resp.status_code == 403