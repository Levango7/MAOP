"""Tests for MAOP.core.session, MAOP.core.conversation, and MAOP.core.project_context."""

from __future__ import annotations


from maop.core.session import SessionManager, SessionStatus
from maop.core.conversation import ConversationManager
from maop.core.project_context import ProjectContext


# ── SessionManager ──────────────────────────────────────────────


class TestSessionCreate:
    def test_create_session(self, tmp_path):
        mgr = SessionManager(root_dir=str(tmp_path))
        sid = mgr.create(agent="mavis", workdir="/project")
        assert sid.startswith("sess-")
        session = mgr.get(sid)
        assert session is not None
        assert session.agent == "mavis"
        assert session.workdir == "/project"
        assert session.status == SessionStatus.ACTIVE

    def test_create_with_tags_and_metadata(self, tmp_path):
        mgr = SessionManager(root_dir=str(tmp_path))
        sid = mgr.create(
            agent="claude",
            tags=["code-review", "v3"],
            metadata={"priority": "high"},
            token_budget=8000,
        )
        session = mgr.get(sid)
        assert session is not None
        assert session.tags == ["code-review", "v3"]
        assert session.metadata == {"priority": "high"}
        assert session.token_budget == 8000


class TestSessionCRUD:
    def test_get_nonexistent(self, tmp_path):
        mgr = SessionManager(root_dir=str(tmp_path))
        assert mgr.get("nonexistent") is None

    def test_list_sessions(self, tmp_path):
        mgr = SessionManager(root_dir=str(tmp_path))
        mgr.create(agent="a")
        mgr.create(agent="b")
        sessions = mgr.list()
        assert len(sessions) == 2

    def test_list_filter_by_status(self, tmp_path):
        mgr = SessionManager(root_dir=str(tmp_path))
        sid = mgr.create(agent="a")
        mgr.update(sid, status="completed")
        mgr.create(agent="b")
        active = mgr.list(status="active")
        assert len(active) == 1
        assert active[0].agent == "b"

    def test_list_filter_by_agent(self, tmp_path):
        mgr = SessionManager(root_dir=str(tmp_path))
        mgr.create(agent="mavis")
        mgr.create(agent="claude")
        sessions = mgr.list(agent="mavis")
        assert len(sessions) == 1

    def test_update_session(self, tmp_path):
        mgr = SessionManager(root_dir=str(tmp_path))
        sid = mgr.create(agent="a")
        ok = mgr.update(sid, status="paused", agent="b", token_count=500)
        assert ok is True
        session = mgr.get(sid)
        assert session.status == "paused"
        assert session.agent == "b"
        assert session.token_count == 500

    def test_update_nonexistent(self, tmp_path):
        mgr = SessionManager(root_dir=str(tmp_path))
        assert mgr.update("nonexistent", status="x") is False

    def test_delete_session(self, tmp_path):
        mgr = SessionManager(root_dir=str(tmp_path))
        sid = mgr.create(agent="a")
        assert mgr.delete(sid) is True
        assert mgr.get(sid) is None
        assert mgr.delete(sid) is False


class TestSessionTokens:
    def test_add_tokens(self, tmp_path):
        mgr = SessionManager(root_dir=str(tmp_path))
        sid = mgr.create(agent="a", token_budget=1000)
        mgr.add_tokens(sid, 300)
        mgr.add_tokens(sid, 200)
        session = mgr.get(sid)
        assert session.token_count == 500

    def test_is_over_budget(self, tmp_path):
        mgr = SessionManager(root_dir=str(tmp_path))
        sid = mgr.create(agent="a", token_budget=100)
        assert mgr.is_over_budget(sid) is False
        mgr.add_tokens(sid, 150)
        assert mgr.is_over_budget(sid) is True

    def test_is_over_budget_no_budget(self, tmp_path):
        mgr = SessionManager(root_dir=str(tmp_path))
        sid = mgr.create(agent="a")
        mgr.add_tokens(sid, 999999)
        assert mgr.is_over_budget(sid) is False


class TestSessionStats:
    def test_stats(self, tmp_path):
        mgr = SessionManager(root_dir=str(tmp_path))
        mgr.create(agent="a")
        sid = mgr.create(agent="b")
        mgr.update(sid, status="completed")
        stats = mgr.stats()
        assert stats["total"] == 2
        assert stats["active"] == 1


class TestSessionTouch:
    def test_touch(self, tmp_path):
        mgr = SessionManager(root_dir=str(tmp_path))
        sid = mgr.create(agent="a")
        session = mgr.get(sid)
        original = session.last_active_at
        import time
        time.sleep(0.01)
        mgr.touch(sid)
        updated = mgr.get(sid).last_active_at
        assert updated >= original


# ── ConversationManager ─────────────────────────────────────────


class TestConversationAddMessage:
    def test_add_message(self, tmp_path):
        mgr = ConversationManager(root_dir=str(tmp_path))
        msg_id = mgr.add_message("sess-1", role="user", content="Hello!")
        assert msg_id.startswith("msg-")

    def test_add_multiple_roles(self, tmp_path):
        mgr = ConversationManager(root_dir=str(tmp_path))
        mgr.add_message("sess-1", role="system", content="You are helpful")
        mgr.add_message("sess-1", role="user", content="Fix the bug")
        mgr.add_message("sess-1", role="assistant", content="Bug fixed")
        mgr.add_message("sess-1", role="tool", content='{"result": "ok"}', metadata={"tool_call_id": "tc1"})
        history = mgr.get_history("sess-1")
        assert len(history) == 4
        assert history[0].role == "system"
        assert history[1].role == "user"
        assert history[2].role == "assistant"
        assert history[3].role == "tool"


class TestConversationHistory:
    def test_get_history(self, tmp_path):
        mgr = ConversationManager(root_dir=str(tmp_path))
        mgr.add_message("sess-1", role="user", content="A")
        mgr.add_message("sess-1", role="user", content="B")
        mgr.add_message("sess-1", role="user", content="C")
        history = mgr.get_history("sess-1")
        assert len(history) == 3
        assert history[0].content == "A"
        assert history[2].content == "C"

    def test_get_recent(self, tmp_path):
        mgr = ConversationManager(root_dir=str(tmp_path))
        for i in range(10):
            mgr.add_message("sess-1", role="user", content=f"msg-{i}")
        recent = mgr.get_recent("sess-1", count=3)
        assert len(recent) == 3
        assert recent[-1].content == "msg-9"

    def test_get_history_with_offset(self, tmp_path):
        mgr = ConversationManager(root_dir=str(tmp_path))
        for i in range(5):
            mgr.add_message("sess-1", role="user", content=f"msg-{i}")
        history = mgr.get_history("sess-1", limit=2, offset=2)
        assert len(history) == 2
        assert history[0].content == "msg-2"


class TestContextWindow:
    def test_fits_in_budget(self, tmp_path):
        mgr = ConversationManager(root_dir=str(tmp_path), max_context_tokens=10000)
        mgr.add_message("sess-1", role="user", content="Short message")
        window = mgr.get_context_window("sess-1")
        assert window.compressed is False
        assert len(window.messages) == 1

    def test_over_budget_sliding_window(self, tmp_path):
        mgr = ConversationManager(root_dir=str(tmp_path), max_context_tokens=50)
        for i in range(20):
            mgr.add_message("sess-1", role="user", content=f"Message number {i} with some padding text")
        window = mgr.get_context_window("sess-1", max_tokens=50)
        assert window.compressed is True
        assert window.total_tokens <= 60  # Allow small overshoot from last message

    def test_empty_session(self, tmp_path):
        mgr = ConversationManager(root_dir=str(tmp_path))
        window = mgr.get_context_window("nonexistent")
        assert len(window.messages) == 0


class TestConversationSearch:
    def test_search(self, tmp_path):
        mgr = ConversationManager(root_dir=str(tmp_path))
        mgr.add_message("sess-1", role="user", content="Fix the authentication bug")
        mgr.add_message("sess-1", role="assistant", content="The bug is in auth.py")
        results = mgr.search("sess-1", "auth")
        assert len(results) == 2

    def test_search_no_results(self, tmp_path):
        mgr = ConversationManager(root_dir=str(tmp_path))
        mgr.add_message("sess-1", role="user", content="Hello")
        results = mgr.search("sess-1", "nonexistent")
        assert len(results) == 0


class TestConversationDelete:
    def test_delete_message(self, tmp_path):
        mgr = ConversationManager(root_dir=str(tmp_path))
        msg_id = mgr.add_message("sess-1", role="user", content="Delete me")
        assert mgr.delete_message(msg_id) is True
        assert mgr.delete_message(msg_id) is False

    def test_clear_session(self, tmp_path):
        mgr = ConversationManager(root_dir=str(tmp_path))
        mgr.add_message("sess-1", role="user", content="A")
        mgr.add_message("sess-1", role="user", content="B")
        count = mgr.clear_session("sess-1")
        assert count == 2
        assert mgr.message_count("sess-1") == 0


class TestConversationCounts:
    def test_message_count(self, tmp_path):
        mgr = ConversationManager(root_dir=str(tmp_path))
        mgr.add_message("sess-1", role="user", content="A")
        mgr.add_message("sess-1", role="user", content="B")
        assert mgr.message_count("sess-1") == 2

    def test_token_total(self, tmp_path):
        mgr = ConversationManager(root_dir=str(tmp_path))
        mgr.add_message("sess-1", role="user", content="Hello world", token_count=10)
        mgr.add_message("sess-1", role="assistant", content="Hi!", token_count=5)
        assert mgr.token_total("sess-1") == 15


class TestToMessagesList:
    def test_to_messages_list(self, tmp_path):
        mgr = ConversationManager(root_dir=str(tmp_path))
        mgr.add_message("sess-1", role="system", content="Be helpful")
        mgr.add_message("sess-1", role="user", content="Fix bug")
        mgr.add_message("sess-1", role="tool", content="ok", metadata={"tool_call_id": "tc1"})
        msgs = mgr.to_messages_list("sess-1")
        assert len(msgs) == 3
        assert msgs[0]["role"] == "system"
        assert msgs[2]["tool_call_id"] == "tc1"


# ── ProjectContext ──────────────────────────────────────────────


class TestProjectContext:
    def test_build(self, tmp_path):
        (tmp_path / "pyproject.toml").write_text("[project]\nname = 'test'\n", encoding="utf-8")
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "main.py").write_text("print('hi')", encoding="utf-8")
        ctx = ProjectContext(workdir=str(tmp_path))
        info = ctx.build()
        assert info.name == tmp_path.name
        assert "python" in info.tech_stack
        assert info.structure != ""
        assert "pyproject.toml" in info.config_files

    def test_build_summary(self, tmp_path):
        (tmp_path / "pyproject.toml").write_text("[project]\nname = 'test'\n", encoding="utf-8")
        ctx = ProjectContext(workdir=str(tmp_path))
        summary = ctx.build_summary()
        assert "Project:" in summary
        assert "python" in summary

    def test_tech_stack_detection(self, tmp_path):
        (tmp_path / "package.json").write_text('{"name": "test"}', encoding="utf-8")
        ctx = ProjectContext(workdir=str(tmp_path))
        info = ctx.build()
        assert "javascript" in info.tech_stack

    def test_instructions(self, tmp_path):
        (tmp_path / "CLAUDE.md").write_text("Always use type hints", encoding="utf-8")
        ctx = ProjectContext(workdir=str(tmp_path))
        info = ctx.build()
        assert "type hints" in info.instructions

    def test_cache(self, tmp_path):
        ctx = ProjectContext(workdir=str(tmp_path))
        info1 = ctx.build()
        info2 = ctx.build()
        assert info1 is info2  # Same object from cache

    def test_force_refresh(self, tmp_path):
        ctx = ProjectContext(workdir=str(tmp_path))
        info1 = ctx.build()
        info2 = ctx.build(force_refresh=True)
        assert info1 is not info2

    def test_empty_project(self, tmp_path):
        ctx = ProjectContext(workdir=str(tmp_path))
        info = ctx.build()
        assert info.name == tmp_path.name
        assert info.tech_stack == []