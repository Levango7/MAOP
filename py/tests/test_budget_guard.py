"""Tests for MAOP.core.budget_guard — Daily token/cost budget enforcement."""

import shutil
import tempfile

import pytest

from maop.core.budget_guard import BudgetGuard, BudgetStatus


@pytest.fixture
def guard_env():
    tmpdir = tempfile.mkdtemp()
    guard = BudgetGuard(
        root_dir=tmpdir,
        daily_token_limit=1000,
        daily_cost_limit=1.0,
    )
    yield guard
    shutil.rmtree(tmpdir, ignore_errors=True)


@pytest.fixture
def guard_env_no_limits():
    tmpdir = tempfile.mkdtemp()
    guard = BudgetGuard(root_dir=tmpdir, daily_token_limit=0, daily_cost_limit=0.0)
    yield guard
    shutil.rmtree(tmpdir, ignore_errors=True)


class TestCheckBudget:
    def test_within_budget(self, guard_env):
        assert guard_env.check_budget() is True

    def test_exceeds_token_limit(self, guard_env):
        guard_env.record_usage(prompt_tokens=800, completion_tokens=300)
        assert guard_env.check_budget() is False

    def test_exceeds_cost_limit(self, guard_env):
        guard_env.record_usage(cost_usd=1.5)
        assert guard_env.check_budget() is False

    def test_no_limits_always_passes(self, guard_env_no_limits):
        guard_env_no_limits.record_usage(prompt_tokens=999999, cost_usd=999.0)
        assert guard_env_no_limits.check_budget() is True


class TestRecordUsage:
    def test_first_record(self, guard_env):
        status = guard_env.record_usage(prompt_tokens=100, completion_tokens=50, cost_usd=0.1)
        assert status.tokens_used == 150
        assert status.cost_used == pytest.approx(0.1)
        assert status.calls_count == 1
        assert status.budget_exceeded is False

    def test_accumulates(self, guard_env):
        guard_env.record_usage(prompt_tokens=100, completion_tokens=50, cost_usd=0.1)
        status = guard_env.record_usage(prompt_tokens=200, completion_tokens=100, cost_usd=0.3)
        assert status.tokens_used == 450
        assert status.cost_used == pytest.approx(0.4)
        assert status.calls_count == 2

    def test_budget_exceeded_flag(self, guard_env):
        status = guard_env.record_usage(prompt_tokens=800, completion_tokens=300, cost_usd=0.0)
        assert status.budget_exceeded is True
        assert "Token limit" in status.reason

    def test_cost_exceeded_flag(self, guard_env):
        status = guard_env.record_usage(prompt_tokens=0, completion_tokens=0, cost_usd=1.5)
        assert status.budget_exceeded is True
        assert "Cost limit" in status.reason


class TestGetStatus:
    def test_empty_status(self, guard_env):
        status = guard_env.get_status()
        assert status.tokens_used == 0
        assert status.cost_used == 0.0
        assert status.calls_count == 0
        assert status.budget_exceeded is False

    def test_after_usage(self, guard_env):
        guard_env.record_usage(prompt_tokens=100, completion_tokens=50, cost_usd=0.2)
        status = guard_env.get_status()
        assert status.tokens_used == 150
        assert status.cost_used == pytest.approx(0.2)
        assert status.calls_count == 1


class TestResetDaily:
    def test_reset_clears_counters(self, guard_env):
        guard_env.record_usage(prompt_tokens=500, completion_tokens=200, cost_usd=0.5)
        guard_env.reset_daily()
        status = guard_env.get_status()
        assert status.tokens_used == 0
        assert status.cost_used == 0.0
        assert status.calls_count == 0

    def test_after_reset_budget_passes(self, guard_env):
        guard_env.record_usage(prompt_tokens=800, completion_tokens=300, cost_usd=0.0)
        assert guard_env.check_budget() is False
        guard_env.reset_daily()
        assert guard_env.check_budget() is True


class TestBudgetStatus:
    def test_defaults(self):
        s = BudgetStatus()
        assert s.date == ""
        assert s.tokens_used == 0
        assert s.budget_exceeded is False

    def test_model_dump(self):
        s = BudgetStatus(date="2025-01-01", tokens_used=100, tokens_limit=1000)
        d = s.model_dump()
        assert d["date"] == "2025-01-01"
        assert d["tokens_used"] == 100
