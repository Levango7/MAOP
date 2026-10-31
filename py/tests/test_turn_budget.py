"""M2：turn 预算与计数单测（注入 dispatcher，不打网络、不依赖 agent 环境）.

一个 turn = 一次 ``dispatcher.dispatch`` 尝试；``max_turns`` 为硬上限
（``None`` = 不限，维持既有行为）。预算耗尽时以最后一个结果（或一个
合成失败——``turn budget exhausted``）返回，交由既有收尾路径处理。
"""

from __future__ import annotations

import time

import pytest

from maop.core.reliability.error_schema import MaopResult, new_result


class _DispatchResult:
    def __init__(self, result: MaopResult) -> None:
        self.result = result


class _CountingDispatcher:
    """总是失败的 fake dispatcher；记录调用次数。"""

    def __init__(self) -> None:
        self.calls = 0

    async def dispatch(self, **kwargs) -> _DispatchResult:
        self.calls += 1
        result = new_result(
            agent=str(kwargs.get("agent", "")),
            task=str(kwargs.get("task", "")),
            exit_code=1,
            error="fake failure",
            trace_id=str(kwargs.get("trace_id", "")),
        )
        return _DispatchResult(result)


def _loop(tmp_path):
    from maop.maop_loop import LoopConfig, MaopLoop

    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "agents.yaml").write_text("agents: {}\n")
    (config_dir / "models.yaml").write_text("models: {}\n")
    (tmp_path / "data").mkdir()
    lc = LoopConfig(
        enable_semantic_analyze=False,
        enable_parallel=False,
        enable_load_balancer=False,
        enable_result_cache=False,
        enable_metrics=False,
        enable_timeseries=False,
        enable_evolve=False,
        enable_dream=False,
        enable_cache_guard=False,
        iterative_max_attempts=5,
        iterative_backoff_ms=0,
        retry_backoff_ms=0,
        default_timeout_s=5,
    )
    return MaopLoop(root_dir=str(tmp_path), loop_config=lc)


@pytest.mark.asyncio
async def test_turn_budget_caps_dispatch_attempts(tmp_path):
    loop = _loop(tmp_path)
    dispatcher = _CountingDispatcher()
    loop._dispatcher = dispatcher
    loop._max_turns = 2
    loop._turns_used = 0

    result = await loop._execute_with_retry(
        task="t",
        fallback_chain=["a1", "a2"],
        routing_key="",
        workdir=str(tmp_path),
        timeout=1,
        retry=True,
        trace_id="tr1",
    )

    assert dispatcher.calls == 2
    assert loop._turns_used == 2
    # 预算耗尽 = 停止派发并原样返回最后一个结果（此处为 fake dispatcher 的失败结果）
    assert result is not None and not result.is_success()
    assert result.error == "fake failure"


@pytest.mark.asyncio
async def test_zero_budget_dispatches_nothing(tmp_path):
    """max_turns=0：预算自始耗尽——不派发，返回合成失败（原因可辨识）。"""
    loop = _loop(tmp_path)
    dispatcher = _CountingDispatcher()
    loop._dispatcher = dispatcher
    loop._max_turns = 0
    loop._turns_used = 0

    result = await loop._execute_with_retry(
        task="t",
        fallback_chain=["a1"],
        routing_key="",
        workdir=str(tmp_path),
        timeout=1,
        retry=True,
        trace_id="tr0",
    )

    assert dispatcher.calls == 0
    assert loop._turns_used == 0
    assert result is not None and not result.is_success()
    assert "turn budget exhausted" in (result.error or "")


@pytest.mark.asyncio
async def test_without_budget_attempts_are_not_truncated(tmp_path):
    loop = _loop(tmp_path)
    dispatcher = _CountingDispatcher()
    loop._dispatcher = dispatcher
    loop._max_turns = None
    loop._turns_used = 0

    await loop._execute_with_retry(
        task="t",
        fallback_chain=["a1", "a2"],
        routing_key="",
        workdir=str(tmp_path),
        timeout=1,
        retry=True,
        trace_id="tr2",
    )

    # 静态配置 iterative_max_attempts=5 × 2 agents = 10（未设上限，全量尝试）
    assert dispatcher.calls == 10
    assert loop._turns_used == 10


def test_build_loop_result_reports_turns(tmp_path):
    from maop.core.agent.evolution.phases import PhaseContext

    loop = _loop(tmp_path)
    loop._turns_used = 3
    ctx = PhaseContext(task="t", plan_result={}, analysis_dict={})
    result = loop._build_loop_result(ctx, time.monotonic())
    assert result.turns == 3
