"""P2 批钉测（F-6~F-11 + dispatch 边缘）— 2026-11-24。

变异验证：旧实现上各用例必须转红（F-6 饿死/F-7 悬挂/F-9 不自动连接/F-10 无字段/
F-11 绕过自引用/working 重复 dispatch/CancelledError 永挂 working），修复后转绿。
"""
import asyncio
import sqlite3
import threading
from unittest.mock import MagicMock

import pytest

from maop.core.agent.delegation.a2a import A2AManager
from maop.core.agent.delegation.agent_proxy import AgentProxy
from maop.core.agent.delegation.human_proxy import HumanProxy
from maop.core.agent.delegation.subagent_lifecycle import (
    AgentConfig,
    AgentStatus,
    SubAgentManager,
)


@pytest.fixture(autouse=True)
def _isolated_maop_root(tmp_path, monkeypatch):
    monkeypatch.setenv("MAOP_ROOT_DIR", str(tmp_path))


class _SlowPool:
    def __init__(self):
        self.fut = None
        self.submit_calls = 0

    async def submit(self, message, workdir="", agent_name=""):
        self.submit_calls += 1
        self.fut = asyncio.get_running_loop().create_future()
        return "wt-1"

    async def wait(self, task_id, timeout=0):
        return await self.fut

    def cancel(self, task_id):
        return True


def test_f6_old_critical_not_starved_by_new_lows(tmp_path):
    """F-6：积压超过 limit*2 时最老的 critical 仍必须排在首位（SQL 内排序）。"""
    proxy = HumanProxy(root_dir=tmp_path)
    cid = proxy.request(task="critical old", priority="critical")
    with proxy._connect() as conn:
        conn.execute(
            "UPDATE approval_requests SET created='2020-01-01T00:00:00+00:00' WHERE id=?",
            (cid,),
        )
    for i in range(30):
        proxy.request(task=f"low {i}", priority="low")
    first = proxy.pending(limit=10)[0]
    assert first.priority == "critical"
    assert first.id == cid


@pytest.mark.asyncio
async def test_f7_started_transcript_failure_cleans_running(tmp_path):
    """F-7：started transcript 写库失败也必须走 finally 清理 _running。"""
    mgr = SubAgentManager(root_dir=tmp_path)
    mgr._running["agent-x"] = MagicMock()
    real_append = mgr._append_transcript

    def flaky(agent_id, event, data):
        if event == "started":
            raise sqlite3.OperationalError("db full")
        return real_append(agent_id, event, data)

    mgr._append_transcript = flaky
    result = await mgr._execute_agent("agent-x", AgentConfig(name="a"), "task", {})
    assert result.status == AgentStatus.FAILED
    assert "agent-x" not in mgr._running


@pytest.mark.asyncio
async def test_f8_cancel_from_other_thread_is_threadsafe(tmp_path):
    """F-8：跨线程 cancel 经 call_soon_threadsafe，不抛且任务被取消。"""
    mgr = SubAgentManager(root_dir=tmp_path)
    loop = asyncio.get_running_loop()

    async def forever():
        await asyncio.sleep(60)

    atask = loop.create_task(forever())
    mgr._running["agent-t"] = atask
    t = threading.Thread(target=lambda: mgr.cancel("agent-t"))
    t.start()
    t.join(timeout=5)
    await asyncio.sleep(0.05)  # 让 call_soon_threadsafe 在主 loop 执行
    assert atask.cancelled() or atask.cancelling()


def test_f9_call_auto_connects_before_execute(tmp_path):
    """F-9：未 connect_all 时 call 自动 connect，且只连一次。"""
    proxy = AgentProxy(root_dir=tmp_path)
    adapter = MagicMock()
    adapter.connect = MagicMock(return_value=True)
    adapter.execute = MagicMock(return_value="ok")
    proxy.register("ext", adapter)
    assert proxy.call("ext", "t") == "ok"
    proxy.call("ext", "t2")
    adapter.connect.assert_called_once()


def test_f9_connect_failure_raises(tmp_path):
    proxy = AgentProxy(root_dir=tmp_path)
    adapter = MagicMock()
    adapter.connect = MagicMock(return_value=False)
    proxy.register("ext", adapter)
    with pytest.raises(ConnectionError):
        proxy.call("ext", "t")


def test_f10_resolver_persisted(tmp_path):
    """F-10：approve/reject 的 resolver 必须持久化为 resolved_by。"""
    proxy = HumanProxy(root_dir=tmp_path)
    rid = proxy.request(task="deploy", priority="high")
    assert proxy.approve(rid, resolver="alice") is True
    assert proxy.get(rid).resolved_by == "alice"
    rid2 = proxy.request(task="deploy2", priority="low")
    assert proxy.reject(rid2, resolver="bob", reason="unsafe") is True
    assert proxy.get(rid2).resolved_by == "bob"


def test_f11_self_ref_without_call_chain_enforced(tmp_path):
    """F-11：无 call_chain 时 parent==agent 也受 max_self_ref_depth 限制。"""
    mgr = SubAgentManager(root_dir=tmp_path)
    mgr._get_depth = lambda name: 3
    with pytest.raises(ValueError, match="Self-reference"):
        mgr.spawn_child(parent="A", agent="A", task="t")
    info = mgr.spawn_child(parent="A", agent="B", task="t")
    assert info is not None


@pytest.mark.asyncio
async def test_working_task_duplicate_dispatch_rejected(tmp_path):
    """working 态重复 dispatch 直接拒绝（不二次 submit、不覆盖 worker 映射）。"""
    mgr = A2AManager(root_dir=str(tmp_path))
    pool = _SlowPool()
    mgr.set_worker_pool(pool)
    task = mgr.create_task("coder", "w")
    d1 = asyncio.create_task(mgr.dispatch_task(task.task_id, "coder", "w"))
    await asyncio.sleep(0.05)
    await mgr.dispatch_task(task.task_id, "coder", "w")
    assert pool.submit_calls == 1
    pool.fut.set_result({})
    await d1


@pytest.mark.asyncio
async def test_dispatch_coroutine_cancel_marks_task_canceled(tmp_path):
    """dispatch 协程自身被取消：任务落 canceled 而非永挂 working。"""
    mgr = A2AManager(root_dir=str(tmp_path))
    pool = _SlowPool()
    mgr.set_worker_pool(pool)
    task = mgr.create_task("coder", "w")
    d1 = asyncio.create_task(mgr.dispatch_task(task.task_id, "coder", "w"))
    await asyncio.sleep(0.05)
    d1.cancel()
    with pytest.raises(asyncio.CancelledError):
        await d1
    assert mgr.get_task(task.task_id).status == "canceled"
