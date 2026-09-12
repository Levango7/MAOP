"""MAOP Agent 路由限流器 — 滑动窗口算法.

按 Agent 粒度实施每分钟请求数限制（``AgentDescriptor.rate_limit_per_min``）。
使用滑动窗口算法：维护每个 Agent 最近 60 秒内的请求时间戳队列，
新请求到达时先淘汰过期时间戳，再判断窗口内请求数是否超限。

核心组件：
  - ``RateLimiter`` — Agent 路由限流器（线程安全）

Usage::

    from maop.core.agent.router.rate_limiter import RateLimiter

    limiter = RateLimiter()
    # rate_limit_per_min=100：每分钟允许 100 次请求
    if limiter.check_and_record("claude-code", rate_limit_per_min=100):
        ...  # 放行执行
    else:
        ...  # 触发限流，拒绝或降级

设计要点：
  - ``rate_limit_per_min == 0`` 表示不限流，直接放行
  - 线程安全：使用 ``threading.RLock`` 保护时间戳队列
  - 内存安全：``cleanup()`` 清理过期时间戳，防止长期运行内存泄漏
"""

from __future__ import annotations

import logging
import threading
import time

logger = logging.getLogger(__name__)

# 滑动窗口大小（秒）：60 秒 = 1 分钟
_WINDOW_SECONDS: float = 60.0


class RateLimiter:
    """Agent 路由限流器（滑动窗口算法，线程安全）.

    内存中维护 ``dict[str, list[float]]``（agent_name → 请求时间戳队列）。
    每次 ``check_and_record`` 先淘汰窗口外的时间戳，再判断是否超限。

    Parameters
    ----------
    window_seconds : float
        滑动窗口大小（秒），默认 60 秒。测试时可调小以加速验证。
    """

    def __init__(self, window_seconds: float = _WINDOW_SECONDS) -> None:
        self._window_seconds: float = window_seconds
        # agent_name -> 请求时间戳队列（单调递增）
        self._timestamps: dict[str, list[float]] = {}
        # 可重入锁：保护 _timestamps 的并发读写
        self._lock: threading.RLock = threading.RLock()

    # ── 核心接口 ──────────────────────────────────────────────────
    def check_and_record(
        self,
        agent_name: str,
        rate_limit_per_min: int,
    ) -> bool:
        """检查并记录一次请求.

        如果 ``agent_name`` 在最近 ``window_seconds`` 秒内的请求数
        < ``rate_limit_per_min``，记录当前时间戳并返回 True（放行）；
        否则返回 False（拒绝，不记录时间戳）。

        ``rate_limit_per_min == 0`` 表示不限流，直接返回 True（不记录）。

        Parameters
        ----------
        agent_name : str
            Agent 名称。
        rate_limit_per_min : int
            每分钟允许的最大请求数。``0`` 表示不限流。

        Returns
        -------
        bool
            True 表示放行；False 表示触发限流。
        """
        # 0 表示不限流，直接放行（不记录时间戳，避免无谓内存占用）
        if rate_limit_per_min <= 0:
            return True

        now = time.monotonic()
        with self._lock:
            # 淘汰窗口外的过期时间戳
            timestamps = self._timestamps.get(agent_name, [])
            cutoff = now - self._window_seconds
            # 利用时间戳单调递增特性，找到第一个未过期的位置
            fresh = [ts for ts in timestamps if ts > cutoff]

            if len(fresh) >= rate_limit_per_min:
                # 已达上限，拒绝（不记录）
                if fresh:
                    self._timestamps[agent_name] = fresh
                elif agent_name in self._timestamps:
                    # 全部过期，移除键保持整洁
                    del self._timestamps[agent_name]
                logger.debug(
                    "[rate_limiter] %s 限流触发: %d/%d",
                    agent_name, len(fresh), rate_limit_per_min,
                )
                return False

            # 放行并记录当前时间戳
            fresh.append(now)
            self._timestamps[agent_name] = fresh
            return True

    def get_current_rate(self, agent_name: str) -> int:
        """获取当前滑动窗口内的请求数.

        先淘汰过期时间戳，再返回剩余数量。

        Parameters
        ----------
        agent_name : str
            Agent 名称。

        Returns
        -------
        int
            当前窗口内请求数（已淘汰过期项）。
        """
        now = time.monotonic()
        with self._lock:
            timestamps = self._timestamps.get(agent_name, [])
            cutoff = now - self._window_seconds
            fresh = [ts for ts in timestamps if ts > cutoff]
            if fresh:
                self._timestamps[agent_name] = fresh
            elif agent_name in self._timestamps:
                # 全部过期，移除键保持整洁（避免残留空列表）
                del self._timestamps[agent_name]
            return len(fresh)

    def cleanup(self) -> None:
        """清理所有 Agent 的过期时间戳，防止内存泄漏.

        长期运行时，已停止使用的 Agent 的时间戳队列会残留。本方法
        遍历所有队列，淘汰过期项，并移除空队列。
        """
        now = time.monotonic()
        cutoff = now - self._window_seconds
        with self._lock:
            empty_keys: list[str] = []
            for name, timestamps in self._timestamps.items():
                fresh = [ts for ts in timestamps if ts > cutoff]
                if fresh:
                    self._timestamps[name] = fresh
                else:
                    empty_keys.append(name)
            for name in empty_keys:
                del self._timestamps[name]

    def reset(self, agent_name: str) -> None:
        """重置指定 Agent 的限流状态（清空时间戳队列）.

        主要用于测试或手动重置场景。

        Parameters
        ----------
        agent_name : str
            Agent 名称。
        """
        with self._lock:
            self._timestamps.pop(agent_name, None)

    def reset_all(self) -> None:
        """重置所有 Agent 的限流状态。"""
        with self._lock:
            self._timestamps.clear()