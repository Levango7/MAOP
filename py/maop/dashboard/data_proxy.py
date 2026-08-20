"""MAOP Dashboard Data Bridge — Pure Python replacement for PS script calls.

Replaces the PowerShell subprocess bridge (_invoke_ps) with direct
Python calls to MAOP's SQLite-backed modules. Eliminates ~800ms
cold-start overhead per PS invocation.

Each method corresponds to a former PS script endpoint and returns
the same JSON structure for backward compatibility.

Usage::

    bridge = DataProxy(root_dir="/path/to/MAOP")
    report = await bridge.report(hours=48)
    agents = await bridge.agent_stats()
    live = await bridge.live()

All endpoints use SQLite-backed Python queries — the Python layer
    runs independently of the legacy PS engine.

The class is split into focused Mixins under :mod:`maop.dashboard.channels`:

* :class:`~maop.dashboard.channels.skills.SkillsMixin`     — skills/versions
* :class:`~maop.dashboard.channels.mcp.McpMixin`           — tools/sandbox/MCP
* :class:`~maop.dashboard.channels.models.ModelsMixin`     — memory/guardrail/providers/graph
* :class:`~maop.dashboard.channels.prompts.PromptsMixin`   — prompts/human/coordination
* :class:`~maop.dashboard.channels.routing.RoutingMixin`   — delegation/chain/queue
* :class:`~maop.dashboard.channels.security.SecurityMixin` — logs
"""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from maop.core.backends.db_utils import get_db_path, get_pool
from maop.dashboard.channels.mcp import McpMixin
from maop.dashboard.channels.models import ModelsMixin
from maop.dashboard.channels.prompts import PromptsMixin
from maop.dashboard.channels.routing import RoutingMixin
from maop.dashboard.channels.security import SecurityMixin
from maop.dashboard.channels.skills import SkillsMixin

logger = logging.getLogger(__name__)


# ── Models ──────────────────────────────────────────────────────

class ProxyStats(BaseModel):
    """Data bridge statistics."""
    queries: int = 0
    cache_hits: int = 0
    total_latency_ms: float = 0.0


# ── DataProxy ────────────────────────────────────────────────

class DataProxy(
    SkillsMixin,
    McpMixin,
    ModelsMixin,
    PromptsMixin,
    RoutingMixin,
    SecurityMixin,
):
    """Pure Python data bridge for Dashboard — replaces PS scripts.

    Parameters
    ----------
    root_dir : str | Path
        MAOP project root directory.
    """

    def __init__(
        self,
        root_dir: str | Path,
    ) -> None:
        self._root = Path(root_dir)
        self._data_dir = self._root / "data"
        self._queries = 0
        self._cache_hits = 0
        self._total_latency = 0.0
        self._tool_mgr: Any = None
        self._sandbox_mgr: Any = None
        self._human_proxy: Any = None

        self._ensure_db_schema()

    def _ensure_db_schema(self) -> None:
        """Create maop.db tables if they don't exist (idempotent)."""
        try:
            from maop.core.backends.data import MaopDatabase
            db = MaopDatabase(get_db_path())
            db.init()
        except Exception as exc:
            # H1 (2026-07-22, Phase H): log the exception instead of silently
            # swallowing it, so schema-init failures surface in logs and are
            # debuggable. Still non-fatal: the bridge degrades to empty queries.
            logger.warning("[bridge] _ensure_db_schema failed: %s", exc)

    # ── Connection helpers (pool-based) ────────────────────────

    def _pool_maop(self):
        return get_pool(get_db_path())

    def _pool_memory(self):
        return get_pool(get_db_path("memory"))

    def _pool_queue(self):
        return get_pool(get_db_path("queue"))

    def _query_maop_sync(self, sql: str, params: tuple = ()) -> list[dict]:
        """Execute a SELECT on maop.db (sync — for run_in_executor)."""
        pool = self._pool_maop()
        conn = pool.acquire()
        try:
            cursor = conn.execute(sql, params)
            cols = [d[0] for d in cursor.description]
            return [dict(zip(cols, row)) for row in cursor.fetchall()]
        except Exception as exc:
            logger.warning("[bridge] maop.db query failed: %s", exc)
            return []
        finally:
            pool.release(conn)

    async def _query_maop(self, sql: str, params: tuple = ()) -> list[dict]:
        """Async wrapper — runs _query_maop_sync in thread executor."""
        return await asyncio.get_running_loop().run_in_executor(
            None, self._query_maop_sync, sql, params
        )

    def _query_memory_sync(self, sql: str, params: tuple = ()) -> list[dict]:
        """Execute a SELECT on memory.db (sync — for run_in_executor)."""
        pool = self._pool_memory()
        conn = pool.acquire()
        try:
            cursor = conn.execute(sql, params)
            cols = [d[0] for d in cursor.description]
            return [dict(zip(cols, row)) for row in cursor.fetchall()]
        except Exception as exc:
            logger.warning("[bridge] memory.db query failed: %s", exc)
            return []
        finally:
            pool.release(conn)

    async def _query_memory(self, sql: str, params: tuple = ()) -> list[dict]:
        """Async wrapper — runs _query_memory_sync in thread executor."""
        return await asyncio.get_running_loop().run_in_executor(
            None, self._query_memory_sync, sql, params
        )

    # ── Data endpoints (replacing PS scripts) ─────────────────

    async def report(self, hours: int = 48) -> dict[str, Any]:
        """Correlation report — replaces correlation.ps1 -Action report.

        Returns delegation stats, success rates, and agent breakdown.
        """
        start = time.monotonic()
        since = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()

        rows = await self._query_maop(
            "SELECT agent, exit_code, COUNT(*) as cnt "
            "FROM delegations WHERE timestamp >= ? "
            "GROUP BY agent, exit_code ORDER BY agent",
            (since,),
        )

        by_agent: dict[str, dict] = {}
        total = 0
        successes = 0
        for r in rows:
            agent = r["agent"]
            if agent not in by_agent:
                by_agent[agent] = {"total": 0, "success": 0, "failure": 0}
            by_agent[agent]["total"] += r["cnt"]
            total += r["cnt"]
            if r["exit_code"] == 0:
                by_agent[agent]["success"] += r["cnt"]
                successes += r["cnt"]
            else:
                by_agent[agent]["failure"] += r["cnt"]

        self._record_latency(start)
        return {
            "hours": hours,
            "total_delegations": total,
            # success_rate: 0-100 percentage (unified with provider.py)
            "success_rate": round((successes / total) * 100, 1) if total > 0 else 0.0,
            "by_agent": by_agent,
            "since": since,
        }

    async def agent_stats(self) -> list[dict[str, Any]]:
        """Agent statistics — replaces correlation.ps1 -Action agent-stats.

        Returns per-agent stats with circuit-breaker state.
        """
        start = time.monotonic()

        # Get delegation counts
        rows = await self._query_maop(
            "SELECT agent, "
            "COUNT(*) as total, "
            "SUM(CASE WHEN exit_code = 0 THEN 1 ELSE 0 END) as successes, "
            "AVG(CASE WHEN exit_code = 0 THEN 1.0 ELSE 0.0 END) as success_rate "
            "FROM delegations GROUP BY agent ORDER BY total DESC"
        )

        # Get circuit-breaker states
        cb_rows = await self._query_maop(
            "SELECT agent, state, failures, threshold FROM circuit_breaker_state"
        )
        cb_map = {r["agent"]: r for r in cb_rows}

        result = []
        seen_agents = set()
        for r in rows:
            agent = r["agent"]
            seen_agents.add(agent)
            cb = cb_map.get(agent, {})
            result.append({
                "agent": agent,
                "total_delegations": r["total"],
                "successes": r["successes"],
                "success_rate": round((r["success_rate"] or 0) * 100, 1),
                "circuit_breaker": cb.get("state", "unknown"),
                "failures": cb.get("failures", 0),
                "threshold": cb.get("threshold", 5),
            })

        # Fall back to config: add agents not in delegations with zero stats
        try:
            from maop.config.loader import ConfigLoader
            cfg = ConfigLoader(project_root=str(self._root)).load()
            for name in cfg.agents:
                if name not in seen_agents:
                    cb = cb_map.get(name, {})
                    result.append({
                        "agent": name,
                        "total_delegations": 0,
                        "successes": 0,
                        "success_rate": 0.0,
                        "circuit_breaker": cb.get("state", "closed"),
                        "failures": cb.get("failures", 0),
                        "threshold": cb.get("threshold", 5),
                    })
        except Exception as exc:
            logger.warning("[bridge] agent_stats config fallback failed: %s", exc)

        self._record_latency(start)
        return result

    async def live(self) -> dict[str, Any]:
        """Live status — replaces correlation.ps1 -Action live.

        Returns current system state snapshot.
        """
        start = time.monotonic()

        # Recent delegations (last 5 min)
        since = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()
        recent = await self._query_maop(
            "SELECT agent, task, exit_code, duration_ms, timestamp "
            "FROM delegations WHERE timestamp >= ? "
            "ORDER BY timestamp DESC LIMIT 20",
            (since,),
        )

        # Open circuit breakers
        open_breakers = await self._query_maop(
            "SELECT agent, state, failures FROM circuit_breaker_state "
            "WHERE state = 'open'"
        )

        # Error log (last 10)
        errors = await self._query_maop(
            "SELECT agent, error, timestamp FROM error_log "
            "ORDER BY timestamp DESC LIMIT 10"
        )

        # Frontend contract (Monitor.vue): add requests_per_min, queue_depth,
        # cost_per_hour, agents[] while keeping legacy fields for backward compat.
        requests_per_min = round(len(recent) / 5.0, 1) if recent else 0.0
        try:
            qstats = await asyncio.get_event_loop().run_in_executor(
                None, self._queue_stats_sync
            )
            queue_depth = qstats.get("pending", 0)
        except Exception:
            queue_depth = 0
        try:
            agent_rows = await self.agent_stats()
            agents_list = [
                {
                    "name": a.get("agent", ""),
                    "healthy": a.get("circuit_breaker", "closed") == "closed",
                    "queue": queue_depth,
                    "load": min(100, max(0, round(100 - a.get("success_rate", 100.0)))),
                }
                for a in agent_rows
            ]
        except Exception:
            agents_list = []
        cost_per_hour = 0.0
        try:
            from maop.core.cost_tracker import CostTracker
            ct = CostTracker(root_dir=str(self._root))
            hour_ago = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
            csum = ct.summary(start_date=hour_ago)
            cost_per_hour = round(getattr(csum, "total_cost_usd", 0.0), 4)
        except Exception:
            logger.debug('swallowed exception', exc_info=True)
        self._record_latency(start)
        return {
            "recent_delegations": recent,
            "open_circuit_breakers": open_breakers,
            "recent_errors": errors,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "requests_per_min": requests_per_min,
            "queue_depth": queue_depth,
            "cost_per_hour": cost_per_hour,
            "agents": agents_list,
        }

    async def failures(self, hours: int = 24) -> list[dict[str, Any]]:
        """Failure report — replaces correlation.ps1 -Action failures."""
        start = time.monotonic()
        since = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()

        rows = await self._query_maop(
            "SELECT agent, task, exit_code, error, duration_ms, timestamp "
            "FROM delegations WHERE exit_code != 0 AND timestamp >= ? "
            "ORDER BY timestamp DESC LIMIT 50",
            (since,),
        )

        self._record_latency(start)
        return rows

    async def timeseries(self, hours: int = 168) -> list[dict[str, Any]]:
        """Time series data — replaces correlation.ps1 -Action timeseries.

        Returns hourly aggregation of delegations.
        """
        start = time.monotonic()
        since = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()

        rows = await self._query_maop(
            "SELECT "
            "  strftime('%Y-%m-%dT%H:00:00', timestamp) as hour, "
            "  COUNT(*) as total, "
            "  SUM(CASE WHEN exit_code = 0 THEN 1 ELSE 0 END) as successes, "
            "  AVG(duration_ms) as avg_duration_ms "
            "FROM delegations WHERE timestamp >= ? "
            "GROUP BY hour ORDER BY hour",
            (since,),
        )

        self._record_latency(start)
        return rows

    async def snapshot(self) -> dict[str, Any]:
        """Aggregate system snapshot for SSE/streaming endpoints.

        F-P0-1 fix: stream.py calls bridge.snapshot() but this method
        was missing, causing SSE to silently fail. Returns fields
        expected by frontend (Overview.vue, Monitor.vue):
        agents_count, healthy_agents, total_agents, memory_usage_pct,
        cpu_pct, queue_health_pct, active_streams, success_rate,
        delegations.
        """
        start = time.monotonic()
        try:
            live_data = await self.live()
            agents = await self.agent_stats()
        except Exception as exc:
            logger.warning("[bridge] snapshot aggregation failed: %s", exc)
            return {}

        total = len(agents)
        healthy = sum(1 for a in agents if a.get("circuit_breaker", "closed") == "closed")
        recent_delegations = live_data.get("recent_delegations", [])
        total_delegations = len(recent_delegations)
        successes = sum(1 for d in recent_delegations if d.get("exit_code") == 0)
        success_rate = (successes / total_delegations * 100) if total_delegations else 100.0

        # Queue health: ratio of pending to total (lower = healthier)
        try:
            qstats = await asyncio.get_event_loop().run_in_executor(
                None, self._queue_stats_sync
            )
            pending = qstats.get("pending", 0)
            queue_health = max(0, 100 - pending * 5)
        except Exception:
            queue_health = 100

        # Memory usage (approximate from memory_stats)
        try:
            mem = await self.memory_stats()
            memory_pct = mem.get("usage_pct", 0)
        except Exception:
            memory_pct = 0

        self._record_latency(start)
        return {
            "agents_count": total,
            "healthy_agents": healthy,
            "total_agents": total,
            "memory_usage_pct": memory_pct,
            "cpu_pct": 0,  # CPU not tracked in DB; placeholder
            "queue_health_pct": queue_health,
            "active_streams": 0,  # Updated by streaming registry
            "success_rate": round(success_rate, 1),
            "delegations": recent_delegations[:10],
            "timestamp": live_data.get("timestamp", ""),
        }

    # ── Internal ─────────────────────────────────────────────

    def _record_latency(self, start: float) -> None:
        self._queries += 1
        self._total_latency += (time.monotonic() - start) * 1000

    def stats(self) -> ProxyStats:
        """Get bridge statistics."""
        return ProxyStats(
            queries=self._queries,
            cache_hits=self._cache_hits,
            total_latency_ms=round(self._total_latency, 1),
        )

    def __repr__(self) -> str:
        return f"DataProxy(queries={self._queries})"
