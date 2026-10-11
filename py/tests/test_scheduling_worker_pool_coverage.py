"""Coverage tests for maop.core.scheduling.worker_pool — Worker 注册表.

该模块在基线测试中覆盖率为 0%。本文件补充 WorkerRegistry 的核心功能测试。
使用 fakeredis 作为 Redis 替代，避免外部依赖。
"""

from __future__ import annotations

import time

import fakeredis
import pytest

from maop.core.scheduling.worker_pool import (
    WorkerInfo,
    WorkerRegistry,
    WorkerStatus,
)


@pytest.fixture
def registry():
    """提供一个使用 fakeredis 的 WorkerRegistry 实例。"""
    fake = fakeredis.FakeRedis()
    return WorkerRegistry(fake, heartbeat_timeout=15.0)


@pytest.fixture
def registry_short_timeout():
    """提供一个短超时的 registry，用于测试心跳过期。"""
    fake = fakeredis.FakeRedis()
    return WorkerRegistry(fake, heartbeat_timeout=1.0)


class TestWorkerStatus:
    """测试 WorkerStatus 枚举。"""

    def test_values(self):
        assert WorkerStatus.ACTIVE.value == "active"
        assert WorkerStatus.FAILED.value == "failed"
        assert WorkerStatus.DRAINING.value == "draining"
        assert WorkerStatus.STOPPED.value == "stopped"

    def test_is_str_enum(self):
        assert isinstance(WorkerStatus.ACTIVE, str)


class TestWorkerInfo:
    """测试 WorkerInfo 数据类。"""

    def test_defaults(self):
        info = WorkerInfo(worker_id="w1")
        assert info.worker_id == "w1"
        assert info.host == ""
        assert info.concurrency == 4
        assert info.capabilities == set()
        assert info.status == WorkerStatus.ACTIVE
        assert info.in_flight == set()

    def test_with_capabilities(self):
        info = WorkerInfo(worker_id="w1", host="node-1", concurrency=8, capabilities={"gpu", "linux"})
        assert info.host == "node-1"
        assert info.concurrency == 8
        assert info.capabilities == {"gpu", "linux"}


class TestWorkerRegistryRegister:
    """测试 WorkerRegistry.register。"""

    def test_register_returns_id(self, registry):
        wid = registry.register(host="node-1", concurrency=4)
        assert isinstance(wid, str)
        assert len(wid) > 0

    def test_register_with_explicit_id(self, registry):
        wid = registry.register(worker_id="my-worker", host="node-1")
        assert wid == "my-worker"

    def test_register_with_capabilities(self, registry):
        wid = registry.register(worker_id="w1", capabilities={"gpu", "linux"})
        info = registry.get_worker(wid)
        assert info is not None
        assert info.capabilities == {"gpu", "linux"}

    def test_register_no_capabilities(self, registry):
        wid = registry.register(worker_id="w1")
        info = registry.get_worker(wid)
        assert info is not None
        assert info.capabilities == set()

    def test_register_in_flight_initialized(self, registry):
        wid = registry.register(worker_id="w1")
        assert registry.in_flight(wid) == set()


class TestWorkerRegistryUnregister:
    """测试 WorkerRegistry.unregister。"""

    def test_unregister_existing(self, registry):
        registry.register(worker_id="w1")
        assert registry.unregister("w1") is True
        assert registry.get_worker("w1") is None

    def test_unregister_nonexistent(self, registry):
        assert registry.unregister("nonexistent") is False

    def test_unregister_removes_from_list(self, registry):
        registry.register(worker_id="w1")
        registry.register(worker_id="w2")
        registry.unregister("w1")
        assert registry.list_workers() == ["w2"]


class TestWorkerRegistryHeartbeat:
    """测试 WorkerRegistry.heartbeat。"""

    def test_heartbeat_refreshes(self, registry):
        registry.register(worker_id="w1")
        assert registry.heartbeat("w1") is True

    def test_heartbeat_nonexistent(self, registry):
        assert registry.heartbeat("nonexistent") is False

    def test_heartbeat_updates_timestamp(self, registry):
        registry.register(worker_id="w1")
        time.sleep(0.01)
        registry.heartbeat("w1")
        info = registry.get_worker("w1")
        assert info is not None
        assert info.last_heartbeat > info.registered_at


class TestWorkerRegistryDetectFailures:
    """测试 WorkerRegistry.detect_failures。"""

    def test_no_failures(self, registry):
        registry.register(worker_id="w1")
        failures = registry.detect_failures()
        assert failures == []

    def test_detect_expired_worker(self, registry_short_timeout):
        registry_short_timeout.register(worker_id="w1")
        # 等待心跳过期
        time.sleep(1.5)
        failures = registry_short_timeout.detect_failures()
        assert len(failures) == 1
        assert failures[0][0] == "w1"
        assert failures[0][1] == set()  # 没有 in_flight 任务

    def test_detect_failures_returns_in_flight(self, registry_short_timeout):
        registry_short_timeout.register(worker_id="w1")
        registry_short_timeout.assign_task("w1", "task-1")
        registry_short_timeout.assign_task("w1", "task-2")
        time.sleep(1.5)
        failures = registry_short_timeout.detect_failures()
        assert len(failures) == 1
        assert failures[0][0] == "w1"
        assert failures[0][1] == {"task-1", "task-2"}

    def test_detect_failures_prunes_registry(self, registry_short_timeout):
        registry_short_timeout.register(worker_id="w1")
        time.sleep(1.5)
        registry_short_timeout.detect_failures()
        assert registry_short_timeout.list_workers() == []


class TestWorkerRegistryCapableWorkers:
    """测试 WorkerRegistry.capable_workers。"""

    def test_no_requirement(self, registry):
        registry.register(worker_id="w1", capabilities={"gpu"})
        registry.register(worker_id="w2", capabilities={"cpu"})
        result = registry.capable_workers(None)
        assert set(result) == {"w1", "w2"}

    def test_single_tag_requirement(self, registry):
        registry.register(worker_id="w1", capabilities={"gpu", "linux"})
        registry.register(worker_id="w2", capabilities={"cpu"})
        result = registry.capable_workers("gpu")
        assert result == ["w1"]

    def test_set_tag_requirement(self, registry):
        registry.register(worker_id="w1", capabilities={"gpu", "linux"})
        registry.register(worker_id="w2", capabilities={"gpu", "windows"})
        registry.register(worker_id="w3", capabilities={"gpu"})
        result = registry.capable_workers({"gpu", "linux"})
        assert result == ["w1"]

    def test_empty_requirement(self, registry):
        registry.register(worker_id="w1")
        result = registry.capable_workers(set())
        assert result == ["w1"]


class TestWorkerRegistryInFlight:
    """测试 in-flight 任务跟踪。"""

    def test_assign_task(self, registry):
        registry.register(worker_id="w1")
        registry.assign_task("w1", "task-1")
        assert registry.in_flight("w1") == {"task-1"}

    def test_complete_task(self, registry):
        registry.register(worker_id="w1")
        registry.assign_task("w1", "task-1")
        registry.complete_task("w1", "task-1")
        assert registry.in_flight("w1") == set()

    def test_complete_nonexistent_task(self, registry):
        registry.register(worker_id="w1")
        registry.complete_task("w1", "nonexistent")  # 不应抛异常
        assert registry.in_flight("w1") == set()

    def test_in_flight_nonexistent_worker(self, registry):
        assert registry.in_flight("nonexistent") == set()


class TestWorkerRegistryIntrospection:
    """测试 list_workers / get_worker / active_count。"""

    def test_list_workers_empty(self, registry):
        assert registry.list_workers() == []

    def test_list_workers_sorted(self, registry):
        registry.register(worker_id="w2")
        registry.register(worker_id="w1")
        assert registry.list_workers() == ["w1", "w2"]

    def test_get_worker_nonexistent(self, registry):
        assert registry.get_worker("nonexistent") is None

    def test_get_worker_info(self, registry):
        registry.register(worker_id="w1", host="node-1", concurrency=8, capabilities={"gpu"})
        info = registry.get_worker("w1")
        assert info is not None
        assert info.worker_id == "w1"
        assert info.host == "node-1"
        assert info.concurrency == 8
        assert info.capabilities == {"gpu"}
        assert info.status == WorkerStatus.ACTIVE

    def test_active_count(self, registry):
        registry.register(worker_id="w1")
        registry.register(worker_id="w2")
        assert registry.active_count() == 2

    def test_active_count_with_expired(self, registry_short_timeout):
        registry_short_timeout.register(worker_id="w1")
        registry_short_timeout.register(worker_id="w2")
        time.sleep(1.5)
        # w1 和 w2 的心跳都过期了
        assert registry_short_timeout.active_count() == 0


class TestWorkerRegistryRepr:
    """测试 __repr__。"""

    def test_repr(self, registry):
        r = repr(registry)
        assert "WorkerRegistry" in r
        assert "timeout=15.0" in r

# ── 批量化读取的往返数守卫（2026-10-10 perf 修复）────────────────────
#
# 背景：capable_workers 在每次派发的选工路径上、detect_failures 每秒跑一次，
# 两者原来对每个 worker 一次 redis 往返（1+N 次）。100 节点层 × 50 worker
# = 5000 次往返的真实 redis 场景下，这是可测量的派发延迟与事件循环占用。
# 修为 pipeline 批量化（恒 2 次往返）后，用调用计数代理钉住——防止回归。


class _CountingRedis:
    """fakeredis 代理：计客户端级调用次数。

    pipeline 内的命令直接在底层客户端上执行、不经过本代理，因此
    ``pipeline()`` + ``execute()`` 只计 2 次——这正是批量化生效的判据。
    """

    def __init__(self, inner):
        self._inner = inner
        self.calls = 0

    def __getattr__(self, name):
        attr = getattr(self._inner, name)
        if callable(attr):
            def counted(*args, **kwargs):
                self.calls += 1
                return attr(*args, **kwargs)
            return counted
        return attr


class TestWorkerRegistryBatchReads:
    """capable_workers / detect_failures 的往返数必须与 worker 数无关。"""

    def _counted_registry(self, n_workers=5, caps_of=None):
        client = _CountingRedis(fakeredis.FakeRedis())
        registry = WorkerRegistry(client, heartbeat_timeout=15.0)
        for i in range(n_workers):
            caps = (caps_of or (lambda _i: set()))(i)
            registry.register(worker_id=f"w{i}", capabilities=caps)
        client.calls = 0  # 注册后的调用不计入测量
        return registry, client

    def test_capable_workers_is_o1_roundtrips(self):
        registry, client = self._counted_registry(n_workers=20)
        registry.capable_workers({"gpu"})
        # 修前：smembers 1 + 20×hget = 21 次；修后：smembers 1 + pipeline 2 = 3 次
        assert client.calls <= 3, (
            f"capable_workers 用了 {client.calls} 次往返（20 workers）——"
            "批量化回归了？"
        )

    def test_detect_failures_is_o1_roundtrips(self):
        registry, client = self._counted_registry(n_workers=20)
        registry.detect_failures()
        # 修前：smembers 1 + 20×get = 21 次；修后：smembers 1 + pipeline 2 = 3 次
        assert client.calls <= 3, (
            f"detect_failures 用了 {client.calls} 次往返（20 workers）——"
            "批量化回归了？"
        )

    def test_capable_workers_correctness_preserved(self):
        def caps_of(i):
            return {"gpu"} if i % 2 == 0 else {"cpu"}
        registry, _client = self._counted_registry(
            n_workers=6, caps_of=caps_of,
        )
        capable = registry.capable_workers({"gpu"})
        assert capable == ["w0", "w2", "w4"]
        # list_workers 的顺序是 sorted，tie-break 语义不变
        assert registry.capable_workers({"cpu"}) == ["w1", "w3", "w5"]
        # 空要求 = 全部工人（不经批量化路径，仍是 list_workers 原语义）
        assert registry.capable_workers(None) == registry.list_workers()
        assert registry.capable_workers(set()) == registry.list_workers()

    def test_detect_failures_dead_worker_still_pruned(self):
        registry, client = self._counted_registry(n_workers=3)
        # 杀掉 w1 的心跳（删除 TTL key）
        registry._redis.delete(registry._heartbeat_key("w1"))
        client.calls = 0
        failed = registry.detect_failures()
        assert [wid for wid, _tasks in failed] == ["w1"]
        assert registry.list_workers() == ["w0", "w2"]
        # 读管道 2 次 + 死亡清理 pipeline 2 次 = 4 次（与 worker 数无关）
        assert client.calls <= 4, (
            f"detect_failures（含 1 个死亡 worker）用了 {client.calls} 次往返"
        )

    def test_all_alive_detect_failures_no_write_pipeline(self):
        registry, client = self._counted_registry(n_workers=3)
        failed = registry.detect_failures()
        assert failed == []
        # 无死亡 worker 时不应触发清理写管道
        assert client.calls <= 3
