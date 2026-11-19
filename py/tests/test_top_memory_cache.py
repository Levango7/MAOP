"""批 10 钉测：bottlenecks top_memory 的 30s TTL 缓存 + 请求级 single-flight。

背景（2026-11-19）：top_memory 与日期参数无关，原实现每次调用（含切日期 tab）
都全表重扫（本机 ~5-9s）。批 10 加缓存+单飞：冷缓存并发只扫一次、TTL 内零扫描。
本测试直接并发调 _top_memory_cached，钉住扫描次数与结果一致性。
"""
from __future__ import annotations

import asyncio
import time
from types import SimpleNamespace

from maop.dashboard.services.data import analysis as an


def test_cold_concurrent_single_scan(monkeypatch):
    calls = {"n": 0}

    def slow_scan(ps, top_n):
        calls["n"] += 1
        time.sleep(0.25)  # 模拟全表扫描成本
        return [
            {"pid": 1000 + i, "name": f"p{i}", "memory_mb": float(100 - i), "cpu_percent": 0.0}
            for i in range(top_n)
        ]

    monkeypatch.setattr(an, "_scan_top_memory", slow_scan)
    monkeypatch.setattr(an, "_top_memory_cache", {})
    monkeypatch.setattr(an, "_top_memory_inflight", {})
    stub = SimpleNamespace()  # _safe_psutil 的返回仅占位（扫描已被替换）

    async def scenario():
        rows = await asyncio.gather(*[an._top_memory_cached(stub, 5) for _ in range(6)])
        return rows

    rows = asyncio.run(scenario())
    # 单飞：6 并发只扫一次
    assert calls["n"] == 1
    # 全部拿到同一份（5 行、按 memory_mb 降序）
    assert all(r == rows[0] for r in rows)
    assert len(rows[0]) == 5
    assert rows[0][0]["memory_mb"] >= rows[0][-1]["memory_mb"]
    # 任务结束后 in-flight 已清理
    assert "future" not in an._top_memory_inflight


def test_ttl_second_call_no_scan(monkeypatch):
    calls = {"n": 0}

    def slow_scan(ps, top_n):
        calls["n"] += 1
        time.sleep(0.1)
        return [{"pid": 1, "name": "x", "memory_mb": 1.0, "cpu_percent": 0.0}]

    monkeypatch.setattr(an, "_scan_top_memory", slow_scan)
    monkeypatch.setattr(an, "_top_memory_cache", {})
    monkeypatch.setattr(an, "_top_memory_inflight", {})
    stub = SimpleNamespace()

    async def scenario():
        await an._top_memory_cached(stub, 5)
        await an._top_memory_cached(stub, 5)

    asyncio.run(scenario())
    assert calls["n"] == 1  # 第二次命中 TTL 缓存，零扫描
