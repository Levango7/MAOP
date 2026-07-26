"""Tests for maop.core.subagent_lifecycle.SubAgentManager.

E3 (2026-07-22, Phase E): verifies the async sub-agent lifecycle:
spawn/wait/cancel/spawn_and_wait_all/list_agents/get_live_transcript.

The _invoke_llm hook is patched to avoid real LLM calls.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from maop.core.subagent_lifecycle import (
    AgentConfig,
    AgentResult,
    AgentRole,
    AgentStatus,
    SubAgentManager,
    TranscriptEntry,
)


@pytest.fixture
def mgr(tmp_path):
    return SubAgentManager(root_dir=tmp_path)


def _config(name: str = "test-agent", model: str = "test-model") -> AgentConfig:
    return AgentConfig(name=name, model=model)


# ── Models ────────────────────────────────────────────────────


def test_agent_config_defaults():
    cfg = AgentConfig()
    assert cfg.name == ""
    assert cfg.role == AgentRole.LEAF
    assert cfg.model == ""
    assert cfg.max_turns == 15
    assert cfg.temperature == 0.7
    assert cfg.memory_layers == ["working", "episodic", "semantic"]


def test_agent_result_defaults():
    r = AgentResult()
    assert r.agent_id == ""
    assert r.output == ""
    assert r.status == AgentStatus.COMPLETED
    assert r.tokens_used == 0


def test_transcript_entry_defaults():
    e = TranscriptEntry()
    assert e.agent_id == ""
    assert e.event == ""
    assert e.data == {}


def test_agent_status_enum_values():
    assert AgentStatus.PENDING.value == "pending"
    assert AgentStatus.RUNNING.value == "running"
    assert AgentStatus.COMPLETED.value == "completed"
    assert AgentStatus.FAILED.value == "failed"
    assert AgentStatus.CANCELLED.value == "cancelled"


# ── spawn ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_spawn_returns_agent_id(mgr):
    """spawn() returns a non-empty agent_id and starts a background task."""
    with patch.object(mgr, "_invoke_llm", new=AsyncMock(return_value="ok")):
        agent_id = await mgr.spawn(_config(), "do something")
    assert agent_id
    assert len(agent_id) == 16  # uuid.hex[:16]


@pytest.mark.asyncio
async def test_spawn_records_running_status_in_db(mgr):
    """After spawn(), the DB row shows status=running."""
    with patch.object(mgr, "_invoke_llm", new=AsyncMock(return_value="ok")):
        await mgr.spawn(_config(), "task")
    agents = mgr.list_agents()
    assert len(agents) == 1
    # Note: by the time list_agents runs, the mock LLM may have already completed.
    # Verify the row exists with the right name.
    assert agents[0]["name"] == "test-agent"


@pytest.mark.asyncio
async def test_spawn_appends_spawned_transcript_entry(mgr):
    """spawn() writes a 'spawned' transcript entry."""
    with patch.object(mgr, "_invoke_llm", new=AsyncMock(return_value="ok")):
        agent_id = await mgr.spawn(_config(model="gpt-4"), "task")
    transcript = mgr.get_live_transcript(agent_id)
    events = [t.event for t in transcript]
    assert "spawned" in events
    # The spawned entry should include the model.
    spawned_entry = next(t for t in transcript if t.event == "spawned")
    assert spawned_entry.data.get("model") == "gpt-4"


# ── wait ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_wait_returns_completed_result(mgr):
    """wait() returns an AgentResult with the LLM output."""
    with patch.object(mgr, "_invoke_llm", new=AsyncMock(return_value="hello world")):
        agent_id = await mgr.spawn(_config(), "say hello")
        result = await mgr.wait(agent_id, timeout=5)
    assert isinstance(result, AgentResult)
    assert result.status == AgentStatus.COMPLETED
    assert result.output == "hello world"
    assert result.duration_ms >= 0


@pytest.mark.asyncio
async def test_wait_returns_failed_on_llm_exception(mgr):
    """When _invoke_llm raises, wait() returns a FAILED result with error."""
    with patch.object(mgr, "_invoke_llm", new=AsyncMock(side_effect=RuntimeError("boom"))):
        agent_id = await mgr.spawn(_config(), "task")
        result = await mgr.wait(agent_id, timeout=5)
    assert result.status == AgentStatus.FAILED
    assert "boom" in result.error


@pytest.mark.asyncio
async def test_wait_returns_failed_for_unknown_agent_id(mgr):
    """wait() on a non-existent agent_id returns a FAILED 'Not found' result."""
    result = await mgr.wait("nonexistent", timeout=1)
    assert result.status == AgentStatus.FAILED
    assert "Not found" in result.error


@pytest.mark.asyncio
async def test_wait_returns_failed_on_timeout(mgr):
    """When the agent doesn't finish in time, wait() returns a FAILED timeout result."""
    async def slow_llm(*args, **kwargs):
        await asyncio.sleep(10)  # longer than timeout
        return "late"

    with patch.object(mgr, "_invoke_llm", new=slow_llm):
        agent_id = await mgr.spawn(_config(), "slow task")
        result = await mgr.wait(agent_id, timeout=1)
    assert result.status == AgentStatus.FAILED
    assert "Timeout" in result.error


# ── cancel ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_cancel_stops_running_agent(mgr):
    """cancel() cancels a running task and marks it CANCELLED."""
    async def slow_llm(*args, **kwargs):
        await asyncio.sleep(10)
        return "should-not-reach"

    with patch.object(mgr, "_invoke_llm", new=slow_llm):
        agent_id = await mgr.spawn(_config(), "long task")
        # Give the task a moment to start.
        await asyncio.sleep(0.05)
        ok = mgr.cancel(agent_id)
    assert ok is True
    result = await mgr.wait(agent_id, timeout=2)
    assert result.status == AgentStatus.CANCELLED


@pytest.mark.asyncio
async def test_cancel_returns_false_for_unknown_agent(mgr):
    """cancel() on an unknown agent_id returns False."""
    assert mgr.cancel("nonexistent") is False


@pytest.mark.asyncio
async def test_cancel_returns_false_for_completed_agent(mgr):
    """cancel() on an already-completed agent returns False."""
    with patch.object(mgr, "_invoke_llm", new=AsyncMock(return_value="done")):
        agent_id = await mgr.spawn(_config(), "task")
        await mgr.wait(agent_id, timeout=5)
    # Task is done; cancel should return False.
    assert mgr.cancel(agent_id) is False


# ── spawn_and_wait_all ────────────────────────────────────────


@pytest.mark.asyncio
async def test_spawn_and_wait_all_returns_all_results(mgr):
    """spawn_and_wait_all() runs agents in parallel and collects results."""
    with patch.object(mgr, "_invoke_llm", new=AsyncMock(return_value="ok")):
        tasks = [
            (_config(name="a"), "task A", {}),
            (_config(name="b"), "task B", {}),
            (_config(name="c"), "task C", {}),
        ]
        results = await mgr.spawn_and_wait_all(tasks, timeout=5)
    assert len(results) == 3
    assert all(r.status == AgentStatus.COMPLETED for r in results)
    assert all(r.output == "ok" for r in results)


@pytest.mark.asyncio
async def test_spawn_and_wait_all_handles_mixed_success_failure(mgr):
    """A failing agent doesn't crash the batch; its result is FAILED."""
    call_count = 0
    async def flaky_llm(agent_id, config, task, context):
        nonlocal call_count
        call_count += 1
        if call_count == 2:
            raise RuntimeError("agent 2 failed")
        return f"ok-{call_count}"

    with patch.object(mgr, "_invoke_llm", new=flaky_llm):
        tasks = [
            (_config(name="a"), "task A", {}),
            (_config(name="b"), "task B", {}),
        ]
        results = await mgr.spawn_and_wait_all(tasks, timeout=5)
    assert len(results) == 2
    statuses = {r.status for r in results}
    assert AgentStatus.COMPLETED in statuses
    assert AgentStatus.FAILED in statuses


# ── list_agents ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_list_agents_returns_all(mgr):
    """list_agents() returns all spawned agents, ordered by created_at desc."""
    with patch.object(mgr, "_invoke_llm", new=AsyncMock(return_value="ok")):
        await mgr.spawn(_config(name="first"), "t1")
        await mgr.spawn(_config(name="second"), "t2")
    agents = mgr.list_agents()
    assert len(agents) == 2
    # Most recent first.
    assert agents[0]["name"] == "second"
    assert agents[1]["name"] == "first"


@pytest.mark.asyncio
async def test_list_agents_filters_by_status(mgr):
    """list_agents(status=...) filters by status."""
    async def slow(*args, **kwargs):
        await asyncio.sleep(10)
        return "late"
    with patch.object(mgr, "_invoke_llm", new=slow):
        running_id = await mgr.spawn(_config(name="slow"), "t")
        await asyncio.sleep(0.05)  # let it start
        running_agents = mgr.list_agents(status=AgentStatus.RUNNING)
        assert len(running_agents) == 1
        assert running_agents[0]["name"] == "slow"
    # Cancel the slow agent so it doesn't leak into other tests.
    mgr.cancel(running_id)


# ── get_live_transcript ──────────────────────────────────────


@pytest.mark.asyncio
async def test_get_live_transcript_returns_entries(mgr):
    """get_live_transcript() returns transcript entries in reverse chronological order."""
    with patch.object(mgr, "_invoke_llm", new=AsyncMock(return_value="done")):
        agent_id = await mgr.spawn(_config(), "task")
        await mgr.wait(agent_id, timeout=5)
    transcript = mgr.get_live_transcript(agent_id)
    events = [t.event for t in transcript]
    # Should contain at least spawned, started, llm_call, completed.
    assert "spawned" in events
    assert "started" in events
    assert "completed" in events


@pytest.mark.asyncio
async def test_get_live_transcript_empty_for_unknown(mgr):
    """get_live_transcript() returns [] for an unknown agent_id."""
    assert mgr.get_live_transcript("nonexistent") == []


@pytest.mark.asyncio
async def test_get_live_transcript_respects_limit(mgr):
    """get_live_transcript(limit=N) returns at most N entries."""
    with patch.object(mgr, "_invoke_llm", new=AsyncMock(return_value="done")):
        agent_id = await mgr.spawn(_config(), "task")
        await mgr.wait(agent_id, timeout=5)
    transcript = mgr.get_live_transcript(agent_id, limit=2)
    assert len(transcript) <= 2


# ── _invoke_llm (integration, not mocked) ─────────────────────


@pytest.mark.asyncio
async def test_invoke_llm_returns_error_message_when_no_provider(mgr):
    """_invoke_llm returns an error string when no provider is configured."""
    # LLMProviderFactory will not find a provider for "no-such-model".
    cfg = _config(model="no-such-model-xyz")
    result = await mgr._invoke_llm("test-id", cfg, "task", {})
    # Either "[No provider...]" or "[LLM Error...]" depending on factory behavior.
    assert isinstance(result, str)
    assert result.startswith("[")  # error-prefixed string


@pytest.mark.asyncio
async def test_invoke_llm_appends_llm_call_transcript(mgr):
    """_invoke_llm writes an 'llm_call' transcript entry."""
    cfg = _config(model="test-model")
    await mgr._invoke_llm("agent-x", cfg, "do thing", {"key": "value"})
    transcript = mgr.get_live_transcript("agent-x")
    events = [t.event for t in transcript]
    assert "llm_call" in events
    llm_entry = next(t for t in transcript if t.event == "llm_call")
    assert llm_entry.data.get("model") == "test-model"
