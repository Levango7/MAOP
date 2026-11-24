"""P3 批钉测（F-9 失效重连 / CancelledError 取消 worker / 判重原子化）— 2026-11-25。"""
import asyncio
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import MagicMock

import pytest

from maop.core.agent.delegation.a2a import A2AManager
from maop.core.agent.delegation.agent_proxy import AgentProxy


@pytest.fixture(autouse=True)
def _isolated_maop_root(tmp_path, monkeypatch):
    monkeypatch.setenv("MAOP_ROOT_DIR", str(tmp_path))


class _SlowPool:
    def __init__(self):
        self.fut = None
        self.submit_calls = 0
        self.cancelled = False

    async def submit(self, message, workdir="", agent_name=""):
        self.submit_calls += 1
        self.fut = asyncio.get_running_loop().create_future()
        return "wt-1"

    async def wait(self, task_id, timeout=0):
        return await self.fut

    def cancel(self, task_id):
        self.cancelled = True
        return True


def test_f9_execute_failure_triggers_reconnect_on_next_call(tmp_path):
    """execute 失败剔除连接态：下一次 call 必须重新 connect（不重用失效连接）。"""
    proxy = AgentProxy(root_dir=tmp_path)
    adapter = MagicMock()
    adapter.connect = MagicMock(return_value=True)
    adapter.execute = MagicMock(side_effect=[ConnectionError("lost"), "ok"])
    proxy.register("ext", adapter)
    with pytest.raises(ConnectionError):
        proxy.call("ext", "t1")
    assert proxy.call("ext", "t2") == "ok"
    assert adapter.connect.call_count == 2


@pytest.mark.asyncio
async def test_cancellederror_also_cancels_worker(tmp_path):
    """dispatch 协程被取消时，已 submit 的底层 worker 必须同步取消（防孤儿）。"""
    mgr = A2AManager(root_dir=str(tmp_path))
    pool = _SlowPool()
    mgr.set_worker_pool(pool)
    task = mgr.create_task("coder", "w")
    d1 = asyncio.create_task(mgr.dispatch_task(task.task_id, "coder", "w"))
    await asyncio.sleep(0.05)
    d1.cancel()
    with pytest.raises(asyncio.CancelledError):
        await d1
    assert pool.cancelled is True
    assert mgr.get_task(task.task_id).status == "canceled"


@pytest.mark.asyncio
async def test_concurrent_dispatch_single_submit(tmp_path):
    """判重+update_task(working) 原子化：多线程并发 dispatch 同一 working 任务，
    只应产生一次 submit（RLock 保证判重-置 working 原子）。"""
    mgr = A2AManager(root_dir=str(tmp_path))
    pool = _SlowPool()
    mgr.set_worker_pool(pool)
    task = mgr.create_task("coder", "w")

    def run_dispatch():
        asyncio.run(mgr.dispatch_task(task.task_id, "coder", "w"))

    ex = ThreadPoolExecutor(max_workers=6)
    futures = [ex.submit(run_dispatch) for _ in range(6)]
    # 等判重/提交发生
    await asyncio.sleep(0.2)
    # 先结算再收 futures：否则 with/shutdown(wait=True) 会等挂 wait 的 dispatch 而死锁。
    # asyncio.Future 非线程安全：必须经其所属 loop 的 call_soon_threadsafe 结算，
    # 否则 wait 协程不被唤醒（首版 f.result 超时根因）。
    if pool.fut is not None and not pool.fut.done():
        pool.fut.get_loop().call_soon_threadsafe(pool.fut.set_result, {})
    for f in futures:
        f.result(timeout=10)
    ex.shutdown(wait=True)
    assert pool.submit_calls == 1
    assert mgr.get_task(task.task_id).status == "completed"
