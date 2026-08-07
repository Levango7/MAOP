"""MAOP Regression & Simulation Testing Framework.

Provides:
  1. RegressionTestRunner: Run test suites against model/prompt changes
  2. PersonaSimulator: Persona-driven multi-turn conversation simulation
  3. RegressionReport: Compare results between baseline and candidate

Usage::

    from maop.core.evolution.regression import RegressionTestRunner, PersonaSimulator

    # Regression testing
    runner = RegressionTestRunner(root_dir="/path/to/MAOP")
    report = await runner.run_suite("baseline", "candidate")

    # Simulation testing
    sim = PersonaSimulator(persona="junior_dev", goal="fix null pointer")
    result = await sim.run_turns(max_turns=5)
"""
from __future__ import annotations

import logging
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, ClassVar

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


class TestCase(BaseModel):
    id: str = Field(default_factory=lambda: uuid.uuid4().hex[:12])
    name: str = ""
    prompt: str = ""
    expected_keywords: list[str] = Field(default_factory=list)
    expected_exit_code: int = 0
    max_duration_ms: int = 30000
    agent: str = ""
    category: str = "general"


class TestResult(BaseModel):
    test_id: str = ""
    test_name: str = ""
    passed: bool = False
    actual_exit_code: int = -1
    actual_output: str = ""
    duration_ms: int = 0
    keyword_matches: list[str] = Field(default_factory=list)
    keyword_misses: list[str] = Field(default_factory=list)
    error: str = ""


class RegressionReport(BaseModel):
    baseline_label: str = ""
    candidate_label: str = ""
    timestamp: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    total_tests: int = 0
    baseline_passed: int = 0
    candidate_passed: int = 0
    regressions: list[str] = Field(default_factory=list)
    improvements: list[str] = Field(default_factory=list)
    details: list[dict[str, Any]] = Field(default_factory=list)


class PersonaConfig(BaseModel):
    name: str = "default"
    role: str = "user"
    expertise: str = "intermediate"
    communication_style: str = "direct"
    goals: list[str] = Field(default_factory=list)
    constraints: list[str] = Field(default_factory=list)


class SimulationTurn(BaseModel):
    turn_number: int = 0
    persona_input: str = ""
    agent_response: str = ""
    latency_ms: int = 0
    satisfaction_score: float = 0.0


class SimulationResult(BaseModel):
    simulation_id: str = Field(default_factory=lambda: uuid.uuid4().hex[:12])
    persona: PersonaConfig = Field(default_factory=PersonaConfig)
    turns: list[SimulationTurn] = Field(default_factory=list)
    total_duration_ms: int = 0
    overall_satisfaction: float = 0.0
    goal_achieved: bool = False
    summary: str = ""


class RegressionTestRunner:
    """Run regression test suites and compare baseline vs candidate results."""

    def __init__(self, root_dir: str | Path | None = None) -> None:
        self._root = Path(root_dir) if root_dir else Path(".")
        self._results_dir = self._root / "data" / "regression"
        self._results_dir.mkdir(parents=True, exist_ok=True)

    async def run_test(self, test: TestCase, *, dispatcher: Any = None) -> TestResult:
        start = time.monotonic()
        result = TestResult(test_id=test.id, test_name=test.name or test.prompt[:50])

        if dispatcher is None:
            try:
                from maop.core.reliability.services import ServiceContainer
                svc = ServiceContainer(root_dir=self._root)
                dispatcher = svc.get("dispatcher", raise_on_failure=False)
            except Exception:
                logger.debug("Silent exception in core/regression.py:113", exc_info=True)

        if dispatcher is None:
            result.error = "No dispatcher available"
            result.duration_ms = int((time.monotonic() - start) * 1000)
            return result

        try:
            from maop.delegate.models import DispatchResult
            dispatch_result: DispatchResult = await dispatcher.dispatch(
                agent=test.agent or "default",
                task=test.prompt,
                timeout_seconds=test.max_duration_ms // 1000,
            )
            maop_result = dispatch_result.result
            result.actual_exit_code = maop_result.exit_code
            result.actual_output = (maop_result.stdout or "")[:2000]
            result.passed = maop_result.exit_code == test.expected_exit_code

            output_lower = result.actual_output.lower()
            for kw in test.expected_keywords:
                if kw.lower() in output_lower:
                    result.keyword_matches.append(kw)
                else:
                    result.keyword_misses.append(kw)

            if result.keyword_misses:
                result.passed = False

        except Exception as exc:
            result.error = str(exc)
            result.passed = False

        result.duration_ms = int((time.monotonic() - start) * 1000)
        return result

    async def run_suite(self, tests: list[TestCase], *, dispatcher: Any = None) -> list[TestResult]:
        results = []
        for test in tests:
            r = await self.run_test(test, dispatcher=dispatcher)
            results.append(r)
        return results

    async def compare(
        self,
        tests: list[TestCase],
        baseline_dispatcher: Any = None,
        candidate_dispatcher: Any = None,
        baseline_label: str = "baseline",
        candidate_label: str = "candidate",
    ) -> RegressionReport:
        baseline_results = await self.run_suite(tests, dispatcher=baseline_dispatcher)
        candidate_results = await self.run_suite(tests, dispatcher=candidate_dispatcher)

        report = RegressionReport(
            baseline_label=baseline_label,
            candidate_label=candidate_label,
            total_tests=len(tests),
        )

        for br, cr in zip(baseline_results, candidate_results):
            if br.passed:
                report.baseline_passed += 1
            if cr.passed:
                report.candidate_passed += 1

            test_name = br.test_name or br.test_id
            if br.passed and not cr.passed:
                report.regressions.append(test_name)
            elif not br.passed and cr.passed:
                report.improvements.append(test_name)

            report.details.append({
                "test": test_name,
                "baseline_passed": br.passed,
                "candidate_passed": cr.passed,
                "baseline_output": br.actual_output[:200],
                "candidate_output": cr.actual_output[:200],
            })

        report_path = self._results_dir / f"regression-{int(time.time())}.json"
        report_path.write_text(report.model_dump_json(indent=2), encoding="utf-8")
        return report


class PersonaSimulator:
    """Simulate multi-turn conversations with a specific persona.

    Generates realistic user inputs based on persona configuration
    and evaluates agent responses for satisfaction and goal achievement.
    """

    PERSONA_TEMPLATES: ClassVar[dict[str, dict[str, str | list[str]]]] = {
        "junior_dev": {
            "role": "Junior developer",
            "expertise": "beginner",
            "communication_style": "uncertain, asks many questions",
            "goals": ["fix bugs", "understand code", "learn patterns"],
            "constraints": ["no architectural decisions", "needs guidance"],
        },
        "senior_dev": {
            "role": "Senior developer",
            "expertise": "expert",
            "communication_style": "precise, technical, expects efficiency",
            "goals": ["refactor", "optimize", "design patterns"],
            "constraints": ["expects high quality", "no hand-holding"],
        },
        "pm": {
            "role": "Product manager",
            "expertise": "business",
            "communication_style": "high-level, feature-focused",
            "goals": ["ship features", "meet deadlines", "user satisfaction"],
            "constraints": ["non-technical language", "deadline-driven"],
        },
        "qa_engineer": {
            "role": "QA engineer",
            "expertise": "testing",
            "communication_style": "detail-oriented, edge-case focused",
            "goals": ["find bugs", "verify behavior", "coverage"],
            "constraints": ["systematic approach", "reproducibility"],
        },
    }

    def __init__(self, persona: str | PersonaConfig = "junior_dev", goal: str = "") -> None:
        if isinstance(persona, str):
            template = self.PERSONA_TEMPLATES.get(persona, self.PERSONA_TEMPLATES["junior_dev"])
            self._persona = PersonaConfig(
                name=persona,
                role=str(template["role"]),
                expertise=str(template["expertise"]),
                communication_style=str(template["communication_style"]),
                goals=list(template["goals"]) if isinstance(template["goals"], list) else [template["goals"]],
                constraints=list(template["constraints"]) if isinstance(template["constraints"], list) else [template["constraints"]],
            )
        else:
            self._persona = persona

        self._goal = goal
        self._turns: list[SimulationTurn] = []

    @property
    def persona(self) -> PersonaConfig:
        return self._persona

    def generate_input(self, turn_number: int, last_response: str = "") -> str:
        if turn_number == 1:
            if self._goal:
                return self._goal
            return f"I need help with {self._persona.goals[0] if self._persona.goals else 'a task'}"

        if last_response:
            if self._persona.expertise == "beginner":
                return f"Can you explain that more? I'm not sure I understand: {last_response[:50]}..."
            if self._persona.expertise == "expert":
                return "Thanks. Can you also handle the edge case where the input is empty?"
            return "That helps. What about the next step?"

        return "Can you continue?"

    def evaluate_satisfaction(self, response: str) -> float:
        score = 0.5
        if len(response) > 50:
            score += 0.1
        if any(kw in response.lower() for kw in ["error", "fail", "bug"]):
            score -= 0.1
        if any(kw in response.lower() for kw in ["success", "done", "complete", "fixed"]):
            score += 0.2
        if any(kw in response.lower() for kw in ["explain", "here's how", "step"]):
            score += 0.1
        return max(0.0, min(1.0, score))

    async def run_turns(
        self,
        max_turns: int = 5,
        *,
        dispatcher: Any = None,
        agent: str = "default",
    ) -> SimulationResult:
        result = SimulationResult(persona=self._persona)
        start = time.monotonic()
        last_response = ""

        for i in range(1, max_turns + 1):
            user_input = self.generate_input(i, last_response)
            turn_start = time.monotonic()
            agent_response = ""

            if dispatcher:
                try:
                    from maop.delegate.models import DispatchResult
                    dr: DispatchResult = await dispatcher.dispatch(
                        agent=agent, task=user_input, timeout_seconds=30,
                    )
                    agent_response = dr.result.stdout or dr.result.error or ""
                except Exception as exc:
                    agent_response = f"[Error: {exc}]"
            else:
                agent_response = f"[Simulated response to: {user_input[:50]}]"

            latency = int((time.monotonic() - turn_start) * 1000)
            satisfaction = self.evaluate_satisfaction(agent_response)

            turn = SimulationTurn(
                turn_number=i,
                persona_input=user_input,
                agent_response=agent_response[:500],
                latency_ms=latency,
                satisfaction_score=satisfaction,
            )
            result.turns.append(turn)
            last_response = agent_response

            if satisfaction >= 0.8 and i >= 2:
                result.goal_achieved = True
                break

        result.total_duration_ms = int((time.monotonic() - start) * 1000)
        if result.turns:
            result.overall_satisfaction = sum(t.satisfaction_score for t in result.turns) / len(result.turns)
        result.summary = (
            f"Persona={self._persona.name} | Turns={len(result.turns)} | "
            f"Satisfaction={result.overall_satisfaction:.2f} | Goal={'achieved' if result.goal_achieved else 'not achieved'}"
        )
        return result
