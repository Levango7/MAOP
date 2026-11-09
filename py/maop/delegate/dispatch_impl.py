"""Core dispatch implementation mixin for :class:`Dispatcher`.

Split out from ``dispatch_core.py`` for maintainability (mixin pattern).
Houses the dispatcher initialisation, configuration accessors, the SLA
wrapping :meth:`_dispatch_impl`, sub-agent delegation, and the
:func:`_record_dispatcher_decision` helper.

- :meth:`__init__` — config-driven initialisation
- :meth:`effective_model` / :meth:`clear_agent_cache` — accessors
- :meth:`set_priority_queue` / :meth:`priority_queue` — queue integration
- :meth:`match_agent` — capability-based agent matching
- :meth:`_dispatch_impl` — SLA-wrapping dispatch entry
- :meth:`delegate_to_subagent` — recursive sub-agent delegation
- :func:`_record_dispatcher_decision` — routing decision record helper

The logger name is pinned to ``maop.delegate.dispatcher`` to preserve log
output exactly as before the split.
"""

from __future__ import annotations

import asyncio
import logging
import sqlite3
import time
from typing import Any

from maop.core.monitoring.monitoring import (
    MAOP_ROUTING_DECISION_DURATION_MS,
    MAOP_ROUTING_DECISION_TOTAL,
)
from maop.core.reliability.circuit_breaker import CircuitBreaker
from maop.core.reliability.error_schema import new_result
from maop.core.routing.routing_decision import (
    RoutingDecisionRecord,
    get_active_span_context,
    record_decision_safe,
)
from maop.delegate.agent_resolver import AgentResolver
from maop.delegate.models import (
    AgentConfig,
    DispatchResult,
)
from maop.delegate.sla_monitor import SLAMonitor

# Pinned to the original module name so log records are identical to the
# pre-split behaviour (tests / dashboards key on this logger name).
logger = logging.getLogger("maop.delegate.dispatcher")

# 哨兵：区分"调用方没传 priority_queue"（→ 默认挂载）与"显式传 None"
# （→ 关掉队列、退回槽位竞争时降级执行）。用 None 当默认值就无法表达后者。
_UNSET_QUEUE: Any = object()

# P3-fix: 常量提升——将魔法数字/字符串提为模块级常量，便于统一维护与调优。
_DEFAULT_DISPATCH_CONCURRENCY: int = 10  # 默认并发限制（settings 加载失败时降级使用）
_DEFAULT_MAX_SUBAGENT_DEPTH: int = 5     # 子代理递归委派最大深度


class DispatchImplMixin:
    """Core dispatch implementation & configuration helpers (mixin).

    Provides ``__init__`` and the configuration / SLA-wrapping helpers.
    Expects the host class to provide (via other mixins or itself):
        - ``self._dispatch_impl_inner`` — inner dispatch coroutine
        - ``self.dispatch`` — synchronous dispatch coroutine
    """

    def __init__(
        self,
        maop_config: Any | None = None,
        breaker: CircuitBreaker | None = None,
        model_selector: Any | None = None,
        root_dir: str | None = None,
        *,
        registry: Any | None = None,
        capability_matcher: Any | None = None,
        # 哨兵默认值：让"没传"与"显式传 None"可区分。若直接用 None 当默认，
        # 就无法表达"我就是要关掉队列、退回降级执行"（2026-11-09 实测踩到）。
        priority_queue: Any | None = _UNSET_QUEUE,
        # P3-4 fix: 保留 MAOP_config 作为向后兼容的别名（PEP8 违规参数名）。
        MAOP_config: Any | None = None,
    ) -> None:
        # P3-4 fix: 优先使用 PEP8 合规的 maop_config，回退到旧 MAOP_config 别名。
        config = maop_config if maop_config is not None else MAOP_config
        self._config = config
        self._breaker = breaker or CircuitBreaker()
        self._model_selector = model_selector
        self._effective_model: Any | None = None
        self._root_dir = root_dir
        self._subagent_mgr = None
        self._registry = registry
        self._matcher = capability_matcher
        # Delegated subsystems (N2 refactor)
        self._resolver = AgentResolver(
            config, root_dir,
            registry=registry, capability_matcher=capability_matcher,
        )
        self._sla = SLAMonitor()
        # Phase γ-2: priority queue for priority-aware dispatch.
        #
        # 2026-11-09：**默认改为挂载**（此前默认 None，导致 `set_priority_queue`
        # 全仓零调用方、`dispatch_priority`/`drain_pending` 也零生产调用方——
        # PRIORITY 策略只是枚举里的一个名字，实际从不发生）。
        #
        # 挂载后行为变化的边界很窄，且只在"槽位竞争"时出现：
        #   · 有空闲槽位（常态）→ 直接执行，与过去逐字一致；
        #   · 槽位被占满 → 入队等待，由槽位释放触发的 drain 按
        #     (priority, deadline, FIFO) 拉起，而不是降级超发。
        # 想彻底关掉：传 `priority_queue=None` 或用 `set_priority_queue(None)`，
        # 回到"降级执行"语义（保留给显式选择该行为的部署）。
        if priority_queue is _UNSET_QUEUE:
            try:
                from maop.core.reliability.priority_queue import PriorityTaskQueue
                priority_queue = PriorityTaskQueue()
            except Exception as exc:  # 队列不可用不代表不能派发
                logger.warning(
                    "[dispatch] 优先级队列构造失败，退回同步派发: %s", exc,
                )
                priority_queue = None
        self._priority_queue = priority_queue
        # 自动排空的去重标记（_schedule_drain 用）：同一时刻只安排一个排空任务。
        self._drain_scheduled = False
        # P2 fix: global concurrency limiter to prevent overwhelming downstream LLM APIs.
        # Uses settings.dispatch_concurrency (env: MAOP_DISPATCH_CONCURRENCY, default: 10).
        # P1-fix: get_settings 异常保护——配置文件损坏/加载失败时降级到默认并发值，
        # 而非让原始异常向上传播导致整个 dispatch 崩溃。
        try:
            from maop.config.settings import get_settings
            _concurrency = get_settings().dispatch_concurrency
        except Exception as exc:
            logger.warning(
                "[dispatch] get_settings 失败，降级到默认并发值 %d: %s",
                _DEFAULT_DISPATCH_CONCURRENCY, exc,
            )
            _concurrency = _DEFAULT_DISPATCH_CONCURRENCY
        self._semaphore = asyncio.Semaphore(_concurrency)

    @property
    def effective_model(self) -> Any | None:
        """Return the last resolved EffectiveModel (for audit/logging)."""
        return self._effective_model

    def clear_agent_cache(self) -> None:
        """Clear the agent config cache (call after config reload)."""
        self._resolver.clear_cache()

    # ── Phase γ-2: priority queue integration ──────────────────

    def set_priority_queue(self, queue: Any | None) -> None:
        """Attach (or detach with ``None``) a priority task queue.

        When a queue is attached, :meth:`dispatch_priority` will enqueue
        dispatch requests and :meth:`drain_pending` will execute them in
        priority order. The synchronous :meth:`dispatch` is unaffected.
        """
        self._priority_queue = queue

    @property
    def priority_queue(self) -> Any | None:
        """The currently attached priority queue (or ``None``)."""
        return self._priority_queue

    def match_agent(self, task: str, requirements: list[str] | None = None) -> AgentConfig | None:
        """Use CapabilityMatcher to find the best agent for a task (delegates to AgentResolver)."""
        return self._resolver.match_agent(task, requirements)

    async def _dispatch_impl(
        self,
        agent: str,
        task: str,
        *,
        routing_key: str = "",
        workdir: str = "",
        timeout_seconds: int | None = None,
        trace_id: str = "",
        streamer: Any | None = None,
        priority: int = 3,
        deadline_ms: int | None = None,
        _failover_depth: int = 0,
        dry_run: bool = False,
        _slot_reserved: bool = False,
    ) -> DispatchResult:
        # Phase γ-1: derive SLA tier, log SLA context, and record
        # in-flight gauges. The finally block at the end of this method
        # decrements the gauges and checks for deadline violation.
        sla_tier = self._sla.tier_from_priority(priority)
        self._record_sla_dispatch_start(priority, sla_tier)
        logger.info(
            "SLA dispatch: agent=%s priority=%d sla_tier=%s deadline_ms=%s trace_id=%s",
            agent, priority, sla_tier, deadline_ms, trace_id,
            extra={
                "sla_priority": priority,
                "sla_deadline_ms": deadline_ms if deadline_ms is not None else 0,
                "sla_tier": sla_tier,
            },
        )

        # ── 并发槽位占位（2026-11-08 接线，2026-11-09 加队列分支）─────────
        # acquire/release 与 AgentRouter._active_count 一直都存在，但**全仓没有
        # 任何调用方**，于是两个后果：① RoutingStrategy.LOAD_BALANCED 的排序键
        # 第一项恒为 0，实际退化成 agent 名字典序（用户选"负载均衡"拿到字母序
        # 第一）；② AgentDescriptor.max_concurrent 从未被强制执行。
        # 这里补上调用点，让两件事第一次真正发生。
        #
        # 拿到槽位时按原路执行；**拿不到时分两种**：
        #   · 未挂优先级队列（默认）→ 保持 11-08 的降级语义：记一条审计后照常
        #     执行。这保住了全部既有行为与测试，硬限额仍留给产品决策。
        #   · 挂了队列 → **入队等待**而非超发。这是 PRIORITY 策略从"仅为标注"
        #     变成真实调度语义的那一步：调用方 await 同一个调用，任务按
        #     (priority, deadline, FIFO) 排队，槽位一空就由 drain 拉起执行。
        _slot_agent = agent
        # 队列排空路径已代为申请槽位（drain_pending 里 acquire 过），
        # 用 _slot_reserved 告知 inner：不要再申请、也不要再入队，否则活锁。
        _slot_held = _slot_reserved or self._acquire_agent_slot(_slot_agent)

        # "拿不到槽位"有两种完全不同的原因，处理也必须不同：
        #   · **agent 不在 catalog**（未配置 / 拼错 / 未注册）→ acquire 必然 False。
        #     这时入队等于把一个"注定失败"的请求塞进队列，而且**永无排空**——
        #     没有槽位曾被 acquire 成功，就没有 release 来触发 drain，调用方
        #     await 的 future 永远不会结算（2026-11-09 实测：unknown agent 的
        #     派发在 dispatch() 内部挂死，13 条存量用例因此超时）。
        #     ⇒ 这种情况必须**照原路往下走**，让 inner 自己给出"agent not found"。
        #   · agent 在 catalog、只是并发已满 → 才是真正的"槽位竞争"，入队等待。
        _agent_known = self._agent_is_tracked(_slot_agent)
        if not _slot_held and self._priority_queue is not None and _agent_known:
            # 槽位竞争 → 入队。dispatch_priority 内部会：死线/容量预检、
            # 包 PriorityTask、push、并尝试立即排空一次。
            return await self.dispatch_priority(
                agent, task,
                routing_key=routing_key, workdir=workdir,
                timeout_seconds=timeout_seconds, trace_id=trace_id,
                streamer=streamer,
                priority=priority, deadline_ms=deadline_ms,
            )

        try:
            return await self._dispatch_impl_inner(
                agent, task,
                routing_key=routing_key, workdir=workdir,
                timeout_seconds=timeout_seconds, trace_id=trace_id,
                streamer=streamer,
                priority=priority, deadline_ms=deadline_ms,
                sla_tier=sla_tier,
                _failover_depth=_failover_depth,
                dry_run=dry_run,
                _slot_reserved=_slot_held,
            )
        finally:
            if _slot_held:
                self._release_agent_slot(_slot_agent)
            self._record_sla_dispatch_end(priority, sla_tier, deadline_ms=deadline_ms)

    def _acquire_agent_slot(self, agent_name: str) -> bool:
        """占用 agent 并发槽位；失败不阻断派发（降级 + 审计）。

        AgentRouter 未配置或不可用时静默返回 True（视为不 tracking），
        保证接线本身不会成为新的故障点。
        """
        try:
            from maop.core.agent.router.agent_router import get_agent_router
            router = get_agent_router()
            if router.acquire(agent_name):
                return True
            logger.info(
                "[dispatch] agent=%s 并发槽位已满，降级执行（max_concurrent 未作硬限额）",
                agent_name,
            )
        except (ImportError, sqlite3.Error, OSError, ValueError, TypeError) as exc:
            # ADR-023：收窄到现实失败面，不用宽口径 except Exception——这条路径的
            # 全部真实异常来源就是"模块导入 / SQLite 读写 / 文件 IO / 描述符形状"。
            # 接线不得成为故障点：任何一类失败都只降级、不阻断派发。
            logger.debug("[dispatch] acquire slot unavailable (non-blocking): %s", exc)
        return False

    def _agent_is_tracked(self, agent_name: str) -> bool:
        """该 agent 是否在 catalog 中登记（即" acquire 失败"是竞争还是不存在）。

        只有**已登记**的 agent 才谈得上"槽位竞争"；未登记时 acquire 必 False，
        此时入队会把一个注定失败的请求永久留在队列里（无槽位曾被占用 ⇒ 无
        release ⇒ 无 drain ⇒ future 永不结算）。查不到时保守返回 True，
        让调用方按既有路径走完（inner 会给出 not-found 结果）。
        """
        try:
            from maop.core.agent.registry.agent_catalog import AgentCatalog
            catalog = AgentCatalog.default()
            return catalog.get(agent_name) is not None
        except (ImportError, sqlite3.Error, OSError, ValueError, TypeError):
            # 查不了就当作"已登记"——宁可走原路让 inner 报错，也不要凭猜测入队。
            return True

    def _release_agent_slot(self, agent_name: str) -> None:
        """释放并发槽位。与 :meth:`_acquire_agent_slot` 严格配对。

        释放后立刻尝试排空一次队列——槽位刚空出来，队列里可能正有同 agent 的
        任务在等（2026-11-09）。没有运行中的事件循环时跳过，等下次显式排空。
        """
        try:
            from maop.core.agent.router.agent_router import get_agent_router
            router = get_agent_router()
            router.release(agent_name)
        except (ImportError, sqlite3.Error, OSError, ValueError, TypeError) as exc:
            logger.debug("[dispatch] release slot failed (ignored): %s", exc)
            return
        if self._priority_queue is not None:
            self._schedule_drain()

    async def delegate_to_subagent(
        self,
        parent: str,
        agent: str,
        task: str,
        *,
        routing_key: str = "",
        trace_id: str = "",
        max_depth: int = _DEFAULT_MAX_SUBAGENT_DEPTH,  # P3-fix: 使用模块级常量
    ) -> DispatchResult:
        """Spawn a sub-agent and dispatch the task through it.

        This enables recursive delegation: agent A -> agent B -> agent C,
        with depth tracking to prevent infinite recursion.
        """
        # Resolve lazy-subsystem helper through the dispatcher namespace so
        # that ``patch("maop.delegate.dispatcher._get_subagent_manager")`` in
        # tests takes effect (call syntax unchanged from pre-split code).
        from maop.delegate.dispatcher import _get_subagent_manager

        if self._subagent_mgr is None:
            self._subagent_mgr = _get_subagent_manager(self._root_dir)
        if self._subagent_mgr is None:
            result = new_result(
                agent=agent, task=task,
                exit_code=-2, error="SubAgentManager not available",
                trace_id=trace_id, routing_key=routing_key,
            )
            return DispatchResult(result=result, breaker_tripped=False)

        sa_info = self._subagent_mgr.spawn_child(
            parent=parent, agent=agent, task=task, max_depth=max_depth,
        )

        dispatch_result = await self.dispatch(
            agent=agent, task=task,
            routing_key=routing_key, trace_id=trace_id,
        )

        exit_code = dispatch_result.result.exit_code if dispatch_result.result else -1
        self._subagent_mgr.terminate(sa_info.id, exit_code=exit_code)

        return dispatch_result


# ── Phase γ-4: decision-record helper ─────────────────────────


def _record_dispatcher_decision(
    *,
    trace_id: str,
    agent: str,
    routing_key: str,
    priority: int,
    sla_tier: str,
    deadline_ms: int | None,
    selected_model: str,
    duration_ms: float,
) -> None:
    """Persist a :class:`RoutingDecisionRecord` for ``Dispatcher.dispatch``.

    The dispatcher is the PARENT of the routing decision chain — its
    record is the entry point for reconstructing the full Plan → Route
    → LB → ModelSelect trace via ``query_by_trace(trace_id)``.
    """
    otel_trace_id, span_id, parent_span_id = get_active_span_context()
    effective_trace = trace_id or otel_trace_id

    deadline_note = f"deadline={deadline_ms}ms" if deadline_ms else "deadline=none"
    model_note = f", model='{selected_model}'" if selected_model else ""
    explanation = (
        f"Dispatched to agent '{agent}' with priority={priority} "
        f"({sla_tier}), {deadline_note}{model_note}."
    )

    try:
        MAOP_ROUTING_DECISION_TOTAL.inc(labels={"stage": "dispatcher"})
        MAOP_ROUTING_DECISION_DURATION_MS.observe(duration_ms)
    except Exception as exc:
        # H10 fix (Phase R7): routing metric 记录失败不应静默
        logger.debug("routing decision metric record failed: %s", exc)

    record_decision_safe(RoutingDecisionRecord(
        trace_id=effective_trace,
        span_id=span_id,
        parent_span_id=parent_span_id,
        timestamp=time.time(),
        stage="dispatcher",
        input_summary={
            "agent": agent,
            "routing_key": routing_key,
            "priority": priority,
            "sla_tier": sla_tier,
            "deadline_ms": deadline_ms,
        },
        output_summary={
            "selected_agent": agent,
            "selected_model": selected_model,
        },
        explanation=explanation,
        duration_ms=duration_ms,
        attributes={
            "priority": priority,
            "sla_tier": sla_tier,
            "deadline_ms": deadline_ms or 0,
            "selected_agent": agent,
            "selected_model": selected_model,
        },
    ))