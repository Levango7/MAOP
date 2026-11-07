"""成本台账「可归因 / 可核验」回归测试（2026-11-08）。

覆盖三条在外部 CLI 路径（`maop run --agent copilot`）上实测到的缺陷：

1. ``record_actual_cost`` 的 ``estimated_cost`` 兜底被 ``record()`` 重算覆盖
   ⇒ ``models.yaml`` 有价、``DEFAULT_PRICING`` 无价的模型台账恒记 0.0。
   证据：trace ``6032ef98``，``run.cost.estimated_cost=0.00098`` 而
   ``cost_entries.cost_usd=0.0``。
2. 估算值与实测值在台账上不可区分 ⇒ 加 ``tokens_source`` 来源标记。
3. ``run.step`` 在反馈循环未真正重跑时被双写
   ⇒ 同一次执行产生两条 ``step=1`` 记录（seq=4 / seq=6）。

另附模型名不再被伪造的断言：``provider`` 为空的纯外部 CLI 不得被
``config.model`` 注入改写（见 ``test_dispatcher.py`` 同名用例）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pytest

from maop.core.cost_tracker import CostTracker
from maop.loop_models import LoopConfig
from maop.maop_loop_phases import PhasesMixin


@pytest.fixture
def tracker(tmp_path) -> CostTracker:
    return CostTracker(root_dir=tmp_path)


# ── 1. estimated_cost 兜底不再被覆盖 ────────────────────────────


def test_record_actual_cost_keeps_estimate_when_pricing_table_misses(tracker: CostTracker) -> None:
    """agnes-2.5-pro 不在 DEFAULT_PRICING 里，兜底金额必须真的落库。

    回归：``record()`` 内部按 ``_calculate_cost`` 重算并把
    ``record_actual_cost`` 算好的兜底值覆盖成 0.0。
    """
    out = tracker.record_actual_cost(
        trace_id="t-fallback", model="agnes-2.5-pro", provider="agnes",
        actual_tokens_in=5, actual_tokens_out=31,
        estimated_cost=0.00098, agent="copilot", phase="execute",
        tokens_source="estimated",
    )
    assert out["actual_cost"] == pytest.approx(0.00098)

    entries = tracker.get_entries(agent="copilot", limit=1)
    assert len(entries) == 1
    assert entries[0].cost_usd == pytest.approx(0.00098), (
        "estimated_cost fallback must survive record()'s own pricing lookup"
    )


def test_record_actual_cost_prefers_pricing_table_when_known(tracker: CostTracker) -> None:
    """模型在 DEFAULT_PRICING 里时，仍按价目表计算，不被估算值覆盖。"""
    out = tracker.record_actual_cost(
        trace_id="t-priced", model="gpt-4o-mini", provider="openai",
        actual_tokens_in=1000, actual_tokens_out=500,
        estimated_cost=999.0, agent="coder", phase="execute",
        tokens_source="measured",
    )
    expected = (1000 / 1e6) * 0.15 + (500 / 1e6) * 0.60
    assert out["actual_cost"] == pytest.approx(expected)


# ── 2. tokens_source 来源标记落库 ───────────────────────────────


@pytest.mark.parametrize("source", ["measured", "estimated", "mixed"])
def test_tokens_source_is_persisted(tracker: CostTracker, source: str) -> None:
    tracker.record_actual_cost(
        trace_id=f"t-src-{source}", model="gpt-4o-mini", provider="openai",
        actual_tokens_in=10, actual_tokens_out=20, estimated_cost=0.0,
        agent="a", phase="execute", tokens_source=source,
    )
    entry = tracker.get_entries(agent="a", limit=1)[0]
    assert entry.metadata["tokens_source"] == source


def test_tokens_source_omitted_when_not_declared(tracker: CostTracker) -> None:
    """旧调用方不传 tokens_source 时不写入该键（零行为变更）。"""
    tracker.record_actual_cost(
        trace_id="t-src-none", model="gpt-4o-mini", provider="openai",
        actual_tokens_in=1, actual_tokens_out=1, estimated_cost=0.0,
        agent="legacy", phase="execute",
    )
    entry = tracker.get_entries(agent="legacy", limit=1)[0]
    assert "tokens_source" not in entry.metadata


# ── 3. run.step 不双写 ──────────────────────────────────────────


@dataclass
class _ExecResult:
    agent: str = "copilot"
    task: str = "hi"
    stdout: str = "ok"
    stderr: str = ""
    error: str | None = None
    exit_code: int = 0
    duration_ms: int = 21903
    routing_key: str = "chat"
    model: str = "gpt-5.4"
    usage: Any = None

    def is_success(self) -> bool:
        return self.exit_code == 0


@dataclass
class _Verify:
    passed: bool
    errored: bool = False
    state: str = "done"
    summary: str = "All gates passed"
    block_reason: str = ""


@dataclass
class _Ctx:
    trace_id: str = "t-dup"
    agent: str = "copilot"
    routing_key: str = "chat"
    original_task: str = "hi"
    timeout: float = 120.0
    feedback_cycles: int = 0
    block_reason: str = ""
    execution_result: Any = None
    verify_result: Any = None
    plan_result: Any = None
    feedback: str = ""
    fallback_chain: list[str] = field(default_factory=list)


class _Host(PhasesMixin):
    """只提供 _phase_feedback 需要的最小宿主。"""

    def __init__(self, loop_config: LoopConfig, verify_returns: list[_Verify] | None = None) -> None:
        self._loop_config = loop_config
        self._root = "."
        self._verify_returns = list(verify_returns or [])

    def _log(self, *a: Any, **k: Any) -> None:
        return None

    def _build_fallback_chain(self, agent: str, routing_key: str) -> list[str]:
        return []

    async def _verify(self, *a: Any, **k: Any) -> _Verify:
        return self._verify_returns.pop(0)

    async def _execute_with_retry(self, **k: Any) -> _ExecResult:
        return _ExecResult(task=k.get("task", "retry"))


@pytest.mark.asyncio
async def test_no_duplicate_run_step_when_loop_body_never_runs(monkeypatch) -> None:
    """首轮 verify 即通过 ⇒ 反馈阶段不得再记一次 run.step。"""
    from maop import maop_loop_phases as phases

    calls: list[Any] = []
    monkeypatch.setattr(phases, "_record_exec_step", lambda ctx, r: calls.append(r))

    host = _Host(LoopConfig(feedback_max_cycles=2))
    ctx = _Ctx(execution_result=_ExecResult(), verify_result=_Verify(passed=True))
    await host._phase_feedback(ctx, workdir=".", skip_verify=False)

    assert calls == [], "run.step must be written exactly once per execution attempt"


@pytest.mark.asyncio
async def test_run_step_recorded_once_when_loop_body_runs(monkeypatch) -> None:
    """真的重跑过一次执行 ⇒ 恰好补记一条 run.step（不是两条）。"""
    from maop import maop_loop_phases as phases

    calls: list[Any] = []
    monkeypatch.setattr(phases, "_record_exec_step", lambda ctx, r: calls.append(r))

    host = _Host(
        LoopConfig(feedback_max_cycles=2),
        verify_returns=[_Verify(passed=True)],
    )
    ctx = _Ctx(
        execution_result=_ExecResult(),
        verify_result=_Verify(passed=False, state="working", summary="needs retry"),
    )
    await host._phase_feedback(ctx, workdir=".", skip_verify=False)

    assert len(calls) == 1
    assert ctx.feedback_cycles == 1
