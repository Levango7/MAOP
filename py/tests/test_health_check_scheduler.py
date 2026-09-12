"""HealthCheckScheduler 白盒测试.

覆盖：
  - check_agent: 健康 / 不健康 / 无适配器 / 异常 / 超时
  - check_once: 全量检查 / 空 catalog
  - start/stop: 启动停止 / 幂等 / 未启动停止
  - get_status: 状态查询
  - check_interval: 间隔验证
  - timeout: 超时保护

每个测试使用 tmp_path 隔离 SQLite，不触碰真实 DB.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

import pytest

from maop.core.agent.delegation.agent_proxy import AgentAdapter
from maop.core.agent.registry.agent_catalog import AgentCatalog, AgentDescriptor
from maop.core.agent.router.health_check_scheduler import (
    HealthCheckScheduler,
    HealthStatus,
)


# ── 测试用 Mock 适配器 ───────────────────────────────────────────


class _MockAdapter(AgentAdapter):
    """测试用 Mock 适配器，可控制 health_check 返回值、异常和延迟."""

    def __init__(
        self,
        healthy: bool = True,
        raise_exc: BaseException | None = None,
        delay: float = 0.0,
    ) -> None:
        self._healthy = healthy
        self._raise = raise_exc
        self._delay = delay
        self.call_count = 0

    def connect(self) -> bool:
        return True

    def execute(self, task: str, **kwargs: Any) -> str:
        return ""

    def health_check(self) -> bool:
        self.call_count += 1
        if self._delay > 0:
            time.sleep(self._delay)
        if self._raise is not None:
            raise self._raise
        return self._healthy

    def sync_config(self, config: dict[str, Any]) -> None:
        pass

    def disconnect(self) -> None:
        pass


# ── Fixtures ─────────────────────────────────────────────────────


@pytest.fixture
def catalog(tmp_path: Path) -> AgentCatalog:
    """使用隔离 tmp_path DB 的 AgentCatalog."""
    return AgentCatalog(db_path=tmp_path / "agent_catalog.db")


def _desc(name: str, **kwargs: object) -> AgentDescriptor:
    """构造 AgentDescriptor，name 必填，其余可选覆盖."""
    return AgentDescriptor(name=name, **kwargs)  # type: ignore[arg-type]


# ── 1. check_agent — 健康 ────────────────────────────────────────


def test_check_agent_healthy(catalog: AgentCatalog) -> None:
    """适配器返回 True → catalog 更新为 healthy."""
    adapter = _MockAdapter(healthy=True)
    catalog.register(_desc("agent_a"))
    scheduler = HealthCheckScheduler(catalog=catalog, adapters={"agent_a": adapter})

    result = scheduler.check_agent("agent_a")

    assert result is True
    assert catalog.get("agent_a").healthy is True
    assert adapter.call_count == 1


# ── 2. check_agent — 不健康 ──────────────────────────────────────


def test_check_agent_unhealthy(catalog: AgentCatalog) -> None:
    """适配器返回 False → catalog 更新为 unhealthy."""
    adapter = _MockAdapter(healthy=False)
    catalog.register(_desc("agent_a"))
    scheduler = HealthCheckScheduler(catalog=catalog, adapters={"agent_a": adapter})

    result = scheduler.check_agent("agent_a")

    assert result is False
    assert catalog.get("agent_a").healthy is False
    assert adapter.call_count == 1


# ── 3. check_agent — 无适配器 ────────────────────────────────────


def test_check_agent_adapter_missing(catalog: AgentCatalog) -> None:
    """无适配器 → 跳过，不报错，不更新 catalog."""
    catalog.register(_desc("agent_a"))
    scheduler = HealthCheckScheduler(catalog=catalog, adapters={})

    result = scheduler.check_agent("agent_a")

    assert result is False
    # catalog 未被更新，保持初始 healthy=True
    assert catalog.get("agent_a").healthy is True


# ── 4. check_agent — 异常 ────────────────────────────────────────


def test_check_agent_exception(catalog: AgentCatalog) -> None:
    """适配器抛异常 → 捕获，标记 unhealthy，error 含异常信息."""
    adapter = _MockAdapter(raise_exc=RuntimeError("connection refused"))
    catalog.register(_desc("agent_a"))
    scheduler = HealthCheckScheduler(catalog=catalog, adapters={"agent_a": adapter})

    result = scheduler.check_agent("agent_a")

    assert result is False
    assert catalog.get("agent_a").healthy is False
    status = scheduler.get_status()
    assert "agent_a" in status
    assert status["agent_a"].healthy is False
    assert "RuntimeError" in status["agent_a"].error
    assert "connection refused" in status["agent_a"].error


# ── 5. check_once — 全量检查 ────────────────────────────────────


def test_check_once_all_agents(catalog: AgentCatalog) -> None:
    """检查所有注册 Agent，每个适配器被调用一次."""
    adapter_a = _MockAdapter(healthy=True)
    adapter_b = _MockAdapter(healthy=False)
    catalog.register(_desc("agent_a"))
    catalog.register(_desc("agent_b"))
    scheduler = HealthCheckScheduler(
        catalog=catalog,
        adapters={"agent_a": adapter_a, "agent_b": adapter_b},
    )

    scheduler.check_once()

    assert adapter_a.call_count == 1
    assert adapter_b.call_count == 1
    assert catalog.get("agent_a").healthy is True
    assert catalog.get("agent_b").healthy is False


# ── 6. check_once — 无 Agent ────────────────────────────────────


def test_check_once_no_agents(catalog: AgentCatalog) -> None:
    """无 Agent 时空操作，不抛异常."""
    scheduler = HealthCheckScheduler(catalog=catalog, adapters={})
    scheduler.check_once()  # 不抛异常


# ── 7. start / stop ─────────────────────────────────────────────


def test_start_stop(catalog: AgentCatalog) -> None:
    """启动/停止后台线程，验证线程生命周期."""
    adapter = _MockAdapter(healthy=True)
    catalog.register(_desc("agent_a"))
    scheduler = HealthCheckScheduler(
        catalog=catalog,
        adapters={"agent_a": adapter},
        check_interval_s=0.05,
    )

    scheduler.start()
    assert scheduler._thread is not None
    assert scheduler._thread.is_alive()

    # 等待至少一轮检查
    time.sleep(0.15)
    scheduler.stop()

    # 线程已停止
    assert scheduler._thread is None or not scheduler._thread.is_alive()
    # 检查确实执行了
    assert adapter.call_count >= 1


# ── 8. start 幂等 ───────────────────────────────────────────────


def test_start_idempotent(catalog: AgentCatalog) -> None:
    """重复 start 不创建多个线程."""
    adapter = _MockAdapter(healthy=True)
    catalog.register(_desc("agent_a"))
    scheduler = HealthCheckScheduler(
        catalog=catalog,
        adapters={"agent_a": adapter},
        check_interval_s=10.0,  # 长间隔，避免多轮检查
    )

    scheduler.start()
    thread1 = scheduler._thread
    scheduler.start()  # 重复 start
    thread2 = scheduler._thread

    assert thread1 is not None
    assert thread1 is thread2

    scheduler.stop()


# ── 9. stop 未启动 ──────────────────────────────────────────────


def test_stop_not_started(catalog: AgentCatalog) -> None:
    """未启动时 stop 无副作用，不抛异常."""
    scheduler = HealthCheckScheduler(catalog=catalog, adapters={})
    scheduler.stop()  # 不抛异常


# ── 10. get_status ──────────────────────────────────────────────


def test_get_status(catalog: AgentCatalog) -> None:
    """获取状态：初始为空，检查后含快照."""
    adapter = _MockAdapter(healthy=True)
    catalog.register(_desc("agent_a"))
    scheduler = HealthCheckScheduler(catalog=catalog, adapters={"agent_a": adapter})

    # 初始状态为空
    assert scheduler.get_status() == {}

    scheduler.check_agent("agent_a")
    status = scheduler.get_status()

    assert "agent_a" in status
    s = status["agent_a"]
    assert s.agent_name == "agent_a"
    assert s.healthy is True
    assert s.last_check != ""
    assert s.latency_ms >= 0
    assert s.error == ""


# ── 11. check_interval ─────────────────────────────────────────


def test_check_interval(catalog: AgentCatalog) -> None:
    """验证检查间隔：短间隔下多轮检查被执行."""
    adapter = _MockAdapter(healthy=True)
    catalog.register(_desc("agent_a"))
    scheduler = HealthCheckScheduler(
        catalog=catalog,
        adapters={"agent_a": adapter},
        check_interval_s=0.02,
    )

    scheduler.start()
    time.sleep(0.1)  # 等待约 5 轮
    scheduler.stop()

    # 验证多轮检查执行（至少 2 次）
    assert adapter.call_count >= 2


# ── 12. timeout ────────────────────────────────────────────────


def test_timeout(catalog: AgentCatalog) -> None:
    """验证超时保护：慢速适配器在超时后被标记 unhealthy."""
    adapter = _MockAdapter(delay=1.0)  # 1 秒延迟
    catalog.register(_desc("agent_a"))
    scheduler = HealthCheckScheduler(
        catalog=catalog,
        adapters={"agent_a": adapter},
        timeout_s=0.1,  # 100ms 超时
    )

    result = scheduler.check_agent("agent_a")

    assert result is False
    status = scheduler.get_status()
    assert "agent_a" in status
    assert status["agent_a"].healthy is False
    assert "timeout" in status["agent_a"].error


# ── 13. HealthStatus 模型 ──────────────────────────────────────


def test_health_status_model() -> None:
    """HealthStatus Pydantic 模型字段正确."""
    status = HealthStatus(
        agent_name="test_agent",
        healthy=True,
        last_check="2026-01-01T00:00:00+00:00",
        latency_ms=42,
        error="",
    )
    assert status.agent_name == "test_agent"
    assert status.healthy is True
    assert status.last_check == "2026-01-01T00:00:00+00:00"
    assert status.latency_ms == 42
    assert status.error == ""


# ── 14. get_status 返回副本 ────────────────────────────────────


def test_get_status_returns_copy(catalog: AgentCatalog) -> None:
    """get_status 返回副本，修改不影响内部状态."""
    adapter = _MockAdapter(healthy=True)
    catalog.register(_desc("agent_a"))
    scheduler = HealthCheckScheduler(catalog=catalog, adapters={"agent_a": adapter})
    scheduler.check_agent("agent_a")

    status = scheduler.get_status()
    status["injected"] = HealthStatus(
        agent_name="injected", healthy=False, last_check="", latency_ms=0, error="hack"
    )

    # 内部状态不受影响
    internal = scheduler.get_status()
    assert "injected" not in internal
    assert "agent_a" in internal