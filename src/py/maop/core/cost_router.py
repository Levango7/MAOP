#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
LLM智能成本路由器
实现预算感知、任务分类、多因素动态路由与降级功能
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple


class TaskClass(Enum):
    """任务复杂度分类"""
    COMPLEX_REASONING = "complex_reasoning"  # 复杂推理任务
    MEDIUM_TASK = "medium_task"              # 中等复杂度任务
    SIMPLE_QUERY = "simple_query"            # 简单查询任务


@dataclass
class RouteRecord:
    """路由记录"""
    query: str
    selected_model: str
    task_class: TaskClass
    remaining_budget_rate: float
    timestamp: float
    cost: float = 0.0


@dataclass
class LLMCostRouter:
    """LLM成本路由器"""
    model_tiers: Dict[TaskClass, List[str]]
    daily_budget: float
    budget_usage_threshold: float = 0.2  # 预算降级阈值，剩余低于20%降级到低耗模型
    route_history: List[RouteRecord] = field(default_factory=list)
    daily_used: float = 0.0
    total_savings: float = 0.0

    def __post_init__(self):
        """初始化后加载任务分类器"""
        from py.maop.core.task_classifier import TaskClassifier
        self.task_classifier = TaskClassifier()

    def route_task(self, user_query: str, remaining_budget_rate: float) -> Tuple[str, TaskClass]:
        """核心路由方法"""
        # 1. 任务分类
        task_class = self.task_classifier.classify(user_query)

        # 2. 获取候选模型
        candidate_models = self.model_tiers.get(task_class, [])
        if not candidate_models:
            raise ValueError(f"任务类型 {task_class.value} 没有配置候选模型")

        # 3. 预算不足时直接用最便宜的免费模型
        if remaining_budget_rate < self.budget_usage_threshold:
            selected_model = candidate_models[-1]
        else:
            # 4. 优先选第一个健康的高端模型
            selected_model = candidate_models[0]

        # 5. 记录路由历史
        import time
        self.route_history.append(
            RouteRecord(
                query=user_query,
                selected_model=selected_model,
                task_class=task_class,
                remaining_budget_rate=remaining_budget_rate,
                timestamp=time.time()
            )
        )

        return selected_model, task_class

    def update_model_status(self, model_name: str, is_healthy: bool):
        """更新模型健康状态，实际场景对接ModelHealthProbe"""
        pass # 预留接口，后续对接健康探针

    def get_cost_stats(self) -> Dict[str, Any]:
        """获取成本统计指标，供监控大盘查询"""
        return {
            "daily_budget": self.daily_budget,
            "daily_used": self.daily_used,
            "remaining_budget": self.daily_budget - self.daily_used,
            "budget_usage_rate": (self.daily_used / self.daily_budget) if self.daily_budget > 0 else 0,
            "total_route_count": len(self.route_history),
            "total_savings": self.total_savings
        }

    def get_recent_routes(self, limit: int = 100) -> List[Dict[str, Any]]:
        """获取最近路由记录，供监控大盘查询"""
        recent = self.route_history[-limit:] if self.route_history else []
        return [
            {
                "query": r.query[:100] + "..." if len(r.query) > 100 else r.query,
                "selected_model": r.selected_model,
                "task_class": r.task_class.value,
                "remaining_budget_rate": r.remaining_budget_rate,
                "timestamp": r.timestamp,
                "cost": r.cost
            }
            for r in recent
        ]

    def add_cost_record(self, model_name: str, cost: float):
        """记录单次路由成本"""
        self.daily_used += cost
        if self.route_history:
            self.route_history[-1].cost = cost
        # 统计节省金额，默认高端模型单价10倍于免费模型
        if "free" in model_name:
            self.total_savings += cost * 9
