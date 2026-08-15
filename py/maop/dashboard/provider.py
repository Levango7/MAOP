"""MAOP Dashboard — Pure Python web dashboard (replaces PS bridge).

FastAPI-based dashboard with:
  - Agent status overview
  - Circuit breaker states
  - Memory search
  - Execution history
  - Evolution suggestions
  - SSE live updates
"""

from __future__ import annotations

import json
import logging
import sqlite3
import time
from pathlib import Path
from typing import Any, cast

import aiosqlite
from pydantic import BaseModel

logger = logging.getLogger(__name__)


# ── Dashboard Data Models ─────────────────────────────────────

class AgentStatus(BaseModel):
    name: str = ""
    driver: str = ""
    available: bool = True
    breaker_state: str = "closed"
    breaker_failures: int = 0
    last_execution_ms: int = 0


class DashboardState(BaseModel):
    """Complete dashboard state snapshot."""
    agents: list[AgentStatus] = []
    total_delegations: int = 0
    success_rate: float = 0.0
    active_tasks: int = 0
    memory_entries: int = 0
    evolution_suggestions: int = 0
    uptime_s: float = 0.0


# ── Dashboard Provider ────────────────────────────────────────

class DashboardProvider:
    """Collects and serves dashboard data from maop subsystems.

    Usage::

        provider = DashboardProvider(root_dir="/path/to/MAOP")
        state = provider.get_state()
    """

    def __init__(self, root_dir: str | Path | None = None) -> None:
        if root_dir is None:
            root_dir = Path.cwd()
        self._root = Path(root_dir)
        # P0 fix: 尊重 root_dir 参数，不用全局 get_db_path()（后者忽略 root_dir，
        # 导致传入临时目录的测试和生产多实例隔离失效）
        self._db_path = self._root / "data" / "maop.db"
        self._start_time = time.time()

    def get_state(self) -> DashboardState:
        """Collect current dashboard state from all subsystems."""
        agents = self._collect_agent_status()
        delegations = self._count_delegations()
        success = self._compute_success_rate()
        memory_count = self._count_memory_entries()
        suggestions = self._count_suggestions()

        return DashboardState(
            agents=agents,
            total_delegations=delegations,
            success_rate=success,
            memory_entries=memory_count,
            evolution_suggestions=suggestions,
            uptime_s=round(time.time() - self._start_time, 1),
        )

    def _collect_agent_status(self) -> list[AgentStatus]:
        """Collect agent statuses from config + breaker."""
        agents: list[AgentStatus] = []

        # Try to load agents from config
        try:
            from maop.config.loader import ConfigLoader
            loader = ConfigLoader(project_root=self._root)
            config = loader.load()

            from maop.core.reliability.circuit_breaker import CircuitBreaker
            # P0-3: circuit-breaker truth source is maop.db
            # (circuit_breaker_state table), not circuit-breaker.json.
            breaker = CircuitBreaker(self._db_path)

            for name, adef in config.agents.items():
                entry = breaker.get(name)
                agents.append(AgentStatus(
                    name=name,
                    driver=adef.driver,
                    available=breaker.is_available(name),
                    breaker_state=entry.state.value if entry else "closed",
                    breaker_failures=entry.failures if entry else 0,
                ))
        except Exception as exc:
            # P1 fix: log instead of silently swallowing
            logger.warning("[provider] _collect_agent_status failed: %s", exc)

        return agents

    def _count_delegations(self) -> int:
        """Count total delegations from maop.db. (migrated from delegations.json)"""
        db_path = self._db_path
        if not db_path.exists():
            return 0
        try:
            conn = sqlite3.connect(str(db_path), timeout=5)
            try:
                cursor = conn.execute("SELECT COUNT(*) FROM delegations")
                row = cursor.fetchone()
                return row[0] if row else 0
            finally:
                conn.close()
        except Exception as exc:
            logger.warning("[provider] _count_delegations failed: %s", exc)
            return 0

    def _compute_success_rate(self) -> float:
        """Compute overall success rate from maop.db. (migrated from delegations.json)"""
        db_path = self._db_path
        if not db_path.exists():
            return 0.0
        try:
            conn = sqlite3.connect(str(db_path), timeout=5)
            try:
                cursor = conn.execute("SELECT COUNT(*) FROM delegations")
                total = cursor.fetchone()[0]
                if total == 0:
                    return 0.0
                cursor = conn.execute(
                    "SELECT COUNT(*) FROM delegations WHERE exit_code = 0"
                )
                success = cursor.fetchone()[0]
                return cast(float, round((success / total) * 100, 1))
            finally:
                conn.close()
        except Exception as exc:
            logger.warning("[provider] _compute_success_rate failed: %s", exc)
            return 0.0

    def _count_memory_entries(self) -> int:
        """Count memory entries."""
        entries_dir = self._root / "memory" / "entries"
        if not entries_dir.exists():
            return 0
        try:
            return len(list(entries_dir.glob("*.json")))
        except Exception as exc:
            logger.warning("[provider] _count_memory_entries failed: %s", exc)
            return 0

    # ── Async variants (aiosqlite — non-blocking event-loop I/O) ──

    async def async_get_state(self) -> DashboardState:
        """Async version of get_state — uses aiosqlite for non-blocking DB I/O."""
        agents = self._collect_agent_status()
        delegations = await self._async_count_delegations()
        success = await self._async_compute_success_rate()
        memory_count = self._count_memory_entries()
        suggestions = self._count_suggestions()

        return DashboardState(
            agents=agents,
            total_delegations=delegations,
            success_rate=success,
            memory_entries=memory_count,
            evolution_suggestions=suggestions,
            uptime_s=round(time.time() - self._start_time, 1),
        )

    async def _async_count_delegations(self) -> int:
        """Async count of total delegations from maop.db."""
        db_path = self._db_path
        if not db_path.exists():
            return 0
        try:
            async with aiosqlite.connect(str(db_path), timeout=5) as db:
                cursor = await db.execute("SELECT COUNT(*) FROM delegations")
                row = await cursor.fetchone()
                return row[0] if row else 0
        except Exception as exc:
            logger.warning("[provider] _async_count_delegations failed: %s", exc)
            return 0

    async def _async_compute_success_rate(self) -> float:
        """Async compute of overall success rate from maop.db."""
        db_path = self._db_path
        if not db_path.exists():
            return 0.0
        try:
            async with aiosqlite.connect(str(db_path), timeout=5) as db:
                cursor = await db.execute("SELECT COUNT(*) FROM delegations")
                total = (await cursor.fetchone())[0]
                if total == 0:
                    return 0.0
                cursor = await db.execute(
                    "SELECT COUNT(*) FROM delegations WHERE exit_code = 0"
                )
                success = (await cursor.fetchone())[0]
                return cast(float, round((success / total) * 100, 1))
        except Exception as exc:
            logger.warning("[provider] _async_compute_success_rate failed: %s", exc)
            return 0.0

    def _count_suggestions(self) -> int:
        """Count evolution suggestions."""
        sfile = self._root / "data" / "evolve-suggestions.json"
        if not sfile.exists():
            return 0
        try:
            data = json.loads(sfile.read_text(encoding="utf-8"))
            return len(data) if isinstance(data, list) else 0
        except Exception as exc:
            logger.warning("[provider] _count_suggestions failed: %s", exc)
            return 0

    def get_agent_detail(self, agent_name: str) -> dict[str, Any]:
        """Get detailed info for a specific agent."""
        detail: dict[str, Any] = {"name": agent_name}

        try:
            from maop.config.loader import ConfigLoader
            from maop.core.reliability.circuit_breaker import CircuitBreaker

            config = ConfigLoader(project_root=self._root).load()
            # P0-3: circuit-breaker truth source is maop.db
            # (circuit_breaker_state table), not circuit-breaker.json.
            breaker = CircuitBreaker(self._db_path)

            if agent_name in config.agents:
                adef = config.agents[agent_name]
                detail["driver"] = adef.driver
                detail["cli"] = adef.cli
                detail["timeout_s"] = adef.timeout_s
                detail["capabilities"] = adef.capabilities

            entry = breaker.get(agent_name)
            if entry:
                detail["breaker_state"] = entry.state.value
                detail["breaker_failures"] = entry.failures
                detail["breaker_threshold"] = entry.threshold
        except Exception as exc:
            logger.warning("[provider] get_agent_detail failed: %s", exc)

        return detail

    def get_recent_delegations(self, limit: int = 20) -> list[dict[str, Any]]:
        """Get recent delegation history."""
        log_file = self._root / "logs" / "delegations.json"
        if not log_file.exists():
            return []
        try:
            data = json.loads(log_file.read_text(encoding="utf-8"))
            if isinstance(data, list):
                return data[-limit:]
            return [data]
        except Exception as exc:
            logger.warning("[provider] get_recent_delegations failed: %s", exc)
            return []
