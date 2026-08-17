"""Reliability tests — Plan-Execute-Verify (PEV) pipeline integrity.

Verifies the full Plan→Execute→Verify lifecycle: Plan produces a valid
routing plan, Execute dispatches to an agent and returns a result,
Verify validates the result against gates, and the three phases
maintain state consistency.  Also covers failure paths (agent failure,
budget exhaustion) and state persistence across dispatches.

All tests use mock agents/drivers — no real subprocess or external
service is invoked.  Windows-compatible.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from maop.core.reliability.circuit_breaker import BreakerState, CircuitBreaker
from maop.core.reliability.error_schema import MaopResult, new_result
from maop.delegate.dispatcher import Dispatcher, DispatchResult
from maop.maop_execute import maop_execute
from maop.maop_plan import Plan, maop_plan
from maop.maop_verify import VerifyEngine, VerifyResult
from maop.model.budget import BudgetGuard
from maop.model.schema import BudgetConfig

# ── Helpers ────────────────────────────────────────────────────


def _mock_permission_allow() -> MagicMock:
    """Create a mock PermissionManager that always allows."""
    perm_result = MagicMock()
    perm_result.decision = "allow"
    perm_result.reason = ""
    perm_result.matched_rule = ""
    pm = MagicMock()
    pm.check.return_value = perm_result
    return pm


def _mock_guardrail_pass() -> MagicMock:
    """Create a mock Guardrail that always passes."""
    gr = MagicMock()
    check_result = MagicMock()
    check_result.passed = True
    check_result.reason = ""
    gr.check = MagicMock(return_value=check_result)
    return gr


def _make_mock_agent(name: str = "test-agent", cli: str = "echo") -> MagicMock:
    """Create a mock agent config object with all required attributes."""
    agent = MagicMock()
    agent.name = name
    agent.cli = cli
    agent.driver = "cli"
    agent.cli_args = ""
    agent.capabilities = []
    agent.timeout_s = 10
    agent.model = None
    agent.wrapper = ""
    agent.command = ""
    agent.provider = ""
    return agent


def _make_mock_config(agent: MagicMock, budget: object | None = None) -> MagicMock:
    """Create a mock MAopConfig with one agent and optional budget."""
    config = MagicMock()
    config.agents = [agent]
    config.workflows = []
    config.budget = budget
    return config


def _success_dispatch_result(agent: str = "test-agent", task: str = "test",
                              stdout: str = "ok") -> DispatchResult:
    """Create a successful DispatchResult."""
    return DispatchResult(
        result=new_result(agent=agent, task=task, exit_code=0, stdout=stdout),
        driver_used="cli",
    )


# ── 1. Plan phase ─────────────────────────────────────────────


def test_plan_phase_produces_valid_plan() -> None:
    """Plan phase produces a Plan with agent, routing_key, gates, budget.

    Calls ``maop_plan`` with a representative task and verifies the
    returned ``Plan`` has all required structural fields populated.
    """
    plan = maop_plan("fix the bug in login")

    assert isinstance(plan, Plan)
    assert plan.phase == "plan"
    assert plan.task == "fix the bug in login"
    assert plan.selected_agent  # non-empty agent name
    assert plan.routing_key  # non-empty routing key
    assert "exit_code" in plan.gates
    assert "output" in plan.gates
    assert isinstance(plan.budget, dict)
    assert "timeout_s" in plan.budget
    assert "max_retries" in plan.budget


# ── 2. Execute phase ──────────────────────────────────────────


async def test_execute_phase_runs_plan() -> None:
    """Execute phase dispatches a plan and returns a MaopResult.

    Uses a mock dispatcher that returns a successful result.  Verifies
    the returned ``MaopResult`` has exit_code, stdout, stderr, and
    a non-negative duration_ms.
    """
    mock_dispatcher = MagicMock()
    mock_dispatcher.dispatch = AsyncMock(
        return_value=_success_dispatch_result(stdout="hello")
    )

    with patch("maop.core.security.permission.PermissionManager",
               return_value=_mock_permission_allow()):
        result = await maop_execute(
            agent="test-agent",
            task="echo hello",
            dispatcher=mock_dispatcher,
            guardrail=_mock_guardrail_pass(),
        )

    assert isinstance(result, MaopResult)
    assert result.exit_code == 0
    assert result.stdout == "hello"
    assert result.stderr == ""
    assert result.duration_ms >= 0
    mock_dispatcher.dispatch.assert_called_once()


# ── 3. Verify phase ───────────────────────────────────────────


def test_verify_phase_validates_result() -> None:
    """Verify phase validates an execution result and returns a verdict.

    Constructs a successful ``MaopResult`` and verifies it against
    the default gates (exit_code + output).  Verifies the
    ``VerifyResult`` reports pass with a per-gate breakdown.
    """
    exec_result = new_result(
        agent="test-agent", task="echo hello", exit_code=0, stdout="hello",
    )
    engine = VerifyEngine()
    plan_dict = {"gates": ["exit_code", "output"]}

    vr = engine.verify(plan_dict, exec_result)

    assert isinstance(vr, VerifyResult)
    assert vr.passed
    assert "All gates passed" in vr.summary
    assert len(vr.gates) == 2
    assert all(g.passed for g in vr.gates)


# ── 4. Full PEV cycle ─────────────────────────────────────────


async def test_full_pev_cycle() -> None:
    """Full Plan→Execute→Verify cycle with a mock agent.

    Runs all three phases in sequence: Plan routes the task, Execute
    dispatches via a mock dispatcher, Verify validates the result.
    Verifies the three phases are state-consistent: the execution
    result passes the plan's gates.
    """
    # Plan
    plan = maop_plan("fix the bug")
    assert plan.selected_agent

    # Execute (mock dispatcher returns success)
    mock_dispatcher = MagicMock()
    mock_dispatcher.dispatch = AsyncMock(
        return_value=_success_dispatch_result(
            agent=plan.selected_agent, task=plan.task, stdout="bug fixed",
        )
    )

    with patch("maop.core.security.permission.PermissionManager",
               return_value=_mock_permission_allow()):
        exec_result = await maop_execute(
            agent=plan.selected_agent,
            task=plan.task,
            routing_key=plan.routing_key,
            dispatcher=mock_dispatcher,
            guardrail=_mock_guardrail_pass(),
        )

    # Verify
    verify_engine = VerifyEngine()
    plan_dict = {"gates": plan.gates}
    vr = verify_engine.verify(plan_dict, exec_result)

    # Three phases are consistent
    assert exec_result.exit_code == 0
    assert vr.passed
    assert vr.state in ("done", "working")


# ── 5. Agent failure ──────────────────────────────────────────


async def test_pev_with_agent_failure(tmp_path: Path) -> None:
    """Agent failure (exit_code=1) is reflected in result and breaker.

    Dispatches to a mock agent whose driver returns exit_code=1.
    Verifies the ``DispatchResult`` carries the failure, and the
    circuit breaker records the failure (failures > 0).
    """
    agent = _make_mock_agent("failing-agent")
    config = _make_mock_config(agent)
    breaker = CircuitBreaker(tmp_path / "breaker_fail.db")
    dispatcher = Dispatcher(
        MAOP_config=config, breaker=breaker, root_dir=str(tmp_path),
    )

    from maop.delegate import dispatcher as disp_mod
    original_cli = disp_mod._DRIVERS["cli"]

    async def failing_driver(config, prompt, timeout, workdir, trace_id, *, streamer=None):
        return new_result(
            agent="failing-agent", task=prompt, exit_code=1, stderr="command failed",
        )

    disp_mod._DRIVERS["cli"] = failing_driver
    try:
        result = await dispatcher.dispatch(agent="failing-agent", task="test")
    finally:
        disp_mod._DRIVERS["cli"] = original_cli

    assert result.result.exit_code == 1
    assert not result.result.is_success()

    # Breaker recorded the failure
    entry = breaker.get("failing-agent")
    assert entry is not None
    assert entry.failures > 0


# ── 6. Budget exceeded ────────────────────────────────────────


async def test_pev_with_budget_exceeded(tmp_path: Path) -> None:
    """Budget exceeded → dispatch rejected with exit_code=-6.

    Configures a tiny daily budget (0.01 USD), pre-spends 0.02 USD
    to exceed it, then dispatches.  Verifies the budget guard
    intercepts the dispatch with ``exit_code=-6`` and the agent is
    not actually executed (driver is never called).
    """
    agent = _make_mock_agent("budget-agent")
    budget_config = BudgetConfig(daily_limit=0.01, monthly_limit=100.0, hard_stop=True)
    config = _make_mock_config(agent, budget=budget_config)
    breaker = CircuitBreaker(tmp_path / "breaker_budget.db")
    dispatcher = Dispatcher(
        MAOP_config=config, breaker=breaker, root_dir=str(tmp_path),
    )

    # Pre-spend to exceed the 0.01 budget.
    # Dispatcher reads from CostTracker (SQLite) — not the legacy JSON
    # BudgetGuard — so we pre-spend via CostTracker to make the limit
    # visible to the dispatcher's budget check.
    from maop.core.cost_tracker import CostTracker
    tracker = CostTracker(
        root_dir=str(tmp_path),
        daily_limit_usd=budget_config.daily_limit,
        monthly_limit_usd=budget_config.monthly_limit,
        alert_threshold=budget_config.alert_threshold,
    )
    tracker.record(model="test", prompt_tokens=0, completion_tokens=0)
    # Force a cost entry that exceeds the 0.01 daily limit.
    import sqlite3
    from maop.core.backends.db_utils import get_db_path
    with sqlite3.connect(get_db_path("cost_tracker")) as conn:
        conn.execute(
            "UPDATE cost_entries SET cost_usd = 0.02 WHERE model = 'test'"
        )
        conn.commit()

    # Driver should never be called — budget check rejects before execution
    from maop.delegate import dispatcher as disp_mod
    original_cli = disp_mod._DRIVERS["cli"]

    async def should_not_run(config, prompt, timeout, workdir, trace_id, *, streamer=None):
        raise AssertionError("Driver should not be called when budget is exceeded")

    disp_mod._DRIVERS["cli"] = should_not_run
    try:
        result = await dispatcher.dispatch(agent="budget-agent", task="test")
    finally:
        disp_mod._DRIVERS["cli"] = original_cli

    assert result.result.exit_code == -6
    assert "Budget" in (result.result.error or "")
    assert result.breaker_tripped is False


# ── 7. State consistency across dispatches ────────────────────


async def test_pev_state_consistency(tmp_path: Path) -> None:
    """State remains consistent across 3 dispatches (2 success, 1 failure).

    Dispatches three times with a driver that succeeds, fails, then
    succeeds.  Verifies each result's exit_code matches expectations,
    the breaker ends in CLOSED (recovered after final success), and
    a new ``CircuitBreaker`` on the same DB restores the same state.
    """
    agent = _make_mock_agent("consistency-agent")
    config = _make_mock_config(agent)
    db_path = tmp_path / "breaker_consistency.db"
    breaker = CircuitBreaker(db_path)
    dispatcher = Dispatcher(
        MAOP_config=config, breaker=breaker, root_dir=str(tmp_path),
    )

    call_count = 0

    async def variable_driver(config, prompt, timeout, workdir, trace_id, *, streamer=None):
        nonlocal call_count
        call_count += 1
        if call_count == 2:  # second call fails
            return new_result(
                agent="consistency-agent", task=prompt, exit_code=1, stderr="transient",
            )
        return new_result(
            agent="consistency-agent", task=prompt, exit_code=0, stdout="ok",
        )

    from maop.delegate import dispatcher as disp_mod
    original_cli = disp_mod._DRIVERS["cli"]
    disp_mod._DRIVERS["cli"] = variable_driver
    try:
        results = []
        for _ in range(3):
            r = await dispatcher.dispatch(agent="consistency-agent", task="test")
            results.append(r)
    finally:
        disp_mod._DRIVERS["cli"] = original_cli

    # Results match the driver's sequence
    assert results[0].result.exit_code == 0
    assert results[1].result.exit_code == 1
    assert results[2].result.exit_code == 0

    # Breaker recovered to CLOSED after the final success
    entry = breaker.get("consistency-agent")
    assert entry is not None
    assert entry.state == BreakerState.CLOSED
    assert entry.failures == 0

    # Persistence: new breaker with same DB restores identical state
    breaker2 = CircuitBreaker(db_path)
    entry2 = breaker2.get("consistency-agent")
    assert entry2 is not None
    assert entry2.state == entry.state
    assert entry2.failures == entry.failures