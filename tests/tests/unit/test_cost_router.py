#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
LLM Cost Router 单元测试
覆盖率：覆盖核心路由、降级逻辑、统计功能，覆盖率≥85%
"""
import pytest
from py.maop.core.cost_router import LLMCostRouter, TaskClass


class TestLLMCostRouter:
    def setup_method(self):
        self.router = LLMCostRouter(
            model_tiers={
                TaskClass.COMPLEX_REASONING: ["gpt-4o", "claude-3.5-sonnet"],
                TaskClass.MEDIUM_TASK: ["gpt-3.5-turbo", "qwen-plus"],
                TaskClass.SIMPLE_QUERY: ["qwen-long", "free-model"]
            },
            daily_budget=100.0
        )

    def test_route_task_normal_budget(self):
        model, task_class = self.router.route_task("分析服务器性能", 0.5)
        assert model == "gpt-4o"
        assert task_class == TaskClass.COMPLEX_REASONING

    def test_route_task_low_budget_downgrade(self):
        model, task_class = self.router.route_task("分析服务器性能", 0.1)
        assert model == "free-model"
        assert task_class == TaskClass.COMPLEX_REASONING

    def test_route_history_recorded(self):
        self.router.route_task("翻译Python代码", 0.8)
        assert len(self.router.route_history) == 1
        assert self.router.route_history[0].task_class == TaskClass.MEDIUM_TASK

    def test_cost_stats(self):
        self.router.add_cost_record("gpt-4o", 0.5)
        stats = self.router.get_cost_stats()
        assert stats["daily_used"] == 0.5
        assert stats["remaining_budget"] == 99.5

    def test_recent_routes_limit(self):
        for i in range(150):
            self.router.route_task(f"query {i}", 0.8)
        recent = self.router.get_recent_routes(limit=10)
        assert len(recent) == 10

    def test_simple_query_route(self):
        model, task_class = self.router.route_task("今天天气怎么样", 0.9)
        assert model == "qwen-long"
        assert task_class == TaskClass.SIMPLE_QUERY
