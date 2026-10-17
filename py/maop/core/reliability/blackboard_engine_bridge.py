"""MAOP Blackboard-Distributed Engine Bridge.

Bridges the Blackboard architecture with the M0 distributed execution
backend. Provides:

1. ``DistributedTaskKS`` — a KnowledgeSource that reads task definitions
   from the ``problem`` domain and writes back results to
   ``partial_solution`` / ``evidence`` domains via the
   ``TaskBackend`` abstraction.

2. ``BlackboardDistributedRunner`` — high-level orchestrator that wires
   a Blackboard + Controller + Distributed Engine together, allowing
   multi-step DAGs to be declared on the blackboard and executed
   across the distributed worker pool.

This enables the pattern::

    # Agent writes problem to blackboard
    await bb.write("problem", {"steps": [...]}, contributor="planner")

    # Controller dispatches DistributedTaskKS which:
    #   1. Reads steps from blackboard
    #   2. Runs them via Engine.run(distributed=True)
    #   3. Writes results back to blackboard
    #   4. Triggers downstream KnowledgeSources

Usage::

    from maop.core.reliability.blackboard_engine_bridge import (
        BlackboardDistributedRunner,
    )

    runner = BlackboardDistributedRunner(redis_client, step_executor=my_exec)
    await runner.start()
    # ... write problems to the shared blackboard ...
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from typing import Any

from maop.core.reliability.blackboard import (
    Blackboard,
    BlackboardDomain,
    BlackboardEntry,
    Controller,
    KnowledgeSource,
)
from maop.engine import Engine, WorkflowStep

logger = logging.getLogger(__name__)


class DistributedTaskKS(KnowledgeSource):
    """KnowledgeSource that executes distributed tasks from blackboard entries.

    Triggered by entries written to the ``problem`` domain. Each entry's
    ``content`` should be a dict with a ``steps`` key containing a list
    of step definitions::

        {
            "steps": [
                {"id": "step-1", "type": "agent", "task": "..."},
                {"id": "step-2", "type": "agent", "task": "...", "depends_on": ["step-1"]},
            ]
        }

    On execution:
    1. Converts step dicts to ``WorkflowStep`` objects
    2. Runs via ``Engine.run(distributed=True)``
    3. Writes results to ``partial_solution`` domain
    4. Writes evidence (success/failure) to ``evidence`` domain

    Parameters
    ----------
    engine : Engine
        The workflow engine instance (configured with Redis for distributed mode).
    name : str
        KnowledgeSource name (default: "distributed-task-ks").
    """

    name = "distributed-task-ks"
    priority = 10
    read_domains = (BlackboardDomain.PROBLEM.value,)  # type: ignore[assignment]
    write_domains = (
        BlackboardDomain.PARTIAL_SOLUTION.value,
        BlackboardDomain.EVIDENCE.value,
    )

    def __init__(self, engine: Engine, *, name: str | None = None) -> None:
        self._engine = engine
        if name:
            self.name = name

    async def execute(self, bb: Blackboard, trigger_entry: BlackboardEntry) -> None:
        """Execute the distributed task defined in the trigger entry."""
        content = trigger_entry.content
        if not isinstance(content, dict):
            logger.warning(
                "[bb-bridge] trigger entry content is not a dict: %s",
                type(content).__name__,
            )
            return

        raw_steps = content.get("steps", [])
        if not raw_steps:
            logger.warning("[bb-bridge] trigger entry has no 'steps'")
            return

        # Convert step dicts to WorkflowStep
        steps = self._build_steps(raw_steps)
        if not steps:
            return

        # Generate a trace/run id
        run_id = content.get("run_id") or f"bb-run-{uuid.uuid4().hex[:12]}"

        logger.info(
            "[bb-bridge] KS '%s' executing %d steps (run=%s)",
            self.name, len(steps), run_id,
        )

        # Execute via distributed engine
        try:
            result = await self._engine.run(
                steps=steps,
                context=content.get("context"),
                workdir=content.get("workdir", ""),
                trace_id=run_id,
                distributed=True,
            )
        except Exception as exc:
            logger.error("[bb-bridge] KS '%s' execution failed: %s", self.name, exc)
            await bb.write(
                BlackboardDomain.EVIDENCE.value,
                {"run_id": run_id, "status": "failed", "error": str(exc)},
                contributor=self.name,
                confidence=0.9,
            )
            return

        # Write results to blackboard
        step_results = [
            {
                "id": s.id,
                "status": s.status.value if hasattr(s.status, "value") else str(s.status),
                "output": s.output,
                "duration_ms": s.duration_ms,
            }
            for s in result.steps
        ]

        await bb.write(
            BlackboardDomain.PARTIAL_SOLUTION.value,
            {
                "run_id": run_id,
                "status": "completed" if result.success else "failed",
                "steps": step_results,
                "total_duration_ms": result.total_duration_ms,
                "source_entry_id": trigger_entry.entry_id,
            },
            contributor=self.name,
            confidence=1.0 if result.success else 0.5,
        )

        await bb.write(
            BlackboardDomain.EVIDENCE.value,
            {
                "run_id": run_id,
                "success": result.success,
                "step_count": len(result.steps),
                "source_entry_id": trigger_entry.entry_id,
            },
            contributor=self.name,
            confidence=1.0,
        )

        logger.info(
            "[bb-bridge] KS '%s' completed run=%s (success=%s, %d steps)",
            self.name, run_id, result.success, len(result.steps),
        )

    @staticmethod
    def _build_steps(raw_steps: list[dict[str, Any]]) -> list[WorkflowStep]:
        """Convert raw step dicts to WorkflowStep objects."""
        steps = []
        for raw in raw_steps:
            sid = raw.get("id", "")
            if not sid:
                continue
            step_kwargs: dict[str, Any] = {
                "id": sid,
                "type": raw.get("type", "agent"),
                "task": raw.get("task", ""),
            }
            if "depends_on" in raw:
                step_kwargs["depends_on"] = raw["depends_on"]
            if "priority" in raw:
                step_kwargs["priority"] = raw["priority"]
            if "workdir" in raw:
                step_kwargs["workdir"] = raw["workdir"]
            steps.append(WorkflowStep(**step_kwargs))
        return steps


class BlackboardDistributedRunner:
    """Orchestrator that wires Blackboard + Controller + Distributed Engine.

    Provides a simplified API for multi-agent coordination via the
    blackboard pattern with distributed execution.

    Usage::

        runner = BlackboardDistributedRunner(redis_client)
        await runner.start()

        # Dispatch a problem
        await runner.submit_problem(
            "analyze-code",
            steps=[{"id": "s1", "task": "Find bugs"}, {"id": "s2", "task": "Fix them", "depends_on": ["s1"]}],
        )

        # Run control loop for N iterations
        await runner.run_control_loop(max_iterations=10)

        # Read results
        solutions = runner.blackboard.read("partial_solution")
    """

    def __init__(
        self,
        redis_client: Any = None,
        *,
        engine: Engine | None = None,
        step_executor: Any = None,
        bb: Blackboard | None = None,
        controller: Controller | None = None,
        event_bus: Any = None,
    ) -> None:
        self._redis = redis_client
        self._engine = engine
        self._step_executor = step_executor
        self._bb = bb
        self._controller = controller
        self._event_bus = event_bus
        self._ks: DistributedTaskKS | None = None

    @property
    def blackboard(self) -> Blackboard:
        if self._bb is None:
            if self._event_bus:
                self._bb = Blackboard(self._event_bus)
                self._bb.enable_event_bus(self._event_bus)
            else:
                self._bb = Blackboard()
        return self._bb

    @property
    def controller(self) -> Controller:
        if self._controller is None:
            self._controller = Controller(self.blackboard, self._event_bus)
        return self._controller

    async def start(self) -> None:
        """Initialize the runner (create engine, register KS, start controller)."""
        if self._engine is None:
            self._engine = Engine(
                redis_client=self._redis,
                step_executor=self._step_executor,
            )
        self._ks = DistributedTaskKS(self._engine)
        self.controller.register_ks(self._ks)
        await self.controller.start()
        logger.info("[bb-runner] started with KS: %s", self._ks.name)

    async def stop(self) -> None:
        """Stop the controller."""
        await self.controller.stop()

    async def submit_problem(
        self,
        run_id: str,
        steps: list[dict[str, Any]],
        *,
        context: dict[str, Any] | None = None,
        workdir: str = "",
        contributor: str = "user",
    ) -> BlackboardEntry:
        """Submit a problem (steps) to the blackboard for distributed execution.

        This writes to the ``problem`` domain which triggers the
        ``DistributedTaskKS``.
        """
        content = {
            "run_id": run_id,
            "steps": steps,
            "context": context or {},
            "workdir": workdir,
        }
        entry = await self.blackboard.write(
            BlackboardDomain.PROBLEM.value,
            content,
            contributor=contributor,
            confidence=1.0,
        )
        logger.info("[bb-runner] submitted problem '%s' (%d steps)", run_id, len(steps))
        return entry

    async def run_control_loop(self, max_iterations: int = 100) -> int:
        """Run the control loop for up to ``max_iterations`` steps.

        Returns the total number of KS executions performed.
        """
        total = 0
        for _ in range(max_iterations):
            executed = await self.controller.control_step()
            total += executed
            if self.controller.is_converged():
                break
            await asyncio.sleep(0.05)  # yield to let async tasks progress
        return total

    def get_results(self) -> list[BlackboardEntry]:
        """Read all partial solutions from the blackboard."""
        return self.blackboard.read(BlackboardDomain.PARTIAL_SOLUTION.value)

    def get_evidence(self) -> list[BlackboardEntry]:
        """Read all evidence entries from the blackboard."""
        return self.blackboard.read(BlackboardDomain.EVIDENCE.value)
