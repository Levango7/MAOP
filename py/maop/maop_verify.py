"""MAOP Verify — Post-execution verification engine.

Execution result verification and quality checks.: checks exit_code, output quality,
content safety, and custom gates. Returns structured VerifyResult.
"""

from __future__ import annotations

import logging
import re
from typing import Any, Literal

from pydantic import BaseModel, Field

from maop.core.error_schema import MaopResult
from maop.core.state_classifier import TaskStateClassifier, ClassificationResult

logger = logging.getLogger(__name__)


class GateResult(BaseModel):
    """Result of a single verification gate."""
    name: str
    passed: bool
    reason: str = ""


# Task state type alias
TaskStateLiteral = Literal["done", "blocked", "working", "failed"]


class VerifyResult(BaseModel):
    """Result of the Verify phase."""
    phase: str = "verify"
    passed: bool = False
    summary: str = ""
    gates: list[GateResult] = Field(default_factory=list)
    feedback: str = ""  # Suggested fix when failed
    # ── State classification (Claude Code-inspired) ──
    state: TaskStateLiteral = "working"
    block_reason: str = ""  # Populated when state == "blocked"
    classification: ClassificationResult | None = None


# ── Built-in gates ────────────────────────────────────────────

def _gate_exit_code(plan: dict, result: MaopResult | None) -> GateResult:
    """Check that exit code is 0."""
    if result is None:
        return GateResult(name="exit_code", passed=False, reason="No execution result")
    if result.exit_code == 0:
        return GateResult(name="exit_code", passed=True)
    return GateResult(name="exit_code", passed=False, reason=f"exit_code={result.exit_code}")


def _gate_output(plan: dict, result: MaopResult | None) -> GateResult:
    """Check that output is non-empty."""
    if result is None:
        return GateResult(name="output", passed=False, reason="No execution result")
    if result.stdout and result.stdout.strip():
        return GateResult(name="output", passed=True)
    return GateResult(name="output", passed=False, reason="Empty output")


def _gate_content_safety(plan: dict, result: MaopResult | None) -> GateResult:
    """Basic content safety check — no secrets/keys leaked."""
    if result is None or not result.stdout:
        return GateResult(name="content-safety", passed=True)

    output = result.stdout
    # Patterns that suggest leaked secrets
    dangerous_patterns = [
        r'(?:api[_-]?key|secret|token|password|credential)\s*[=:]\s*["\']?[A-Za-z0-9_\-]{16,}',
        r'-----BEGIN (?:RSA |EC )?PRIVATE KEY-----',
        r'sk-[a-zA-Z0-9]{20,}',  # OpenAI-style keys
        r'ghp_[a-zA-Z0-9]{36}',   # GitHub PATs
        r'AKIA[A-Z0-9]{16}',      # AWS access keys
    ]
    for pattern in dangerous_patterns:
        if re.search(pattern, output, re.IGNORECASE):
            return GateResult(name="content-safety", passed=False,
                              reason="Potential secret/credential leaked in output")

    return GateResult(name="content-safety", passed=True)


def _gate_syntax_check(plan: dict, result: MaopResult | None) -> GateResult:
    """Check output doesn't contain obvious syntax errors."""
    if result is None or not result.stdout:
        return GateResult(name="syntax-check", passed=True)

    output = result.stdout
    # Common syntax error patterns
    error_patterns = [
        r'SyntaxError',
        r'IndentationError',
        r'ParseError',
        r'Unexpected token',
        r'unterminated string',
    ]
    for pattern in error_patterns:
        if re.search(pattern, output, re.IGNORECASE):
            return GateResult(name="syntax-check", passed=False,
                              reason=f"Syntax error detected: {pattern}")

    return GateResult(name="syntax-check", passed=True)


def _gate_lint(plan: dict, result: MaopResult | None) -> GateResult:
    """Check output doesn't contain lint errors (basic)."""
    if result is None or not result.stdout:
        return GateResult(name="lint", passed=True)

    output = result.stdout
    # Common lint error patterns
    lint_patterns = [
        r'\bE\d{3}\b',   # pycodestyle errors like E501
        r'\bF\d{3}\b',   # pyflakes errors like F841
        r'\bW\d{3}\b',   # warnings
    ]
    for pattern in lint_patterns:
        matches = re.findall(pattern, output)
        if matches:
            return GateResult(name="lint", passed=False,
                              reason=f"Lint issues found: {', '.join(matches[:5])}")

    return GateResult(name="lint", passed=True)


def _gate_dry_run(plan: dict, result: MaopResult | None) -> GateResult:
    """Dry-run gate — verifies that a dry-run was actually performed.

    .. note::
       **This is a contract-verification gate, not a dry-run executor.**
       The gate itself does NOT execute any dry-run command, run any
       subprocess, or invoke any LLM. It only inspects the ``plan`` and
       ``result`` data structures for dry-run signals that the *executor*
       (e.g. ``loop_executor``, ``dispatcher``) must have emitted. The
       real dry-run execution (if any) happens upstream — this gate just
       checks that the executor's output is self-consistent with the
       ``plan["dry_run"]`` declaration. Naming the function "_gate_dry_run"
       (rather than "_dry_run") is intentional: it is a gate on dry-run,
       not the dry-run itself. See ADR-013 (planned) for the rationale.

    Plan contract (all optional):
      ``plan["dry_run"]`` (bool):
          When True, the gate expects the executor to have performed a dry-run
          (no real side effects). The result must contain at least one signal
          confirming the dry-run path was taken.
      ``plan["expected_dry_run_artifacts"]`` (list[str]):
          When present, the gate additionally checks that each named artifact
          appears in ``result.structured_output["dry_run_artifacts"]``.

    Result signals (any one suffices when ``dry_run=True``):
      1. ``result.stdout`` contains "DRY-RUN" / "dry_run" / "dry-run" /
         "no changes applied" (case-insensitive).
      2. ``result.structured_output`` is a dict with key ``"dry_run"`` set
         to a truthy value.
      3. ``result.structured_output`` is a dict containing a
         ``"dry_run_artifacts"`` list.

    Behavior:
      - Plan with no dry_run declaration (default): PASS (backward compat
        with callers that never opted into dry-run).
      - Plan with ``dry_run=True`` but result is None: FAIL.
      - Plan with ``dry_run=True`` and result present but no dry-run signal:
        FAIL with reason explaining what was expected.
      - Plan with ``dry_run=True`` and ``expected_dry_run_artifacts`` set:
        PASS only if every named artifact appears in
        ``result.structured_output["dry_run_artifacts"]``.
    """
    dry_run_requested = bool(plan.get("dry_run", False))
    expected_artifacts = plan.get("expected_dry_run_artifacts") or []

    # Backward compat: plan never opted into dry-run → always pass.
    if not dry_run_requested:
        return GateResult(name="dry-run", passed=True)

    if result is None:
        return GateResult(
            name="dry-run", passed=False,
            reason="plan declared dry_run=True but no execution result was provided",
        )

    # Probe result for dry-run signals.
    has_stdout_signal = False
    if result.stdout:
        stdout_lower = result.stdout.lower()
        for marker in ("dry-run", "dry_run", "no changes applied"):
            if marker in stdout_lower:
                has_stdout_signal = True
                break

    has_structured_signal = False
    structured = result.structured_output
    if isinstance(structured, dict):
        if structured.get("dry_run"):
            has_structured_signal = True
        elif "dry_run_artifacts" in structured and isinstance(
            structured["dry_run_artifacts"], list
        ) and structured["dry_run_artifacts"]:
            has_structured_signal = True

    if not (has_stdout_signal or has_structured_signal):
        return GateResult(
            name="dry-run", passed=False,
            reason=(
                "plan declared dry_run=True but result contains no dry-run "
                "signal (expected 'DRY-RUN' marker in stdout or "
                "structured_output.dry_run / .dry_run_artifacts)"
            ),
        )

    # If expected_dry_run_artifacts were declared, verify each is present.
    if expected_artifacts:
        actual_artifacts: list[str] = []
        if isinstance(structured, dict):
            raw = structured.get("dry_run_artifacts")
            if isinstance(raw, list):
                actual_artifacts = [str(a) for a in raw]
        missing = [a for a in expected_artifacts if a not in actual_artifacts]
        if missing:
            return GateResult(
                name="dry-run", passed=False,
                reason=(
                    f"dry-run missing expected artifacts: {missing} "
                    f"(actual: {actual_artifacts})"
                ),
            )

    return GateResult(name="dry-run", passed=True)


# ── Gate registry ─────────────────────────────────────────────

def _gate_schema(plan: dict, result: MaopResult | None) -> GateResult:
    """Validate structured output against expected_schema from the plan.

    The plan may specify ``expected_schema`` as a JSON Schema dict.
    If ``result.structured_output`` is present, it is validated against
    the schema.  If absent, the stdout is parsed via OutputParser first.
    """
    expected = plan.get("expected_schema")
    if not expected:
        return GateResult(name="schema", passed=True)

    if result is None:
        return GateResult(name="schema", passed=False, reason="No execution result")

    data = result.structured_output
    if data is None and result.stdout:
        from maop.core.output_parser import OutputParser
        parser = OutputParser()
        pr = parser.extract_json(result.stdout)
        if pr.success:
            data = pr.data
        else:
            return GateResult(name="schema", passed=False, reason="No JSON in output to validate against schema")

    if data is None:
        return GateResult(name="schema", passed=False, reason="No structured output to validate")

    required_fields = expected.get("required", [])
    for field in required_fields:
        if field not in data:
            return GateResult(name="schema", passed=False, reason=f"Missing required field: {field}")

    properties = expected.get("properties", {})
    for key, spec in properties.items():
        if key in data:
            expected_type = spec.get("type")
            if expected_type:
                type_map = {"string": str, "integer": int, "number": (int, float), "boolean": bool, "array": list, "object": dict}
                py_type = type_map.get(expected_type)
                if py_type and not isinstance(data[key], py_type):  # type: ignore[arg-type]
                    return GateResult(name="schema", passed=False, reason=f"Type mismatch for '{key}': expected {expected_type}")

    return GateResult(name="schema", passed=True)


GATE_REGISTRY: dict[str, Any] = {
    "exit_code": _gate_exit_code,
    "output": _gate_output,
    "content-safety": _gate_content_safety,
    "syntax-check": _gate_syntax_check,
    "lint": _gate_lint,
    "dry-run": _gate_dry_run,
    "schema": _gate_schema,
}


# ── Verify Engine ─────────────────────────────────────────────

class VerifyEngine:
    """Run verification gates against execution results.

    Usage::

        engine = VerifyEngine()
        result = engine.verify(plan=plan_dict, result=exec_result, workdir="/tmp")
        if result.passed:
            logger.info("All gates passed!")
    """

    def __init__(
        self,
        custom_gates: dict[str, Any] | None = None,
        classifier: TaskStateClassifier | None = None,
    ) -> None:
        self._gates = dict(GATE_REGISTRY)
        if custom_gates:
            self._gates.update(custom_gates)
        self._classifier = classifier or TaskStateClassifier()

    def verify(
        self,
        plan: dict[str, Any],
        result: MaopResult | None,
        workdir: str = "",
    ) -> VerifyResult:
        """Run all gates specified in the plan.

        Parameters
        ----------
        plan : dict
            Plan result containing 'gates' list.
        result : MaopResult | None
            Execution result to verify.
        workdir : str
            Working directory.

        Returns
        -------
        VerifyResult
            Verification result with per-gate details.
        """
        requested_gates = plan.get("gates", ["exit_code", "output"])
        if not requested_gates:
            requested_gates = ["exit_code", "output"]

        gate_results: list[GateResult] = []
        for gate_name in requested_gates:
            gate_fn = self._gates.get(gate_name)
            if gate_fn is None:
                gate_results.append(GateResult(
                    name=gate_name, passed=False,
                    reason=f"Unknown gate: {gate_name}",
                ))
                continue

            try:
                gr = gate_fn(plan, result)
                gate_results.append(gr)
            except Exception as exc:
                gate_results.append(GateResult(
                    name=gate_name, passed=False,
                    reason=f"Gate exception: {exc}",
                ))

        all_passed = all(gr.passed for gr in gate_results)
        failed_gates = [gr for gr in gate_results if not gr.passed]

        summary = "All gates passed" if all_passed else f"Failed: {', '.join(gr.name for gr in failed_gates)}"
        feedback = ""
        if not all_passed:
            feedback = "; ".join(f"{gr.name}: {gr.reason}" for gr in failed_gates)

        # ── State classification ──
        stdout_text = result.stdout if result and result.stdout else ""
        stderr_text = result.stderr if result and result.stderr else ""
        gates_for_classifier = [
            {"name": gr.name, "passed": gr.passed, "reason": gr.reason}
            for gr in gate_results
        ]
        classification = self._classifier.classify(
            passed=all_passed,
            summary=summary,
            feedback=feedback,
            stdout=stdout_text,
            stderr=stderr_text,
            gates=gates_for_classifier,
        )

        return VerifyResult(
            passed=all_passed,
            summary=summary,
            gates=gate_results,
            feedback=feedback,
            state=classification.state.value,
            block_reason=classification.block_reason,
            classification=classification,
        )
