"""Swarm 审计修复钉测（F-1~F-5）— 2026-11-24 多厂商 swarm 审计发现。

变异验证纪律：本文件在旧实现（未修复源码）上必须转红、修复后转绿；
执行方式=git stash 四个源码文件后跑本文件（红），stash pop 后复跑（绿）。
"""
import asyncio
import sqlite3
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import pytest

from maop.core.agent.delegation.a2a import A2AManager, A2AMessage
from maop.core.agent.delegation.agent_proxy import AgentProxy
from maop.core.agent.delegation.human_proxy import HumanProxy
from maop.core.agent.delegation.subagent_lifecycle import (
    AgentConfig,
    AgentStatus,
    SubAgentManager,
)


class _SlowPool:
    """submit 立即返回任务 ID、wait 挂起到测试手动结算的 WorkerPool stub。"""

    def __init__(self):
        self.fut = None
        self.cancelled = False

    async def submit(self, message, workdir="", agent_name=""):
        self.fut = asyncio.get_running_loop().create_future()
        return "wt-1"

    async def wait(self, task_id):
        return await self.fut

    def cancel(self, task_id):
        self.cancelled = True
        return True


@pytest.fixture(autouse=True)
def _isolated_maop_root(tmp_path, monkeypatch):
    """get_db_path 不看各 Manager 的 root_dir（统一共享 maop.db）——
    必须钉 MAOP_ROOT_DIR 隔离，否则测试会写本机真实开发库（首跑实测踩到）。"""
    monkeypatch.setenv("MAOP_ROOT_DIR", str(tmp_path))


@pytest.mark.asyncio
async def test_f1_dispatch_waits_real_result_not_task_id(tmp_path):
    """F-1(P0)：dispatch 不得把 WorkerPool 任务 ID 当结果（旧实现秒 completed）。"""
    mgr = A2AManager(root_dir=str(tmp_path))
    pool = _SlowPool()
    mgr.set_worker_pool(pool)
    task = mgr.create_task("coder", "do work")
    dispatch = asyncio.create_task(mgr.dispatch_task(task.task_id, "coder", "do work"))
    await asyncio.sleep(0.05)
    # 旧实现此刻已 completed 且 artifact="wt-1"；修复后必须仍在 working
    assert mgr.get_task(task.task_id).status == "working"
    pool.fut.set_result({"output": "real-result"})
    await dispatch
    t = mgr.get_task(task.task_id)
    assert t.status == "completed"
    assert t.artifacts[0]["result"] == {"output": "real-result"}


@pytest.mark.asyncio
async def test_f3_cancel_cancels_worker_and_terminal_not_overwritten(tmp_path):
    """F-3(P1)：tasks/cancel 真取消 Worker，且终态不被后台完成覆盖。"""
    mgr = A2AManager(root_dir=str(tmp_path))
    pool = _SlowPool()
    mgr.set_worker_pool(pool)
    task = mgr.create_task("coder", "do work")
    dispatch = asyncio.create_task(mgr.dispatch_task(task.task_id, "coder", "do work"))
    await asyncio.sleep(0.05)
    resp = mgr.handle_message(
        A2AMessage(method="tasks/cancel", params={"task_id": task.task_id})
    )
    assert resp.result["status"] == "canceled"
    assert pool.cancelled is True  # 底层 Worker 收到取消（旧实现不调用）
    pool.fut.set_result({"output": "late"})
    await dispatch  # 后台完成到达，但守卫必须拒绝 completed 回写
    assert mgr.get_task(task.task_id).status == "canceled"
    # 终态不可再迁移
    assert mgr.update_task(task.task_id, "completed") is None


@pytest.mark.asyncio
async def test_f2_llm_error_string_becomes_failed_not_completed(tmp_path):
    """F-2(P1)：[LLM Error]/[No provider] 文本不得写成 COMPLETED。"""
    mgr = SubAgentManager(root_dir=tmp_path)

    async def fake_invoke(agent_id, config, task, context):
        return "[LLM Error] provider exploded"

    mgr._invoke_llm = fake_invoke  # type: ignore[attr-defined]
    result = await mgr._execute_agent("agent-1", AgentConfig(name="a"), "task", {})
    assert result.status == AgentStatus.FAILED
    assert "provider exploded" in result.error


@pytest.mark.asyncio
async def test_f2_no_provider_string_becomes_failed(tmp_path):
    mgr = SubAgentManager(root_dir=tmp_path)

    async def fake_invoke(agent_id, config, task, context):
        return "[No provider for model 'ghost']"

    mgr._invoke_llm = fake_invoke  # type: ignore[attr-defined]
    result = await mgr._execute_agent("agent-2", AgentConfig(name="a"), "task", {})
    assert result.status == AgentStatus.FAILED


def test_f4_expire_old_same_day_iso_created_expires(tmp_path):
    """F-4(P1)：当天创建的 ISO「T+时区」记录也必须能过期（旧实现当天恒不过期）。"""
    proxy = HumanProxy(root_dir=tmp_path)
    req_id = proxy.request(task="approve deploy", priority="critical")
    old_iso = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
    with proxy._connect() as conn:
        conn.execute(
            "UPDATE approval_requests SET created=? WHERE id=?", (old_iso, req_id)
        )
    assert proxy.expire_old(hours=1) == 1
    assert proxy.get(req_id).status == "expired"


def test_f5_success_with_broken_state_db_returns_result_not_raise(tmp_path):
    """F-5(P1)：执行成功但状态库故障，按成功返回（不得反转为失败致重试重复）。"""
    proxy = AgentProxy(root_dir=tmp_path)
    adapter = MagicMock()
    adapter.execute = MagicMock(return_value="side-effect-done")
    proxy.register("ext", adapter)
    proxy._connect = MagicMock(side_effect=sqlite3.OperationalError("db gone"))
    assert proxy.call("ext", "task") == "side-effect-done"


def test_f5_execute_failure_original_error_not_masked(tmp_path):
    """F-5 附：执行失败 + 状态库也故障时，原始异常必须抛出不被掩盖。"""
    proxy = AgentProxy(root_dir=tmp_path)
    adapter = MagicMock()
    adapter.execute = MagicMock(side_effect=ValueError("real-cause"))
    proxy.register("ext", adapter)
    proxy._connect = MagicMock(side_effect=sqlite3.OperationalError("db gone"))
    with pytest.raises(ValueError, match="real-cause"):
        proxy.call("ext", "task")
