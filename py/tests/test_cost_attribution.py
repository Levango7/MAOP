"""M-X3 成本归因测试：三维聚合逐条对齐 + 阈值告警真实落库。

钉子 M0 的两条验收：
1. **聚合数值与 cost_tracker 原始记录逐条对齐**——聚合与手算的子集和一致，
   且 phase 缺失显式进 ``unattributed`` 桶（不摊进任何相位）；
2. **越阈告警真实送达**——经 ``alert_ingest`` 通道落库可查，重复上报按
   fingerprint 去重（只刷新 received_at，不产新行）。

数据隔离：``CostTracker`` 与 ``alert_ingest`` 的 db 都走 ``get_db_path``，
由 conftest 的 ``MAOP_DATA_DIR`` 每测试隔离。
"""

from __future__ import annotations

from maop.core.alert_ingest import list_alerts
from maop.core.cost_attribution import (
    UNATTRIBUTED,
    attribution_summary,
    check_budget_thresholds,
    emit_budget_alerts,
)
from maop.core.cost_tracker import CostTracker, ModelPricing

#: 注入价目：1k prompt tokens = $0.002，1k completion tokens = $0.004 ⇒ 成本精确可算。
_PRICING = {"m-test": ModelPricing(prompt_per_1m=2.0, completion_per_1m=4.0)}


def _seed() -> CostTracker:
    """三条种子记录：alpha/execute、alpha/plan、beta/无 phase。"""
    tracker = CostTracker(pricing=dict(_PRICING))
    tracker.record(
        model="m-test", agent="alpha", prompt_tokens=1000, completion_tokens=500,
        metadata={"phase": "execute"},
    )  # $0.004
    tracker.record(
        model="m-test", agent="alpha", prompt_tokens=500, completion_tokens=0,
        metadata={"phase": "plan"},
    )  # $0.001
    tracker.record(
        model="m-test", agent="beta", prompt_tokens=2000, completion_tokens=1000,
    )  # $0.008，无 phase
    return tracker


def test_summary_totals_match_raw_entries():
    """合计与 by_* 分桶全部与原始记录手算一致（逐条对齐）。"""
    tracker = _seed()
    summary = attribution_summary(tracker=tracker)

    entries = tracker.get_entries(limit=100)
    assert summary["total"]["calls"] == len(entries) == 3
    assert summary["total"]["cost_usd"] == 0.013
    assert summary["total"]["tokens"] == 1500 + 500 + 3000

    assert summary["by_agent"]["alpha"] == {"cost_usd": 0.005, "calls": 2, "tokens": 2000}
    assert summary["by_agent"]["beta"] == {"cost_usd": 0.008, "calls": 1, "tokens": 3000}
    assert summary["by_model"]["m-test"] == {"cost_usd": 0.013, "calls": 3, "tokens": 5000}

    # 分桶成本之和必须回到合计——防漏桶（例如吞掉零成本条目）
    for dimension in ("by_agent", "by_phase", "by_model"):
        assert round(sum(v["cost_usd"] for v in summary[dimension].values()), 6) == 0.013


def test_missing_phase_lands_in_unattributed_bucket():
    """phase 缺失显式进 unattributed，不摊进任何相位。"""
    summary = attribution_summary(tracker=_seed())
    assert summary["by_phase"]["execute"] == {"cost_usd": 0.004, "calls": 1, "tokens": 1500}
    assert summary["by_phase"]["plan"] == {"cost_usd": 0.001, "calls": 1, "tokens": 500}
    assert summary["by_phase"][UNATTRIBUTED] == {"cost_usd": 0.008, "calls": 1, "tokens": 3000}


def test_transitive_wiring_records_agent_and_phase():
    """归属修线：record_actual_cost 现在把 agent/phase 写进条目。"""
    tracker = CostTracker(pricing=dict(_PRICING))
    tracker.record_actual_cost(
        trace_id="t-1", model="m-test", provider="p",
        actual_tokens_in=1000, actual_tokens_out=500,
        estimated_cost=0.0, agent="alpha", phase="execute",
    )
    entry = tracker.get_entries(limit=1)[0]
    assert entry.agent == "alpha"
    assert entry.metadata.get("phase") == "execute"

    # 旧调用方（不传 agent/phase）零破坏：不写 phase 键，聚合记 unattributed
    tracker.record_actual_cost(trace_id="t-2", model="m-test", actual_tokens_in=1, actual_tokens_out=1)
    latest = tracker.get_entries(limit=1)[0]
    assert latest.agent == ""
    assert "phase" not in latest.metadata


def test_thresholds_unconfigured_never_alert():
    """预算未配置（None）或为零 ≠ 越阈：不产告警。"""
    summary = attribution_summary(tracker=_seed())
    assert check_budget_thresholds(summary) == []
    assert check_budget_thresholds(summary, total_budget_usd=0.0) == []
    assert check_budget_thresholds(summary, total_budget_usd=-1.0) == []


def test_thresholds_severity_and_dimensions():
    """越阈维度齐全、严重度分档：<120% 记 warning，≥120% 记 critical。"""
    summary = attribution_summary(tracker=_seed())
    alerts = check_budget_thresholds(
        summary,
        total_budget_usd=0.012,        # 0.013/0.012 = 108% → warning
        per_agent_budget_usd=0.006,    # beta 0.008/0.006 = 133% → critical（alpha 不越）
        per_phase_budget_usd=0.005,    # unattributed 0.008/0.005 = 160% → critical
    )
    by_key = {(a["labels"]["dimension"], a["labels"]["key"]): a for a in alerts}
    assert set(by_key) == {("total", "total"), ("agent", "beta"), ("phase", UNATTRIBUTED)}
    assert by_key[("total", "total")]["labels"]["severity"] == "warning"
    assert by_key[("agent", "beta")]["labels"]["severity"] == "critical"
    assert by_key[("agent", "beta")]["labels"]["agent"] == "beta"  # agent 专列
    assert "CostBudgetThreshold" == by_key[("total", "total")]["labels"]["alertname"]


def test_emit_budget_alerts_lands_in_alert_ingest_and_dedupes():
    """越阈告警真实落库可查；重复上报只记 duplicate。"""
    tracker = _seed()
    result = emit_budget_alerts(tracker=tracker, total_budget_usd=0.012)
    assert result["ingest"]["stored"] >= 1

    rows = [r for r in list_alerts(limit=50) if r.get("alertname") == "CostBudgetThreshold"]
    assert rows, "告警必须能在 alert_ingest 通道查到"
    assert rows[0]["severity"] == "warning"
    assert rows[0]["receiver"] == "cost-attribution"

    # 同一越阈事件重复上报：fingerprint 稳定 ⇒ 无新行，duplicate 计数
    again = emit_budget_alerts(tracker=tracker, total_budget_usd=0.012)
    assert again["ingest"]["stored"] == 0
    assert again["ingest"]["duplicate"] >= 1
