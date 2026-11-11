"""EventBus → 联邦桥接的端到端钉子（2026-11-10 接线审计）。

缺陷本体：``MAOPEventBusBridge.attach_federation`` 用 ``subscribe("*", ...)``
订阅全部事件，而 ``EventBus._find_matching_subs`` 只实现 ``前缀.*`` 形式，
裸 ``"*"`` 匹配不到任何事件 —— 于是**联邦第一跳永远静默为空**。

这个缺陷极隐蔽：桥接代码看起来完全正确，联邦总线自己的分发逻辑也完整，
只是事件根本进不来。此前没有任何测试跨越这一跳。

注意：本文件钉的是"事件能进联邦"，不是"能跨进程"。``FederationBus`` 至今
没有跨进程传输层（无 HTTP/WS/Redis/MQ），双实例联邦只在同进程内成立。
"""

from __future__ import annotations

import asyncio

from maop.core.reliability.event_bus import Event, EventBus
from maop.core.reliability.event_federation import FederationBus, MAOPEventBusBridge


def _wire() -> tuple[EventBus, FederationBus, list[dict]]:
    """按生产用法接线：EventBus → 桥接 → FederationBus。"""
    bus = EventBus()
    fed = FederationBus()
    bridge = MAOPEventBusBridge(bus, source_label="inst-A")
    fed.register_bridge(bridge)
    bridge.attach_federation(fed)

    got: list[dict] = []
    fed.subscribe("*", lambda e: got.append({"src": e.source, "topic": e.topic}))
    return bus, fed, got


def test_bus_events_reach_the_federation() -> None:
    """一次 publish 必须能在联邦侧看见（这就是修复前为 0 的那一跳）。"""
    bus, _fed, got = _wire()

    async def _go() -> None:
        await bus.publish(Event(topic="execution.result", data={"n": 1}))
        await bus.publish(Event(topic="blackboard.write", data={"n": 2}))
        await asyncio.sleep(0.15)

    asyncio.run(_go())

    assert [g["topic"] for g in got] == ["execution.result", "blackboard.write"], (
        "事件没进联邦 —— 说明 EventBus 又不认裸 * 订阅了"
    )
    assert {g["src"] for g in got} == {"inst-A"}


def test_event_payload_survives_the_hop() -> None:
    """载荷（不只是 topic）必须原样过桥。"""
    from maop.core.reliability.event_federation import FederatedEvent

    fed_events: list[FederatedEvent] = []
    bus = EventBus()
    fed = FederationBus()
    bridge = MAOPEventBusBridge(bus, source_label="inst-A")
    fed.register_bridge(bridge)
    bridge.attach_federation(fed)
    fed.subscribe("*", fed_events.append)

    async def _go() -> None:
        await bus.publish(Event(topic="x.y", data={"k": "v"}))
        await asyncio.sleep(0.15)

    asyncio.run(_go())

    assert fed_events, "载荷测试前提不成立：事件未到达"
    assert fed_events[0].data == {"k": "v"}
    assert fed_events[0].topic == "x.y"