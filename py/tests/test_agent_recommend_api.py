"""``agent_recommend_service`` 与 ``/api/agent-catalog/recommend`` 端点。

覆盖三类"如果坏了用户会被误导"的场景：
  1. 无历史统计时 ``scored=False``（而不是返回空列表让人以为推荐失败）；
  2. ``limit`` 截断生效，且顺序保持评分降序；
  3. 端点确实注册了、参数确实透传到了 service。
"""

from __future__ import annotations

from typing import Any, ClassVar

import pytest

from maop.dashboard.routers import agent_catalog
from maop.dashboard.services import agent_recommend_service


class _FakeStats:
    """替身：避免真连 SQLite，只记录调用参数并返回固定结果。

    构造签名必须与 ``UsageStatistics(db_path=None, catalog=None)`` 兼容 ——
    服务层调用的是 ``UsageStatistics(catalog=...)``，所以 fake 只接受 ``**kwargs``。
    """

    recs: ClassVar[list[Any]] = []

    def __init__(self, **kwargs: Any) -> None:
        self.kwargs = kwargs

    def recommend_agent(self, **kw: Any) -> list[Any]:
        self.called_with = kw
        return self.recs


def _rec(name: str, score: float, cost: float = 0.0) -> Any:
    from maop.core.agent.ops.usage_statistics import AgentRecommendation

    return AgentRecommendation(
        agent_name=name, score=score, reason="r", estimated_cost=cost
    )


class _NoRecs(_FakeStats):
    recs: ClassVar[list[Any]] = []


class _TwoRecs(_FakeStats):
    recs: ClassVar[list[Any]] = [_rec("codex", 0.9), _rec("qwen", 0.4)]


class _TenRecs(_FakeStats):
    recs: ClassVar[list[Any]] = [_rec(f"a{i}", 1.0 - i / 100) for i in range(10)]


# ── service 层 ─────────────────────────────────────────────────────


def test_empty_stats_reports_scored_false(monkeypatch: pytest.MonkeyPatch) -> None:
    """全新部署（无任何历史统计）⇒ scored=False，而不是空列表。"""
    monkeypatch.setattr(
        "maop.core.agent.ops.usage_statistics.UsageStatistics",
        _NoRecs,
    )
    out = agent_recommend_service.recommend_agents()
    assert out["scored"] is False
    assert out["agents"] == []
    assert out["count"] == 0


def test_returns_recommendations_with_scores(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "maop.core.agent.ops.usage_statistics.UsageStatistics",
        _TwoRecs,
    )
    out = agent_recommend_service.recommend_agents(task_type="refactor")
    assert out["scored"] is True
    assert out["count"] == 2
    # 顺序与字段原样透出（不重排、不改名）
    assert [a["agent_name"] for a in out["agents"]] == ["codex", "qwen"]
    assert out["agents"][0]["score"] == 0.9


def test_limit_truncates(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "maop.core.agent.ops.usage_statistics.UsageStatistics",
        _TenRecs,
    )
    out = agent_recommend_service.recommend_agents(limit=3)
    assert out["count"] == 3
    assert [a["agent_name"] for a in out["agents"]] == ["a0", "a1", "a2"]


def test_parameters_are_forwarded(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    class _Spy(_FakeStats):
        def recommend_agent(self, **kw: Any) -> list[Any]:
            captured.update(kw)
            return []

    monkeypatch.setattr(
        "maop.core.agent.ops.usage_statistics.UsageStatistics", _Spy
    )
    agent_recommend_service.recommend_agents(
        task_type="review", capabilities=["code_generation"], prefer_free=False
    )
    assert captured == {
        "task_type": "review",
        "capabilities": ["code_generation"],
        "prefer_free": False,
    }


def test_none_capabilities_becomes_empty_list(monkeypatch: pytest.MonkeyPatch) -> None:
    """``capabilities=None`` 必须转成 ``[]``——下游按"空则不考量"处理。"""
    captured: dict[str, Any] = {}

    class _Spy(_FakeStats):
        def recommend_agent(self, **kw: Any) -> list[Any]:
            captured.update(kw)
            return []

    monkeypatch.setattr(
        "maop.core.agent.ops.usage_statistics.UsageStatistics", _Spy
    )
    agent_recommend_service.recommend_agents()
    assert captured["capabilities"] == []


# ── 路由层 ─────────────────────────────────────────────────────────


def test_recommend_endpoint_is_registered() -> None:
    """端点必须真的注册了 —— 否则 service 写得再好也到不了前端。"""
    paths = {r.path for r in agent_catalog.router.routes}
    assert "/api/agent-catalog/recommend" in paths


def test_recommend_endpoint_uses_response_model() -> None:
    """响应模型契约：与 #141 的 response_model 棘轮方向一致。"""
    route = next(
        r for r in agent_catalog.router.routes
        if r.path == "/api/agent-catalog/recommend"
    )
    assert route.response_model is not None
    assert route.response_model.__name__ == "RecommendAgentsPayload"
