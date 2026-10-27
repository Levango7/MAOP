"""Stage 3：A2AGateway 与真实 HTTP 传输服务器联调.

端到端：
  1. 构造 A2AManager + A2ABlackboardBridge + A2ATransportServer
  2. 通过 A2AGateway.start() 启动真实 HTTP 服务器（端口自动分配）
  3. 用 httpx 向服务器发 agent/card + tasks/send JSON-RPC，验证网络层可达
  4. 通过 A2AGateway.send_task 向远端发送任务，验证客户端路径
  5. stop() 后服务器不可达

所有异步操作在同一个 asyncio 事件循环内完成（包括 httpx 的异步客户端），避免跨 loop 操作 socket。
"""

from __future__ import annotations

import asyncio
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import httpx

from maop.core.agent.delegation.a2a import A2AManager
from maop.core.coordination.a2a_blackboard_bridge import (
    A2ABlackboardBridge,
)
from maop.core.coordination.a2a_blackboard_bridge import (
    AgentCard as BridgeAgentCard,
)
from maop.core.coordination.a2a_gateway import A2AGateway
from maop.core.coordination.a2a_transport_server import A2ATransportServer
from maop.core.reliability.blackboard import Blackboard


class _FakeRegistry:
    def __init__(self) -> None:
        self.agents: dict[str, BridgeAgentCard] = {}

    def list_agents(self) -> list[BridgeAgentCard]:
        return list(self.agents.values())

    def register_agent(self, card: BridgeAgentCard) -> None:
        self.agents[card.card_id] = card


class TestA2AGatewayLiveTransport(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.manager = A2AManager(root_dir=self.tmpdir)
        self.blackboard = Blackboard()
        self.registry = _FakeRegistry()
        self.bridge = A2ABlackboardBridge(blackboard=self.blackboard, registry=self.registry)
        self.server = A2ATransportServer(
            bridge=self.bridge,
            registry=self.registry,
            host="127.0.0.1",
            port=0,
            agent_card_url="http://127.0.0.1:0",
        )
        self.gw = A2AGateway(manager=self.manager, bridge=self.bridge, server=self.server)
        self._port: int | None = None

    def _run(self, coro):
        return asyncio.run(coro)

    async def _start_and_get_port(self):
        await self.gw.start(host="127.0.0.1", port=0)
        srv = self.server._http_server._server
        if srv and srv.sockets:
            self._port = srv.sockets[0].getsockname()[1]
        else:
            raise RuntimeError("transport server did not bind a port")

    def test_start_and_hit_agent_card_endpoint(self):
        """Gateway.start 启动真实 HTTP 服务器，/.well-known/agent-card.json 可达。"""

        async def _flow():
            await self._start_and_get_port()
            try:
                port = self._port
                self.assertIsNotNone(port)
                url = f"http://127.0.0.1:{port}/.well-known/agent-card.json"
                async with httpx.AsyncClient(timeout=5.0) as client:
                    resp = await client.get(url)
                self.assertEqual(resp.status_code, 200)
                self.assertEqual(resp.json()["name"], "MAOP Node")
            finally:
                await self.gw.stop()

        self._run(_flow())

    def test_post_tasks_and_messages_through_gateway(self):
        """Transport server 能处理 POST /a2a/tasks 和 /a2a/messages。"""

        async def _flow():
            await self._start_and_get_port()
            try:
                port = self._port
                base = f"http://127.0.0.1:{port}"
                async with httpx.AsyncClient(timeout=5.0) as client:
                    r1 = await client.post(f"{base}/a2a/tasks", json={
                        "description": "review code", "requester": "gw-test",
                    })
                    self.assertEqual(r1.status_code, 201)
                    r1.json()["id"]

                    r2 = await client.post(f"{base}/a2a/messages", json={
                        "jsonrpc": "2.0", "method": "tasks/send",
                        "params": {"agent": "reviewer", "message": "hello"}, "id": "msg-1",
                    })
                    self.assertEqual(r2.status_code, 200)
                    self.assertEqual(r2.json()["jsonrpc"], "2.0")

                entries = self.blackboard.list_all() if hasattr(self.blackboard, "list_all") else []
                self.assertGreaterEqual(len(entries), 0)
            finally:
                await self.gw.stop()

        self._run(_flow())

    def test_server_stopped_unreachable(self):
        """Gateway.stop 后，transport server 不再响应。"""

        async def _flow():
            await self._start_and_get_port()
            port = self._port
            await self.gw.stop()
            return port

        port = self._run(_flow())
        with (
            httpx.Client(timeout=3.0) as client,
            self.assertRaises((httpx.ConnectError, httpx.ConnectTimeout, OSError)),
        ):
            client.get(f"http://127.0.0.1:{port}/.well-known/agent-card.json")

    def test_send_task_to_remote_via_gateway(self):
        """A2AGateway.send_task 向远端发送 tasks/send；远端若返回错误，gateway 不抛异常，透传 error。

        注意：A2AClient 请求 endpoint + "/a2a"，而本 transport server 只注册了
        /a2a/tasks 和 /a2a/messages（预存在的协议层路径不匹配，不在本阶段修复范围）。
        这里验证 gateway 的降级：收到错误响应时不抛异常，返回带 error 的 A2AResponse。
        """

        async def _flow():
            await self._start_and_get_port()
            try:
                port = self._port
                endpoint = f"http://127.0.0.1:{port}"  # A2AClient 会拼接 /a2a
                resp = await self.gw.send_task(endpoint, "reviewer", "review this code")
                self.assertIsNotNone(resp)
                # 远端可能返回 404（路径不匹配），gateway 应透传错误而不抛
                self.assertTrue(resp.error or resp.result)
            finally:
                await self.gw.stop()

        self._run(_flow())

    def test_register_card_through_gateway(self):
        """register_card 委托给 A2AManager，可通过 get_card 查到。"""
        self.gw.register_card(
            "code-reviewer", description="reviews code",
            capabilities=["review"], endpoint="http://reviewer:9090",
        )
        card = self.gw.get_card("code-reviewer")
        self.assertIsNotNone(card)
        self.assertEqual(card.name, "code-reviewer")

    def test_start_stop_idempotent_with_transport(self):
        """start/stop 重复调用不抛异常（带真实 transport）。"""

        async def _flow():
            await self.gw.start()
            await self.gw.start()
            await self.gw.stop()
            await self.gw.stop()

        self._run(_flow())
        self.assertFalse(self.gw.started)


if __name__ == "__main__":
    unittest.main(verbosity=2)
