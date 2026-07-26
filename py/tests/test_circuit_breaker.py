"""Tests for MAOP.core.circuit_breaker — SQLite-backed with failover and health-check."""

import asyncio
import contextlib
import shutil
import tempfile
import time
from pathlib import Path

import pytest

from maop.core.circuit_breaker import (
    BreakerState,
    CircuitBreaker,
)


@pytest.fixture
def breaker() -> CircuitBreaker:
    """Create a CircuitBreaker with a temp DB."""
    tmp = tempfile.mkdtemp(prefix="MAOP_cb_")
    db_path = Path(tmp) / "maop.db"
    cb = CircuitBreaker(path=db_path)
    yield cb
    with contextlib.suppress(Exception):
        shutil.rmtree(tmp, ignore_errors=True)


class TestCircuitBreaker:
    def test_init_seeds_default_agents(self, breaker: CircuitBreaker):
        entry = breaker.get("claude")
        assert entry is not None
        assert entry.state == BreakerState.CLOSED

    def test_get_known_agent(self, breaker: CircuitBreaker):
        entry = breaker.get("claude")
        assert entry is not None
        assert entry.state == BreakerState.CLOSED

    def test_get_unknown_agent(self, breaker: CircuitBreaker):
        assert breaker.get("nonexistent") is None

    def test_record_success(self, breaker: CircuitBreaker):
        breaker.record_failure("claude")
        breaker.record_failure("claude")
        entry = breaker.record_success("claude")
        assert entry.state == BreakerState.CLOSED
        assert entry.failures == 0

    def test_record_failure_opens_after_threshold(self, breaker: CircuitBreaker):
        entry = breaker.get("claude")
        threshold = entry.threshold  # default 3
        for _ in range(threshold):
            breaker.record_failure("claude")
        entry = breaker.get("claude")
        assert entry.state == BreakerState.OPEN

    def test_is_available_closed(self, breaker: CircuitBreaker):
        assert breaker.is_available("claude") is True

    def test_is_available_open(self, breaker: CircuitBreaker):
        breaker.set_state("claude", BreakerState.OPEN, failures=3)
        assert breaker.is_available("claude") is False

    def test_is_available_half_open(self, breaker: CircuitBreaker):
        breaker.set_state("claude", BreakerState.HALF_OPEN)
        assert breaker.is_available("claude") is True

    def test_set_state_new_agent(self, breaker: CircuitBreaker):
        entry = breaker.set_state("new-agent", BreakerState.OPEN, failures=5)
        assert entry.state == BreakerState.OPEN
        assert entry.failures == 5

    def test_all_states(self, breaker: CircuitBreaker):
        states = breaker.all_states()
        assert "claude" in states
        assert isinstance(states["claude"].state, BreakerState)

    def test_sqlite_persistence(self, breaker: CircuitBreaker):
        """Verify state persists across instances."""
        breaker.record_failure("claude")
        breaker.record_failure("claude")
        breaker.record_failure("claude")

        # Create new instance with same DB
        cb2 = CircuitBreaker(path=breaker._path)
        entry = cb2.get("claude")
        assert entry is not None
        assert entry.state == BreakerState.OPEN


class TestFailover:
    def test_register_failover(self, breaker: CircuitBreaker):
        breaker.register_failover("codegen", ["claude", "kimi", "codex"])
        chain = breaker.get_failover_chain("codegen")
        assert chain is not None
        assert chain.agents == ["claude", "kimi", "codex"]
        assert chain.current == "claude"

    def test_resolve_failover_primary(self, breaker: CircuitBreaker):
        breaker.register_failover("codegen", ["claude", "kimi", "codex"])
        result = breaker.resolve_failover("codegen")
        assert result is not None
        assert result.agent == "claude"
        assert result.is_primary is True
        assert result.degraded is False

    def test_resolve_failover_fallback(self, breaker: CircuitBreaker):
        breaker.register_failover("codegen", ["claude", "kimi", "codex"])
        # Open claude → should fallback to kimi
        breaker.set_state("claude", BreakerState.OPEN, failures=3)
        result = breaker.resolve_failover("codegen")
        assert result is not None
        assert result.agent == "kimi"
        assert result.is_primary is False
        assert result.degraded is True

    def test_resolve_failover_all_open(self, breaker: CircuitBreaker):
        breaker.register_failover("codegen", ["claude", "kimi", "codex"])
        breaker.set_state("claude", BreakerState.OPEN, failures=3)
        breaker.set_state("kimi", BreakerState.OPEN, failures=3)
        breaker.set_state("codex", BreakerState.OPEN, failures=3)
        result = breaker.resolve_failover("codegen")
        assert result is None

    def test_record_failure_advances_failover(self, breaker: CircuitBreaker):
        breaker.register_failover("codegen", ["claude", "kimi"])
        # claude is current → fail 3 times → opens → chain advances
        breaker.record_failure("claude")
        breaker.record_failure("claude")
        breaker.record_failure("claude")

        chain = breaker.get_failover_chain("codegen")
        assert chain.current == "kimi"

    def test_record_success_resets_failover(self, breaker: CircuitBreaker):
        breaker.register_failover("codegen", ["claude", "kimi"])
        # Advance to kimi
        breaker.record_failure("claude")
        breaker.record_failure("claude")
        breaker.record_failure("claude")

        chain = breaker.get_failover_chain("codegen")
        assert chain.current == "kimi"

        # kimi succeeds → chain resets to primary
        breaker.record_success("kimi")
        chain = breaker.get_failover_chain("codegen")
        assert chain.current == "claude"

    def test_resolve_nonexistent_chain(self, breaker: CircuitBreaker):
        result = breaker.resolve_failover("nonexistent")
        assert result is None


class TestHealthCheck:
    def test_health_check_recovers_half_open(self, breaker: CircuitBreaker):
        # Set claude to half-open with old failure time
        entry = breaker.set_state("claude", BreakerState.HALF_OPEN)
        entry.last_failure = time.time() - 120  # 2 min ago
        breaker._save_agent("claude", entry)

        recovered = asyncio.run(breaker.health_check(probe=lambda n: True))
        assert "claude" in recovered
        assert recovered["claude"] == BreakerState.CLOSED

    def test_health_check_no_recovery_for_recent(self, breaker: CircuitBreaker):
        entry = breaker.set_state("claude", BreakerState.HALF_OPEN)
        entry.last_failure = time.time()  # just now
        breaker._save_agent("claude", entry)

        recovered = asyncio.run(breaker.health_check(probe=lambda n: False))
        assert recovered["claude"] == BreakerState.HALF_OPEN

    def test_get_open_agents(self, breaker: CircuitBreaker):
        breaker.set_state("claude", BreakerState.OPEN, failures=3)
        breaker.set_state("kimi", BreakerState.OPEN, failures=3)

        open_agents = breaker.get_open_agents()
        assert "claude" in open_agents
        assert "kimi" in open_agents

    def test_get_half_open_agents(self, breaker: CircuitBreaker):
        breaker.set_state("claude", BreakerState.HALF_OPEN)

        half_open = breaker.get_half_open_agents()
        assert "claude" in half_open
