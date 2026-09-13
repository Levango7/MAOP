"""E2E 测试：SSE error event 异常脱敏（第五轮 P0 修复）。

验证当 agent 执行抛异常时，通过 EventBus 发布的 SSE error event 中
error 字段为通用消息 "Internal server error"，不泄露异常详情
（堆栈跟踪、数据库连接字符串、文件路径等敏感信息）。

修复背景（maop_execute.py P1-SSE fix）：
  dispatch 抛异常时，详细异常仅记录到 logger，对外 SSE error event
  只发送 {"error": "Internal server error"}，避免信息泄露。

测试策略：
  - 直接调用 maop_execute（端到端入口）
  - mock dispatcher.dispatch 抛含敏感信息的 RuntimeError
  - 订阅 EventBus 捕获 agent.{trace_id}.error 事件
  - 断言 error 事件 data["error"] == "Internal server error"
  - 断言不包含敏感信息（postgres 连接字符串、密码等）
"""

from __future__ import annotations

import asyncio
import os

# 在导入 app 前设置测试环境
os.environ.setdefault("MAOP_ENV", "test")

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from maop.core.reliability.event_bus import Event, get_event_bus
from maop.core.reliability.error_schema import MaopResult, new_result
from maop.delegate.models import DispatchResult


# -- 辅助：等待 EventBus fire-and-forget 任务完成 -------------------

async def _drain_event_bus(bus, max_iters: int = 200) -> None:
    """等待 EventBus 的 pending publish tasks 完成。

    publish_sync 在有运行中 event loop 时使用 asyncio.ensure_future
    创建 fire-and-forget 任务发布事件。这些任务需要 event loop 调度
    才会执行。本函数通过反复 await sleep(0) 让出控制权，直到所有
    pending tasks 完成或达到最大迭代次数。
    """
    for _ in range(max_iters):
        if not bus._pending_publish_tasks:
            break
        await asyncio.sleep(0.001)
    # 额外让出一轮确保 handler 回调执行完毕
    await asyncio.sleep(0)


# -- 敏感信息常量 ---------------------------------------------------

# 模拟异常中包含的敏感数据库连接字符串
_SENSITIVE_DSN = "postgres://user:pass@host:5432/db"
# 模拟异常消息
_SENSITIVE_ERROR_MSG = f"database connection failed: {_SENSITIVE_DSN}"
# 异常中可能出现的敏感片段列表
_SENSITIVE_FRAGMENTS = [
    _SENSITIVE_DSN,
    "postgres://user:pass",
    "pass@host",
    "user:pass",
    "5432/db",
]


# -- 辅助：捕获 EventBus 事件的订阅器 -------------------------------


class _EventCapture:
    """捕获指定 trace_id 的所有 agent 事件。"""

    def __init__(self, trace_id: str) -> None:
        self.trace_id = trace_id
        self.events: list[Event] = []

    def __call__(self, event: Event) -> None:
        """同步事件处理器。"""
        self.events.append(event)

    @property
    def error_events(self) -> list[Event]:
        """返回所有 error 类型的事件。"""
        return [e for e in self.events if e.topic.endswith(".error")]

    @property
    def meta_events(self) -> list[Event]:
        """返回所有 meta 类型的事件。"""
        return [e for e in self.events if e.topic.endswith(".meta")]


# -- Fixtures -------------------------------------------------------


@pytest.fixture
def clean_event_bus():
    """每个测试前后清理全局 EventBus，避免跨测试事件污染。"""
    bus = get_event_bus()
    bus.clear()
    yield bus
    bus.clear()


@pytest.fixture
def mock_dispatcher_raises_sensitive_error():
    """创建 mock dispatcher，其 dispatch 方法抛含敏感信息的 RuntimeError。

    模拟 agent 执行失败，异常消息中包含数据库连接字符串等敏感信息。
    """
    dispatcher = MagicMock()
    dispatcher.dispatch = AsyncMock(
        side_effect=RuntimeError(_SENSITIVE_ERROR_MSG)
    )
    return dispatcher


# -- 测试：SSE error event 脱敏 ------------------------------------


class TestSseErrorSanitization:
    """验证 SSE error event 不泄露异常详情（P1-SSE fix）。"""

    async def test_error_event_contains_generic_message(
        self, clean_event_bus, mock_dispatcher_raises_sensitive_error,
    ):
        """SSE error event 的 error 字段为通用 "Internal server error"。

        当 dispatch 抛异常时，对外发布的 error 事件应只包含通用错误消息，
        而非异常详情（可能含堆栈跟踪、连接字符串等）。
        """
        trace_id = "test-sse-sanitize-001"
        capture = _EventCapture(trace_id)
        bus = clean_event_bus
        # 订阅该 trace_id 的所有 agent 事件（通配符）
        bus.subscribe(f"agent.{trace_id}.*", capture)

        # mock 掉 permission/hooks 检查（放行）和成本护栏（放行），
        # 聚焦于 SSE 脱敏逻辑本身
        with patch(
            "maop.maop_execute._check_permission_and_hooks",
            return_value=None,
        ), patch(
            "maop.core.personal_cost_guard.PersonalCostGuard"
        ) as mock_cost_guard:
            mock_cost_guard.return_value.check_new_call.return_value = (True, "")
            from maop.maop_execute import maop_execute

            result = await maop_execute(
                agent="test-agent",
                task="查询数据库",
                trace_id=trace_id,
                dispatcher=mock_dispatcher_raises_sensitive_error,
            )

        # 等待 EventBus fire-and-forget 任务完成
        await _drain_event_bus(bus)

        # 验证执行结果为失败
        assert result.exit_code == -1, f"期望 exit_code=-1，实际 {result.exit_code}"

        # 验证捕获到 error 事件
        error_events = capture.error_events
        assert len(error_events) >= 1, (
            f"期望至少 1 个 error 事件，实际捕获 {len(error_events)} 个。"
            f"所有事件: {[(e.topic, e.data) for e in capture.events]}"
        )

        error_data = error_events[0].data
        assert "error" in error_data, f"error 事件缺少 'error' 字段: {error_data}"

        # 核心断言：error 字段为通用消息
        assert error_data["error"] == "Internal server error", (
            f"error 字段应为 'Internal server error'，实际为: {error_data['error']!r}"
        )

    async def test_error_event_excludes_sensitive_info(
        self, clean_event_bus, mock_dispatcher_raises_sensitive_error,
    ):
        """SSE error event 不包含敏感信息（连接字符串、密码等）。

        异常消息中包含 "postgres://user:pass@host:5432/db"，
        但 SSE error event 的 data 中不应出现任何敏感片段。
        """
        trace_id = "test-sse-sanitize-002"
        capture = _EventCapture(trace_id)
        bus = clean_event_bus
        bus.subscribe(f"agent.{trace_id}.*", capture)

        with patch(
            "maop.maop_execute._check_permission_and_hooks",
            return_value=None,
        ), patch(
            "maop.core.personal_cost_guard.PersonalCostGuard"
        ) as mock_cost_guard:
            mock_cost_guard.return_value.check_new_call.return_value = (True, "")
            from maop.maop_execute import maop_execute

            await maop_execute(
                agent="test-agent",
                task="查询数据库",
                trace_id=trace_id,
                dispatcher=mock_dispatcher_raises_sensitive_error,
            )

        # 等待 EventBus fire-and-forget 任务完成
        await _drain_event_bus(bus)

        error_events = capture.error_events
        assert len(error_events) >= 1, "未捕获到 error 事件"

        # 将整个 error 事件 data 序列化为字符串，检查不包含任何敏感片段
        import json
        error_data_str = json.dumps(
            error_events[0].data, ensure_ascii=False, default=str
        )

        for fragment in _SENSITIVE_FRAGMENTS:
            assert fragment not in error_data_str, (
                f"SSE error event data 泄露敏感信息: 包含 '{fragment}'。"
                f"完整 data: {error_data_str}"
            )

    async def test_error_event_excludes_stack_trace(
        self, clean_event_bus,
    ):
        """SSE error event 不包含堆栈跟踪信息。

        异常的堆栈跟踪可能包含文件路径、行号等内部实现细节，
        不应通过 SSE 事件泄露给客户端。
        """
        trace_id = "test-sse-sanitize-003"
        capture = _EventCapture(trace_id)
        bus = clean_event_bus
        bus.subscribe(f"agent.{trace_id}.*", capture)

        # 异常中包含文件路径等堆栈跟踪特征
        sensitive_path = "/etc/maop/secrets/db_config.yml"
        dispatcher = MagicMock()
        dispatcher.dispatch = AsyncMock(
            side_effect=FileNotFoundError(f"Config not found: {sensitive_path}")
        )

        with patch(
            "maop.maop_execute._check_permission_and_hooks",
            return_value=None,
        ), patch(
            "maop.core.personal_cost_guard.PersonalCostGuard"
        ) as mock_cost_guard:
            mock_cost_guard.return_value.check_new_call.return_value = (True, "")
            from maop.maop_execute import maop_execute

            await maop_execute(
                agent="test-agent",
                task="加载配置",
                trace_id=trace_id,
                dispatcher=dispatcher,
            )

        # 等待 EventBus fire-and-forget 任务完成
        await _drain_event_bus(bus)

        error_events = capture.error_events
        assert len(error_events) >= 1, "未捕获到 error 事件"

        import json
        error_data_str = json.dumps(
            error_events[0].data, ensure_ascii=False, default=str
        )

        # 文件路径不应出现在 SSE error event 中
        assert sensitive_path not in error_data_str, (
            f"SSE error event 泄露文件路径: 包含 '{sensitive_path}'。"
            f"完整 data: {error_data_str}"
        )
        assert "secrets" not in error_data_str.lower() or "Internal server error" in error_data_str, (
            f"SSE error event 可能泄露敏感路径信息: {error_data_str}"
        )

    async def test_meta_event_still_emitted_on_error(
        self, clean_event_bus, mock_dispatcher_raises_sensitive_error,
    ):
        """异常发生前 meta 事件仍正常发布。

        验证脱敏修复不影响正常事件流：meta 事件在 dispatch 前发布，
        error 事件在 dispatch 异常后发布，两者都应存在。
        """
        trace_id = "test-sse-sanitize-004"
        capture = _EventCapture(trace_id)
        bus = clean_event_bus
        bus.subscribe(f"agent.{trace_id}.*", capture)

        with patch(
            "maop.maop_execute._check_permission_and_hooks",
            return_value=None,
        ), patch(
            "maop.core.personal_cost_guard.PersonalCostGuard"
        ) as mock_cost_guard:
            mock_cost_guard.return_value.check_new_call.return_value = (True, "")
            from maop.maop_execute import maop_execute

            await maop_execute(
                agent="test-agent",
                task="查询数据库",
                trace_id=trace_id,
                dispatcher=mock_dispatcher_raises_sensitive_error,
            )

        # 等待 EventBus fire-and-forget 任务完成
        await _drain_event_bus(bus)

        # meta 事件应在 dispatch 前发布
        assert len(capture.meta_events) >= 1, (
            "期望 dispatch 前发布 meta 事件，但未捕获到"
        )
        # error 事件应在 dispatch 异常后发布
        assert len(capture.error_events) >= 1, (
            "期望 dispatch 异常后发布 error 事件，但未捕获到"
        )