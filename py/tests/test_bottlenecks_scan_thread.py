"""批 8 F2 钉测：performance-bottlenecks 的进程表扫描必须离开事件循环。

背景（2026-11-16）：该端点因在全进程表扫描为同步阻塞调用，空库实测 7.65s
且期间事件循环冻结（全站请求停摆）。修复＝asyncio.to_thread。本测试用慢速
psutil stub 验证：扫描进行时事件循环仍推进（ticker 持续走动）——旧实现
（同步执行）会让 ticker 几乎不走，此断言即为变异敏感情形。
"""
from __future__ import annotations

import asyncio
import time
from types import SimpleNamespace

from maop.dashboard.services.data import analysis as an


class _SlowProcIter:
    def __init__(self, n: int, sleep_s: float) -> None:
        self._n = n
        self._sleep = sleep_s

    def process_iter(self, attrs=None):
        for i in range(self._n):
            time.sleep(self._sleep)
            yield SimpleNamespace(
                info={
                    "pid": 1000 + i,
                    "name": f"proc{i}",
                    "memory_info": SimpleNamespace(rss=(i + 1) * 1024 * 1024),
                    "cpu_percent": 0.0,
                }
            )


def test_scan_off_event_loop(monkeypatch):
    stub = _SlowProcIter(6, 0.12)  # 扫描总耗时 ≥0.72s
    # 批 10 引入 top_memory 缓存/单飞后，清空以防跨测试串扰
    monkeypatch.setattr(an, "_top_memory_cache", {})
    monkeypatch.setattr(an, "_top_memory_inflight", {})
    monkeypatch.setattr(an, "_safe_psutil", lambda: stub)

    ticks = {"n": 0}

    async def scenario():
        async def ticker():
            for _ in range(60):
                await asyncio.sleep(0.02)
                ticks["n"] += 1

        t = asyncio.create_task(ticker())
        try:
            res = await an.analysis_performance_bottlenecks(
                date_from="2026-11-09", date_to="2026-11-16", top_n=5
            )
        finally:
            t.cancel()
        return res

    res = asyncio.run(scenario())
    assert res["status"] == "ok"
    rows = res["data"]["top_memory_consumers"]
    assert len(rows) == 5  # top_n 生效
    assert rows[0]["memory_mb"] >= rows[-1]["memory_mb"]  # 按 RSS 降序
    # 事件循环在扫描期间持续推进（旧同步实现 ≈0-2 次）
    assert ticks["n"] >= 20
