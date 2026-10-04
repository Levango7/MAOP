"""MAOP Verify — Post-execution verification engine.

Execution result verification and quality checks.: checks exit_code, output quality,
content safety, and custom gates. Returns structured VerifyResult.
"""

from __future__ import annotations

import inspect
import logging
import re
from pathlib import Path
from typing import Any, Literal, cast

from pydantic import BaseModel, Field

from maop.core.agent.lifecycle.state_classifier import ClassificationResult, TaskStateClassifier
from maop.core.reliability.error_schema import MaopResult

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
    errored: bool = False  # True when verification could not run (engine error), not a real task failure
    gates: list[GateResult] = Field(default_factory=list)
    feedback: str = ""  # Suggested fix when failed
    # ── State classification (Claude Code-inspired) ──
    state: TaskStateLiteral = "working"
    block_reason: str = ""  # Populated when state == "blocked"
    classification: ClassificationResult | None = None


# ── Built-in gates ────────────────────────────────────────────

def _gate_exit_code(plan: dict, result: MaopResult | None,
              workdir: str = "") -> GateResult:
    """Check that exit code is 0."""
    if result is None:
        return GateResult(name="exit_code", passed=False, reason="No execution result")
    if result.exit_code == 0:
        return GateResult(name="exit_code", passed=True)
    return GateResult(name="exit_code", passed=False, reason=f"exit_code={result.exit_code}")


def _gate_output(plan: dict, result: MaopResult | None,
              workdir: str = "") -> GateResult:
    """Check that output is non-empty."""
    if result is None:
        return GateResult(name="output", passed=False, reason="No execution result")
    if result.stdout and result.stdout.strip():
        return GateResult(name="output", passed=True)
    return GateResult(name="output", passed=False, reason="Empty output")


def _gate_content_safety(plan: dict, result: MaopResult | None,
              workdir: str = "") -> GateResult:
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


def _gate_syntax_check(plan: dict, result: MaopResult | None,
              workdir: str = "") -> GateResult:
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


def _gate_lint(plan: dict, result: MaopResult | None,
              workdir: str = "") -> GateResult:
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


def _gate_dry_run(plan: dict, result: MaopResult | None,
              workdir: str = "") -> GateResult:
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
    # P2-fix: 为 and/or 混用的布尔表达式添加括号，明确求值顺序。
    # 原代码依赖默认优先级（and > or），可读性差且容易出错。
    # 语义：structured 是 dict 且（有 dry_run 标志 或（有 dry_run_artifacts 且是非空列表））
    if isinstance(structured, dict) and (
        structured.get("dry_run")
        or (
            "dry_run_artifacts" in structured
            and isinstance(structured["dry_run_artifacts"], list)
            and structured["dry_run_artifacts"]
        )
    ):
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

def _gate_schema(plan: dict, result: MaopResult | None,
              workdir: str = "") -> GateResult:
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
        from maop.core.agent.llm_chat.output_parser import OutputParser
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
                if py_type and not isinstance(data[key], py_type):  # type: ignore
                    return GateResult(name="schema", passed=False, reason=f"Type mismatch for '{key}': expected {expected_type}")

    return GateResult(name="schema", passed=True)


def _gate_expected_files(plan: dict, result: MaopResult | None,
                         workdir: str = "") -> GateResult:
    """工件门：plan 里声明的产物必须真的落在 workdir 上。

    为什么加它：在此之前**没有任何 gate 看过磁盘** —— 七个内置 gate 全部只看
    `exit_code` 与 stdout 的正则（`workdir` 一路传到 `verify()` 却从不下传给 gate）。
    于是"验证通过"的真实含义只是"进程退出 0 且打印了点什么"，agent 完全没产出文件
    也算通过。

    plan 里的声明方式（**不声明就不跑**，所以对既有 plan 零行为变化）::

        {
          "gates": ["exit_code", "expected_files"],
          "expected_files": [
            "out/report.md",                       # 存在即可
            {"path": "out/data.json", "min_bytes": 1024},
            {"path": "out", "kind": "dir"},
          ]
        }

    安全：所有路径都按 `workdir` 解析并**必须落在 workdir 内** —— 声明来自 plan
    （可能由 LLM 产出），允许 `../` 或绝对路径就等于让 plan 去探测宿主文件系统。
    越界（含符号链接逃逸）、workdir 缺失或不是目录一律判失败（fail-closed）。
    """
    declared = plan.get("expected_files")
    if not declared:
        # 未声明 = 本门不适用。返回 passed 而不是失败：否则所有历史 plan 一夜之间全红。
        return GateResult(name="expected_files", passed=True, reason="未声明 expected_files，跳过")

    if not workdir:
        return GateResult(
            name="expected_files", passed=False,
            reason="plan 声明了 expected_files，但没有 workdir 可供校验（fail-closed）",
        )

    base = Path(workdir).resolve()
    if not base.is_dir():
        return GateResult(name="expected_files", passed=False,
                          reason=f"workdir 不存在或不是目录：{base}")

    if isinstance(declared, (str, dict)):
        declared = [declared]
    if not isinstance(declared, list):
        return GateResult(name="expected_files", passed=False,
                          reason=f"expected_files 必须是列表，实际是 {type(declared).__name__}")

    for entry in declared:
        if isinstance(entry, str):
            spec: dict[str, Any] = {"path": entry}
        elif isinstance(entry, dict):
            spec = entry
        else:
            return GateResult(name="expected_files", passed=False,
                              reason=f"expected_files 条目必须是字符串或对象：{entry!r}")

        raw = str(spec.get("path", "")).strip()
        if not raw:
            return GateResult(name="expected_files", passed=False, reason="expected_files 条目缺少 path")

        target = Path(raw).resolve() if Path(raw).is_absolute() else (base / raw).resolve()
        if target != base and base not in target.parents:
            # 用 resolve() 之后再比较（而非字符串前缀）：符号链接指向外部同样被拦下。
            return GateResult(
                name="expected_files", passed=False,
                reason=f"路径越出 workdir，拒绝校验：{raw} → {target}",
            )

        kind = str(spec.get("kind", "file") or "file")
        if not target.exists():
            return GateResult(name="expected_files", passed=False,
                              reason=f"声明产物不存在：{raw}")
        if kind == "dir" and not target.is_dir():
            return GateResult(name="expected_files", passed=False,
                              reason=f"声明产物应为目录但是文件：{raw}")
        if kind == "file":
            if not target.is_file():
                return GateResult(name="expected_files", passed=False,
                                  reason=f"声明产物应为文件但是目录：{raw}")
            min_bytes = spec.get("min_bytes")
            if min_bytes is not None:
                try:
                    need = int(min_bytes)
                except (TypeError, ValueError):
                    return GateResult(name="expected_files", passed=False,
                                      reason=f"min_bytes 不是整数：{min_bytes!r}")
                size = target.stat().st_size
                if size < need:
                    return GateResult(
                        name="expected_files", passed=False,
                        reason=f"产物过小（可能只是占位）：{raw} 实际 {size} 字节 < 要求 {need}",
                    )

    return GateResult(name="expected_files", passed=True,
                      reason=f"已校验 {len(declared)} 项声明产物")


def _call_gate(gate_fn: Any, plan: dict, result: MaopResult | None, workdir: str) -> GateResult:
    """调用一个 gate，按需下传 `workdir`。

    内置 gate 都收第三个参数；但 `VerifyEngine(custom_gates=...)` 是公开构造参数，
    既有外部 gate 是 `(plan, result)` 两参数 —— 直接三参调用会把它们打挂。所以这里
    按签名判定，两种都支持。

    刻意**不用** `try: f(a,b,c) except TypeError: f(a,b)`：gate 内部抛的 TypeError
    会被吞成"签名不匹配"，把一个真实的 gate bug 变成静默重试。

    返回处一律 `cast`：`gate_fn` 是 `Any`（注册表里混着内置函数与外部插件），
    mypy 的 no-any-return 会在这里报错；签名判定本身保证调用形状正确，返回值则由
    调用方按 `GateResult` 使用 —— 真返回了别的东西，`verify()` 构造 `VerifyResult`
    时 pydantic 会拒绝（不是静默放过）。
    """
    try:
        params = list(inspect.signature(gate_fn).parameters.values())
    except (TypeError, ValueError):
        # 拿不到签名（内建/C 实现）→ 按旧约定调用
        return cast(GateResult, gate_fn(plan, result))
    if any(p.kind is p.VAR_POSITIONAL for p in params):
        return cast(GateResult, gate_fn(plan, result, workdir))
    positional = [p for p in params
                  if p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD)]
    if len(positional) >= 3:
        return cast(GateResult, gate_fn(plan, result, workdir))
    return cast(GateResult, gate_fn(plan, result))


GATE_REGISTRY: dict[str, Any] = {
    "exit_code": _gate_exit_code,
    "output": _gate_output,
    "content-safety": _gate_content_safety,
    "syntax-check": _gate_syntax_check,
    "lint": _gate_lint,
    "dry-run": _gate_dry_run,
    "schema": _gate_schema,
    # 唯一一个看磁盘的门（需要 plan 声明 expected_files，见其 docstring）。
    "expected_files": _gate_expected_files,
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
        engine_errored = False
        for gate_name in requested_gates:
            gate_fn = self._gates.get(gate_name)
            if gate_fn is None:
                gate_results.append(GateResult(
                    name=gate_name, passed=False,
                    reason=f"Unknown gate: {gate_name}",
                ))
                engine_errored = True
                continue

            try:
                gr = _call_gate(gate_fn, plan, result, workdir)
                gate_results.append(gr)
            except Exception as exc:
                engine_errored = True
                logger.exception("Gate %s raised an exception (engine bug, not a task failure)", gate_name)
                gate_results.append(GateResult(
                    name=gate_name, passed=False,
                    reason=f"Gate engine error (not a task failure): {exc}",
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
            errored=engine_errored,
            gates=gate_results,
            feedback=feedback,
            state=classification.state.value,
            block_reason=classification.block_reason,
            classification=classification,
        )
