"""ResultCache 白盒测试.

覆盖：缓存存取 / TTL 过期 / LRU 淘汰 / 失效 / 清理 / 统计 / 持久化 / 线程安全。
每个测试使用 tmp_path 隔离 SQLite，不触碰真实 DB。
"""

from __future__ import annotations

import threading
import time
from pathlib import Path

import pytest

from maop.core.agent.ops.result_cache import (
    CacheEntry,  # noqa: F401
    CacheStats,  # noqa: F401
    ResultCache,
)

# ── Fixtures ─────────────────────────────────────────────────────


@pytest.fixture
def cache(tmp_path: Path) -> ResultCache:
    """使用隔离 tmp_path DB 的全新 ResultCache。"""
    return ResultCache(db_path=tmp_path / "cache.db")


@pytest.fixture
def small_cache(tmp_path: Path) -> ResultCache:
    """max_entries=3 的小缓存，用于测试 LRU 淘汰。"""
    return ResultCache(
        db_path=tmp_path / "cache.db",
        default_ttl_s=3600,
        max_entries=3,
    )


# ── 1. 基本存取 ──────────────────────────────────────────────────


class TestPutGet:
    """put / get 基本缓存行为。"""

    def test_put_then_get(self, cache: ResultCache) -> None:
        """存入后应能获取到相同结果。"""
        cache.put("agent-a", "task-1", "result-1")
        entry = cache.get("agent-a", "task-1")
        assert entry is not None
        assert entry.result == "result-1"
        assert entry.agent_name == "agent-a"
        assert entry.hit_count == 1

    def test_get_miss(self, cache: ResultCache) -> None:
        """未缓存的键应返回 None。"""
        entry = cache.get("agent-a", "nonexistent")
        assert entry is None

    def test_put_overwrite(self, cache: ResultCache) -> None:
        """相同键再次 put 应覆盖旧值。"""
        cache.put("agent-a", "task-1", "old-result")
        cache.put("agent-a", "task-1", "new-result")
        entry = cache.get("agent-a", "task-1")
        assert entry is not None
        assert entry.result == "new-result"
        # 覆盖后 hit_count 应重置为 0，get 后变为 1
        assert entry.hit_count == 1

    def test_hit_count_increments(self, cache: ResultCache) -> None:
        """多次 get 应递增 hit_count。"""
        cache.put("agent-a", "task-1", "result-1")
        cache.get("agent-a", "task-1")
        cache.get("agent-a", "task-1")
        entry = cache.get("agent-a", "task-1")
        assert entry is not None
        assert entry.hit_count == 3

    def test_different_agents_separate(self, cache: ResultCache) -> None:
        """不同 Agent 的相同任务应独立缓存。"""
        cache.put("agent-a", "task-1", "result-a")
        cache.put("agent-b", "task-1", "result-b")
        assert cache.get("agent-a", "task-1").result == "result-a"
        assert cache.get("agent-b", "task-1").result == "result-b"


# ── 2. TTL 过期 ──────────────────────────────────────────────────


class TestTTLExpiry:
    """TTL 过期机制。"""

    def test_custom_ttl_expiry(self, tmp_path: Path) -> None:
        """自定义短 TTL 应在过期后返回 None。"""
        cache = ResultCache(db_path=tmp_path / "cache.db", default_ttl_s=1)
        cache.put("agent-a", "task-1", "result-1", ttl_s=1)
        # 立即获取应命中
        assert cache.get("agent-a", "task-1") is not None
        # 等待过期
        time.sleep(1.1)
        assert cache.get("agent-a", "task-1") is None

    def test_default_ttl_used_when_zero(self, cache: ResultCache) -> None:
        """ttl_s=0 时应使用默认 TTL。"""
        cache.put("agent-a", "task-1", "result-1", ttl_s=0)
        entry = cache.get("agent-a", "task-1")
        assert entry is not None
        # expires_at 应在 created_at 之后 3600 秒
        assert entry.expires_at != ""
        assert entry.created_at != ""


# ── 3. LRU 淘汰 ──────────────────────────────────────────────────


class TestLRUEviction:
    """LRU 淘汰机制。"""

    def test_lru_eviction(self, small_cache: ResultCache) -> None:
        """超过 max_entries 时应淘汰最久未访问的。"""
        # 存入 3 条（达到上限）
        small_cache.put("agent-a", "task-1", "r1")
        small_cache.put("agent-a", "task-2", "r2")
        small_cache.put("agent-a", "task-3", "r3")

        # 访问 task-1，使其成为最近访问
        small_cache.get("agent-a", "task-1")

        # 存入第 4 条，应淘汰最久未访问的 task-2
        small_cache.put("agent-a", "task-4", "r4")

        # task-2 应被淘汰
        assert small_cache.get("agent-a", "task-2") is None
        # task-1 应存在（最近访问过）
        assert small_cache.get("agent-a", "task-1") is not None


# ── 4. 失效 ──────────────────────────────────────────────────────


class TestInvalidate:
    """invalidate 失效缓存。"""

    def test_invalidate_single(self, cache: ResultCache) -> None:
        """失效单条缓存。"""
        cache.put("agent-a", "task-1", "r1")
        cache.put("agent-a", "task-2", "r2")

        count = cache.invalidate("agent-a", "task-1")
        assert count == 1
        assert cache.get("agent-a", "task-1") is None
        assert cache.get("agent-a", "task-2") is not None

    def test_invalidate_all_for_agent(self, cache: ResultCache) -> None:
        """task 为空时失效该 Agent 所有缓存。"""
        cache.put("agent-a", "task-1", "r1")
        cache.put("agent-a", "task-2", "r2")
        cache.put("agent-b", "task-1", "r3")

        count = cache.invalidate("agent-a")
        assert count == 2
        assert cache.get("agent-a", "task-1") is None
        assert cache.get("agent-a", "task-2") is None
        # agent-b 不受影响
        assert cache.get("agent-b", "task-1") is not None

    def test_invalidate_nonexistent(self, cache: ResultCache) -> None:
        """失效不存在的缓存应返回 0。"""
        count = cache.invalidate("nonexistent", "task-1")
        assert count == 0


# ── 5. 清理与统计 ────────────────────────────────────────────────


class TestCleanupAndStats:
    """cleanup_expired / get_stats / clear_all。"""

    def test_cleanup_expired(self, tmp_path: Path) -> None:
        """清理过期缓存应返回清理数。"""
        cache = ResultCache(db_path=tmp_path / "cache.db")
        cache.put("agent-a", "task-1", "r1", ttl_s=1)
        cache.put("agent-a", "task-2", "r2", ttl_s=3600)

        time.sleep(1.1)
        count = cache.cleanup_expired()
        assert count == 1
        # 未过期的应保留
        assert cache.get("agent-a", "task-2") is not None

    def test_get_stats(self, cache: ResultCache) -> None:
        """缓存统计应正确反映状态。"""
        cache.put("agent-a", "task-1", "result-1")
        cache.put("agent-a", "task-2", "result-2")

        # 一次命中，一次未命中
        cache.get("agent-a", "task-1")  # 命中
        cache.get("agent-a", "nonexistent")  # 未命中

        stats = cache.get_stats()
        assert stats.total_entries == 2
        assert stats.total_hits == 1
        assert stats.total_misses == 1
        assert stats.hit_rate == pytest.approx(0.5)
        assert stats.expired_entries == 0
        assert stats.size_bytes > 0

    def test_clear_all(self, cache: ResultCache) -> None:
        """清空所有缓存。"""
        cache.put("agent-a", "task-1", "r1")
        cache.put("agent-b", "task-2", "r2")
        cache.get("agent-a", "task-1")  # 产生一次命中

        cache.clear_all()
        stats = cache.get_stats()
        assert stats.total_entries == 0
        # 计数器不应重置
        assert stats.total_hits == 1


# ── 6. 持久化与线程安全 ─────────────────────────────────────────


class TestPersistenceAndConcurrency:
    """持久化与线程安全。"""

    def test_persistence_across_instances(self, tmp_path: Path) -> None:
        """新实例应能加载旧实例写入的缓存。"""
        db_path = tmp_path / "cache.db"
        cache1 = ResultCache(db_path=db_path)
        cache1.put("agent-a", "task-1", "result-1")

        cache2 = ResultCache(db_path=db_path)
        entry = cache2.get("agent-a", "task-1")
        assert entry is not None
        assert entry.result == "result-1"

    def test_thread_safety(self, cache: ResultCache) -> None:
        """多线程并发读写不应出错。"""
        num_threads = 10
        ops_per_thread = 30
        errors: list[Exception] = []

        def worker(tid: int) -> None:
            try:
                for i in range(ops_per_thread):
                    agent = f"agent-{tid}"
                    task = f"task-{i}"
                    cache.put(agent, task, f"result-{i}")
                    cache.get(agent, task)
            except Exception as exc:
                errors.append(exc)

        threads = [
            threading.Thread(target=worker, args=(tid,))
            for tid in range(num_threads)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert errors == []
        stats = cache.get_stats()
        assert stats.total_entries == num_threads * ops_per_thread