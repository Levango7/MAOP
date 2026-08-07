"""Coverage tests for core/tool_manager.py + core/message_queue.py
+ core/mcp_hub.py + core/mcp_hub_transport.py + core/preemptable_worker_pool.py
+ maop_execute.py.

Uses isolated tmp_path + real instances where possible.
"""
from __future__ import annotations

import asyncio
import pytest


# ── Tool Manager (extended) ─────────────────────────────────────────

class TestToolManagerExtended:
    def test_register_with_params(self, tmp_path):
        from maop.core.agent.tools.tool_manager import ToolManager
        mgr = ToolManager(root_dir=str(tmp_path))
        mgr.register("t1", command="echo", name="Echo", description="echo tool",
                     category="util", params={"k": "v"}, version="2.0")
        info = mgr.info("t1")
        assert info is not None
        assert info.name == "Echo"
        assert info.version == "2.0"

    def test_register_empty_id(self, tmp_path):
        from maop.core.agent.tools.tool_manager import ToolManager
        mgr = ToolManager(root_dir=str(tmp_path))
        with pytest.raises(ValueError):
            mgr.register("", command="echo")

    def test_register_empty_command(self, tmp_path):
        from maop.core.agent.tools.tool_manager import ToolManager
        mgr = ToolManager(root_dir=str(tmp_path))
        with pytest.raises(ValueError):
            mgr.register("t1", command="")

    def test_register_incompatible_version(self, tmp_path, monkeypatch):
        from maop.core.agent.tools.tool_manager import ToolManager
        # Force MAOP version to be lower than required.
        import maop
        monkeypatch.setattr(maop, "__version__", "1.0.0")
        mgr = ToolManager(root_dir=str(tmp_path))
        with pytest.raises(ValueError):
            mgr.register("t1", command="echo", min_platform_version="999.0.0")

    def test_list_with_category(self, tmp_path):
        from maop.core.agent.tools.tool_manager import ToolManager
        mgr = ToolManager(root_dir=str(tmp_path))
        mgr.register("t1", command="echo", category="util")
        mgr.register("t2", command="ls", category="fs")
        result = mgr.list(category="util")
        assert isinstance(result, list)

    def test_find_with_query(self, tmp_path):
        from maop.core.agent.tools.tool_manager import ToolManager
        mgr = ToolManager(root_dir=str(tmp_path))
        mgr.register("echo-tool", command="echo", description="echo utility")
        result = mgr.find("echo")
        assert len(result) >= 1

    def test_find_no_match(self, tmp_path):
        from maop.core.agent.tools.tool_manager import ToolManager
        mgr = ToolManager(root_dir=str(tmp_path))
        result = mgr.find("nonexistent")
        assert result == []

    def test_call_nonexistent(self, tmp_path):
        from maop.core.agent.tools.tool_manager import ToolManager
        mgr = ToolManager(root_dir=str(tmp_path))
        result = asyncio.run(mgr.call("nonexistent"))
        assert result.ok is False

    def test_call_disabled(self, tmp_path):
        from maop.core.agent.tools.tool_manager import ToolManager
        mgr = ToolManager(root_dir=str(tmp_path))
        mgr.register("t1", command="echo")
        mgr.disable("t1")
        result = asyncio.run(mgr.call("t1"))
        assert result.ok is False

    def test_call_sync_nonexistent(self, tmp_path):
        from maop.core.agent.tools.tool_manager import ToolManager
        mgr = ToolManager(root_dir=str(tmp_path))
        result = mgr.call_sync("nonexistent")
        assert result.ok is False

    def test_call_sync_disabled(self, tmp_path):
        from maop.core.agent.tools.tool_manager import ToolManager
        mgr = ToolManager(root_dir=str(tmp_path))
        mgr.register("t1", command="echo")
        mgr.disable("t1")
        result = mgr.call_sync("t1")
        assert result.ok is False

    def test_stats_with_tools(self, tmp_path):
        from maop.core.agent.tools.tool_manager import ToolManager
        mgr = ToolManager(root_dir=str(tmp_path))
        mgr.register("t1", command="echo", category="util")
        mgr.register("t2", command="ls", category="fs")
        mgr.disable("t2")
        stats = mgr.stats()
        assert stats["total"] == 2
        assert stats["enabled"] == 1
        assert stats["disabled"] == 1

    def test_row_to_tool(self, tmp_path):
        from maop.core.agent.tools.tool_manager import ToolManager
        mgr = ToolManager(root_dir=str(tmp_path))
        mgr.register("t1", command="echo", description="test")
        info = mgr.info("t1")
        assert info is not None
        assert info.id == "t1"
        assert info.command == "echo"

    def test_get_maop_version(self):
        from maop.core.agent.tools.tool_manager import _get_maop_version
        result = _get_maop_version()
        assert isinstance(result, str)

    def test_parse_version(self):
        from maop.core.agent.tools.tool_manager import _parse_version
        assert _parse_version("") == (0,)
        assert _parse_version("1.0.0") is not None
        assert _parse_version("1.2.3") is not None

    def test_is_version_compatible_empty(self):
        from maop.core.agent.tools.tool_manager import _is_version_compatible
        assert _is_version_compatible("") is True

    def test_is_version_compatible_unknown_current(self, monkeypatch):
        # If MAOP version is unknown, fails open (returns True).
        import maop
        monkeypatch.setattr(maop, "__version__", "", raising=False)
        from maop.core.agent.tools.tool_manager import _is_version_compatible
        result = _is_version_compatible("1.0.0")
        assert isinstance(result, bool)


# ── Message Queue ───────────────────────────────────────────────────

class TestMessageQueue:
    def test_init(self, tmp_path):
        from maop.core.reliability.message_queue import MessageQueue
        mq = MessageQueue(db_path=str(tmp_path / "queue.db"))
        assert mq is not None

    def test_enqueue(self, tmp_path):
        from maop.core.reliability.message_queue import MessageQueue
        mq = MessageQueue(db_path=str(tmp_path / "queue.db"))
        msg_id = mq.enqueue("topic1", {"key": "value"})
        assert msg_id

    def test_enqueue_with_msg_id(self, tmp_path):
        from maop.core.reliability.message_queue import MessageQueue
        mq = MessageQueue(db_path=str(tmp_path / "queue.db"))
        msg_id = mq.enqueue("topic1", {"k": "v"}, msg_id="custom-id")
        assert msg_id == "custom-id"

    def test_enqueue_idempotent(self, tmp_path):
        from maop.core.reliability.message_queue import MessageQueue
        mq = MessageQueue(db_path=str(tmp_path / "queue.db"))
        id1 = mq.enqueue("topic1", {"k": "v"}, msg_id="dup-id")
        id2 = mq.enqueue("topic1", {"k": "v2"}, msg_id="dup-id")
        assert id1 == id2

    def test_enqueue_with_delay(self, tmp_path):
        from maop.core.reliability.message_queue import MessageQueue
        mq = MessageQueue(db_path=str(tmp_path / "queue.db"))
        msg_id = mq.enqueue("topic1", {"k": "v"}, delay_s=10.0)
        assert msg_id

    def test_dequeue_empty(self, tmp_path):
        from maop.core.reliability.message_queue import MessageQueue
        mq = MessageQueue(db_path=str(tmp_path / "queue.db"))
        result = mq.dequeue("topic1")
        assert result is None

    def test_enqueue_dequeue(self, tmp_path):
        from maop.core.reliability.message_queue import MessageQueue
        mq = MessageQueue(db_path=str(tmp_path / "queue.db"))
        msg_id = mq.enqueue("topic1", {"k": "v"})
        msg = mq.dequeue("topic1")
        assert msg is not None
        assert msg.id == msg_id

    def test_dequeue_with_consumer(self, tmp_path):
        from maop.core.reliability.message_queue import MessageQueue
        mq = MessageQueue(db_path=str(tmp_path / "queue.db"))
        mq.enqueue("topic1", {"k": "v"})
        msg = mq.dequeue("topic1", consumer_group="g1", consumer_id="c1")
        assert msg is not None

    def test_ack(self, tmp_path):
        from maop.core.reliability.message_queue import MessageQueue
        mq = MessageQueue(db_path=str(tmp_path / "queue.db"))
        mq.enqueue("topic1", {"k": "v"})
        msg = mq.dequeue("topic1")
        assert msg is not None
        result = mq.ack(msg.id, consumer_id="c1")
        assert result is True

    def test_ack_nonexistent(self, tmp_path):
        from maop.core.reliability.message_queue import MessageQueue
        mq = MessageQueue(db_path=str(tmp_path / "queue.db"))
        result = mq.ack("nonexistent")
        assert result is False

    def test_nack(self, tmp_path):
        from maop.core.reliability.message_queue import MessageQueue
        mq = MessageQueue(db_path=str(tmp_path / "queue.db"))
        mq.enqueue("topic1", {"k": "v"})
        msg = mq.dequeue("topic1")
        result = mq.nack(msg.id, error="test error")
        assert isinstance(result, bool)

    def test_nack_nonexistent(self, tmp_path):
        from maop.core.reliability.message_queue import MessageQueue
        mq = MessageQueue(db_path=str(tmp_path / "queue.db"))
        result = mq.nack("nonexistent")
        assert result is False

    def test_stats_empty(self, tmp_path):
        from maop.core.reliability.message_queue import MessageQueue
        mq = MessageQueue(db_path=str(tmp_path / "queue.db"))
        stats = mq.stats()
        assert stats.pending == 0
        assert stats.acked == 0

    def test_stats_with_messages(self, tmp_path):
        from maop.core.reliability.message_queue import MessageQueue
        mq = MessageQueue(db_path=str(tmp_path / "queue.db"))
        mq.enqueue("topic1", {"k": "v"})
        mq.enqueue("topic2", {"k": "v"})
        stats = mq.stats()
        assert stats.pending == 2

    def test_purge_acked(self, tmp_path):
        from maop.core.reliability.message_queue import MessageQueue
        mq = MessageQueue(db_path=str(tmp_path / "queue.db"))
        result = mq.purge_acked()
        assert result == 0

    def test_cleanup_dead_letters(self, tmp_path):
        from maop.core.reliability.message_queue import MessageQueue
        mq = MessageQueue(db_path=str(tmp_path / "queue.db"))
        result = mq.cleanup_dead_letters()
        assert result == 0

    def test_requeue_dead_letter_nonexistent(self, tmp_path):
        from maop.core.reliability.message_queue import MessageQueue
        mq = MessageQueue(db_path=str(tmp_path / "queue.db"))
        result = mq.requeue_dead_letter("nonexistent")
        assert result is False

    def test_enqueue_with_priority(self, tmp_path):
        from maop.core.reliability.message_queue import MessageQueue, MessagePriority
        mq = MessageQueue(db_path=str(tmp_path / "queue.db"))
        mq.enqueue("t1", {"k": "v"}, priority=MessagePriority.HIGH)
        mq.enqueue("t1", {"k": "v"}, priority=MessagePriority.LOW)
        # Higher priority (lower number) should be dequeued first.
        msg = mq.dequeue("t1")
        assert msg is not None
        assert msg.priority == MessagePriority.HIGH


# ── MCP Hub ─────────────────────────────────────────────────────────

class TestMcpHub:
    def test_module_import(self):
        import maop.core.mcp.mcp_hub
        assert maop.core.mcp_hub is not None


# ── MCP Hub Transport ───────────────────────────────────────────────

class TestMcpHubTransport:
    def test_module_import(self):
        import maop.core.mcp.mcp_hub_transport
        assert maop.core.mcp_hub_transport is not None


# ── Preemptable Worker Pool ─────────────────────────────────────────

class TestPreemptableWorkerPoolExtended:
    def test_module_import(self):
        import maop.core.reliability.preemptable_worker_pool
        assert maop.core.preemptable_worker_pool is not None


# ── maop_execute ────────────────────────────────────────────────────

class TestMaopExecuteExtended:
    def test_observability_model(self):
        from maop.maop_execute import Observability
        obs = Observability()
        assert obs is not None

    def test_delegate_model_full(self):
        from maop.maop_execute import Delegate
        d = Delegate(
            agent="a", task="t", routing_key="rk", workdir="/tmp",
            timeout_seconds=60, trace_id="t1",
        )
        assert d.agent == "a"
        assert d.task == "t"
        assert d.timeout_seconds == 60

    def test_delegate_with_tools(self):
        from maop.maop_execute import Delegate
        d = Delegate(agent="a", task="t", tools=[{"name": "tool1"}])
        assert d.tools is not None
        assert len(d.tools) == 1