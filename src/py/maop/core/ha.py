#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
高可用保障模块
实现限流、熔断、重试核心功能，提升服务稳定性
"""

import time
import threading
from enum import Enum
from typing import Callable, Optional, Any
from functools import wraps


class CircuitState(Enum):
    """熔断器状态"""
    CLOSED = "closed"       # 正常状态
    OPEN = "open"           # 熔断状态，拒绝请求
    HALF_OPEN = "half_open" # 半开状态，尝试恢复


class RateLimiter:
    """令牌桶限流器"""
    def __init__(self, rate: float = 100, burst: int = 200):
        """
        rate: 每秒允许的请求数
        burst: 最大突发请求数
        """
        self.rate = rate
        self.burst = burst
        self.tokens = burst
        self.last_refill = time.time()
        self._lock = threading.Lock()

    def allow_request(self) -> bool:
        """判断是否允许当前请求"""
        with self._lock:
            now = time.time()
            # 补充令牌
            elapsed = now - self.last_refill
            self.tokens = min(self.burst, self.tokens + elapsed * self.rate)
            self.last_refill = now
            # 消耗令牌
            if self.tokens >= 1:
                self.tokens -= 1
                return True
            return False


class CircuitBreaker:
    """熔断器"""
    def __init__(
        self,
        failure_threshold: int = 5,
        reset_timeout: int = 30,
        success_threshold: int = 2
    ):
        """
        failure_threshold: 连续失败次数阈值，触发熔断
        reset_timeout: 熔断后重试间隔（秒）
        success_threshold: 半开状态连续成功次数，恢复服务
        """
        self.failure_threshold = failure_threshold
        self.reset_timeout = reset_timeout
        self.success_threshold = success_threshold
        self.state = CircuitState.CLOSED
        self.failure_count = 0
        self.success_count = 0
        self.last_failure_time = 0
        self._lock = threading.Lock()

    def execute(self, func: Callable, *args, **kwargs) -> Any:
        """执行被保护的函数，应用熔断逻辑"""
        with self._lock:
            if self.state == CircuitState.OPEN:
                # 超过重试间隔，切换到半开
                if time.time() - self.last_failure_time > self.reset_timeout:
                    self.state = CircuitState.HALF_OPEN
                    self.success_count = 0
                else:
                    raise Exception("服务熔断中，请稍后重试")
        try:
            result = func(*args, **kwargs)
            with self._lock:
                if self.state == CircuitState.HALF_OPEN:
                    self.success_count += 1
                    if self.success_count >= self.success_threshold:
                        self.state = CircuitState.CLOSED
                        self.failure_count = 0
                else:
                    self.failure_count = 0
            return result
        except Exception as e:
            with self._lock:
                self.failure_count += 1
                self.last_failure_time = time.time()
                if self.state == CircuitState.HALF_OPEN or self.failure_count >= self.failure_threshold:
                    self.state = CircuitState.OPEN
            raise e


def retry(max_retries: int = 3, delay: float = 0.1, backoff: float = 2, exceptions: tuple = (Exception,)):
    """
    重试装饰器
    max_retries: 最大重试次数
    delay: 初始重试延迟（秒）
    backoff: 延迟倍数，每次重试延迟翻倍
    exceptions: 触发重试的异常类型
    """
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            current_delay = delay
            last_exception = None
            for i in range(max_retries + 1):
                try:
                    return func(*args, **kwargs)
                except exceptions as e:
                    last_exception = e
                    if i < max_retries:
                        time.sleep(current_delay)
                        current_delay *= backoff
            raise last_exception
        return wrapper
    return decorator
