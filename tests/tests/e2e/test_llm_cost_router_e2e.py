#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
阶段三 F3-05 LLM Cost Router E2E 测试用例
覆盖核心路由逻辑、预算感知、降级策略功能验证
"""
import pytest
from maop.core.cost_router import LLMCostRouter, TaskClass

class TestLLMCostRouterE2E:
    def setup_method(self):
        """初始化路由实例，默认配置高端/中端/免费三档模型降级链"""
        self.router = LLMCostRouter(
            model_tiers={
                TaskClass.COMPLEX_REASONING: ["gpt-4o", "claude-3.5-sonnet", "qwen-max"],
                TaskClass.MEDIUM_TASK: ["gpt-3.5-turbo", "claude-3-haiku", "qwen-plus"],
                TaskClass.SIMPLE_QUERY: ["gpt-3.5-turbo", "qwen-long", "free-model"]
            },
            daily_budget=100.0 # 每日100美元预算测试
        )

    def test_high_budget_complex_task_route(self):
        """预算充足时复杂任务优先路由到高端模型"""
        selected_model, task_class = self.router.route_task(
            user_query="分析当前服务器集群的性能瓶颈并提供优化建议",
            remaining_budget_rate=0.8
        )
        assert task_class == TaskClass.COMPLEX_REASONING
        assert selected_model == "gpt-4o"

    def test_medium_budget_medium_task_route(self):
        """预算中等时中等任务路由到中端模型"""
        selected_model, task_class = self.router.route_task(
            user_query="将这段Python代码翻译成Go语言",
            remaining_budget_rate=0.5
        )
        assert task_class == TaskClass.MEDIUM_TASK
        assert selected_model == "gpt-3.5-turbo"

    def test_low_budget_task_downgrade(self):
        """预算低于20%时自动降级到免费模型"""
        selected_model, task_class = self.router.route_task(
            user_query="将这段Python代码翻译成Go语言",
            remaining_budget_rate=0.1
        )
        assert task_class == TaskClass.MEDIUM_TASK
        assert selected_model == "free-model"

    def test_unavailable_model_fallback(self):
        """首个候选模型不可用时自动降级到下一个"""
        # 模拟gpt-4o不可用
        self.router.update_model_status("gpt-4o", is_healthy=False)
        selected_model, task_class = self.router.route_task(
            user_query="分析当前服务器集群的性能瓶颈并提供优化建议",
            remaining_budget_rate=0.8
        )
        assert task_class == TaskClass.COMPLEX_REASONING
        assert selected_model == "claude-3.5-sonnet"

    def test_simple_query_route(self):
        """简单查询路由到低成本模型"""
        selected_model, task_class = self.router.route_task(
            user_query="今天的天气怎么样",
            remaining_budget_rate=0.9
        )
        assert task_class == TaskClass.SIMPLE_QUERY
        assert selected_model in ["gpt-3.5-turbo", "qwen-long", "free-model"]

class TestTaskClassifyE2E:
    def test_task_classification_accuracy(self):
        """任务分类器准确率验证"""
        classifier = self.router.task_classifier
        test_cases = [
            ("分析当前服务器集群的性能瓶颈并提供优化建议", TaskClass.COMPLEX_REASONING),
            ("将这段Python代码翻译成Go语言", TaskClass.MEDIUM_TASK),
            ("今天的天气怎么样", TaskClass.SIMPLE_QUERY),
            ("设计一个高可用的分布式存储系统架构", TaskClass.COMPLEX_REASONING),
            ("写一个快排算法的实现", TaskClass.MEDIUM_TASK),
            ("给我讲个笑话", TaskClass.SIMPLE_QUERY)
        ]
        for query, expected_class in test_cases:
            assert classifier.classify(query) == expected_class, f"分类失败: {query}"
