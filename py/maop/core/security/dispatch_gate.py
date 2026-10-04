"""派发前的统一安全门：权限规则 + ``agent.pre_dispatch`` 钩子。

## 为什么要有这个模块

这套检查原先只写在 ``maop/maop_execute.py::_check_permission_and_hooks`` 里，
而 ``maop_execute()`` **全仓零生产调用方** —— 三个真实入口（CLI ``run``、
dashboard DAG、chat 回退）全都直接走 ``Dispatcher.dispatch``。于是
``PermissionManager`` 的 fail-closed 判断、``ask`` 转人工、以及 ``pre_dispatch``
钩子否决，在生产路径上一次都没有执行过：``maop run`` 可以派发任意 agent 而没有任何
RBAC。这是一个"函数是对的、但接线在没人走的那条路上"的典型。

现在检查点落在**唯一漏斗** ``Dispatcher.dispatch()`` 上，所以三个入口自动共享同一条门；
``maop_execute`` 也改成调用本模块，避免安全逻辑出现第二份拷贝（本仓有过"同一 bug
只修了一份拷贝"的先例）。

## 默认不改变行为（照 `MAOP_DRY_RUN_ENFORCE` 的范式）

``PermissionManager.check()`` 在**没有匹配规则**时返回 ``decision="ask"``
（挂起待人工批准 ⇒ 派发被拒，见 ``core/security/permission.py`` 的默认返回）。
也就是说直接把门接上并默认启用，会把开箱即用（个人版、没配过规则）的**所有**派发
拒掉，等于把产品打死。

所以门默认**挂载但不生效**：只有显式设 ``MAOP_PERMISSION_ENFORCE=1`` 才启用。
启用后语义与 ``maop_execute`` 原来的完全一致（deny → 拒、ask → 建人工审批并拒、
异常 → fail-closed 拒），不新增也不放宽任何判定。

⚠️ 启用前置条件：先给要用的 agent 加 allow 规则（``POST /api/rbac/rules`` 或
``PermissionManager.add_rule``），否则第一批派发会全部被 ask 拒掉。
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from maop.core.reliability.error_schema import MaopResult, new_result

logger = logging.getLogger(__name__)

#: 权限拒绝 / 待批准 / 检查失败统一用这个退出码（沿用 maop_execute 的历史取值）。
PERMISSION_DENIED_EXIT_CODE = 126


def _project_root() -> Path:
    """仓库根（``py/maop/core/security/dispatch_gate.py`` → 上溯 4 层）。"""
    return Path(__file__).resolve().parents[3]


def permission_enforced() -> bool:
    """当前是否启用派发权限门（``MAOP_PERMISSION_ENFORCE``，默认关）。"""
    from maop.config.settings import get_settings

    return bool(get_settings().permission_enforce)


def _denied(agent: str, task: str, routing_key: str, trace_id: str, error: str) -> MaopResult:
    return new_result(
        agent=agent, task=task,
        exit_code=PERMISSION_DENIED_EXIT_CODE,
        error=error,
        trace_id=trace_id, routing_key=routing_key,
    )


async def check_dispatch_gate(
    *,
    agent: str,
    task: str,
    routing_key: str = "",
    trace_id: str = "",
    permission_manager: Any = None,
) -> MaopResult | None:
    """派发前的安全门。

    Returns
    -------
    MaopResult | None
        ``None`` = 放行；否则是应当直接返回给调用方的拒绝结果。

    门关闭时（默认）**立即返回 None**，不做任何检查 —— 这是"挂载但不改变行为"的
    实现方式，也是本项目对未就绪门禁的既有范式（见 ``MAOP_DRY_RUN_ENFORCE``）。
    """
    if not permission_enforced():
        return None

    # 1) 权限规则：deny 直接拒；ask 建人工审批请求并按拒处理（fail-closed）；
    #    任何异常同样 fail-closed —— 安全检查崩溃绝不能等于放行。
    try:
        from maop.core.security.permission import PermissionManager

        pm = permission_manager if permission_manager is not None else PermissionManager(
            root_dir=str(_project_root())
        )
        perm = pm.check(agent=agent, action=routing_key or "execute")
        if perm.decision == "deny":
            reason = perm.reason or f"rule={perm.matched_rule}"
            logger.warning("[dispatch-gate] DENY agent=%s action=%s (%s)", agent, routing_key, reason)
            return _denied(agent, task, routing_key, trace_id, f"Permission denied: {reason}")
        if perm.decision == "ask":
            from maop.core.agent.delegation.human_proxy import HumanProxy

            req_id = HumanProxy(root_dir=str(_project_root())).request(
                task=task, agent=agent, priority="high",
                reason=f"Permission check: agent={agent} action={routing_key or 'execute'}",
                metadata={"routing_key": routing_key, "trace_id": trace_id},
            )
            logger.warning(
                "[dispatch-gate] ASK agent=%s action=%s → 人工审批 %s 挂起，先按拒绝处理",
                agent, routing_key, req_id,
            )
            return _denied(
                agent, task, routing_key, trace_id,
                f"Permission pending human approval (request={req_id}): "
                f"{perm.reason or 'agent=' + agent}",
            )
    except Exception as exc:
        logger.error("[dispatch-gate] 权限检查异常（fail-closed）：%s", exc)
        return _denied(agent, task, routing_key, trace_id, f"Permission check failed: {exc}")

    # 2) pre_dispatch 钩子：任一钩子 decision="deny" 即否决整次派发。
    try:
        from maop.core.agent.plugins_hooks.hook_manager import LifecycleEvent, get_hook_manager

        results = await get_hook_manager().trigger(LifecycleEvent.AGENT_PRE_DISPATCH, {
            "agent": agent, "task": task, "routing_key": routing_key, "trace_id": trace_id,
        })
        for hr in results:
            if hr.decision == "deny":
                logger.warning("[dispatch-gate] 钩子否决 agent=%s hook=%s", agent, hr.hook_id)
                return _denied(
                    agent, task, routing_key, trace_id,
                    f"Hook vetoed dispatch: hook={hr.hook_id} reason={hr.error or 'denied'}",
                )
    except Exception as exc:
        logger.error("[dispatch-gate] pre_dispatch 钩子异常（fail-closed）：%s", exc)
        return _denied(agent, task, routing_key, trace_id, f"Hook pre_dispatch error (fail-closed): {exc}")

    return None
