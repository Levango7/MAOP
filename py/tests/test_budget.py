"""Comprehensive tests for MAOP.model.budget — BudgetGuard."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from maop.model.budget import BudgetGuard
from maop.model.schema import BudgetConfig

# ── Fixtures ──────────────────────────────────────────────────

@pytest.fixture
def budget_config() -> BudgetConfig:
    return BudgetConfig(
        daily_limit=1.0,
        monthly_limit=10.0,
        alert_threshold=0.8,
        hard_stop=True,
    )


@pytest.fixture
def no_stop_config() -> BudgetConfig:
    return BudgetConfig(
        daily_limit=1.0,
        monthly_limit=10.0,
        hard_stop=False,
    )


@pytest.fixture
def guard(tmp_path, budget_config) -> BudgetGuard:
    return BudgetGuard(root_dir=tmp_path, config=budget_config)


@pytest.fixture
def no_stop_guard(tmp_path, no_stop_config) -> BudgetGuard:
    return BudgetGuard(root_dir=tmp_path, config=no_stop_config)


# ── Initialization Tests ─────────────────────────────────────

class TestBudgetGuardInit:
    """Tests for BudgetGuard initialization."""

    def test_init_with_config(self, tmp_path, budget_config):
        g = BudgetGuard(root_dir=tmp_path, config=budget_config)
        assert g._config.daily_limit == 1.0
        assert g._daily_spend == 0.0
        assert g._monthly_spend == 0.0
        assert g._ledger == []
        assert g._alerted is False

    def test_init_default_config(self, tmp_path):
        g = BudgetGuard(root_dir=tmp_path)
        assert g._config.daily_limit == 5.0
        assert g._config.monthly_limit == 100.0

    def test_init_no_root(self):
        g = BudgetGuard()
        assert g._root == Path.cwd()

    def test_load_ledger_nonexistent(self, tmp_path):
        # JSON ledger path removed (P2-1); guard starts empty in-memory.
        g = BudgetGuard(root_dir=tmp_path)
        assert g._daily_spend == 0.0
        assert g._ledger == []

    def test_load_ledger_with_entries(self, tmp_path):
        # JSON ledger load removed (P2-1). Guard now starts empty regardless
        # of any leftover budget_ledger.json on disk — verify it is ignored.
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        ledger_data = {
            "entries": [
                {"timestamp": f"{today}T10:00:00+00:00", "cost": 0.5, "model": "gpt-4", "provider": "openai"},
                {"timestamp": f"{today}T11:00:00+00:00", "cost": 0.3, "model": "gpt-3.5", "provider": "openai"},
                {"timestamp": "2000-01-01T00:00:00+00:00", "cost": 1.0, "model": "old", "provider": "old"},
            ]
        }
        data_dir = tmp_path / "data"
        data_dir.mkdir(parents=True)
        (data_dir / "budget_ledger.json").write_text(json.dumps(ledger_data), encoding="utf-8")

        g = BudgetGuard(root_dir=tmp_path)
        # In-memory shim ignores on-disk ledger; starts at zero.
        assert g._daily_spend == 0.0
        assert g._monthly_spend == 0.0
        assert g._ledger == []

    def test_load_ledger_corrupt(self, tmp_path):
        # JSON ledger load removed (P2-1); corrupt file is simply ignored.
        data_dir = tmp_path / "data"
        data_dir.mkdir(parents=True)
        (data_dir / "budget_ledger.json").write_text("not json", encoding="utf-8")
        g = BudgetGuard(root_dir=tmp_path)
        # Should not crash, just warn
        assert g._daily_spend == 0.0


# ── can_spend Tests ───────────────────────────────────────────

class TestCanSpend:
    """Tests for BudgetGuard.can_spend."""

    def test_can_spend_within_budget(self, guard):
        assert guard.can_spend(0.5) is True

    def test_can_spend_exact_limit(self, guard):
        assert guard.can_spend(1.0) is True

    def test_can_spend_exceeds_daily(self, guard):
        assert guard.can_spend(1.5) is False

    def test_can_spend_after_spending(self, guard):
        guard.record("model", "prov", 0.6)
        assert guard.can_spend(0.3) is True
        assert guard.can_spend(0.5) is False

    def test_can_spend_no_hard_stop(self, no_stop_guard):
        # hard_stop=False → always True
        assert no_stop_guard.can_spend(999.0) is True

    def test_can_spend_zero_cost(self, guard):
        assert guard.can_spend(0.0) is True

    def test_can_spend_exceeds_monthly(self, tmp_path):
        config = BudgetConfig(daily_limit=100.0, monthly_limit=5.0, hard_stop=True)
        g = BudgetGuard(root_dir=tmp_path, config=config)
        g.record("m", "p", 4.0)
        assert g.can_spend(2.0) is False  # 4+2 > 5 monthly


# ── record Tests ─────────────────────────────────────────────

class TestRecord:
    """Tests for BudgetGuard.record."""

    def test_record_basic(self, guard):
        guard.record("gpt-4", "openai", 0.5, tokens_in=1000, tokens_out=500)
        assert guard._daily_spend == pytest.approx(0.5)
        assert guard._monthly_spend == pytest.approx(0.5)
        assert len(guard._ledger) == 1
        entry = guard._ledger[0]
        assert entry["model"] == "gpt-4"
        assert entry["provider"] == "openai"
        assert entry["cost"] == 0.5
        assert entry["tokens_in"] == 1000
        assert entry["tokens_out"] == 500

    def test_record_multiple(self, guard):
        guard.record("m1", "p1", 0.3)
        guard.record("m2", "p2", 0.2)
        assert guard._daily_spend == pytest.approx(0.5)
        assert len(guard._ledger) == 2

    def test_record_no_json_persistence(self, guard, tmp_path):
        # P2-1: JSON budget_ledger.json persistence removed.
        # record() now only updates in-memory accumulators.
        guard.record("gpt-4", "openai", 0.5)
        ledger_path = tmp_path / "data" / "budget_ledger.json"
        assert not ledger_path.exists()
        assert guard._daily_spend == pytest.approx(0.5)

    def test_record_triggers_alert(self, guard):
        # alert_threshold=0.8, daily_limit=1.0 → alert at 0.8
        guard.record("m", "p", 0.8)
        assert guard._alerted is True

    def test_record_no_alert_below_threshold(self, guard):
        guard.record("m", "p", 0.5)
        assert guard._alerted is False

    def test_record_alert_only_once(self, guard):
        guard.record("m", "p", 0.8)
        assert guard._alerted is True
        # Second record should not re-alert (already alerted)
        guard.record("m", "p", 0.1)
        assert guard._alerted is True

    def test_record_rounds_cost(self, guard):
        guard.record("m", "p", 0.123456789)
        assert guard._ledger[0]["cost"] == round(0.123456789, 6)


# ── stats Tests ───────────────────────────────────────────────

class TestStats:
    """Tests for BudgetGuard.stats."""

    def test_stats_initial(self, guard):
        s = guard.stats()
        assert s["daily_spend"] == 0.0
        assert s["daily_limit"] == 1.0
        assert s["monthly_spend"] == 0.0
        assert s["monthly_limit"] == 10.0
        assert s["alert_threshold"] == 0.8
        assert s["hard_stop"] is True
        assert s["daily_remaining"] == 1.0
        assert s["daily_utilization"] == 0.0
        assert s["alerted"] is False

    def test_stats_after_spending(self, guard):
        guard.record("m", "p", 0.3)
        s = guard.stats()
        assert s["daily_spend"] == 0.3
        assert s["daily_remaining"] == pytest.approx(0.7)
        assert s["daily_utilization"] == pytest.approx(0.3)

    def test_stats_remaining_clamped_to_zero(self, guard):
        guard.record("m", "p", 1.5)
        s = guard.stats()
        assert s["daily_remaining"] == 0.0

    def test_stats_zero_daily_limit(self, tmp_path):
        config = BudgetConfig(daily_limit=0.0, hard_stop=False)
        g = BudgetGuard(root_dir=tmp_path, config=config)
        s = g.stats()
        assert s["daily_utilization"] == 0.0


# ── reset_alert Tests ────────────────────────────────────────

class TestResetAlert:
    """Tests for BudgetGuard.reset_alert."""

    def test_reset_alert(self, guard):
        guard.record("m", "p", 0.8)
        assert guard._alerted is True
        guard.reset_alert()
        assert guard._alerted is False


# ── record_actual_cost Tests ─────────────────────────────────

class TestRecordActualCost:
    """Tests for BudgetGuard.record_actual_cost."""

    def test_record_actual_cost_with_registry(self, guard):
        mock_registry = MagicMock()
        mock_model = MagicMock()
        mock_model.cost_per_1k_input = 0.01
        mock_model.cost_per_1k_output = 0.02
        mock_registry.get_model.return_value = mock_model
        guard._registry = mock_registry

        result = guard.record_actual_cost(
            trace_id="trace-1",
            model="gpt-4",
            provider="openai",
            actual_tokens_in=2000,
            actual_tokens_out=1000,
            estimated_cost=0.05,
        )
        # actual = 0.01*2000/1000 + 0.02*1000/1000 = 0.02 + 0.02 = 0.04
        assert result["trace_id"] == "trace-1"
        assert result["actual_cost"] == pytest.approx(0.04)
        assert result["estimated_cost"] == 0.05
        assert result["discrepancy"] == pytest.approx(round(0.04 - 0.05, 6))
        assert result["tokens_in"] == 2000
        assert result["tokens_out"] == 1000

    def test_record_actual_cost_no_model_in_registry(self, guard):
        mock_registry = MagicMock()
        mock_registry.get_model.return_value = None
        guard._registry = mock_registry

        result = guard.record_actual_cost(
            trace_id="trace-2",
            model="unknown",
            provider="local",
            actual_tokens_in=100,
            actual_tokens_out=50,
            estimated_cost=0.01,
        )
        # Falls back to estimated
        assert result["actual_cost"] == 0.01
        assert result["discrepancy"] == 0.0

    def test_record_actual_cost_large_discrepancy(self, guard):
        mock_registry = MagicMock()
        mock_model = MagicMock()
        mock_model.cost_per_1k_input = 0.10
        mock_model.cost_per_1k_output = 0.20
        mock_registry.get_model.return_value = mock_model
        guard._registry = mock_registry

        result = guard.record_actual_cost(
            trace_id="trace-3",
            model="expensive",
            provider="openai",
            actual_tokens_in=10000,
            actual_tokens_out=5000,
            estimated_cost=0.01,
        )
        # actual = 0.10*10 + 0.20*5 = 1.0 + 1.0 = 2.0
        # discrepancy = 2.0 - 0.01 = 1.99, which is >50% of 0.01
        assert result["actual_cost"] == pytest.approx(2.0)
        assert abs(result["discrepancy"]) > 0.01 * 0.5

    def test_record_actual_cost_records_in_ledger(self, guard):
        mock_registry = MagicMock()
        mock_registry.get_model.return_value = None
        guard._registry = mock_registry

        guard.record_actual_cost(
            model="m", provider="p",
            actual_tokens_in=100, actual_tokens_out=50,
            estimated_cost=0.02,
        )
        assert len(guard._ledger) == 1
        assert guard._daily_spend == pytest.approx(0.02)
