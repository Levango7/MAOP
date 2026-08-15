"""Tests for AgentForge Round 3: P1-P5 (Lifecycle, SemanticCache, HybridSearch, MCPDiscovery, PipelineCheckpoint)."""

import shutil
import tempfile
import time
from unittest.mock import patch

import pytest

from maop.core.agent.lifecycle.agent_lifecycle import (
    AgentLifecycle,
    AgentLifecycleManager,
    AgentState,
)
from maop.core.memory.hybrid_search import HybridSearch, rrf_fuse
from maop.core.memory.semantic_cache import SemanticCache
from maop.core.reliability.pipeline_checkpoint import PipelineCheckpoint

# ── P1: Agent Lifecycle ───────────────────────────────────────

class TestAgentLifecycle:
    def test_initial_state_is_init(self):
        lc = AgentLifecycle("test")
        assert lc.state == AgentState.INIT

    def test_valid_transition_init_to_ready(self):
        lc = AgentLifecycle("test")
        lc.transition(AgentState.READY)
        assert lc.state == AgentState.READY

    def test_valid_transition_ready_to_running(self):
        lc = AgentLifecycle("test")
        lc.transition(AgentState.READY)
        lc.transition(AgentState.RUNNING)
        assert lc.state == AgentState.RUNNING

    def test_valid_transition_running_to_paused(self):
        lc = AgentLifecycle("test")
        lc.transition(AgentState.READY)
        lc.transition(AgentState.RUNNING)
        lc.transition(AgentState.PAUSED)
        assert lc.state == AgentState.PAUSED

    def test_valid_transition_paused_to_running(self):
        lc = AgentLifecycle("test")
        lc.transition(AgentState.READY)
        lc.transition(AgentState.RUNNING)
        lc.transition(AgentState.PAUSED)
        lc.transition(AgentState.RUNNING)
        assert lc.state == AgentState.RUNNING

    def test_valid_transition_running_to_stopped(self):
        lc = AgentLifecycle("test")
        lc.transition(AgentState.READY)
        lc.transition(AgentState.RUNNING)
        lc.transition(AgentState.STOPPED)
        assert lc.state == AgentState.STOPPED

    def test_valid_transition_running_to_error(self):
        lc = AgentLifecycle("test")
        lc.transition(AgentState.READY)
        lc.transition(AgentState.RUNNING)
        lc.transition(AgentState.ERROR)
        assert lc.state == AgentState.ERROR

    def test_valid_transition_error_to_ready(self):
        lc = AgentLifecycle("test")
        lc.transition(AgentState.READY)
        lc.transition(AgentState.ERROR)
        lc.transition(AgentState.READY)
        assert lc.state == AgentState.READY

    def test_invalid_transition(self):
        lc = AgentLifecycle("test")
        with pytest.raises(ValueError, match="Invalid transition"):
            lc.transition(AgentState.RUNNING)

    def test_stopped_cannot_transition(self):
        lc = AgentLifecycle("test")
        lc.transition(AgentState.READY)
        lc.transition(AgentState.STOPPED)
        with pytest.raises(ValueError):
            lc.transition(AgentState.READY)

    def test_history_records_transitions(self):
        lc = AgentLifecycle("test")
        lc.transition(AgentState.READY)
        lc.transition(AgentState.RUNNING)
        assert len(lc.history) == 2
        assert lc.history[0].from_state == "init"
        assert lc.history[0].to_state == "ready"

    def test_transition_with_reason(self):
        lc = AgentLifecycle("test")
        record = lc.transition(AgentState.READY, reason="initialized")
        assert record.reason == "initialized"

    def test_can_transition(self):
        lc = AgentLifecycle("test")
        assert lc.can_transition(AgentState.READY) is True
        assert lc.can_transition(AgentState.RUNNING) is False

    def test_state_duration(self):
        lc = AgentLifecycle("test")
        assert lc.state_duration_s >= 0

    def test_force_state(self):
        lc = AgentLifecycle("test")
        lc.force_state(AgentState.ERROR, reason="test")
        assert lc.state == AgentState.ERROR

    def test_reset(self):
        lc = AgentLifecycle("test")
        lc.transition(AgentState.READY)
        lc.reset()
        assert lc.state == AgentState.INIT

    def test_summary(self):
        lc = AgentLifecycle("test")
        lc.transition(AgentState.READY)
        s = lc.summary()
        assert s["current_state"] == "ready"
        assert s["total_transitions"] == 1

    def test_hook(self):
        transitions = []
        lc = AgentLifecycle("test")
        lc.on_transition(lambda old, new, rec: transitions.append((old.value, new.value)))
        lc.transition(AgentState.READY)
        assert len(transitions) == 1
        assert transitions[0] == ("init", "ready")


class TestAgentLifecycleManager:
    def test_get_creates_lifecycle(self):
        mgr = AgentLifecycleManager()
        lc = mgr.get("claude")
        assert lc.state == AgentState.INIT

    def test_list_agents(self):
        mgr = AgentLifecycleManager()
        mgr.get("claude")
        mgr.get("gpt")
        assert set(mgr.list_agents()) == {"claude", "gpt"}

    def test_agents_by_state(self):
        mgr = AgentLifecycleManager()
        mgr.get("claude").transition(AgentState.READY)
        mgr.get("gpt")
        assert mgr.agents_by_state(AgentState.READY) == ["claude"]

    def test_running_agents(self):
        mgr = AgentLifecycleManager()
        mgr.get("claude").transition(AgentState.READY)
        mgr.get("claude").transition(AgentState.RUNNING)
        assert mgr.running_agents() == ["claude"]

    def test_summary(self):
        mgr = AgentLifecycleManager()
        mgr.get("claude")
        s = mgr.summary()
        assert s["total_agents"] == 1


# ── P2: Semantic Cache ────────────────────────────────────────

class TestSemanticCache:
    def test_put_and_get_exact(self):
        cache = SemanticCache(similarity_threshold=0.5)
        cache.put("hello world", "response1")
        result = cache.get("hello world")
        assert result == "response1"

    def test_get_miss(self):
        cache = SemanticCache(similarity_threshold=0.99)
        cache.put("hello world", "response1")
        result = cache.get("completely different topic about quantum physics")
        assert result is None

    def test_stats(self):
        cache = SemanticCache(similarity_threshold=0.5)
        cache.put("q1", "r1")
        cache.get("q1")
        cache.get("missing")
        stats = cache.stats()
        assert stats.entries == 1
        assert stats.hits >= 1

    def test_clear(self):
        cache = SemanticCache()
        cache.put("q1", "r1")
        cache.clear()
        assert cache.stats().entries == 0

    def test_max_entries_eviction(self):
        cache = SemanticCache(max_entries=2)
        cache.put("q1", "r1")
        cache.put("q2", "r2")
        cache.put("q3", "r3")
        assert cache.stats().entries <= 2

    def test_ttl_expiration(self):
        cache = SemanticCache(default_ttl_s=0.1)
        cache.put("q1", "r1", ttl_s=0.1)
        time.sleep(0.2)
        cache.cleanup_expired()
        assert cache.stats().entries == 0


# ── P3: Hybrid Search (RRF) ──────────────────────────────────

class TestRRFFusion:
    def test_single_list(self):
        results = rrf_fuse([("a", 0.9), ("b", 0.8)], [])
        assert results["a"] > results["b"]

    def test_both_lists(self):
        vec = [("a", 0.9), ("b", 0.8)]
        kw = [("b", 1.0), ("c", 0.7)]
        results = rrf_fuse(vec, kw)
        assert "b" in results
        assert results["b"] > results["a"]

    def test_empty_lists(self):
        results = rrf_fuse([], [])
        assert results == {}


class TestHybridSearch:
    def test_search_no_data(self):
        tmpdir = tempfile.mkdtemp()
        try:
            # Force HashEmbedding by making SentenceTransformerEmbedding
            # unavailable (avoids network model download in CI/offline).
            with patch(
                "maop.core.memory.vector.SentenceTransformerEmbedding",
                side_effect=ImportError("offline"),
            ):
                hs = HybridSearch(root_dir=tmpdir)
                results = hs.search("test query")
            assert isinstance(results, list)
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)


# ── P5: Pipeline Checkpoint ──────────────────────────────────

@pytest.fixture
def ckpt_env():
    tmpdir = tempfile.mkdtemp()
    ckpt = PipelineCheckpoint(root_dir=tmpdir)
    yield ckpt
    shutil.rmtree(tmpdir, ignore_errors=True)


class TestPipelineCheckpoint:
    def test_start_run(self, ckpt_env):
        run_id = ckpt_env.start_run("test_workflow", steps=["a", "b", "c"])
        assert run_id

    def test_complete_step(self, ckpt_env):
        run_id = ckpt_env.start_run("wf", steps=["s1", "s2"])
        ok = ckpt_env.complete_step(run_id, "s1", output="done")
        assert ok is True

    def test_pending_steps(self, ckpt_env):
        run_id = ckpt_env.start_run("wf", steps=["s1", "s2"])
        ckpt_env.complete_step(run_id, "s1")
        pending = ckpt_env.pending_steps(run_id)
        assert pending == ["s2"]

    def test_completed_steps(self, ckpt_env):
        run_id = ckpt_env.start_run("wf", steps=["s1", "s2"])
        ckpt_env.complete_step(run_id, "s1")
        completed = ckpt_env.completed_steps(run_id)
        assert completed == ["s1"]

    def test_get_run(self, ckpt_env):
        run_id = ckpt_env.start_run("wf", steps=["s1", "s2"])
        state = ckpt_env.get_run(run_id)
        assert state is not None
        assert state.run_id == run_id
        assert state.workflow_name == "wf"
        assert len(state.steps) == 2

    def test_fail_step(self, ckpt_env):
        run_id = ckpt_env.start_run("wf", steps=["s1"])
        ckpt_env.fail_step(run_id, "s1", error="boom")
        state = ckpt_env.get_run(run_id)
        assert state.steps[0].status == "failed"

    def test_resume_run(self, ckpt_env):
        run_id = ckpt_env.start_run("wf", steps=["s1"])
        ckpt_env.fail_step(run_id, "s1")
        state = ckpt_env.resume_run(run_id)
        assert state is not None
        assert state.status == "running"

    def test_finish_run(self, ckpt_env):
        run_id = ckpt_env.start_run("wf", steps=["s1"])
        ckpt_env.complete_step(run_id, "s1")
        ckpt_env.finish_run(run_id)
        state = ckpt_env.get_run(run_id)
        assert state.status == "completed"

    def test_list_runs(self, ckpt_env):
        ckpt_env.start_run("wf1", steps=["a"])
        ckpt_env.start_run("wf2", steps=["b"])
        runs = ckpt_env.list_runs()
        assert len(runs) == 2

    def test_list_runs_by_workflow(self, ckpt_env):
        ckpt_env.start_run("wf1", steps=["a"])
        ckpt_env.start_run("wf2", steps=["b"])
        runs = ckpt_env.list_runs(workflow_name="wf1")
        assert len(runs) == 1

    def test_start_step(self, ckpt_env):
        run_id = ckpt_env.start_run("wf", steps=["s1"])
        ckpt_env.start_step(run_id, "s1")
        state = ckpt_env.get_run(run_id)
        assert state.steps[0].status == "running"

    def test_cleanup_old_runs(self, ckpt_env):
        run_id = ckpt_env.start_run("wf", steps=["s1"])
        ckpt_env.complete_step(run_id, "s1")
        ckpt_env.finish_run(run_id)
        removed = ckpt_env.cleanup(max_age_days=0)
        assert removed >= 1

    def test_complete_nonexistent_step(self, ckpt_env):
        run_id = ckpt_env.start_run("wf", steps=["s1"])
        ok = ckpt_env.complete_step(run_id, "nonexistent")
        assert ok is False

    def test_get_nonexistent_run(self, ckpt_env):
        assert ckpt_env.get_run("nope") is None
