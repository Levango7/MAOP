"""MAOP A2A Gateway — 统一门户，整合 A2A 协议三层组件。

将 A2AManager（协议层：卡片/任务/消息处理）、
A2ABlackboardBridge（黑板桥接：A2A <-> Blackboard 双向同步）、
A2ATransportServer（HTTP 传输：供外部 A2A 节点发现与通信）
聚合为单一对外入口，供调度层与代理层统一调用。

Usage::

    from maop.core.coordination.a2a_gateway import A2AGateway
    from maop.core.coordination.a2a_blackboard_bridge import A2ABlackboardBridge
    from maop.core.coordination.a2a_transport_server import A2ATransportServer
    from maop.core.agent.delegation.a2a import A2AManager

    manager = A2AManager(root_dir="/path/to/MAOP")
    bridge  = A2ABlackboardBridge(blackboard=bb, registry=registry)
    server  = A2ATransportServer(bridge=bridge, registry=registry, port=9090)

    gateway = A2AGateway(manager=manager, bridge=bridge, server=server)
    await gateway.start(host="0.0.0.0", port=9090)

    # 向远端 A2A 节点发送任务
    resp = await gateway.send_task("http://remote:9090", "code-reviewer", "review this code")

    await gateway.stop()

边界（阶段2 P1-4）：
  * 只负责 A2A 协议的统一对外入口与生命周期管理。
  * 不负责 DAG 分层、Redis Streams 分发（DistributedScheduler 职责）。
  * 不负责单 agent 直接执行（Dispatcher 职责）。
  * 三层组件可独立注入，任意一层为 None 时自动降级（不阻塞其他层）。
"""

from __future__ import annotations

import logging
from typing import Any

from maop.core.agent.delegation.a2a import A2AClient, A2AManager, A2AResponse
from maop.core.coordination.a2a_blackboard_bridge import (
    A2ABlackboardBridge,
)
from maop.core.coordination.a2a_transport_server import A2ATransportServer

logger = logging.getLogger(__name__)


class A2AGateway:
    """A2A 协议统一门户。

    聚合 A2AManager / A2ABlackboardBridge / A2ATransportServer，
    提供统一的 start / stop / register_card / send_task / get_card API。

    Parameters
    ----------
    manager : A2AManager | None
        协议层：卡片注册、任务状态、消息处理。可为 None（降级为无协议层）。
    bridge : A2ABlackboardBridge | None
        黑板桥接：A2A 消息与黑板的双向同步。可为 None。
    server : A2ATransportServer | None
        HTTP 传输层：供外部节点发现与通信。可为 None。
    """

    def __init__(
        self,
        manager: A2AManager | None = None,
        bridge: A2ABlackboardBridge | None = None,
        server: A2ATransportServer | None = None,
    ) -> None:
        self._manager = manager
        self._bridge = bridge
        self._server = server
        self._started = False

    # ── Lifecycle ──────────────────────────────────────────────

    async def start(
        self,
        host: str = "0.0.0.0",  # nosec B104 — 节点间 A2A 通信需对外监听（同 a2a_transport_server），部署侧用网络策略收敛
        port: int = 9090,
        agent_card_url: str = "",
    ) -> None:
        """启动所有已注入的层（桥接 + HTTP 服务器）。"""
        if self._bridge is not None:
            try:
                await self._bridge.start()
                logger.info("[a2a-gateway] bridge started")
            except Exception as exc:  # silent-ok: best-effort start
                logger.warning("[a2a-gateway] bridge start failed: %s", exc)

        if self._server is not None:
            # 允许覆盖端口/主机（server 已构造时以构造参数为准，此处仅记录）。
            try:
                await self._server.start()
                logger.info("[a2a-gateway] transport server started on %s:%d", host, port)
            except Exception as exc:  # silent-ok: best-effort start
                logger.warning("[a2a-gateway] transport server start failed: %s", exc)

        self._started = True
        logger.info("[a2a-gateway] started")

    async def stop(self) -> None:
        """停止所有已启动的层。"""
        if self._server is not None:
            try:
                await self._server.stop()
                logger.info("[a2a-gateway] transport server stopped")
            except Exception as exc:  # silent-ok: best-effort stop
                logger.warning("[a2a-gateway] transport server stop failed: %s", exc)

        if self._bridge is not None:
            try:
                await self._bridge.stop()
                logger.info("[a2a-gateway] bridge stopped")
            except Exception as exc:  # silent-ok: best-effort stop
                logger.warning("[a2a-gateway] bridge stop failed: %s", exc)

        self._started = False
        logger.info("[a2a-gateway] stopped")

    @property
    def started(self) -> bool:
        return self._started

    # ── Card / task management (delegate to A2AManager) ────────

    def register_card(self, name: str, description: str = "", capabilities: list[str] | None = None,
                      endpoint: str = "", version: str = "1.0.0",
                      provider: str = "maop") -> None:
        """注册本地 A2A 代理卡片（委托 A2AManager）。"""
        if self._manager is None:
            logger.warning("[a2a-gateway] register_card: no A2AManager configured")
            return
        from maop.core.agent.delegation.a2a import A2ACard

        card = A2ACard(
            name=name,
            description=description,
            capabilities=capabilities or [],
            endpoint=endpoint,
            version=version,
            provider=provider,
        )
        self._manager.register_card(card)
        logger.info("[a2a-gateway] registered card: %s", name)

    def get_card(self, name: str) -> Any | None:
        """查询本地已注册的 A2A 卡片（委托 A2AManager）。"""
        if self._manager is None:
            return None
        return self._manager.get_card(name)

    def list_cards(self) -> list[Any]:
        """列出所有本地已注册的 A2A 卡片（委托 A2AManager）。"""
        if self._manager is None:
            return []
        return self._manager.list_cards()

    def create_task(self, agent_name: str, message: str) -> Any | None:
        """创建一个 A2A 任务（委托 A2AManager，仅持久化不执行）。"""
        if self._manager is None:
            return None
        return self._manager.create_task(agent_name, message)

    # ── Message sending (delegate to A2AClient) ────────────────

    async def send_task(
        self,
        endpoint: str,
        agent_name: str,
        message: str,
        *,
        timeout: float = 30.0,
    ) -> Any:
        """向远端 A2A 节点发送 tasks/send 任务（委托 A2AClient）。

        Returns the A2AResponse from the remote node.
        """
        client = A2AClient()
        try:
            return await client.send(endpoint, agent_name, message, timeout=timeout)
        except Exception as exc:  # silent-ok: 网络/httpx 异常不应阻断调用方
            logger.error("[a2a-gateway] send_task failed: %s", exc)
            return A2AResponse(error={"code": -32000, "message": str(exc)})
        finally:
            # A2AClient 无需显式关闭（每次 send 独立 httpx 会话）。
            pass

    async def fetch_card(
        self,
        endpoint: str,
        agent_name: str,
        *,
        timeout: float = 10.0,
    ) -> Any | None:
        """从远端 A2A 节点获取 AgentCard（委托 A2AClient）。"""
        client = A2AClient()
        try:
            return await client.get_card(endpoint, agent_name, timeout=timeout)
        finally:
            pass

    # ── Bridge passthrough ─────────────────────────────────────

    async def on_a2a_message(self, message: Any) -> Any | None:
        """将入站 A2A 消息写入黑板（委托 A2ABlackboardBridge.on_a2a_message）。"""
        if self._bridge is None:
            logger.warning("[a2a-gateway] on_a2a_message: no bridge configured")
            return None
        return await self._bridge.on_a2a_message(message)

    # ── Accessors ──────────────────────────────────────────────

    @property
    def manager(self) -> A2AManager | None:
        return self._manager

    @property
    def bridge(self) -> A2ABlackboardBridge | None:
        return self._bridge

    @property
    def server(self) -> A2ATransportServer | None:
        return self._server


__all__ = ["A2AGateway"]
