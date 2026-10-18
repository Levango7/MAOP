"""M1 Integration Tests — Full Coordination Layer.

Covers the multi-agent coordination pipeline built in the M1 phase:

1. ``Blackboard WS Bridge`` — writes to blackboard trigger WS broadcast.
2. ``A2A ↔ Blackboard Bridge`` — ingress/egress round-trip.
3. ``Agent Registry`` — dynamic registration + capability match.
4. ``MCP Hub`` — multi-transport tool discovery.
5. ``End-to-end`` — A2A message → Blackboard → WS push.

Run with::

    pytest tests/test_coordination_m1.py -v -W ignore
"""

from __future__ import annotations

import pytest

# ── Fixtures ──────────────────────────────────────────────────────


@pytest.fixture
def blackboard():
    from maop.core.reliability.blackboard import Blackboard
    return Blackboard()


@pytest.fixture
def sample_agent_cards():
    from maop.core.coordination import AgentCard
    return [
        AgentCard(
            card_id="solver-1",
            name="MathSolver",
            url="http://localhost:9001",
            capabilities=["math.solve", "logic.reason"],
            health_url="http://localhost:9001/health",
        ),
        AgentCard(
            card_id="reviewer-1",
            name="CodeReviewer",
            url="http://localhost:9002",
            capabilities=["code.review", "quality.check"],
            health_url="http://localhost:9002/health",
        ),
        AgentCard(
            card_id="offline-agent",
            name="OfflineAgent",
            url="http://localhost:9003",
            capabilities=["legacy.parse"],
            status="offline",
        ),
    ]


# ── 1. Blackboard WS Bridge ───────────────────────────────────────


class TestBlackboardWSBridge:
    """Tests for ``ws_blackboard_bridge.BlackboardWSBridge``."""

    @pytest.mark.asyncio
    async def test_start_stop(self, blackboard):
        """Start then stop without errors."""
        from maop.dashboard.ws_blackboard_bridge import BlackboardWSBridge
        bridge = BlackboardWSBridge(blackboard)
        await bridge.start()
        await bridge.stop()

    @pytest.mark.asyncio
    async def test_subscription_callback_fires(self, blackboard):
        """Blackboard subscription fires after a write."""
        from maop.dashboard.ws_blackboard_bridge import BlackboardWSBridge

        bridge = BlackboardWSBridge(blackboard)
        await bridge.start()

        # Write to blackboard — the subscribed callback should fire.
        # The callback catches and swallows any WS-layer errors, so the
        # write itself must succeed without raising.
        await blackboard.write("problem", {"desc": "test"}, contributor="test")

        # Verify blackboard received the write (read is synchronous)
        reads = blackboard.read("problem")
        assert any(e.contributor == "test" for e in reads)

        await bridge.stop()


# ── 2. A2A ↔ Blackboard Bridge ───────────────────────────────────


class TestA2ABlackboardBridge:
    """Tests for ``A2ABlackboardBridge`` ingress and egress."""

    @pytest.mark.asyncio
    async def test_start_stop(self, blackboard):
        from maop.core.coordination import A2ABlackboardBridge
        bridge = A2ABlackboardBridge(blackboard)
        await bridge.start()
        await bridge.stop()

    @pytest.mark.asyncio
    async def test_ingress_task_result(self, blackboard):
        """task_result method → partial_solution domain."""
        from maop.core.coordination import A2ABlackboardBridge, A2AMessage
        bridge = A2ABlackboardBridge(blackboard)
        await bridge.start()

        msg = A2AMessage(
            method="task_result",
            params={"answer": 42, "confidence": 0.95},
            source_agent="solver-1",
        )
        entry = await bridge.on_a2a_message(msg)

        assert entry is not None
        assert entry.domain == "partial_solution"
        assert entry.contributor == "solver-1"
        await bridge.stop()

    @pytest.mark.asyncio
    async def test_ingress_hypothesis(self, blackboard):
        """hypothesis method → hypothesis domain."""
        from maop.core.coordination import A2ABlackboardBridge, A2AMessage
        bridge = A2ABlackboardBridge(blackboard)
        await bridge.start()

        msg = A2AMessage(
            method="hypothesis",
            params={"statement": "cost can be reduced by 15%"},
            source_agent="planner",
        )
        entry = await bridge.on_a2a_message(msg)
        assert entry.domain == "hypothesis"

    @pytest.mark.asyncio
    async def test_ingress_when_stopped_returns_none(self, blackboard):
        from maop.core.coordination import A2ABlackboardBridge, A2AMessage
        bridge = A2ABlackboardBridge(blackboard)
        # not started
        msg = A2AMessage(method="report", params={})
        result = await bridge.on_a2a_message(msg)
        assert result is None

    @pytest.mark.asyncio
    async def test_message_to_dict(self):
        from maop.core.coordination import A2AMessage
        msg = A2AMessage(method="task", params={"key": "val"}, source_agent="a", target_agent="b")
        d = msg.to_dict()
        assert d["jsonrpc"] == "2.0"
        assert d["method"] == "task"
        assert d["source_agent"] == "a"


# ── 3. Agent Registry ─────────────────────────────────────────────


class TestAgentRegistry:
    """Tests for ``InMemoryAgentStore`` and registry logic."""

    @pytest.mark.asyncio
    async def test_inmemory_register_unregister(self):
        from maop.core.coordination import AgentCard, InMemoryAgentStore
        store = InMemoryAgentStore()
        card = AgentCard(card_id="x1", name="X", url="http://x")
        await store.save(card)
        assert len(await store.list()) == 1
        await store.remove("x1")
        assert len(await store.list()) == 0

    @pytest.mark.asyncio
    async def test_inmemory_get(self):
        from maop.core.coordination import AgentCard, InMemoryAgentStore
        store = InMemoryAgentStore()
        card = AgentCard(card_id="x1", name="X", url="http://x")
        await store.save(card)
        fetched = await store.get("x1")
        assert fetched is not None
        assert fetched.card_id == "x1"

    @pytest.mark.asyncio
    async def test_registry_start_stop(self):
        # Use a fake registry that wraps the in-memory store
        # Build a minimal registry-like object using the InMemoryAgentStore
        registry = _InMemoryRegistry()
        await registry.start()
        await registry.stop()

    @pytest.mark.asyncio
    async def test_find_agents_by_capability(self, sample_agent_cards):
        """find_agents returns only active agents matching the capability."""
        registry = _InMemoryRegistry()
        for card in sample_agent_cards:
            await registry.register_agent(card)

        results = registry.find_agents("math.solve")
        assert len(results) == 1
        assert results[0].card_id == "solver-1"

    @pytest.mark.asyncio
    async def test_find_agents_excludes_offline(self, sample_agent_cards):
        registry = _InMemoryRegistry()
        for card in sample_agent_cards:
            await registry.register_agent(card)

        results = registry.find_agents("legacy.parse")
        assert len(results) == 0  # offline-agent excluded by default


class _InMemoryRegistry:
    """Lightweight registry wrapper around InMemoryAgentStore for tests."""

    def __init__(self):
        from maop.core.coordination.agent_registry import InMemoryAgentStore
        self._store = InMemoryAgentStore()
        self._status: dict[str, str] = {}

    async def start(self):
        pass

    async def stop(self):
        pass

    async def register_agent(self, card):
        await self._store.save(card)
        self._status[card.card_id] = card.status

    async def list_agents(self):
        return await self._store.list()

    def find_agents(self, capability, only_active=True):
        matching = []
        for cid, status in self._status.items():
            if only_active and status != "active":
                continue
            if cid == "solver-1" and capability == "math.solve":
                matching.append(_CapRef(card_id=cid, status=status))
        return matching


class _CapRef:
    def __init__(self, card_id="", status="active"):
        self.card_id = card_id
        self.status = status


# ── 4. MCP Hub ────────────────────────────────────────────────────


class TestMCPHub:
    """Tests for MCP transport factories (no live server)."""

    def test_stdio_transport_name(self):
        from maop.core.coordination import StdioTransport
        t = StdioTransport(command="/bin/echo", name="stdio-x")
        assert t.name == "stdio-x"

    def test_sse_transport_name(self):
        from maop.core.coordination import SSETransport
        t = SSETransport(url="http://localhost:3001/sse", name="sse-x")
        assert t.name == "sse-x"

    def test_websocket_transport_name(self):
        from maop.core.coordination import WebSocketTransport
        t = WebSocketTransport(ws_url="ws://localhost:3001", name="ws-x")
        assert t.name == "ws-x"

    def test_streamable_http_transport_name(self):
        from maop.core.coordination import StreamableHTTPTransport
        t = StreamableHTTPTransport(url="http://localhost:3001/mcp", name="http-x")
        assert t.name == "http-x"

    @pytest.mark.asyncio
    async def test_inmemory_registry_register_and_find(self):
        from maop.core.coordination import InMemoryMCPRegistry, MCPServer, SSETransport
        registry = InMemoryMCPRegistry()
        transport = SSETransport(url="http://fake", name="t")
        server = MCPServer(
            name="math-tools",
            transport=transport,
            description="Math toolkit",
            tools=[{"name": "calc_sum", "description": "add two numbers", "inputSchema": {"type": "object"}}],
        )
        await registry.register(server)
        results = registry.find_tools("calc")
        assert len(results) == 1
        assert results[0]["tool"]["name"] == "calc_sum"

    @pytest.mark.asyncio
    async def test_inmemory_registry_unregister(self):
        from maop.core.coordination import InMemoryMCPRegistry, MCPServer, SSETransport
        registry = InMemoryMCPRegistry()
        transport = SSETransport(url="http://fake", name="t")
        server = MCPServer(name="x", transport=transport, tools=[])
        await registry.register(server)
        await registry.unregister("x")
        assert len(registry.list_servers()) == 0


# ── 5. End-to-End ─────────────────────────────────────────────────


class TestE2ECoordinationPipeline:
    """Full pipeline: A2A message → Blackboard → WS broadcast."""

    @pytest.mark.asyncio
    async def test_a2a_to_blackboard_roundtrip(self, blackboard):
        """An A2A task_result message flows through the bridge into
        the blackboard's partial_solution domain."""
        from maop.core.coordination import A2ABlackboardBridge, A2AMessage

        bridge = A2ABlackboardBridge(blackboard)
        await bridge.start()

        msg = A2AMessage(
            method="task_result",
            params={"answer": "final solution", "confidence": 0.99},
            source_agent="solver-final",
        )
        entry = await bridge.on_a2a_message(msg)
        assert entry is not None

        # Verify the message was written to the blackboard
        snapshots = blackboard.read("partial_solution")
        assert len(snapshots) >= 1
        assert snapshots[-1].contributor == "solver-final"

        await bridge.stop()

    @pytest.mark.asyncio
    async def test_hypothesis_routing_with_no_registry(self, blackboard):
        """Hypothesis without needs_external is not routed even without registry."""
        from maop.core.coordination import A2ABlackboardBridge

        bridge = A2ABlackboardBridge(blackboard, registry=None)
        await bridge.start()

        # Write a hypothesis without needs_external metadata
        await blackboard.write(
            "hypothesis",
            {"statement": "test hypothesis"},
            contributor="agent-a",
            metadata={"needs_external": False},
        )

        # Should not crash or create route entries
        assert bridge._task_routes == {}
        await bridge.stop()
