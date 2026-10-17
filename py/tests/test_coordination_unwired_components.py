"""M0–M4 中"尚未接线"的可靠性组件：单元测试。

这些模块没有生产调用方（登记在 ``test_dead_wiring_inventory.KNOWN_ORPHANS``），
但它们是分布式能力的基础件。**接口契约必须先钉住** —— 否则接上线那天才发现
"恢复 / 互斥 / 租约"其实不工作，而那时的现场是生产。

覆盖：

- ``DistributedLease``：acquire / release / renew / current_holder / 自动续租 /
  Redis 故障 fail-open；
- ``LeaseBasedLeaderElection``：start / stop / heartbeat / contested_once；
- ``DAGCheckpoint`` + ``DAGCheckpointManager``：层级进度、持久化、崩溃恢复、
  中断清单、异常路径；
- ``BlackboardTaskContext``：attach / detach / 快照 / 贡献。

每个断言都对着实现里的具体分支写，不用"跑通就行"的宽松断言。
"""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import MagicMock

import fakeredis
import pytest

from maop.core.coordination.bb_task_context import BlackboardTaskContext
from maop.core.reliability.distributed_checkpoint import (
    DAGCheckpoint,
    DAGCheckpointManager,
)
from maop.core.reliability.distributed_lease import (
    DistributedLease,
    LeaseBasedLeaderElection,
    LeaseState,
)

pytestmark = pytest.mark.asyncio


@pytest.fixture
def redis() -> Any:
    return fakeredis.FakeRedis()


# ── DistributedLease ─────────────────────────────────────────────


class TestDistributedLease:
    async def test_ttl_must_be_positive(self, redis: Any) -> None:
        with pytest.raises(ValueError, match="ttl must be >= 1"):
            DistributedLease(redis, "bad", ttl=0)

    async def test_acquire_then_second_holder_fails(self, redis: Any) -> None:
        first = DistributedLease(redis, "job", ttl=30, auto_renew=False)
        second = DistributedLease(redis, "job", ttl=30, auto_renew=False)

        state = await first.acquire()
        assert isinstance(state, LeaseState)
        assert bool(state) is True
        assert state.token > 0
        assert first.is_held is True

        denied = await second.acquire()
        assert bool(denied) is False
        assert second.is_held is False
        assert second.token == 0

    async def test_acquire_fails_open_when_redis_errors(self) -> None:
        """Redis 故障时按"假设自己是 leader"放行 —— 可用性优先，代价是可能双主。"""
        broken = MagicMock()
        broken.set.side_effect = ConnectionError("redis down")
        lease = DistributedLease(broken, "job", ttl=5, auto_renew=False)

        state = await lease.acquire()
        assert bool(state) is True
        assert lease.is_held is True
        assert lease.token > 0

    async def test_release_without_token_returns_false(self, redis: Any) -> None:
        lease = DistributedLease(redis, "job", auto_renew=False)
        assert await lease.release() is False

    async def test_release_removes_key_and_clears_token(self, redis: Any) -> None:
        lease = DistributedLease(redis, "job", ttl=30, auto_renew=False)
        await lease.acquire()
        token = lease.token

        assert await lease.release() is True
        assert lease.is_held is False
        assert lease.token == 0
        assert redis.get("maop:lease:job") is None
        assert token > 0

    async def test_release_after_someone_else_took_over(self, redis: Any) -> None:
        """fencing：别人重新拿到 key 后，我方 release 不许删掉他的锁。"""
        lease = DistributedLease(redis, "job", ttl=30, auto_renew=False)
        await lease.acquire()
        # 模拟租约过期后被另一个 holder 抢到
        redis.set("maop:lease:job", "other-holder:999")

        assert await lease.release() is False
        assert redis.get("maop:lease:job") == b"other-holder:999"

    async def test_release_returns_false_on_redis_error(self) -> None:
        broken = MagicMock()
        broken.set.return_value = True
        broken.eval.side_effect = ConnectionError("down")
        lease = DistributedLease(broken, "job", ttl=5, auto_renew=False)
        await lease.acquire()

        assert await lease.release() is False
        assert lease.token == 0

    async def test_release_uses_evalsha_when_script_cached(self, redis: Any) -> None:
        """``_register_scripts`` 成功缓存后走 evalsha 快路径。"""
        lease = DistributedLease(redis, "job", ttl=30, auto_renew=False)
        await lease._register_scripts()
        assert lease._release_sha != ""
        assert lease._extend_sha != ""

        await lease.acquire()
        assert await lease.release() is True

    async def test_register_scripts_is_idempotent(self, redis: Any) -> None:
        lease = DistributedLease(redis, "job", auto_renew=False)
        await lease._register_scripts()
        first = lease._release_sha
        await lease._register_scripts()
        assert lease._release_sha == first

    async def test_register_scripts_swallows_unsupported_backend(self) -> None:
        """不支持 script_load 的后端不该把 acquire 拖垮。"""
        partial = MagicMock()
        partial.script_load.side_effect = RuntimeError("no scripts")
        lease = DistributedLease(partial, "job", auto_renew=False)

        await lease._register_scripts()
        assert lease._release_sha == ""

    async def test_renew_requires_held_lease(self, redis: Any) -> None:
        lease = DistributedLease(redis, "job", auto_renew=False)
        assert await lease.renew() is False

    async def test_renew_extends_ttl(self, redis: Any) -> None:
        lease = DistributedLease(redis, "job", ttl=30, auto_renew=False)
        await lease.acquire()
        assert await lease.renew() is True
        assert redis.ttl("maop:lease:job") > 0

    async def test_renew_returns_false_when_key_changed(self, redis: Any) -> None:
        lease = DistributedLease(redis, "job", ttl=30, auto_renew=False)
        await lease.acquire()
        redis.set("maop:lease:job", "other:1")
        assert await lease.renew() is False

    async def test_renew_returns_false_on_redis_error(self) -> None:
        broken = MagicMock()
        broken.set.return_value = True
        broken.eval.side_effect = ConnectionError("down")
        lease = DistributedLease(broken, "job", ttl=5, auto_renew=False)
        await lease.acquire()
        assert await lease.renew() is False

    async def test_current_holder(self, redis: Any) -> None:
        lease = DistributedLease(redis, "job", ttl=30, auto_renew=False)
        assert await lease.current_holder() is None

        await lease.acquire()
        holder = await lease.current_holder()
        assert holder is not None
        assert holder[1] == lease.token
        assert holder[0] == lease._holder_id

    async def test_current_holder_handles_malformed_value(self, redis: Any) -> None:
        redis.set("maop:lease:job", "garbage-without-token")
        lease = DistributedLease(redis, "job", auto_renew=False)
        assert await lease.current_holder() is None

    async def test_current_holder_returns_none_on_error(self) -> None:
        broken = MagicMock()
        broken.get.side_effect = ConnectionError("down")
        lease = DistributedLease(broken, "job", auto_renew=False)
        assert await lease.current_holder() is None

    async def test_auto_renew_starts_and_stops_task(self, redis: Any) -> None:
        lease = DistributedLease(redis, "job", ttl=3, auto_renew=True)
        await lease.acquire()
        assert lease._renewal_task is not None
        assert not lease._renewal_task.done()

        await lease.release()
        assert lease._renewal_task is None

    async def test_renewal_loop_survives_cancellation(self, redis: Any) -> None:
        lease = DistributedLease(redis, "job", ttl=3, auto_renew=True)
        await lease.acquire()
        task = lease._renewal_task
        assert task is not None

        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        lease._stop_renewal()

    async def test_renewal_loop_stops_when_lease_lost(self, redis: Any) -> None:
        """续租时 key 已不存在 ⇒ 自认掉线，退出循环而不是空转。"""
        lease = DistributedLease(redis, "job", ttl=1, auto_renew=True)
        await lease.acquire()
        redis.delete("maop:lease:job")

        await asyncio.sleep(2.0)  # 越过 interval = max(1.0, ttl/3) 并留出调度余量
        assert lease.is_held is False
        lease._stop_renewal()

    async def test_close_releases(self, redis: Any) -> None:
        lease = DistributedLease(redis, "job", ttl=30, auto_renew=False)
        await lease.acquire()
        await lease.close()
        assert lease.is_held is False
        assert redis.get("maop:lease:job") is None

    async def test_repr(self, redis: Any) -> None:
        lease = DistributedLease(redis, "job", auto_renew=False)
        assert "DistributedLease(name='job'" in repr(lease)


# ── LeaseBasedLeaderElection ─────────────────────────────────────


class TestLeaseBasedLeaderElection:
    async def test_start_makes_leader(self, redis: Any) -> None:
        election = LeaseBasedLeaderElection(redis, "primary", ttl=5)
        assert election.is_leader is False
        assert election.name == "primary"

        await election.start()
        assert election.is_leader is True
        await election.stop()
        assert election.is_leader is False

    async def test_only_one_leader_at_a_time(self, redis: Any) -> None:
        a = LeaseBasedLeaderElection(redis, "primary", ttl=5)
        b = LeaseBasedLeaderElection(redis, "primary", ttl=5)

        await a.start()
        await b.start()
        assert a.is_leader is True
        assert b.is_leader is False

        await a.stop()
        assert await b.contested_once() is True
        await b.stop()

    async def test_heartbeat_false_when_not_leader(self, redis: Any) -> None:
        election = LeaseBasedLeaderElection(redis, "primary", ttl=5)
        assert await election.heartbeat() is False

    async def test_heartbeat_true_while_lease_held(self, redis: Any) -> None:
        election = LeaseBasedLeaderElection(redis, "primary", ttl=5)
        await election.start()
        assert await election.heartbeat() is True
        await election.stop()

    async def test_heartbeat_steps_down_when_lease_lost(self, redis: Any) -> None:
        election = LeaseBasedLeaderElection(redis, "primary", ttl=5)
        await election.start()
        election._lease._held = False  # 模拟租约被抢/过期
        assert await election.heartbeat() is False
        assert election.is_leader is False

    async def test_current_leader_reports_holder(self, redis: Any) -> None:
        election = LeaseBasedLeaderElection(redis, "primary", ttl=5)
        await election.start()
        leader = await election.current_leader()
        assert leader is not None
        assert leader[0] == election._lease._holder_id
        await election.stop()

    async def test_close_releases_leadership(self, redis: Any) -> None:
        election = LeaseBasedLeaderElection(redis, "primary", ttl=5)
        await election.start()
        await election.close()
        assert election.is_leader is False
        assert redis.get("maop:lease:election:primary") is None


# ── DAGCheckpoint ────────────────────────────────────────────────


class TestDAGCheckpoint:
    def test_progress_and_next_layer(self) -> None:
        ckpt = DAGCheckpoint("run-1", [["a"], ["b"], ["c"]])
        assert ckpt.total_layers == 3
        assert ckpt.progress == 0.0
        assert ckpt.next_layer == 0
        assert ckpt.is_complete is False

        ckpt.completed_layers = {0, 1}
        ckpt.pending_layers = {2}
        assert ckpt.progress == pytest.approx(2 / 3)
        assert ckpt.next_layer == 2

    def test_empty_layers_is_complete_by_progress(self) -> None:
        ckpt = DAGCheckpoint("run-empty", [])
        assert ckpt.total_layers == 0
        assert ckpt.progress == 1.0
        assert ckpt.next_layer is None

    def test_is_complete_requires_both_flags(self) -> None:
        ckpt = DAGCheckpoint("run-1", [["a"]], pending_layers=set(), status="running")
        assert ckpt.is_complete is False  # 没有 pending 但状态未完成
        ckpt.status = "completed"
        assert ckpt.is_complete is True

    def test_roundtrip_through_dict(self) -> None:
        ckpt = DAGCheckpoint(
            "run-1",
            [["a"], ["b"]],
            completed_layers={0},
            pending_layers={1},
            results={"a": {"ok": True}},
            metadata={"who": "test"},
        )
        restored = DAGCheckpoint.from_dict(ckpt.to_dict())
        assert restored.run_id == "run-1"
        assert restored.layers == [["a"], ["b"]]
        assert restored.completed_layers == {0}
        assert restored.pending_layers == {1}
        assert restored.results == {"a": {"ok": True}}
        assert restored.metadata == {"who": "test"}

    def test_from_dict_defaults_for_missing_keys(self) -> None:
        restored = DAGCheckpoint.from_dict({"run_id": "r", "layers": "[]"})
        assert restored.completed_layers == set()
        assert restored.status == "running"
        assert restored.created_at == 0.0

    def test_repr(self) -> None:
        ckpt = DAGCheckpoint("run-1", [["a"]], completed_layers={0})
        assert "DAGCheckpoint(run_id='run-1'" in repr(ckpt)


# ── DAGCheckpointManager ─────────────────────────────────────────


class TestDAGCheckpointManager:
    async def test_create_and_get(self, redis: Any) -> None:
        mgr = DAGCheckpointManager(redis, ttl=60)
        created = await mgr.create_run("run-1", [["a"], ["b"]], metadata={"k": "v"})
        assert created.pending_layers == {0, 1}
        assert created.total_layers == 2

        loaded = await mgr.get_run("run-1")
        assert loaded is not None
        assert loaded.run_id == "run-1"
        assert loaded.layers == [["a"], ["b"]]
        assert loaded.metadata == {"k": "v"}
        assert await mgr.get_run("nope") is None

    async def test_mark_layer_done_moves_pending_to_completed(self, redis: Any) -> None:
        mgr = DAGCheckpointManager(redis, ttl=60)
        await mgr.create_run("run-1", [["a"], ["b"]])

        after_first = await mgr.mark_layer_done("run-1", 0, {"a": {"ok": True}})
        assert after_first is not None
        assert await mgr.get_completed_layers("run-1") == {0}
        assert await mgr.get_pending_layers("run-1") == {1}
        assert after_first.status == "running"
        assert after_first.results == {"a": {"ok": True}}

        after_last = await mgr.mark_layer_done("run-1", 1)
        assert after_last is not None
        assert after_last.status == "completed"
        assert after_last.is_complete is True

    async def test_mark_layer_done_merges_results(self, redis: Any) -> None:
        mgr = DAGCheckpointManager(redis, ttl=60)
        await mgr.create_run("run-1", [["a"], ["b"]])
        await mgr.mark_layer_done("run-1", 0, {"a": {"n": 1}})
        final = await mgr.mark_layer_done("run-1", 1, {"b": {"n": 2}})
        assert final is not None
        assert final.results == {"a": {"n": 1}, "b": {"n": 2}}

    async def test_mark_layer_done_returns_none_on_redis_error(self) -> None:
        broken = MagicMock()
        broken.pipeline.side_effect = ConnectionError("down")
        mgr = DAGCheckpointManager(broken)
        assert await mgr.mark_layer_done("run-1", 0) is None

    async def test_mark_failed_sets_status(self, redis: Any) -> None:
        mgr = DAGCheckpointManager(redis, ttl=60)
        await mgr.create_run("run-1", [["a"]])
        await mgr.mark_failed("run-1", "boom")

        loaded = await mgr.get_run("run-1")
        assert loaded is not None
        assert loaded.status == "failed"
        assert loaded.metadata == {"error": "boom"}

    async def test_mark_failed_swallows_errors(self) -> None:
        broken = MagicMock()
        broken.hset.side_effect = ConnectionError("down")
        mgr = DAGCheckpointManager(broken)
        await mgr.mark_failed("run-1", "boom")  # 不抛

    async def test_recover_run(self, redis: Any) -> None:
        mgr = DAGCheckpointManager(redis, ttl=60)
        await mgr.create_run("run-1", [["a"], ["b"], ["c"]])
        await mgr.mark_layer_done("run-1", 0)

        recovered = await mgr.recover_run("run-1")
        assert recovered is not None
        assert recovered.next_layer == 1
        assert recovered.pending_layers == {1, 2}
        assert await mgr.recover_run("missing") is None

    async def test_list_interrupted_filters_sub_keys_and_finished_runs(
        self, redis: Any,
    ) -> None:
        mgr = DAGCheckpointManager(redis, ttl=60)
        await mgr.create_run("running-1", [["a"], ["b"]])
        await mgr.mark_layer_done("running-1", 0)
        await mgr.create_run("done-1", [["a"]])
        await mgr.mark_layer_done("done-1", 0)
        await mgr.create_run("failed-1", [["a"]])
        await mgr.mark_failed("failed-1", "x")

        interrupted = await mgr.list_interrupted()
        ids = sorted(c.run_id for c in interrupted)
        assert ids == ["running-1"]

    async def test_list_interrupted_returns_empty_on_error(self) -> None:
        broken = MagicMock()
        broken.keys.side_effect = ConnectionError("down")
        mgr = DAGCheckpointManager(broken)
        assert await mgr.list_interrupted() == []

    async def test_delete_run_removes_all_keys(self, redis: Any) -> None:
        mgr = DAGCheckpointManager(redis, ttl=60)
        await mgr.create_run("run-1", [["a"], ["b"]])
        await mgr.delete_run("run-1")

        assert await mgr.get_run("run-1") is None
        assert await mgr.get_completed_layers("run-1") == set()
        assert await mgr.get_pending_layers("run-1") == set()

    async def test_delete_run_swallows_errors(self) -> None:
        broken = MagicMock()
        broken.pipeline.side_effect = ConnectionError("down")
        mgr = DAGCheckpointManager(broken)
        await mgr.delete_run("run-1")  # 不抛

    async def test_persist_failure_raises_runtime_error(self) -> None:
        broken = MagicMock()
        broken.pipeline.side_effect = ConnectionError("down")
        mgr = DAGCheckpointManager(broken)
        with pytest.raises(RuntimeError, match="persist failed"):
            await mgr.create_run("run-1", [["a"]])

    async def test_count_pending_returns_zero_on_error(self) -> None:
        broken = MagicMock()
        broken.scard.side_effect = ConnectionError("down")
        mgr = DAGCheckpointManager(broken)
        assert await mgr._count_pending("run-1") == 0

    async def test_get_completed_and_pending_return_empty_on_error(self) -> None:
        broken = MagicMock()
        broken.smembers.side_effect = ConnectionError("down")
        mgr = DAGCheckpointManager(broken)
        assert await mgr.get_completed_layers("run-1") == set()
        assert await mgr.get_pending_layers("run-1") == set()

    async def test_get_run_returns_none_on_error(self) -> None:
        broken = MagicMock()
        broken.hgetall.side_effect = ConnectionError("down")
        mgr = DAGCheckpointManager(broken)
        assert await mgr.get_run("run-1") is None


# ── BlackboardTaskContext ────────────────────────────────────────


class TestBlackboardTaskContext:
    async def test_unattached_access_raises(self) -> None:
        ctx = BlackboardTaskContext(node_id="w1", auto_sync=False)
        assert ctx.node_id == "w1"
        with pytest.raises(RuntimeError, match="not attached"):
            _ = ctx.blackboard
        with pytest.raises(RuntimeError, match="not attached"):
            ctx.read_domain("problem")
        with pytest.raises(RuntimeError, match="not attached"):
            ctx.get_snapshot()

    async def test_node_id_is_generated_when_omitted(self) -> None:
        ctx = BlackboardTaskContext(auto_sync=False)
        assert ctx.node_id.startswith("worker-")

    async def test_attach_without_sync_bootstraps_empty_snapshot(self) -> None:
        ctx = BlackboardTaskContext(node_id="w1", auto_sync=False)
        await ctx.attach()
        assert ctx.get_snapshot() == {}
        assert ctx.read_domain("decision") == []

        await ctx.detach()
        with pytest.raises(RuntimeError, match="not attached"):
            _ = ctx.blackboard

    async def test_contribute_writes_to_attached_blackboard(self) -> None:
        ctx = BlackboardTaskContext(node_id="w1", auto_sync=False)
        await ctx.attach()
        entry = await ctx.contribute(
            "decision", {"v": 1}, contributor="w1", confidence=0.7,
            metadata={"src": "test"},
        )
        assert entry.content == {"v": 1}
        assert entry.contributor == "w1"

        entries = ctx.read_domain("decision")
        assert len(entries) == 1
        assert entries[0].confidence == pytest.approx(0.7)
        assert "decision" in ctx.get_snapshot()
        await ctx.detach()


# ── RedisAgentStore ──────────────────────────────────────────────


class TestRedisAgentStore:
    """Redis 后端的 agent 存储 —— 只走 InMemoryAgentStore 的测试抓不到这里的错。

    两个历史缺陷（都是"测试只覆盖 InMemory 路径"掩盖的）：

    - ``remove()`` 用了 ``_json.dumps`` 却没 ``import json``（ruff F821）；
    - ``get()`` 的 ``hget`` 漏了 ``await``，异步客户端下每次查询都 TypeError。
    """

    @pytest.fixture
    def store(self) -> Any:
        import fakeredis.aioredis

        from maop.core.coordination.agent_registry import RedisAgentStore

        s = RedisAgentStore("redis://unused:6379/0")
        s._redis = fakeredis.aioredis.FakeRedis()
        return s

    @staticmethod
    def _card(card_id: str = "agent-1") -> Any:
        from maop.core.coordination.a2a_blackboard_bridge import AgentCard

        return AgentCard(
            card_id=card_id,
            name="n1",
            url="http://n1",
            description="d",
            capabilities=["plan"],
            status="active",
            health_url="http://n1/h",
            metadata={"zone": "a"},
        )

    async def test_save_then_get_roundtrip(self, store: Any) -> None:
        await store.save(self._card())
        loaded = await store.get("agent-1")
        assert loaded is not None
        assert loaded.card_id == "agent-1"
        assert loaded.capabilities == ["plan"]
        assert loaded.metadata == {"zone": "a"}

    async def test_get_missing_returns_none(self, store: Any) -> None:
        assert await store.get("nope") is None

    async def test_remove_deletes_and_publishes(self, store: Any) -> None:
        await store.save(self._card())
        await store.remove("agent-1")  # 曾经因 _json 未导入直接 NameError
        assert await store.list() == []
        assert await store.get("agent-1") is None

    async def test_list_skips_malformed_entries(self, store: Any) -> None:
        await store.save(self._card("a"))
        await store.save(self._card("b"))
        await store._redis.hset(store._agents_key(), "broken", "not-json")

        ids = sorted(c.card_id for c in await store.list())
        assert ids == ["a", "b"]

    async def test_agents_key_is_the_shared_hash(self, store: Any) -> None:
        assert store._agents_key() == "maop:a2a:agents"


# ── MCP Hub：内存注册表 + 传输守卫 ───────────────────────────────


class TestMCPHubRegistry:
    @staticmethod
    def _server(name: str, tools: list[str], desc: str = "") -> Any:
        from maop.core.coordination.mcp_hub import MCPServer

        return MCPServer(
            name=name,
            transport=MagicMock(),
            description=desc,
            tools=[{"name": t, "description": f"{t} tool"} for t in tools],
        )

    async def test_register_and_list_servers(self) -> None:
        from maop.core.coordination.mcp_hub import InMemoryMCPRegistry

        reg = InMemoryMCPRegistry()
        await reg.register(self._server("fs", ["read", "write"]))
        await reg.register(self._server("git", ["status"]))

        names = sorted(s.name for s in reg.list_servers())
        assert names == ["fs", "git"]

    async def test_register_is_idempotent_by_name(self) -> None:
        from maop.core.coordination.mcp_hub import InMemoryMCPRegistry

        reg = InMemoryMCPRegistry()
        await reg.register(self._server("fs", ["read"]))
        await reg.register(self._server("fs", ["read", "write"]))
        assert len(reg.list_servers()) == 1
        assert len(reg.find_tools("write")) == 1

    async def test_unregister(self) -> None:
        from maop.core.coordination.mcp_hub import InMemoryMCPRegistry

        reg = InMemoryMCPRegistry()
        await reg.register(self._server("fs", ["read"]))
        await reg.unregister("fs")
        assert reg.list_servers() == []
        await reg.unregister("never-registered")  # 不抛

    async def test_find_tools_empty_registry(self) -> None:
        from maop.core.coordination.mcp_hub import InMemoryMCPRegistry

        assert InMemoryMCPRegistry().find_tools() == []

    async def test_find_tools_filters_case_insensitively(self) -> None:
        from maop.core.coordination.mcp_hub import InMemoryMCPRegistry

        reg = InMemoryMCPRegistry()
        await reg.register(self._server("fs", ["read_file", "write_file"], desc="files"))
        await reg.register(self._server("git", ["status"], desc="vcs"))

        hits = reg.find_tools("READ")
        assert len(hits) == 1
        assert hits[0]["server_name"] == "fs"
        assert hits[0]["tool"]["name"] == "read_file"
        assert hits[0]["server_description"] == "files"

        assert len(reg.find_tools()) == 3  # 空词 = 全部


class TestMCPTransportGuards:
    """未 connect 就发请求必须立刻报错，而不是把 None 当客户端用。"""

    async def test_stdio_send_before_connect_raises(self) -> None:
        from maop.core.coordination.mcp_hub import StdioTransport

        transport = StdioTransport("no-such-binary")
        with pytest.raises(RuntimeError, match="not connected"):
            await transport.send_request("ping", {})

    async def test_stdio_disconnect_without_connect(self) -> None:
        from maop.core.coordination.mcp_hub import StdioTransport

        await StdioTransport("no-such-binary").disconnect()  # 不抛

    async def test_sse_send_before_connect_raises(self) -> None:
        from maop.core.coordination.mcp_hub import SSETransport

        with pytest.raises(RuntimeError, match="not connected"):
            await SSETransport("http://localhost:1/sse").send_request("ping", {})

    async def test_websocket_send_before_connect_raises(self) -> None:
        from maop.core.coordination.mcp_hub import WebSocketTransport

        with pytest.raises(RuntimeError, match="not connected"):
            await WebSocketTransport("ws://localhost:1/ws").send_request("ping", {})

    async def test_streamable_http_send_before_connect_raises(self) -> None:
        from maop.core.coordination.mcp_hub import StreamableHTTPTransport

        with pytest.raises(RuntimeError, match="not connected"):
            await StreamableHTTPTransport("http://localhost:1/mcp").send_request("ping", {})


class TestMCPServerContract:
    async def test_initialize_connects_then_handshakes(self) -> None:
        from maop.core.coordination.mcp_hub import MCPServer

        calls: list[str] = []

        class FakeTransport:
            async def connect(self) -> None:
                calls.append("connect")

            async def send_request(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
                calls.append(method)
                return {"protocolVersion": params["protocolVersion"]}

        server = MCPServer(name="fake", transport=FakeTransport())
        caps = await server.initialize()
        assert calls == ["connect", "initialize"]
        assert caps["protocolVersion"] == "2024-11-05"

    async def test_call_tool_passes_name_and_arguments(self) -> None:
        from maop.core.coordination.mcp_hub import MCPServer

        seen: dict[str, Any] = {}

        class FakeTransport:
            async def connect(self) -> None:
                pass

            async def send_request(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
                seen["method"] = method
                seen["params"] = params
                return {"content": [{"type": "text", "text": "ok"}]}

        server = MCPServer(name="fake", transport=FakeTransport())
        result = await server.call_tool("read_file", {"path": "/tmp/x"})
        assert seen["method"] == "tools/call"
        assert seen["params"] == {"name": "read_file", "arguments": {"path": "/tmp/x"}}
        assert result["content"][0]["text"] == "ok"


# ── 调度器 checkpoint 协调器 ─────────────────────────────────────


class TestDAGRunCheckpointSerialization:
    def test_roundtrip_preserves_layers_and_failures(self) -> None:
        from maop.core.scheduling.distributed_checkpoint_coordinator import (
            DAGRunCheckpoint,
            LayerCheckpoint,
        )

        ckpt = DAGRunCheckpoint(
            run_id="run-1",
            total_layers=2,
            completed_layers=[LayerCheckpoint(layer_idx=0, node_ids=["a"], duration_ms=12)],
            failed_nodes=["b"],
            status="running",
            resume_count=1,
            node_map_snapshot={"a": {"id": "a"}},
        )
        restored = DAGRunCheckpoint.from_dict(ckpt.to_dict())
        assert restored.run_id == "run-1"
        assert restored.total_layers == 2
        assert restored.failed_nodes == ["b"]
        assert restored.resume_count == 1
        assert restored.node_map_snapshot == {"a": {"id": "a"}}
        assert len(restored.completed_layers) == 1
        assert restored.completed_layers[0].layer_idx == 0
        assert restored.completed_layers[0].node_ids == ["a"]
        assert restored.completed_layers[0].duration_ms == 12

    def test_from_dict_uses_defaults(self) -> None:
        from maop.core.scheduling.distributed_checkpoint_coordinator import (
            DAGRunCheckpoint,
        )

        restored = DAGRunCheckpoint.from_dict({"run_id": "r"})
        assert restored.total_layers == 0
        assert restored.completed_layers == []
        assert restored.status == "pending"


class TestDistributedCheckpointCoordinator:
    @pytest.fixture
    def coordinator(self) -> Any:
        import fakeredis.aioredis

        from maop.core.scheduling.distributed_checkpoint_coordinator import (
            DistributedCheckpointCoordinator,
        )

        return DistributedCheckpointCoordinator(MagicMock(), fakeredis.aioredis.FakeRedis())

    async def test_save_then_load(self, coordinator: Any) -> None:
        from maop.core.scheduling.distributed_checkpoint_coordinator import (
            DAGRunCheckpoint,
            LayerCheckpoint,
        )

        ckpt = DAGRunCheckpoint(
            run_id="run-1",
            total_layers=3,
            completed_layers=[LayerCheckpoint(layer_idx=0, node_ids=["a"])],
            status="running",
        )
        await coordinator._save_checkpoint(ckpt)

        loaded = await coordinator.get_checkpoint("run-1")
        assert loaded is not None
        assert loaded.status == "running"
        assert len(loaded.completed_layers) == 1
        assert await coordinator.get_checkpoint("missing") is None

    async def test_load_malformed_json_returns_none(self, coordinator: Any) -> None:
        from maop.core.scheduling.distributed_checkpoint_coordinator import (
            _CHECKPOINT_HASH,
        )

        await coordinator._redis.hset(_CHECKPOINT_HASH, "bad", "not-json")
        assert await coordinator.get_checkpoint("bad") is None

    async def test_list_incomplete_runs(self, coordinator: Any) -> None:
        from maop.core.scheduling.distributed_checkpoint_coordinator import (
            DAGRunCheckpoint,
        )

        await coordinator._save_checkpoint(
            DAGRunCheckpoint(run_id="pending-1", status="pending")
        )
        await coordinator._save_checkpoint(
            DAGRunCheckpoint(run_id="running-1", status="running")
        )
        await coordinator._save_checkpoint(
            DAGRunCheckpoint(run_id="done-1", status="completed")
        )
        assert sorted(await coordinator.list_incomplete_runs()) == ["pending-1", "running-1"]

    async def test_list_incomplete_skips_malformed_entries(self, coordinator: Any) -> None:
        from maop.core.scheduling.distributed_checkpoint_coordinator import (
            _CHECKPOINT_HASH,
        )

        await coordinator._redis.hset(_CHECKPOINT_HASH, "broken", "not-json")
        assert await coordinator.list_incomplete_runs() == []

    async def test_cleanup_removes_run_and_layer_status(self, coordinator: Any) -> None:
        from maop.core.scheduling.distributed_checkpoint_coordinator import (
            _CHECKPOINT_HASH,
            _LAYER_STATUS_HASH,
            _RUN_META,
            DAGRunCheckpoint,
        )

        await coordinator._save_checkpoint(DAGRunCheckpoint(run_id="run-1", status="running"))
        await coordinator._redis.hset(_LAYER_STATUS_HASH, "run-1:0", "[]")
        await coordinator._redis.hset(_LAYER_STATUS_HASH, "run-2:0", "[]")

        await coordinator.cleanup("run-1")

        assert await coordinator._redis.hget(_CHECKPOINT_HASH, "run-1") is None
        assert await coordinator._redis.hget(_RUN_META, "run-1") is None
        assert await coordinator._redis.hget(_LAYER_STATUS_HASH, "run-1:0") is None
        # 别的 run 的层状态不能被误删
        assert await coordinator._redis.hget(_LAYER_STATUS_HASH, "run-2:0") is not None

    async def test_serialize_node_map_keeps_scheduling_fields(self, coordinator: Any) -> None:
        from types import SimpleNamespace

        node_map = {
            "a": SimpleNamespace(depends_on=[], affinity=["gpu"], priority=1),
            "b": SimpleNamespace(),  # 缺字段 → 取默认值
        }
        snapshot = coordinator._serialize_node_map(node_map)
        assert snapshot["a"] == {"id": "a", "depends_on": [], "affinity": ["gpu"], "priority": 1}
        assert snapshot["b"] == {"id": "b", "depends_on": [], "affinity": [], "priority": 3}


# ── A2A 传输服务：路由与错误边界 ────────────────────────────────


class TestA2ATransportServerRoutes:
    @staticmethod
    def _server(bridge: Any = None, registry: Any = None) -> Any:
        from maop.core.coordination.a2a_transport_server import (
            InMemoryTaskStore,
            _A2ARequestHandler,
        )

        return _A2ARequestHandler(
            bridge=bridge,
            registry=registry,
            task_store=InMemoryTaskStore(),
            agent_card={"name": "test-node", "version": "1.0.0"},
        )

    async def test_health_route(self) -> None:
        status, payload = await self._server().handle_request("GET", "/a2a/health", None)
        assert status == 200
        assert payload["status"] == "ok"
        assert "timestamp" in payload

    async def test_agent_card_routes(self) -> None:
        srv = self._server()
        for path in ("/.well-known/agent-card.json", "/a2a/agent-card"):
            status, payload = await srv.handle_request("GET", path, None)
            assert status == 200
            assert payload["name"] == "test-node"

    async def test_unknown_route_returns_404(self) -> None:
        status, payload = await self._server().handle_request("GET", "/nope", None)
        assert status == 404
        assert "No route" in payload["error"]

    async def test_handler_exception_becomes_500(self) -> None:
        srv = self._server()

        async def _boom(body: bytes | None) -> tuple[int, dict[str, Any]]:
            raise RuntimeError("handler exploded")

        srv._routes["GET"]["/a2a/health"] = _boom
        status, payload = await srv.handle_request("GET", "/a2a/health", None)
        assert status == 500
        assert "handler exploded" in payload["error"]

    async def test_list_agents_maps_registry_cards(self) -> None:
        from maop.core.coordination.a2a_blackboard_bridge import AgentCard

        class Registry:
            async def list_agents(self) -> list[Any]:
                return [AgentCard(card_id="a1", name="n1", url="http://a1", capabilities=["x"])]

        status, payload = await self._server(registry=Registry()).handle_request(
            "GET", "/a2a/agents", None,
        )
        assert status == 200
        assert payload["count"] == 1
        assert payload["agents"][0]["card_id"] == "a1"
        assert payload["agents"][0]["capabilities"] == ["x"]

    async def test_list_agents_survives_registry_error(self) -> None:
        class Registry:
            async def list_agents(self) -> list[Any]:
                raise ConnectionError("registry down")

        status, payload = await self._server(registry=Registry()).handle_request(
            "GET", "/a2a/agents", None,
        )
        assert status == 200  # 降级成空列表，而不是 500
        assert payload == {"agents": [], "count": 0}

    async def test_create_task_parses_body(self) -> None:
        srv = self._server()
        body = b'{"description": "do it", "input": "x", "requester": "bob"}'
        status, payload = await srv.handle_request("POST", "/a2a/tasks", body)
        assert status == 201
        assert payload["status"] == "submitted"
        assert payload["id"]

        stored = srv._tasks.get(payload["id"])
        assert stored is not None
        assert stored["description"] == "do it"
        assert stored["requester"] == "bob"

    async def test_create_task_without_body(self) -> None:
        status, payload = await self._server().handle_request("POST", "/a2a/tasks", None)
        assert status == 201
        assert payload["id"]

    async def test_message_routes_through_bridge(self) -> None:
        class Bridge:
            def __init__(self) -> None:
                self.seen: Any = None

            async def on_a2a_message(self, msg: Any) -> str:
                self.seen = msg
                return "handled"

        bridge = Bridge()
        srv = self._server(bridge=bridge)
        body = b'{"method": "tasks/send", "params": {"q": 1}, "id": "m1", "source_agent": "a"}'
        status, payload = await srv.handle_request("POST", "/a2a/messages", body)

        assert status == 200
        assert payload["jsonrpc"] == "2.0"
        assert payload["id"] == "m1"
        assert payload["result"]["bridge_result"] == "handled"
        assert bridge.seen.method == "tasks/send"
        assert bridge.seen.params == {"q": 1}

    async def test_register_agent_prefers_register_agent_method(self) -> None:
        class Registry:
            def __init__(self) -> None:
                self.cards: list[Any] = []

            async def register_agent(self, card: Any) -> None:
                self.cards.append(card)

        registry = Registry()
        body = b'{"card_id": "a9", "name": "node9", "capabilities": ["plan"]}'
        status, payload = await self._server(registry=registry).handle_request(
            "POST", "/a2a/agents/register", body,
        )
        assert status == 201
        assert payload == {"card_id": "a9", "status": "registered"}
        assert registry.cards[0].name == "node9"
        assert registry.cards[0].status == "active"

    async def test_register_agent_falls_back_to_sync_register(self) -> None:
        class Registry:
            def __init__(self) -> None:
                self.cards: list[Any] = []

            def register(self, card: Any) -> None:  # 同步实现
                self.cards.append(card)

        registry = Registry()
        status, _payload = await self._server(registry=registry).handle_request(
            "POST", "/a2a/agents/register", b'{"card_id": "a8"}',
        )
        assert status == 201
        assert len(registry.cards) == 1

    async def test_register_agent_without_register_support_returns_500(self) -> None:
        class Registry:
            pass

        status, payload = await self._server(registry=Registry()).handle_request(
            "POST", "/a2a/agents/register", b'{"card_id": "a7"}',
        )
        assert status == 500
        assert "does not support" in payload["error"]
