
"""长任务管理器测试 — 覆盖 LongRunningTaskManager 全生命周期.

测试用 ``tmp_path`` 隔离 SQLite，每个测试独立数据库，互不干扰。
覆盖：
  - 提交 / 查询 / 进度更新 / 取消 / 完成 / 失败
  - 列表（活跃 / 按 Agent）
  - 超时清理
  - 持久化（关闭后重开）
  - 并发提交（线程安全）
  - 进度边界夹取
  - API 路由端到端
"""

from __future__ import annotations

import threading
import time

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from maop.core.agent.router.long_running_task import (
    STATUS_CANCELLED,
    STATUS_COMPLETED,
    STATUS_FAILED,
    STATUS_PENDING,
    STATUS_RUNNING,
    STATUS_TIMEOUT,
    LongRunningTask,
    LongRunningTaskManager,
)
from tests.thread_join_guard import join_all

# ── Fixtures ──────────────────────────────────────────────────────


@pytest.fixture
def manager(tmp_path):
    """每个测试独立的 LongRunningTaskManager（文件 DB 隔离）."""
    db_path = tmp_path / "test_lrt.db"
    return LongRunningTaskManager(db_path=db_path)


@pytest.fixture
def memory_manager():
    """内存 DB 管理器（速度更快，但无持久化测试价值）."""
    return LongRunningTaskManager(db_path=None)


# ── 核心管理器测试 ────────────────────────────────────────────────


class TestSubmitAndGetStatus:
    """测试提交与查询."""

    def test_submit_returns_task_id(self, manager):
        """提交任务应返回非空 task_id 字符串."""
        task_id = manager.submit("devin", "重构认证模块")
        assert isinstance(task_id, str)
        assert len(task_id) > 0

    def test_get_status_pending(self, manager):
        """新提交的任务状态应为 pending."""
        task_id = manager.submit("devin", "完成任务")
        task = manager.get_status(task_id)
        assert task.task_id == task_id
        assert task.agent_name == "devin"
        assert task.task == "完成任务"
        assert task.status == STATUS_PENDING
        assert task.progress == 0.0
        assert task.submitted_at != ""

    def test_get_status_nonexistent(self, manager):
        """查询不存在的任务应抛出 KeyError."""
        with pytest.raises(KeyError, match="任务不存在"):
            manager.get_status("nonexistent-id-123456")


class TestUpdateProgress:
    """测试进度更新."""

    def test_update_progress(self, manager):
        """更新进度应改变 progress 和 progress_message."""
        task_id = manager.submit("openhands", "数据分析")
        manager.update_progress(task_id, 0.5, "已完成一半")
        task = manager.get_status(task_id)
        assert task.progress == 0.5
        assert task.progress_message == "已完成一半"
        # 首次更新应将 pending → running
        assert task.status == STATUS_RUNNING
        assert task.started_at != ""

    def test_progress_bounds(self, manager):
        """进度值超出 [0, 1] 应被夹取."""
        task_id = manager.submit("devin", "任务")
        # 超过 1.0 应夹取为 1.0
        manager.update_progress(task_id, 1.5, "超额")
        task = manager.get_status(task_id)
        assert task.progress == 1.0
        # 负数应夹取为 0.0
        manager.update_progress(task_id, -0.5, "负数")
        task = manager.get_status(task_id)
        assert task.progress == 0.0

    def test_update_progress_on_terminal_ignored(self, manager):
        """对终态任务更新进度应被静默忽略."""
        task_id = manager.submit("devin", "任务")
        manager.complete(task_id, "完成")
        # 对已完成任务更新进度：不应抛异常，不应改变状态
        manager.update_progress(task_id, 0.5, "不应生效")
        task = manager.get_status(task_id)
        assert task.status == STATUS_COMPLETED
        assert task.progress == 1.0  # complete 设置的


class TestCancel:
    """测试取消."""

    def test_cancel_pending(self, manager):
        """取消 pending 任务应成功."""
        task_id = manager.submit("devin", "待执行任务")
        assert manager.cancel(task_id) is True
        task = manager.get_status(task_id)
        assert task.status == STATUS_CANCELLED
        assert task.completed_at != ""

    def test_cancel_running(self, manager):
        """取消 running 任务应成功."""
        task_id = manager.submit("devin", "运行中任务")
        manager.update_progress(task_id, 0.3, "进行中")
        assert manager.cancel(task_id) is True
        task = manager.get_status(task_id)
        assert task.status == STATUS_CANCELLED

    def test_cancel_completed_fails(self, manager):
        """取消已完成任务应返回 False."""
        task_id = manager.submit("devin", "任务")
        manager.complete(task_id, "结果")
        assert manager.cancel(task_id) is False
        task = manager.get_status(task_id)
        assert task.status == STATUS_COMPLETED

    def test_cancel_nonexistent(self, manager):
        """取消不存在的任务应返回 False."""
        assert manager.cancel("nonexistent-id") is False


class TestCompleteAndFail:
    """测试完成与失败."""

    def test_complete(self, manager):
        """标记完成应设置 result 和 completed_at."""
        task_id = manager.submit("devin", "任务")
        manager.complete(task_id, "重构完成，测试全通过")
        task = manager.get_status(task_id)
        assert task.status == STATUS_COMPLETED
        assert task.result == "重构完成，测试全通过"
        assert task.completed_at != ""
        assert task.progress == 1.0  # complete 应将进度设为 1.0

    def test_fail(self, manager):
        """标记失败应设置 error 和 completed_at."""
        task_id = manager.submit("devin", "任务")
        manager.fail(task_id, "连接超时")
        task = manager.get_status(task_id)
        assert task.status == STATUS_FAILED
        assert task.error == "连接超时"
        assert task.completed_at != ""

    def test_complete_already_terminal_raises(self, manager):
        """对终态任务标记完成应抛出 ValueError."""
        task_id = manager.submit("devin", "任务")
        manager.complete(task_id, "第一次完成")
        with pytest.raises(ValueError, match="已处于终态"):
            manager.complete(task_id, "第二次完成")

    def test_fail_nonexistent_raises(self, manager):
        """对不存在的任务标记失败应抛出 KeyError."""
        with pytest.raises(KeyError):
            manager.fail("nonexistent", "error")


class TestList:
    """测试列表查询."""

    def test_list_active(self, manager):
        """list_active 应只返回 pending/running 任务."""
        tid1 = manager.submit("devin", "任务1")
        tid2 = manager.submit("openhands", "任务2")
        tid3 = manager.submit("devin", "任务3")
        # 将 tid2 推进为 running
        manager.update_progress(tid2, 0.1, "开始")
        # 完成 tid3
        manager.complete(tid3, "done")
        active = manager.list_active()
        active_ids = {t.task_id for t in active}
        assert tid1 in active_ids  # pending
        assert tid2 in active_ids  # running
        assert tid3 not in active_ids  # completed

    def test_list_by_agent(self, manager):
        """list_by_agent 应只返回指定 Agent 的任务."""
        manager.submit("devin", "任务1")
        manager.submit("devin", "任务2")
        manager.submit("openhands", "任务3")
        devin_tasks = manager.list_by_agent("devin")
        assert len(devin_tasks) == 2
        assert all(t.agent_name == "devin" for t in devin_tasks)
        # limit 参数
        manager.submit("devin", "任务4")
        limited = manager.list_by_agent("devin", limit=2)
        assert len(limited) == 2

    def test_list_active_empty(self, manager):
        """无活跃任务时应返回空列表."""
        assert manager.list_active() == []


class TestCleanupExpired:
    """测试超时清理."""

    def test_cleanup_expired(self, manager):
        """超时的 pending/running 任务应被标记为 timeout."""
        # 提交一个超时阈值极短的任务
        tid = manager.submit("devin", "会超时的任务", timeout_s=0.01)
        # 等待超时
        time.sleep(0.05)
        count = manager.cleanup_expired()
        assert count == 1
        task = manager.get_status(tid)
        assert task.status == STATUS_TIMEOUT

    def test_cleanup_not_expired(self, manager):
        """未超时的任务不应被清理."""
        tid = manager.submit("devin", "不会超时", timeout_s=3600)
        count = manager.cleanup_expired()
        assert count == 0
        task = manager.get_status(tid)
        assert task.status == STATUS_PENDING

    def test_cleanup_skips_terminal(self, manager):
        """终态任务不应被清理."""
        tid = manager.submit("devin", "已完成", timeout_s=0.01)
        manager.complete(tid, "done")
        time.sleep(0.05)
        count = manager.cleanup_expired()
        assert count == 0


class TestPersistence:
    """测试持久化."""

    def test_persistence(self, tmp_path):
        """关闭管理器后重新打开，数据应保留."""
        db_path = tmp_path / "persist_test.db"
        mgr1 = LongRunningTaskManager(db_path=db_path)
        task_id = mgr1.submit("devin", "持久化任务", timeout_s=7200)
        mgr1.update_progress(task_id, 0.3, "进行中")
        # 模拟管理器关闭（丢弃实例）
        del mgr1

        # 重新打开同一数据库
        mgr2 = LongRunningTaskManager(db_path=db_path)
        task = mgr2.get_status(task_id)
        assert task.task_id == task_id
        assert task.agent_name == "devin"
        assert task.task == "持久化任务"
        assert task.status == STATUS_RUNNING
        assert task.progress == 0.3
        assert task.progress_message == "进行中"


class TestConcurrency:
    """测试并发安全."""

    @pytest.mark.timeout(240)
    def test_concurrent_submit(self, manager):
        """多线程并发提交应全部成功，task_id 互不相同."""
        num_threads = 10
        tasks_per_thread = 5
        all_ids: list[str] = []
        lock = threading.Lock()

        def worker(thread_idx: int) -> None:
            local_ids: list[str] = []
            for i in range(tasks_per_thread):
                tid = manager.submit(
                    f"agent-{thread_idx}",
                    f"任务-{thread_idx}-{i}",
                )
                local_ids.append(tid)
            with lock:
                all_ids.extend(local_ids)

        threads = [
            threading.Thread(target=worker, args=(t,))
            for t in range(num_threads)
        ]
        for t in threads:
            t.start()
        join_all(threads, 120.0)

        # 所有 ID 应唯一
        assert len(all_ids) == num_threads * tasks_per_thread
        assert len(set(all_ids)) == num_threads * tasks_per_thread
        # 每个任务都应可查询
        for tid in all_ids:
            task = manager.get_status(tid)
            assert task.status == STATUS_PENDING


class TestMetadata:
    """测试元数据."""

    def test_metadata_stored_and_retrieved(self, manager):
        """metadata 应被正确存储和检索."""
        meta = {
            "priority": "high",
            "tags": ["refactor", "auth"],
            "config": {"model": "gpt-4", "max_tokens": 8192},
        }
        task_id = manager.submit("devin", "带元数据的任务", metadata=meta)
        task = manager.get_status(task_id)
        assert task.metadata == meta

    def test_metadata_default_empty(self, manager):
        """不传 metadata 时应为空 dict."""
        task_id = manager.submit("devin", "无元数据任务")
        task = manager.get_status(task_id)
        assert task.metadata == {}


# ── Pydantic 模型测试 ─────────────────────────────────────────────


class TestLongRunningTaskModel:
    """测试 Pydantic 模型."""

    def test_model_defaults(self):
        """模型默认值应正确."""
        task = LongRunningTask(
            task_id="test-id",
            agent_name="devin",
            task="测试任务",
            submitted_at="2026-01-01T00:00:00Z",
        )
        assert task.status == STATUS_PENDING
        assert task.progress == 0.0
        assert task.progress_message == ""
        assert task.result == ""
        assert task.error == ""
        assert task.started_at == ""
        assert task.completed_at == ""
        assert task.timeout_s == 3600.0
        assert task.metadata == {}

    def test_model_serialization(self):
        """模型应可序列化为 dict."""
        task = LongRunningTask(
            task_id="test-id",
            agent_name="devin",
            task="测试",
            submitted_at="2026-01-01T00:00:00Z",
            metadata={"key": "value"},
        )
        d = task.model_dump()
        assert d["task_id"] == "test-id"
        assert d["metadata"] == {"key": "value"}


# ── API 路由端到端测试 ────────────────────────────────────────────


class TestAPIRoutes:
    """测试 HTTP API 路由."""

    @pytest.fixture
    def client(self, manager):
        """构造带注入管理器的 TestClient."""
        from maop.dashboard.routers import long_running_task as lrt_router

        # 注入测试管理器
        lrt_router._set_manager_for_tests(manager)
        app = FastAPI()
        app.include_router(lrt_router.router)
        yield TestClient(app)
        # 清理
        lrt_router._set_manager_for_tests(None)

    def test_api_submit(self, client):
        """POST 提交长任务."""
        r = client.post("/api/long-running-tasks", json={
            "agent_name": "devin",
            "task": "重构模块",
            "timeout_s": 1800,
        })
        assert r.status_code == 200
        data = r.json()
        assert data["status"] == "ok"
        assert "task_id" in data

    def test_api_get_status(self, client):
        """GET 查询任务状态."""
        r = client.post("/api/long-running-tasks", json={
            "agent_name": "devin",
            "task": "测试任务",
        })
        task_id = r.json()["task_id"]

        r2 = client.get(f"/api/long-running-tasks/{task_id}")
        assert r2.status_code == 200
        task = r2.json()["task"]
        assert task["task_id"] == task_id
        assert task["status"] == STATUS_PENDING

    def test_api_get_nonexistent(self, client):
        """GET 不存在的任务应返回 404."""
        r = client.get("/api/long-running-tasks/nonexistent-id")
        assert r.status_code == 404

    def test_api_list_active(self, client):
        """GET 列出活跃任务."""
        client.post("/api/long-running-tasks", json={
            "agent_name": "devin", "task": "任务1",
        })
        client.post("/api/long-running-tasks", json={
            "agent_name": "openhands", "task": "任务2",
        })
        r = client.get("/api/long-running-tasks")
        assert r.status_code == 200
        data = r.json()
        assert data["count"] == 2
        assert len(data["tasks"]) == 2

    def test_api_cancel(self, client):
        """POST 取消任务."""
        r = client.post("/api/long-running-tasks", json={
            "agent_name": "devin", "task": "待取消",
        })
        task_id = r.json()["task_id"]

        r2 = client.post(f"/api/long-running-tasks/{task_id}/cancel")
        assert r2.status_code == 200

        # 验证已取消
        r3 = client.get(f"/api/long-running-tasks/{task_id}")
        assert r3.json()["task"]["status"] == STATUS_CANCELLED

    def test_api_cancel_terminal_conflict(self, client):
        """取消终态任务应返回 409."""
        r = client.post("/api/long-running-tasks", json={
            "agent_name": "devin", "task": "任务",
        })
        task_id = r.json()["task_id"]
        # 先完成
        from maop.dashboard.routers import long_running_task as lrt_router
        lrt_router.get_manager().complete(task_id, "done")
        # 再取消应 409
        r2 = client.post(f"/api/long-running-tasks/{task_id}/cancel")
        assert r2.status_code == 409