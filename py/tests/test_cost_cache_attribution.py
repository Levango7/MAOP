"""缓存命中明细落库的回归测试（2026-11-08）。

背景：外部 CLI 的提示侧消耗里 ``cache_read`` 常占九成以上（copilot 实测
19328/19853 = 97.4%）。它是「token 用量节省」唯一可验证的指标——没有它，
"省了多少"只能靠总量倒推，无法区分「上下文真的变短」与「缓存命中率变高」。

本组守住三件事：

1. ``record_actual_cost`` 接受 cache 明细并写进 ``cost_entries.metadata``。
2. 零值不写键（未启用缓存的 agent 不产生噪声字段，旧调用方零行为变更）。
3. ``_budget_reconciliation`` 从 ``MaopResult.usage`` 读出 cache 并透传，
   同时 ``run.cost`` 事件带上同口径字段。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest

from maop.core.cost_tracker import CostTracker
from maop.loop_models import LoopConfig
from maop.maop_loop_phases import PhasesMixin


@pytest.fixture
def tracker(tmp_path) -> CostTracker:
    return CostTracker(root_dir=tmp_path)


# ── 1. record_actual_cost 写 cache 明细 ────────────────────────


def test_cache_tokens_persisted(tracker: CostTracker) -> None:
    tracker.record_actual_cost(
        trace_id="t-cache", model="gpt-4o-mini", provider="openai",
        actual_tokens_in=19853, actual_tokens_out=5, estimated_cost=0.0,
        agent="copilot", phase="execute", tokens_source="measured",
        cache_read_tokens=19328, cache_write_tokens=11045,
    )
    entry = tracker.get_entries(agent="copilot", limit=1)[0]
    assert entry.metadata["cache_read_tokens"] == 19328
    assert entry.metadata["cache_write_tokens"] == 11045


def test_cache_keys_omitted_when_zero(tracker: CostTracker) -> None:
    """零值不写键——未启用缓存的 agent 台账里不该多两个恒为 0 的字段。"""
    tracker.record_actual_cost(
        trace_id="t-nocache", model="gpt-4o-mini", provider="openai",
        actual_tokens_in=10, actual_tokens_out=20, estimated_cost=0.0,
        agent="legacy", phase="execute", tokens_source="measured",
    )
    entry = tracker.get_entries(agent="legacy", limit=1)[0]
    assert "cache_read_tokens" not in entry.metadata
    assert "cache_write_tokens" not in entry.metadata


def test_cache_tokens_returned_in_summary(tracker: CostTracker) -> None:
    out = tracker.record_actual_cost(
        trace_id="t-ret", model="gpt-4o-mini", provider="openai",
        actual_tokens_in=100, actual_tokens_out=10, estimated_cost=0.0,
        agent="a", phase="execute",
        cache_read_tokens=60, cache_write_tokens=5,
    )
    assert out["cache_read_tokens"] == 60
    assert out["cache_write_tokens"] == 5


# ── 2. _budget_reconciliation 从 usage 透传 ─────────────────────


@dataclass
class _ExecResult:
    agent: str = "copilot"
    task: str = "hi"
    stdout: str = "ok"
    stderr: str = ""
    error: str | None = None
    exit_code: int = 0
    duration_ms: int = 1000
    routing_key: str = "chat"
    model: str = "gpt-5.4"
    usage: Any = None

    def is_success(self) -> bool:
        return self.exit_code == 0


class _Host(PhasesMixin):
    """只提供 _budget_reconciliation 需要的最小宿主。"""

    def __init__(self, root: str) -> None:
        self._loop_config = LoopConfig()
        self._root = root

    def _log(self, *a: Any, **k: Any) -> None:
        return None


def test_reconciliation_reads_cache_from_usage(tmp_path, monkeypatch) -> None:
    captured: dict[str, Any] = {}

    def _capture(trace_id: str, kind: str, **kwargs: Any) -> None:
        captured[kind] = kwargs.get("payload", {})

    monkeypatch.setattr("maop.cost_reconciliation.record_run_event", _capture)

    host = _Host(str(tmp_path))
    result = _ExecResult(usage={
        "prompt_tokens": 19853,
        "completion_tokens": 5,
        "total_tokens": 19858,
        "cache_read_tokens": 19328,
        "cache_write_tokens": 0,
    })
    host._budget_reconciliation(result, "t-loop")

    entry = CostTracker(root_dir=tmp_path).get_entries(agent="copilot", limit=1)[0]
    assert entry.prompt_tokens == 19853
    assert entry.metadata["cache_read_tokens"] == 19328
    assert "cache_write_tokens" not in entry.metadata

    payload = captured["run.cost"]
    assert payload["cache_read_tokens"] == 19328
    assert payload["cache_write_tokens"] == 0


def test_reconciliation_without_cache_keys_is_unchanged(tmp_path, monkeypatch) -> None:
    """协议行 / 启发式路径没有 cache 键时，行为与既往一致（不写、不报错）。"""
    monkeypatch.setattr("maop.cost_reconciliation.record_run_event", lambda *a, **k: None)

    host = _Host(str(tmp_path))
    result = _ExecResult(usage={"prompt_tokens": 100, "completion_tokens": 10})
    host._budget_reconciliation(result, "t-plain")

    entry = CostTracker(root_dir=tmp_path).get_entries(agent="copilot", limit=1)[0]
    assert "cache_read_tokens" not in entry.metadata
    assert "cache_write_tokens" not in entry.metadata


def test_reconciliation_passes_priced_flag_to_ledger(tmp_path, monkeypatch) -> None:
    """拆分后的 _budget_reconciliation 必须把 priced 判据传给台账。

    f9822c8c 把 ``priced=bool(model_def)`` 加在 maop_loop_phases 的
    ``record_actual_cost`` 调用上；#246 把该方法整体拆到
    ``cost_reconciliation.py`` 后，这个参数若没跟着搬过来，
    ``cost_entries.metadata`` 就没有 priced 键，成本页会把「无法计价」
    显示成「$0.0000 = 免费」。现有 8 条 priced 测试都是**直接调**
    ``record_actual_cost``，守不住这条调用边——本例端到端钉死它。
    """
    monkeypatch.setattr("maop.cost_reconciliation.record_run_event", lambda *a, **k: None)

    host = _Host(str(tmp_path))
    result = _ExecResult(usage={"prompt_tokens": 100, "completion_tokens": 10})
    host._budget_reconciliation(result, "t-priced-loop")

    entry = CostTracker(root_dir=tmp_path).get_entries(agent="copilot", limit=1)[0]
    # 不依赖 models.yaml 收录了哪些模型：调用方传了 priced，键必定存在且为
    # bool；没传则 metadata 里根本没有这个键（record_actual_cost 的 None 分支）。
    assert isinstance(entry.metadata.get("priced"), bool)
