"""Stage 3/P2：SubAgentManager 拆分验证（unittest + fakeredis-free，无 pytest）。

覆盖：
  - async 生命周期：spawn / wait / cancel / spawn_and_wait_all / list_agents / get_live_transcript
  - 继承的 sync 委派（SubAgentDelegate）：spawn_child / terminate / get / list_children /
    get_tree / send / receive / purge_messages
  - 别名兼容：SubagentManager is SubAgentManager
  - 继承关系：issubclass(SubAgentManager, SubAgentDelegate)
"""

from __future__ import annotations

import asyncio
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from maop.core.agent.delegation.subagent_lifecycle import (  # noqa: E402
    AgentConfig,
    AgentResult,
    AgentRole,
    AgentStatus,
    SubAgentDelegate,
    SubAgentManager,
    SubagentManager,
    TranscriptEntry,
)


def _config(name="test-agent", model="test-model") -> AgentConfig:
    return AgentConfig(name=name, model=model)


class TestSubAgentSplit(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.mgr = SubAgentManager(root_dir=self.tmpdir)
        # 全局 DB 路径（不随 root_dir 变），每个 test 清空表以保证隔离。
        from maop.core.backends.db_utils import sqlite_connect
        with sqlite_connect(self.mgr._db_path) as conn:
            conn.execute("DELETE FROM subagents")
            conn.execute("DELETE FROM agent_messages")
            conn.execute("DELETE FROM subagent_transcripts")

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    # ── Inheritance & alias ──────────────────────────────────────

    def test_subclass_and_alias(self):
        self.assertTrue(issubclass(SubAgentManager, SubAgentDelegate))
        self.assertIs(SubagentManager, SubAgentManager)

    # ── Async lifecycle ──────────────────────────────────────────

    def test_spawn_returns_agent_id(self):
        async def _run():
            with patch.object(self.mgr, "_invoke_llm", new=AsyncMock(return_value="ok")):
                agent_id = await self.mgr.spawn(_config(), "do something")
            return agent_id

        agent_id = asyncio.run(_run())
        self.assertTrue(agent_id)
        self.assertEqual(len(agent_id), 16)

    def test_spawn_records_running_status(self):
        async def _run():
            with patch.object(self.mgr, "_invoke_llm", new=AsyncMock(return_value="ok")):
                await self.mgr.spawn(_config(), "task")
            return self.mgr.list_agents()

        agents = asyncio.run(_run())
        self.assertEqual(len(agents), 1)
        self.assertEqual(agents[0]["name"], "test-agent")

    def test_wait_returns_completed_result(self):
        async def _run():
            with patch.object(self.mgr, "_invoke_llm", new=AsyncMock(return_value="hello world")):
                agent_id = await self.mgr.spawn(_config(), "say hello")
                return await self.mgr.wait(agent_id, timeout=5)

        result = asyncio.run(_run())
        self.assertIsInstance(result, AgentResult)
        self.assertEqual(result.status, AgentStatus.COMPLETED)
        self.assertEqual(result.output, "hello world")

    def test_wait_failed_for_unknown(self):
        async def _run():
            return await self.mgr.wait("nonexistent", timeout=1)

        result = asyncio.run(_run())
        self.assertEqual(result.status, AgentStatus.FAILED)
        self.assertIn("Not found", result.error)

    def test_cancel_returns_false_for_unknown(self):
        self.assertFalse(self.mgr.cancel("nonexistent"))

    def test_spawn_and_wait_all(self):
        async def _run():
            with patch.object(self.mgr, "_invoke_llm", new=AsyncMock(return_value="ok")):
                tasks = [
                    (_config(name="a"), "task A", {}),
                    (_config(name="b"), "task B", {}),
                ]
                return await self.mgr.spawn_and_wait_all(tasks, timeout=5)

        results = asyncio.run(_run())
        self.assertEqual(len(results), 2)
        self.assertTrue(all(r.status == AgentStatus.COMPLETED for r in results))
        self.assertTrue(all(r.output == "ok" for r in results))

    def test_list_agents_filters_by_status(self):
        async def _run():
            async def slow(*args, **kwargs):
                await asyncio.sleep(10)
                return "late"
            with patch.object(self.mgr, "_invoke_llm", new=slow):
                running_id = await self.mgr.spawn(_config(name="slow"), "t")
                await asyncio.sleep(0.05)
                running_agents = self.mgr.list_agents(status=AgentStatus.RUNNING)
                self.mgr.cancel(running_id)
                return running_agents

        running_agents = asyncio.run(_run())
        self.assertEqual(len(running_agents), 1)
        self.assertEqual(running_agents[0]["name"], "slow")

    def test_get_live_transcript(self):
        async def _run():
            with patch.object(self.mgr, "_invoke_llm", new=AsyncMock(return_value="done")):
                agent_id = await self.mgr.spawn(_config(), "task")
                await self.mgr.wait(agent_id, timeout=5)
                return self.mgr.get_live_transcript(agent_id)

        transcript = asyncio.run(_run())
        events = [t.event for t in transcript]
        self.assertIn("spawned", events)
        self.assertIn("started", events)
        self.assertIn("completed", events)

    def test_get_live_transcript_empty_for_unknown(self):
        self.assertEqual(self.mgr.get_live_transcript("nonexistent"), [])

    # ── Inherited sync delegation (SubAgentDelegate) ─────────────

    def test_spawn_child_returns_info(self):
        info = self.mgr.spawn_child(parent="orchestrator", agent="coder", task="fix bug")
        self.assertTrue(info.id.startswith("sa-"))
        self.assertEqual(info.parent_agent, "orchestrator")
        self.assertEqual(info.child_agent, "coder")
        self.assertEqual(info.task, "fix bug")
        self.assertEqual(info.status, "spawned")
        self.assertEqual(info.depth, 1)

    def test_get_and_terminate(self):
        spawned = self.mgr.spawn_child(parent="root", agent="child")
        fetched = self.mgr.get(spawned.id)
        self.assertIsNotNone(fetched)
        self.assertEqual(fetched.id, spawned.id)
        self.assertEqual(fetched.child_agent, "child")

        result = self.mgr.terminate(spawned.id, exit_code=0)
        self.assertEqual(result.status, "completed")
        self.assertEqual(result.exit_code, 0)

    def test_list_children(self):
        self.mgr.spawn_child(parent="root", agent="A")
        self.mgr.spawn_child(parent="root", agent="B")
        children = self.mgr.list_children("root")
        self.assertEqual(len(children), 2)
        names = {c.child_agent for c in children}
        self.assertEqual(names, {"A", "B"})

    def test_get_tree(self):
        self.mgr.spawn_child(parent="root", agent="A")
        tree = self.mgr.get_tree("root")
        self.assertEqual(tree.agent_name, "root")
        self.assertIn("A", tree.children)

    def test_send_receive_purge(self):
        msg = self.mgr.send(sender="A", recipient="B", msg_type="info", payload={"k": "v"})
        self.assertTrue(msg.id.startswith("msg-"))
        self.assertEqual(msg.sender, "A")
        self.assertEqual(msg.recipient, "B")
        self.assertEqual(msg.payload, {"k": "v"})

        self.mgr.send(sender="C", recipient="B", payload={"n": 2})
        msgs = self.mgr.receive("B")
        self.assertEqual(len(msgs), 2)

        deleted = self.mgr.purge_messages("B")
        self.assertEqual(deleted, 2)
        self.assertEqual(self.mgr.receive("B"), [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
