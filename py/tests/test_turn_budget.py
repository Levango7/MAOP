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
    """总是失败的 fake dispatcher；记录调用次数与返回的 usage。"""

    def __init__(self, usage: dict[str, int] | None = None) -> None:
        self.calls = 0
        self.usage = usage

    async def dispatch(self, **kwargs) -> _DispatchResult:
        self.calls += 1
        result = new_result(
            agent=str(kwargs.get("agent", "")),
            task=str(kwargs.get("task", "")),
            exit_code=1,
            error="fake failure",
            trace_id=str(kwargs.get("trace_id", "")),
            usage=self.usage,
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
    # M3.1: 未观测到实测 usage → tokens_used=0 且来源=estimated
    assert result.tokens_used == 0
    assert result.tokens_source == "estimated"


@pytest.mark.asyncio
async def test_measured_tokens_accumulate_and_flip_source(tmp_path):
    """M3.1：usage 非空的 dispatch 结果计入实测 token，来源翻转为 measured。"""
    loop = _loop(tmp_path)
    dispatcher = _CountingDispatcher(usage={
        "prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150,
    })
    loop._dispatcher = dispatcher
    loop._max_turns = None
    loop._turns_used = 0
    loop._tokens_used_measured = 0

    await loop._execute_with_retry(
        task="t",
        fallback_chain=["a1"],
        routing_key="",
        workdir=str(tmp_path),
        timeout=1,
        retry=False,
        trace_id="tr3",
    )

    assert loop._tokens_used_measured == 150

    loop2 = loop  # 同一实例直接构结果，验证接线
    from maop.core.agent.evolution.phases import PhaseContext

    result = loop2._build_loop_result(
        PhaseContext(task="t", plan_result={}, analysis_dict={}), time.monotonic(),
    )
    assert result.tokens_used == 150
    assert result.tokens_source == "measured"


@pytest.mark.asyncio
async def test_cli_path_without_usage_keeps_estimated(tmp_path):
    """CLI 路径（usage=None）：token 不计入实测，来源保持 estimated。"""
    loop = _loop(tmp_path)
    dispatcher = _CountingDispatcher(usage=None)
    loop._dispatcher = dispatcher
    loop._max_turns = 1
    loop._turns_used = 0
    loop._tokens_used_measured = 0

    await loop._execute_with_retry(
        task="t",
        fallback_chain=["a1"],
        routing_key="",
        workdir=str(tmp_path),
        timeout=1,
        retry=False,
        trace_id="tr4",
    )

    assert loop._turns_used == 1
    assert loop._tokens_used_measured == 0


# ── M3.1: budget_reconciliation 真实值分支 ───────────────────────────


def _fake_exec_result(usage: dict[str, int] | None):
    from types import SimpleNamespace

    return SimpleNamespace(
        model="m1", agent="a1", task="task text", stdout="out",
        usage=usage,
    )


def test_budget_reconciliation_uses_measured_usage(tmp_path):
    """usage 存在时成本对账记真实 token（不再落启发式估算）。"""
    from maop.core.cost_tracker import CostTracker

    loop = _loop(tmp_path)
    loop._budget_reconciliation(
        _fake_exec_result({
            "prompt_tokens": 120, "completion_tokens": 80, "total_tokens": 200,
        }),
        "trace-m1",
    )
    entries = CostTracker(root_dir=str(tmp_path)).get_entries(model="m1")
    assert len(entries) == 1
    assert (entries[0].prompt_tokens, entries[0].completion_tokens) == (120, 80)
    assert entries[0].total_tokens == 200


def test_budget_reconciliation_without_usage_still_records(tmp_path):
    """usage=None（CLI 路径）：记录仍发生（token 值由启发式/空值决定，不冒充实测）。"""
    from maop.core.cost_tracker import CostTracker

    loop = _loop(tmp_path)
    loop._budget_reconciliation(_fake_exec_result(None), "trace-m2")
    entries = CostTracker(root_dir=str(tmp_path)).get_entries(model="m1")
    assert len(entries) == 1
    # 无 usage → 启发式估算：task "task text"（9 个 ASCII 字符）→ 9//4 = 2
    #（确定性公式，见 _budget_reconciliation）；关键是记录存在且语义为估算。
    assert entries[0].prompt_tokens == 2
