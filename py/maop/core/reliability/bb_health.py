"""MAOP Blackboard — Health Monitor & Prometheus-style Metrics Endpoint.

Provides operational visibility for the M3 blackboard stack:

* ``BlackboardHealthMonitor``: tracks solve cycles, convergence rate,
  conflict count, debate stats.
* ``metrics_snapshot()``: returns a dict suitable for Prometheus text
  exposition or a ``/metrics`` HTTP endpoint.

Classes
-------
- ``BlackboardHealthMonitor``: accumulates operational counters.
- ``HealthProbes``: bundles sub-component liveness/readiness probes.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


# ── 进程级单例（2026-11-10 接线审计）─────────────────────────────
# 此前 monitor 只被 dashboard service 自己 new 了一个（_health_monitor），
# 而 record_ks_dispatch / record_dead_letter 的**真实调用点在 core 层**
# （黑板的 KS 派发与消息队列的死信）——两边各持一份实例的话，core 记的数永远不会
# 出现在 dashboard 的 snapshot 里。给一个进程级访问器，两边共用同一份。
#
# 模式与 core/scheduling/failure_detector.get_failure_detector() 一致。
_monitor_instance: BlackboardHealthMonitor | None = None
_monitor_lock = threading.Lock()


def get_health_monitor() -> BlackboardHealthMonitor:
    """Return the process-wide :class:`BlackboardHealthMonitor` singleton."""
    global _monitor_instance
    with _monitor_lock:
        if _monitor_instance is None:
            _monitor_instance = BlackboardHealthMonitor()
        return _monitor_instance


def set_health_monitor(monitor: BlackboardHealthMonitor | None) -> None:
    """Replace (or clear with ``None``) the global monitor singleton (tests)."""
    global _monitor_instance
    with _monitor_lock:
        _monitor_instance = monitor


def notify_dead_letter(n: int = 1) -> None:
    """上报 ``n`` 条消息进入死信（2026-11-10 接线审计）。

    ``record_dead_letter`` 此前实现完整但**零调用**，健康面 ``dead_letters``
    恒为 0 —— 死信在积压却看不出来。真实调用点在 ``message_queue`` 的三处入死信
    分支，那里用本函数上报。

    记账失败绝不阻断入死信本身（死信是数据安全网，不能被观测性拖垮）。
    """
    try:
        get_health_monitor().record_dead_letter(n)
    except (OSError, RuntimeError, ValueError, TypeError) as exc:
        logger.debug("[bb_health] dead-letter record skipped: %s", exc)


# ── BlackboardHealthMonitor ────────────────────────────────────────


@dataclass
class BlackboardHealthMonitor:
    """Accumulates operational counters for the blackboard stack.

    Usage::

        monitor = BlackboardHealthMonitor()
        # After each solve:
        monitor.record_solve(report)
        # Periodically:
        snapshot = monitor.snapshot()
    """

    solves_initiated: int = 0
    solves_converged: int = 0
    solves_max_iter: int = 0
    solves_error: int = 0
    total_iterations: int = 0
    ks_dispatches: int = 0
    ks_errors: int = 0
    debate_rounds: int = 0
    debates_converged: int = 0
    conflicts_detected: int = 0
    conflicts_resolved: int = 0
    dead_letters: int = 0

    _solve_durations: list[float] = field(default_factory=list)
    _max_durations: int = 100

    def record_solve(self, report: dict[str, Any]) -> None:
        """Record a solve completion report from ``BlackboardConvergenceController.run_until_convergence``."""
        self.solves_initiated += 1
        if report.get("converged"):
            self.solves_converged += 1
        elif report.get("reason") == "max_iterations":
            self.solves_max_iter += 1
        self.total_iterations += report.get("iterations", 0)
        dur = report.get("duration_ms")
        if dur is not None:
            self._solve_durations.append(dur)
            if len(self._solve_durations) > self._max_durations:
                self._solve_durations = self._solve_durations[-self._max_durations:]

    def record_ks_dispatch(self, success: bool = True) -> None:
        self.ks_dispatches += 1
        if not success:
            self.ks_errors += 1

    def record_debate(self, converged: bool, rounds: int) -> None:
        self.debate_rounds += rounds
        if converged:
            self.debates_converged += 1

    def record_conflict(self, resolved: bool = False) -> None:
        self.conflicts_detected += 1
        if resolved:
            self.conflicts_resolved += 1

    def record_dead_letter(self, n: int = 1) -> None:
        self.dead_letters += n

    def snapshot(self) -> dict[str, Any]:
        """Return a full snapshot dict for metrics exposition."""
        durations = self._solve_durations
        return {
            "solves": {
                "initiated": self.solves_initiated,
                "converged": self.solves_converged,
                "max_iter": self.solves_max_iter,
                "error": self.solves_error,
                "convergence_rate": (
                    self.solves_converged / self.solves_initiated
                    if self.solves_initiated > 0
                    else 0.0
                ),
            },
            "dispatches": {
                "total": self.ks_dispatches,
                "errors": self.ks_errors,
                "error_rate": (
                    self.ks_errors / self.ks_dispatches
                    if self.ks_dispatches > 0
                    else 0.0
                ),
            },
            "debates": {
                "rounds": self.debate_rounds,
                "converged": self.debates_converged,
            },
            "conflicts": {
                "detected": self.conflicts_detected,
                "resolved": self.conflicts_resolved,
            },
            "dead_letters": self.dead_letters,
            "solve_duration_ms": {
                "count": len(durations),
                "avg": sum(durations) / len(durations) if durations else 0.0,
                "min": min(durations) if durations else 0.0,
                "max": max(durations) if durations else 0.0,
                "p99": self._percentile(durations, 99) if durations else 0.0,
            },
        }

    @staticmethod
    def _percentile(data: list[float], pct: int) -> float:
        if not data:
            return 0.0
        s = sorted(data)
        k = (len(s) - 1) * (pct / 100.0)
        f = int(k)
        c = f + 1 if f + 1 < len(s) else f
        d = k - f
        return s[f] + d * (s[c] - s[f])


# ── HealthProbes ───────────────────────────────────────────────────


class HealthProbes:
    """Liveness/readiness probes for MAOP blackboard stack.

    Suitable for Kubernetes livenessProbe / readinessProbe endpoints.
    """

    def __init__(self) -> None:
        self._checks: dict[str, Callable[[], bool]] = {}
        self._last_result: dict[str, bool] = {}

    def register(self, name: str, check: Callable[[], bool]) -> None:
        """Register a probe. ``check() -> bool``."""
        self._checks[name] = check

    def liveness(self) -> bool:
        """Quick liveness check — is the process alive?"""
        return True

    def readiness(self) -> dict[str, Any]:
        """Full readiness — are all subsystems functioning?"""
        all_ok = True
        results: dict[str, bool] = {}
        for name, check in self._checks.items():
            try:
                ok = bool(check())
            except Exception:
                ok = False
            if not ok:
                all_ok = False
            results[name] = ok
        self._last_result = results
        return {
            "ready": all_ok,
            "checks": results,
        }
