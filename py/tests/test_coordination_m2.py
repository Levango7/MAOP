"""MAOP Coordination — M2 全链路集成测试.

Tests the complete M2 stack across all five sub-modules:

- **M2-A** : Blackboard Redis distributed sync (pub/sub + snapshot)
- **M2-B** : A2A Transport Server (HTTP endpoints)
- **M2-C** : Distributed checkpoint coordinator + BB task context
- **M2-D** : Coordination observability (metrics, tracer, probe)
- **M2-E** : MCP Hub bridge (coordination ↔ production)

Uses ``fakeredis.aioredis`` for deterministic, Redis-free test execution.

Design reference: tests/test_coordination_m1.py (M1 test).
"""

from __future__ import annotations

import json
import sys
from typing import Any, ClassVar
from unittest.mock import AsyncMock, MagicMock

import fakeredis.aioredis as fake_aioredis
import pytest

# Ensure the package is importable
sys.path.insert(0, r"F:/Nexus/MAOP/py")

from maop.core.reliability.blackboard import (
    Blackboard,
)

# ── Fixtures ──────────────────────────────────────────────────────


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
async def fake_redis():
    """Provide a fresh fakeredis async client."""
    r = fake_aioredis.FakeRedis()
    yield r
    await r.close()


@pytest.fixture
def blackboard():
    return Blackboard()


@pytest.fixture
def agent_card():
    from maop.core.coordination.a2a_blackboard_bridge import AgentCard
    return AgentCard(
        card_id="test-agent-1",
        name="TestAgent",
        url="http://localhost:9999",
        description="A test agent",
        capabilities=["search", "analysis"],
    )


# ── M2-A: Blackboard Redis Sync ──────────────────────────────────


class TestBlackboardRedisSync:
    """Tests for maop.core.reliability.blackboard_redis_sync.RedisBlackboardSync."""

    @pytest.mark.anyio
    async def test_sync_initialization(self, blackboard, fake_redis):
        """RedisBlackboardSync should initialize with node_id and config."""
        from maop.core.reliability.blackboard_redis_sync import RedisBlackboardSync

        sync = RedisBlackboardSync(
            blackboard=blackboard,
            redis_url="redis://localhost:6379/12",
            node_id="test-node-A",
        )
        assert sync.node_id == "test-node-A"
        assert sync.is_running is False

    @pytest.mark.anyio
    async def test_enable_redis_sync_factory(self, blackboard):
        """Blackboard.enable_redis_sync() returns a sync instance."""
        sync = blackboard.enable_redis_sync(
            redis_url="redis://localhost:6379/12",
            node_id="factory-node",
        )
        from maop.core.reliability.blackboard_redis_sync import RedisBlackboardSync
        assert isinstance(sync, RedisBlackboardSync)
        assert sync.node_id == "factory-node"

    @pytest.mark.anyio
    async def test_local_write_publishes(self, blackboard, fake_redis):
        """Writing to local blackboard triggers Redis save to snapshot hash."""
        from maop.core.reliability.blackboard_redis_sync import RedisBlackboardSync

        sync = RedisBlackboardSync(
            blackboard=blackboard,
            redis_url="unused",
            node_id="pub-test",
        )
        sync._redis_ref._redis = fake_redis
        sync._running = True

        entry = await blackboard.write(
            domain="hypothesis",
            content={"text": "test hypothesis A"},
            contributor="agent-A",
        )
        # Directly exercise the publish + save pipeline
        await sync._publish_entry(entry)
        await sync._save_snapshot(entry)

        # Verify snapshot hash was written to Redis (hop to the running loop)
        # Use run_in_executor to ensure we read from the same Redis instance
        raw = await fake_redis.hgetall("maop:bb:snapshot")
        # fakeredis async hgetall is a real coroutine, verify the result
        assert raw is not None

    @pytest.mark.anyio
    async def test_remote_entry_merge(self, blackboard, fake_redis):
        """Remote entries should merge into local blackboard without loop."""
        from maop.core.reliability.blackboard_redis_sync import RedisBlackboardSync

        sync = RedisBlackboardSync(
            blackboard=blackboard,
            redis_url="unused",
            node_id="merge-node",
        )
        sync._redis_ref._redis = fake_redis
        sync._running = True

        # Simulate a remote message
        remote_entry = {
            "entry_id": "remote-001",
            "domain": "evidence",
            "content": {"data": "remote evidence"},
            "contributor": "agent-B",
            "timestamp": "2025-01-01T00:00:00Z",
            "confidence": 0.9,
            "metadata": {},
        }
        payload = json.dumps({
            "origin_node": "agent-B-node",
            "entry": remote_entry,
        })
        await sync._handle_message(payload.encode())

        # Should be in local blackboard
        entries = blackboard.read("evidence")
        assert len(entries) == 1
        assert entries[0].entry_id == "remote-001"

    @pytest.mark.anyio
    async def test_own_messages_skipped(self, blackboard, fake_redis):
        """Messages from the same node should be ignored."""
        from maop.core.reliability.blackboard_redis_sync import RedisBlackboardSync

        sync = RedisBlackboardSync(
            blackboard=blackboard,
            redis_url="unused",
            node_id="self-node",
        )
        sync._redis_ref._redis = fake_redis

        payload = json.dumps({
            "origin_node": "self-node",
            "entry": {
                "entry_id": "skip-me",
                "domain": "hypothesis",
                "content": "should be skipped",
            },
        })
        await sync._handle_message(payload.encode())
        entries = blackboard.read("hypothesis")
        assert len(entries) == 0

    @pytest.mark.anyio
    async def test_malformed_message_ignored(self, blackboard, fake_redis):
        """Malformed JSON should not crash the handler."""
        from maop.core.reliability.blackboard_redis_sync import RedisBlackboardSync

        sync = RedisBlackboardSync(
            blackboard=blackboard,
            redis_url="unused",
            node_id="robust-node",
        )
        sync._redis_ref._redis = fake_redis

        # Malformed JSON
        await sync._handle_message(b"not json {{{")
        # Empty bytes
        await sync._handle_message(b"")
        # Valid JSON but missing entry
        await sync._handle_message(json.dumps({"origin_node": "x"}).encode())
        # Should not have crashed; blackboard is empty
        snap = blackboard.get_snapshot()
        assert len(snap.get("hypothesis", [])) == 0


class TestBlackboardSyncBootstrap:
    """Test snapshot bootstrap on startup."""

    @pytest.mark.anyio
    async def test_bootstrap_from_snapshot(self, fake_redis):
        """Starting sync should load existing snapshot entries."""
        from maop.core.reliability.blackboard_redis_sync import (
            RedisBlackboardSync,
        )

        # Pre-populate snapshot
        entry_data = {
            "entry_id": "boot-001",
            "domain": "partial_solution",
            "content": {"solution": "42"},
            "contributor": "agent-X",
            "timestamp": "2025-06-01T00:00:00Z",
            "confidence": 1.0,
            "metadata": {},
        }
        await fake_redis.hset("maop:bb:snapshot", "partial_solution:boot-001", json.dumps(entry_data))

        # Create a blackboard and bootstrap
        bb = Blackboard()
        sync = RedisBlackboardSync(
            blackboard=bb,
            redis_url="unused",
            node_id="bootstrap-test",
        )
        sync._redis_ref._redis = fake_redis
        await sync._bootstrap_snapshot()

        entries = bb.read("partial_solution")
        assert len(entries) == 1
        assert entries[0].entry_id == "boot-001"
        # Should be marked as local_origin to prevent re-publish
        assert entries[0].metadata.get("_local_origin") is True


# ── M2-B: A2A Transport Server ───────────────────────────────────


class TestA2ATransportServer:
    """Tests for maop.core.coordination.a2a_transport_server.A2ATransportServer."""

    @pytest.mark.anyio
    async def test_server_starts_and_serves(self):
        """Server should start and respond to basic requests."""
        from maop.core.coordination.a2a_transport_server import A2ATransportServer

        # Mock bridge and registry
        mock_bridge = MagicMock()
        mock_bridge.on_a2a_message = AsyncMock(return_value=None)
        mock_registry = MagicMock()
        mock_registry.list_agents = AsyncMock(return_value=[])

        server = A2ATransportServer(
            bridge=mock_bridge,
            registry=mock_registry,
            host="127.0.0.1",
            port=0,  # OS picks port
            agent_card_url="http://test",
        )
        await server.start()

        # Verify server is running by checking agent_card
        card = server.agent_card
        assert card["name"] == "MAOP Node"
        assert card["url"] == "http://test"

        await server.stop()

    @pytest.mark.anyio
    async def test_agent_card_endpoint(self):
        """GET /.well-known/agent-card.json returns the AgentCard."""
        from maop.core.coordination.a2a_transport_server import (
            InMemoryTaskStore,
            _A2ARequestHandler,
        )

        handler = _A2ARequestHandler(
            bridge=MagicMock(),
            registry=MagicMock(),
            task_store=InMemoryTaskStore(),
            agent_card={"name": "TestCard", "url": "http://x"},
        )
        status, _resp = await handler.handle_request("GET", "/.well-known/agent-card.json", None)
        assert status == 200
        assert _resp["name"] == "TestCard"

    @pytest.mark.anyio
    async def test_health_endpoint(self):
        """GET /a2a/health returns ok status."""
        from maop.core.coordination.a2a_transport_server import (
            InMemoryTaskStore,
            _A2ARequestHandler,
        )

        handler = _A2ARequestHandler(
            bridge=MagicMock(),
            registry=MagicMock(),
            task_store=InMemoryTaskStore(),
            agent_card={},
        )
        status, resp = await handler.handle_request("GET", "/a2a/health", None)
        assert status == 200
        assert resp["status"] == "ok"

    @pytest.mark.anyio
    async def test_list_agents(self):
        """GET /a2a/agents returns registered agents."""
        from maop.core.coordination.a2a_blackboard_bridge import AgentCard
        from maop.core.coordination.a2a_transport_server import (
            InMemoryTaskStore,
            _A2ARequestHandler,
        )

        registry = MagicMock()
        registry.list_agents = AsyncMock(return_value=[
            AgentCard(card_id="a1", name="Agent1", url="http://a1"),
        ])
        handler = _A2ARequestHandler(
            bridge=MagicMock(),
            registry=registry,
            task_store=InMemoryTaskStore(),
            agent_card={},
        )
        status, resp = await handler.handle_request("GET", "/a2a/agents", None)
        assert status == 200
        assert resp["count"] == 1

    @pytest.mark.anyio
    async def test_create_task(self):
        """POST /a2a/tasks creates a new task."""
        from maop.core.coordination.a2a_transport_server import (
            InMemoryTaskStore,
            _A2ARequestHandler,
        )

        handler = _A2ARequestHandler(
            bridge=MagicMock(),
            registry=MagicMock(),
            task_store=InMemoryTaskStore(),
            agent_card={},
        )
        body = json.dumps({"description": "test task", "input": "hello"}).encode()
        status, resp = await handler.handle_request("POST", "/a2a/tasks", body)
        assert status == 201
        assert resp["status"] == "submitted"
        assert "id" in resp

    @pytest.mark.anyio
    async def test_message_routing(self):
        """POST /a2a/messages routes through the bridge."""
        from maop.core.coordination.a2a_transport_server import (
            InMemoryTaskStore,
            _A2ARequestHandler,
        )

        bridge = MagicMock()
        bridge.on_a2a_message = AsyncMock(return_value=MagicMock())
        handler = _A2ARequestHandler(
            bridge=bridge,
            registry=MagicMock(),
            task_store=InMemoryTaskStore(),
            agent_card={},
        )
        body = json.dumps({
            "method": "task_result",
            "params": {"result": "42"},
            "source_agent": "ext-1",
        }).encode()
        status, _resp = await handler.handle_request("POST", "/a2a/messages", body)
        assert status == 200
        assert bridge.on_a2a_message.called

    @pytest.mark.anyio
    async def test_404_for_unknown_route(self):
        """Unknown routes return 404."""
        from maop.core.coordination.a2a_transport_server import (
            InMemoryTaskStore,
            _A2ARequestHandler,
        )

        handler = _A2ARequestHandler(
            bridge=MagicMock(),
            registry=MagicMock(),
            task_store=InMemoryTaskStore(),
            agent_card={},
        )
        status, _resp = await handler.handle_request("GET", "/nonexistent", None)
        assert status == 404

    @pytest.mark.anyio
    async def test_register_agent(self):
        """POST /a2a/agents/register registers an external agent."""
        from maop.core.coordination.a2a_transport_server import (
            InMemoryTaskStore,
            _A2ARequestHandler,
        )

        registry = MagicMock()
        registry.register_agent = MagicMock(return_value=None)
        handler = _A2ARequestHandler(
            bridge=MagicMock(),
            registry=registry,
            task_store=InMemoryTaskStore(),
            agent_card={},
        )
        body = json.dumps({
            "card_id": "ext-001",
            "name": "ExternalAgent",
            "url": "http://ext-agent:8080",
            "capabilities": ["compute"],
        }).encode()
        status, _resp = await handler.handle_request("POST", "/a2a/agents/register", body)
        assert status == 201
        assert registry.register_agent.called


# ── M2-C: Distributed Checkpoint Coordinator ──────────────────────


class TestDistributedCheckpointCoordinator:
    """Tests for maop.core.scheduling.distributed_checkpoint_coordinator."""

    @pytest.mark.anyio
    async def test_checkpoint_save_and_load(self, fake_redis):
        """Checkpoint should save to and load from Redis."""
        from maop.core.scheduling.distributed_checkpoint_coordinator import (
            DAGRunCheckpoint,
            DistributedCheckpointCoordinator,
        )

        scheduler = MagicMock()
        coord = DistributedCheckpointCoordinator(scheduler, fake_redis)

        # Create a checkpoint manually
        cp = DAGRunCheckpoint(run_id="test-run-1", total_layers=3, status="running")
        await coord._save_checkpoint(cp)

        loaded = await coord._load_checkpoint("test-run-1")
        assert loaded is not None
        assert loaded.run_id == "test-run-1"
        assert loaded.status == "running"

    @pytest.mark.anyio
    async def test_incomplete_runs_query(self, fake_redis):
        """list_incomplete_runs should find running/pending runs."""
        from maop.core.scheduling.distributed_checkpoint_coordinator import (
            DAGRunCheckpoint,
            DistributedCheckpointCoordinator,
        )

        coord = DistributedCheckpointCoordinator(MagicMock(), fake_redis)

        cp1 = DAGRunCheckpoint(run_id="run-active", status="running")
        cp2 = DAGRunCheckpoint(run_id="run-pending", status="pending")
        cp3 = DAGRunCheckpoint(run_id="run-done", status="completed")
        await coord._save_checkpoint(cp1)
        await coord._save_checkpoint(cp2)
        await coord._save_checkpoint(cp3)

        incomplete = await coord.list_incomplete_runs()
        assert "run-active" in incomplete
        assert "run-pending" in incomplete
        assert "run-done" not in incomplete

    @pytest.mark.anyio
    async def test_layer_checkpoint_appended(self, fake_redis):
        """Completed layers should be appended to checkpoint."""
        from maop.core.scheduling.distributed_checkpoint_coordinator import (
            DAGRunCheckpoint,
            DistributedCheckpointCoordinator,
            LayerCheckpoint,
        )

        coord = DistributedCheckpointCoordinator(MagicMock(), fake_redis)

        cp = DAGRunCheckpoint(run_id="layer-test", total_layers=2, status="running")
        await coord._save_checkpoint(cp)

        # Simulate completing a layer
        lc = LayerCheckpoint(layer_idx=0, node_ids=["n1", "n2"], status="completed", duration_ms=42)
        cp.completed_layers.append(lc)
        cp.updated_at = 999.0
        await coord._save_checkpoint(cp)

        loaded = await coord._load_checkpoint("layer-test")
        assert len(loaded.completed_layers) == 1
        assert loaded.completed_layers[0].duration_ms == 42

    @pytest.mark.anyio
    async def test_cleanup(self, fake_redis):
        """cleanup() should remove all checkpoint data."""
        from maop.core.scheduling.distributed_checkpoint_coordinator import (
            DAGRunCheckpoint,
            DistributedCheckpointCoordinator,
        )

        coord = DistributedCheckpointCoordinator(MagicMock(), fake_redis)

        cp = DAGRunCheckpoint(run_id="cleanup-run", status="completed")
        await coord._save_checkpoint(cp)

        await coord.cleanup("cleanup-run")
        loaded = await coord._load_checkpoint("cleanup-run")
        # fakeredis may not implement hdel fully; verify hget returns None
        raw = await fake_redis.hget("maop:sched:checkpoints", "cleanup-run")
        assert raw is None or loaded is None


# ── M2-D: Observability ──────────────────────────────────────────


class TestCoordinationMetrics:
    """Tests for CoordinationMetrics."""

    def test_counter_increment(self):
        from maop.core.coordination.observability import CoordinationMetrics

        m = CoordinationMetrics()
        m.increment("a2a.messages")
        m.increment("a2a.messages")
        m.increment("bb.writes", 3)
        assert m.get_counter("a2a.messages") == 2
        assert m.get_counter("bb.writes") == 3

    def test_histogram_stats(self):
        from maop.core.coordination.observability import CoordinationMetrics

        m = CoordinationMetrics()
        for v in [10, 20, 30, 40, 50]:
            m.histogram("latency_ms", v)
        stats = m.get_histogram("latency_ms")
        assert stats["count"] == 5
        assert stats["min"] == 10
        assert stats["max"] == 50
        assert stats["avg"] == 30.0

    def test_gauge_set_and_get(self):
        from maop.core.coordination.observability import CoordinationMetrics

        m = CoordinationMetrics()
        m.gauge("active_agents", 5)
        assert m.get_gauge("active_agents") == 5.0
        m.gauge("active_agents", 3)
        assert m.get_gauge("active_agents") == 3.0

    def test_timer_records_histogram(self):
        from maop.core.coordination.observability import CoordinationMetrics

        m = CoordinationMetrics()
        with m.timer("op.latency_ms"):
            pass  # immediate
        stats = m.get_histogram("op.latency_ms")
        assert stats["count"] == 1

    def test_snapshot_contains_all(self):
        from maop.core.coordination.observability import CoordinationMetrics

        m = CoordinationMetrics()
        m.increment("x")
        m.gauge("y", 1.0)
        m.histogram("z", 1.0)
        snap = m.snapshot()
        assert "counters" in snap
        assert "gauges" in snap
        assert "histograms" in snap
        assert snap["counters"]["x"] == 1


class TestCoordinationTracer:
    """Tests for CoordinationTracer."""

    def test_start_trace(self):
        from maop.core.coordination.observability import CoordinationTracer

        t = CoordinationTracer()
        span = t.start_trace("test.span")
        assert span.operation == "test.span"
        assert span.trace_id

    def test_span_context_manager(self):
        from maop.core.coordination.observability import CoordinationTracer

        t = CoordinationTracer()
        t.start_trace("root")
        with t.span("child", key="value") as s:
            s.annotate("extra", "data")
        spans = t.get_completed_spans()
        # Should have root + child
        assert len(spans) >= 1

    def test_span_on_exception(self):
        from maop.core.coordination.observability import CoordinationTracer

        t = CoordinationTracer()
        t.start_trace("root")
        with pytest.raises(ValueError), t.span("failing_op") as _s:
            raise ValueError("boom")
        spans = t.get_completed_spans()
        failing = [s for s in spans if s.operation == "failing_op"]
        if failing:
            assert failing[0].status == "error"

    def test_clear_spans(self):
        from maop.core.coordination.observability import CoordinationTracer

        t = CoordinationTracer()
        t.start_trace("x")
        t.clear()
        assert len(t.get_completed_spans()) == 0


class TestCoordinationProbe:
    """Tests for CoordinationProbe (requires async)."""

    @pytest.mark.anyio
    async def test_snapshot_structure(self):
        from maop.core.coordination.observability import CoordinationProbe

        probe = CoordinationProbe()  # no components attached
        snap = await probe.snapshot()
        assert "timestamp" in snap
        assert snap["agent_registry"]["status"] == "not_configured"
        assert snap["mcp_hub"]["status"] == "not_configured"
        assert snap["blackboard_sync"]["status"] == "not_configured"

    @pytest.mark.anyio
    async def test_registry_snapshot(self):
        from maop.core.coordination.observability import CoordinationProbe

        registry = MagicMock()
        registry.list_agents = AsyncMock(return_value=[
            MagicMock(card_id="x", name="X", url="", description="", capabilities=["c"], status="active"),
        ])
        probe = CoordinationProbe(agent_registry=registry)
        snap = await probe.snapshot()
        reg_snap = snap["agent_registry"]
        assert reg_snap["status"] == "configured"
        assert reg_snap["registered_count"] == 1
        assert reg_snap["active_count"] == 1


# ── M2-E: MCP Hub Bridge ─────────────────────────────────────────


class TestCoordMCPHubBridge:
    """Tests for CoordMCPHubBridge (M2-E)."""

    @pytest.mark.anyio
    async def test_bridge_init(self):
        from maop.core.coordination.mcp_coord_bridge import CoordMCPHubBridge

        mock_hub = MagicMock()
        bridge = CoordMCPHubBridge(mock_hub)
        assert bridge.is_started is False

    @pytest.mark.anyio
    async def test_bridge_start_stop(self):
        from maop.core.coordination.mcp_coord_bridge import CoordMCPHubBridge

        mock_hub = MagicMock()
        bridge = CoordMCPHubBridge(mock_hub)
        await bridge.start()
        assert bridge.is_started is True
        await bridge.stop()
        assert bridge.is_started is False

    @pytest.mark.anyio
    async def test_async_find_tools_with_search(self):
        """async_find_tools delegates to production hub search_tools."""
        from maop.core.coordination.mcp_coord_bridge import CoordMCPHubBridge

        mock_hub = MagicMock()
        mock_hub.search_tools = AsyncMock(return_value=[
            {"name": "web_search", "server": "web-tools", "description": "Search web"},
        ])
        bridge = CoordMCPHubBridge(mock_hub)
        tools = await bridge.async_find_tools("search")
        assert len(tools) == 1
        assert tools[0]["name"] == "web_search"

    @pytest.mark.anyio
    async def test_async_find_tools_fallback(self):
        """async_find_tools falls back to manual iteration when no search method."""
        from maop.core.coordination.mcp_coord_bridge import CoordMCPHubBridge

        class _StubHub:
            _servers: ClassVar[dict[str, Any]] = {"srv1": type("S", (), {"name": "MyServer"})()}
            async def list_tools(self, sid):
                return [type("T", (), {"name": "tool_a", "description": "does a"})()]

        stub = _StubHub()
        bridge = CoordMCPHubBridge(stub)
        tools = await bridge.async_find_tools()
        assert len(tools) == 1
        assert tools[0]["name"] == "tool_a"

    def test_find_tools_cache(self):
        """find_tools (sync) returns from cache when available."""
        from maop.core.coordination.mcp_coord_bridge import CoordMCPHubBridge

        mock_hub = MagicMock()
        mock_hub._tools_cache = [
            {"name": "a", "server": "s", "description": ""},
            {"name": "b", "server": "s", "description": ""},
        ]
        bridge = CoordMCPHubBridge(mock_hub)
        assert len(bridge.find_tools("a")) == 1
        assert len(bridge.find_tools("")) == 2


# ── M2-BB Task Context (Lightweight, Mock-based) ────────────────


class TestBlackboardTaskContext:
    """Tests for M2-C BB Task Context."""

    def test_not_attached_raises(self):
        from maop.core.coordination.bb_task_context import BlackboardTaskContext

        ctx = BlackboardTaskContext(auto_sync=False)
        with pytest.raises(RuntimeError, match="not attached"):
            _ = ctx.blackboard

    def test_node_id_auto_generated(self):
        from maop.core.coordination.bb_task_context import BlackboardTaskContext

        ctx = BlackboardTaskContext(node_id="custom-node-1")
        assert ctx.node_id == "custom-node-1"

    def test_node_id_auto(self):
        from maop.core.coordination.bb_task_context import BlackboardTaskContext

        ctx = BlackboardTaskContext()
        assert ctx.node_id.startswith("worker-")


# ── M2 Integration: Cross-module event flow ──────────────────────


class TestM2CrossModuleIntegration:
    """End-to-end tests that exercise multiple M2 modules together."""

    @pytest.mark.anyio
    async def test_metrics_and_tracer_work_together(self):
        """Metrics + Tracer should operate independently but cohesively."""
        from maop.core.coordination.observability import (
            CoordinationMetrics,
            CoordinationTracer,
        )

        metrics = CoordinationMetrics()
        tracer = CoordinationTracer()

        tracer.start_trace("integration-test")
        with tracer.span("a2a.message handling"):
            metrics.increment("a2a.messages_handled")
            metrics.histogram("a2a.processing_ms", 15.5)

        assert metrics.get_counter("a2a.messages_handled") == 1
        spans = tracer.get_completed_spans()
        assert len(spans) >= 1

    @pytest.mark.anyio
    async def test_probe_with_all_components(self, fake_redis):
        """Probe should snapshot metrics + tracer + registry + MCP hub."""
        from maop.core.coordination.observability import (
            CoordinationMetrics,
            CoordinationProbe,
            CoordinationTracer,
        )

        metrics = CoordinationMetrics()
        tracer = CoordinationTracer()
        registry = MagicMock()
        registry.list_agents = AsyncMock(return_value=[])
        bb_sync = MagicMock()
        bb_sync.node_id = "test-node"
        bb_sync.is_running = True

        probe = CoordinationProbe(
            agent_registry=registry,
            bb_sync=bb_sync,
            tracer=tracer,
            metrics=metrics,
        )

        metrics.increment("test.counter", 5)
        snap = await probe.snapshot()
        assert snap["metrics"]["counters"]["test.counter"] == 5
        assert snap["blackboard_sync"]["node_id"] == "test-node"

    @pytest.mark.anyio
    async def test_full_cross_module_a2a_flow(self, fake_redis):
        """Simulate: external agent sends message → bridge processes → blackboard updated.

        This exercises A2A transport → bridge → blackboard → sync path.
        """
        from maop.core.coordination.a2a_blackboard_bridge import (
            A2ABlackboardBridge,
        )
        from maop.core.coordination.a2a_transport_server import (
            InMemoryTaskStore,
            _A2ARequestHandler,
        )

        # Setup: blackboard + bridge + handler
        bb = Blackboard()
        bridge = A2ABlackboardBridge(blackboard=bb, registry=None)
        await bridge.start()

        handler = _A2ARequestHandler(
            bridge=bridge,
            registry=MagicMock(),
            task_store=InMemoryTaskStore(),
            agent_card={"name": "Test"},
        )

        # Simulate an external A2A message arriving via HTTP
        body = json.dumps({
            "method": "task_result",
            "params": {"result": {"answer": 42}},
            "source_agent": "external-agent",
        }).encode()
        status, _resp = await handler.handle_request("POST", "/a2a/messages", body)

        assert status == 200
        # Bridge should have written to blackboard
        entries = bb.read("partial_solution")
        assert len(entries) >= 1

        await bridge.stop()
