"""GAP-2 发现 B 钉测：LLM 直连与计费入账的 tokens_source=measured 标签。

背景（2026-11-21）：GAP-2 审计发现 B——LLM 直连路径 `_record_cost` 两分支
metadata 只写 provider、计费引擎 charge 入账行无 metadata，导致成本页无法按
tokens_source 区分实测/估算。词表＝measured/estimated/mixed（cost_reconciliation）。
本测试钉住两条路径都写入 tokens_source="measured"。
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

from maop.core.agent.billing.billing_abstraction import BillingEngine
from maop.core.agent.llm_chat import llm_factory
from maop.core.agent.registry.agent_catalog import BillingModel


def test_llm_factory_record_cost_marks_measured(monkeypatch):
    calls = []

    class SpyTracker:
        async def record_async(self, **kwargs):
            calls.append(kwargs)

    monkeypatch.setattr(
        "maop.core.cost_tracker.get_cost_tracker", lambda: SpyTracker()
    )
    resp = SimpleNamespace(
        model="m1",
        prompt_tokens=10,
        completion_tokens=5,
        total_tokens=15,
        latency_ms=100,
        provider="openai",
        content="ok",
    )
    asyncio.run(llm_factory._record_cost(resp, {"session_id": "s1", "agent": "a1"}))
    assert len(calls) == 1
    md = calls[0]["metadata"]
    assert md["tokens_source"] == "measured"
    assert md["provider"] == "openai"  # provider 标签保留（兼容）


def test_billing_charge_marks_measured(tmp_path):
    records = []

    class SpyTracker:
        def record(self, **kwargs):
            records.append(kwargs)

    catalog = SimpleNamespace(
        get=lambda name: SimpleNamespace(
            billing_model=BillingModel.FREE,
            billing_config=SimpleNamespace(),  # FREE 不读 config 字段，仅占位
        )
    )
    engine = BillingEngine(
        catalog=catalog,
        quota_bucket=SimpleNamespace(),
        cost_tracker=SpyTracker(),
        db_path=tmp_path / "billing.db",
    )
    result = engine.charge("a1", tokens=10, calls=1, session_id="s1", model="m1")
    assert result.success
    assert len(records) == 1
    md = records[0]["metadata"]
    assert md["tokens_source"] == "measured"
