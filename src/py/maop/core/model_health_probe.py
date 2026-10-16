#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
LLM模型健康度探测模块
实现模型可用性探测、延迟统计、健康状态上报功能
"""

import time
import threading
from dataclasses import dataclass, field
from typing import Dict, Optional, List
from enum import Enum


class HealthStatus(Enum):
    """健康状态枚举"""
    HEALTHY = "healthy"
    UNHEALTHY = "unhealthy"
    UNKNOWN = "unknown"


@dataclass
class ModelHealthStats:
    """模型健康统计"""
    model_name: str
    status: HealthStatus = HealthStatus.UNKNOWN
    total_requests: int = 0
    success_count: int = 0
    error_count: int = 0
    avg_latency_ms: float = 0.0
    p99_latency_ms: float = 0.0
    last_error: Optional[str] = None
    last_check_time: float = 0.0
    _latency_samples: List[float] = field(default_factory=list, init=False, repr=False)


class ModelHealthProbe:
    """模型健康探针，定期检查模型可用性"""
    def __init__(self, check_interval: int = 60):
        self.check_interval = check_interval  # 检查间隔，默认60秒
        self.model_stats: Dict[str, ModelHealthStats] = {}
        self._running = False
        self._timer: Optional[threading.Timer] = None

    def register_models(self, model_names: List[str]):
        """注册需要探测的模型列表"""
        for model in model_names:
            if model not in self.model_stats:
                self.model_stats[model] = ModelHealthStats(model_name=model)

    def report_request(self, model_name: str, success: bool, latency_ms: float, error: Optional[str] = None):
        """上报单次模型调用结果"""
        if model_name not in self.model_stats:
            self.register_models([model_name])
        stats = self.model_stats[model_name]
        stats.total_requests += 1
        if success:
            stats.success_count += 1
            stats._latency_samples.append(latency_ms)
            # 保留最近1000个样本计算延迟指标
            if len(stats._latency_samples) > 1000:
                stats._latency_samples = stats._latency_samples[-1000:]
            stats.avg_latency_ms = sum(stats._latency_samples) / len(stats._latency_samples)
            sorted_samples = sorted(stats._latency_samples)
            stats.p99_latency_ms = sorted_samples[int(len(sorted_samples) * 0.99)] if sorted_samples else 0
        else:
            stats.error_count += 1
            stats.last_error = error

    def update_model_status(self, model_name: str, is_healthy: bool):
        """手动更新模型健康状态"""
        if model_name in self.model_stats:
            self.model_stats[model_name].status = HealthStatus.HEALTHY if is_healthy else HealthStatus.UNHEALTHY
            self.model_stats[model_name].last_check_time = time.time()

    def get_healthy_models(self, model_names: List[str]) -> List[str]:
        """获取模型列表中的健康模型"""
        healthy = []
        for model in model_names:
            if model in self.model_stats and self.model_stats[model].status == HealthStatus.HEALTHY:
                healthy.append(model)
        return healthy

    def get_all_stats(self) -> Dict:
        """获取所有模型的统计指标，用于监控大盘展示"""
        return {
            model: {
                "status": stats.status.value,
                "total_requests": stats.total_requests,
                "success_rate": stats.success_count / stats.total_requests if stats.total_requests > 0 else 0,
                "avg_latency_ms": stats.avg_latency_ms,
                "p99_latency_ms": stats.p99_latency_ms,
                "error_count": stats.error_count,
                "last_error": stats.last_error,
                "last_check_time": stats.last_check_time
            }
            for model, stats in self.model_stats.items()
        }

    def start_periodic_check(self, test_func):
        """启动定时检查线程
        test_func: 探测函数，入参模型名，返回 (是否成功, 延迟ms, 错误信息)
        """
        if self._running:
            return
        self._running = True

        def check_all():
            if not self._running:
                return
            for model in list(self.model_stats.keys()):
                try:
                    success, latency, error = test_func(model)
                    self.report_request(model, success, latency, error)
                except Exception as e:
                    self.update_model_status(model, False)
            self._timer = threading.Timer(self.check_interval, check_all)
            self._timer.start()

        check_all()

    def stop(self):
        """停止定时探测"""
        self._running = False
        if self._timer:
            self._timer.cancel()
