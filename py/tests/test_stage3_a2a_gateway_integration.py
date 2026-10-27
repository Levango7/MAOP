"""Stage 3：A2AGateway 集成验证.

覆盖：
  1. Lifecycle：start/stop 幂等，桥接 + 传输层均被启停
  2. Card 管理：register_card / get_card / list_cards 委托给 A2AManager
  3. 任务：create_task 创建并返回 task_id
  4. 消息转发：on_a2a_message 委托给 bridge；bridge 为 None 时降级返回 None
  5. 远端通信：send_task / fetch_card 委托给 A2AClient；httpx 不可用时降级返回错误响应/None
  6. 全部层为 None 时：start/stop 不抛异常，register_card 静默返回

使用 tmp_path 隔离 SQLite，桥接/传输层用 MagicMock，不启动真实 HTTP 服务器。
"""

from __future__ import annotations

import asyncio
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from maop.core.agent.delegation.a2a import (
    A2ACard,
    A2AClient,
    A2AManager,
    A2AMessage,
    A2AResponse,
)
from maop.core.coordination.a2a_gateway import A2AGateway


class _FakeBridge:
    """模拟桥接层：记录 on_a2a_message 调用，start/stop 幂等."""

    def __init__(self) -> None:
        self.started = False
        self.stopped = False
        self.messages: list = []

    async def start(self) -> None:
        self.started = True

    async def stop(self) -> None:
        self.stopped = True

    async def on_a2a_message(self, message) -> str:
        self.messages.append(message)
        return "bb-entry-id"


class _FakeServer:
    """模拟传输层：记录 start/stop."""

    def __init__(self) -> None:
        self.started = False
        self.stopped = False

    async def start(self) -> None:
        self.started = True

    async def stop(self) -> None:
        self.stopped = True


class TestA2AGatewayLifecycle(unittest.TestCase):
    def test_start_stop_with_all_layers(self):
        """全部层存在：start/stop 分别启停桥接与传输层。"""
        bridge = _FakeBridge()
        server = _FakeServer()
        manager = A2AManager(root_dir=tempfile.mkdtemp())

        gw = A2AGateway(manager=manager, bridge=bridge, server=server)
        asyncio.run(gw.start())
        self.assertTrue(gw.started)
        self.assertTrue(bridge.started)
        self.assertTrue(server.started)

        asyncio.run(gw.stop())
        self.assertFalse(gw.started)
        self.assertTrue(bridge.stopped)
        self.assertTrue(server.stopped)

    def test_start_stop_idempotent(self):
        """start/stop 重复调用不抛异常。"""
        gw = A2AGateway()
        asyncio.run(gw.start())
        asyncio.run(gw.start())  # 再次 start 无-op
        self.assertTrue(gw.started)
        asyncio.run(gw.stop())
        asyncio.run(gw.stop())  # 再次 stop 无-op
        self.assertFalse(gw.started)

    def test_start_stop_all_none(self):
        """全部层为 None 时 start/stop 不抛异常。"""
        gw = A2AGateway()
        asyncio.run(gw.start(host="127.0.0.1", port=9999))
        self.assertTrue(gw.started)
        asyncio.run(gw.stop())
        self.assertFalse(gw.started)

    def test_start_bridge_failure_does_not_block(self):
        """桥接启动失败不阻塞 gateway start（best-effort）。"""

        class _BoomBridge:
            async def start(self):
                raise RuntimeError("bridge down")

        gw = A2AGateway(bridge=_BoomBridge())
        asyncio.run(gw.start())
        self.assertTrue(gw.started)
        asyncio.run(gw.stop())


class TestA2AGatewayCardManagement(unittest.TestCase):
    def setUp(self):
        self.manager = A2AManager(root_dir=tempfile.mkdtemp())
        self.gw = A2AGateway(manager=self.manager)

    def test_register_get_list_card(self):
        """register_card / get_card / list_cards 委托给 A2AManager。"""
        self.gw.register_card(
            "code-reviewer",
            description="reviews code",
            capabilities=["review", "suggest"],
            endpoint="http://reviewer:9090",
        )

        card = self.gw.get_card("code-reviewer")
        self.assertIsNotNone(card)
        self.assertEqual(card.name, "code-reviewer")
        self.assertEqual(card.capabilities, ["review", "suggest"])

        cards = self.gw.list_cards()
        self.assertEqual(len(cards), 1)
        self.assertEqual(cards[0].name, "code-reviewer")

    def test_register_card_no_manager(self):
        """manager 为 None 时 register_card 静默返回，不抛异常。"""
        gw = A2AGateway()
        gw.register_card("x")  # 不应抛
        self.assertIsNone(gw.get_card("x"))
        self.assertEqual(gw.list_cards(), [])

    def test_get_card_not_found(self):
        """不存在的卡片返回 None。"""
        self.assertIsNone(self.gw.get_card("nonexistent"))


class TestA2AGatewayTask(unittest.TestCase):
    def setUp(self):
        self.manager = A2AManager(root_dir=tempfile.mkdtemp())
        self.gw = A2AGateway(manager=self.manager)

    def test_create_task(self):
        """create_task 创建并返回带 task_id 的任务。"""
        task = self.gw.create_task("code-reviewer", "review this")
        self.assertIsNotNone(task)
        self.assertIsNotNone(task.task_id)
        self.assertEqual(task.status, "submitted")

        # manager 也能查到
        got = self.manager.get_task(task.task_id)
        self.assertIsNotNone(got)
        self.assertEqual(got.status, "submitted")

    def test_create_task_no_manager(self):
        """manager 为 None 时 create_task 返回 None。"""
        gw = A2AGateway()
        self.assertIsNone(gw.create_task("x", "msg"))


class TestA2AGatewayMessagePassthrough(unittest.TestCase):
    def test_on_a2a_message_delegates_to_bridge(self):
        """on_a2a_message 转发给 bridge，返回 bridge 的结果。"""
        bridge = _FakeBridge()
        gw = A2AGateway(bridge=bridge)

        msg = A2AMessage(method="tasks/send", params={"agent": "a", "message": "hi"})
        result = asyncio.run(gw.on_a2a_message(msg))
        self.assertEqual(result, "bb-entry-id")
        self.assertEqual(len(bridge.messages), 1)
        self.assertEqual(bridge.messages[0].id, msg.id)

    def test_on_a2a_message_no_bridge(self):
        """bridge 为 None 时 on_a2a_message 降级返回 None，不抛异常。"""
        gw = A2AGateway()
        result = asyncio.run(gw.on_a2a_message(A2AMessage()))
        self.assertIsNone(result)


class TestA2AGatewayRemoteCommunication(unittest.TestCase):
    def test_send_task_delegates_to_client(self):
        """send_task 将调用委托给 A2AClient.send（mock client）。"""
        gw = A2AGateway()

        expected = A2AResponse(id="msg-1", result={"task_id": "t-1", "status": "submitted"})
        with _patch_client_send(expected):
            resp = asyncio.run(gw.send_task("http://remote:9090", "reviewer", "do it"))
        self.assertEqual(resp.result.get("task_id"), "t-1")
        self.assertEqual(resp.result.get("status"), "submitted")

    def test_send_task_network_failure_degrades(self):
        """远端不可达时 send_task 返回错误响应，不抛异常。"""
        gw = A2AGateway()
        resp = asyncio.run(gw.send_task("http://bad-host:9090", "reviewer", "do it"))
        self.assertIsNotNone(resp.error)
        self.assertTrue(resp.error.get("message"))

    def test_fetch_card_delegates_to_client(self):
        """fetch_card 委托给 A2AClient.get_card。"""
        gw = A2AGateway()
        expected_card = A2ACard(name="reviewer", capabilities=["review"])
        with _patch_client_get_card(expected_card):
            card = asyncio.run(gw.fetch_card("http://remote:9090", "reviewer"))
        self.assertIsNotNone(card)
        self.assertEqual(card.name, "reviewer")

    def test_fetch_card_failure_returns_none(self):
        """fetch_card 异常时返回 None，不抛异常。"""
        gw = A2AGateway()
        card = asyncio.run(gw.fetch_card("http://bad:9090", "reviewer"))
        self.assertIsNone(card)


def _patch_client_send(response: A2AResponse):
    """将 A2AClient.send 替换为返回指定响应的 mock（上下文管理器）。"""

    class _Ctx:
        def __enter__(self):
            self.orig = A2AClient.send
            async def _fake_send(self_, endpoint, agent_name, message, *, timeout=30.0):
                return response
            A2AClient.send = _fake_send
            return self

        def __exit__(self, *exc):
            A2AClient.send = self.orig

    return _Ctx()


def _patch_client_get_card(card: A2ACard | None):
    """将 A2AClient.get_card 替换为返回指定卡片的 mock（上下文管理器）。"""

    class _Ctx:
        def __enter__(self):
            self.orig = A2AClient.get_card
            async def _fake_get_card(self_, endpoint, agent_name, *, timeout=10.0):
                return card
            A2AClient.get_card = _fake_get_card
            return self

        def __exit__(self, *exc):
            A2AClient.get_card = self.orig

    return _Ctx()


if __name__ == "__main__":
    unittest.main(verbosity=2)
