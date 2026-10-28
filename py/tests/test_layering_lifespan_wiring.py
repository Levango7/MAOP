"""分层服务 lifespan 接线测试（阶段1 HealthService / 阶段3 A2AGateway）。

驱动对象是 ``server.start_layering_services`` / ``shutdown_layering_services``——
lifespan 的起停路径之所以拆成独立函数，正是为了让本文件直接驱动它们，
不起整套 lifespan 的副作用（WS 推送、备份调度、MCP 播种、多模态预热）。

四个判据（对应接线的四条约定）：
1. ``bg_enabled=False`` → 探测循环不启动（多 worker 门控，避免 N 份循环）；
2. ``bg_enabled=True`` → HealthService 真启动，shutdown 后停机（对称）；
3. ``MAOP_A2A_GATEWAY`` 缺省 → 门户不构造（默认不占端口——部署决策）；
4. ``MAOP_A2A_GATEWAY=1`` → 门户构造且 started，shutdown 后 started=False。
"""

from __future__ import annotations

from fastapi import FastAPI

from maop.dashboard import server


def _app() -> FastAPI:
    return FastAPI()


async def test_health_service_not_started_when_bg_disabled(monkeypatch):
    """多 worker 门控：bg_enabled=False 时探测循环不应启动。"""
    monkeypatch.delenv("MAOP_A2A_GATEWAY", raising=False)
    app = _app()
    await server.start_layering_services(app, bg_enabled=False)
    assert app.state.health_service is None
    assert app.state.a2a_gateway is None
    await server.shutdown_layering_services(app)  # 幂等：全是 None 也应安全


async def test_health_service_starts_and_stops_when_bg_enabled(monkeypatch):
    """bg_enabled=True → HealthService 启动；shutdown 后循环任务被取消。"""
    monkeypatch.delenv("MAOP_A2A_GATEWAY", raising=False)
    app = _app()
    await server.start_layering_services(app, bg_enabled=True)
    svc = app.state.health_service
    assert svc is not None, "bg_enabled=True 时 HealthService 必须启动"
    assert svc._running is True

    await server.shutdown_layering_services(app)
    assert svc._running is False
    assert svc._loop_task is None, "stop() 后循环任务句柄必须清空（可重复起停）"


async def test_gateway_off_by_default(monkeypatch):
    """默认不构造门户：打开对外端口是部署决策，不是升级默认值。"""
    monkeypatch.delenv("MAOP_A2A_GATEWAY", raising=False)
    app = _app()
    await server.start_layering_services(app, bg_enabled=False)
    assert app.state.a2a_gateway is None


async def test_gateway_constructed_and_started_when_enabled(monkeypatch):
    """MAOP_A2A_GATEWAY=1 → 门户构造并进入 started；shutdown 后归位。"""
    monkeypatch.setenv("MAOP_A2A_GATEWAY", "1")
    app = _app()
    await server.start_layering_services(app, bg_enabled=False)
    gw = app.state.a2a_gateway
    assert gw is not None, "显式开启时门户必须构造"
    assert gw.started is True

    await server.shutdown_layering_services(app)
    assert gw.started is False
