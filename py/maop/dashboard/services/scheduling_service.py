"""Scheduling & Supervisor 域 Service 层.

框架无关（不导入 FastAPI），函数接收显式参数，返回普通 dict/object，
不抛 HTTPException。可独立单元测试，无需 HTTP 上下文。

本模块从以下 2 个 router 提取业务逻辑：

- ``scheduling``  — F1-02 异常自适应调度失败检测器状态查询/重置
- ``supervisor``  — 主动多 Agent 监管的 patrol / 控制动作

每个子域对应一组 ``_get_xxx`` 单例访问器 + 业务函数。单例使用
双重检查锁定保护，与原 router 风格一致；``_set_xxx`` 供测试注入。

注意：
  - ``FailurePatternDetector`` 是进程级单例（``get_failure_detector``），
    本 service 仅做透传，不持有独立全局变量。
  - ``Supervisor`` 同样是进程级单例（``get_supervisor``），可能为 None
    （passive-only 模式）；service 层返回 None 由 router 决定 404。
"""

from __future__ import annotations

import logging
import threading  # noqa: F401
import time
from typing import Any

logger = logging.getLogger(__name__)


# ══════════════════════════════════════════════════════════════════
# §1  Scheduling — 失败检测器状态
# ══════════════════════════════════════════════════════════════════


def _get_detector() -> Any:
    """返回进程级 ``FailurePatternDetector`` 单例.

    透传到 ``maop.core.scheduling.failure_detector.get_failure_detector``，
    保持与原 router ``_detector()`` 完全一致的行为。该单例由 core 层
    自行管理（双重检查锁定 + module-level global），本 service 不重复
    持有，避免双源真相。

    Imported lazily so the service module is import-safe even when the
    scheduling subsystem has not been initialised (e.g. personal edition
    fallback to in-process execution).
    """
    from maop.core.scheduling.failure_detector import get_failure_detector

    return get_failure_detector()


def get_failure_stats() -> dict[str, Any]:
    """返回 per-agent 失败检测器快照.

    Returns
    -------
    dict
        ``FailurePatternDetector.get_stats()`` 的原始返回值，结构为::

            {
              "agents": [...],
              "config": {...},
              "total_agents": int
            }
    """
    return _get_detector().get_stats()


def reset_failure_stats(agent_id: str | None) -> None:
    """重置失败检测器状态.

    Parameters
    ----------
    agent_id : str | None
        指定 agent_id 则只重置该 agent；None 则重置全部。
    """
    _get_detector().reset(agent_id)


# ══════════════════════════════════════════════════════════════════
# §2  Supervisor — 主动多 Agent 监管
# ══════════════════════════════════════════════════════════════════


def _get_supervisor() -> Any:
    """返回进程级 ``Supervisor`` 单例（可能为 None）.

    透传到 ``maop.core.scheduling.failure_detector.get_supervisor``。
    返回 None 表示 passive-only 模式（未配置 Supervisor），由 router
    决定是否抛 404。
    """
    from maop.core.scheduling.failure_detector import get_supervisor

    return get_supervisor()


def get_supervisor_status() -> dict[str, Any]:
    """返回完整的 supervisor 状态快照.

    Raises
    ------
    RuntimeError
        当 Supervisor 未配置（None）。由 router 转为 HTTPException 404。
    """
    sup = _get_supervisor()
    if sup is None:
        raise RuntimeError("Supervisor not configured (passive-only mode)")
    return sup.get_supervisor_status()


def list_supervisor_rules() -> list[Any]:
    """返回当前监管规则集合（``SupervisorRule`` 列表）.

    Raises
    ------
    RuntimeError
        当 Supervisor 未配置。
    """
    sup = _get_supervisor()
    if sup is None:
        raise RuntimeError("Supervisor not configured (passive-only mode)")
    return list(sup.rules)


def update_supervisor_rules(new_rules: list[Any]) -> int:
    """热更新监管规则集合，返回新规则数.

    Parameters
    ----------
    new_rules : list[SupervisorRule]
        已由 router 通过 Pydantic 校验过的 ``SupervisorRule`` 列表。

    Raises
    ------
    RuntimeError
        当 Supervisor 未配置。
    """
    sup = _get_supervisor()
    if sup is None:
        raise RuntimeError("Supervisor not configured (passive-only mode)")
    sup.set_rules(new_rules)
    return len(new_rules)


def get_supervisor_actions(*, agent_id: str | None, limit: int) -> list[Any]:
    """返回控制动作历史（可选按 agent 过滤）.

    Raises
    ------
    RuntimeError
        当 Supervisor 未配置。
    """
    sup = _get_supervisor()
    if sup is None:
        raise RuntimeError("Supervisor not configured (passive-only mode)")
    return sup.get_actions(agent_id=agent_id, limit=limit)


async def supervisor_patrol() -> tuple[list[Any], float]:
    """手动触发一轮 patrol，返回 (probes, duration_s).

    在 service 层测量 patrol 持续时间，避免访问 sup 的内部属性
    ``_last_patrol_duration_s``。

    Raises
    ------
    RuntimeError
        当 Supervisor 未配置。
    """
    sup = _get_supervisor()
    if sup is None:
        raise RuntimeError("Supervisor not configured (passive-only mode)")
    _start = time.monotonic()
    probes = await sup.patrol()
    _duration_s = time.monotonic() - _start
    return list(probes), _duration_s


async def supervisor_action(
    *,
    agent_id: str,
    action: str,
    params: dict[str, Any],
    reason: str | None,
) -> dict[str, Any]:
    """手动执行一个控制动作.

    Parameters
    ----------
    agent_id : str
        目标 agent ID。
    action : str
        动作类型：alert / replace / degrade / terminate / upgrade。
    params : dict
        动作特定参数（如 ``{"factor": 0.5}`` / ``{"replacement": "..."}``）。
    reason : str | None
        触发原因；None 则自动生成 ``"manual <action> via API"``。

    Returns
    -------
    dict
        ``{"action": str, "agent_id": str, ...}``，包含 action_id（除 alert 外）。

    Raises
    ------
    RuntimeError
        当 Supervisor 未配置。
    ValueError
        当 action 未知，或必需参数缺失（如 replace 缺 replacement、
        upgrade 缺 target_version）。由 router 转为 HTTPException 400。
    """
    from maop.core.scheduling.supervisor import AlertLevel

    sup = _get_supervisor()
    if sup is None:
        raise RuntimeError("Supervisor not configured (passive-only mode)")

    action_str = action.strip().lower()
    actual_reason = reason or f"manual {action_str} via API"
    triggered_by = "manual"

    if action_str == "alert":
        level = AlertLevel(params.get("level", "warning"))
        await sup.warn(
            agent_id, reason=actual_reason, level=level,
            extra=params.get("extra"),
        )
        return {"action": "alert", "agent_id": agent_id}

    if action_str == "replace":
        replacement = params.get("replacement")
        if not replacement:
            raise ValueError("replace requires params.replacement")
        record = await sup.replace(
            agent_id, str(replacement), reason=actual_reason,
            routing_key=str(params.get("routing_key", "")),
            triggered_by=triggered_by,
        )
        return {"action": record.action.value, "action_id": record.action_id}

    if action_str == "degrade":
        factor = float(params.get("factor", 0.5))
        record = await sup.degrade(
            agent_id, factor=factor, reason=actual_reason,
            max_concurrency=params.get("max_concurrency"),
            timeout_s=params.get("timeout_s"),
            triggered_by=triggered_by,
        )
        return {"action": record.action.value, "action_id": record.action_id}

    if action_str == "terminate":
        force = bool(params.get("force", False))
        record = await sup.terminate(
            agent_id, reason=actual_reason,
            triggered_by=triggered_by, force=force,
        )
        return {"action": record.action.value, "action_id": record.action_id}

    if action_str == "upgrade":
        target_version = params.get("target_version")
        if not target_version:
            raise ValueError("upgrade requires params.target_version")
        record = await sup.upgrade(
            agent_id, str(target_version), reason=actual_reason,
            triggered_by=triggered_by,
        )
        return {"action": record.action.value, "action_id": record.action_id}

    raise ValueError(
        f"unknown action {action_str!r}; expected one of "
        f"alert/replace/degrade/terminate/upgrade"
    )