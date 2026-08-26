"""MAOP Loop Analyzer — Requirements analysis with LLM-first semantic extraction.

Extracted from maop_loop.py for single-responsibility separation.

G1b (2026-07-22, Phase G): ``simple_analyze`` is now ``async`` and runs an
LLM-first semantic extraction path when ``LoopConfig.enable_llm_analyze`` is
True and an LLM provider is configured. The original rule-based heuristic
(regex + keyword tables + scoring formula) is retained verbatim as the
``_rule_based_analyze`` private function and serves as automatic fallback when:
- ``enable_llm_analyze`` is False (default — preserves prior behavior)
- LLM provider is not configured (no API key)
- LLM call raises or returns invalid JSON

This dual-path design follows ADR-013 ("LLM direct primary + CLI fallback").
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from maop.loop_models import RequirementAnalysis

logger = logging.getLogger(__name__)


# ── t18: semantic keyword tables (used by rule fallback) ────
# Action verbs — bilingual (EN + zh-CN). Matched case-insensitively as whole
# tokens (word-boundary for ASCII letters, substring for CJK).
_ACTION_VERBS_EN: tuple[str, ...] = (
    "implement", "fix", "add", "remove", "delete", "refactor", "test",
    "deploy", "document", "configure", "optimize", "migrate", "integrate",
    "audit", "review", "update", "create", "build", "design", "analyze",
)
_ACTION_VERBS_ZH: tuple[str, ...] = (
    "实现", "修复", "添加", "删除", "重构", "测试", "部署", "文档",
    "配置", "优化", "迁移", "集成", "审计", "审查", "更新", "创建",
    "构建", "设计", "分析",
)

# Tech-stack keywords — bilingual. Detected by substring match (lowercased).
_TECH_KEYWORDS_EN: tuple[str, ...] = (
    "api", "database", "db", "sql", "ui", "cli", "http", "rest", "graphql",
    "frontend", "backend", "config", "yaml", "json", "docker", "k8s",
    "kubernetes", "redis", "postgres", "sqlite", "webhook", "websocket",
    "authentication", "auth", "jwt", "oauth", "sso",
)
_TECH_KEYWORDS_ZH: tuple[str, ...] = (
    "接口", "数据库", "前端", "后端", "认证", "授权", "配置",
    "容器", "部署", "鉴权",
)


def _detect_action_verbs(task_lower: str) -> list[str]:
    """Return the set of action verbs detected in the task (lowercased)."""
    detected: list[str] = []
    for verb in _ACTION_VERBS_EN:
        # Word-boundary prefix match for English verbs to avoid false positives
        # (e.g. "fix" inside "suffix") while allowing inflections
        # ("tests", "testing", "tested", "configures", ...).
        if re.search(rf"\b{re.escape(verb)}(?:s|es|ed|ing)?\b", task_lower):
            detected.append(verb)
    for verb in _ACTION_VERBS_ZH:
        if verb in task_lower:
            detected.append(verb)
    # de-duplicate while preserving order
    seen: set[str] = set()
    unique: list[str] = []
    for v in detected:
        if v not in seen:
            seen.add(v)
            unique.append(v)
    return unique


def _detect_tech_stack(task_lower: str) -> list[str]:
    """Return the set of tech-stack keywords detected in the task."""
    detected: list[str] = []
    for kw in _TECH_KEYWORDS_EN:
        # Word-boundary prefix match to allow inflections
        # ("databases", "configs", "deployments" ...).
        if re.search(rf"\b{re.escape(kw)}(?:s|es)?\b", task_lower):
            detected.append(kw)
    for kw in _TECH_KEYWORDS_ZH:
        if kw in task_lower:
            detected.append(kw)
    # de-duplicate
    seen: set[str] = set()
    unique: list[str] = []
    for k in detected:
        if k not in seen:
            seen.add(k)
            unique.append(k)
    return unique


def _estimate_complexity(
    task: str,
    action_verbs: list[str],
    tech_stack: list[str],
) -> str:
    """Rough complexity heuristic.

    Scoring:
      + 1 point per 100 chars of task length (capped at 4)
      + 1 point per action verb (capped at 3)
      + 1 point per tech-stack keyword (capped at 3)
      + 1 point if task mentions multi-step cue words ("then", "after",
        "followed", "然后", "之后", "接着")

    Bucket:
      0-2  → simple
      3-5  → moderate
      6+   → complex
    """
    score = 0
    score += min(len(task) // 100, 4)
    score += min(len(action_verbs), 3)
    score += min(len(tech_stack), 3)
    multi_step_cues = ("then", "after", "followed", "然后", "之后", "接着")
    task_lower = task.lower()
    if any(cue in task_lower for cue in multi_step_cues):
        score += 1
    if score <= 2:
        return "simple"
    if score <= 5:
        return "moderate"
    return "complex"


# Section header prefixes for rule-based task parsing (lower-cased).
# Maps section name → tuple of recognised header prefixes.
_SECTION_HEADERS: dict[str, tuple[str, ...]] = {
    "objective": ("目标:", "objective:", "goal:"),
    "boundary": ("边界:", "boundary:", "范围:", "scope:"),
    "acceptance": ("验收:", "acceptance:", "标准:", "criteria:"),
    "assumption": ("假设:", "assumption:", "前提:"),
    "risk": ("风险:", "risk:"),
}


def _detect_section_header(stripped: str) -> tuple[str, str]:
    """Return (section_name, content_after_colon) if ``stripped`` starts with a
    recognised section header prefix, else ("", "").

    Prefix matching is case-insensitive; the returned content preserves the
    original casing of ``stripped``.
    """
    lower = stripped.lower()
    for section, prefixes in _SECTION_HEADERS.items():
        for prefix in prefixes:
            if lower.startswith(prefix):
                content = stripped.split(":", 1)[1].strip() if ":" in stripped else ""
                return section, content
    return "", ""


def _parse_task_sections(task: str) -> dict[str, list[str]]:
    """Parse task text into structured sections (rule-based).

    Returns a dict with keys: objectives, boundaries, acceptance,
    assumptions, risks — each a list[str].
    """
    sections: dict[str, list[str]] = {
        "objectives": [],
        "boundaries": [],
        "acceptance": [],
        "assumptions": [],
        "risks": [],
    }
    # section name → list key in ``sections``.
    section_to_key = {
        "objective": "objectives",
        "boundary": "boundaries",
        "acceptance": "acceptance",
        "assumption": "assumptions",
        "risk": "risks",
    }
    current_section = ""
    for line in task.strip().split("\n"):
        stripped = line.strip()
        section, content = _detect_section_header(stripped)
        if section:
            current_section = section
            if content:
                sections[section_to_key[section]].append(content)
            continue
        if stripped and current_section:
            key = section_to_key.get(current_section)
            if key:
                sections[key].append(stripped)
    return sections


def _build_clarified_task(
    task: str,
    objectives: list[str],
    boundaries: list[str],
    acceptance: list[str],
) -> str:
    """Build the clarified task summary from parsed sections."""
    parts = [task.strip()]
    if objectives and len(objectives) > 1:
        parts.append("Objectives: " + "; ".join(objectives))
    if boundaries:
        parts.append("Boundaries: " + "; ".join(boundaries))
    if acceptance:
        parts.append("Accept: " + "; ".join(acceptance))
    return "\n".join(parts)


def _rule_based_analyze(task: str) -> RequirementAnalysis:
    """Rule-based fallback — original simple_analyze behavior.

    Parses task text for structured sections (objectives, boundaries,
    acceptance criteria, assumptions, risks) using keyword prefixes.
    Falls back to treating the entire task as a single objective if
    no structured sections are found.

    t18 (2026-07-21) — semantic fields populated by:
      - Detecting action verbs (bilingual EN/zh).
      - Detecting tech-stack keywords (bilingual EN/zh).
      - Estimating complexity (simple / moderate / complex).

    G1a (2026-07-22): extracted verbatim from ``simple_analyze`` to serve
    as the rule-based fallback path. No behavioral change.
    """
    sections = _parse_task_sections(task)
    objectives = sections["objectives"]
    boundaries = sections["boundaries"]
    acceptance = sections["acceptance"]
    assumptions = sections["assumptions"]
    risks = sections["risks"]

    if not objectives:
        objectives = [task.strip()]
    if not acceptance:
        acceptance = [f"Task completes without error: {task[:60]}"]
    if not assumptions:
        assumptions = ["Assumption: Task description is complete and unambiguous"]

    clarified_task = _build_clarified_task(task, objectives, boundaries, acceptance)

    # ── t18: semantic analysis (rule-based) ───────────────
    task_lower = task.lower()
    action_verbs = _detect_action_verbs(task_lower)
    tech_stack = _detect_tech_stack(task_lower)
    complexity = _estimate_complexity(task, action_verbs, tech_stack)

    return RequirementAnalysis(
        task=task,
        objectives=objectives,
        boundaries=boundaries,
        acceptance_criteria=acceptance,
        assumptions=assumptions,
        risks=risks,
        clarified_task=clarified_task,
        action_verbs=action_verbs,
        tech_stack=tech_stack,
        complexity=complexity,
    )


# ── G1b: LLM-first semantic extraction ──────────────────────────

# JSON schema keys the LLM must return. Maps to RequirementAnalysis fields.
_LLM_EXTRACTION_KEYS: tuple[str, ...] = (
    "action_verbs",
    "tech_stack",
    "complexity",
    "objectives",
    "boundaries",
    "acceptance_criteria",
    "assumptions",
    "risks",
    "clarified_task",
)

_VALID_COMPLEXITY: frozenset[str] = frozenset({"simple", "moderate", "complex", "unknown"})


def _build_llm_extraction_prompt(task: str) -> list[dict[str, str]]:
    """Build the chat messages for LLM-based semantic extraction.

    Asks the LLM to return strict JSON matching the RequirementAnalysis
    schema. Temperature should be 0.0 for deterministic extraction.
    """
    system_prompt = (
        "You are a requirements analysis assistant. Extract structured "
        "semantic information from the given task description and return "
        "it as strict JSON. Do NOT wrap the JSON in markdown code fences.\n\n"
        "Required fields (all keys must be present):\n"
        "  - action_verbs: list[str] — action verbs found in the task "
        "(e.g. 'implement', 'test', 'deploy'; Chinese: '实现','测试','部署')\n"
        "  - tech_stack: list[str] — concrete technologies mentioned "
        "(e.g. 'fastapi', 'vue3', 'postgres', 'docker')\n"
        "  - complexity: str — one of 'simple' | 'moderate' | 'complex'\n"
        "  - objectives: list[str] — explicit goals stated or implied\n"
        "  - boundaries: list[str] — scope constraints\n"
        "  - acceptance_criteria: list[str] — what 'done' looks like\n"
        "  - assumptions: list[str] — unstated prerequisites assumed\n"
        "  - risks: list[str] — potential failure modes\n"
        "  - clarified_task: str — the task rewritten with clarifications\n\n"
        "Return ONLY the JSON object, no commentary."
    )
    user_prompt = f"Task:\n{task}\n\nExtract the structured analysis as JSON."
    return [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]


def _parse_llm_extraction(content: str, task: str) -> RequirementAnalysis | None:
    """Parse the LLM JSON response into a RequirementAnalysis.

    Returns None if the response cannot be parsed or fails validation.
    The caller falls back to ``_rule_based_analyze`` in that case.
    """
    if not content or not content.strip():
        return None
    text = content.strip()
    # Strip accidental markdown code fences if present.
    if text.startswith("```"):
        # ``` or ```json
        first_newline = text.find("\n")
        if first_newline != -1:
            text = text[first_newline + 1:]
        text = text.removesuffix("```")
        text = text.strip()
    try:
        data: Any = json.loads(text)
    except (json.JSONDecodeError, ValueError) as exc:
        logger.debug("[loop_analyzer] LLM JSON parse failed: %s", exc)
        return None
    if not isinstance(data, dict):
        return None
    # Normalize + validate field types.
    kwargs: dict[str, Any] = {"task": task}
    for key in _LLM_EXTRACTION_KEYS:
        val = data.get(key)
        if key == "complexity":
            if isinstance(val, str) and val.lower() in _VALID_COMPLEXITY:
                kwargs[key] = val.lower()
            # else: leave default "unknown"
        elif key == "clarified_task":
            if isinstance(val, str) and val.strip():
                kwargs[key] = val
        else:
            # List fields — coerce to list[str], skip non-strings.
            if isinstance(val, list):
                cleaned = [str(x).strip() for x in val if isinstance(x, (str, int, float)) and str(x).strip()]
                kwargs[key] = cleaned
    try:
        return RequirementAnalysis(**kwargs)
    except Exception as exc:  # pydantic ValidationError
        logger.debug("[loop_analyzer] LLM extraction failed validation: %s", exc)
        return None


async def simple_analyze(
    task: str,
    *,
    llm_factory: Any = None,
    model_name: str = "",
    enable_llm: bool = False,
) -> RequirementAnalysis:
    """Analyze a task — LLM-first semantic extraction with rule-based fallback.

    Parses task text for structured sections (objectives, boundaries,
    acceptance criteria, assumptions, risks) and semantic fields
    (action_verbs, tech_stack, complexity).

    G1b (2026-07-22, Phase G): dual-path implementation per ADR-013.

    - **Primary path (LLM)**: when ``enable_llm=True`` and ``llm_factory``
      is provided with a configured provider, call
      ``llm_factory.chat_with_fallback(messages=_build_llm_extraction_prompt(task),
      model_name, temperature=0.0, max_tokens=1024)`` and parse
      the JSON response into RequirementAnalysis. Real semantic
      understanding — action_verbs / tech_stack / complexity come from
      the LLM, not from hardcoded keyword tables.
    - **Fallback path (rule)**: when LLM is disabled, not configured, or
      raises/errors, falls back to ``_rule_based_analyze(task)`` — the
      original t18 rule-based heuristic (regex + keyword + scoring).

    Args:
        task: The task description to analyze.
        llm_factory: ``LLMProviderFactory`` instance (lazy-loaded by caller).
        model_name: Model key in models.yaml (e.g. "yi-large"). Empty = default.
        enable_llm: Master switch — when False, skip LLM path entirely.

    Returns:
        RequirementAnalysis with action_verbs / tech_stack / complexity
        populated either by LLM extraction or rule-based heuristic.
    """
    if not enable_llm or llm_factory is None:
        return _rule_based_analyze(task)

    # Primary path: LLM semantic extraction.
    try:
        provider = llm_factory.get_provider(model_name) if model_name else None
        if provider is None or not provider.is_configured:
            logger.debug(
                "[loop_analyzer] LLM provider not configured for model=%r, "
                "falling back to rule-based analysis",
                model_name,
            )
            return _rule_based_analyze(task)
        messages = _build_llm_extraction_prompt(task)
        # 统一走 chat_with_fallback 以触发 _record_cost 成本记录与 fallback 链
        fb_result = await llm_factory.chat_with_fallback(
            messages, model_name,
            temperature=0.0, max_tokens=1024,
        )
        result = _parse_llm_extraction(fb_result.response.content, task)
        if result is not None:
            logger.info(
                "[loop_analyzer] LLM extraction succeeded: action_verbs=%d "
                "tech_stack=%d complexity=%s (model=%s)",
                len(result.action_verbs), len(result.tech_stack),
                result.complexity, model_name,
            )
            return result
        logger.warning(
            "[loop_analyzer] LLM returned unparseable content, "
            "falling back to rule-based analysis (model=%s)",
            model_name,
        )
    except Exception as exc:
        logger.warning(
            "[loop_analyzer] LLM call failed, falling back to rule-based "
            "analysis (model=%s, error=%s)",
            model_name, exc,
        )
    return _rule_based_analyze(task)
