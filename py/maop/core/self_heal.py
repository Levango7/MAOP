"""MAOP Self-Heal — Detect-Verify-Repair loop for system resilience.

Supports:
  - HealRule: condition → action → verify
  - Built-in rules for common failure scenarios
  - Custom rule registration
  - Execution with verification

Usage::

    from maop.core.self_heal import SelfHealEngine

    engine = SelfHealEngine(root_dir="/path/to/MAOP")

    # Register a rule
    engine.register(HealRule(
        name="db_locked",
        condition="sqlite3.OperationalError: database is locked",
        action="vacuum",
        verify="db_accessible",
    ))

    # Run all rules
    report = engine.run_all()
"""

from __future__ import annotations

import logging
import time
import uuid
from enum import Enum
from pathlib import Path
from typing import Callable

from pydantic import BaseModel, Field

from maop.core.db_utils import get_db_path, sqlite_connect

logger = logging.getLogger(__name__)


class HealAction(str, Enum):
    VACUUM = "vacuum"
    RECONNECT = "reconnect"
    CLEAR_CACHE = "clear_cache"
    REBUILD_INDEX = "rebuild_index"
    RESTART = "restart"
    CUSTOM = "custom"


class HealStatus(str, Enum):
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    UNHEALTHY = "unhealthy"
    REPAIRED = "repaired"
    REPAIR_FAILED = "repair_failed"


class HealRule(BaseModel):
    name: str = Field(default_factory=lambda: uuid.uuid4().hex[:8])
    condition: str = ""
    action: HealAction = HealAction.CUSTOM
    verify: str = ""
    priority: int = 0
    enabled: bool = True
    max_retries: int = 1


class HealResult(BaseModel):
    rule_name: str
    status: HealStatus = HealStatus.HEALTHY
    message: str = ""
    retries: int = 0
    duration_s: float = 0.0


class HealReport(BaseModel):
    total_rules: int = 0
    checked: int = 0
    repaired: int = 0
    failed: int = 0
    healthy: int = 0
    results: list[HealResult] = Field(default_factory=list)


_SELF_HEAL_DDL = """
CREATE TABLE IF NOT EXISTS heal_rules (
    name TEXT PRIMARY KEY,
    condition TEXT NOT NULL,
    action TEXT NOT NULL,
    verify TEXT DEFAULT '',
    priority INTEGER DEFAULT 0,
    enabled INTEGER DEFAULT 1,
    max_retries INTEGER DEFAULT 1
);

CREATE TABLE IF NOT EXISTS heal_history (
    id TEXT PRIMARY KEY,
    rule_name TEXT NOT NULL,
    status TEXT NOT NULL,
    message TEXT DEFAULT '',
    duration_s REAL DEFAULT 0.0,
    executed_at REAL NOT NULL
);
"""


class SelfHealEngine:
    def __init__(self, root_dir: str | Path) -> None:
        self._root = Path(root_dir)
        self._data_dir = self._root / "data"
        self._data_dir.mkdir(parents=True, exist_ok=True)
        self._db_path = get_db_path("self_heal")
        self._custom_actions: dict[str, Callable[[], bool]] = {}
        self._custom_verifiers: dict[str, Callable[[], bool]] = {}
        self._init_db()
        self._register_builtin_rules()

    def _init_db(self) -> None:
        with self._db_connect() as conn:
            conn.executescript(_SELF_HEAL_DDL)

    def _db_connect(self):
        return sqlite_connect(self._db_path, foreign_keys=False)

    def _register_builtin_rules(self) -> None:
        builtins = [
            HealRule(name="db_locked", condition="database is locked",
                     action=HealAction.VACUUM, verify="db_accessible", priority=10),
            HealRule(name="cache_stale", condition="cache hit rate < 10%",
                     action=HealAction.CLEAR_CACHE, verify="cache_accessible", priority=20),
        ]
        for rule in builtins:
            self._register_to_db(rule)

    def _register_to_db(self, rule: HealRule) -> None:
        with self._db_connect() as conn:
            conn.execute(
                """INSERT OR REPLACE INTO heal_rules (name, condition, action, verify, priority, enabled, max_retries)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (rule.name, rule.condition, rule.action.value, rule.verify,
                 rule.priority, int(rule.enabled), rule.max_retries),
            )

    def register(self, rule: HealRule) -> None:
        self._register_to_db(rule)
        logger.info("Heal rule registered: %s", rule.name)

    def register_action(self, name: str, fn: Callable[[], bool]) -> None:
        self._custom_actions[name] = fn

    def register_verifier(self, name: str, fn: Callable[[], bool]) -> None:
        self._custom_verifiers[name] = fn

    def _execute_action(self, action: HealAction, rule_name: str) -> bool:
        try:
            if action == HealAction.VACUUM:
                db_path = self._data_dir / "maop.db"
                if db_path.exists():
                    with sqlite_connect(db_path) as conn:
                        conn.execute("VACUUM")
                return True
            elif action == HealAction.CLEAR_CACHE:
                cache_dir = self._data_dir / "cache"
                if cache_dir.exists():
                    for f in cache_dir.iterdir():
                        if f.is_file():
                            f.unlink(missing_ok=True)
                return True
            elif action == HealAction.CUSTOM:
                fn = self._custom_actions.get(rule_name)
                if fn:
                    return fn()
            return True
        except Exception as exc:
            logger.warning("Heal action '%s' failed: %s", action.value, exc)
            return False

    def _verify(self, verify: str) -> bool:
        if not verify:
            return True
        if verify == "db_accessible":
            db_path = self._data_dir / "maop.db"
            if not db_path.exists():
                return True
            try:
                with sqlite_connect(db_path) as conn:
                    conn.execute("SELECT 1")
                return True
            except Exception:
                return False
        if verify == "cache_accessible":
            return True
        fn = self._custom_verifiers.get(verify)
        if fn:
            return fn()
        return True

    def run_rule(self, rule: HealRule) -> HealResult:
        start = time.time()
        result = HealResult(rule_name=rule.name)

        for attempt in range(rule.max_retries + 1):
            success = self._execute_action(rule.action, rule.name)
            if success and self._verify(rule.verify):
                result.status = HealStatus.REPAIRED
                result.message = f"Repaired after {attempt + 1} attempt(s)"
                result.retries = attempt
                break
        else:
            result.status = HealStatus.REPAIR_FAILED
            result.message = f"Failed after {rule.max_retries + 1} attempt(s)"

        result.duration_s = round(time.time() - start, 3)

        self._record_history(result)
        return result

    def run_all(self, trigger_condition: str = "") -> HealReport:
        report = HealReport()
        with self._db_connect() as conn:
            cursor = conn.execute(
                """SELECT name, condition, action, verify, priority, enabled, max_retries
                   FROM heal_rules WHERE enabled = 1 ORDER BY priority ASC"""
            )
            cols = [d[0] for d in cursor.description] if cursor.description else []
            rows = cursor.fetchall()

        report.total_rules = len(rows)

        for row in rows:
            d = dict(zip(cols, row))
            d["enabled"] = bool(d.get("enabled", 1))
            rule = HealRule(**d)

            if trigger_condition and trigger_condition not in rule.condition:
                report.healthy += 1
                report.checked += 1
                continue

            result = self.run_rule(rule)
            report.results.append(result)
            report.checked += 1
            if result.status == HealStatus.REPAIRED:
                report.repaired += 1
            elif result.status == HealStatus.REPAIR_FAILED:
                report.failed += 1
            else:
                report.healthy += 1

        return report

    def _record_history(self, result: HealResult) -> None:
        with self._db_connect() as conn:
            conn.execute(
                """INSERT INTO heal_history (id, rule_name, status, message, duration_s, executed_at)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (uuid.uuid4().hex[:12], result.rule_name, result.status.value,
                 result.message, result.duration_s, time.time()),
            )