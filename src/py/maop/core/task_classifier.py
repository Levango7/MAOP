#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
任务复杂度分类器
基于规则匹配的轻量级任务分类实现，后续可替换为模型分类
"""

from .cost_router import TaskClass


class TaskClassifier:
    """任务分类器，基于关键词和规则匹配的轻量实现"""
    def __init__(self):
        # 复杂推理关键词
        self.complex_keywords = [
            "分析", "架构", "优化", "设计", "复杂", "解决", "方案",
            "analyze", "optimize", "design", "solution", "architecture"
        ]
        # 中等任务关键词
        self.medium_keywords = [
            "翻译", "重写", "实现", "代码", "程序", "测试", "调试",
            "translate", "rewrite", "implement", "code", "test", "debug"
        ]

    def classify(self, query: str) -> TaskClass:
        """对输入查询进行复杂度分类"""
        query_lower = query.lower()
        # 优先判断复杂任务
        for kw in self.complex_keywords:
            if kw in query_lower:
                return TaskClass.COMPLEX_REASONING
        # 判断中等任务
        for kw in self.medium_keywords:
            if kw in query_lower:
                return TaskClass.MEDIUM_TASK
        # 默认简单任务
        return TaskClass.SIMPLE_QUERY
