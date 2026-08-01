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
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, cast

from pydantic import BaseModel

from maop.core.db_utils import get_db_path, get_pool

logger = logging.getLogger(__name__)


# ── Models ──────────────────────────────────────────────────────

class ProxyStats(BaseModel):
    """Data bridge statistics."""
    queries: int = 0
    cache_hits: int = 0
    total_latency_ms: float = 0.0


# ── DataProxy ────────────────────────────────────────────────

class DataProxy:
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
            from maop.core.data import MaopDatabase
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
            pass
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

    async def delegation_period_stats(self, now: "datetime | None" = None) -> dict[str, Any]:
        """Compute MoM / YoY trend for delegation volume and success rate.

        The genuine delegation history lives in ``logs/delegations.json``
        (the SQL ``delegations`` table is not populated by the current
        pipeline). This method reads that file and returns, for the trailing
        30-day window (the natural base for 环比/MoM) and the trailing 365-day
        window (同比/YoY):
          - ``total`` / ``success_rate`` for the current window
          - ``delegations_mom`` / ``delegations_yoy`` : % change vs previous
            window (None when the previous window has no data)
          - ``success_rate_mom`` / ``success_rate_yoy`` : percentage-point delta

        Returning None (not 0) for a missing previous period lets the UI skip
        the trend pill instead of showing a misleading 0%.
        """
        import re

        def _parse_ts(s: str) -> "datetime | None":
            if not s:
                return None
            s = str(s).strip()
            if s.endswith("Z"):
                s = s[:-1] + "+00:00"
            m = re.match(r"^(.*\.\d+)([+\-]\d{2}:?\d{2})$", s)
            if m:
                frac = m.group(1)
                dot = frac.rfind(".")
                frac6 = frac[: dot + 1] + frac[dot + 1 : dot + 7].ljust(6, "0")[:6]
                s = frac6 + m.group(2)
            try:
                return datetime.fromisoformat(s)
            except Exception:
                return None

        now_dt = now or datetime.now(timezone.utc)
        empty = {
            "total": 0, "success_rate": 0.0,
            "delegations_mom": None, "delegations_yoy": None,
            "success_rate_mom": None, "success_rate_yoy": None,
        }
        log_path = Path(self._root) / "logs" / "delegations.json"
        if not log_path.exists():
            return empty
        try:
            with open(log_path, encoding="utf-8") as fh:
                records = json.load(fh)
        except Exception as exc:
            logger.warning("[bridge] delegation_period_stats read failed: %s", exc)
            return empty
        if not isinstance(records, list):
            return empty

        from datetime import timedelta

        def _window(since: datetime, until: datetime) -> "tuple[int, float]":
            total = 0
            succ = 0
            for rec in records:
                if not isinstance(rec, dict):
                    continue
                ts = _parse_ts(cast(str, rec.get("timestamp")))
                if ts is None or not (since <= ts < until):
                    continue
                total += 1
                ec = rec.get("exit_code")
                if ec is None:
                    ec = (rec.get("result") or {}).get("exit_code")
                if ec == 0:
                    succ += 1
            rate = round(succ / total * 100, 1) if total else 0.0
            return total, rate

        cur30_s, cur30_e = now_dt - timedelta(days=30), now_dt
        prev30_s, prev30_e = now_dt - timedelta(days=60), now_dt - timedelta(days=30)
        cur365_s, cur365_e = now_dt - timedelta(days=365), now_dt
        prev365_s, prev365_e = now_dt - timedelta(days=730), now_dt - timedelta(days=365)

        cur30_t, cur30_r = _window(cur30_s, cur30_e)
        prev30_t, prev30_r = _window(prev30_s, prev30_e)
        cur365_t, cur365_r = _window(cur365_s, cur365_e)
        prev365_t, prev365_r = _window(prev365_s, prev365_e)

        def _pct(cur: int, prev: int) -> "float | None":
            return round((cur - prev) / prev * 100, 1) if prev else None

        return {
            "total": cur30_t,
            "success_rate": cur30_r,
            "delegations_mom": _pct(cur30_t, prev30_t),
            "delegations_yoy": _pct(cur365_t, prev365_t),
            "success_rate_mom": round(cur30_r - prev30_r, 1) if prev30_r else None,
            "success_rate_yoy": round(cur365_r - prev365_r, 1) if prev365_r else None,
        }

    async def chain(self) -> list[dict[str, Any]]:
        """Fallback chain info — replaces correlation.ps1 -Action chain."""
        start = time.monotonic()

        rows = await self._query_maop(
            "SELECT name, agents, current_index FROM failover_chains"
        )

        self._record_latency(start)
        return rows

    async def memory_stats(self) -> dict[str, Any]:
        """Memory statistics — replaces llm-wiki.ps1 -Action stats."""
        start = time.monotonic()

        entry_count = await self._query_memory("SELECT COUNT(*) as cnt FROM memory_entries")
        trace_count = await self._query_memory("SELECT COUNT(*) as cnt FROM memory_traces")
        traj_count = await self._query_memory("SELECT COUNT(*) as cnt FROM memory_trajectory")
        # 补充 episodic_memory 表统计 (之前缺失)
        try:
            episodic_count = await self._query_memory("SELECT COUNT(*) as cnt FROM episodic_memory")
        except Exception:
            episodic_count = []

        by_agent = await self._query_memory(
            "SELECT agent, COUNT(*) as cnt FROM memory_entries GROUP BY agent ORDER BY cnt DESC"
        )
        by_topic = await self._query_memory(
            "SELECT topic, COUNT(*) as cnt FROM memory_entries GROUP BY topic ORDER BY cnt DESC"
        )

        self._record_latency(start)
        return {
            "total_entries": entry_count[0]["cnt"] if entry_count else 0,
            "total_traces": trace_count[0]["cnt"] if trace_count else 0,
            "total_trajectory_steps": traj_count[0]["cnt"] if traj_count else 0,
            "total_episodic": episodic_count[0]["cnt"] if episodic_count else 0,
            "by_agent": {r["agent"]: r["cnt"] for r in by_agent},
            "by_topic": {r["topic"]: r["cnt"] for r in by_topic},
        }

    async def guardrail_report(self) -> dict[str, Any]:
        """Guardrail report — replaces guardrail.ps1 -Action report."""
        start = time.monotonic()

        # Read guardrail config if available
        config_path = self._root / "config" / "guardrails.yaml"
        rules: list[Any] = []
        if config_path.exists():
            try:
                import yaml
                _text = await asyncio.to_thread(Path(config_path).read_text, encoding="utf-8")
                data = yaml.safe_load(_text)
                rules = data.get("rules", []) if isinstance(data, dict) else []
            except Exception as exc:
                # H1: log instead of silently swallowing
                logger.warning("[bridge] silent except: %s", exc)

        self._record_latency(start)
        return {
            "total_rules": len(rules),
            "rules": rules,
            "status": "active" if rules else "no_rules",
        }

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

    def _queue_stats_sync(self) -> dict[str, int]:
        """Sync queue stats — for run_in_executor."""
        pool = self._pool_queue()
        conn = pool.acquire()
        try:
            rows = conn.execute(
                "SELECT status, COUNT(*) as cnt FROM queue_messages GROUP BY status"
            ).fetchall()
            counts = {r["status"]: r["cnt"] for r in rows}
            dead = conn.execute(
                "SELECT COUNT(*) as cnt FROM queue_dead_letters"
            ).fetchone()["cnt"]
            return {"pending": counts.get("pending", 0), "processing": counts.get("processing", 0), "dead_letters": dead}
        except Exception:
            return {"pending": 0, "processing": 0, "dead_letters": 0}
        finally:
            pool.release(conn)

    async def queue_stats(self) -> dict[str, Any]:
        """Message queue statistics — from queue.db."""
        start = time.monotonic()
        result = await asyncio.get_running_loop().run_in_executor(
            None, self._queue_stats_sync
        )
        self._record_latency(start)
        return result

    # ── New pure-Python endpoints (replacing PS bridge) ──────

    async def tools_stats(self) -> dict[str, Any]:
        """Tool manager statistics — replaces tool-manager.ps1 -Action stats."""
        start = time.monotonic()
        try:
            if self._tool_mgr is None:
                from maop.core.tool_manager import ToolManager
                self._tool_mgr = ToolManager(root_dir=self._root)
            assert self._tool_mgr is not None
            result: dict[str, Any] = self._tool_mgr.stats()
        except Exception as exc:
            logger.warning("[bridge] tools_stats failed: %s", exc)
            result = {"total": 0, "enabled": 0, "disabled": 0, "total_calls": 0}
        self._record_latency(start)
        return result

    async def tools_list(self) -> list[dict[str, Any]]:
        """Tool list — replaces tool-manager.ps1 -Action list."""
        start = time.monotonic()
        try:
            if self._tool_mgr is None:
                from maop.core.tool_manager import ToolManager
                self._tool_mgr = ToolManager(root_dir=self._root)
            assert self._tool_mgr is not None
            result: list[Any] = self._tool_mgr.list()
        except Exception as exc:
            logger.warning("[bridge] tools_list failed: %s", exc)
            result = []
        self._record_latency(start)
        return result

    async def sandbox_list(self) -> list[dict[str, Any]]:
        """Sandbox list — replaces sandbox.ps1 -Action list."""
        start = time.monotonic()
        try:
            if self._sandbox_mgr is None:
                from maop.core.sandbox import SandboxManager
                self._sandbox_mgr = SandboxManager(root_dir=self._root)
            sandboxes = self._sandbox_mgr.list_all()
            result = [s.model_dump() for s in sandboxes]
        except Exception as exc:
            logger.warning("[bridge] sandbox_list failed: %s", exc)
            result = []
        self._record_latency(start)
        return result

    async def human_pending(self) -> dict[str, Any]:
        """Human proxy pending requests — replaces human-proxy.ps1 -Action pending."""
        start = time.monotonic()
        try:
            if self._human_proxy is None:
                from maop.core.human_proxy import HumanProxy
                self._human_proxy = HumanProxy(root_dir=self._root)
            pending = self._human_proxy.pending()
            stats = self._human_proxy.stats()
            result = {
                "pending": [r.model_dump() for r in pending],
                "stats": stats,
            }
        except Exception as exc:
            logger.warning("[bridge] human_pending failed: %s", exc)
            result = {"pending": [], "stats": {}}
        self._record_latency(start)
        return result

    async def prompts_list(self) -> dict[str, Any]:
        """Prompt templates — replaces prompt-manager.ps1 -Action list."""
        start = time.monotonic()
        try:
            from maop.prompt_manager import PromptManager
            mgr = PromptManager(root_dir=self._root)
            templates = mgr.list_templates()
            stats = mgr.stats()
            result = {
                "templates": [t.model_dump() for t in templates],
                "stats": stats,
            }
        except Exception as exc:
            logger.warning("[bridge] prompts_list failed: %s", exc)
            result = {"templates": [], "stats": {}}
        self._record_latency(start)
        return result

    async def coordination_report(self) -> dict[str, Any]:
        """Coordination/teams report — pure Python from queue.db + config."""
        start = time.monotonic()
        try:
            queue = await self.queue_stats()
            config_agents = await self._query_maop(
                "SELECT agent, COUNT(*) as cnt FROM delegations GROUP BY agent"
            )
            # Build teams from agent config groups
            teams = []
            try:
                from maop.config.loader import ConfigLoader
                cfg = ConfigLoader(project_root=str(self._root)).load()
                groups: dict[str, list[str]] = {}
                for name, ad in cfg.agents.items():
                    group = getattr(ad, "group", "default")
                    groups.setdefault(group, []).append(name)
                teams = [{"team": k, "agents": v, "count": len(v)} for k, v in groups.items()]
            except Exception as exc:
                # H1: log instead of silently swallowing
                logger.warning("[bridge] silent except: %s", exc)
            result = {
                "queue": queue,
                "active_agents": [r["agent"] for r in config_agents],
                "teams": teams,
            }
        except Exception as exc:
            logger.warning("[bridge] coordination_report failed: %s", exc)
            result = {"queue": {}, "active_agents": [], "teams": []}
        self._record_latency(start)
        return result

    async def skills_list(self) -> list[dict[str, Any]]:
        """Skills list — derived from tool_manager registry."""
        start = time.monotonic()
        try:
            from maop.core.tool_manager import ToolManager
            mgr = ToolManager(root_dir=self._root)
            tools = mgr.list()
            result = []
            for cat_group in tools:
                if cat_group.get("category") in ("skill", "skills"):
                    result.extend(cat_group.get("tools", []))
        except Exception as exc:
            logger.warning("[bridge] skills_list failed: %s", exc)
            result = []
        self._record_latency(start)
        return result

    async def versions_check(self) -> dict[str, Any]:
        """Version check — returns real MAOP version from package."""
        start = time.monotonic()
        try:
            from maop import __version__ as MAOP_ver
        except ImportError:
            MAOP_ver = "unknown"
        import sys as _sys
        result = {
            "MAOP_VERSION": MAOP_ver,
            "python": _sys.version.split()[0],
            "ps_bridge_active": False,
            "checked_at": datetime.now(timezone.utc).isoformat(),
        }
        self._record_latency(start)
        return result

    async def providers_report(self) -> dict[str, Any]:
        """Providers report — agent availability from circuit breaker."""
        start = time.monotonic()
        try:
            agents = await self.agent_stats()
            result = {
                "agents": agents,
                "total": len(agents),
                "available": sum(1 for a in agents if a.get("circuit_breaker") == "closed"),
            }
        except Exception as exc:
            logger.warning("[bridge] providers_report failed: %s", exc)
            result = {"agents": [], "total": 0, "available": 0}
        self._record_latency(start)
        return result

    async def mcp_servers(self) -> list[dict[str, Any]]:
        """MCP servers list — from config if available."""
        start = time.monotonic()
        mcp_config = self._root / "config" / "mcp_servers.yaml"
        result = []
        if mcp_config.exists():
            try:
                import yaml
                data = yaml.safe_load(mcp_config.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    result = data.get("servers", [])
            except Exception as exc:
                # H1: log instead of silently swallowing
                logger.warning("[bridge] silent except: %s", exc)
        self._record_latency(start)
        return result

    async def mcp_tools(self) -> list[dict[str, Any]]:
        """MCP tools list — from config if available."""
        start = time.monotonic()
        mcp_config = self._root / "config" / "mcp_servers.yaml"
        result = []
        if mcp_config.exists():
            try:
                import yaml
                data = yaml.safe_load(mcp_config.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    for server in data.get("servers", []):
                        if isinstance(server, dict):
                            result.extend(server.get("tools", []))
            except Exception as exc:
                # H1: log instead of silently swallowing
                logger.warning("[bridge] silent except: %s", exc)
        self._record_latency(start)
        return result

    async def graph_nodes(self) -> list[dict[str, Any]]:
        """Memory graph nodes — from memory.db."""
        start = time.monotonic()
        result = await self._query_memory(
            "SELECT agent as id, agent as label, COUNT(*) as weight "
            "FROM memory_entries GROUP BY agent ORDER BY weight DESC"
        )
        self._record_latency(start)
        return result

    async def graph_edges(self) -> list[dict[str, Any]]:
        """Memory graph edges — from memory_traces."""
        start = time.monotonic()
        result = await self._query_memory(
            "SELECT trace_id as source, agent as target, COUNT(*) as weight "
            "FROM memory_traces GROUP BY trace_id, agent ORDER BY weight DESC LIMIT 100"
        )
        self._record_latency(start)
        return result

    async def logs_get(self, name: str = "dashboard", limit: int = 50) -> list[dict[str, Any]]:
        """Get log entries for the named log stream.

        Routes by ``name`` to the correct source. Previously every call
        returned the ``error_log`` table regardless of ``name``, so
        ``logs_get(name="delegations")`` returned the wrong data.

        * ``delegations`` → ``logs/delegations.json`` (the genuine delegation history)
        * ``checker``     → ``logs/checker_*.log`` parsed into structured entries
        * anything else (default ``dashboard``) → ``error_log`` table
        """
        start = time.monotonic()
        if name == "delegations":
            result = await asyncio.to_thread(self._read_delegations_json, limit)
        elif name == "checker":
            result = await asyncio.to_thread(self._read_checker_logs, limit)
        else:
            result = await self._query_maop(
                "SELECT * FROM error_log ORDER BY timestamp DESC LIMIT ?",
                (limit,),
            )
        self._record_latency(start)
        return result

    # ── log readers ───────────────────────────────────────────

    def _read_delegations_json(self, limit: int) -> list[dict[str, Any]]:
        """Read the genuine delegation history from logs/delegations.json."""
        path = self._root / "logs" / "delegations.json"
        if not path.exists():
            return []
        try:
            data = json.loads(path.read_text(encoding="utf-8", errors="replace"))
        except Exception:
            return []
        if not isinstance(data, list):
            return []
        if limit and limit > 0:
            return data[-limit:]
        return data

    def _read_checker_logs(self, limit: int) -> list[dict[str, Any]]:
        """Read and parse checker log files into structured entries."""
        log_dir = self._root / "logs"
        if not log_dir.is_dir():
            return []
        files = sorted(log_dir.glob("checker_*.log"), reverse=True)
        entries: list[dict[str, Any]] = []
        _log_re = re.compile(
            r"^\[(?P<ts>\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2})(?:\.\d+)?\]\s*"
            r"\[(?P<agent>[^\]]+)\]\s*"
            r"(?P<level>\w+):?\s*"
            r"(?P<msg>.*)$"
        )
        for f in files:
            try:
                text = f.read_text(encoding="utf-8", errors="replace")
            except Exception:
                continue
            for raw in text.splitlines():
                line = raw.rstrip("\r\n")
                if not line:
                    continue
                m = _log_re.match(line)
                if m:
                    entries.append({
                        "ts": m.group("ts"),
                        "level": (m.group("level") or "info").lower(),
                        "agent": m.group("agent") or "checker",
                        "msg": m.group("msg") or line,
                    })
                else:
                    entries.append({"ts": None, "level": "info", "agent": "checker", "msg": line})
                if limit and len(entries) >= limit:
                    break
            if limit and len(entries) >= limit:
                break
        return entries[:limit] if limit else entries

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
