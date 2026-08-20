"""P0-3 契约测试（remediation-plan-v5.1.0）：dispatcher 准入拦截必须读到
真实花费（CostTracker SQLite cost_entries），防止预算账本再次断写导致
静默放行——对外宣称的预算管控实际不生效。

验收标准：构造超预算用例时 dispatch 返回 exit_code=-6。
"""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock

import pytest

from maop.core.cost_tracker import BudgetStatus, CostTracker
from maop.delegate import dispatcher as disp_mod
from maop.delegate.dispatcher import Dispatcher
from maop.model.schema import BudgetConfig


def _make_config(hard_stop: bool = True) -> MagicMock:
    agent = MagicMock()
    agent.name = "claude"
    agent.cli = "echo"
    agent.driver = "cli"
    agent.cli_args = ""
    agent.capabilities = []
    agent.timeout_s = 10
    agent.model = "claude-3"
    agent.wrapper = ""
    agent.command = ""
    agent.provider = ""
    config = MagicMock()
    config.agents = [agent]
    config.workflows = []
    config.budget = BudgetConfig(hard_stop=hard_stop)
    return config


def _over_status() -> BudgetStatus:
    return BudgetStatus(
        daily_limit_usd=5.0,
        monthly_limit_usd=100.0,
        daily_spent_usd=6.0,
        monthly_spent_usd=0.0,
        daily_over_budget=True,
        monthly_over_budget=False,
    )


def _under_status() -> BudgetStatus:
    return BudgetStatus(
        daily_limit_usd=5.0,
        monthly_limit_usd=100.0,
        daily_spent_usd=1.0,
        monthly_spent_usd=0.0,
        daily_over_budget=False,
        monthly_over_budget=False,
    )


def _run_dispatch(dispatcher: Dispatcher) -> disp_mod.DispatchResult:
    return asyncio.run(dispatcher.dispatch(agent="claude", task="test"))


def _stub_cli_driver(monkeypatch) -> None:
    async def mock_cli(config, prompt, timeout, workdir, trace_id, *, streamer=None):
        from maop.delegate.drivers import new_result
        return new_result(
            agent="claude", task=prompt, exit_code=0, stdout="hello",
            driver="cli", model="claude-3",
        )
    monkeypatch.setitem(disp_mod._DRIVERS, "cli", mock_cli)


@pytest.mark.parametrize("period", ["daily", "monthly"])
def test_dispatch_rejects_when_over_budget(monkeypatch, period: str) -> None:
    """超预算（日/月任一）→ dispatch 必须返回 exit_code=-6。"""
    status = _over_status()
    if period == "monthly":
        status = BudgetStatus(
            daily_limit_usd=5.0, monthly_limit_usd=100.0,
            daily_spent_usd=0.0, monthly_spent_usd=120.0,
            daily_over_budget=False, monthly_over_budget=True,
        )
    monkeypatch.setattr(
        CostTracker, "budget_status", lambda self: status,
    )
    _stub_cli_driver(monkeypatch)

    dispatcher = Dispatcher(MAOP_config=_make_config(hard_stop=True))
    result = _run_dispatch(dispatcher)

    assert result.result.exit_code == -6
    assert "Budget" in (result.result.error or "")


def test_dispatch_allows_within_budget(monkeypatch) -> None:
    """预算内 → 正常派发（exit_code 0），不误伤。"""
    monkeypatch.setattr(CostTracker, "budget_status", lambda self: _under_status())
    _stub_cli_driver(monkeypatch)

    dispatcher = Dispatcher(MAOP_config=_make_config(hard_stop=True))
    result = _run_dispatch(dispatcher)

    assert result.result.exit_code == 0


def test_dispatch_skips_when_hard_stop_disabled(monkeypatch) -> None:
    """hard_stop=False → 永远放行（与旧 BudgetGuard.can_spend 语义一致）。"""
    monkeypatch.setattr(CostTracker, "budget_status", lambda self: _over_status())
    _stub_cli_driver(monkeypatch)

    dispatcher = Dispatcher(MAOP_config=_make_config(hard_stop=False))
    result = _run_dispatch(dispatcher)

    assert result.result.exit_code == 0


def test_dispatch_non_blocking_on_tracker_error(monkeypatch) -> None:
    """CostTracker 异常 → 不阻断派发（保留 non-blocking 语义）。"""
    def _boom(self) -> BudgetStatus:
        raise RuntimeError("db unavailable")
    monkeypatch.setattr(CostTracker, "budget_status", _boom)
    _stub_cli_driver(monkeypatch)

    dispatcher = Dispatcher(MAOP_config=_make_config(hard_stop=True))
    result = _run_dispatch(dispatcher)

    assert result.result.exit_code == 0


def test_dispatcher_uses_cost_tracker_not_json_ledger() -> None:
    """契约：dispatcher 的预算检查必须走 CostTracker（SQLite 真数据源），
    不得再 import / 实例化已断写的 JSON BudgetGuard（防止账本再次断写）。

    注：dispatcher.py 已拆分为 re-export shim + dispatch_core.py 实现；
    预算检查源码现位于 dispatch_core.py（Dispatcher._dispatch_impl_inner）。"""
    import inspect

    import maop.delegate.dispatch_core as core
    import maop.delegate.dispatcher as disp

    # 实现源码（Dispatcher._dispatch_impl_inner 现位于 dispatch_core）
    src = inspect.getsource(core)
    # re-export shim 也不得重新引入 JSON BudgetGuard
    shim_src = inspect.getsource(disp)

    assert "CostTracker" in src
    assert "from maop.model.budget import BudgetGuard" not in src
    assert "BudgetGuard(" not in src
    assert "from maop.model.budget import BudgetGuard" not in shim_src
    assert "BudgetGuard(" not in shim_src
