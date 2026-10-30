"""Reconcile 状态机（纯函数）—— Pending → Running → 终态，phase 集合与 CRD 对齐.

phase 枚举与 ``deploy/k8s/operator/crds/maoptask.yaml`` 的 status.phase enum
一致（Pending/Running/Succeeded/Failed/Cancelled/TimedOut/QuotaExceeded）。
M0 只驱动其中四个：Pending（观测到未启动）→ Running（已拉起）→
Succeeded/Failed（子进程退出码）；TimedOut 由 runner 超时产生；Cancelled
由任务取消产生；QuotaExceeded 预留给配额里程碑。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

#: 与 CRD status.phase enum 一致的合法相位集合。
PHASES = frozenset(
    {"Pending", "Running", "Succeeded", "Failed", "Cancelled", "TimedOut", "QuotaExceeded"}
)

#: M0 会驱动的相位。
M0_PHASES = frozenset({"Pending", "Running", "Succeeded", "Failed", "TimedOut", "Cancelled"})

TERMINAL_PHASES = frozenset({"Succeeded", "Failed", "Cancelled", "TimedOut", "QuotaExceeded"})


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True)
class Transition:
    """一次 status 子资源补丁：``phase`` + 条件 + 时间戳。"""

    phase: str
    patch: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.phase not in PHASES:
            raise ValueError(f"unknown phase {self.phase!r} (allowed: {sorted(PHASES)})")


def _condition(cond_type: str, reason: str, message: str, when: str) -> dict[str, Any]:
    return {
        "type": cond_type,
        "status": "True",
        "reason": reason,
        "message": message,
        "lastTransitionTime": when,
    }


def plan_launch(*, when: str | None = None) -> Transition:
    """观测到未启动的 MaopTask：进入 Running 并记录 startTime."""
    when = when or now_iso()
    return Transition(
        phase="Running",
        patch={
            "phase": "Running",
            "startTime": when,
            "conditions": [_condition("Launched", "SubprocessStarted", "maop run subprocess started", when)],
        },
    )


def plan_finish(
    *,
    exit_code: int | None = None,
    timed_out: bool = False,
    cancelled: bool = False,
    invalid: str | None = None,
    when: str | None = None,
) -> Transition:
    """子进程结束后的终态转换.

    - 退出码 0 → Succeeded；非 0 → Failed（message 携带退出码）；
    - runner 超时 → TimedOut；任务被取消 → Cancelled；
    - spec 校验失败（``invalid`` 携带原因）→ Failed（reason=InvalidSpec，
      **不经过子进程**）。
    """
    when = when or now_iso()
    if invalid:
        return Transition(
            phase="Failed",
            patch={
                "phase": "Failed",
                "completionTime": when,
                "conditions": [_condition("Completed", "InvalidSpec", invalid, when)],
            },
        )
    if cancelled:
        return Transition(
            phase="Cancelled",
            patch={
                "phase": "Cancelled",
                "completionTime": when,
                "conditions": [_condition("Completed", "Cancelled", "task cancelled", when)],
            },
        )
    if timed_out:
        return Transition(
            phase="TimedOut",
            patch={
                "phase": "TimedOut",
                "completionTime": when,
                "conditions": [_condition("Completed", "TimedOut", "subprocess exceeded its time budget", when)],
            },
        )
    code = int(exit_code if exit_code is not None else -1)
    if code == 0:
        return Transition(
            phase="Succeeded",
            patch={
                "phase": "Succeeded",
                "completionTime": when,
                "conditions": [_condition("Completed", "ExitZero", "maop run finished successfully", when)],
            },
        )
    return Transition(
        phase="Failed",
        patch={
            "phase": "Failed",
            "completionTime": when,
            "conditions": [
                _condition("Completed", "ExitNonZero", f"maop run exited with code {code}", when)
            ],
        },
    )


def is_terminal(phase: str | None) -> bool:
    return phase in TERMINAL_PHASES
