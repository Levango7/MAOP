"""`maop.worker.agent_executor` 的单消息处理：ACK / NACK 语义与日志字段。

背景：这条链（`agent_tasks` 队列 → worker → `Dispatcher`）在本仓**没有生产者**，
所以下面两个 bug 一直没人发现 —— 但 MAOS 侧可能真的有生产者，代码本身得是对的。

修的两处：

1. **失败也 ACK**：原实现只在**抛异常**时 NACK，而 `Dispatcher.dispatch` 失败时是
   **正常返回**的（`DispatchResult.result.exit_code != 0`）。于是失败任务被 ACK 掉、
   永不重试 —— 与"nack 重试到上限再进死信"的设计意图相反。
2. **日志读错对象**：`getattr(result, "agent"/"exit_code")` 取的是 `DispatchResult`
   的属性，而这两个字段在 `result.result`（`MaopResult`）上，日志恒为
   `agent=unknown exit_code=-1`，排障时等于没有信息。
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from maop.core.reliability.error_schema import new_result
from maop.delegate.models import DispatchResult
from maop.worker.agent_executor import _process_message


class _FakeQueue:
    def __init__(self) -> None:
        self.acked: list[str] = []
        self.nacked: list[tuple[str, str]] = []

    def ack(self, msg_id: str, consumer_id: str = "") -> bool:
        self.acked.append(msg_id)
        return True

    def nack(self, msg_id: str, error: str = "") -> bool:
        self.nacked.append((msg_id, error))
        return True


class _Msg:
    def __init__(self, payload: dict, msg_id: str = "m-1") -> None:
        self.payload = payload
        self.id = msg_id


class _Dispatcher:
    def __init__(self, result: Any = None, raises: BaseException | None = None) -> None:
        self._result = result
        self._raises = raises
        self.calls: list[dict] = []

    async def dispatch(self, **kw) -> Any:
        self.calls.append(kw)
        if self._raises is not None:
            raise self._raises
        return self._result


def _ok_result(agent: str = "claude") -> DispatchResult:
    return DispatchResult(result=new_result(agent=agent, task="t", exit_code=0, stdout="ok"))


def _fail_result(exit_code: int = 7, error: str = "boom") -> DispatchResult:
    return DispatchResult(
        result=new_result(agent="claude", task="t", exit_code=exit_code, error=error)
    )


def _run(dispatcher: Any, queue: Any, payload: dict | None = None) -> None:
    asyncio.run(_process_message(
        queue, dispatcher, _Msg(payload or {"agent": "claude", "task": "do it"}), "w-1",
    ))


def test_success_is_acked() -> None:
    q = _FakeQueue()
    _run(_Dispatcher(_ok_result()), q)
    assert q.acked == ["m-1"]
    assert q.nacked == []


def test_nonzero_exit_is_nacked_not_acked() -> None:
    """核心回归：派发**正常返回但失败**时必须 NACK，否则失败任务永不重试。"""
    q = _FakeQueue()
    _run(_Dispatcher(_fail_result(exit_code=7, error="agent exploded")), q)
    assert q.acked == [], "失败的派发被 ACK 掉了 —— 任务会静默消失，永不重试"
    assert len(q.nacked) == 1
    msg_id, error = q.nacked[0]
    assert msg_id == "m-1"
    assert "agent exploded" in error, error


def test_permission_denied_exit_is_nacked_with_its_error() -> None:
    """权限门拒绝（126）同样是非 0 退出 —— 走 NACK；死信由队列的 max_retries 兜底。"""
    q = _FakeQueue()
    _run(_Dispatcher(_fail_result(exit_code=126, error="Permission denied: nope")), q)
    assert q.nacked and "Permission denied" in q.nacked[0][1]


def test_exception_is_nacked_and_does_not_propagate() -> None:
    """派发抛异常 → NACK，且异常不许冒出去把 worker 循环打死。"""
    q = _FakeQueue()
    _run(_Dispatcher(raises=RuntimeError("driver died")), q)
    assert q.acked == []
    assert len(q.nacked) == 1
    assert "driver died" in q.nacked[0][1]


def test_log_names_the_real_agent_and_exit_code(caplog: pytest.LogCaptureFixture) -> None:
    """日志必须给出真实 agent 与 exit_code（原先恒为 unknown / -1，排障时无信息）。"""
    import logging

    q = _FakeQueue()
    with caplog.at_level(logging.INFO, logger="maop.worker.agent_executor"):
        _run(_Dispatcher(_ok_result(agent="mavis")), q)
    text = "\n".join(r.getMessage() for r in caplog.records)
    assert "mavis" in text, text
    assert "exit_code=0" in text, text
    assert "unknown" not in text, text


def test_payload_fields_are_forwarded() -> None:
    q = _FakeQueue()
    d = _Dispatcher(_ok_result())
    _run(d, q, {"agent": "mavis", "task": "write tests", "routing_key": "codegen"})
    assert d.calls == [{
        "agent": "mavis", "task": "write tests", "routing_key": "codegen", "trace_id": "m-1",
    }]
