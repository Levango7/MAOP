"""Tests for P3: Access-Count consolidation, P4: Tool Audit Log, P1: Working Memory Pin."""

import shutil
import tempfile

import pytest

from maop.core.memory.three_layer_memory import ThreeLayerMemory
from maop.core.agent.tools.tool_audit import ToolAuditLog

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
