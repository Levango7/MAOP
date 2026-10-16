#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
实时监控大盘API
提供成本、路由、模型健康度指标查询接口
"""

from flask import Flask, jsonify
from py.maop.core.model_health_probe import ModelHealthProbe
from py.maop.core.cost_router import LLMCostRouter

app = Flask(__name__)

# 全局依赖注入，实际项目中通过配置初始化
health_probe = ModelHealthProbe()
cost_router: LLMCostRouter = None # 运行时初始化


@app.route("/api/v1/monitor/health", methods=["GET"])
def get_health_stats():
    return jsonify({
        "code": 200,
        "data": health_probe.get_all_stats()
    })


@app.route("/api/v1/monitor/cost/summary", methods=["GET"])
def get_cost_summary():
    # 调用Router的统计方法获取成本汇总
    return jsonify({
        "code": 200,
        "data": cost_router.get_cost_stats() if cost_router else {}
    })


@app.route("/api/v1/monitor/routes/recent", methods=["GET"])
def get_recent_routes():
    # 获取最近路由记录
    return jsonify({
        "code": 200,
        "data": cost_router.get_recent_routes(limit=100) if cost_router else []
    })


def run_monitor_api(host: str = "0.0.0.0", port: int = 8080, debug: bool = False):
    app.run(host=host, port=port, debug=debug)


if __name__ == "__main__":
    run_monitor_api(debug=True)
