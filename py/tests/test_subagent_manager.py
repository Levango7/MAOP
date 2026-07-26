"""Tests for SubAgent Manager — spawn/wait/cancel/parallel."""

import shutil
import tempfile

import pytest

from maop.core.subagent_manager import (
    AgentConfig,
    AgentResult,
    AgentStatus,
    SubAgentManager,
)


@pytest.fixture
def mgr():
    tmpdir = tempfile.mkdtemp()
    manager = SubAgentManager(root_dir=tmpdir)
    yield manager
    shutil.rmtree(tmpdir, ignore_errors=True)


def _config(name: str = "test-agent", model: str = "test-model") -> AgentConfig:
    return AgentConfig(name=name, model=model, system_prompt="You are a test agent.")


class TestSpawnAndWait:
    @pytest.mark.asyncio
    async def test_spawn_and_wait(self, mgr):
        agent_id = await mgr.spawn(_config(), task="Say hello")
        result = await mgr.wait(agent_id, timeout=10)
        assert isinstance(result, AgentResult)
        assert result.agent_id == agent_id

    @pytest.mark.asyncio
    async def test_spawn_creates_transcript(self, mgr):
        agent_id = await mgr.spawn(_config(), task="Test transcript")
        await mgr.wait(agent_id, timeout=10)
        transcript = mgr.get_live_transcript(agent_id)
        assert len(transcript) >= 2
        events = [t.event for t in transcript]
        assert "spawned" in events

    @pytest.mark.asyncio
    async def test_wait_nonexistent(self, mgr):
        result = await mgr.wait("nonexistent", timeout=5)
        assert result.status == AgentStatus.FAILED
        assert "Not found" in result.error


class TestParallelExecution:
    @pytest.mark.asyncio
    async def test_spawn_and_wait_all(self, mgr):
        tasks = [
            (_config("agent-a", "model-a"), "Task A", {"key": "a"}),
            (_config("agent-b", "model-b"), "Task B", {"key": "b"}),
        ]
        results = await mgr.spawn_and_wait_all(tasks, timeout=15)
        assert len(results) == 2
        assert all(isinstance(r, AgentResult) for r in results)

    @pytest.mark.asyncio
    async def test_spawn_and_wait_all_empty(self, mgr):
        results = await mgr.spawn_and_wait_all([], timeout=5)
        assert results == []


class TestCancel:
    @pytest.mark.asyncio
    async def test_cancel_running(self, mgr):
        config = AgentConfig(name="slow-agent", model="test")
        agent_id = await mgr.spawn(config, task="Long running task")
        cancelled = mgr.cancel(agent_id)
        assert cancelled is True

    @pytest.mark.asyncio
    async def test_cancel_nonexistent(self, mgr):
        assert mgr.cancel("nonexistent") is False


class TestTranscript:
    @pytest.mark.asyncio
    async def test_transcript_events(self, mgr):
        agent_id = await mgr.spawn(_config(), task="Transcript test")
        await mgr.wait(agent_id, timeout=10)
        transcript = mgr.get_live_transcript(agent_id)
        events = {t.event for t in transcript}
        assert "spawned" in events

    @pytest.mark.asyncio
    async def test_transcript_limit(self, mgr):
        agent_id = await mgr.spawn(_config(), task="Limit test")
        await mgr.wait(agent_id, timeout=10)
        transcript = mgr.get_live_transcript(agent_id, limit=1)
        assert len(transcript) <= 1


class TestListAgents:
    @pytest.mark.asyncio
    async def test_list_agents(self, mgr):
        await mgr.spawn(_config("a"), task="Task A")
        await mgr.spawn(_config("b"), task="Task B")
        agents = mgr.list_agents()
        assert len(agents) >= 2

    @pytest.mark.asyncio
    async def test_list_by_status(self, mgr):
        agent_id = await mgr.spawn(_config(), task="Status test")
        await mgr.wait(agent_id, timeout=10)
        completed = mgr.list_agents(status=AgentStatus.COMPLETED)
        assert len(completed) >= 1
        assert completed[0]["status"] == "completed"
