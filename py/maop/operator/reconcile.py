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


def plan_launch(
    *,
    when: str | None = None,
    agent: str = "",
    agent_resolved: bool | None = None,
) -> Transition:
    """观测到未启动的 MaopTask：进入 Running 并记录 startTime.

    M1：``agent`` 与 ``agent_resolved`` 提供时追加 ``AgentResolved`` 条件 ——
    True=MaopAgent CR 存在（命令带 ``--agent`` pin）；False=未声明（按 M0
    行为不 pin）。两参缺省时不追加条件，输出与 M0 保持一致。
    """
    when = when or now_iso()
    conditions = [
        _condition("Launched", "SubprocessStarted", "maop run subprocess started", when)
    ]
    if agent and agent_resolved is not None:
        if agent_resolved:
            conditions.append(
                _condition(
                    "AgentResolved",
                    "MaopAgentFound",
                    f"MaopAgent {agent!r} resolved; executing agent pinned",
                    when,
                )
            )
        else:
            conditions.append(
                _condition(
                    "AgentResolved",
                    "MaopAgentNotDeclared",
                    f"MaopAgent {agent!r} not found; running without pinning",
                    when,
                )
            )
    return Transition(
        phase="Running",
        patch={"phase": "Running", "startTime": when, "conditions": conditions},
    )


def plan_quota_exceeded(
    *,
    tenant: str,
    reason: str,
    when: str | None = None,
) -> Transition:
    """配额门拒绝（M3.2）：终态 ``QuotaExceeded``（CRD enum 预留相位）.

    在子进程拉起**之前**补丁——任务未执行；``reason`` 由注入的配额门给出
    （如 ``tenant quota exceeded: turns``）。未注入配额门时本转换不会被调用。
    """
    when = when or now_iso()
    detail = f"tenant {tenant!r} quota check denied: {reason}"
    return Transition(
        phase="QuotaExceeded",
        patch={
            "phase": "QuotaExceeded",
            "completionTime": when,
            "conditions": [_condition("Completed", "QuotaExceeded", detail, when)],
        },
    )


def _with_usage(
    t: Transition,
    turns: int | None,
    tokens_used: int | None = None,
    tokens_source: str | None = None,
) -> Transition:
    """M2/M3.3：终态补丁附加用量会计字段（仅在观测到值时）。

    ``turns`` → ``status.turns``（M2）；``tokens_used`` / ``tokens_source``
    → ``status.tokensUsed`` / ``status.tokensSource``（M3.3；source 区分
    measured/estimated，CLI 驱动不产 token 时不冒充）。
    """
    patch = dict(t.patch)
    if turns is not None:
        patch["turns"] = int(turns)
    if tokens_used is not None:
        patch["tokensUsed"] = int(tokens_used)
    if tokens_source is not None:
        patch["tokensSource"] = str(tokens_source)
    if patch == t.patch:
        return t
    return Transition(phase=t.phase, patch=patch)


def plan_finish(
    *,
    exit_code: int | None = None,
    timed_out: bool = False,
    cancelled: bool = False,
    invalid: str | None = None,
    turns: int | None = None,
    tokens_used: int | None = None,
    tokens_source: str | None = None,
    when: str | None = None,
) -> Transition:
    """子进程结束后的终态转换.

    - 退出码 0 → Succeeded；非 0 → Failed（message 携带退出码）；
    - runner 超时 → TimedOut；任务被取消 → Cancelled；
    - spec 校验失败（``invalid`` 携带原因）→ Failed（reason=InvalidSpec，
      **不经过子进程**）；
    - M2：``turns`` 为观测到的编排 turn 消耗数（operator 从 ``[USAGE]
      turns=N`` 协议行解析），有值时写入终态补丁的 ``status.turns``。
    - M3.3：``tokens_used`` / ``tokens_source`` 为实测 token 与来源
      （measured/estimated，operator 从 ``tokens=M source=S`` 段解析），
      有值时写入 ``status.tokensUsed`` / ``status.tokensSource``。
    """
    when = when or now_iso()
    if invalid:
        return _with_usage(
            Transition(
                phase="Failed",
                patch={
                    "phase": "Failed",
                    "completionTime": when,
                    "conditions": [_condition("Completed", "InvalidSpec", invalid, when)],
                },
            ),
            turns,
            tokens_used,
            tokens_source,
        )
    if cancelled:
        return _with_usage(
            Transition(
                phase="Cancelled",
                patch={
                    "phase": "Cancelled",
                    "completionTime": when,
                    "conditions": [_condition("Completed", "Cancelled", "task cancelled", when)],
                },
            ),
            turns,
            tokens_used,
            tokens_source,
        )
    if timed_out:
        return _with_usage(
            Transition(
                phase="TimedOut",
                patch={
                    "phase": "TimedOut",
                    "completionTime": when,
                    "conditions": [_condition("Completed", "TimedOut", "subprocess exceeded its time budget", when)],
                },
            ),
            turns,
            tokens_used,
            tokens_source,
        )
    code = int(exit_code if exit_code is not None else -1)
    if code == 0:
        return _with_usage(
            Transition(
                phase="Succeeded",
                patch={
                    "phase": "Succeeded",
                    "completionTime": when,
                    "conditions": [_condition("Completed", "ExitZero", "maop run finished successfully", when)],
                },
            ),
            turns,
            tokens_used,
            tokens_source,
        )
    return _with_usage(
        Transition(
            phase="Failed",
            patch={
                "phase": "Failed",
                "completionTime": when,
                "conditions": [
                    _condition("Completed", "ExitNonZero", f"maop run exited with code {code}", when)
                ],
            },
        ),
        turns,
        tokens_used,
        tokens_source,
    )


def is_terminal(phase: str | None) -> bool:
    return phase in TERMINAL_PHASES
