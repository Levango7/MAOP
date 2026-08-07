"""Tests for Phase γ-1 SLA-aware task scheduling.

Covers:
  - Plan SLA field defaults / custom values / helpers (is_deadline_urgent,
    effective_priority_score).
  - Dispatcher acceptance of priority / deadline_ms parameters.
  - SLA metric registration and recording (Phase α style).

Reference style: test_observability.py + test_maop_plan.py.
"""
from __future__ import annotations

import asyncio
import time

import pytest

from maop.core.monitoring.monitoring import (
    MAOP_TASK_DEADLINE_SECONDS,
    MAOP_TASK_PRIORITY_DISTRIBUTION,
    MAOP_TASK_SLA_TIER_DISTRIBUTION,
    MAOP_TASK_SLA_VIOLATION_TOTAL,
    metrics,
)
from maop.delegate.dispatcher import Dispatcher
from maop.delegate.sla_monitor import SLAMonitor
from maop.maop_plan import Plan

_tier_from_priority = SLAMonitor.tier_from_priority

# ── Plan SLA fields ───────────────────────────────────────────────


class TestPlanSLA:
    def test_default_sla_fields(self):
        """Plan with no SLA args uses backward-compatible defaults."""
        plan = Plan()
        assert plan.deadline_ms is None
        assert plan.priority == 3
        assert plan.sla_tier == "standard"

    def test_custom_sla_fields(self):
        plan = Plan(
            deadline_ms=1_700_000_000_000,
            priority=1,
            sla_tier="critical",
        )
        assert plan.deadline_ms == 1_700_000_000_000
        assert plan.priority == 1
        assert plan.sla_tier == "critical"

    def test_backward_compat_existing_fields(self):
        """Existing Plan fields keep their defaults after γ-1 extension."""
        plan = Plan()
        assert plan.phase == "plan"
        assert plan.selected_agent == "claude"
        assert plan.routing_key == "chat"
        assert plan.gates == ["exit_code", "output"]
        assert plan.budget == {"timeout_s": 120, "max_retries": 1}

    def test_priority_full_range_accepted(self):
        for p in (1, 2, 3, 4, 5):
            plan = Plan(priority=p)
            assert plan.priority == p

    def test_sla_tier_all_values_accepted(self):
        for tier in ("best_effort", "standard", "critical"):
            plan = Plan(sla_tier=tier)
            assert plan.sla_tier == tier

    # ── is_deadline_urgent ────────────────────────────────────────

    def test_is_deadline_urgent_none(self):
        """No deadline → not urgent."""
        plan = Plan()
        assert plan.is_deadline_urgent() is False

    def test_is_deadline_urgent_far_future(self):
        """Deadline far in the future → not urgent."""
        future_ms = int(time.time() * 1000) + 3_600_000  # +1h
        plan = Plan(deadline_ms=future_ms)
        assert plan.is_deadline_urgent() is False

    def test_is_deadline_urgent_close(self):
        """Deadline within default 30s threshold → urgent."""
        soon_ms = int(time.time() * 1000) + 5_000  # +5s
        plan = Plan(deadline_ms=soon_ms)
        assert plan.is_deadline_urgent() is True

    def test_is_deadline_urgent_past(self):
        """Deadline already passed → urgent."""
        past_ms = int(time.time() * 1000) - 1_000  # -1s
        plan = Plan(deadline_ms=past_ms)
        assert plan.is_deadline_urgent() is True

    def test_is_deadline_urgent_custom_threshold(self):
        """Custom threshold respected."""
        future_ms = int(time.time() * 1000) + 60_000  # +60s
        plan = Plan(deadline_ms=future_ms)
        # Default threshold (30s) → not urgent
        assert plan.is_deadline_urgent() is False
        # Wider threshold (120s) → urgent
        assert plan.is_deadline_urgent(threshold_ms=120_000) is True

    # ── effective_priority_score ──────────────────────────────────

    def test_effective_priority_score_in_range(self):
        for p in (1, 2, 3, 4, 5):
            plan = Plan(priority=p)
            score = plan.effective_priority_score()
            assert 0.0 <= score <= 1.0

    def test_effective_priority_score_priority_ordering(self):
        """Higher priority (lower int) → higher score (without deadline)."""
        p1 = Plan(priority=1).effective_priority_score()
        p3 = Plan(priority=3).effective_priority_score()
        p5 = Plan(priority=5).effective_priority_score()
        assert p1 > p3 > p5

    def test_effective_priority_score_deadline_boost(self):
        """Past deadline boosts score above the no-deadline baseline."""
        base = Plan(priority=3).effective_priority_score()
        past_ms = int(time.time() * 1000) - 1_000
        boosted = Plan(priority=3, deadline_ms=past_ms).effective_priority_score()
        assert boosted > base

    def test_effective_priority_score_no_deadline_neutral(self):
        """No deadline → deadline component is neutral 0.5."""
        # priority=3 → priority_score = (6-3)/5 = 0.6
        # deadline_score = 0.5 (neutral)
        # total = 0.6*0.6 + 0.4*0.5 = 0.36 + 0.2 = 0.56
        plan = Plan(priority=3)
        assert plan.effective_priority_score() == pytest.approx(0.56)

    def test_effective_priority_score_past_deadline_max(self):
        """Past deadline → deadline component 1.0 (max urgency)."""
        past_ms = int(time.time() * 1000) - 1_000
        plan = Plan(priority=3, deadline_ms=past_ms)
        # priority=3 → 0.6 ; deadline → 1.0
        # total = 0.6*0.6 + 0.4*1.0 = 0.36 + 0.4 = 0.76
        assert plan.effective_priority_score() == pytest.approx(0.76)

    def test_effective_priority_score_clamps_invalid_priority(self):
        """Out-of-range priority is clamped, not rejected."""
        low = Plan(priority=-5).effective_priority_score()
        high = Plan(priority=100).effective_priority_score()
        assert low == pytest.approx(1.0 * 0.6 + 0.4 * 0.5)  # clamped to p=1
        assert high == pytest.approx(0.2 * 0.6 + 0.4 * 0.5)  # clamped to p=5


# ── Dispatcher SLA parameters ─────────────────────────────────────


class TestDispatcherSLA:
    def test_tier_from_priority_mapping(self):
        assert _tier_from_priority(1) == "critical"
        assert _tier_from_priority(2) == "standard"
        assert _tier_from_priority(3) == "standard"
        assert _tier_from_priority(4) == "best_effort"
        assert _tier_from_priority(5) == "best_effort"

    def test_dispatch_accepts_priority(self):
        """dispatch(priority=...) does not raise."""
        dispatcher = Dispatcher()
        result = asyncio.run(
            dispatcher.dispatch(agent="nonexistent", task="t", priority=1)
        )
        # Agent not found → error result, but no exception
        assert result is not None
        assert not result.result.is_success()

    def test_dispatch_accepts_deadline_ms(self):
        dispatcher = Dispatcher()
        future_ms = int(time.time() * 1000) + 60_000
        result = asyncio.run(
            dispatcher.dispatch(agent="nonexistent", task="t", deadline_ms=future_ms)
        )
        assert result is not None
        assert not result.result.is_success()

    def test_dispatch_accepts_both_sla_params(self):
        dispatcher = Dispatcher()
        future_ms = int(time.time() * 1000) + 60_000
        result = asyncio.run(
            dispatcher.dispatch(
                agent="nonexistent", task="t",
                priority=2, deadline_ms=future_ms,
            )
        )
        assert result is not None

    def test_dispatch_default_sla_params(self):
        """dispatch without SLA params uses defaults (backward compatible)."""
        dispatcher = Dispatcher()
        result = asyncio.run(
            dispatcher.dispatch(agent="nonexistent", task="t")
        )
        assert result is not None

    def test_dispatch_past_deadline_records_violation(self):
        """A past deadline triggers the SLA violation counter."""
        dispatcher = Dispatcher()
        past_ms = int(time.time() * 1000) - 10_000
        before = MAOP_TASK_SLA_VIOLATION_TOTAL.get()
        asyncio.run(
            dispatcher.dispatch(
                agent="nonexistent", task="t",
                priority=1, deadline_ms=past_ms,
            )
        )
        after = MAOP_TASK_SLA_VIOLATION_TOTAL.get()
        assert after > before


# ── SLA Metrics registration ─────────────────────────────────────


class TestSLAMetrics:
    def test_metrics_registered_in_collector(self):
        """All 4 SLA metrics are registered in the global MetricsCollector."""
        assert "MAOP_task_deadline_seconds" in metrics._histograms
        assert "MAOP_task_sla_violation_total" in metrics._counters
        assert "MAOP_task_priority_distribution" in metrics._gauges
        assert "MAOP_task_sla_tier_distribution" in metrics._gauges

    def test_metric_module_constants_exposed(self):
        """Metric objects are importable as module-level constants."""
        from maop.core.monitoring.monitoring import (
            MAOP_TASK_DEADLINE_SECONDS,
            MAOP_TASK_PRIORITY_DISTRIBUTION,
            MAOP_TASK_SLA_TIER_DISTRIBUTION,
            MAOP_TASK_SLA_VIOLATION_TOTAL,
        )
        assert MAOP_TASK_DEADLINE_SECONDS.name == "MAOP_task_deadline_seconds"
        assert MAOP_TASK_SLA_VIOLATION_TOTAL.name == "MAOP_task_sla_violation_total"
        assert MAOP_TASK_PRIORITY_DISTRIBUTION.name == "MAOP_task_priority_distribution"
        assert MAOP_TASK_SLA_TIER_DISTRIBUTION.name == "MAOP_task_sla_tier_distribution"

    def test_priority_distribution_gauge_labels(self):
        """Gauge tracks per-priority counts via labels."""
        g = MAOP_TASK_PRIORITY_DISTRIBUTION
        before = g.get(labels={"priority": "1"})
        g.inc(labels={"priority": "1"})
        g.inc(labels={"priority": "1"})
        g.inc(labels={"priority": "5"})
        assert g.get(labels={"priority": "1"}) == before + 2.0
        assert g.get(labels={"priority": "5"}) >= 1.0
        # cleanup
        g.dec(labels={"priority": "1"}, value=2.0)
        g.dec(labels={"priority": "5"})

    def test_sla_tier_distribution_gauge_labels(self):
        g = MAOP_TASK_SLA_TIER_DISTRIBUTION
        before = g.get(labels={"tier": "critical"})
        g.inc(labels={"tier": "critical"})
        assert g.get(labels={"tier": "critical"}) == before + 1.0
        g.dec(labels={"tier": "critical"})

    def test_sla_violation_counter_increments(self):
        c = MAOP_TASK_SLA_VIOLATION_TOTAL
        before = c.get()
        c.inc()
        assert c.get() == before + 1.0

    def test_deadline_seconds_histogram_observes(self):
        h = MAOP_TASK_DEADLINE_SECONDS
        before_count = h._total
        h.observe(-5.0)  # missed by 5s
        assert h._total == before_count + 1
        assert h._sum == pytest.approx(-5.0, rel=1e-6) if before_count == 0 else True

    def test_all_sla_metrics_in_prometheus_export(self):
        """All SLA metrics appear in the Prometheus text export."""
        out = metrics.to_prometheus()
        assert "# TYPE MAOP_task_deadline_seconds histogram" in out
        assert "# TYPE MAOP_task_sla_violation_total counter" in out
        assert "# TYPE MAOP_task_priority_distribution gauge" in out
        assert "# TYPE MAOP_task_sla_tier_distribution gauge" in out
