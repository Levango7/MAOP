"""Tests for MAOP.core.react_loop, MAOP.core.change_tracker, and MAOP.core.artifact_store."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from maop.core.agent.llm_chat.react_loop import (
    ReactConfig,
    ReactLoop,
    ReactPhase,
    ReactResult,
    ReactStep,
)
from maop.core.backends.artifact_store import ArtifactStore
from maop.core.reliability.change_tracker import ChangeTracker

# ── ReactLoop (unit tests, no real LLM) ────────────────────────


class TestReactConfig:
    def test_defaults(self):
        cfg = ReactConfig()
        assert cfg.max_iterations == 10
        assert cfg.max_tool_calls == 30
        assert cfg.provider == "openai"

    def test_custom(self):
        cfg = ReactConfig(max_iterations=5, provider="anthropic")
        assert cfg.max_iterations == 5
        assert cfg.provider == "anthropic"


class TestReactStep:
    def test_thought_step(self):
        step = ReactStep(iteration=0, phase=ReactPhase.THOUGHT, content="I need to fix the bug")
        assert step.phase == ReactPhase.THOUGHT
        assert step.tool_name == ""

    def test_action_step(self):
        step = ReactStep(
            iteration=1, phase=ReactPhase.ACTION,
            tool_name="read_file", tool_args={"path": "/tmp/test.py"},
        )
        assert step.phase == ReactPhase.ACTION
        assert step.tool_name == "read_file"

    def test_observation_step(self):
        step = ReactStep(
            iteration=1, phase=ReactPhase.OBSERVATION,
            tool_result="file contents here",
        )
        assert step.phase == ReactPhase.OBSERVATION
        assert step.tool_result == "file contents here"


class TestReactResult:
    def test_default_result(self):
        result = ReactResult(task="test", agent="mavis")
        assert result.success is False
        assert result.total_iterations == 0
        assert result.steps == []

    def test_with_steps(self):
        result = ReactResult(
            task="fix bug", agent="mavis",
            steps=[
                ReactStep(iteration=0, phase=ReactPhase.THOUGHT, content="thinking"),
                ReactStep(iteration=0, phase=ReactPhase.FINAL, content="done"),
            ],
            final_answer="done",
            total_iterations=1,
            success=True,
        )
        assert result.success is True
        assert len(result.steps) == 2


class TestReactLoopInit:
    def test_init(self):
        loop = ReactLoop()
        assert loop.config.max_iterations == 10

    def test_custom_config(self):
        cfg = ReactConfig(max_iterations=3)
        loop = ReactLoop(config=cfg)
        assert loop.config.max_iterations == 3


# ── ChangeTracker ───────────────────────────────────────────────


class TestChangeTrackerSnapshot:
    def test_create_snapshot(self, tmp_path):
        tracker = ChangeTracker(root_dir=str(tmp_path))
        workdir = tmp_path / "project"
        workdir.mkdir()
        (workdir / "main.py").write_text("print('hello')", encoding="utf-8")
        snap_id = tracker.snapshot(str(workdir), label="v1")
        assert snap_id.startswith("snap-")
        snap = tracker.get_snapshot(snap_id)
        assert snap is not None
        assert snap.label == "v1"
        assert snap.file_count >= 1

    def test_list_snapshots(self, tmp_path):
        tracker = ChangeTracker(root_dir=str(tmp_path))
        workdir = tmp_path / "project"
        workdir.mkdir()
        (workdir / "a.py").write_text("a", encoding="utf-8")
        tracker.snapshot(str(workdir), label="s1")
        tracker.snapshot(str(workdir), label="s2")
        snaps = tracker.list_snapshots(str(workdir))
        assert len(snaps) >= 2

    def test_empty_directory_snapshot(self, tmp_path):
        tracker = ChangeTracker(root_dir=str(tmp_path))
        workdir = tmp_path / "empty_project"
        workdir.mkdir()
        snap_id = tracker.snapshot(str(workdir), label="empty")
        snap = tracker.get_snapshot(snap_id)
        assert snap is not None
        assert snap.file_count == 0


class TestChangeTrackerDiff:
    def test_detect_added_file(self, tmp_path):
        tracker = ChangeTracker(root_dir=str(tmp_path))
        workdir = tmp_path / "project"
        workdir.mkdir()
        tracker.snapshot(str(workdir), label="before")
        (workdir / "new_file.py").write_text("new content", encoding="utf-8")
        diff = tracker.diff(str(workdir), since_label="before")
        assert diff.added >= 1
        assert any(c.change_type == "added" for c in diff.changes)

    def test_detect_modified_file(self, tmp_path):
        tracker = ChangeTracker(root_dir=str(tmp_path))
        workdir = tmp_path / "project"
        workdir.mkdir()
        (workdir / "main.py").write_text("original", encoding="utf-8")
        tracker.snapshot(str(workdir), label="before")
        (workdir / "main.py").write_text("modified", encoding="utf-8")
        diff = tracker.diff(str(workdir), since_label="before")
        assert diff.modified >= 1

    def test_detect_deleted_file(self, tmp_path):
        tracker = ChangeTracker(root_dir=str(tmp_path))
        workdir = tmp_path / "project"
        workdir.mkdir()
        (workdir / "temp.py").write_text("temp", encoding="utf-8")
        tracker.snapshot(str(workdir), label="before")
        (workdir / "temp.py").unlink()
        diff = tracker.diff(str(workdir), since_label="before")
        assert diff.deleted >= 1

    def test_unauthorized_changes(self, tmp_path):
        tracker = ChangeTracker(root_dir=str(tmp_path))
        workdir = tmp_path / "project"
        workdir.mkdir()
        tracker.snapshot(str(workdir), label="before")
        (workdir / "unauthorized.py").write_text("bad", encoding="utf-8")
        diff = tracker.diff(str(workdir), since_label="before", authorized_paths=["allowed.py"])
        assert diff.has_unauthorized is True
        assert "unauthorized.py" in diff.unauthorized_paths

    def test_no_changes(self, tmp_path):
        tracker = ChangeTracker(root_dir=str(tmp_path))
        workdir = tmp_path / "project"
        workdir.mkdir()
        (workdir / "stable.py").write_text("stable", encoding="utf-8")
        tracker.snapshot(str(workdir), label="before")
        diff = tracker.diff(str(workdir), since_label="before")
        assert diff.added == 0
        assert diff.modified == 0
        assert diff.deleted == 0


class TestChangeTrackerLog:
    def test_change_log(self, tmp_path):
        tracker = ChangeTracker(root_dir=str(tmp_path))
        workdir = tmp_path / "project"
        workdir.mkdir()
        tracker.snapshot(str(workdir), label="before")
        (workdir / "new.py").write_text("new", encoding="utf-8")
        tracker.diff(str(workdir), since_label="before")
        log = tracker.get_change_log(str(workdir))
        assert len(log) >= 1

    def test_delete_snapshot(self, tmp_path):
        tracker = ChangeTracker(root_dir=str(tmp_path))
        workdir = tmp_path / "project"
        workdir.mkdir()
        snap_id = tracker.snapshot(str(workdir), label="delete-me")
        assert tracker.delete_snapshot(snap_id) is True
        assert tracker.get_snapshot(snap_id) is None


# ── ArtifactStore ───────────────────────────────────────────────


class TestArtifactSave:
    def test_save_and_load(self, tmp_path):
        store = ArtifactStore(root_dir=str(tmp_path))
        v = store.save("main.py", content="print('hello')")
        assert v == 1
        content = store.load("main.py")
        assert content == "print('hello')"

    def test_save_multiple_versions(self, tmp_path):
        store = ArtifactStore(root_dir=str(tmp_path))
        store.save("config.yaml", content="v1")
        v2 = store.save("config.yaml", content="v2")
        assert v2 == 2
        assert store.load("config.yaml") == "v2"
        assert store.load("config.yaml", version=1) == "v1"

    def test_save_with_tag(self, tmp_path):
        store = ArtifactStore(root_dir=str(tmp_path))
        store.save("main.py", content="hello", tag="stable")
        content = store.get_by_tag("main.py", "stable")
        assert content == "hello"


class TestArtifactHistory:
    def test_history(self, tmp_path):
        store = ArtifactStore(root_dir=str(tmp_path))
        store.save("main.py", content="v1")
        store.save("main.py", content="v2")
        store.save("main.py", content="v3")
        history = store.history("main.py")
        assert len(history) == 3
        assert history[0].version == 3  # Latest first

    def test_list_artifacts(self, tmp_path):
        store = ArtifactStore(root_dir=str(tmp_path))
        store.save("a.py", content="a")
        store.save("b.py", content="b")
        artifacts = store.list_artifacts()
        assert len(artifacts) == 2


class TestArtifactRestore:
    def test_restore(self, tmp_path):
        store = ArtifactStore(root_dir=str(tmp_path))
        store.save("main.py", content="original")
        store.save("main.py", content="modified")
        ok = store.restore("main.py", version=1)
        assert ok is True
        history = store.history("main.py")
        assert len(history) == 3  # v1, v2, v3 (restored)
        assert store.load("main.py") == "original"


class TestArtifactDiff:
    def test_diff_versions(self, tmp_path):
        store = ArtifactStore(root_dir=str(tmp_path))
        store.save("main.py", content="v1")
        store.save("main.py", content="v2")
        diff = store.diff_versions("main.py", 1, 2)
        assert diff["changed"] is True
        assert diff["v1_hash"] != diff["v2_hash"]

    def test_diff_same_version(self, tmp_path):
        store = ArtifactStore(root_dir=str(tmp_path))
        store.save("main.py", content="same")
        diff = store.diff_versions("main.py", 1, 1)
        assert diff["changed"] is False


class TestArtifactDelete:
    def test_delete_artifact(self, tmp_path):
        store = ArtifactStore(root_dir=str(tmp_path))
        store.save("temp.py", content="temp")
        assert store.delete_artifact("temp.py") is True
        assert store.load("temp.py") is None
        assert store.delete_artifact("temp.py") is False

    def test_load_nonexistent(self, tmp_path):
        store = ArtifactStore(root_dir=str(tmp_path))
        assert store.load("nonexistent.py") is None


class TestArtifactTag:
    def test_tag_version(self, tmp_path):
        store = ArtifactStore(root_dir=str(tmp_path))
        store.save("main.py", content="hello")
        assert store.tag_version("main.py", 1, "release") is True
        content = store.get_by_tag("main.py", "release")
        assert content == "hello"

    def test_get_by_nonexistent_tag(self, tmp_path):
        store = ArtifactStore(root_dir=str(tmp_path))
        store.save("main.py", content="hello")
        assert store.get_by_tag("main.py", "nonexistent") is None


# --- Merged from test_react_loop_coverage3.py ---
# Coverage tests (round 3) for maop.core.agent.react_loop.
#
# Targets: provider_factory, _get_bridge, _get_change_tracker,
# _estimate_tokens, _trim_conversation, _call_llm, run() branches.

# ── provider_factory property ───────────────────────────────────────


class TestProviderFactory:
    def test_factory_init_failure(self):
        """Cover LLMProviderFactory init failure (154-155)."""
        loop = ReactLoop()
        with patch(
            "maop.core.agent.llm_chat.llm_provider.LLMProviderFactory",
            side_effect=ImportError("no provider"),
        ):
            result = loop.provider_factory
        assert result is None

    def test_factory_cached(self):
        """Cover cached factory."""
        loop = ReactLoop()
        mock_factory = MagicMock()
        loop._provider_factory = mock_factory
        assert loop.provider_factory is mock_factory


# ── _get_bridge and _get_change_tracker ─────────────────────────────


class TestLazyInit:
    def test_get_bridge_cached(self):
        loop = ReactLoop()
        mock_bridge = MagicMock()
        loop._bridge = mock_bridge
        assert loop._get_bridge() is mock_bridge

    def test_get_change_tracker_no_root(self):
        """Cover _get_change_tracker with no root_dir (168)."""
        loop = ReactLoop(root_dir=None)
        result = loop._get_change_tracker()
        assert result is None

    def test_get_change_tracker_cached(self):
        loop = ReactLoop()
        mock_tracker = MagicMock()
        loop._change_tracker = mock_tracker
        assert loop._get_change_tracker() is mock_tracker

    def test_get_change_tracker_init_failure(self):
        """Cover _get_change_tracker init failure (172-173)."""
        loop = ReactLoop(root_dir="/nonexistent")
        with patch(
            "maop.core.reliability.change_tracker.ChangeTracker",
            side_effect=ImportError("no tracker"),
        ):
            result = loop._get_change_tracker()
        assert result is None


# ── _estimate_tokens ────────────────────────────────────────────────


class TestEstimateTokens:
    def test_string_content(self):
        messages = [{"role": "user", "content": "hello world"}]
        result = ReactLoop._estimate_tokens(messages)
        assert result > 0

    def test_list_content(self):
        """Cover list content blocks (183-186)."""
        messages = [
            {"role": "user", "content": [{"type": "text", "text": "hello"}, {"type": "text", "text": "world"}]}
        ]
        result = ReactLoop._estimate_tokens(messages)
        assert result > 0

    def test_with_tool_calls(self):
        """Cover tool_calls token estimation (187)."""
        messages = [
            {"role": "assistant", "content": "", "tool_calls": [{"id": "1", "function": {"name": "tool"}}]}
        ]
        result = ReactLoop._estimate_tokens(messages)
        assert result > 0


# ── _trim_conversation ──────────────────────────────────────────────


class TestTrimConversation:
    def test_short_conversation(self):
        """Cover short conversation (193-194)."""
        loop = ReactLoop()
        msgs = [{"role": "user", "content": "hi"}]
        result = loop._trim_conversation(msgs)
        assert result is msgs

    def test_under_limit(self):
        """Cover conversation under token limit (196-197)."""
        loop = ReactLoop(config=ReactConfig(max_total_tokens=100000))
        msgs = [
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": "hi"},
            {"role": "user", "content": "bye"},
        ]
        result = loop._trim_conversation(msgs)
        assert result is msgs

    def test_over_limit_trimming(self):
        """Cover conversation trimming (198-211)."""
        loop = ReactLoop(config=ReactConfig(max_total_tokens=10))
        msgs = [
            {"role": "system", "content": "system prompt"},
            {"role": "user", "content": "x" * 100},
            {"role": "assistant", "content": "y" * 100},
            {"role": "user", "content": "z" * 100},
            {"role": "assistant", "content": "final"},
        ]
        result = loop._trim_conversation(msgs)
        assert len(result) < len(msgs)
        # Should keep system messages
        assert any(m.get("role") == "system" for m in result)

    def test_over_limit_few_non_system(self):
        """Cover trimming with <= 2 non-system messages (204-205)."""
        loop = ReactLoop(config=ReactConfig(max_total_tokens=10))
        msgs = [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "x" * 100},
            {"role": "assistant", "content": "y" * 100},
        ]
        result = loop._trim_conversation(msgs)
        # Only 2 non-system → return as-is
        assert result is msgs


# ── _call_llm ───────────────────────────────────────────────────────


class TestCallLlm:
    @pytest.mark.asyncio
    async def test_factory_none(self):
        """Cover _call_llm with factory=None (245-250)."""
        loop = ReactLoop()
        loop._provider_factory = None
        with patch.object(type(loop), "provider_factory", None):
            result = await loop._call_llm([], "model", "trace")
        assert result.exit_code == -1
        assert "not available" in (result.error or "")

    @pytest.mark.asyncio
    async def test_llm_success(self):
        """Cover _call_llm success (262-269)."""
        loop = ReactLoop()
        mock_factory = MagicMock()
        mock_fb_result = MagicMock()
        mock_fb_result.response.content = "LLM response"
        mock_fb_result.response.latency_ms = 100
        mock_factory.chat_with_fallback = AsyncMock(return_value=mock_fb_result)
        loop._provider_factory = mock_factory

        result = await loop._call_llm(
            [{"role": "user", "content": "hi"}], "model", "trace",
            tools=[{"name": "tool1"}],
        )
        assert result.exit_code == 0
        assert result.stdout == "LLM response"

    @pytest.mark.asyncio
    async def test_llm_exception(self):
        """Cover _call_llm exception (270-279)."""
        loop = ReactLoop()
        mock_factory = MagicMock()
        mock_factory.chat_with_fallback = AsyncMock(side_effect=RuntimeError("boom"))
        loop._provider_factory = mock_factory

        result = await loop._call_llm([], "model", "trace")
        assert result.exit_code == -1
        assert "LLM call failed" in (result.error or "")


# ── run() — CLI path ────────────────────────────────────────────────


def _mock_dispatch_result(stdout="", error=None, success=True, exit_code=0, duration_ms=0):
    """Create a mock dispatch result."""
    mock_result = MagicMock()
    mock_result.is_success.return_value = success
    mock_result.stdout = stdout
    mock_result.error = error
    mock_result.exit_code = exit_code
    mock_result.trace_id = ""
    mock_result.duration_ms = duration_ms
    mock_dispatch = MagicMock()
    mock_dispatch.result = mock_result
    return mock_dispatch


class TestRunCliPath:
    @pytest.mark.asyncio
    async def test_final_answer_text(self):
        """Cover run() with text final answer (non-JSON stdout) (404-409)."""
        loop = ReactLoop(config=ReactConfig(max_iterations=1, enable_change_tracking=False))
        mock_dispatcher = MagicMock()
        mock_dispatcher.dispatch = AsyncMock(
            return_value=_mock_dispatch_result(stdout="This is the final answer")
        )
        result = await loop.run("task", "agent", mock_dispatcher)
        assert result.success is True
        assert result.final_answer == "This is the final answer"

    @pytest.mark.asyncio
    async def test_final_answer_no_tool_calls(self):
        """Cover run() with JSON but no tool calls (414-430)."""
        loop = ReactLoop(config=ReactConfig(max_iterations=1, enable_change_tracking=False))
        mock_dispatcher = MagicMock()
        mock_dispatcher.dispatch = AsyncMock(
            return_value=_mock_dispatch_result(
                stdout=json.dumps({"choices": [{"message": {"content": "final"}}]})
            )
        )
        with patch("maop.core.agent.llm_chat.function_call.FunctionCallBridge") as MockBridge:
            mock_bridge = MagicMock()
            mock_bridge.parse_response.return_value = []
            MockBridge.return_value = mock_bridge
            result = await loop.run("task", "agent", mock_dispatcher)
        assert result.success is True
        assert result.final_answer == "final"

    @pytest.mark.asyncio
    async def test_final_answer_anthropic_content(self):
        """Cover Anthropic content blocks (423-427)."""
        loop = ReactLoop(config=ReactConfig(max_iterations=1, enable_change_tracking=False))
        mock_dispatcher = MagicMock()
        mock_dispatcher.dispatch = AsyncMock(
            return_value=_mock_dispatch_result(
                stdout=json.dumps({
                    "content": [
                        {"type": "text", "text": "line1"},
                        {"type": "text", "text": "line2"},
                    ]
                })
            )
        )
        with patch("maop.core.agent.llm_chat.function_call.FunctionCallBridge") as MockBridge:
            mock_bridge = MagicMock()
            mock_bridge.parse_response.return_value = []
            MockBridge.return_value = mock_bridge
            result = await loop.run("task", "agent", mock_dispatcher, provider="anthropic")
        assert result.success is True
        assert "line1" in result.final_answer

    @pytest.mark.asyncio
    async def test_dispatch_error(self):
        """Cover dispatch exception (382-388)."""
        loop = ReactLoop(config=ReactConfig(max_iterations=1, enable_change_tracking=False))
        mock_dispatcher = MagicMock()
        mock_dispatcher.dispatch = AsyncMock(side_effect=RuntimeError("dispatch boom"))
        result = await loop.run("task", "agent", mock_dispatcher)
        assert result.success is False
        assert "Dispatch error" in result.error

    @pytest.mark.asyncio
    async def test_execution_failure(self):
        """Cover execution failure (390-396)."""
        loop = ReactLoop(config=ReactConfig(max_iterations=1, enable_change_tracking=False))
        mock_dispatcher = MagicMock()
        mock_dispatcher.dispatch = AsyncMock(
            return_value=_mock_dispatch_result(success=False, error="exec failed", exit_code=1)
        )
        result = await loop.run("task", "agent", mock_dispatcher)
        assert result.success is False
        assert "exec failed" in result.error

    @pytest.mark.asyncio
    async def test_max_iterations_exhausted(self):
        """Cover max iterations exhausted (502-504)."""
        loop = ReactLoop(config=ReactConfig(max_iterations=2, enable_change_tracking=False))
        mock_dispatcher = MagicMock()
        # Return JSON with tool calls to keep the loop going
        mock_dispatcher.dispatch = AsyncMock(
            return_value=_mock_dispatch_result(
                stdout=json.dumps({"choices": [{"message": {"tool_calls": [{"id": "1", "function": {"name": "tool"}}]}}]})
            )
        )
        with patch("maop.core.agent.llm_chat.function_call.FunctionCallBridge") as MockBridge:
            mock_bridge = MagicMock()
            mock_call = MagicMock()
            mock_call.name = "tool"
            mock_call.arguments = {}
            mock_call.id = "1"
            mock_bridge.parse_response.return_value = [mock_call]
            mock_bridge.execute = AsyncMock(return_value=MagicMock(output="result", error="", success=True, duration_ms=0))
            mock_bridge.format_result.return_value = {"role": "tool", "content": "result"}
            MockBridge.return_value = mock_bridge
            result = await loop.run("task", "agent", mock_dispatcher, provider="openai")
        assert result.success is False
        assert "exhausted" in (result.error or "")

    @pytest.mark.asyncio
    async def test_max_tool_calls_reached(self):
        """Cover max tool calls reached (444-447)."""
        loop = ReactLoop(config=ReactConfig(max_iterations=5, max_tool_calls=1, enable_change_tracking=False))
        mock_dispatcher = MagicMock()
        mock_dispatcher.dispatch = AsyncMock(
            return_value=_mock_dispatch_result(
                stdout=json.dumps({"choices": [{"message": {"tool_calls": [{"id": "1", "function": {"name": "tool"}}]}}]})
            )
        )
        with patch("maop.core.agent.llm_chat.function_call.FunctionCallBridge") as MockBridge:
            mock_bridge = MagicMock()
            mock_call = MagicMock()
            mock_call.name = "tool"
            mock_call.arguments = {}
            mock_call.id = "1"
            mock_bridge.parse_response.return_value = [mock_call]
            mock_bridge.execute = AsyncMock(return_value=MagicMock(output="result", error="", success=True, duration_ms=0))
            mock_bridge.format_result.return_value = {"role": "tool", "content": "result"}
            MockBridge.return_value = mock_bridge
            result = await loop.run("task", "agent", mock_dispatcher, provider="openai")
        assert result.success is False
        assert "Max tool calls" in (result.error or "")


# ── run() — LLM path ────────────────────────────────────────────────


class TestRunLlmPath:
    @pytest.mark.asyncio
    async def test_llm_path_success(self):
        """Cover LLM direct call path (341-361)."""
        config = ReactConfig(max_iterations=1, enable_change_tracking=False, enable_llm=True, llm_model="gpt-4")
        loop = ReactLoop(config=config)

        mock_factory = MagicMock()
        mock_fb_result = MagicMock()
        mock_fb_result.response.content = "LLM final answer"
        mock_fb_result.response.latency_ms = 50
        mock_factory.chat_with_fallback = AsyncMock(return_value=mock_fb_result)
        loop._provider_factory = mock_factory

        result = await loop.run("task", "agent", MagicMock())
        assert result.success is True
        assert result.final_answer == "LLM final answer"

    @pytest.mark.asyncio
    async def test_llm_path_fallback_to_cli(self):
        """Cover LLM failure → CLI fallback (362-381)."""
        config = ReactConfig(max_iterations=1, enable_change_tracking=False, enable_llm=True, llm_model="gpt-4")
        loop = ReactLoop(config=config)

        # LLM factory returns None → _call_llm returns error → falls back to CLI
        loop._provider_factory = None
        with patch.object(type(loop), "provider_factory", None):
            mock_dispatcher = MagicMock()
            mock_dispatcher.dispatch = AsyncMock(
                return_value=_mock_dispatch_result(stdout="CLI answer")
            )
            result = await loop.run("task", "agent", mock_dispatcher)
        assert result.success is True
        assert result.final_answer == "CLI answer"


# ── run() — tool execution ─────────────────────────────────────────


class TestRunToolExecution:
    @pytest.mark.asyncio
    async def test_tool_execution_success(self):
        """Cover tool execution with observation (443-489)."""
        loop = ReactLoop(config=ReactConfig(max_iterations=2, enable_change_tracking=False))
        # First dispatch returns tool calls, second returns final answer
        mock_dispatcher = MagicMock()
        mock_dispatcher.dispatch = AsyncMock(
            side_effect=[
                _mock_dispatch_result(
                    stdout=json.dumps({"choices": [{"message": {"tool_calls": [{"id": "1", "function": {"name": "tool"}}]}}]})
                ),
                _mock_dispatch_result(stdout="final after tool"),
            ]
        )
        with patch("maop.core.agent.llm_chat.function_call.FunctionCallBridge") as MockBridge:
            mock_bridge = MagicMock()
            mock_call = MagicMock()
            mock_call.name = "tool"
            mock_call.arguments = {}
            mock_call.id = "1"
            mock_bridge.parse_response.return_value = [mock_call]
            mock_bridge.execute = AsyncMock(return_value=MagicMock(output="tool result", error="", success=True, duration_ms=10))
            mock_bridge.format_result.return_value = {"role": "tool", "content": "tool result"}
            MockBridge.return_value = mock_bridge
            result = await loop.run("task", "agent", mock_dispatcher, provider="openai")
        assert result.success is True
        assert result.final_answer == "final after tool"

    @pytest.mark.asyncio
    async def test_tool_execution_error(self):
        """Cover tool execution exception (475-482)."""
        loop = ReactLoop(config=ReactConfig(max_iterations=2, enable_change_tracking=False))
        mock_dispatcher = MagicMock()
        mock_dispatcher.dispatch = AsyncMock(
            side_effect=[
                _mock_dispatch_result(
                    stdout=json.dumps({"choices": [{"message": {"tool_calls": [{"id": "1", "function": {"name": "tool"}}]}}]})
                ),
                _mock_dispatch_result(stdout="final"),
            ]
        )
        with patch("maop.core.agent.llm_chat.function_call.FunctionCallBridge") as MockBridge:
            mock_bridge = MagicMock()
            mock_call = MagicMock()
            mock_call.name = "tool"
            mock_call.arguments = {}
            mock_call.id = "1"
            mock_bridge.parse_response.return_value = [mock_call]
            mock_bridge.execute = AsyncMock(side_effect=RuntimeError("tool boom"))
            mock_bridge.format_result.return_value = {"role": "tool", "content": "error"}
            MockBridge.return_value = mock_bridge
            result = await loop.run("task", "agent", mock_dispatcher, provider="openai")
        assert result.success is True
        assert result.final_answer == "final"

    @pytest.mark.asyncio
    async def test_anthropic_provider_tool_calls(self):
        """Cover Anthropic provider conversation append (440-441)."""
        loop = ReactLoop(config=ReactConfig(max_iterations=2, enable_change_tracking=False))
        mock_dispatcher = MagicMock()
        mock_dispatcher.dispatch = AsyncMock(
            side_effect=[
                _mock_dispatch_result(
                    stdout=json.dumps({"content": [{"type": "text", "text": "thinking"}]})
                ),
                _mock_dispatch_result(stdout="final"),
            ]
        )
        with patch("maop.core.agent.llm_chat.function_call.FunctionCallBridge") as MockBridge:
            mock_bridge = MagicMock()
            mock_call = MagicMock()
            mock_call.name = "tool"
            mock_call.arguments = {}
            mock_call.id = "1"
            mock_bridge.parse_response.return_value = [mock_call]
            mock_bridge.execute = AsyncMock(return_value=MagicMock(output="result", error="", success=True, duration_ms=0))
            mock_bridge.format_result.return_value = {"role": "tool", "content": "result"}
            MockBridge.return_value = mock_bridge
            result = await loop.run("task", "agent", mock_dispatcher, provider="anthropic")
        assert result.success is True
