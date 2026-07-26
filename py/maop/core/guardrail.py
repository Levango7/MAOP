"""MAOP Guardrail — rule engine for input/output safety checks.

Rule types:
  - content  : regex patterns for sensitive data (API keys, private keys, etc.)
  - input    : max length check on input content
  - agent    : blocklist of forbidden agents
  - task     : allowlist of permitted task patterns
  - rate     : per-minute rate limiting via RateLimiter (token bucket)
  - output   : max output size check
"""

from __future__ import annotations

import json
import logging
import re
from enum import Enum
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


# ── Models ────────────────────────────────────────────────────

class RuleAction(str, Enum):
    WARN = "warn"
    BLOCK = "block"
    TRUNCATE = "truncate"


class RuleType(str, Enum):
    CONTENT = "content"
    INPUT = "input"
    AGENT = "agent"
    TASK = "task"
    RATE = "rate"
    OUTPUT = "output"


class GuardRule(BaseModel):
    id: str
    type: RuleType
    enabled: bool = True
    action: RuleAction = RuleAction.BLOCK
    description: str = ""
    # type-specific fields (optional)
    limit: int | None = None
    patterns: list[str] = Field(default_factory=list)
    blocklist: list[str] = Field(default_factory=list)
    allowlist: list[str] = Field(default_factory=list)
    max_per_minute: int | None = None


class GuardConfig(BaseModel):
    rules: list[GuardRule] = Field(default_factory=list)


class Violation(BaseModel):
    rule: str
    severity: str  # "warn" | "block"
    message: str
    action: str


class CheckResult(BaseModel):
    passed: bool
    violations: list[Violation] = Field(default_factory=list)
    summary: str  # "PASS" | "WARN" | "BLOCKED"


# ── Default config (mirrors guardrail.ps1) ────────────────────

DEFAULT_RULES: list[dict[str, Any]] = [
    {
        "id": "max-task-length",
        "type": "input",
        "enabled": True,
        "limit": 5000,
        "action": "warn",
        "description": "任务文本最长5000字符",
    },
    {
        "id": "blocked-agents",
        "type": "agent",
        "enabled": True,
        "blocklist": [],
        "action": "block",
        "description": "禁止使用的 agent 列表",
    },
    {
        "id": "rate-limit",
        "type": "rate",
        "enabled": False,
        "max_per_minute": 30,
        "action": "block",
        "description": "每分钟最大请求数",
    },
    {
        "id": "sensitive-patterns",
        "type": "content",
        "enabled": True,
        "patterns": [
            r"sk-[a-zA-Z0-9]{20,}",
            r"AKIA[0-9A-Z]{16}",
            r"-----BEGIN (RSA |EC )?PRIVATE KEY-----",
        ],
        "action": "block",
        "description": "敏感信息泄露防护",
    },
    {
        "id": "allowed-tasks",
        "type": "task",
        "enabled": False,
        "allowlist": ["*"],
        "action": "block",
        "description": "允许执行的任务白名单",
    },
    {
        "id": "max-output-size",
        "type": "output",
        "enabled": True,
        "limit": 100_000,
        "action": "truncate",
        "description": "输出最大100KB",
    },
]


def _default_config() -> GuardConfig:
    return GuardConfig(rules=[GuardRule(**r) for r in DEFAULT_RULES])


# ── Guardrail Engine ─────────────────────────────────────────

class Guardrail:
    """File-backed guardrail rule engine.

    Usage::

        g = Guardrail()  # loads data/guardrails.json
        result = g.check(content="my code", agent="claude", task="codegen")
        if not result.passed:
            raise RuntimeError(result.summary)
    """

    def __init__(self, config_path: Path | str | None = None) -> None:
        if config_path is None:
            config_path = Path(__file__).resolve().parents[3] / "data" / "guardrails.json"
        self._path = Path(config_path)
        self._config = self._load()
        self._rate_limiters: dict[str, Any] = {}  # persistent rate limiters per rule

    # ── persistence ──────────────────────────────────────────

    def _load(self) -> GuardConfig:
        if self._path.exists():
            try:
                raw = json.loads(self._path.read_text(encoding="utf-8"))
                return GuardConfig(**raw)
            except (json.JSONDecodeError, ValueError, TypeError):
                pass
        return _default_config()

    def _save(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(
            self._config.model_dump_json(indent=2),
            encoding="utf-8",
        )

    # ── check ────────────────────────────────────────────────

    def check(
        self,
        content: str = "",
        agent: str = "",
        task: str = "",
    ) -> CheckResult:
        """Run all enabled rules and return a CheckResult."""
        violations: list[Violation] = []

        for rule in self._config.rules:
            if not rule.enabled:
                continue

            if rule.type == RuleType.CONTENT:
                for pattern in rule.patterns:
                    if re.search(pattern, content):
                        violations.append(Violation(
                            rule=rule.id,
                            severity="block",
                            message="sensitive content detected",
                            action="block",
                        ))

            elif rule.type == RuleType.INPUT:
                if rule.limit is not None and len(content) > rule.limit:
                    violations.append(Violation(
                        rule=rule.id,
                        severity="warn",
                        message=f"content exceeds limit: {len(content)} chars",
                        action=rule.action.value,
                    ))

            elif rule.type == RuleType.AGENT:
                if agent in rule.blocklist:
                    violations.append(Violation(
                        rule=rule.id,
                        severity="block",
                        message=f"agent blocked: {agent}",
                        action="block",
                    ))

            elif rule.type == RuleType.TASK:
                if rule.allowlist and rule.allowlist != ["*"]:
                    matched = any(
                        fnmatch_simple(task, pat) for pat in rule.allowlist
                    )
                    if not matched:
                        violations.append(Violation(
                            rule=rule.id,
                            severity="block",
                            message="task not in allowlist",
                            action="block",
                        ))

            elif rule.type == RuleType.OUTPUT:
                # Output size check — caller should pass output as content
                if rule.limit is not None and len(content) > rule.limit:
                    violations.append(Violation(
                        rule=rule.id,
                        severity="warn",
                        message=f"output exceeds limit: {len(content)} bytes",
                        action=rule.action.value,
                    ))

            elif rule.type == RuleType.RATE:
                # Rate limiting: persistent in-memory token bucket per agent
                try:
                    from maop.core.rate_limiter import RateLimiter, RateLimiterConfig
                    max_rpm = rule.max_per_minute or rule.limit or 30
                    # Reuse persistent RateLimiter instance per rule
                    if rule.id not in self._rate_limiters:
                        self._rate_limiters[rule.id] = RateLimiter(
                            config=RateLimiterConfig(max_requests=max_rpm, window_s=60.0),
                        )
                    rl = self._rate_limiters[rule.id]
                    result = rl.consume(agent or "default")
                    if not result.allowed:
                        violations.append(Violation(
                            rule=rule.id,
                            severity="warn",
                            message=f"rate limit exceeded for agent: {agent}",
                            action=rule.action.value,
                        ))
                except Exception as exc:
                    # Security: fail-closed — if rate limiter fails, treat as violation
                    # rather than silently allowing the request through.
                    logger.warning("[guardrail] rate limit check failed (fail-closed): %s", exc)
                    violations.append(Violation(
                        rule=rule.id,
                        severity="warn",
                        message=f"rate limit check error: {exc}",
                        action=rule.action.value,
                    ))

        blocked = [v for v in violations if v.action == "block"]
        passed = len(blocked) == 0
        summary = "BLOCKED" if blocked else ("WARN" if violations else "PASS")

        return CheckResult(passed=passed, violations=violations, summary=summary)

    def allow(self, content: str = "", agent: str = "", task: str = "") -> bool:
        """Return True if all checks pass; False otherwise."""
        result = self.check(content=content, agent=agent, task=task)
        return result.passed

    def block(self, agent: str, task: str) -> dict[str, str]:
        """Explicitly block an agent/task pair."""
        return {"action": "blocked", "message": f"{agent} blocked: {task}"}

    # ── config management ────────────────────────────────────

    def get_config(self, rule_id: str | None = None) -> dict[str, Any]:
        """Return full config or a single rule by id."""
        if rule_id:
            for r in self._config.rules:
                if r.id == rule_id:
                    return r.model_dump(mode="json")
            return {}
        return self._config.model_dump(mode="json")

    def report(self) -> dict[str, Any]:
        """Summary of all rules."""
        rules_summary = [
            {
                "id": r.id,
                "type": r.type.value,
                "enabled": r.enabled,
                "action": r.action.value,
                "description": r.description,
            }
            for r in self._config.rules
        ]
        enabled_count = sum(1 for r in rules_summary if r["enabled"])
        return {
            "total": len(rules_summary),
            "enabled": enabled_count,
            "rules": rules_summary,
        }

    def reset(self) -> None:
        """Reset guardrails to defaults."""
        self._config = _default_config()
        self._save()


# ── Helpers ───────────────────────────────────────────────────

def fnmatch_simple(name: str, pattern: str) -> bool:
    """Minimal glob-style matching (only ``*`` wildcard).

    Avoids importing ``fnmatch`` to keep dependencies minimal;
    use ``fnmatch.fnmatch`` if you need full glob semantics.
    """
    if pattern == "*":
        return True
    if "*" not in pattern:
        return name == pattern
    # Split on * and check sequential containment
    parts = pattern.split("*")
    idx = 0
    for _i, part in enumerate(parts):
        if not part:
            continue
        found = name.find(part, idx)
        if found == -1:
            return False
        idx = found + len(part)
    return True


# ── CLI bridge — allows PS guardrail.ps1 to call this module ──
if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="MAOP Guardrail — CLI bridge")
    sub = parser.add_subparsers(dest="action", required=True)

    check_parser = sub.add_parser("check", help="Check content against rules")
    check_parser.add_argument("--content", default="", help="Content to check")
    check_parser.add_argument("--agent", default="", help="Agent name")
    check_parser.add_argument("--task", default="", help="Task description")

    sub.add_parser("report", help="Show active rules summary")

    args = parser.parse_args()
    g = Guardrail()

    if args.action == "check":
        result = g.check(content=args.content, agent=args.agent, task=args.task)
        logger.info(result.model_dump_json(indent=2))

    elif args.action == "report":
        for r in g._config.rules:
            logger.info(f"  [{r.type.value}] {r.id}: {('enabled' if r.enabled else 'disabled')}")
        logger.info(f'\n  Total rules: {len(g._config.rules)}')
