"""Tests for P3: Access-Count consolidation, P4: Tool Audit Log, P5: Agent Bridge."""

import shutil
import tempfile

import pytest

from maop.core.three_layer_memory import ThreeLayerMemory
from maop.core.tool_audit import ToolAuditLog
from maop.core.agent_bridge import AgentAdapter, AgentBridge


# ── P3: Access-Count Consolidation ────────────────────────────

@pytest.fixture
def mem_env():
    tmpdir = tempfile.mkdtemp()
    mem = ThreeLayerMemory(root_dir=tmpdir, working_max=50, working_ttl=60)
    yield mem
    shutil.rmtree(tmpdir, ignore_errors=True)


class TestAccessCountConsolidation:
    def test_access_count_field_exists(self, mem_env):
        eid = mem_env.episodic_store(task="test task", agent="claude", outcome="success")
        entry = mem_env.episodic_get(eid)
        assert entry is not None
        assert entry.access_count == 0

    def test_search_increments_access_count(self, mem_env):
        eid = mem_env.episodic_store(task="unique_task_xyz", agent="claude", outcome="success")
        mem_env.episodic_search(query="unique_task_xyz")
        entry = mem_env.episodic_get(eid)
        assert entry is not None
        assert entry.access_count >= 1

    def test_multiple_searches_increment(self, mem_env):
        eid = mem_env.episodic_store(task="multi_access_task", agent="claude", outcome="success")
        for _ in range(5):
            mem_env.episodic_search(query="multi_access_task")
        entry = mem_env.episodic_get(eid)
        assert entry is not None
        assert entry.access_count >= 5

    def test_consolidate_by_access(self, mem_env):
        mem_env.episodic_store(task="frequent_task", agent="claude", outcome="success", score=0.5)
        for _ in range(4):
            mem_env.episodic_search(query="frequent_task")
        report = mem_env.consolidate_by_access(min_access_count=3)
        assert report.candidates >= 1
        assert report.consolidated >= 1

    def test_consolidate_by_access_skips_low_count(self, mem_env):
        mem_env.episodic_store(task="rare_task", agent="claude", outcome="success", score=0.5)
        mem_env.episodic_search(query="rare_task")
        report = mem_env.consolidate_by_access(min_access_count=3)
        assert report.consolidated == 0


# ── P4: Tool Audit Log ────────────────────────────────────────

@pytest.fixture
def audit_env():
    tmpdir = tempfile.mkdtemp()
    audit = ToolAuditLog(root_dir=tmpdir)
    yield audit
    shutil.rmtree(tmpdir, ignore_errors=True)


class TestToolAuditRecord:
    def test_record_returns_id(self, audit_env):
        eid = audit_env.record(tool_name="file_read", agent="claude")
        assert eid

    def test_record_and_query(self, audit_env):
        audit_env.record(tool_name="file_read", agent="claude", duration_ms=50, success=True)
        entries = audit_env.query(tool_name="file_read")
        assert len(entries) == 1
        assert entries[0].tool_name == "file_read"
        assert entries[0].agent == "claude"
        assert entries[0].duration_ms == 50
        assert entries[0].success is True

    def test_record_failure(self, audit_env):
        audit_env.record(tool_name="shell_exec", agent="claude", success=False, error_message="timeout")
        entries = audit_env.query(success=False)
        assert len(entries) == 1
        assert entries[0].success is False
        assert entries[0].error_message == "timeout"

    def test_query_by_agent(self, audit_env):
        audit_env.record(tool_name="t1", agent="claude")
        audit_env.record(tool_name="t2", agent="gpt")
        entries = audit_env.query(agent="claude")
        assert len(entries) == 1
        assert entries[0].agent == "claude"

    def test_query_with_limit(self, audit_env):
        for i in range(10):
            audit_env.record(tool_name=f"tool_{i}", agent="claude")
        entries = audit_env.query(limit=5)
        assert len(entries) == 5


class TestToolAuditStats:
    def test_empty_stats(self, audit_env):
        stats = audit_env.stats()
        assert stats.total_calls == 0

    def test_stats_after_calls(self, audit_env):
        audit_env.record(tool_name="t1", agent="claude", duration_ms=100, success=True)
        audit_env.record(tool_name="t2", agent="gpt", duration_ms=200, success=False)
        stats = audit_env.stats()
        assert stats.total_calls == 2
        assert stats.success_calls == 1
        assert stats.failed_calls == 1
        assert stats.avg_duration_ms > 0

    def test_stats_by_tool(self, audit_env):
        audit_env.record(tool_name="file_read", agent="claude")
        audit_env.record(tool_name="file_read", agent="claude")
        audit_env.record(tool_name="shell_exec", agent="claude")
        stats = audit_env.stats()
        assert stats.by_tool.get("file_read") == 2
        assert stats.by_tool.get("shell_exec") == 1


class TestToolAuditCleanup:
    def test_cleanup_old_entries(self, audit_env):
        audit_env.record(tool_name="old", agent="claude")
        count = audit_env.cleanup(max_age_days=0)
        assert count >= 1


# ── P5: Agent Bridge ──────────────────────────────────────────

class MockAdapter(AgentAdapter):
    def __init__(self):
        self.connected = False
        self.config: dict = {}
        self.tasks: list[str] = []
        self.healthy = True

    def connect(self) -> bool:
        self.connected = True
        return True

    def execute(self, task: str, **kwargs) -> str:
        if not self.connected:
            raise RuntimeError("Not connected")
        self.tasks.append(task)
        return f"result:{task}"

    def health_check(self) -> bool:
        return self.healthy and self.connected

    def sync_config(self, config: dict) -> None:
        self.config = config

    def disconnect(self) -> None:
        self.connected = False


class FailingAdapter(AgentAdapter):
    def connect(self) -> bool:
        return False

    def execute(self, task: str, **kwargs) -> str:
        raise RuntimeError("Adapter failed")

    def health_check(self) -> bool:
        return False

    def sync_config(self, config: dict) -> None:
        pass

    def disconnect(self) -> None:
        pass


@pytest.fixture
def bridge_env():
    tmpdir = tempfile.mkdtemp()
    bridge = AgentBridge(root_dir=tmpdir)
    yield bridge
    shutil.rmtree(tmpdir, ignore_errors=True)


class TestAgentBridgeRegister:
    def test_register_and_list(self, bridge_env):
        adapter = MockAdapter()
        bridge_env.register("mock", adapter)
        assert "mock" in bridge_env.list_adapters()

    def test_unregister(self, bridge_env):
        adapter = MockAdapter()
        bridge_env.register("mock", adapter)
        bridge_env.unregister("mock")
        assert "mock" not in bridge_env.list_adapters()

    def test_get_adapter(self, bridge_env):
        adapter = MockAdapter()
        bridge_env.register("mock", adapter)
        assert bridge_env.get("mock") is adapter
        assert bridge_env.get("nonexistent") is None


class TestAgentBridgeCall:
    def test_call_success(self, bridge_env):
        adapter = MockAdapter()
        adapter.connected = True
        bridge_env.register("mock", adapter)
        result = bridge_env.call("mock", "hello")
        assert result == "result:hello"

    def test_call_not_registered(self, bridge_env):
        with pytest.raises(KeyError, match="not registered"):
            bridge_env.call("nope", "task")

    def test_call_failure_tracked(self, bridge_env):
        adapter = FailingAdapter()
        bridge_env.register("fail", adapter)
        with pytest.raises(RuntimeError):
            bridge_env.call("fail", "task")
        status = bridge_env.get_status("fail")
        assert status.error_count >= 1


class TestAgentBridgeConnectAll:
    def test_connect_all(self, bridge_env):
        adapter = MockAdapter()
        bridge_env.register("mock", adapter)
        results = bridge_env.connect_all()
        assert results["mock"] is True
        assert adapter.connected is True

    def test_connect_all_with_failure(self, bridge_env):
        adapter = FailingAdapter()
        bridge_env.register("fail", adapter)
        results = bridge_env.connect_all()
        assert results["fail"] is False


class TestAgentBridgeHealthCheck:
    def test_health_check_all(self, bridge_env):
        adapter = MockAdapter()
        adapter.connected = True
        bridge_env.register("mock", adapter)
        results = bridge_env.health_check_all()
        assert results["mock"] is True

    def test_health_check_unhealthy(self, bridge_env):
        adapter = MockAdapter()
        adapter.healthy = False
        adapter.connected = True
        bridge_env.register("mock", adapter)
        results = bridge_env.health_check_all()
        assert results["mock"] is False


class TestAgentBridgeSyncConfig:
    def test_sync_config(self, bridge_env):
        adapter = MockAdapter()
        adapter.connected = True
        bridge_env.register("mock", adapter)
        bridge_env.sync_config("mock", {"model": "gpt-4"})
        assert adapter.config == {"model": "gpt-4"}

    def test_sync_config_not_registered(self, bridge_env):
        with pytest.raises(KeyError):
            bridge_env.sync_config("nope", {})


class TestAgentBridgeGetStatus:
    def test_status_after_register(self, bridge_env):
        adapter = MockAdapter()
        bridge_env.register("mock", adapter)
        status = bridge_env.get_status("mock")
        assert status.name == "mock"
        assert status.adapter_type == "MockAdapter"

    def test_status_after_call(self, bridge_env):
        adapter = MockAdapter()
        adapter.connected = True
        bridge_env.register("mock", adapter)
        bridge_env.call("mock", "task1")
        status = bridge_env.get_status("mock")
        assert status.call_count >= 1


class TestAgentBridgeDisconnectAll:
    def test_disconnect_all(self, bridge_env):
        adapter = MockAdapter()
        adapter.connected = True
        bridge_env.register("mock", adapter)
        bridge_env.disconnect_all()
        assert adapter.connected is False


# ── P1: Working Memory Pin (ThreeLayerMemory) ─────────────────

class TestWorkingMemoryPin:
    def test_working_pin(self, mem_env):
        mem_env.working_put("key1", "value1")
        assert mem_env.working_pin("key1") is True

    def test_working_pin_nonexistent(self, mem_env):
        assert mem_env.working_pin("nope") is False

    def test_working_unpin(self, mem_env):
        mem_env.working_put("key1", "value1")
        mem_env.working_pin("key1")
        mem_env.working_unpin("key1")
        assert "key1" not in mem_env.working_pinned_keys()

    def test_working_pinned_keys(self, mem_env):
        mem_env.working_put("a", 1)
        mem_env.working_put("b", 2)
        mem_env.working_pin("a")
        mem_env.working_pin("b")
        assert set(mem_env.working_pinned_keys()) == {"a", "b"}