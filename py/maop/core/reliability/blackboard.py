"""MAOP Blackboard Architecture — structured shared knowledge + KS + controller.

Implements the classic Blackboard pattern on top of the existing EventBus:

- ``BlackboardDomain``: enum of allowed domains (R-8 domain whitelist).
- ``BlackboardEntry``: structured knowledge entry (entry_id/domain/content/
  contributor/timestamp/confidence/metadata).
- ``Blackboard``: in-memory shared knowledge store with domain-scoped
  subscriptions, snapshot, history, and clear operations.
- ``KnowledgeSource``: abstract processing unit (Agent/Tool/Human).
- ``Controller``: event-driven trigger that listens to bb changes and
  dispatches registered KnowledgeSources; ``control_step`` runs one cycle.
- EventBus integration via ``publish(Event)`` (C-3 unified API).
- ``read_domains`` validates the input domain list (R-7).
- Backward compatible: blackboard is disabled by default; opt-in via
  ``enable_event_bus()`` so existing modules are unaffected until enabled.

Design reference: docs/design-blackboard.md (P2 implementation slice).
"""

from __future__ import annotations

import asyncio
import logging
import threading
import uuid
from collections import defaultdict
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from maop.core.reliability.event_bus import Event, EventBus, get_event_bus

logger = logging.getLogger(__name__)

# ── Domain whitelist (R-8) ────────────────────────────────────────


class BlackboardDomain(str, Enum):
    """黑板领域枚举（R-8 域白名单）。

    仅允许写入以下预定义域；``Blackboard._validate_domain`` 据此校验。
    """

    PROBLEM = "problem"
    PARTIAL_SOLUTION = "partial_solution"
    HYPOTHESIS = "hypothesis"
    EVIDENCE = "evidence"
    DECISION = "decision"
    CONSTRAINT = "constraint"
    AGENT_CONTRIBUTION = "agent_contribution"


# ── Exceptions ────────────────────────────────────────────────────


class BlackboardError(Exception):
    """黑板基础异常。"""


class InvalidDomainError(BlackboardError):
    """无效域异常（R-7 输入校验 / R-8 白名单校验）。"""


class KnowledgeSourceError(BlackboardError):
    """知识源相关异常。"""


# ── BlackboardEntry ───────────────────────────────────────────────


@dataclass
class BlackboardEntry:
    """黑板条目：结构化知识单元。

    Parameters
    ----------
    entry_id : str
        全局唯一 ID（UUID4）。
    domain : str
        领域命名空间，必须在 ``BlackboardDomain`` 白名单内。
    content : Any
        实际知识内容（任意 JSON 可序列化结构）。
    contributor : str
        产生该条目的知识源/Agent 名称。
    timestamp : str
        ISO-8601 创建时间。
    confidence : float
        置信度 [0.0, 1.0]。
    metadata : dict[str, Any]
        附加元数据（如来源溯源、schema 标识等）。
    """

    entry_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    domain: str = ""
    content: Any = None
    contributor: str = ""
    timestamp: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    confidence: float = 1.0
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """序列化为 JSON 友好字典。"""
        return {
            "entry_id": self.entry_id,
            "domain": self.domain,
            "content": self.content,
            "contributor": self.contributor,
            "timestamp": self.timestamp,
            "confidence": self.confidence,
            "metadata": self.metadata,
        }


# ── Blackboard ───────────────────────────────────────────────────

# 订阅者回调：同步或异步，接收一个 BlackboardEntry。
DomainCallback = Callable[[BlackboardEntry], Awaitable[None] | None]

# 全部允许的域值集合（R-8 白名单）
_ALLOWED_DOMAINS: frozenset[str] = frozenset(d.value for d in BlackboardDomain)


class Blackboard:
    """共享黑板：结构化知识库。

    提供：

    - ``write(domain, content, contributor, ...)`` 写入条目（R-8 校验域白名单）。
    - ``read(domain)`` 读取域内所有条目。
    - ``read_domains(domains)`` 读取多个域（R-7 校验输入域列表）。
    - ``get_snapshot()`` 获取完整快照。
    - ``subscribe(domain, callback)`` 订阅域变更通知。
    - ``clear(domain)`` 清除域。
    - ``get_history()`` 获取操作历史。

    默认**不**接入 EventBus（向后兼容）；调用 ``enable_event_bus()`` 启用，
    启用后所有写操作通过 ``publish(Event)`` 异步广播（C-3 统一 API）。
    """

    def __init__(self, event_bus: EventBus | None = None) -> None:
        self._entries: dict[str, list[BlackboardEntry]] = defaultdict(list)
        self._subscribers: dict[str, list[DomainCallback]] = defaultdict(list)
        self._lock = threading.RLock()
        self._event_bus: EventBus | None = None
        self._history: list[dict[str, Any]] = []
        self._max_history = 1000
        if event_bus is not None:
            self.enable_event_bus(event_bus)

    # ── EventBus integration (C-3) ─────────────────────────────

    def enable_event_bus(self, event_bus: EventBus | None = None) -> None:
        """启用 EventBus 集成（C-3 publish(Event) API）。

        不传 ``event_bus`` 时使用全局单例 ``get_event_bus()``。
        """
        self._event_bus = event_bus if event_bus is not None else get_event_bus()

    def disable_event_bus(self) -> None:
        """禁用 EventBus 集成（退化为纯内存黑板）。"""
        self._event_bus = None

    @property
    def event_bus_enabled(self) -> bool:
        return self._event_bus is not None

    # ── Domain validation (R-7 / R-8) ──────────────────────────

    @staticmethod
    def _validate_domain(domain: str) -> None:
        """校验单个域是否在白名单内（R-8）。"""
        if not isinstance(domain, str):
            raise InvalidDomainError(
                f"domain must be str, got {type(domain).__name__}"
            )
        if domain not in _ALLOWED_DOMAINS:
            raise InvalidDomainError(
                f"Invalid domain '{domain}'. Allowed: {sorted(_ALLOWED_DOMAINS)}"
            )

    @staticmethod
    def _validate_domains(domains: list[str]) -> None:
        """校验域列表（R-7 输入校验）。

        - 必须为 list；
        - 每个元素必须为 str；
        - 每个元素必须在白名单内。
        """
        if not isinstance(domains, list):
            raise InvalidDomainError(
                f"domains must be a list, got {type(domains).__name__}"
            )
        for d in domains:
            if not isinstance(d, str):
                raise InvalidDomainError(
                    f"domain must be str, got {type(d).__name__}"
                )
            if d not in _ALLOWED_DOMAINS:
                raise InvalidDomainError(
                    f"Invalid domain '{d}'. Allowed: {sorted(_ALLOWED_DOMAINS)}"
                )

    # ── Write ──────────────────────────────────────────────────

    async def write(
        self,
        domain: str,
        content: Any,
        contributor: str = "",
        *,
        confidence: float = 1.0,
        metadata: dict[str, Any] | None = None,
    ) -> BlackboardEntry:
        """写入条目。

        - R-8：校验 ``domain`` 在白名单内，否则抛 ``InvalidDomainError``。
        - 写入后通知该域订阅者（同步或异步回调）。
        - 若 EventBus 已启用，异步 ``publish(Event)`` 广播 ``blackboard.write``。
        """
        self._validate_domain(domain)
        entry = BlackboardEntry(
            domain=domain,
            content=content,
            contributor=contributor,
            confidence=confidence,
            metadata=metadata or {},
        )
        with self._lock:
            self._entries[domain].append(entry)
            self._record_history(
                action="write",
                domain=domain,
                entry_id=entry.entry_id,
                contributor=contributor,
                timestamp=entry.timestamp,
            )
            subscribers = list(self._subscribers[domain])
        # 通知订阅者（锁外执行避免长时回调阻塞其他写操作）
        await self._notify_subscribers(subscribers, entry)
        # EventBus 广播（C-3 publish(Event)）
        await self._publish_event("write", entry)
        return entry

    async def _notify_subscribers(
        self, subscribers: list[DomainCallback], entry: BlackboardEntry
    ) -> None:
        """逐个调用订阅者回调；同步与异步回调均支持。"""
        for cb in subscribers:
            try:
                result = cb(entry)
                if asyncio.iscoroutine(result):
                    await result
            except Exception as exc:  # BLE001: 隔离单个回调失败
                logger.error(
                    "Blackboard subscriber error on domain=%s: %s",
                    entry.domain, exc,
                )

    async def _publish_event(self, action: str, entry: BlackboardEntry) -> None:
        """通过 EventBus 发布事件（C-3 publish(Event) API）。"""
        if self._event_bus is None:
            return
        try:
            await self._event_bus.publish(
                Event(
                    topic=f"blackboard.{action}",
                    data={
                        "entry_id": entry.entry_id,
                        "domain": entry.domain,
                        "contributor": entry.contributor,
                        "action": action,
                        # 携带 content/confidence/metadata 以便订阅者重建条目
                        "content": entry.content,
                        "confidence": entry.confidence,
                        "metadata": entry.metadata,
                        "timestamp": entry.timestamp,
                    },
                    source="blackboard",
                )
            )
        except Exception as exc:  # BLE001: EventBus 故障不应阻断黑板写入
            logger.error("Blackboard EventBus publish failed: %s", exc)

    # ── Read ───────────────────────────────────────────────────

    def read(self, domain: str) -> list[BlackboardEntry]:
        """读取域内所有条目（按写入顺序）。"""
        self._validate_domain(domain)
        with self._lock:
            return list(self._entries.get(domain, []))

    def read_domains(
        self, domains: list[str]
    ) -> dict[str, list[BlackboardEntry]]:
        """读取多个域（R-7 校验输入域列表）。

        返回 ``{domain: [entries...]}`` 字典。无效域抛 ``InvalidDomainError``。
        """
        self._validate_domains(domains)
        with self._lock:
            return {d: list(self._entries.get(d, [])) for d in domains}

    def get_snapshot(self) -> dict[str, list[dict[str, Any]]]:
        """获取完整快照（所有域 → 条目字典列表）。"""
        with self._lock:
            return {
                d: [e.to_dict() for e in entries]
                for d, entries in self._entries.items()
            }

    # ── Subscribe ─────────────────────────────────────────────

    def subscribe(self, domain: str, callback: DomainCallback) -> None:
        """订阅域变更通知。回调签名 ``callback(entry: BlackboardEntry)``。"""
        self._validate_domain(domain)
        with self._lock:
            self._subscribers[domain].append(callback)

    def unsubscribe(self, domain: str, callback: DomainCallback) -> None:
        """取消订阅。"""
        self._validate_domain(domain)
        with self._lock:
            if domain in self._subscribers:
                self._subscribers[domain] = [
                    cb for cb in self._subscribers[domain] if cb is not callback
                ]

    # ── Clear ─────────────────────────────────────────────────

    async def clear(self, domain: str) -> int:
        """清除域。返回被清除的条目数。"""
        self._validate_domain(domain)
        with self._lock:
            count = len(self._entries.get(domain, []))
            self._entries[domain] = []
            self._record_history(
                action="clear",
                domain=domain,
                entry_id="",
                contributor="",
                timestamp=datetime.now(timezone.utc).isoformat(),
                extra={"cleared_count": count},
            )
        # EventBus 广播 clear 事件
        if self._event_bus is not None:
            try:
                await self._event_bus.publish(
                    Event(
                        topic="blackboard.clear",
                        data={"domain": domain, "cleared_count": count},
                        source="blackboard",
                    )
                )
            except Exception as exc:  # BLE001
                logger.error("Blackboard EventBus publish failed: %s", exc)
        return count

    # ── History ───────────────────────────────────────────────

    def _record_history(
        self,
        *,
        action: str,
        domain: str,
        entry_id: str,
        contributor: str,
        timestamp: str,
        extra: dict[str, Any] | None = None,
    ) -> None:
        """记录操作历史（环形缓冲）。"""
        record: dict[str, Any] = {
            "action": action,
            "domain": domain,
            "entry_id": entry_id,
            "contributor": contributor,
            "timestamp": timestamp,
        }
        if extra:
            record.update(extra)
        self._history.append(record)
        if len(self._history) > self._max_history:
            self._history = self._history[-self._max_history:]

    def get_history(self, limit: int = 100) -> list[dict[str, Any]]:
        """获取操作历史（最近 ``limit`` 条）。"""
        with self._lock:
            return list(self._history[-limit:])

    # ── Misc ──────────────────────────────────────────────────

    def get_domains(self) -> list[str]:
        """获取所有非空域。"""
        with self._lock:
            return [d for d, entries in self._entries.items() if entries]

    def total_entries(self) -> int:
        """获取所有条目总数。"""
        with self._lock:
            return sum(len(entries) for entries in self._entries.values())


# ── KnowledgeSource ──────────────────────────────────────────────


class KnowledgeSource:
    """知识源抽象基类。

    Agent / Tool / Human 都可作为知识源。子类需：

    1. 设置类属性 ``name`` / ``priority`` / ``read_domains`` / ``write_domains``。
    2. 实现 ``async execute(bb, trigger_entry)``。

    ``read_domains`` 用于控制器依赖分析与并发调度；声明 ``("*",)`` 表示读取全部域
    （保守策略，等价于不与其他知识源并发）。

    Note: ``read_domains`` / ``write_domains`` 使用不可变 ``tuple`` 而非 ``list``
    作为类属性默认值，以符合 ruff RUF012（避免可变默认值共享风险）。
    """

    name: str = ""
    priority: int = 0
    read_domains: tuple[str, ...] = ()
    write_domains: tuple[str, ...] = ()

    async def execute(
        self, bb: Blackboard, trigger_entry: BlackboardEntry
    ) -> None:
        """执行知识源逻辑。子类必须实现。

        Parameters
        ----------
        bb : Blackboard
            共享黑板实例，用于读写。
        trigger_entry : BlackboardEntry
            触发本次执行的黑板条目。
        """
        raise NotImplementedError(
            f"KnowledgeSource {self.name or type(self).__name__} must implement execute()"
        )


# ── Controller ──────────────────────────────────────────────────


class Controller:
    """黑板控制器：事件驱动触发。

    生命周期：

    1. ``register_ks`` 注册知识源。
    2. ``start`` 订阅 EventBus 黑板变更事件（若注入了 EventBus）。
    3. 黑板写入 → EventBus 事件 → ``_on_blackboard_event`` → 入队触发条目。
    4. ``control_step`` 处理队列中所有触发条目，按知识源优先级调度执行。
    5. ``stop`` 取消订阅，停止调度。
    """

    def __init__(
        self,
        bb: Blackboard,
        event_bus: EventBus | None = None,
        *,
        max_iterations: int = 100,
    ) -> None:
        self._bb = bb
        self._event_bus = event_bus
        self._knowledge_sources: dict[str, KnowledgeSource] = {}
        self._max_iterations = max_iterations
        self._iteration_count = 0
        self._running = False
        self._lock = threading.RLock()
        self._trigger_queue: list[BlackboardEntry] = []
        # 执行轨迹（供 dashboard 审计）
        self._trace: list[dict[str, Any]] = []

    # ── Registration ──────────────────────────────────────────

    def register_ks(self, ks: KnowledgeSource) -> None:
        """注册知识源。"""
        if not ks.name:
            raise KnowledgeSourceError(
                f"KnowledgeSource name must be non-empty: {type(ks).__name__}"
            )
        with self._lock:
            self._knowledge_sources[ks.name] = ks
        logger.info("[blackboard] Registered KS: %s (priority=%d)", ks.name, ks.priority)

    def unregister_ks(self, name: str) -> None:
        """注销知识源。"""
        with self._lock:
            self._knowledge_sources.pop(name, None)

    @property
    def registered_ks(self) -> list[str]:
        with self._lock:
            return list(self._knowledge_sources.keys())

    @property
    def iteration_count(self) -> int:
        return self._iteration_count

    @property
    def is_running(self) -> bool:
        return self._running

    @property
    def max_iterations(self) -> int:
        return self._max_iterations

    def get_trace(self, limit: int = 100) -> list[dict[str, Any]]:
        """获取执行轨迹。"""
        with self._lock:
            return list(self._trace[-limit:])

    # ── Trigger queue ─────────────────────────────────────────

    def enqueue_trigger(self, entry: BlackboardEntry) -> None:
        """入队触发条目（供外部直接触发，或由 EventBus 回调入队）。"""
        with self._lock:
            self._trigger_queue.append(entry)

    def pending_triggers(self) -> int:
        """当前队列中待处理的触发条目数。"""
        with self._lock:
            return len(self._trigger_queue)

    # ── Control loop ──────────────────────────────────────────

    async def control_step(self) -> int:
        """单步控制循环：处理队列中所有触发条目。

        Returns
        -------
        int
            本步执行的知识源数量。

        若达到 ``max_iterations`` 上限则跳过执行并记录告警。
        """
        if self._iteration_count >= self._max_iterations:
            logger.warning(
                "[blackboard] Controller reached max_iterations=%d, skipping step",
                self._max_iterations,
            )
            return 0
        with self._lock:
            triggers = list(self._trigger_queue)
            self._trigger_queue.clear()
        executed = 0
        for entry in triggers:
            for ks in self._select_ks_for(entry):
                try:
                    await ks.execute(self._bb, entry)
                    executed += 1
                    with self._lock:
                        self._trace.append({
                            "ks": ks.name,
                            "entry_id": entry.entry_id,
                            "domain": entry.domain,
                            "timestamp": datetime.now(timezone.utc).isoformat(),
                            "status": "ok",
                        })
                except Exception as exc:  # BLE001: 单个 KS 失败不阻断其他
                    logger.error(
                        "[blackboard] KS %s execution failed: %s", ks.name, exc
                    )
                    with self._lock:
                        self._trace.append({
                            "ks": ks.name,
                            "entry_id": entry.entry_id,
                            "domain": entry.domain,
                            "timestamp": datetime.now(timezone.utc).isoformat(),
                            "status": "error",
                            "error": str(exc),
                        })
        self._iteration_count += 1
        return executed

    def _select_ks_for(self, entry: BlackboardEntry) -> list[KnowledgeSource]:
        """选择处理该条目的知识源（按 priority 降序）。

        命中条件：知识源 ``read_domains`` 包含条目域，或包含 ``"*"``（通配全部）。
        """
        with self._lock:
            candidates = [
                ks for ks in self._knowledge_sources.values()
                if entry.domain in ks.read_domains or "*" in ks.read_domains
            ]
        return sorted(candidates, key=lambda k: k.priority, reverse=True)

    # ── Lifecycle ─────────────────────────────────────────────

    async def start(self) -> None:
        """启动控制器：订阅 EventBus 黑板变更事件。"""
        self._running = True
        if self._event_bus is not None:
            self._event_bus.subscribe(
                "blackboard.write",
                self._on_blackboard_event,
            )
            logger.info("[blackboard] Controller started (EventBus subscribed)")

    async def _on_blackboard_event(self, event: Event) -> None:
        """EventBus 事件回调：将条目入队等待 ``control_step`` 处理。

        从事件 data 重建 BlackboardEntry（携带 content/confidence/metadata），
        以便知识源在 execute 中读取触发条目的完整内容。
        """
        data = event.data
        entry = BlackboardEntry(
            entry_id=data.get("entry_id", ""),
            domain=data.get("domain", ""),
            content=data.get("content"),
            contributor=data.get("contributor", ""),
            timestamp=data.get("timestamp", ""),
            confidence=data.get("confidence", 1.0),
            metadata=data.get("metadata", {}),
        )
        self.enqueue_trigger(entry)

    async def stop(self) -> None:
        """停止控制器：取消 EventBus 订阅。"""
        self._running = False
        if self._event_bus is not None:
            self._event_bus.unsubscribe(
                "blackboard.write", self._on_blackboard_event
            )
            logger.info("[blackboard] Controller stopped")

    async def is_converged(self) -> bool:
        """判定是否已收敛。

        默认收敛条件：触发队列为空且已达 ``max_iterations``。
        子类可重写以实现自定义收敛谓词。
        """
        return self.pending_triggers() == 0 and self._iteration_count > 0


# ── Global singletons ────────────────────────────────────────────

_blackboard: Blackboard | None = None
_blackboard_lock = threading.Lock()

_controller: Controller | None = None
_controller_lock = threading.Lock()


def get_blackboard() -> Blackboard:
    """获取全局黑板单例。

    默认**不**启用 EventBus（向后兼容）；显式调用
    ``get_blackboard().enable_event_bus()`` 启用。
    """
    global _blackboard
    if _blackboard is None:
        with _blackboard_lock:
            if _blackboard is None:
                _blackboard = Blackboard()
    return _blackboard


def reset_blackboard() -> None:
    """重置全局黑板单例（测试用）。"""
    global _blackboard
    with _blackboard_lock:
        _blackboard = None


def get_blackboard_controller() -> Controller:
    """获取全局黑板控制器单例。

    绑定到 ``get_blackboard()``，默认不注入 EventBus（向后兼容）。
    """
    global _controller
    if _controller is None:
        with _controller_lock:
            if _controller is None:
                _controller = Controller(get_blackboard())
    return _controller


def reset_blackboard_controller() -> None:
    """重置全局控制器单例（测试用）。"""
    global _controller
    with _controller_lock:
        _controller = None