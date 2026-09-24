"""MCP 调用统计 —— 可查询的按小时分桶计数器。

## 为什么需要这个模块

``MCPHubMetricsMixin`` 已在 ``call_tool`` 里插桩（``_record_call_attempt`` /
``_record_call_error`` / ``_record_call_duration``），但那些是 **Prometheus
计数器**：只能被 scrape，**不能反查**「最近 24 小时每小时的调用量」
或「哪个工具最慢」。前端的 MCP 统计面板需要后者，此前无任何后端实现。

## 设计取舍

**按小时分桶的计数器，而不是原始调用记录。**
原始记录（每次调用一行）在长时间运行下会无界增长；而统计面板只需要
分桶聚合值。因此每个 ``(小时桶, server, tool)`` 维护一个定长累加器，
按时间裁剪超过保留窗口的桶 —— 内存占用与调用量无关，只与
「时间跨度 × 工具数」有关。

**内存态，不持久化。** 这是运维遥测（与 Prometheus 计数器同类），
进程重启清零是可接受的语义。若需跨重启保留，应接入时序数据库而非 SQLite。

## 线程安全

``record`` 会被多个并发工具调用触达，故用 ``threading.Lock`` 保护桶字典。
（``call_tool`` 是 async，但 ``asyncio.to_thread`` 与并发请求会让它从
不同线程进入。）
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Any

# 保留窗口（小时）。超过的桶在写入时被裁剪。
DEFAULT_RETENTION_HOURS = 24
# 单次裁剪的触发间隔（秒）——避免每次 record 都全量扫桶。
_PRUNE_INTERVAL_S = 300.0


@dataclass
class _Bucket:
    """单个 (小时桶, server, tool) 的累加器。"""

    calls: int = 0
    errors: int = 0
    total_ms: float = 0.0
    max_ms: float = 0.0

    def add(self, duration_ms: float, success: bool) -> None:
        self.calls += 1
        if not success:
            self.errors += 1
        self.total_ms += duration_ms
        self.max_ms = max(self.max_ms, duration_ms)

    def merge_into(self, acc: dict[str, Any]) -> None:
        acc["calls"] += self.calls
        acc["errors"] += self.errors
        acc["total_ms"] += self.total_ms
        acc["max_ms"] = max(acc["max_ms"], self.max_ms)


@dataclass
class _State:
    buckets: dict[tuple[int, str, str], _Bucket] = field(default_factory=dict)
    last_prune: float = 0.0


class MCPCallStats:
    """MCP 调用统计收集器（按小时分桶，内存态）。"""

    def __init__(self, retention_hours: int = DEFAULT_RETENTION_HOURS) -> None:
        self._retention_s = max(1, retention_hours) * 3600
        self._lock = threading.Lock()
        self._state = _State()

    # ── 写入 ──────────────────────────────────────────────────────

    def record(
        self,
        server_name: str,
        tool_name: str,
        duration_ms: float,
        success: bool,
        *,
        now: float | None = None,
    ) -> None:
        """记录一次工具调用。

        Parameters
        ----------
        server_name, tool_name : str
            用于按工具聚合的维度。
        duration_ms : float
            实际传输耗时（毫秒）。权限拒绝等未发起传输的调用应传 0。
        success : bool
            是否成功（含 JSON-RPC 层错误与 tool-level isError）。
        now : float | None
            测试注入用时间戳（秒）；默认取当前时间。
        """
        ts = time.time() if now is None else now
        bucket_ts = int(ts // 3600) * 3600
        key = (bucket_ts, server_name or "", tool_name or "")

        with self._lock:
            bucket = self._state.buckets.get(key)
            if bucket is None:
                bucket = _Bucket()
                self._state.buckets[key] = bucket
            bucket.add(max(0.0, duration_ms), success)
            self._maybe_prune(ts)

    def _maybe_prune(self, now: float) -> None:
        """裁掉超出保留窗口的桶（调用方须持锁）。"""
        if now - self._state.last_prune < _PRUNE_INTERVAL_S:
            return
        self._state.last_prune = now
        cutoff = int((now - self._retention_s) // 3600) * 3600
        stale = [k for k in self._state.buckets if k[0] < cutoff]
        for k in stale:
            del self._state.buckets[k]

    def clear(self) -> None:
        """清空全部统计（供测试与手动重置）。"""
        with self._lock:
            self._state.buckets.clear()
            self._state.last_prune = 0.0

    # ── 读取 ──────────────────────────────────────────────────────

    def snapshot(self, hours: int = DEFAULT_RETENTION_HOURS) -> dict[str, Any]:
        """返回统计快照。

        Returns
        -------
        dict
            ``{"series": [...], "per_tool": [...], "total": {...}}``

            - ``series``：按小时升序，``{ts, count, errors}``（``ts`` 为 Unix 秒）
            - ``per_tool``：按调用量降序，
              ``{tool, server_name, calls, errors, avg_ms, max_ms, error_rate}``
            - ``total``：``{calls, errors, avg_ms}``
        """
        with self._lock:
            buckets = dict(self._state.buckets)

        cutoff = int((time.time() - max(1, hours) * 3600) // 3600) * 3600
        series: dict[int, dict[str, Any]] = {}
        per_tool: dict[tuple[str, str], dict[str, Any]] = {}

        for (bucket_ts, server_name, tool_name), bucket in buckets.items():
            if bucket_ts < cutoff:
                continue
            row = series.setdefault(
                bucket_ts, {"ts": bucket_ts, "count": 0, "errors": 0}
            )
            row["count"] += bucket.calls
            row["errors"] += bucket.errors

            agg = per_tool.setdefault(
                (server_name, tool_name),
                {
                    "tool": tool_name,
                    "server_name": server_name,
                    "calls": 0,
                    "errors": 0,
                    "total_ms": 0.0,
                    "max_ms": 0.0,
                },
            )
            bucket.merge_into(agg)

        tools = []
        for agg in per_tool.values():
            calls = agg["calls"] or 1
            tools.append({
                "tool": agg["tool"],
                "server_name": agg["server_name"],
                "calls": agg["calls"],
                "errors": agg["errors"],
                "avg_ms": round(agg["total_ms"] / calls, 2),
                "max_ms": round(agg["max_ms"], 2),
                "error_rate": round(agg["errors"] / calls, 4),
            })
        tools.sort(key=lambda t: t["calls"], reverse=True)

        total_calls = sum(t["calls"] for t in tools)
        total_errors = sum(t["errors"] for t in tools)
        total_ms = sum(a["total_ms"] for a in per_tool.values())

        return {
            "series": [series[k] for k in sorted(series)],
            "per_tool": tools,
            "total": {
                "calls": total_calls,
                "errors": total_errors,
                "avg_ms": round(total_ms / total_calls, 2) if total_calls else 0.0,
            },
        }
