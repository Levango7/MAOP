"""Stage 1 端到端验证：AgentIdentity + HealthService + Dispatcher/DistributedScheduler 健康门禁.

覆盖：
  1. AgentIdentity 联邦映射表：agent_name <-> card_id <-> worker_id 双向查询；进程级单例
  2. HealthService 探测：CLI/HTTP/TCP 探针；结果写入 AgentCatalog.healthy；单次 + 批量探测
  3. Dispatcher 健康门禁：unhealthy / drained(weight=0) agent 被拒绝；healthy agent 正常分发；异常降级为 pass-through
  4. DistributedScheduler 健康门禁：unhealthy/drained worker 被过滤；healthy worker 正常入队；无映射保守放行
  5. CLI/API 完好性：现有入口不受影响（AgentCatalog register/get/list_enabled 不变）

每个测试使用 tmp_path 隔离 SQLite，fakeredis 隔离 Redis，不触碰真实 DB/网络。
"""

from __future__ import annotations

import asyncio
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

# 确保 maop 可导入
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import fakeredis

from maop.core.agent.identity import (
    AgentIdentity,
    get_agent_identity,
    reset_agent_identity,
)
from maop.core.agent.registry.agent_catalog import (
    AgentCapability,
    AgentCatalog,
    AgentDescriptor,
)
from maop.core.scheduling.distributed_scheduler import (
    DistributedScheduler,
    NodeAffinity,
    _NodeSpec,
)
from maop.core.scheduling.failure_detector import (
    FailurePatternDetector,
    set_failure_detector,
)
from maop.core.scheduling.health_service import (
    HealthService,
    get_health_service,
    reset_health_service,
)
from maop.core.scheduling.worker_pool import WorkerRegistry
from maop.delegate.dispatcher import Dispatcher

# =====================================================================
# 辅助：构造干净的 AgentCatalog（每个测试用不同 DB）
# =====================================================================

def _make_catalog(db_path: Path) -> AgentCatalog:
    """构造指向临时 DB 的 AgentCatalog 实例（非单例，避免跨测试污染）。"""
    return AgentCatalog(db_path=str(db_path))


def _register_agent(catalog: AgentCatalog, name: str, healthy: bool = True,
                    adapter_type: str = "cli", **extra_cfg) -> AgentDescriptor:
    """在 catalog 中注册一个 agent，返回 descriptor。"""
    desc = AgentDescriptor(
        name=name,
        adapter_type=adapter_type,
        adapter_config=extra_cfg,
        capabilities=[AgentCapability.CODE_GENERATION],
        healthy=healthy,
        enabled=True,
    )
    catalog.register(desc)
    return desc


# =====================================================================
# 1. AgentIdentity 联邦映射表
# =====================================================================

class TestAgentIdentity(unittest.TestCase):
    def setUp(self):
        reset_agent_identity()
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp:
            self.db_path = Path(tmp.name)
        self.identity = AgentIdentity(db_path=self.db_path)

    def tearDown(self):
        reset_agent_identity()
        try:
            os.unlink(self.db_path)
        except OSError:
            pass

    def test_bidirectional_mapping(self):
        """agent_name <-> card_id <-> worker_id 双向查询。"""
        self.identity.register_mapping("coder", card_id="card-1", worker_id="worker-1", source="cli")

        # name -> card/worker
        rows = self.identity.get_by_agent_name("coder")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["card_id"], "card-1")
        self.assertEqual(rows[0]["worker_id"], "worker-1")

        # card_id -> name
        rows = self.identity.get_by_card_id("card-1")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["agent_name"], "coder")

        # worker_id -> name
        rows = self.identity.get_by_worker_id("worker-1")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["agent_name"], "coder")

    def test_resolve_agent_name(self):
        """resolve_agent_name 优先 card_id，其次 worker_id。"""
        self.identity.register_mapping("coder", card_id="card-1", worker_id="worker-1")

        self.assertEqual(self.identity.resolve_agent_name(card_id="card-1"), "coder")
        self.assertEqual(self.identity.resolve_agent_name(worker_id="worker-1"), "coder")
        # 优先 card_id：即使 card_id 指向另一个 agent，也返回 card_id 对应的
        self.identity.register_mapping("reviewer", card_id="card-2", worker_id="worker-1")
        self.assertEqual(self.identity.resolve_agent_name(worker_id="worker-1"), "coder")

    def test_resolve_not_found(self):
        """找不到返回 None。"""
        self.assertIsNone(self.identity.resolve_agent_name(card_id="nope"))
        self.assertIsNone(self.identity.resolve_agent_name(worker_id="nope"))

    def test_remove_mapping(self):
        """remove_mapping 按条件删除。"""
        self.identity.register_mapping("coder", card_id="card-1", worker_id="worker-1")
        self.identity.register_mapping("coder", card_id="card-2", worker_id="worker-2")

        # 仅按 agent_name 删除所有
        count = self.identity.remove_mapping("coder")
        self.assertEqual(count, 2)
        self.assertEqual(self.identity.get_by_agent_name("coder"), [])

    def test_singleton(self):
        """get_agent_iden
tity() 返回进程级单例。"""
        reset_agent_identity()
        a = get_agent_identity()
        b = get_agent_identity()
        self.assertIs(a, b)

    def test_multiple_cards_per_agent(self):
        """一个 agent 可关联多个 card_id（四套 ID 体系传播）。"""
        self.identity.register_mapping("coder", card_id="card-a", worker_id="worker-1")
        self.identity.register_mapping("coder", card_id="card-b", worker_id="worker-2")
        rows = self.identity.get_by_agent_name("coder")
        self.assertEqual(len(rows), 2)
        cards = {r["card_id"] for r in rows}
        self.assertEqual(cards, {"card-a", "card-b"})


# =====================================================================
# 2. HealthService 探测
# =====================================================================

class TestHealthService(unittest.TestCase):
    def setUp(self):
        reset_health_service()
        reset_agent_identity()
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp:
            self.db_path = Path(tmp.name)
        self.catalog = _make_catalog(self.db_path)

    def tearDown(self):
        reset_health_service()
        reset_agent_identity()
        try:
            os.unlink(self.db_path)
        except OSError:
            pass

    def test_probe_agent_not_found(self):
        """catalog 中不存在的 agent 返回 unhealthy。"""
        svc = HealthService(catalog=self.catalog)
        result = asyncio.run(svc.probe_agent("nonexistent"))
        self.assertFalse(result.healthy)
        self.assertIn("not found in catalog", result.error)

    def test_probe_cli_success(self):
        """CLI --version 返回 0/1 视为 healthy。"""
        _register_agent(self.catalog, "cli-agent", healthy=True,
                        adapter_type="cli", cli_path=sys.executable)

        svc = HealthService(catalog=self.catalog, probe_timeout_s=5)
        result = asyncio.run(svc.probe_agent("cli-agent"))
        # python --version 应返回 0
        self.assertTrue(result.healthy)
        self.assertEqual(result.source, "cli")

    def test_probe_cli_missing(self):
        """CLI 路径不存在返回 unhealthy。"""
        _register_agent(self.catalog, "cli-agent", healthy=True,
                        adapter_type="cli", cli_path="/nonexistent/cli")

        svc = HealthService(catalog=self.catalog, probe_timeout_s=5)
        result = asyncio.run(svc.probe_agent("cli-agent"))
        self.assertFalse(result.healthy)
        self.assertEqual(result.source, "cli")

    def test_persist_to_catalog(self):
        """探测结果写入 AgentCatalog.healthy。"""
        _register_agent(self.catalog, "cli-agent", healthy=True,
                        adapter_type="cli", cli_path=sys.executable)

        svc = HealthService(catalog=self.catalog, probe_timeout_s=5)
        asyncio.run(svc.probe_agent("cli-agent"))

        desc = self.catalog.get("cli-agent")
        self.assertTrue(desc.healthy)
        self.assertGreater(desc.last_health_check, 0)

    def test_probe_all_enabled(self):
        """probe_all_enabled 并发探测全部 enabled agent。"""
        _register_agent(self.catalog, "agent-a", healthy=True,
                        adapter_type="cli", cli_path=sys.executable)
        _register_agent(self.catalog, "agent-b", healthy=True,
                        adapter_type="cli", cli_path=sys.executable)

        svc = HealthService(catalog=self.catalog, probe_timeout_s=5)
        results = asyncio.run(svc.probe_all_enabled())
        self.assertEqual(len(results), 2)
        names = {r.agent_name for r in results}
        self.assertEqual(names, {"agent-a", "agent-b"})

    def test_list_unhealthy(self):
        """list_unhealthy 仅返回 unhealthy agent。"""
        _register_agent(self.catalog, "healthy-agent", healthy=True,
                        adapter_type="cli", cli_path=sys.executable)
        _register_agent(self.catalog, "sick-agent", healthy=False,
                        adapter_type="cli", cli_path="/nonexistent")

        svc = HealthService(catalog=self.catalog, probe_timeout_s=5)
        asyncio.run(svc.probe_all_enabled())
        unhealthy = asyncio.run(svc.list_unhealthy())
        unhealthy_names = {r.agent_name for r in unhealthy}
        self.assertIn("sick-agent", unhealthy_names)

    def test_is_healthy_sync(self):
        """is_healthy() 同步读取缓存。"""
        _register_agent(self.catalog, "cli-agent", healthy=True,
                        adapter_type="cli", cli_path=sys.executable)

        svc = HealthService(catalog=self.catalog, probe_timeout_s=5)
        asyncio.run(svc.probe_agent("cli-agent"))

        self.assertTrue(svc.is_healthy("cli-agent"))
        self.assertIsNone(svc.is_healthy("unknown-agent"))

    def test_singleton(self):
        """get_health_service() 返回进程级单例。"""
        reset_health_service()
        a = get_health_service()
        b = get_health_service()
        self.assertIs(a, b)


# =====================================================================
# 3. Dispatcher 健康门禁
# =====================================================================

class TestDispatcherHealthGate(unittest.TestCase):
    def setUp(self):
        reset_agent_identity()
        reset_health_service()
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp:
            self.db_path = Path(tmp.name)
        self.catalog = _make_catalog(self.db_path)

        # 干净的 failure detector（weight 默认 1.0，注册为模块级单例）
        self.detector = FailurePatternDetector()
        set_failure_detector(self.detector)

        # 构造 Dispatcher（不连接真实 driver，用 mock）
        self.dispatcher = Dispatcher.__new__(Dispatcher)
        self.dispatcher._root_dir = str(tempfile.mkdtemp())
        self.dispatcher._effective_model = None
        self.dispatcher._model_selector = None
        self.dispatcher._resolver = MagicMock()  # 必须在 _resolve_agent 被调用前存在
        self.dispatcher._resolver.resolve.return_value = MagicMock(
            name="claude", capabilities=[], model="", adapter_type="cli",
            adapter_config={},
        )
        self.dispatcher._sla = MagicMock()
        self.dispatcher._sla.tier_from_priority.return_value = "standard"
        self.dispatcher._breaker = MagicMock()
        self.dispatcher._breaker.ais_available = AsyncMock(return_value=True)
        self.dispatcher._breaker.arecord_failure = AsyncMock()

    def tearDown(self):
        reset_agent_identity()
        reset_health_service()
        set_failure_detector(None)
        try:
            os.unlink(self.db_path)
        except OSError:
            pass

    def _patch_catalog_default(self):
        """将 AgentCatalog.default() 指向测试 catalog（monkeypatch 类方法）。"""
        self._orig_default = AgentCatalog.default
        AgentCatalog.default = classmethod(lambda cls: self.catalog)

    def _restore_catalog_default(self):
        AgentCatalog.default = self._orig_default

    def test_healthy_agent_dispatched(self):
        """healthy agent 正常分发（driver 返回成功）。"""
        self._patch_catalog_default()
        try:
            _register_agent(self.catalog, "claude", healthy=True,
                            adapter_type="cli", cli_path=sys.executable)

            # mock driver 执行成功
            async def _fake_dispatch_impl(agent, task, **kwargs):
                from maop.core.reliability.error_schema import new_result
                from maop.delegate.models import DispatchResult
                r = new_result(agent=agent, task=task, exit_code=0, stdout="ok",
                               trace_id="t", routing_key="rk")
                return DispatchResult(result=r, breaker_tripped=False)

            with patch.object(self.dispatcher, "_dispatch_impl", new=_fake_dispatch_impl):
                result = asyncio.run(self.dispatcher.dispatch(
                    agent="claude", task="write code", trace_id="t", routing_key="rk",
                ))
            self.assertFalse(result.breaker_tripped)
        finally:
            self._restore_catalog_default()

    def test_unhealthy_agent_rejected(self):
        """unhealthy agent 被健康门禁拒绝（exit_code=-7）。"""
        self._patch_catalog_default()
        try:
            _register_agent(self.catalog, "claude", healthy=False,
                            adapter_type="cli", cli_path=sys.executable)

            result = asyncio.run(self.dispatcher.dispatch(
                agent="claude", task="write code", trace_id="t", routing_key="rk",
            ))
            self.assertTrue(result.breaker_tripped is False)
            self.assertEqual(result.result.exit_code, -7)
            self.assertIn("unhealthy", result.result.error)
        finally:
            self._restore_catalog_default()

    def test_drained_agent_rejected(self):
        """drained agent (weight=0) 被健康门禁拒绝。"""
        self._patch_catalog_default()
        try:
            _register_agent(self.catalog, "claude", healthy=True,
                            adapter_type="cli", cli_path=sys.executable)
            # 手动把 detector 中 claude 的 weight 置为 0（drained）；
            # set_failure_detector 已在 setUp 把本实例注册为模块级单例，
            # dispatch_core 里的 get_failure_detector() 会返回它。
            from maop.core.scheduling.failure_detector import _AgentState
            with self.detector._lock:
                state = self.detector._agents.setdefault("claude", _AgentState())
                state.drained = True
                state.weight = 0.0

            orig_resolve = self.dispatcher._resolve_agent
            self.dispatcher._resolve_agent = lambda agent_name: MagicMock(
                name="claude", capabilities=[], model="", adapter_type="cli", adapter_config={}
            )
            try:
                result = asyncio.run(self.dispatcher._dispatch_impl_inner(
                    agent="claude", task="write code",
                    routing_key="rk", trace_id="t",
                ))
            finally:
                self.dispatcher._resolve_agent = orig_resolve
            self.assertEqual(result.result.exit_code, -7)
            self.assertIn("drained", result.result.error)
        finally:
            self._restore_catalog_default()

    def test_health_gate_exception_pass_through(self):
        """健康门禁抛异常时降级为 pass-through（不阻断分发）。"""
        self._patch_catalog_default()
        try:
            _register_agent(self.catalog, "claude", healthy=True,
                            adapter_type="cli", cli_path=sys.executable)

            # 让 AgentCatalog.default() 抛异常，模拟 catalog 不可用
            def _boom(*a, **k):
                raise RuntimeError("catalog down")
            AgentCatalog.default = classmethod(_boom)

            async def _fake_dispatch_impl(agent, task, **kwargs):
                from maop.core.reliability.error_schema import new_result
                from maop.delegate.models import DispatchResult
                r = new_result(agent=agent, task=task, exit_code=0, stdout="ok",
                               trace_id="t", routing_key="rk")
                return DispatchResult(result=r, breaker_tripped=False)

            with patch.object(self.dispatcher, "_dispatch_impl", new=_fake_dispatch_impl):
                result = asyncio.run(self.dispatcher.dispatch(
                    agent="claude", task="write code", trace_id="t", routing_key="rk",
                ))
            # 异常降级为 pass-through：正常分发
            self.assertEqual(result.result.exit_code, 0)
        finally:
            self._restore_catalog_default()

    def test_agent_not_in_catalog_pass_through(self):
        """catalog 中没有该 agent 时不拦截（pass-through）。"""
        self._patch_catalog_default()
        try:
            # 不注册 claude
            async def _fake_dispatch_impl(agent, task, **kwargs):
                from maop.core.reliability.error_schema import new_result
                from maop.delegate.models import DispatchResult
                r = new_result(agent=agent, task=task, exit_code=0, stdout="ok",
                               trace_id="t", routing_key="rk")
                return DispatchResult(result=r, breaker_tripped=False)

            with patch.object(self.dispatcher, "_dispatch_impl", new=_fake_dispatch_impl):
                result = asyncio.run(self.dispatcher.dispatch(
                    agent="claude", task="write code", trace_id="t", routing_key="rk",
                ))
            self.assertEqual(result.result.exit_code, 0)
        finally:
            self._restore_catalog_default()


# =====================================================================
# 4. DistributedScheduler 健康门禁
# =====================================================================

class TestDistributedSchedulerHealthGate(unittest.TestCase):
    def setUp(self):
        reset_agent_identity()
        self.redis = fakeredis.FakeRedis(decode_responses=True)
        self.registry = WorkerRegistry(redis_client=self.redis, heartbeat_timeout=15)
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp:
            self.db_path = Path(tmp.name)
        self.catalog = _make_catalog(self.db_path)

        # 构造调度器（传入 registry）
        self.scheduler = DistributedScheduler(
            redis_client=self.redis,
            registry=self.registry,
            max_retries=1,
        )
        # 绑定测试 catalog（替换 AgentCatalog.default）
        self._orig_default = AgentCatalog.default
        AgentCatalog.default = classmethod(lambda cls: self.catalog)

    def tearDown(self):
        AgentCatalog.default = self._orig_default
        reset_agent_identity()
        try:
            os.unlink(self.db_path)
        except OSError:
            pass

    def _register_worker(self, worker_id: str, capabilities: set[str] | None = None):
        self.registry.register(worker_id=worker_id,
                               host="localhost",
                               capabilities=capabilities or set())

    def _stream_last_entry(self) -> dict[str, str]:
        """读取任务 stream 最后一条 entry 的字段（dict）。"""
        entries = self.redis.xrevrange(self.scheduler._task_stream, count=1)
        if not entries:
            return {}
        _, fields = entries[0]
        return {k: v for k, v in fields.items()}

    def test_healthy_worker_dispatched(self):
        """healthy agent 对应的 worker 正常入队（stream entry 带 worker_id）。"""
        _register_agent(self.catalog, "agent-a", healthy=True,
                        adapter_type="cli", cli_path=sys.executable)
        from maop.core.agent.identity import get_agent_identity
        identity = get_agent_identity()
        identity.register_mapping("agent-a", card_id="card-a", worker_id="worker-a")

        self._register_worker("worker-a", capabilities={"code_generation"})

        node = _NodeSpec(
            id="node-1",
            affinity=NodeAffinity(required={"code_generation"}),
        )
        msg_id = asyncio.run(self.scheduler._dispatch_node(
            run_id="run-1", layer_idx=0, node=node,
        ))
        self.assertIsNotNone(msg_id)
        self.assertIsInstance(msg_id, str)
        entry = self._stream_last_entry()
        self.assertEqual(entry.get("worker_id"), "worker-a")

    def test_unhealthy_worker_filtered(self):
        """unhealthy agent 对应的 worker 被过滤（stream entry 不带 worker_id）。"""
        _register_agent(self.catalog, "agent-a", healthy=False,
                        adapter_type="cli", cli_path=sys.executable)
        identity = get_agent_identity()
        identity.register_mapping("agent-a", card_id="card-a", worker_id="worker-a")

        self._register_worker("worker-a", capabilities={"code_generation"})

        node = _NodeSpec(
            id="node-1",
            affinity=NodeAffinity(required={"code_generation"}),
        )
        msg_id = asyncio.run(self.scheduler._dispatch_node(
            run_id="run-1", layer_idx=0, node=node,
        ))
        # xadd 仍会返回 msg_id，但 entry 不应带 worker_id（未选中）
        self.assertIsNotNone(msg_id)
        entry = self._stream_last_entry()
        self.assertNotIn("worker_id", entry)

    def test_drained_worker_filtered(self):
        """drained agent (weight=0) 对应的 worker 被过滤（entry 不带 worker_id）。"""
        _register_agent(self.catalog, "agent-a", healthy=True,
                        adapter_type="cli", cli_path=sys.executable)
        identity = get_agent_identity()
        identity.register_mapping("agent-a", card_id="card-a", worker_id="worker-a")

        from maop.core.scheduling.failure_detector import _AgentState
        detector = FailurePatternDetector()
        with detector._lock:
            state = detector._agents.setdefault("agent-a", _AgentState())
            state.drained = True
            state.weight = 0.0
        self.scheduler._failure_detector = detector

        self._register_worker("worker-a", capabilities={"code_generation"})

        node = _NodeSpec(
            id="node-1",
            affinity=NodeAffinity(required={"code_generation"}),
        )
        msg_id = asyncio.run(self.scheduler._dispatch_node(
            run_id="run-1", layer_idx=0, node=node,
        ))
        self.assertIsNotNone(msg_id)
        entry = self._stream_last_entry()
        self.assertNotIn("worker_id", entry)

    def test_unmapped_worker_pass_through(self):
        """没有 identity 映射的 worker 保守放行（entry 带 worker_id）。"""
        # 不注册任何 identity 映射
        self._register_worker("worker-a", capabilities={"code_generation"})

        node = _NodeSpec(
            id="node-1",
            affinity=NodeAffinity(required={"code_generation"}),
        )
        msg_id = asyncio.run(self.scheduler._dispatch_node(
            run_id="run-1", layer_idx=0, node=node,
        ))
        self.assertIsNotNone(msg_id)
        entry = self._stream_last_entry()
        self.assertEqual(entry.get("worker_id"), "worker-a")

    def test_no_capable_worker(self):
        """没有 capable worker 时不入队（entry 不带 worker_id）。"""
        _register_agent(self.catalog, "agent-a", healthy=True,
                        adapter_type="cli", cli_path=sys.executable)
        identity = get_agent_identity()
        identity.register_mapping("agent-a", card_id="card-a", worker_id="worker-a")

        # worker 没有需要的 capability
        self._register_worker("worker-a", capabilities={"other_cap"})

        node = _NodeSpec(
            id="node-1",
            affinity=NodeAffinity(required={"code_generation"}),
        )
        msg_id = asyncio.run(self.scheduler._dispatch_node(
            run_id="run-1", layer_idx=0, node=node,
        ))
        self.assertIsNotNone(msg_id)
        entry = self._stream_last_entry()
        self.assertNotIn("worker_id", entry)


# =====================================================================
# 5. CLI/API 完好性（现有入口不破裂）
# =====================================================================

class TestCLIAPIEntryPoints(unittest.TestCase):
    def setUp(self):
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp:
            self.db_path = Path(tmp.name)

    def tearDown(self):
        try:
            from maop.core.agent.registry.agent_catalog import reset_catalog as _rc
            _rc()
        except Exception:
            pass
        try:
            os.unlink(self.db_path)
        except OSError:
            pass

    def test_catalog_register_get_list(self):
        """AgentCatalog register/get/list_enabled 基础 API 完好。"""
        catalog = _make_catalog(self.db_path)
        _register_agent(catalog, "agent-x", healthy=True,
                        adapter_type="cli", cli_path=sys.executable)

        got = catalog.get("agent-x")
        self.assertIsNotNone(got)
        self.assertEqual(got.name, "agent-x")
        self.assertTrue(got.healthy)

        enabled = catalog.list_enabled()
        self.assertEqual(len(enabled), 1)
        self.assertEqual(enabled[0].name, "agent-x")

        # update_health
        catalog.update_health("agent-x", False)
        self.assertFalse(catalog.get("agent-x").healthy)

    def test_catalog_count_healthy(self):
        """count / count_healthy 正确。"""
        catalog = _make_catalog(self.db_path)
        _register_agent(catalog, "a", healthy=True)
        _register_agent(catalog, "b", healthy=False)
        _register_agent(catalog, "c", healthy=True)

        self.assertEqual(catalog.count(), 3)
        self.assertEqual(catalog.count_healthy(), 2)

    def test_health_service_start_stop_idempotent(self):
        """HealthService start/stop 幂等，不抛异常。"""
        catalog = _make_catalog(self.db_path)
        svc = HealthService(catalog=catalog, interval_s=0.1)
        asyncio.run(svc.start())
        self.assertTrue(svc._running)
        asyncio.run(svc.start())  # 再次 start 无-op
        asyncio.run(svc.stop())
        self.assertFalse(svc._running)
        asyncio.run(svc.stop())  # 再次 stop 无-op


if __name__ == "__main__":
    unittest.main(verbosity=2)
