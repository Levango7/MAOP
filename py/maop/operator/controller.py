"""OperatorController —— M0 的 mock 可测核心.

流程（单个 MaopTask 事件）：
1. ``spec.parse_task_spec`` 校验 —— 失败直接补丁 Failed（reason=InvalidSpec），
   **不拉起**子进程；
2. 终态（status.phase 已是 terminal）→ no-op（k8s 事件重放幂等）；
3. （M1）``resolver`` 注入时解析 ``spec.agent`` 的 MaopAgent CR —— 存在即
   pin（命令带 ``--agent``，``AgentResolved=True`` 条件）；未声明则按 M0
   行为不 pin（``AgentResolved=False`` 条件），两者都继续执行；
4. （M3.2）``quota_checker`` 注入时过配额门 —— 拒绝则终态 ``QuotaExceeded``
   （不拉起子进程）；社区版不注入配额门 = 行为不变；
5. 否则补丁 Running（``plan_launch``）→ runner 执行 ``command_for(spec)``
   （M2：恒带任务级 ``--max-turns``）→ 按结果补丁终态（``plan_finish``；
   M2/M3.3：解析 ``[USAGE] turns=N [tokens=M] [source=S]`` 写入
   ``status.turns`` / ``status.tokensUsed`` / ``status.tokensSource``——
   旧版只有 ``turns=`` 的行同样兼容；M3.2：注入配额门时回报用量）；
   任务被取消 → Cancelled 后原样上抛。

gateway/runner/resolver/quota_checker 全部构造注入 —— 测试用 fake 驱动
全链路，不需要 k8s。
"""

from __future__ import annotations

import asyncio
import logging
import re
from typing import Any

from maop.operator.gateway import (
    AgentResolver,
    QuotaChecker,
    RunOutcome,
    StatusGateway,
    SubprocessRunner,
)
from maop.operator.reconcile import (
    is_terminal,
    plan_finish,
    plan_launch,
    plan_quota_exceeded,
)
from maop.operator.spec import (
    InvalidSpecError,
    MaopTaskSpec,
    command_for,
    parse_task_spec,
)

logger = logging.getLogger(__name__)

#: M2/M3.3 协议行：``maop run`` 在 stdout 打印的 turn/token 消耗
#: （与 [TRACE] 同为稳定单行；M2 时代只有 ``turns=``，tokens/source 可缺省）。
_USAGE_RE = re.compile(
    r"\[USAGE\]\s*turns=(\d+)(?:\s+tokens=(\d+))?(?:\s+source=(\w+))?"
)


def _parse_usage(stdout: str) -> tuple[int | None, int | None, str | None]:
    """解析 ``[USAGE] turns=N [tokens=M] [source=S]``.

    缺行返回 ``(None, None, None)``（旧版 ``maop run`` 不打该行——前向兼容）。
    """
    match = _USAGE_RE.search(stdout or "")
    if not match:
        return None, None, None
    turns = int(match.group(1))
    tokens = int(match.group(2)) if match.group(2) is not None else None
    source = match.group(3)
    return turns, tokens, source


class OperatorController:
    """MaopTask 的协调器（gateway/runner 注入；无 k8s 依赖可测）。"""

    def __init__(
        self,
        *,
        gateway: StatusGateway,
        runner: SubprocessRunner,
        resolver: AgentResolver | None = None,
        quota_checker: QuotaChecker | None = None,
        timeout_s: float | None = None,
    ) -> None:
        self._gateway = gateway
        self._runner = runner
        self._resolver = resolver
        self._quota_checker = quota_checker
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

        # 3. M1: MaopAgent CR 解析（存在即 pin；未注入 resolver 时保持 M0 行为）。
        agent_resolved: bool | None = None
        if self._resolver is not None:
            agent_resolved = (
                await self._resolver.resolve_agent(name=spec.agent, namespace=namespace)
            ) is not None
        pin = agent_resolved is True

        # 4. M3.2: 配额门（注入才生效）——拒绝则终态 QuotaExceeded，不拉起。
        if self._quota_checker is not None:
            allowed, reason = await self._quota_checker.check_quota(
                tenant=spec.tenant, max_turns=spec.max_turns, prompt=spec.prompt
            )
            if not allowed:
                logger.info(
                    "[operator] %s/%s quota denied: %s", namespace, name, reason
                )
                await self._patch(
                    name, namespace,
                    plan_quota_exceeded(tenant=spec.tenant, reason=reason),
                )
                return {"phase": "QuotaExceeded", "launched": False, "reason": reason}

        # 5. Running + 拉起。
        await self._patch(
            name, namespace, plan_launch(agent=spec.agent, agent_resolved=agent_resolved)
        )
        logger.info(
            "[operator] %s/%s launching (agent=%s resolved=%s pinned=%s): %s",
            namespace, name, spec.agent, agent_resolved, pin, spec.prompt[:80],
        )

        try:
            outcome = await self._runner.run(
                command_for(spec, pin_agent=pin, apply_turn_budget=True),
                timeout_s=self._timeout_s,
            )
        except asyncio.CancelledError:
            await self._patch(name, namespace, plan_finish(cancelled=True))
            raise
        return await self._finalize(name, namespace, outcome, spec)

    async def _finalize(
        self, name: str, namespace: str, outcome: RunOutcome, spec: MaopTaskSpec
    ) -> dict[str, Any]:
        turns, tokens_used, tokens_source = _parse_usage(outcome.stdout_tail)
        finish = plan_finish(
            exit_code=outcome.exit_code,
            timed_out=outcome.timed_out,
            cancelled=outcome.cancelled,
            turns=turns,
            tokens_used=tokens_used,
            tokens_source=tokens_source,
        )

        # M3.2: 用量回报（注入配额门时）——缺协议行（旧版 maop run）则不回报。
        if self._quota_checker is not None and turns is not None:
            await self._quota_checker.record_usage(
                tenant=spec.tenant,
                turns=turns,
                tokens_used=tokens_used or 0,
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
