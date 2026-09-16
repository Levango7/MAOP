"""Recording / monitoring mixin for :class:`Dispatcher`.

Split out from ``dispatch_core.py`` for maintainability (mixin pattern).
Houses the agent-resolution, route-scorer notification, SLA recording,
and agent-performance tracking helpers:

- :meth:`_resolve_agent` — resolve agent config by name
- :meth:`_notify_route_scorer` — notify RouteScorer of success/failure
- :meth:`_record_sla_dispatch_start` — SLA metrics at dispatch start
- :meth:`_record_sla_dispatch_end` — SLA metrics at dispatch completion
- :meth:`_record_agent_performance` — wire AgentPerformanceTracker to main path

These methods access ``self`` attributes (``_resolver``, ``_config``,
``_sla``) provided by the :class:`Dispatcher` host class, so the mixin is
purely a structural split — no logic changes.

The logger name is pinned to ``maop.delegate.dispatcher`` to preserve log
output exactly as before the split.
"""

from __future__ import annotations

import logging
from typing import Any

from maop.delegate.models import AgentConfig

# Pinned to the original module name so log records are identical to the
# pre-split behaviour (tests / dashboards key on this logger name).
logger = logging.getLogger("maop.delegate.dispatcher")


class DispatchRecordingMixin:
    """Agent-resolution / SLA / performance recording helpers (mixin).

    Expects the host class to provide:
        - ``self._resolver``  — AgentResolver instance
        - ``self._config``    — MAOP config object
        - ``self._sla``       — SLAMonitor instance
    """

    def _resolve_agent(self, agent_name: str) -> AgentConfig | None:
        """Resolve agent config by name (delegates to AgentResolver)."""
        return self._resolver.resolve(agent_name)

    def _notify_route_scorer(
        self,
        agent: str,
        *,
        success: bool,
    ) -> None:
        """Notify RouteScorer of agent success/failure for cooldown tracking.

        P0-3 fix: RouteScorer.cooldown was never populated because
        mark_agent_failed/mark_agent_success had no callers. Now invoked
        alongside circuit-breaker recording in _dispatch_impl.
        """
        try:
            from maop.core.routing.route_scorer import get_route_scorer
            scorer = get_route_scorer(self._config)
            if success:
                scorer.mark_agent_success(agent)
            else:
                scorer.mark_agent_failed(agent)
        except Exception as exc:
            # P2-7 fix: upgrade to warning — cooldown mechanism failure
            # affects routing quality and should be visible in logs
            logger.warning("[dispatch] RouteScorer notify failed: %s", exc)

    def _record_sla_dispatch_start(self, priority: int, sla_tier: str) -> None:
        """Record SLA metrics at task dispatch start (delegates to SLAMonitor)."""
        self._sla.record_start(priority, sla_tier)

    def _record_sla_dispatch_end(
        self,
        priority: int,
        sla_tier: str,
        *,
        deadline_ms: int | None,
    ) -> None:
        """Record SLA metrics at task dispatch completion (delegates to SLAMonitor)."""
        self._sla.record_end(priority, sla_tier, deadline_ms=deadline_ms)

    def _record_agent_performance(
        self,
        agent: str,
        routing_key: str,
        dispatch_result: Any,
    ) -> None:
        """C1 fix: 接线 ``AgentPerformanceTracker.record()`` 到主执行路径。

        此前 ``record()`` 仅被 ``sync_from_episodic`` 手动触发，自适应路由与
        演化分析读取的 ``agent_performance`` 表长期为空。此处统一在 dispatch
        结果确定后记录（成功/部分成功/失败均记录）。记录失败仅 debug 日志，
        不阻塞 dispatch。
        """
        try:
            from maop.config.env import get_root_dir
            from maop.core.agent.lifecycle.agent_performance import AgentPerformanceTracker

            # M3 修复：统一使用 get_root_dir() 解析根目录（兼容 MAOP_ROOT_DIR / MAOP_ROOT）
            root = str(get_root_dir(default="."))
            tracker = AgentPerformanceTracker(root_dir=root)
            result = dispatch_result.result if dispatch_result else None
            if result is None:
                return
            exit_code = getattr(result, "exit_code", -1)
            outcome = "success" if exit_code == 0 else ("failure" if exit_code < 0 else "partial")
            tracker.record(
                agent=agent,
                routing_key=routing_key or "",
                outcome=outcome,
                cost_usd=0.0,  # 成本由 cost_tracker 独立记录，避免重复记账
                latency_ms=float(getattr(result, "duration_ms", 0) or 0),
            )
        except Exception as exc:
            logger.debug("[dispatcher] agent performance record failed: %s", exc)