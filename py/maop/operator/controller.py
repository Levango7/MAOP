"""OperatorController —— M0 的 mock 可测核心.

流程（单个 MaopTask 事件）：
1. ``spec.parse_task_spec`` 校验 —— 失败直接补丁 Failed（reason=InvalidSpec），
   **不拉起**子进程；
2. 终态（status.phase 已是 terminal）→ no-op（k8s 事件重放幂等）；
3. 否则补丁 Running（``plan_launch``）→ runner 执行 ``command_for(spec)`` →
   按结果补丁终态（``plan_finish``）；任务被取消 → Cancelled 后原样上抛。

gateway/runner 全部构造注入 —— 测试用 fake 驱动全链路，不需要 k8s。
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from maop.operator.gateway import RunOutcome, StatusGateway, SubprocessRunner
from maop.operator.reconcile import is_terminal, plan_finish, plan_launch
from maop.operator.spec import InvalidSpecError, command_for, parse_task_spec

logger = logging.getLogger(__name__)


class OperatorController:
    """MaopTask 的协调器（gateway/runner 注入；无 k8s 依赖可测）。"""

    def __init__(
        self,
        *,
        gateway: StatusGateway,
        runner: SubprocessRunner,
        timeout_s: float | None = None,
    ) -> None:
        self._gateway = gateway
        self._runner = runner
        self._timeout_s = timeout_s

    async def handle_task(
        self,
        *,
        name: str,
        namespace: str = "default",
        body: dict[str, Any],
        status_phase: str | None = None,
    ) -> dict[str, Any]:
        """处理一个 MaopTask 事件，返回 ``{"phase": <最终相位>, "launched": bool}``."""
        # 1. 终态幂等：k8s 事件可能重放，已到终态的任务不再处理。
        if is_terminal(status_phase):
            logger.debug("[operator] %s/%s already %s — noop", namespace, name, status_phase)
            return {"phase": status_phase or "", "launched": False}

        # 2. spec 校验：失败 → Failed（不拉起）。
        try:
            spec = parse_task_spec(body, name=name, namespace=namespace)
        except InvalidSpecError as exc:
            logger.warning("[operator] %s/%s invalid spec: %s", namespace, name, exc)
            await self._patch(name, namespace, plan_finish(invalid=str(exc)))
            return {"phase": "Failed", "launched": False, "reason": "InvalidSpec"}

        # 3. Running + 拉起。
        await self._patch(name, namespace, plan_launch())
        logger.info("[operator] %s/%s launching: %s", namespace, name, spec.prompt[:80])

        try:
            outcome = await self._runner.run(command_for(spec), timeout_s=self._timeout_s)
        except asyncio.CancelledError:
            await self._patch(name, namespace, plan_finish(cancelled=True))
            raise
        return await self._finalize(name, namespace, outcome)

    async def _finalize(
        self, name: str, namespace: str, outcome: RunOutcome
    ) -> dict[str, Any]:
        finish = plan_finish(
            exit_code=outcome.exit_code,
            timed_out=outcome.timed_out,
            cancelled=outcome.cancelled,
        )
        await self._patch(name, namespace, finish)
        logger.info(
            "[operator] %s/%s finished: phase=%s exit=%s timed_out=%s",
            namespace, name, finish.phase, outcome.exit_code, outcome.timed_out,
        )
        return {"phase": finish.phase, "launched": True}

    async def _patch(self, name: str, namespace: str, transition: Any) -> None:
        await self._gateway.patch_status(
            name=name, namespace=namespace, patch=dict(transition.patch)
        )
