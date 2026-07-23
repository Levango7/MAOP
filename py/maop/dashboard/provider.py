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

import asyncio
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

            from maop.core.circuit_breaker import CircuitBreaker
            # P0-3: circuit-breaker truth source is maop.db
            # (circuit_breaker_state table), not circuit-breaker.json.
            breaker = CircuitBreaker(self._root / "data" / "maop.db")

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
        db_path = self._root / "data" / "maop.db"
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
        db_path = self._root / "data" / "maop.db"
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
        db_path = self._root / "data" / "maop.db"
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
        db_path = self._root / "data" / "maop.db"
        if not db_path.exists():
            return 0.0
        try:
            async with aiosqlite.connect(str(db_path), timeout=5) as db:
                cursor = await db.execute("SELECT COUNT(*) FROM delegations")
                total = (await cursor.fetchone())[0]  # type: ignore[index]
                if total == 0:
                    return 0.0
                cursor = await db.execute(
                    "SELECT COUNT(*) FROM delegations WHERE exit_code = 0"
                )
                success = (await cursor.fetchone())[0]  # type: ignore[index]
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
            from maop.core.circuit_breaker import CircuitBreaker

            config = ConfigLoader(project_root=self._root).load()
            # P0-3: circuit-breaker truth source is maop.db
            # (circuit_breaker_state table), not circuit-breaker.json.
            breaker = CircuitBreaker(self._root / "data" / "maop.db")

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


# ── FastAPI app (optional — only if fastapi installed) ────────

def create_app(root_dir: str | Path | None = None) -> Any:
    """Deprecated: use maop.dashboard.server.app instead.

    This function is retained only for backward compatibility with tests.
    It creates an isolated FastAPI app with routes that conflict with
    the main server.py routes. Do not use in production.

    .. deprecated:: 4.0.0
        Use ``maop.dashboard.server:app`` for all production use.
    """
    import warnings
    warnings.warn(
        "create_app() is deprecated and will be removed in v4.1. "
        "Use maop.dashboard.server:app instead.",
        DeprecationWarning,
        stacklevel=2,
    )
    """Create FastAPI dashboard application.

    Returns None if FastAPI is not installed.
    """
    try:
        from fastapi import FastAPI
        from fastapi.responses import HTMLResponse, StreamingResponse
    except ImportError:
        return None

    provider = DashboardProvider(root_dir=root_dir)
    app = FastAPI(title="MAOP Dashboard", version="1.0.0")

    @app.get("/")
    async def index():
        """P2-8 fix: redirect to Vue3 SPA instead of rendering deprecated HTML."""
        from fastapi.responses import RedirectResponse
        return RedirectResponse(url="/index.html")

    @app.get("/api/state")
    async def api_state():
        state = await provider.async_get_state()
        return state.model_dump()

    @app.get("/api/agents/{agent_name}")
    async def api_agent_detail(agent_name: str):
        return await asyncio.get_running_loop().run_in_executor(
            None, provider.get_agent_detail, agent_name
        )

    @app.get("/api/delegations")
    async def api_delegations(limit: int = 20):
        return await asyncio.get_running_loop().run_in_executor(
            None, provider.get_recent_delegations, limit
        )

    @app.get("/api/stream")
    async def api_stream():
        """SSE endpoint for live updates."""
        from maop.concurrency import SSEStreamer
        streamer = SSEStreamer()

        async def generate():
            while True:
                state = await provider.async_get_state()
                streamer.send_json(
                    event="state",
                    agents=len(state.agents),
                    success_rate=state.success_rate,
                    delegations=state.total_delegations,
                )
                async for chunk in streamer.stream():
                    yield chunk
                await asyncio.sleep(2)

        return StreamingResponse(generate(), media_type="text/event-stream")

    return app


def _render_html(state: DashboardState) -> str:
    """Render minimal dashboard HTML.

    .. deprecated:: 4.0.0
       This function is the v3.x-era static HTML renderer. As of P2-8c fix,
       ``create_app()`` no longer calls this function (it redirects to the
       Vue3 SPA instead). The function is retained solely for backward
       compatibility with test_provider.py and test_phase7.py.

       Emits a DeprecationWarning on every call.
    """
    import warnings as _w
    _w.warn(
        "_render_html is deprecated since v4.0.0; the frontend is now a "
        "unified Vue3 SPA. See the docstring for the migration path.",
        DeprecationWarning,
        stacklevel=2,
    )
    # Original implementation retained unchanged below.
    agent_rows = ""
    for a in state.agents:
        status_color = "#4caf50" if a.available else "#f44336"
        agent_rows += f"""
        <tr>
            <td>{a.name}</td>
            <td>{a.driver}</td>
            <td style="color:{status_color}">{a.breaker_state}</td>
            <td>{a.breaker_failures}</td>
        </tr>"""

    return f"""<!DOCTYPE html>
<html><head><title>MAOP Dashboard</title>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<style>
body{{font-family:system-ui;margin:20px;background:#1a1a2e;color:#eee}}
h1{{color:#e94560}}table{{border-collapse:collapse;width:100%}}
th,td{{border:1px solid #333;padding:8px;text-align:left}}
th{{background:#16213e}}.metric{{display:inline-block;margin:10px;padding:15px;background:#16213e;border-radius:8px}}
.metric .val{{font-size:2em;color:#e94560}}.metric .label{{color:#999;font-size:0.8em}}
</style></head><body>
<h1>MAOP Dashboard</h1>
<div class="metric"><div class="val">{state.total_delegations}</div><div class="label">Delegations</div></div>
<div class="metric"><div class="val">{state.success_rate}%</div><div class="label">Success Rate</div></div>
<div class="metric"><div class="val">{state.memory_entries}</div><div class="label">Memory Entries</div></div>
<div class="metric"><div class="val">{state.evolution_suggestions}</div><div class="label">Suggestions</div></div>
<div class="metric"><div class="val">{state.uptime_s}s</div><div class="label">Uptime</div></div>
<h2>Agents</h2>
<table><tr><th>Name</th><th>Driver</th><th>Breaker</th><th>Failures</th></tr>
{agent_rows}</table>
<script>
const es=new EventSource("/api/stream");
es.onmessage=e=>{{if(e.data)document.querySelector(".metric .val").textContent=e.data}};
</script></body></html>"""
