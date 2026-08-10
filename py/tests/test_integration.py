"""Integration tests — Cross-module workflows for MAOP Python package."""

import asyncio
import sqlite3
import tempfile
from pathlib import Path

from maop.concurrency import Priority, Task, TaskPool
from maop.config.loader import AgentDef, MaopConfig
from maop.core.reliability.circuit_breaker import CircuitBreaker
from maop.core.reliability.error_schema import new_result
from maop.core.reliability.event_bus import Event, EventBus
from maop.dashboard import DashboardProvider
from maop.delegate.dispatcher import Dispatcher
from maop.engine import Engine, StepType, WorkflowStep
from maop.evolve import EvolveEngine
from maop.maop_plan import maop_plan
from maop.maop_verify import VerifyEngine
from maop.memory.store import MemoryStore

# ═══════════════════════════════════════════════════════════════
# Plan -> Verify workflow
# ═══════════════════════════════════════════════════════════════

class TestPlanVerifyWorkflow:
    """Test Plan -> Execute -> Verify end-to-end."""

    def test_plan_routes_correctly(self):
        plan = maop_plan(task="fix the authentication bug")
        # v5.0.0: routing depends on config routing; verify plan is well-formed
        assert plan.routing_key
        assert plan.selected_agent

    def test_verify_checks_result(self):
        plan = maop_plan(task="write a function")
        result = new_result(agent="codex", task="write a function", exit_code=0, stdout="def foo(): pass")
        engine = VerifyEngine()
        vr = engine.verify(plan=plan.model_dump(), result=result)
        assert vr.passed

    def test_verify_catches_failure(self):
        plan = maop_plan(task="deploy service")
        result = new_result(agent="codex", task="deploy", exit_code=1, error="deploy failed")
        engine = VerifyEngine()
        vr = engine.verify(plan=plan.model_dump(), result=result)
        assert not vr.passed


# ═══════════════════════════════════════════════════════════════
# Engine DAG workflow
# ═══════════════════════════════════════════════════════════════

class TestEngineDAGWorkflow:
    """Test multi-step DAG execution."""

    def test_codegen_pipeline(self):
        steps = [
            WorkflowStep(id="codegen", type=StepType.AGENT, agent="codex", task="write code"),
            WorkflowStep(id="verify", type=StepType.VERIFY, depends_on=["codegen"]),
            WorkflowStep(id="done", type=StepType.TERMINAL, depends_on=["verify"]),
        ]
        engine = Engine()
        result = asyncio.run(engine.run(steps, context={"task": "refactor"}))
        assert result.success
        assert len(result.steps) == 3

    def test_parallel_agents(self):
        steps = [
            WorkflowStep(id="agent1", type=StepType.AGENT, agent="claude", task="task1"),
            WorkflowStep(id="agent2", type=StepType.AGENT, agent="codex", task="task2"),
            WorkflowStep(id="merge", type=StepType.TERMINAL, depends_on=["agent1", "agent2"]),
        ]
        engine = Engine()
        result = asyncio.run(engine.run(steps))
        assert result.success


# ═══════════════════════════════════════════════════════════════
# Circuit Breaker + Dispatcher integration
# ═══════════════════════════════════════════════════════════════

class TestBreakerDispatcherIntegration:
    """Test circuit breaker blocks dispatching."""

    def test_breaker_blocks_after_threshold(self):
        with tempfile.TemporaryDirectory() as tmp:
            breaker = CircuitBreaker(Path(tmp) / "breaker.json")
            # Trip the breaker
            for _ in range(3):
                breaker.record_failure("failing-agent")

            assert not breaker.is_available("failing-agent")

            # Dispatcher should reject
            config = MaopConfig(
                agents={"failing-agent": AgentDef(cli="echo", driver="cli")},
            )
            dispatcher = Dispatcher(MAOP_config=config, breaker=breaker)
            result = asyncio.run(
                dispatcher.dispatch(agent="failing-agent", task="test")
            )
            assert result.breaker_tripped


# ═══════════════════════════════════════════════════════════════
# Memory + Evolve integration
# ═══════════════════════════════════════════════════════════════

class TestMemoryEvolveIntegration:
    """Test memory store feeds evolution analysis."""

    def test_store_then_evolve(self):
        with tempfile.TemporaryDirectory() as tmp:
            # Store a memory entry
            store = MemoryStore(root_dir=Path(tmp))
            store.store(agent="claude", task="fix bug", content="fixed", tags=["debug"])

            # Evolve should see delegation data
            evolve = EvolveEngine(root_dir=tmp)
            result = evolve.analyze()
            assert result.action == "analyze"


# ═══════════════════════════════════════════════════════════════
# Event Bus cross-module
# ═══════════════════════════════════════════════════════════════

class TestEventBusIntegration:
    """Test event bus works across modules."""

    def test_emit_and_subscribe(self):
        bus = EventBus()
        received = []

        async def handler(event: Event):
            received.append(event)

        bus.subscribe("test.topic", handler)

        async def _test():
            await bus.publish(Event(topic="test.topic", data={"key": "value"}))

        asyncio.run(_test())
        assert len(received) == 1
        assert received[0].data["key"] == "value"


# ═══════════════════════════════════════════════════════════════
# TaskPool concurrent execution
# ═══════════════════════════════════════════════════════════════

class TestTaskPoolIntegration:
    """Test task pool with real async work."""

    def test_concurrent_delegations(self):
        async def _test():
            pool = TaskPool(max_workers=3)
            await pool.start()

            results = []

            async def work(task):
                await asyncio.sleep(0.05)
                results.append(task.name)
                return f"done-{task.name}"

            ids = []
            for i in range(5):
                t = Task(name=f"job{i}", priority=Priority.NORMAL)
                tid = await pool.submit(t, work)
                ids.append(tid)

            for tid in ids:
                await pool.wait(tid, timeout=5)

            assert len(results) == 5
            await pool.stop()

        asyncio.run(_test())


# ═══════════════════════════════════════════════════════════════
# Dashboard integration
# ═══════════════════════════════════════════════════════════════

class TestDashboardIntegration:
    """Test dashboard reads from all subsystems."""

    def test_dashboard_reads_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            # Set up maop.db with delegation data (provider reads from SQLite)
            data_dir = Path(tmp) / "data"
            data_dir.mkdir()
            MAOP_db = data_dir / "maop.db"
            conn = sqlite3.connect(str(MAOP_db))
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS delegations (
                    agent TEXT, task TEXT, exit_code INTEGER,
                    duration_ms INTEGER, timestamp TEXT, error TEXT
                );
                INSERT INTO delegations VALUES
                    ('claude', 'fix bug', 0, 1500, '2026-07-15T10:00:00+00:00', NULL);
            """)
            conn.commit()
            conn.close()

            provider = DashboardProvider(root_dir=tmp)
            state = provider.get_state()
            assert state.total_delegations == 1
            assert state.success_rate == 100.0


# ═══════════════════════════════════════════════════════════════
# All modules importable
# ═══════════════════════════════════════════════════════════════

class TestModuleImports:
    """Verify all MAOP modules are importable."""

    def test_core_modules(self):
        from maop.core import error_schema
        assert error_schema is not None

    def test_config_modules(self):
        from maop.config import loader
        assert loader is not None

    def test_delegate_modules(self):
        from maop.delegate import dispatcher
        assert dispatcher is not None

    def test_memory_modules(self):
        from maop.memory import store
        assert store is not None

    def test_loop_modules(self):
        from maop import maop_loop
        assert maop_loop is not None

    def test_engine_module(self):
        from maop import engine
        assert engine is not None

    def test_evolve_module(self):
        from maop import evolve
        assert evolve is not None

    def test_concurrency_module(self):
        from maop import concurrency
        assert concurrency is not None

    def test_dashboard_module(self):
        from maop import dashboard
        assert dashboard is not None
