"""细粒度成本归因（M-X3 M0）：按 agent / phase / model 三维聚合 + 预算阈值告警。

数据来源 = CostTracker 写入的 ``cost_entries``（复用 ``get_entries``，不另写 SQL）。

三维口径
--------
- ``model`` / ``agent``：条目自带字段（新记录由写入侧归属，见 maop_loop 的
  ``_reconcile`` 现打 ``agent`` + ``metadata["phase"]``）；
- ``phase``：取自 ``metadata["phase"]``；缺失一律记 ``unattributed`` 桶——
  显式列"未归属"，而不是把未知摊进任一 phase，聚合口径才不伪造事实。

告警通道复用 ``alert_ingest.record_alerts``（Alertmanager 形状落库），
receiver 固定 ``cost-attribution``；fingerprint 由标签摘要稳定生成，同一
越阈事件的重复上报只刷新 ``received_at``（去重语义与日志侧一致）。

M0 边界：窗口上限 ``MAX_WINDOW_ENTRIES`` 一条链内取完（不做分页/流式，
超窗请分段调用）；阈值全部走显式参数（不读 env——config-drift 棘轮口径）。
"""

from __future__ import annotations

import logging
from typing import Any

from maop.core.cost_tracker import CostEntry, CostTracker

logger = logging.getLogger(__name__)

UNATTRIBUTED = "unattributed"
RECEIVER = "cost-attribution"

#: 单次聚合的取数上限；超出该值请按 start_date/end_date 分段。
MAX_WINDOW_ENTRIES = 10_000


def _bucket_add(bucket: dict[str, dict[str, Any]], key: str, entry: CostEntry) -> None:
    item = bucket.setdefault(key, {"cost_usd": 0.0, "calls": 0, "tokens": 0})
    item["cost_usd"] = round(item["cost_usd"] + float(entry.cost_usd or 0.0), 6)
    item["calls"] += 1
    item["tokens"] += int(entry.total_tokens or 0)


def attribution_summary(
    *,
    tracker: CostTracker | None = None,
    start_date: str = "",
    end_date: str = "",
    limit: int = MAX_WINDOW_ENTRIES,
) -> dict[str, Any]:
    """按 agent / phase / model 三维聚合。

    返回 ``{"total": {...}, "by_agent": {...}, "by_phase": {...}, "by_model": {...}}``；
    每个分桶含 ``cost_usd`` / ``calls`` / ``tokens`` 三键。聚合值与
    ``cost_entries`` 原始记录逐条同源（同一批 ``CostEntry``），不引入估算。
    """
    entries = (tracker or CostTracker()).get_entries(
        start_date=start_date, end_date=end_date, limit=limit
    )
    by_agent: dict[str, dict[str, Any]] = {}
    by_phase: dict[str, dict[str, Any]] = {}
    by_model: dict[str, dict[str, Any]] = {}
    total = {"cost_usd": 0.0, "calls": 0, "tokens": 0}
    for entry in entries:
        phase = str(entry.metadata.get("phase") or UNATTRIBUTED)
        _bucket_add(by_agent, entry.agent or UNATTRIBUTED, entry)
        _bucket_add(by_phase, phase, entry)
        _bucket_add(by_model, entry.model or UNATTRIBUTED, entry)
        total["cost_usd"] = round(total["cost_usd"] + float(entry.cost_usd or 0.0), 6)
        total["calls"] += 1
        total["tokens"] += int(entry.total_tokens or 0)
    return {"total": total, "by_agent": by_agent, "by_phase": by_phase, "by_model": by_model}


def check_budget_thresholds(
    summary: dict[str, Any],
    *,
    total_budget_usd: float | None = None,
    per_agent_budget_usd: float | None = None,
    per_phase_budget_usd: float | None = None,
) -> list[dict[str, Any]]:
    """对合计与 agent/phase 分桶做越阈检查，返回 Alertmanager 形状告警（不落库）。

    严重度：超出即 ``warning``；超出 20% 及以上记 ``critical``。预算为
    ``None`` 或非正数的维度不检查（未配置 ≠ 预算为零）。
    """
    alerts: list[dict[str, Any]] = []

    def _check(dimension: str, key: str, spent: float, budget: float | None) -> None:
        if budget is None or budget <= 0 or spent <= budget:
            return
        ratio = spent / budget
        labels: dict[str, str] = {
            "alertname": "CostBudgetThreshold",
            "severity": "critical" if ratio >= 1.2 else "warning",
            "dimension": dimension,
            "key": key,
        }
        # alert_ingest 对 agent 标签有专列（instance/agent/tenant），带上便于检索。
        if dimension == "agent":
            labels["agent"] = key
        alerts.append(
            {
                "status": "firing",
                "labels": labels,
                "annotations": {
                    "summary": f"成本越阈：{dimension}={key}",
                    "description": (
                        f"已花 ${spent:.4f} / 预算 ${budget:.4f}（{ratio * 100:.1f}%）"
                    ),
                },
            }
        )

    _check("total", "total", float(summary["total"]["cost_usd"]), total_budget_usd)
    for agent, item in sorted(summary["by_agent"].items()):
        _check("agent", agent, float(item["cost_usd"]), per_agent_budget_usd)
    for phase, item in sorted(summary["by_phase"].items()):
        _check("phase", phase, float(item["cost_usd"]), per_phase_budget_usd)
    return alerts


def emit_budget_alerts(
    *,
    tracker: CostTracker | None = None,
    start_date: str = "",
    end_date: str = "",
    limit: int = MAX_WINDOW_ENTRIES,
    total_budget_usd: float | None = None,
    per_agent_budget_usd: float | None = None,
    per_phase_budget_usd: float | None = None,
) -> dict[str, Any]:
    """聚合 → 越阈检查 → 经 alert_ingest 落库。返回 ``{"alerts", "ingest"}``。"""
    from maop.core.alert_ingest import record_alerts

    summary = attribution_summary(
        tracker=tracker, start_date=start_date, end_date=end_date, limit=limit
    )
    alerts = check_budget_thresholds(
        summary,
        total_budget_usd=total_budget_usd,
        per_agent_budget_usd=per_agent_budget_usd,
        per_phase_budget_usd=per_phase_budget_usd,
    )
    ingest = (
        record_alerts(alerts, receiver=RECEIVER)
        if alerts
        else {"received": 0, "stored": 0, "duplicate": 0}
    )
    if alerts:
        logger.info(
            "[cost-attribution] budget alerts emitted: %d (stored=%s duplicate=%s)",
            len(alerts), ingest.get("stored"), ingest.get("duplicate"),
        )
    return {"alerts": alerts, "ingest": ingest}
