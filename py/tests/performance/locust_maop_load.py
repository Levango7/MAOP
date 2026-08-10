"""MAOP API 负载压测脚本 (Locust).

用法::

    locust -f locust_maop_load.py --host http://localhost:9079
    locust -f locust_maop_load.py --host http://localhost:9079 \
        --headless -u 100 -r 10 -t 5m

场景与 ``k6_maop_load.js`` 对齐：
  1. 控制面 API (GET /api/agents, /api/models) — 高 QPS, 低延迟
  2. 编排执行 API (POST /api/execute) — 中 QPS, 高延迟
  3. 向量检索 API (POST /api/search) — 中 QPS, 中延迟
  4. 健康检查 (GET /api/health) — 低 QPS, 极低延迟
"""

from __future__ import annotations

import random
import string

from locust import HttpUser, between, task

# ── 测试数据 ────────────────────────────────────────────────────────
AGENT_MODELS = [
    "gpt-4o", "gpt-4o-mini", "claude-3.5-sonnet", "claude-3.5-haiku",
    "gemini-1.5-pro", "gemini-1.5-flash",
]

SEARCH_QUERIES = [
    "agent orchestration plan", "memory consolidation strategy",
    "MCP tool discovery", "budget guard threshold",
    "circuit breaker fallback", "vector search ANN",
    "DAG progress streaming", "knowledge graph entity",
    "multi-tenant RLS", "SAML SSO callback",
]

CONTROL_ENDPOINTS = ["/api/agents", "/api/models", "/api/config"]


def _random_task_label() -> str:
    """生成随机任务标签（避免缓存命中）。"""
    suffix = "".join(random.choices(string.ascii_lowercase + string.digits, k=8))
    return f"locust-task-{suffix}"


class MaopApiUser(HttpUser):
    """模拟单个 MAOP API 用户。

    ``wait_time`` 设为 1-5 秒，模拟真实用户节奏。
    各 task 的 ``weight`` 与 k6 脚本对齐：控制面 40%、编排 20%、检索 30%、健康 10%。
    """

    wait_time = between(1, 5)

    # ── 健康检查 (weight=1, ~10%) ─────────────────────────────────
    @task(1)
    def health_check(self) -> None:
        with self.client.get(
            "/api/health", name="GET /api/health", catch_response=True,
        ) as response:
            if response.status_code == 200:
                response.success()
            else:
                response.failure(f"unexpected status {response.status_code}")

    # ── 控制面 API (weight=4, ~40%) ───────────────────────────────
    @task(4)
    def control_plane(self) -> None:
        endpoint = random.choice(CONTROL_ENDPOINTS)
        with self.client.get(
            endpoint, name=f"GET {endpoint}", catch_response=True,
        ) as response:
            if 200 <= response.status_code < 300:
                response.success()
            else:
                response.failure(f"unexpected status {response.status_code}")

    # ── 编排执行 API (weight=2, ~20%) ─────────────────────────────
    @task(2)
    def execute(self) -> None:
        payload = {
            "model": random.choice(AGENT_MODELS),
            "task": _random_task_label(),
            "maxTurns": 3,
            "tools": ["mcp.search"],
        }
        with self.client.post(
            "/api/execute", json=payload, name="POST /api/execute",
            catch_response=True,
        ) as response:
            if response.status_code in (200, 202):
                response.success()
            else:
                response.failure(f"unexpected status {response.status_code}")

    # ── 向量检索 API (weight=3, ~30%) ─────────────────────────────
    @task(3)
    def search(self) -> None:
        payload = {
            "query": random.choice(SEARCH_QUERIES),
            "topK": 10,
            "threshold": 0.7,
        }
        with self.client.post(
            "/api/search", json=payload, name="POST /api/search",
            catch_response=True,
        ) as response:
            if 200 <= response.status_code < 300:
                response.success()
            else:
                response.failure(f"unexpected status {response.status_code}")


class MaopAdminUser(HttpUser):
    """模拟管理员用户（低频，重操作）。

    ``wait_time`` 较长（5-15 秒），模拟管理员偶尔执行的管理操作。
    """

    wait_time = between(5, 15)
    weight = 0.1  # 管理员占比 10%

    @task
    def get_audit_log(self) -> None:
        with self.client.get(
            "/api/audit?limit=50", name="GET /api/audit", catch_response=True,
        ) as response:
            if 200 <= response.status_code < 300:
                response.success()
            else:
                response.failure(f"unexpected status {response.status_code}")

    @task
    def get_metrics(self) -> None:
        with self.client.get(
            "/api/metrics", name="GET /api/metrics", catch_response=True,
        ) as response:
            if 200 <= response.status_code < 300:
                response.success()
            else:
                response.failure(f"unexpected status {response.status_code}")