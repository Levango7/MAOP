"""Tests for MAOP.core.subagent — Hierarchical agent delegation."""


import pytest

from maop.core.subagent import SubagentManager


@pytest.fixture
def mgr(tmp_path):
    return SubagentManager(root_dir=str(tmp_path))


class TestSubagentSpawn:
    def test_spawn_basic(self, mgr):
        info = mgr.spawn(parent="orchestrator", agent="coder", task="fix bug")
        assert info.id.startswith("sa-")
        assert info.parent_agent == "orchestrator"
        assert info.child_agent == "coder"
        assert info.status == "spawned"
        assert info.depth == 1

    def test_spawn_nested(self, mgr):
        mgr.spawn(parent="orchestrator", agent="coder")
        info2 = mgr.spawn(parent="coder", agent="reviewer", task="review code")
        assert info2.depth == 2

    def test_spawn_max_depth_exceeded(self, mgr):
        mgr.spawn(parent="a", agent="b")
        mgr.spawn(parent="b", agent="c")
        mgr.spawn(parent="c", agent="d")
        mgr.spawn(parent="d", agent="e")
        with pytest.raises(ValueError, match="Max subagent depth"):
            mgr.spawn(parent="e", agent="f", max_depth=4)


class TestSubagentTerminate:
    def test_terminate_success(self, mgr):
        info = mgr.spawn(parent="orchestrator", agent="coder")
        result = mgr.terminate(info.id, exit_code=0)
        assert result.status == "completed"
        assert result.exit_code == 0

    def test_terminate_failure(self, mgr):
        info = mgr.spawn(parent="orchestrator", agent="coder")
        result = mgr.terminate(info.id, exit_code=1)
        assert result.status == "failed"
        assert result.exit_code == 1

    def test_terminate_not_found(self, mgr):
        result = mgr.terminate("sa-nonexistent")
        assert result is None


class TestSubagentGet:
    def test_get_existing(self, mgr):
        info = mgr.spawn(parent="orchestrator", agent="coder")
        fetched = mgr.get(info.id)
        assert fetched is not None
        assert fetched.id == info.id

    def test_get_not_found(self, mgr):
        assert mgr.get("sa-nonexistent") is None


class TestSubagentListChildren:
    def test_list_children(self, mgr):
        mgr.spawn(parent="orchestrator", agent="coder")
        mgr.spawn(parent="orchestrator", agent="reviewer")
        children = mgr.list_children("orchestrator")
        assert len(children) == 2

    def test_list_children_by_status(self, mgr):
        info = mgr.spawn(parent="orchestrator", agent="coder")
        mgr.terminate(info.id, exit_code=0)
        active = mgr.list_children("orchestrator", status="spawned")
        completed = mgr.list_children("orchestrator", status="completed")
        assert len(active) == 0
        assert len(completed) == 1


class TestSubagentTree:
    def test_get_tree(self, mgr):
        mgr.spawn(parent="orchestrator", agent="coder")
        tree = mgr.get_tree("orchestrator")
        assert tree.agent_name == "orchestrator"
        assert "coder" in tree.children


class TestSubagentMessaging:
    def test_send_receive(self, mgr):
        mgr.send(sender="orchestrator", recipient="coder", msg_type="task", payload={"action": "fix"})
        messages = mgr.receive(recipient="coder")
        assert len(messages) == 1
        assert messages[0].payload["action"] == "fix"

    def test_purge_messages(self, mgr):
        mgr.send(sender="a", recipient="b")
        mgr.send(sender="a", recipient="b")
        count = mgr.purge_messages(recipient="b")
        assert count == 2
        assert len(mgr.receive(recipient="b")) == 0

    def test_multiple_recipients(self, mgr):
        mgr.send(sender="a", recipient="b")
        mgr.send(sender="a", recipient="c")
        assert len(mgr.receive(recipient="b")) == 1
        assert len(mgr.receive(recipient="c")) == 1