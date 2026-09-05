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

from unittest.mock import patch, MagicMock, AsyncMock

import pytest


def test_ac07_evolution_loop_status(evolution_loop_factory):
    """GET /api/evolution/loop/status 返回状态机状态 + 最近 cycle。"""
    loop = evolution_loop_factory()

    # Mock EvolutionLoop.get_cycle_history - function imports locally from maop.core.evolution.evolution_loop
    with patch("maop.core.evolution.evolution_loop.EvolutionLoop") as mock_loop_class:
        mock_loop = MagicMock()
        mock_loop.get_cycle_history.return_value = []
        mock_loop.get_stats.return_value = {"total_cycles": 0}
        mock_loop_class.return_value = mock_loop

        from maop.dashboard.routers.evolve_insights import api_evolution_loop_status

        import asyncio
        result = asyncio.run(api_evolution_loop_status())

        assert result["status"] == "ok"
        assert "state" in result
        assert "evolution_loop_enabled" in result
        assert "recent_cycles" in result
        assert "pending_approval_count" in result


def test_ac07_evolution_loop_trigger(evolution_loop_factory):
    """POST /api/evolution/loop/trigger 触发闭环。"""
    from unittest.mock import AsyncMock

    with patch("maop.core.evolution.evolution_loop.EvolutionLoop") as mock_loop_class, \
         patch("maop.dashboard.routers.evolve_insights.require_admin", return_value=None):

        mock_loop = MagicMock()
        async def mock_run_cycle(dry_run=True, auto_rollback=True):
            report = MagicMock()
            report.model_dump.return_value = {"cycle_id": "test-001", "errors_observed": 0, "suggestions_applied": 0}
            return report
        mock_loop.run_cycle = mock_run_cycle
        mock_loop_class.return_value = mock_loop

        from maop.dashboard.routers.evolve_insights import api_evolution_loop_trigger

        import asyncio
        mock_request = MagicMock()
        mock_request.json = AsyncMock(return_value={"dry_run": True})

        result = asyncio.run(api_evolution_loop_trigger(mock_request))

        assert result["status"] == "ok"
        assert "report" in result


def test_ac07_evolution_approvals(evolution_loop_factory):
    """GET /api/evolution/approvals 返回待审批列表。"""
    with patch("maop.core.evolution.evolution_loop.EvolutionLoop") as mock_loop_class:

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

        from maop.dashboard.routers.evolve_insights import api_evolution_approvals

        import asyncio
        result = asyncio.run(api_evolution_approvals())

        assert result["status"] == "ok"
        assert "approvals" in result
        assert len(result["approvals"]) == 1
        assert result["approvals"][0]["cycle_id"] == "cycle-001"


def test_ac07_evolution_approval_decision(evolution_loop_factory):
    """POST /api/evolution/approvals/{id}/decision 审批通过/拒绝。"""
    from unittest.mock import AsyncMock

    with patch("maop.core.evolution.evolution_loop.EvolutionLoop") as mock_loop_class, \
         patch("maop.dashboard.routers.evolve_insights.require_admin", return_value=None):

        mock_loop = MagicMock()
        mock_report = MagicMock()
        mock_loop._load_report.return_value = mock_report
        mock_loop._save_report = MagicMock()
        mock_loop_class.return_value = mock_loop

        from maop.dashboard.routers.evolve_insights import api_evolution_approval_decision

        import asyncio
        mock_request = MagicMock()
        mock_request.json = AsyncMock(return_value={
            "decision": "approve",
            "approved_by": "admin",
            "reason": "looks good"
        })

        result = asyncio.run(api_evolution_approval_decision("cycle-001:pending-001", mock_request))

        assert result["status"] == "ok"
        assert result["decision"] == "approve"
        assert result["approval_id"] == "cycle-001:pending-001"


def test_ac07_evolution_ab_results(evolution_loop_factory):
    """GET /api/evolution/ab/{cycle_id} 返回 A/B 结果。"""
    with patch("maop.core.evolution.evolution_loop.EvolutionLoop"), \
         patch("maop.core.evolution.ab_test.ABTestManager") as mock_ab_class:

        mock_ab = MagicMock()
        mock_ab.evaluate.return_value = MagicMock(
            p_value=0.03,
            control_rate=0.10,
            treatment_rate=0.12,
            control_count=1000,
            treatment_count=1000,
        )
        mock_ab_class.return_value = mock_ab

        from maop.dashboard.routers.evolve_insights import api_evolution_ab_results

        import asyncio
        result = asyncio.run(api_evolution_ab_results("cycle-001"))

        assert result["status"] == "ok"
        assert result["cycle_id"] == "cycle-001"
        assert result["ab_result"] is not None
        assert result["ab_result"]["p_value"] == 0.03


def test_ac07_evolution_loop_rollback(evolution_loop_factory):
    """POST /api/evolution/loop/rollback 手动触发回滚。"""
    from unittest.mock import AsyncMock

    with patch("maop.core.evolution.evolution_loop.EvolutionLoop") as mock_loop_class, \
         patch("maop.dashboard.routers.evolve_insights.require_admin", return_value=None):

        mock_loop = MagicMock()
        mock_loop.rollback_cycle.return_value = 5
        mock_loop_class.return_value = mock_loop

        from maop.dashboard.routers.evolve_insights import api_evolution_loop_rollback

        import asyncio
        mock_request = MagicMock()
        mock_request.json = AsyncMock(return_value={
            "cycle_id": "cycle-001",
            "snapshot_id": "snap-001"
        })

        result = asyncio.run(api_evolution_loop_rollback(mock_request))

        assert result["status"] == "ok"
        assert result["restored_files"] == 5
        assert result["cycle_id"] == "cycle-001"