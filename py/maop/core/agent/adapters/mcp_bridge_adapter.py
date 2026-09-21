"""MCP 桥接适配器 — 将 MCPHub Server 包装为 ``AgentAdapter``。

复用现有 ``MCPHub`` 单例的连接/调用逻辑，不重复实现传输层。
由于 ``MCPHub`` 的方法是 ``async`` 而 ``AgentAdapter.execute`` 是同步的，
本适配器内部通过 :func:`_run_coro_sync` 将协程在同步上下文中运行。

Usage::

    from maop.core.mcp.mcp_hub import MCPHub
    from maop.core.agent.adapters import MCPBridgeAdapter, MCPBridgeAdapterConfig

    hub = MCPHub(root_dir="/path/to/MAOP")
    adapter = MCPBridgeAdapter(
        MCPBridgeAdapterConfig(server_id="abc123", tool_name="my_server.analyze"),
        mcp_hub=hub,
    )
    if adapter.connect():
        result = adapter.execute("Analyze this code")
"""
from __future__ import annotations

import asyncio
import concurrent.futures
import logging
from typing import Any

from pydantic import BaseModel

from maop.core.agent.delegation.agent_proxy import AgentAdapter

logger = logging.getLogger(__name__)


def _run_coro_sync(coro: Any) -> Any:
    """在同步上下文中运行协程并返回结果。

    若当前线程已有运行中的事件循环（如在被 async 测试调用时），
    通过线程池在新线程中 ``asyncio.run`` 避免嵌套循环错误。

    P2 修复：coroutine 可能引用主循环原语（绑定主循环的
    asyncio.Lock/Queue 等），在新事件循环中执行时抛 RuntimeError。
    包装执行函数捕获并记录 warning，异常向上传播由调用方兜底处理
    （connect/execute/health_check/disconnect 均有外层 try-except）。
    """
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None

    if loop is not None and loop.is_running():
        def _run_in_new_loop() -> Any:
            try:
                return asyncio.run(coro)
            except RuntimeError as re:
                logger.warning(
                    "[mcp_bridge] coroutine 在新循环执行时可能引用了"
                    "主循环原语: %s", re,
                )
                raise

        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            return pool.submit(_run_in_new_loop).result()
    return asyncio.run(coro)


class MCPBridgeAdapterConfig(BaseModel):
    """MCP 桥接适配器配置。

    Parameters
    ----------
    server_id : str
        已注册的 MCPHub Server ID。
    tool_name : str
        要调用的工具名（支持 ``"server.tool"`` 限定名或纯工具名）。
    timeout_s : float
        调用超时秒数（文档层面标注，实际超时由 MCPHub 控制）。
    """

    server_id: str = ""
    tool_name: str = ""
    timeout_s: float = 30.0

    model_config = {"extra": "forbid"}


class MCPBridgeAdapter(AgentAdapter):
    """将 MCPHub 的 Server 包装为 ``AgentAdapter``。

    Parameters
    ----------
    config : MCPBridgeAdapterConfig | None
        适配器配置。
    mcp_hub : Any
        MCPHub 实例。必须实现 ``health_check(server_id) -> bool``、
        ``call_tool_by_name(name, args) -> ToolResult``、
        ``disconnect(server_id) -> bool`` 异步方法。
    """

    def __init__(
        self,
        config: MCPBridgeAdapterConfig | None = None,
        mcp_hub: Any = None,
    ) -> None:
        self.config: MCPBridgeAdapterConfig = config or MCPBridgeAdapterConfig()
        self._mcp_hub = mcp_hub
        self._connected: bool = False

    # ------------------------------------------------------------------
    # AgentAdapter 实现
    # ------------------------------------------------------------------

    def connect(self) -> bool:
        """通过 MCPHub ``health_check`` 验证 Server 连接状态。"""
        if self._mcp_hub is None:
            logger.error("[mcp_bridge] No MCPHub instance provided")
            return False
        if not self.config.server_id:
            logger.error("[mcp_bridge] server_id is empty")
            return False
        try:
            healthy = _run_coro_sync(
                self._mcp_hub.health_check(self.config.server_id)
            )
            self._connected = bool(healthy)
            if self._connected:
                logger.info(
                    "[mcp_bridge] Connected to server %s", self.config.server_id
                )
            return self._connected
        except Exception as exc:
            logger.error("[mcp_bridge] Connect failed: %s", exc)
            self._connected = False
            return False

    def execute(self, task: str, **kwargs: Any) -> str:
        """调用 MCPHub 工具并返回文本结果。

        ``task`` 作为 ``{"prompt": task}`` 参数传入工具，额外 ``kwargs``
        合并到参数字典中。工具返回错误时抛出 ``RuntimeError``。
        """
        if not self._connected and not self.connect():
            raise RuntimeError(
                "MCPBridgeAdapter not connected — connect() failed"
            )

        arguments: dict[str, Any] = {"prompt": task}
        arguments.update(kwargs)

        result = _run_coro_sync(
            self._mcp_hub.call_tool_by_name(self.config.tool_name, arguments)
        )

        if getattr(result, "is_error", False):
            raise RuntimeError(
                f"MCP tool '{self.config.tool_name}' error: "
                f"{getattr(result, 'error_message', 'unknown')}"
            )

        # 从 ToolResult.content 列表中提取文本
        texts: list[str] = []
        content = getattr(result, "content", [])
        for item in content:
            if isinstance(item, dict):
                texts.append(item.get("text", str(item)))
            else:
                texts.append(str(item))
        return "\n".join(texts)

    def health_check(self) -> bool:
        """通过 MCPHub ``health_check`` 检查 Server 健康状态。"""
        if self._mcp_hub is None or not self.config.server_id:
            return False
        try:
            return bool(
                _run_coro_sync(
                    self._mcp_hub.health_check(self.config.server_id)
                )
            )
        except Exception:
            return False

    def sync_config(self, config: dict[str, Any]) -> None:
        """用新配置替换当前配置。"""
        self.config = MCPBridgeAdapterConfig(**config)
        self._connected = False

    def disconnect(self) -> None:
        """通过 MCPHub ``disconnect`` 断开 Server 连接。"""
        if self._mcp_hub is not None and self.config.server_id:
            try:
                _run_coro_sync(
                    self._mcp_hub.disconnect(self.config.server_id)
                )
            except Exception as exc:
                logger.warning("[mcp_bridge] Disconnect failed: %s", exc)
        self._connected = False