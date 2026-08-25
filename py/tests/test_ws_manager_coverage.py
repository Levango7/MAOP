"""Coverage tests for maop.dashboard._ws_manager — WebSocket 推送管理.

该模块在基线测试中覆盖率为 0%。本文件补充 _ws_broadcast 和 _ws_push_loop 的测试。
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from maop.dashboard import _ws_manager


@pytest.fixture(autouse=True)
def _reset_ws_state():
    """每个测试前后重置 _ws_clients 全局状态。"""
    _ws_manager._ws_clients.clear()
    yield
    _ws_manager._ws_clients.clear()


def _make_mock_ws() -> MagicMock:
    """创建一个 mock WebSocket。"""
    ws = MagicMock()
    ws.send_json = AsyncMock()
    return ws


class TestWsBroadcast:
    """测试 _ws_broadcast 函数。"""

    @pytest.mark.asyncio
    async def test_broadcast_no_clients(self):
        """没有客户端时立即返回。"""
        result = await _ws_manager._ws_broadcast({"type": "test"})
        assert result is None

    @pytest.mark.asyncio
    async def test_broadcast_to_single_client(self):
        """向单个客户端广播消息。"""
        ws = _make_mock_ws()
        _ws_manager._ws_clients.add(ws)
        await _ws_manager._ws_broadcast({"type": "test"})
        ws.send_json.assert_awaited_once_with({"type": "test"})

    @pytest.mark.asyncio
    async def test_broadcast_to_multiple_clients(self):
        """向多个客户端广播消息。"""
        ws1 = _make_mock_ws()
        ws2 = _make_mock_ws()
        _ws_manager._ws_clients.add(ws1)
        _ws_manager._ws_clients.add(ws2)
        await _ws_manager._ws_broadcast({"type": "test"})
        ws1.send_json.assert_awaited_once_with({"type": "test"})
        ws2.send_json.assert_awaited_once_with({"type": "test"})

    @pytest.mark.asyncio
    async def test_broadcast_removes_dead_client(self):
        """发送失败的客户端被移除。"""
        ws_good = _make_mock_ws()
        ws_bad = MagicMock()
        ws_bad.send_json = AsyncMock(side_effect=Exception("connection closed"))
        _ws_manager._ws_clients.add(ws_good)
        _ws_manager._ws_clients.add(ws_bad)
        await _ws_manager._ws_broadcast({"type": "test"})
        assert ws_good in _ws_manager._ws_clients
        assert ws_bad not in _ws_manager._ws_clients

    @pytest.mark.asyncio
    async def test_broadcast_all_dead_clients_removed(self):
        """所有发送失败的客户端都被移除。"""
        ws1 = MagicMock()
        ws1.send_json = AsyncMock(side_effect=Exception("closed"))
        ws2 = MagicMock()
        ws2.send_json = AsyncMock(side_effect=Exception("closed"))
        _ws_manager._ws_clients.add(ws1)
        _ws_manager._ws_clients.add(ws2)
        await _ws_manager._ws_broadcast({"type": "test"})
        assert len(_ws_manager._ws_clients) == 0


class TestWsPushLoop:
    """测试 _ws_push_loop 函数。"""

    @pytest.mark.asyncio
    async def test_push_loop_no_clients_skips(self):
        """没有客户端时跳过推送。"""
        # 用 asyncio.wait_for 设置超时，确保循环不会卡住
        task = asyncio.create_task(_ws_manager._ws_push_loop())
        await asyncio.sleep(0.2)  # 让循环跑一会
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    @pytest.mark.asyncio
    async def test_push_loop_cancelled_cleanly(self):
        """推送循环可以被干净地取消。"""
        task = asyncio.create_task(_ws_manager._ws_push_loop())
        await asyncio.sleep(0.1)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    @pytest.mark.asyncio
    async def test_push_loop_uses_cache_within_ttl(self):
        """在 TTL 内使用缓存的 snapshot。"""
        # 设置缓存
        _ws_manager._ws_snapshot_cache = {"type": "snapshot", "cached": True}
        _ws_manager._ws_snapshot_ts = asyncio.get_event_loop().time() + 1000  # 远未来

        ws = _make_mock_ws()
        _ws_manager._ws_clients.add(ws)

        task = asyncio.create_task(_ws_manager._ws_push_loop())
        await asyncio.sleep(0.2)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

        # 应该发送了缓存的消息
        if ws.send_json.await_count > 0:
            sent_msg = ws.send_json.await_args_list[0].args[0]
            assert sent_msg.get("cached") is True

        # 清理缓存
        _ws_manager._ws_snapshot_cache = None
        _ws_manager._ws_snapshot_ts = 0.0


class TestWsManagerConstants:
    """测试模块常量。"""

    def test_send_timeout(self):
        assert _ws_manager._WS_SEND_TIMEOUT == 5.0

    def test_snapshot_ttl(self):
        assert _ws_manager._WS_SNAPSHOT_TTL == 5.0

    def test_ws_clients_is_set(self):
        assert isinstance(_ws_manager._ws_clients, set)