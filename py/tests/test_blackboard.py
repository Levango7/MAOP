"""Tests for MAOP Blackboard Architecture.

Covers:
- Blackboard 基本操作（write/read/clear/subscribe/snapshot/history）
- 域白名单机制（R-8）：write 拒绝未定义域
- read_domains 校验（R-7）：拒绝无效域列表
- KnowledgeSource 抽象与 Controller 事件驱动触发
- EventBus 集成（C-3 publish(Event) API）
- API 路由（snapshot/domains/write/clear/history/stats）
- 向后兼容：黑板未启用 EventBus 时退化为纯内存
"""

from __future__ import annotations

import asyncio

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from maop.core.reliability.blackboard import (
    Blackboard,
    BlackboardDomain,
    BlackboardEntry,
    Controller,
    InvalidDomainError,
    KnowledgeSource,
    KnowledgeSourceError,
    get_blackboard,
    get_blackboard_controller,
    reset_blackboard,
    reset_blackboard_controller,
)
from maop.core.reliability.event_bus import Event, EventBus

# ── Fixtures ─────────────────────────────────────────────────────


@pytest.fixture
def bb() -> Blackboard:
    """提供一个干净的 Blackboard 实例（不接入 EventBus）。"""
    return Blackboard()


@pytest.fixture
def bb_with_bus() -> tuple[Blackboard, EventBus]:
    """提供一个接入 EventBus 的 Blackboard 实例。"""
    bus = EventBus()
    board = Blackboard(event_bus=bus)
    return board, bus


@pytest.fixture(autouse=True)
def _reset_global_singletons():
    """每个测试前后重置全局单例，避免污染。"""
    reset_blackboard()
    reset_blackboard_controller()
    yield
    reset_blackboard()
    reset_blackboard_controller()


# ── 1. BlackboardDomain 枚举 ─────────────────────────────────────


class TestBlackboardDomain:
    def test_all_domains_defined(self):
        expected = {
            "problem",
            "partial_solution",
            "hypothesis",
            "evidence",
            "decision",
            "constraint",
            "agent_contribution",
        }
        actual = {d.value for d in BlackboardDomain}
        assert actual == expected

    def test_is_str_enum(self):
        for d in BlackboardDomain:
            assert isinstance(d, str)
            assert isinstance(d.value, str)


# ── 2. BlackboardEntry 数据模型 ──────────────────────────────────


class TestBlackboardEntry:
    def test_default_factory_generates_uuid_and_timestamp(self):
        e = BlackboardEntry(domain="problem", content="x")
        assert e.entry_id  # non-empty UUID
        assert e.timestamp  # non-empty ISO-8601
        assert e.confidence == 1.0
        assert e.metadata == {}

    def test_to_dict_roundtrip(self):
        e = BlackboardEntry(
            domain="evidence",
            content={"k": "v"},
            contributor="ks1",
            confidence=0.8,
            metadata={"schema": "v1"},
        )
        d = e.to_dict()
        assert d["domain"] == "evidence"
        assert d["content"] == {"k": "v"}
        assert d["contributor"] == "ks1"
        assert d["confidence"] == 0.8
        assert d["metadata"] == {"schema": "v1"}
        assert "entry_id" in d and "timestamp" in d

    def test_unique_entry_ids(self):
        e1 = BlackboardEntry(domain="problem", content="a")
        e2 = BlackboardEntry(domain="problem", content="b")
        assert e1.entry_id != e2.entry_id


# ── 3. Blackboard 基本操作 ───────────────────────────────────────


class TestBlackboardBasic:
    async def test_write_and_read(self, bb: Blackboard):
        entry = await bb.write(
            "problem", "find root cause", contributor="agent-1"
        )
        assert entry.domain == "problem"
        assert entry.content == "find root cause"
        assert entry.contributor == "agent-1"

        entries = bb.read("problem")
        assert len(entries) == 1
        assert entries[0].entry_id == entry.entry_id

    async def test_write_multiple_entries(self, bb: Blackboard):
        await bb.write("problem", "p1", contributor="a")
        await bb.write("problem", "p2", contributor="b")
        await bb.write("evidence", "e1", contributor="c")
        assert len(bb.read("problem")) == 2
        assert len(bb.read("evidence")) == 1

    async def test_read_empty_domain(self, bb: Blackboard):
        entries = bb.read("problem")
        assert entries == []

    async def test_clear(self, bb: Blackboard):
        await bb.write("problem", "p1", contributor="a")
        await bb.write("problem", "p2", contributor="b")
        cleared = await bb.clear("problem")
        assert cleared == 2
        assert bb.read("problem") == []

    async def test_clear_empty_domain(self, bb: Blackboard):
        cleared = await bb.clear("problem")
        assert cleared == 0

    async def test_get_snapshot(self, bb: Blackboard):
        await bb.write("problem", "p1", contributor="a")
        await bb.write("evidence", "e1", contributor="b")
        snap = bb.get_snapshot()
        assert "problem" in snap
        assert "evidence" in snap
        assert len(snap["problem"]) == 1
        assert snap["problem"][0]["content"] == "p1"

    async def test_get_snapshot_empty(self, bb: Blackboard):
        snap = bb.get_snapshot()
        assert snap == {}

    async def test_get_history(self, bb: Blackboard):
        await bb.write("problem", "p1", contributor="a")
        await bb.clear("problem")
        history = bb.get_history()
        assert len(history) == 2
        assert history[0]["action"] == "write"
        assert history[1]["action"] == "clear"

    async def test_get_history_limit(self, bb: Blackboard):
        for i in range(5):
            await bb.write("problem", f"p{i}", contributor="a")
        history = bb.get_history(limit=3)
        assert len(history) == 3

    async def test_get_domains(self, bb: Blackboard):
        await bb.write("problem", "p1", contributor="a")
        await bb.write("evidence", "e1", contributor="b")
        domains = bb.get_domains()
        assert set(domains) == {"problem", "evidence"}

    async def test_total_entries(self, bb: Blackboard):
        await bb.write("problem", "p1", contributor="a")
        await bb.write("problem", "p2", contributor="a")
        await bb.write("evidence", "e1", contributor="b")
        assert bb.total_entries() == 3

    async def test_subscribe_sync_callback(self, bb: Blackboard):
        received: list[BlackboardEntry] = []

        def callback(entry: BlackboardEntry) -> None:
            received.append(entry)

        bb.subscribe("problem", callback)
        await bb.write("problem", "p1", contributor="a")
        assert len(received) == 1
        assert received[0].content == "p1"

    async def test_subscribe_async_callback(self, bb: Blackboard):
        received: list[BlackboardEntry] = []

        async def callback(entry: BlackboardEntry) -> None:
            received.append(entry)

        bb.subscribe("problem", callback)
        await bb.write("problem", "p1", contributor="a")
        assert len(received) == 1

    async def test_subscribe_only_triggered_for_subscribed_domain(
        self, bb: Blackboard
    ):
        received: list[BlackboardEntry] = []

        def callback(entry: BlackboardEntry) -> None:
            received.append(entry)

        bb.subscribe("problem", callback)
        await bb.write("evidence", "e1", contributor="a")
        assert received == []

    async def test_unsubscribe(self, bb: Blackboard):
        received: list[BlackboardEntry] = []

        def callback(entry: BlackboardEntry) -> None:
            received.append(entry)

        bb.subscribe("problem", callback)
        bb.unsubscribe("problem", callback)
        await bb.write("problem", "p1", contributor="a")
        assert received == []

    async def test_subscriber_error_does_not_block_write(
        self, bb: Blackboard
    ):
        def bad_callback(entry: BlackboardEntry) -> None:
            raise RuntimeError("subscriber failure")

        bb.subscribe("problem", bad_callback)
        entry = await bb.write("problem", "p1", contributor="a")
        assert entry.content == "p1"
        assert len(bb.read("problem")) == 1


# ── 4. 域白名单机制（R-8） ───────────────────────────────────────


class TestDomainWhitelist:
    async def test_write_rejects_invalid_domain(self, bb: Blackboard):
        with pytest.raises(InvalidDomainError) as exc_info:
            await bb.write("invalid_domain", "content")
        assert "invalid_domain" in str(exc_info.value)

    async def test_write_rejects_non_string_domain(self, bb: Blackboard):
        with pytest.raises(InvalidDomainError):
            await bb.write(123, "content")  # type: ignore[arg-type]

    async def test_read_rejects_invalid_domain(self, bb: Blackboard):
        with pytest.raises(InvalidDomainError):
            bb.read("invalid_domain")

    async def test_clear_rejects_invalid_domain(self, bb: Blackboard):
        with pytest.raises(InvalidDomainError):
            await bb.clear("invalid_domain")

    async def test_subscribe_rejects_invalid_domain(self, bb: Blackboard):
        with pytest.raises(InvalidDomainError):
            bb.subscribe("invalid_domain", lambda e: None)

    async def test_all_enum_domains_accepted(self, bb: Blackboard):
        for domain in BlackboardDomain:
            entry = await bb.write(domain.value, f"content-{domain.value}")
            assert entry.domain == domain.value


# ── 5. read_domains 校验（R-7） ──────────────────────────────────


class TestReadDomainsValidation:
    async def test_read_domains_returns_dict(self, bb: Blackboard):
        await bb.write("problem", "p1", contributor="a")
        await bb.write("evidence", "e1", contributor="b")
        result = bb.read_domains(["problem", "evidence"])
        assert isinstance(result, dict)
        assert len(result["problem"]) == 1
        assert len(result["evidence"]) == 1

    async def test_read_domains_empty_list(self, bb: Blackboard):
        result = bb.read_domains([])
        assert result == {}

    async def test_read_domains_rejects_non_list(self, bb: Blackboard):
        with pytest.raises(InvalidDomainError):
            bb.read_domains("problem")  # type: ignore[arg-type]

    async def test_read_domains_rejects_invalid_domain_in_list(
        self, bb: Blackboard
    ):
        with pytest.raises(InvalidDomainError) as exc_info:
            bb.read_domains(["problem", "invalid_domain"])
        assert "invalid_domain" in str(exc_info.value)

    async def test_read_domains_rejects_non_string_element(
        self, bb: Blackboard
    ):
        with pytest.raises(InvalidDomainError):
            bb.read_domains(["problem", 123])  # type: ignore[list-item]

    async def test_read_domains_rejects_all_invalid(self, bb: Blackboard):
        with pytest.raises(InvalidDomainError):
            bb.read_domains(["foo", "bar"])

    async def test_read_domains_returns_empty_for_unwritten_domain(
        self, bb: Blackboard
    ):
        result = bb.read_domains(["problem"])
        assert result == {"problem": []}


# ── 6. EventBus 集成（C-3 publish(Event) API） ──────────────────


class TestEventBusIntegration:
    async def test_enable_event_bus_publishes_write_event(
        self, bb_with_bus: tuple[Blackboard, EventBus]
    ):
        bb, bus = bb_with_bus
        received: list[Event] = []

        async def handler(event: Event) -> None:
            received.append(event)

        bus.subscribe("blackboard.write", handler)
        await bb.write("problem", "p1", contributor="agent-1")
        assert len(received) == 1
        evt = received[0]
        assert evt.topic == "blackboard.write"
        assert evt.source == "blackboard"
        assert evt.data["domain"] == "problem"
        assert evt.data["contributor"] == "agent-1"
        assert evt.data["action"] == "write"
        assert "entry_id" in evt.data

    async def test_disable_event_bus_no_publish(self, bb: Blackboard):
        bus = EventBus()
        bb.enable_event_bus(bus)
        bb.disable_event_bus()
        received: list[Event] = []

        async def handler(event: Event) -> None:
            received.append(event)

        bus.subscribe("blackboard.write", handler)
        await bb.write("problem", "p1", contributor="a")
        assert received == []
        assert not bb.event_bus_enabled

    async def test_clear_publishes_clear_event(
        self, bb_with_bus: tuple[Blackboard, EventBus]
    ):
        bb, bus = bb_with_bus
        received: list[Event] = []

        async def handler(event: Event) -> None:
            received.append(event)

        bus.subscribe("blackboard.clear", handler)
        await bb.write("problem", "p1", contributor="a")
        await bb.clear("problem")
        assert len(received) == 1
        evt = received[0]
        assert evt.topic == "blackboard.clear"
        assert evt.data["domain"] == "problem"
        assert evt.data["cleared_count"] == 1

    async def test_default_blackboard_no_event_bus(self):
        bb = Blackboard()
        assert not bb.event_bus_enabled

    async def test_event_bus_failure_does_not_block_write(
        self, bb_with_bus: tuple[Blackboard, EventBus]
    ):
        bb, bus = bb_with_bus

        async def bad_handler(event: Event) -> None:
            raise RuntimeError("handler failure")

        bus.subscribe("blackboard.write", bad_handler)
        entry = await bb.write("problem", "p1", contributor="a")
        assert entry.content == "p1"
        assert len(bb.read("problem")) == 1

    async def test_enable_event_bus_uses_global_singleton(self):
        bb = Blackboard()
        bb.enable_event_bus()
        assert bb.event_bus_enabled


# ── 7. KnowledgeSource ───────────────────────────────────────────


class TestKnowledgeSource:
    async def test_execute_raises_not_implemented(self, bb: Blackboard):
        ks = KnowledgeSource()
        ks.name = "test-ks"
        entry = BlackboardEntry(domain="problem", content="x")
        with pytest.raises(NotImplementedError):
            await ks.execute(bb, entry)

    async def test_custom_knowledge_source(self, bb: Blackboard):
        class MyKS(KnowledgeSource):
            name = "my-ks"
            priority = 5
            read_domains = ("problem",)
            write_domains = ("hypothesis",)

            async def execute(
                self, bb: Blackboard, trigger_entry: BlackboardEntry
            ) -> None:
                problems = bb.read("problem")
                for p in problems:
                    await bb.write(
                        "hypothesis", f"hyp-{p.content}", contributor=self.name
                    )

        ks = MyKS()
        await bb.write("problem", "p1", contributor="agent")
        trigger = bb.read("problem")[0]
        await ks.execute(bb, trigger)
        hypotheses = bb.read("hypothesis")
        assert len(hypotheses) == 1
        assert hypotheses[0].content == "hyp-p1"


# ── 8. Controller ────────────────────────────────────────────────


class TestController:
    async def test_register_and_list_ks(self, bb: Blackboard):
        ctrl = Controller(bb)
        ks = KnowledgeSource()
        ks.name = "test-ks"
        ctrl.register_ks(ks)
        assert "test-ks" in ctrl.registered_ks

    async def test_register_ks_empty_name_rejected(self, bb: Blackboard):
        ctrl = Controller(bb)
        ks = KnowledgeSource()  # name == ""
        with pytest.raises(KnowledgeSourceError):
            ctrl.register_ks(ks)

    async def test_unregister_ks(self, bb: Blackboard):
        ctrl = Controller(bb)
        ks = KnowledgeSource()
        ks.name = "test-ks"
        ctrl.register_ks(ks)
        ctrl.unregister_ks("test-ks")
        assert "test-ks" not in ctrl.registered_ks

    async def test_control_step_no_triggers(self, bb: Blackboard):
        ctrl = Controller(bb)
        executed = await ctrl.control_step()
        assert executed == 0
        assert ctrl.iteration_count == 1

    async def test_control_step_dispatches_to_matching_ks(
        self, bb: Blackboard
    ):
        executed_ks: list[str] = []

        class ProblemKS(KnowledgeSource):
            name = "problem-ks"
            priority = 1
            read_domains = ("problem",)
            write_domains = ("hypothesis",)

            async def execute(
                self, bb: Blackboard, trigger_entry: BlackboardEntry
            ) -> None:
                executed_ks.append(self.name)
                await bb.write(
                    "hypothesis", "generated", contributor=self.name
                )

        ctrl = Controller(bb)
        ctrl.register_ks(ProblemKS())

        # 模拟触发条目
        trigger = BlackboardEntry(domain="problem", content="trigger")
        ctrl.enqueue_trigger(trigger)
        executed = await ctrl.control_step()
        assert executed == 1
        assert executed_ks == ["problem-ks"]
        assert len(bb.read("hypothesis")) == 1

    async def test_control_step_priority_order(self, bb: Blackboard):
        order: list[str] = []

        class HighPriorityKS(KnowledgeSource):
            name = "high"
            priority = 10
            read_domains = ("problem",)
            write_domains = ()

            async def execute(
                self, bb: Blackboard, trigger_entry: BlackboardEntry
            ) -> None:
                order.append(self.name)

        class LowPriorityKS(KnowledgeSource):
            name = "low"
            priority = 1
            read_domains = ("problem",)
            write_domains = ()

            async def execute(
                self, bb: Blackboard, trigger_entry: BlackboardEntry
            ) -> None:
                order.append(self.name)

        ctrl = Controller(bb)
        ctrl.register_ks(LowPriorityKS())
        ctrl.register_ks(HighPriorityKS())

        trigger = BlackboardEntry(domain="problem", content="trigger")
        ctrl.enqueue_trigger(trigger)
        await ctrl.control_step()
        # 高优先级先执行
        assert order == ["high", "low"]

    async def test_control_step_wildcard_read_domains(
        self, bb: Blackboard
    ):
        executed: list[str] = []

        class WildcardKS(KnowledgeSource):
            name = "wildcard"
            priority = 1
            read_domains = ("*",)
            write_domains = ()

            async def execute(
                self, bb: Blackboard, trigger_entry: BlackboardEntry
            ) -> None:
                executed.append(self.name)

        ctrl = Controller(bb)
        ctrl.register_ks(WildcardKS())

        trigger = BlackboardEntry(domain="evidence", content="trigger")
        ctrl.enqueue_trigger(trigger)
        await ctrl.control_step()
        assert executed == ["wildcard"]

    async def test_control_step_ks_failure_does_not_block_others(
        self, bb: Blackboard
    ):
        executed: list[str] = []

        class GoodKS(KnowledgeSource):
            name = "good"
            priority = 1
            read_domains = ("problem",)
            write_domains = ()

            async def execute(
                self, bb: Blackboard, trigger_entry: BlackboardEntry
            ) -> None:
                executed.append(self.name)

        class BadKS(KnowledgeSource):
            name = "bad"
            priority = 2
            read_domains = ("problem",)
            write_domains = ()

            async def execute(
                self, bb: Blackboard, trigger_entry: BlackboardEntry
            ) -> None:
                raise RuntimeError("intentional failure")

        ctrl = Controller(bb)
        ctrl.register_ks(GoodKS())
        ctrl.register_ks(BadKS())

        trigger = BlackboardEntry(domain="problem", content="trigger")
        ctrl.enqueue_trigger(trigger)
        executed_count = await ctrl.control_step()
        # BadKS 失败，GoodKS 仍执行
        assert executed_count == 1
        assert executed == ["good"]

    async def test_max_iterations_limit(self, bb: Blackboard):
        ctrl = Controller(bb, max_iterations=2)
        await ctrl.control_step()
        await ctrl.control_step()
        assert ctrl.iteration_count == 2
        # 第三步应被跳过
        executed = await ctrl.control_step()
        assert executed == 0
        assert ctrl.iteration_count == 2  # 不再增加

    async def test_start_stop_with_event_bus(self, bb: Blackboard):
        bus = EventBus()
        ctrl = Controller(bb, event_bus=bus)
        await ctrl.start()
        assert ctrl.is_running
        await ctrl.stop()
        assert not ctrl.is_running

    async def test_event_bus_event_triggers_enqueue(
        self, bb: Blackboard
    ):
        bus = EventBus()
        # 黑板必须启用 EventBus 才能发布事件
        bb.enable_event_bus(bus)
        ctrl = Controller(bb, event_bus=bus)
        await ctrl.start()

        # 写入黑板，EventBus 应触发控制器入队
        await bb.write("problem", "p1", contributor="agent")
        # EventBus 异步分发，需要让事件循环处理
        await asyncio.sleep(0.01)
        assert ctrl.pending_triggers() >= 1
        await ctrl.stop()

    async def test_get_trace(self, bb: Blackboard):
        class SimpleKS(KnowledgeSource):
            name = "simple"
            priority = 1
            read_domains = ("problem",)
            write_domains = ()

            async def execute(
                self, bb: Blackboard, trigger_entry: BlackboardEntry
            ) -> None:
                pass

        ctrl = Controller(bb)
        ctrl.register_ks(SimpleKS())
        trigger = BlackboardEntry(domain="problem", content="trigger")
        ctrl.enqueue_trigger(trigger)
        await ctrl.control_step()
        trace = ctrl.get_trace()
        assert len(trace) == 1
        assert trace[0]["ks"] == "simple"
        assert trace[0]["status"] == "ok"

    async def test_is_converged(self, bb: Blackboard):
        ctrl = Controller(bb)
        # 未执行任何 step，未收敛
        assert not await ctrl.is_converged()
        await ctrl.control_step()
        # 已执行且队列为空，收敛
        assert await ctrl.is_converged()


# ── 9. 全局单例 ──────────────────────────────────────────────────


class TestGlobalSingleton:
    def test_get_blackboard_returns_same_instance(self):
        bb1 = get_blackboard()
        bb2 = get_blackboard()
        assert bb1 is bb2

    def test_get_blackboard_default_no_event_bus(self):
        bb = get_blackboard()
        assert not bb.event_bus_enabled

    def test_get_blackboard_controller_returns_same_instance(self):
        ctrl1 = get_blackboard_controller()
        ctrl2 = get_blackboard_controller()
        assert ctrl1 is ctrl2

    def test_reset_blackboard(self):
        bb1 = get_blackboard()
        reset_blackboard()
        bb2 = get_blackboard()
        assert bb1 is not bb2


# ── 10. 向后兼容 ─────────────────────────────────────────────────


class TestBackwardCompatibility:
    async def test_blackboard_without_event_bus_works(self):
        """黑板未启用 EventBus 时退化为纯内存，仍可正常读写。"""
        bb = Blackboard()
        assert not bb.event_bus_enabled
        entry = await bb.write("problem", "p1", contributor="a")
        assert bb.read("problem")[0].entry_id == entry.entry_id
        snap = bb.get_snapshot()
        assert snap["problem"][0]["content"] == "p1"

    async def test_global_blackboard_does_not_pollute_event_bus(self):
        """全局黑板默认不接入 EventBus，不影响现有 EventBus 订阅者。"""
        bb = get_blackboard()
        bus = EventBus()
        received: list[Event] = []

        async def handler(event: Event) -> None:
            received.append(event)

        bus.subscribe("blackboard.write", handler)
        await bb.write("problem", "p1", contributor="a")
        # 全局黑板未启用 EventBus，不应发布事件
        assert received == []


# ── 11. API 路由 ────────────────────────────────────────────────


class TestBlackboardRouter:
    @pytest.fixture
    def client(self, monkeypatch):
        """构造一个挂载 blackboard 路由的 TestClient，并绕过 admin 鉴权。"""
        # 重置全局单例，确保测试隔离
        reset_blackboard()
        reset_blackboard_controller()

        # Stub require_admin to no-op（测试无 auth 中间件）
        monkeypatch.setattr(
            "maop.dashboard.routers.blackboard.require_admin",
            lambda request: None,
        )

        app = FastAPI()
        from maop.dashboard.routers import blackboard as blackboard_router
        app.include_router(blackboard_router.router)
        return TestClient(app)

    def test_get_snapshot_empty(self, client: TestClient):
        resp = client.get("/api/blackboard/snapshot")
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "ok"
        assert body["data"] == {}

    def test_list_domains(self, client: TestClient):
        resp = client.get("/api/blackboard/domains")
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "ok"
        assert "problem" in body["data"]["allowed"]
        assert "evidence" in body["data"]["allowed"]
        assert body["data"]["active"] == []

    def test_read_domain_empty(self, client: TestClient):
        resp = client.get("/api/blackboard/domains/problem")
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "ok"
        assert body["data"] == []
        assert body["count"] == 0

    def test_read_domain_invalid(self, client: TestClient):
        resp = client.get("/api/blackboard/domains/invalid_domain")
        assert resp.status_code == 400

    def test_write_and_read(self, client: TestClient):
        resp = client.post(
            "/api/blackboard/write",
            json={
                "domain": "problem",
                "content": "find root cause",
                "contributor": "agent-1",
            },
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "ok"
        assert body["data"]["domain"] == "problem"
        assert body["data"]["content"] == "find root cause"

        # 读取验证
        resp = client.get("/api/blackboard/domains/problem")
        assert resp.status_code == 200
        body = resp.json()
        assert body["count"] == 1
        assert body["data"][0]["content"] == "find root cause"

    def test_write_invalid_domain(self, client: TestClient):
        resp = client.post(
            "/api/blackboard/write",
            json={"domain": "invalid", "content": "x"},
        )
        assert resp.status_code == 400

    def test_write_with_metadata_and_confidence(self, client: TestClient):
        resp = client.post(
            "/api/blackboard/write",
            json={
                "domain": "evidence",
                "content": {"metric": "cpu", "value": 0.95},
                "contributor": "monitor",
                "confidence": 0.85,
                "metadata": {"source": "prometheus"},
            },
        )
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["confidence"] == 0.85
        assert data["metadata"] == {"source": "prometheus"}

    def test_clear_domain(self, client: TestClient):
        # 先写入
        client.post(
            "/api/blackboard/write",
            json={"domain": "problem", "content": "p1"},
        )
        # 清除
        resp = client.post("/api/blackboard/clear/problem")
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "ok"
        assert body["cleared"] == 1
        # 验证已清空
        resp = client.get("/api/blackboard/domains/problem")
        assert resp.json()["count"] == 0

    def test_clear_invalid_domain(self, client: TestClient):
        resp = client.post("/api/blackboard/clear/invalid")
        assert resp.status_code == 400

    def test_get_history(self, client: TestClient):
        client.post(
            "/api/blackboard/write",
            json={"domain": "problem", "content": "p1"},
        )
        client.post(
            "/api/blackboard/write",
            json={"domain": "evidence", "content": "e1"},
        )
        resp = client.get("/api/blackboard/history")
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "ok"
        assert len(body["data"]) == 2
        assert body["data"][0]["action"] == "write"
        assert body["data"][1]["action"] == "write"

    def test_stats(self, client: TestClient):
        client.post(
            "/api/blackboard/write",
            json={"domain": "problem", "content": "p1"},
        )
        client.post(
            "/api/blackboard/write",
            json={"domain": "evidence", "content": "e1"},
        )
        resp = client.get("/api/blackboard/stats")
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "ok"
        assert body["data"]["total_entries"] == 2
        assert set(body["data"]["active_domains"]) == {"problem", "evidence"}
        assert "problem" in body["data"]["allowed_domains"]

    def test_snapshot_after_writes(self, client: TestClient):
        client.post(
            "/api/blackboard/write",
            json={"domain": "problem", "content": "p1"},
        )
        client.post(
            "/api/blackboard/write",
            json={"domain": "evidence", "content": "e1"},
        )
        resp = client.get("/api/blackboard/snapshot")
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert "problem" in data
        assert "evidence" in data
        assert len(data["problem"]) == 1
        assert len(data["evidence"]) == 1


# ── 12. 端到端：黑板 + EventBus + Controller ───────────────────


class TestEndToEnd:
    async def test_event_driven_pipeline(self):
        """端到端：黑板写入 → EventBus 事件 → Controller 入队 → KS 执行。"""
        bus = EventBus()
        bb = Blackboard(event_bus=bus)
        ctrl = Controller(bb, event_bus=bus)

        results: list[str] = []

        class AnalystKS(KnowledgeSource):
            name = "analyst"
            priority = 1
            read_domains = ("problem",)
            write_domains = ("hypothesis",)

            async def execute(
                self, bb: Blackboard, trigger_entry: BlackboardEntry
            ) -> None:
                results.append(f"analyzed:{trigger_entry.content}")
                await bb.write(
                    "hypothesis",
                    f"hyp-{trigger_entry.content}",
                    contributor=self.name,
                )

        ctrl.register_ks(AnalystKS())
        await ctrl.start()

        # 写入问题，触发整个流水线
        await bb.write("problem", "service-down", contributor="monitor")
        # 等待 EventBus 异步分发
        await asyncio.sleep(0.05)

        # 控制器应已入队触发条目
        assert ctrl.pending_triggers() >= 1

        # 执行控制步
        executed = await ctrl.control_step()
        assert executed == 1
        assert results == ["analyzed:service-down"]
        assert len(bb.read("hypothesis")) == 1

        await ctrl.stop()