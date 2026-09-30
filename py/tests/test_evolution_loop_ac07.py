"""AC-07 验收：Dashboard 闭环可视化。

基于 spec-v5.2.0-evolution-loop.md §7 + §9 AC-07。

AC-07：While 闭环运行，dashboard `/evolve` 必须展示状态机当前状态、A/B 结果、审批入口。

验收标准：
1. GET /api/evolution/loop/status 返回状态机状态 + 最近 cycle + 待审批数量
2. POST /api/evolution/loop/trigger 触发闭环（支持 dry_run）
3. GET /api/evolution/approvals 返回待审批列表
4. POST /api/evolution/approvals/{id}/decision 审批通过/拒绝
5. GET /api/evolution/ab/{cycle_id} 返回 A/B 结果
5. POST /api/evolution/loop/rollback 手动触发回滚
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def test_ac07_evolution_loop_status(evolution_loop_factory):
    """GET /api/evolution/loop/status 返回状态机状态 + 最近 cycle。"""
    evolution_loop_factory()

    # Mock EvolutionLoop.get_cycle_history - function imports locally from maop.core.evolution.evolution_loop
    with patch("maop.core.evolution.evolution_loop.EvolutionLoop") as mock_loop_class, \
         patch("maop.dashboard.routers.evolve_insights.require_admin", return_value=None):
        mock_loop = MagicMock()
        mock_loop.get_cycle_history.return_value = []
        mock_loop.get_stats.return_value = {"total_cycles": 0}
        mock_loop_class.return_value = mock_loop

        import asyncio

        from maop.dashboard.routers.evolve_insights import api_evolution_loop_status
        mock_request = MagicMock()
        result = asyncio.run(api_evolution_loop_status(mock_request))

        assert result["status"] == "ok"
        assert "state" in result
        assert "evolution_loop_enabled" in result
        assert "recent_cycles" in result
        assert "pending_approval_count" in result


def test_ac07_evolution_approvals(evolution_loop_factory):
    """GET /api/evolution/approvals 返回待审批列表。"""
    with patch("maop.core.evolution.evolution_loop.EvolutionLoop") as mock_loop_class, \
         patch("maop.dashboard.routers.evolve_insights.require_admin", return_value=None):

        mock_loop = MagicMock()
        mock_loop.get_cycle_history.return_value = [
            MagicMock(
                cycle_id="cycle-001",
                started_at=1234567890.0,
                pending_approval=["pending-001"],
                approval_state="pending",
                errors_observed=5,
                suggestions_generated=3,
                validation_improved=False,
            )
        ]
        mock_loop_class.return_value = mock_loop

        import asyncio

        from maop.dashboard.routers.evolve_insights import api_evolution_approvals
        mock_request = MagicMock()
        result = asyncio.run(api_evolution_approvals(mock_request))

        assert result["status"] == "ok"
        assert "approvals" in result
        assert len(result["approvals"]) == 1
        assert result["approvals"][0]["cycle_id"] == "cycle-001"


def test_ac07_evolution_ab_results(evolution_loop_factory):
    """GET /api/evolution/ab/{cycle_id} 返回 A/B 结果。"""
    with patch("maop.core.evolution.evolution_loop.EvolutionLoop"), \
         patch("maop.core.evolution.ab_test.ABTestManager") as mock_ab_class, \
         patch("maop.dashboard.routers.evolve_insights.require_admin", return_value=None):

        mock_ab = MagicMock()
        mock_ab.evaluate.return_value = MagicMock(
            p_value=0.03,
            control_rate=0.10,
            treatment_rate=0.12,
            control_count=1000,
            treatment_count=1000,
        )
        mock_ab_class.return_value = mock_ab

        import asyncio

        from maop.dashboard.routers.evolve_insights import api_evolution_ab_results
        mock_request = MagicMock()
        result = asyncio.run(api_evolution_ab_results(mock_request, "cycle-001"))

        assert result["status"] == "ok"
        assert result["cycle_id"] == "cycle-001"
        assert result["ab_result"] is not None
        assert result["ab_result"]["p_value"] == 0.03


def test_ac07_evolution_loop_rollback(evolution_loop_factory):
    """POST /api/evolution/loop/rollback 手动触发回滚。"""

    with patch("maop.core.evolution.evolution_loop.EvolutionLoop") as mock_loop_class, \
         patch("maop.dashboard.routers.evolve_insights.require_admin", return_value=None):

        mock_loop = MagicMock()
        mock_loop.rollback_cycle.return_value = 5
        mock_loop_class.return_value = mock_loop

        import asyncio

        from maop.dashboard.routers.evolve_insights import (
            EvolutionLoopRollbackRequest,
            api_evolution_loop_rollback,
        )
        mock_request = MagicMock()
        mock_request.json = AsyncMock(return_value={
            "cycle_id": "cycle-001",
            "snapshot_id": "snap-001"
        })

        body = EvolutionLoopRollbackRequest(cycle_id="cycle-001", snapshot_id="snap-001")
        result = asyncio.run(api_evolution_loop_rollback(mock_request, body))

        assert result["status"] == "ok"
        assert result["restored_files"] == 5
        assert result["cycle_id"] == "cycle-001"


# ── T3.0 非 mock 回归：真 EvolutionLoop + 真 SQLite（隔离临时目录） ──────
# 背景：AC-07 首版把 EvolutionLoop 全量 mock 掉，两个 500 哑弹因此上线——
# trigger 对同步 run_cycle 做 await（TypeError）；decision 调不存在的
# _load_report（AttributeError）且 approve 分支是 pass。本组测试只 stub
# require_admin（外部边界），loop → SQLite → service → router 全链路真实。


class _NoopRequest:
    """router 在 require_admin 被 stub 后不再读 request——占位即可。"""


def _stub_admin(monkeypatch) -> None:
    monkeypatch.setattr(
        "maop.dashboard.routers.evolve_insights.require_admin",
        lambda request: None,
    )


def test_t300_trigger_runs_real_cycle(evolution_loop_factory, monkeypatch):
    """trigger 走真实 run_cycle（to_thread）且报告真实落 SQLite。"""
    _stub_admin(monkeypatch)
    loop = evolution_loop_factory()

    import asyncio

    from maop.dashboard.routers.evolve_insights import (
        EvolutionLoopTriggerRequest,
        api_evolution_loop_trigger,
    )

    body = EvolutionLoopTriggerRequest(dry_run=True)
    result = asyncio.run(api_evolution_loop_trigger(_NoopRequest(), body))

    assert result["status"] == "ok"
    report = result["report"]
    assert report["cycle_id"]
    # 真 SQLite 落盘：历史里必须查得到这一轮（隔离数据目录内的同一库文件）
    history = loop.get_cycle_history(limit=5)
    assert any(r.cycle_id == report["cycle_id"] for r in history), (
        "trigger 返回 ok 但报告未落库——持久化链路断裂"
    )


def test_t300_approval_decision_roundtrip(evolution_loop_factory, monkeypatch):
    """审批决策真实持久化：approve → rejected 混合 → partial 收敛。"""
    _stub_admin(monkeypatch)
    loop = evolution_loop_factory()

    from maop.core.evolution.evolution_loop_types import LoopReport
    from maop.dashboard.routers.evolve_insights import (
        EvolutionApprovalDecisionRequest,
        api_evolution_approval_decision,
    )

    report = LoopReport(
        pending_approval=["s-1", "s-2"],
        approval_state="pending",
        suggestions_generated=2,
    )
    loop._save_report(report)  # 种数据：真实 SQLite

    import asyncio

    body_ok = EvolutionApprovalDecisionRequest(
        decision="approve", approved_by="admin", reason="ok"
    )
    result = asyncio.run(
        api_evolution_approval_decision(f"{report.cycle_id}:s-1", _NoopRequest(), body_ok)
    )
    assert result["status"] == "ok"
    assert result["approval_state"] == "pending"  # s-2 还没决策

    body_rej = EvolutionApprovalDecisionRequest(
        decision="reject", approved_by="admin", reason="no"
    )
    result2 = asyncio.run(
        api_evolution_approval_decision(f"{report.cycle_id}:s-2", _NoopRequest(), body_rej)
    )
    assert result2["approval_state"] == "partial"  # 一批准一拒绝

    stored = loop.get_report(report.cycle_id)
    assert stored is not None
    assert stored.approved_suggestions == ["s-1"]
    assert stored.rejected_suggestions == ["s-2"]
    assert stored.pending_approval == []
    assert stored.approved_by == "admin"
    assert stored.approved_at > 0


def test_t300_approval_decision_error_paths(evolution_loop_factory):
    """错误路径契约：404（未知 cycle）/ 400（未知 suggestion、非法 decision/格式）。"""
    from maop.core.evolution.evolution_loop_types import LoopReport
    from maop.dashboard.services import evolution_service

    loop = evolution_loop_factory()
    report = LoopReport(pending_approval=["s-1"], approval_state="pending")
    loop._save_report(report)

    with pytest.raises(KeyError):
        evolution_service.decide_evolution_approval("nope:s-1", "approve", "a", "r")
    with pytest.raises(ValueError):
        evolution_service.decide_evolution_approval(
            f"{report.cycle_id}:zzz", "approve", "a", "r"
        )
    with pytest.raises(ValueError):
        evolution_service.decide_evolution_approval(
            f"{report.cycle_id}:s-1", "maybe", "a", "r"
        )
    with pytest.raises(ValueError):
        evolution_service.decide_evolution_approval("no-colon", "approve", "a", "r")


def test_t300_loop_get_and_update_report(evolution_loop_factory):
    """get_report / update_report 契约：UPDATE 命中、幽灵 id 返回 False 不炸。"""
    from maop.core.evolution.evolution_loop_types import LoopReport

    loop = evolution_loop_factory()
    assert loop.get_report("missing-cycle") is None

    report = LoopReport(pending_approval=["s-1"], approval_state="pending")
    loop._save_report(report)

    report.approval_state = "approved"
    assert loop.update_report(report) is True
    assert loop.get_report(report.cycle_id).approval_state == "approved"

    ghost = LoopReport()  # 从未落库——update 必须返回 False 而非撞主键
    assert loop.update_report(ghost) is False