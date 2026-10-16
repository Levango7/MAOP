#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
MAOP性能压测脚本
支持路由、工作流编译接口的QPS、延迟基准测试
"""

import time
import requests
from typing import Dict, Tuple
import statistics


def benchmark_request(
    url: str,
    method: str = "GET",
    headers: Dict = None,
    data: Dict = None,
    total_requests: int = 1000,
    concurrency: int = 10
) -> Dict:
    """基准压测"""
    results = []
    errors = 0
    start_time = time.time()
    
    # 并发发送请求
    import concurrent.futures
    def send_request():
        try:
            req_start = time.time()
            if method == "GET":
                resp = requests.get(url, headers=headers, timeout=30)
            else:
                resp = requests.post(url, json=data, headers=headers, timeout=30)
            latency = time.time() - req_start
            if resp.status_code == 200:
                return latency
            else:
                return None
        except:
            return None
    
    with concurrent.futures.ThreadPoolExecutor(max_workers=concurrency) as executor:
        futures = [executor.submit(send_request) for _ in range(total_requests)]
        for future in concurrent.futures.as_completed(futures):
            result = future.result()
            if result is not None:
                results.append(result)
            else:
                errors += 1
    
    total_time = time.time() - start_time
    qps = len(results) / total_time
    latencies = sorted(results)
    
    return {
        "total_requests": total_requests,
        "success_count": len(results),
        "error_count": errors,
        "total_time_s": round(total_time, 2),
        "qps": round(qps, 2),
        "avg_latency_ms": round(statistics.mean(latencies) * 1000, 2) if latencies else 0,
        "p50_latency_ms": round(latencies[int(len(latencies)*0.5)] * 1000, 2) if latencies else 0,
        "p95_latency_ms": round(latencies[int(len(latencies)*0.95)] * 1000, 2) if latencies else 0,
        "p99_latency_ms": round(latencies[int(len(latencies)*0.99)] * 1000, 2) if latencies else 0,
        "max_latency_ms": round(max(latencies) * 1000, 2) if latencies else 0
    }


if __name__ == "__main__":
    # 配置压测参数
    BASE_URL = "http://localhost:5000"
    API_KEY = "test_admin_key_123"
    headers = {"X-API-Key": API_KEY}
    
    # 1. 路由接口压测
    print("开始路由接口压测...")
    route_result = benchmark_request(
        url=f"{BASE_URL}/api/v1/workflow/list",
        method="GET",
        headers=headers,
        total_requests=1000,
        concurrency=20
    )
    print(f"路由接口压测结果: {route_result}")
    
    # 2. 工作流编译接口压测
    workflow_data = {
        "name": "压测工作流",
        "nodes": [
            {"id": "n1", "type": "trigger", "config": {}},
            {"id": "n2", "type": "llm", "config": {}}
        ],
        "edges": [
            {"from": "n1", "to": "n2"}
        ]
    }
    print("开始编译接口压测...")
    compile_result = benchmark_request(
        url=f"{BASE_URL}/api/v1/workflow/compile",
        method="POST",
        headers=headers,
        data=workflow_data,
        total_requests=500,
        concurrency=10
    )
    print(f"编译接口压测结果: {compile_result}")
