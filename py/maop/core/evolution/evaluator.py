"""MAOP Performance Evaluator — Compute performance metrics from execution traces.

F2-01 Agent 自演化闭环的第一环：从 delegation / LLM 调用 traces 中
计算延迟、成功率、成本等核心指标，为后续 ImprovementSuggester 与
ABTestFramework 提供量化基线。

支持两种输入：
  1. trace 列表 (list[dict]) — 每条 trace 至少包含 ``success`` 布尔字段，
     可选 ``latency_ms`` / ``duration_ms`` / ``cost_usd`` / ``tokens`` /
     ``agent`` / ``model``。
  2. 两个 variant 的 trace 列表对比，返回 :class:`MetricDelta`。

Usage::

    from maop.core.evolution.evaluator import PerformanceEvaluator

    evaluator = PerformanceEvaluator()
    metrics = evaluator.evaluate(traces)
    delta = evaluator.compare(baseline_traces, candidate_traces)
"""

from __future__ import annotations

import logging
import math
import statistics
from collections.abc import Sequence
from typing import Any

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


# ── 指标模型 ──────────────────────────────────────────────────────


class PerformanceMetrics(BaseModel):
    """单组 trace 的聚合性能指标。"""

    sample_count: int = 0
    success_count: int = 0
    failure_count: int = 0
    success_rate: float = 0.0
    avg_latency_ms: float = 0.0
    p50_latency_ms: float = 0.0
    p95_latency_ms: float = 0.0
    p99_latency_ms: float = 0.0
    max_latency_ms: float = 0.0
    total_cost_usd: float = 0.0
    avg_cost_usd: float = 0.0
    total_tokens: int = 0
    avg_tokens: float = 0.0
    # 按 agent / model 分组的成功率，便于定位瓶颈
    by_agent: dict[str, dict[str, float]] = Field(default_factory=dict)
    by_model: dict[str, dict[str, float]] = Field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return self.model_dump()  # type: ignore


class MetricDelta(BaseModel):
    """两组指标的差值 (candidate - baseline)。"""

    success_rate_delta: float = 0.0
    avg_latency_ms_delta: float = 0.0
    p95_latency_ms_delta: float = 0.0
    total_cost_usd_delta: float = 0.0
    total_tokens_delta: int = 0
    improved: bool = False
    regression: bool = False
    summary: str = ""

    def to_dict(self) -> dict[str, Any]:
        return self.model_dump()  # type: ignore


# ── 评估器 ────────────────────────────────────────────────────────


class PerformanceEvaluator:
    """从执行 traces 计算性能指标。

    所有方法都是纯函数式的：不持有可变状态，线程安全。
    """

    def evaluate(self, traces: Sequence[dict[str, Any]]) -> PerformanceMetrics:
        """聚合一组 trace，返回 :class:`PerformanceMetrics`。

        每条 trace 的字段约定（缺失字段安全忽略）::

            {
              "success": bool,            # 必须
              "latency_ms": float,        # 或 duration_ms
              "cost_usd": float,
              "tokens": int,              # 或 total_tokens
              "agent": str,
              "model": str,
            }
        """
        if not traces:
            return PerformanceMetrics()

        samples = list(traces)
        n = len(samples)
        successes = sum(1 for t in samples if _as_bool(t.get("success")))
        latencies = [_as_float(t.get("latency_ms", t.get("duration_ms", 0))) for t in samples]
        latencies = [x for x in latencies if x > 0]
        costs = [_as_float(t.get("cost_usd", 0)) for t in samples]
        tokens_list = [_as_int(t.get("tokens", t.get("total_tokens", 0))) for t in samples]

        success_rate = successes / n if n else 0.0
        avg_latency = statistics.fmean(latencies) if latencies else 0.0
        p50, p95, p99 = _percentiles(latencies)
        max_latency = max(latencies) if latencies else 0.0
        total_cost = sum(costs)
        avg_cost = total_cost / n if n else 0.0
        total_tokens = sum(tokens_list)
        avg_tokens = total_tokens / n if n else 0.0

        by_agent = _group_stats(samples, key="agent")
        by_model = _group_stats(samples, key="model")

        return PerformanceMetrics(
            sample_count=n,
            success_count=successes,
            failure_count=n - successes,
            success_rate=round(success_rate, 4),
            avg_latency_ms=round(avg_latency, 2),
            p50_latency_ms=round(p50, 2),
            p95_latency_ms=round(p95, 2),
            p99_latency_ms=round(p99, 2),
            max_latency_ms=round(max_latency, 2),
            total_cost_usd=round(total_cost, 6),
            avg_cost_usd=round(avg_cost, 6),
            total_tokens=total_tokens,
            avg_tokens=round(avg_tokens, 2),
            by_agent=by_agent,
            by_model=by_model,
        )

    def compare(
        self,
        baseline: Sequence[dict[str, Any]],
        candidate: Sequence[dict[str, Any]],
        *,
        latency_tolerance_pct: float = 5.0,
        cost_tolerance_pct: float = 5.0,
    ) -> MetricDelta:
        """对比两组 trace，返回 :class:`MetricDelta`。

        判定规则：
          - improved: 成功率提升 > 0 且延迟未显著恶化（在 tolerance 内）
          - regression: 成功率下降 或 延迟恶化超过 tolerance
        """
        base = self.evaluate(baseline)
        cand = self.evaluate(candidate)

        sr_delta = cand.success_rate - base.success_rate
        lat_delta = cand.avg_latency_ms - base.avg_latency_ms
        p95_delta = cand.p95_latency_ms - base.p95_latency_ms
        cost_delta = cand.total_cost_usd - base.total_cost_usd
        tokens_delta = cand.total_tokens - base.total_tokens

        lat_threshold = abs(base.avg_latency_ms) * latency_tolerance_pct / 100.0
        cost_threshold = abs(base.total_cost_usd) * cost_tolerance_pct / 100.0

        improved = sr_delta > 0 and lat_delta <= lat_threshold
        regression = sr_delta < 0 or lat_delta > lat_threshold or cost_delta > cost_threshold

        summary = (
            f"success_rate {base.success_rate:.2%}→{cand.success_rate:.2%} "
            f"({sr_delta:+.2%}), "
            f"avg_latency {base.avg_latency_ms:.0f}→{cand.avg_latency_ms:.0f}ms "
            f"({lat_delta:+.0f}ms)"
        )

        return MetricDelta(
            success_rate_delta=round(sr_delta, 4),
            avg_latency_ms_delta=round(lat_delta, 2),
            p95_latency_ms_delta=round(p95_delta, 2),
            total_cost_usd_delta=round(cost_delta, 6),
            total_tokens_delta=tokens_delta,
            improved=improved,
            regression=regression,
            summary=summary,
        )

    def score(self, metrics: PerformanceMetrics) -> float:
        """将指标压缩为单一标量分数 ∈ [0, 1]，用于排序/AB 判定。

        权重：成功率 0.5 + 延迟归一化 0.3 + 成本归一化 0.2。
        延迟/成本越低分越高，用 sigmoid 归一化避免极端值。
        """
        sr = metrics.success_rate
        lat_score = 1.0 / (1.0 + math.exp((metrics.avg_latency_ms - 3000) / 2000))
        cost_score = 1.0 / (1.0 + math.exp((metrics.avg_cost_usd - 0.01) / 0.01))
        return round(0.5 * sr + 0.3 * lat_score + 0.2 * cost_score, 4)


# ── 辅助函数 ──────────────────────────────────────────────────────


def _as_bool(val: Any) -> bool:
    if isinstance(val, bool):
        return val
    if isinstance(val, (int, float)):
        return val > 0
    if isinstance(val, str):
        return val.lower() in ("true", "1", "yes", "ok", "success")
    return False


def _as_float(val: Any) -> float:
    try:
        return float(val)
    except (TypeError, ValueError):
        return 0.0


def _as_int(val: Any) -> int:
    try:
        return int(val)
    except (TypeError, ValueError):
        return 0


def _percentiles(values: list[float]) -> tuple[float, float, float]:
    """返回 (p50, p95, p99)，使用线性插值。"""
    if not values:
        return 0.0, 0.0, 0.0
    s = sorted(values)
    n = len(s)

    def _pct(p: float) -> float:
        if n == 1:
            return s[0]
        k = (n - 1) * p / 100.0
        lo = math.floor(k)
        hi = math.ceil(k)
        if lo == hi:
            return s[lo]
        return s[lo] + (s[hi] - s[lo]) * (k - lo)

    return _pct(50), _pct(95), _pct(99)


def _group_stats(samples: list[dict[str, Any]], *, key: str) -> dict[str, dict[str, float]]:
    """按指定键分组，返回 {value: {success_rate, avg_latency_ms, count}}。"""
    groups: dict[str, list[dict[str, Any]]] = {}
    for t in samples:
        k = t.get(key, "")
        if not k:
            continue
        groups.setdefault(str(k), []).append(t)
    result: dict[str, dict[str, float]] = {}
    for gk, items in groups.items():
        gn = len(items)
        gs = sum(1 for t in items if _as_bool(t.get("success")))
        gl = [_as_float(t.get("latency_ms", t.get("duration_ms", 0))) for t in items]
        gl = [x for x in gl if x > 0]
        result[gk] = {
            "success_rate": round(gs / gn, 4) if gn else 0.0,
            "avg_latency_ms": round(statistics.fmean(gl), 2) if gl else 0.0,
            "count": gn,
        }
    return result