"""Tests for maop.core.subagent_delegation.SubagentManager.

E2 (2026-07-22, Phase E): verifies hierarchical agent delegation:
spawn/terminate/get/list_children/get_tree/send/receive/purge_messages.

Uses tmp_path so each test gets an isolated SQLite DB.
"""

from __future__ import annotations

import pytest

from maop.core.subagent_delegation import (
    AgentMessage,
    AgentTreeNode,
    SubagentInfo,
    SubagentManager,
)


@pytest.fixture
def mgr(tmp_path):
    """Fresh SubagentManager with isolated DB."""
    return SubagentManager(root_dir=tmp_path)


# ── Models ────────────────────────────────────────────────────


def test_subagent_info_defaults():
    info = SubagentInfo(id="sa-1", parent_agent="orchestrator", child_agent="coder")
    assert info.task == ""
    assert info.status == "spawned"
    assert info.depth == 0
    assert info.exit_code is None


def test_agent_message_defaults():
    msg = AgentMessage(sender="a", recipient="b")
    assert msg.msg_type == "info"
    assert msg.payload == {}
    assert msg.id == ""  # auto-generated on send()


def test_agent_tree_node_defaults():
    node = AgentTreeNode(agent_name="root")
    assert node.depth == 0
    assert node.parent is None
    assert node.children == []


# ── spawn ─────────────────────────────────────────────────────


def test_spawn_returns_subagent_info_with_id(mgr):
    info = mgr.spawn(parent="orchestrator", agent="coder", task="fix bug")
    assert info.id.startswith("sa-")
    assert info.parent_agent == "orchestrator"
    assert info.child_agent == "coder"
    assert info.task == "fix bug"
    assert info.status == "spawned"
    assert info.depth == 1  # first child of depth-0 parent


def test_spawn_increments_depth_via_db(mgr):
    """Without call_chain, depth is tracked via DB lookup of parent depth."""
    a = mgr.spawn(parent="root", agent="A")
    assert a.depth == 1
    b = mgr.spawn(parent="A", agent="B")
    # _get_depth("A") returns max depth where A is a child = 1, so B is depth 2
    assert b.depth == 2


def test_spawn_with_call_chain_enforces_max_depth(mgr):
    """call_chain-based depth tracking enforces max_depth."""
    chain = ["L0", "L1", "L2", "L3", "L4"]  # depth 5 would exceed max_depth=5
    with pytest.raises(ValueError, match="Max subagent depth"):
        mgr.spawn(parent="L4", agent="L5", max_depth=5, call_chain=chain)


def test_spawn_with_call_chain_enforces_self_ref_limit(mgr):
    """call_chain detects self-referential loops."""
    # agent "MAOP" appears 3 times in chain, max_self_ref_depth=3 → spawn 4th fails
    chain = ["MAOP", "mavis", "MAOP", "mavis", "MAOP"]
    with pytest.raises(ValueError, match="Self-reference limit"):
        mgr.spawn(parent="mavis", agent="MAOP", call_chain=chain, max_self_ref_depth=3)


def test_spawn_with_call_chain_allows_within_limits(mgr):
    """Within limits, spawn succeeds and depth = len(call_chain)."""
    chain = ["A", "B"]
    info = mgr.spawn(parent="B", agent="C", call_chain=chain)
    assert info.depth == 2  # len(chain) = 2


# ── get / terminate ───────────────────────────────────────────


def test_get_returns_none_for_unknown_id(mgr):
    assert mgr.get("nonexistent") is None


def test_get_returns_stored_info(mgr):
    spawned = mgr.spawn(parent="root", agent="child")
    fetched = mgr.get(spawned.id)
    assert fetched is not None
    assert fetched.id == spawned.id
    assert fetched.child_agent == "child"


def test_terminate_returns_none_for_unknown(mgr):
    assert mgr.terminate("nope") is None


def test_terminate_success_sets_status_completed(mgr):
    spawned = mgr.spawn(parent="root", agent="child")
    result = mgr.terminate(spawned.id, exit_code=0)
    assert result is not None
    assert result.status == "completed"
    assert result.exit_code == 0
    assert result.finished_at != ""


def test_terminate_failure_sets_status_failed(mgr):
    spawned = mgr.spawn(parent="root", agent="child")
    result = mgr.terminate(spawned.id, exit_code=1)
    assert result.status == "failed"
    assert result.exit_code == 1


# ── list_children ─────────────────────────────────────────────


def test_list_children_empty_for_unknown_parent(mgr):
    assert mgr.list_children("ghost") == []


def test_list_children_returns_all_children(mgr):
    mgr.spawn(parent="root", agent="A")
    mgr.spawn(parent="root", agent="B")
    mgr.spawn(parent="root", agent="C")
    children = mgr.list_children("root")
    assert len(children) == 3
    names = {c.child_agent for c in children}
    assert names == {"A", "B", "C"}


def test_list_children_filters_by_status(mgr):
    a = mgr.spawn(parent="root", agent="A")
    mgr.spawn(parent="root", agent="B")
    mgr.terminate(a.id, exit_code=0)
    active = mgr.list_children("root", status="spawned")
    completed = mgr.list_children("root", status="completed")
    assert len(active) == 1
    assert active[0].child_agent == "B"
    assert len(completed) == 1
    assert completed[0].child_agent == "A"


# ── get_tree ──────────────────────────────────────────────────


def test_get_tree_returns_node_with_children(mgr):
    mgr.spawn(parent="root", agent="A")
    mgr.spawn(parent="root", agent="B")
    tree = mgr.get_tree("root")
    assert isinstance(tree, AgentTreeNode)
    assert tree.agent_name == "root"
    assert "A" in tree.children
    assert "B" in tree.children


def test_get_tree_for_leaf_has_no_children(mgr):
    mgr.spawn(parent="root", agent="A")
    tree = mgr.get_tree("A")
    assert tree.children == []
    assert tree.parent == "root"


# ── send / receive / purge ────────────────────────────────────


def test_send_returns_message_with_id(mgr):
    msg = mgr.send(sender="A", recipient="B", msg_type="info", payload={"k": "v"})
    assert msg.id.startswith("msg-")
    assert msg.sender == "A"
    assert msg.recipient == "B"
    assert msg.msg_type == "info"
    assert msg.payload == {"k": "v"}


def test_receive_returns_pending_messages(mgr):
    mgr.send(sender="A", recipient="B", payload={"n": 1})
    mgr.send(sender="C", recipient="B", payload={"n": 2})
    msgs = mgr.receive("B")
    assert len(msgs) == 2
    # Ordered by created_at
    assert msgs[0].sender == "A"
    assert msgs[1].sender == "C"


def test_receive_empty_for_no_messages(mgr):
    assert mgr.receive("nobody") == []


def test_receive_respects_limit(mgr):
    for i in range(5):
        mgr.send(sender="A", recipient="B", payload={"i": i})
    msgs = mgr.receive("B", limit=2)
    assert len(msgs) == 2


def test_receive_deserializes_payload(mgr):
    mgr.send(sender="A", recipient="B", payload={"nested": {"x": [1, 2]}})
    msgs = mgr.receive("B")
    assert msgs[0].payload == {"nested": {"x": [1, 2]}}


def test_purge_messages_deletes_all_for_recipient(mgr):
    mgr.send(sender="A", recipient="B")
    mgr.send(sender="C", recipient="B")
    mgr.send(sender="A", recipient="D")  # different recipient
    deleted = mgr.purge_messages("B")
    assert deleted == 2
    assert mgr.receive("B") == []
    # D's messages untouched
    assert len(mgr.receive("D")) == 1


def test_purge_messages_returns_zero_for_unknown(mgr):
    assert mgr.purge_messages("nobody") == 0


# ── _get_depth / _get_parent ──────────────────────────────────


def test_get_depth_zero_for_agent_without_parent(mgr):
    assert mgr._get_depth("orphan") == 0


def test_get_depth_returns_max_depth_as_child(mgr):
    mgr.spawn(parent="root", agent="A")  # depth 1
    mgr.spawn(parent="A", agent="A")     # depth 2 (same name, different role)
    # _get_depth("A") returns max depth where A is a child = 2
    assert mgr._get_depth("A") == 2


def test_get_parent_none_for_agent_without_parent(mgr):
    assert mgr._get_parent("orphan") is None


def test_get_parent_returns_most_recent_parent(mgr):
    mgr.spawn(parent="first", agent="shared")
    mgr.spawn(parent="second", agent="shared")
    parent = mgr._get_parent("shared")
    assert parent == "second"  # most recent
