"""Agent 路由限流器（RateLimiter）单元测试.

覆盖：
  - 基本限流（达到上限后拒绝）
  - 不限流（rate_limit_per_min=0）
  - 窗口过期后恢复
  - 并发安全（多线程）
  - cleanup 清理过期时间戳
  - get_current_rate 当前速率查询
  - reset / reset_all 重置
  - 不同 Agent 独立计数
  - 拒绝时不记录时间戳
  - 自定义窗口大小
"""

from __future__ import annotations

import threading
import time

from maop.core.agent.router.rate_limiter import RateLimiter


# ── 基本限流 ─────────────────────────────────────────────────────


class TestRateLimiterBasic:
    def test_allow_under_limit(self) -> None:
        """请求数未达上限时应放行."""
        rl = RateLimiter()
        assert rl.check_and_record("agent_a", rate_limit_per_min=10) is True
        assert rl.check_and_record("agent_a", rate_limit_per_min=10) is True

    def test_reject_at_limit(self) -> None:
        """请求数达到上限后应拒绝."""
        rl = RateLimiter()
        # 上限 3，前 3 次放行
        for _ in range(3):
            assert rl.check_and_record("agent_a", rate_limit_per_min=3) is True
        # 第 4 次拒绝
        assert rl.check_and_record("agent_a", rate_limit_per_min=3) is False

    def test_reject_does_not_record(self) -> None:
        """拒绝时不记录时间戳（当前速率不因拒绝而增长）."""
        rl = RateLimiter()
        rl.check_and_record("a", rate_limit_per_min=1)
        # 触发拒绝
        assert rl.check_and_record("a", rate_limit_per_min=1) is False
        # 当前速率仍为 1（拒绝未记录）
        assert rl.get_current_rate("a") == 1


# ── 不限流 ───────────────────────────────────────────────────────


class TestRateLimiterUnlimited:
    def test_zero_means_unlimited(self) -> None:
        """rate_limit_per_min=0 表示不限流，始终放行."""
        rl = RateLimiter()
        for _ in range(100):
            assert rl.check_and_record("agent_a", rate_limit_per_min=0) is True

    def test_negative_means_unlimited(self) -> None:
        """负数同样视为不限流（防御性）."""
        rl = RateLimiter()
        assert rl.check_and_record("agent_a", rate_limit_per_min=-1) is True

    def test_unlimited_does_not_record(self) -> None:
        """不限流时不记录时间戳（get_current_rate 为 0）."""
        rl = RateLimiter()
        for _ in range(50):
            rl.check_and_record("agent_a", rate_limit_per_min=0)
        assert rl.get_current_rate("agent_a") == 0


# ── 窗口过期 ─────────────────────────────────────────────────────


class TestRateLimiterWindowExpiry:
    def test_window_expiry_restores_capacity(self) -> None:
        """窗口过期后应恢复配额."""
        # 使用 0.1s 窗口加速测试
        rl = RateLimiter(window_seconds=0.1)
        assert rl.check_and_record("a", rate_limit_per_min=1) is True
        assert rl.check_and_record("a", rate_limit_per_min=1) is False
        # 等待窗口过期
        time.sleep(0.15)
        assert rl.check_and_record("a", rate_limit_per_min=1) is True

    def test_custom_window_seconds(self) -> None:
        """自定义窗口大小生效."""
        rl = RateLimiter(window_seconds=0.05)
        # 上限 2
        assert rl.check_and_record("a", rate_limit_per_min=2) is True
        assert rl.check_and_record("a", rate_limit_per_min=2) is True
        assert rl.check_and_record("a", rate_limit_per_min=2) is False
        time.sleep(0.06)
        # 窗口过期，恢复
        assert rl.check_and_record("a", rate_limit_per_min=2) is True


# ── 并发安全 ─────────────────────────────────────────────────────


class TestRateLimiterConcurrency:
    def test_thread_safe_under_concurrent_access(self) -> None:
        """多线程并发调用不应破坏内部状态.

        上限 1000，20 线程各 50 次请求，总放行数应恰好 1000。
        """
        rl = RateLimiter()
        limit = 1000
        threads = 20
        per_thread = 50
        allowed = [0] * threads
        barrier = threading.Barrier(threads)

        def worker(idx: int) -> None:
            barrier.wait()  # 同时起跑
            count = 0
            for _ in range(per_thread):
                if rl.check_and_record("shared", rate_limit_per_min=limit):
                    count += 1
            allowed[idx] = count

        ts = [threading.Thread(target=worker, args=(i,)) for i in range(threads)]
        for t in ts:
            t.start()
        for t in ts:
            t.join()

        total_allowed = sum(allowed)
        # 放行总数应恰好等于 limit（不多不少）
        assert total_allowed == limit
        # 当前速率应等于 limit
        assert rl.get_current_rate("shared") == limit

    def test_thread_safe_different_agents(self) -> None:
        """多线程对不同 Agent 并发限流，各自独立计数."""
        rl = RateLimiter()
        results: list[bool] = []
        lock = threading.Lock()

        def worker(agent: str) -> None:
            r = rl.check_and_record(agent, rate_limit_per_min=1)
            with lock:
                results.append(r)

        ts = [
            threading.Thread(target=worker, args=(f"agent_{i}",))
            for i in range(10)
        ]
        for t in ts:
            t.start()
        for t in ts:
            t.join()

        # 每个 Agent 第一次请求都应放行
        assert all(results)
        assert len(results) == 10


# ── cleanup / get_current_rate / reset ───────────────────────────


class TestRateLimiterCleanupAndQuery:
    def test_get_current_rate(self) -> None:
        """get_current_rate 返回窗口内请求数."""
        rl = RateLimiter()
        assert rl.get_current_rate("a") == 0
        rl.check_and_record("a", rate_limit_per_min=10)
        rl.check_and_record("a", rate_limit_per_min=10)
        assert rl.get_current_rate("a") == 2

    def test_get_current_rate_evicts_expired(self) -> None:
        """get_current_rate 淘汰过期时间戳."""
        rl = RateLimiter(window_seconds=0.05)
        rl.check_and_record("a", rate_limit_per_min=10)
        time.sleep(0.06)
        assert rl.get_current_rate("a") == 0

    def test_cleanup_removes_expired(self) -> None:
        """cleanup 清理过期时间戳并移除空队列."""
        rl = RateLimiter(window_seconds=0.05)
        rl.check_and_record("a", rate_limit_per_min=10)
        rl.check_and_record("b", rate_limit_per_min=10)
        time.sleep(0.06)
        rl.cleanup()
        # 内部队列应被清空
        assert rl.get_current_rate("a") == 0
        assert rl.get_current_rate("b") == 0
        # cleanup 后空队列应被移除
        assert "a" not in rl._timestamps
        assert "b" not in rl._timestamps

    def test_cleanup_keeps_fresh(self) -> None:
        """cleanup 保留未过期的时间戳."""
        rl = RateLimiter(window_seconds=10)
        rl.check_and_record("a", rate_limit_per_min=10)
        rl.cleanup()
        assert rl.get_current_rate("a") == 1

    def test_reset_single_agent(self) -> None:
        """reset 清空指定 Agent 的限流状态."""
        rl = RateLimiter()
        rl.check_and_record("a", rate_limit_per_min=10)
        rl.check_and_record("b", rate_limit_per_min=10)
        rl.reset("a")
        assert rl.get_current_rate("a") == 0
        assert rl.get_current_rate("b") == 1

    def test_reset_all(self) -> None:
        """reset_all 清空所有 Agent 的限流状态."""
        rl = RateLimiter()
        rl.check_and_record("a", rate_limit_per_min=10)
        rl.check_and_record("b", rate_limit_per_min=10)
        rl.reset_all()
        assert rl.get_current_rate("a") == 0
        assert rl.get_current_rate("b") == 0


# ── 多 Agent 独立 ────────────────────────────────────────────────


class TestRateLimiterMultiAgent:
    def test_different_agents_independent(self) -> None:
        """不同 Agent 的限流计数相互独立."""
        rl = RateLimiter()
        # agent_a 达上限
        assert rl.check_and_record("agent_a", rate_limit_per_min=1) is True
        assert rl.check_and_record("agent_a", rate_limit_per_min=1) is False
        # agent_b 不受影响
        assert rl.check_and_record("agent_b", rate_limit_per_min=1) is True
        assert rl.get_current_rate("agent_a") == 1
        assert rl.get_current_rate("agent_b") == 1