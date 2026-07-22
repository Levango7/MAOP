"""MAOP Agent Registry — Unified agent registration, health probes, and connectivity.

Builds on AgentScanner for discovery, adds:
  - Health probes (ping CLI with --version or --help)
  - Connectivity status tracking
  - Agent enable/disable
  - Sync with agents.yaml config
  - CircuitBreaker integration for auto-degradation
"""

from __future__ import annotations

import logging
import sqlite3
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from maop.core.db_utils import get_db_path, sqlite_connect

logger = logging.getLogger(__name__)


class HealthStatus(str):
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    UNHEALTHY = "unhealthy"
    UNKNOWN = "unknown"


class HealthCheckResult(BaseModel):
    agent_name: str
    healthy: bool = False
    latency_ms: int = 0
    version: str = ""
    error: str = ""
    checked_at: str = ""


class RegisteredAgent(BaseModel):
    name: str
    cli_path: str = ""
    version: str = ""
    provider: str = ""
    capabilities: list[str] = Field(default_factory=list)
    description: str = ""
    model: str = ""
    driver: str = "cli"
    cli_args: str = ""
    timeout_s: int = 120
    enabled: bool = True
    health: str = "unknown"
    last_health_check: str = ""
    last_latency_ms: int = 0
    consecutive_failures: int = 0
    registered_at: str = ""
    source: str = "scanned"


class AgentRegistry:
    """Unified agent registry with health monitoring.

    Features:
      - Register/unregister agents (from scanner or manual)
      - Periodic health probes
      - Enable/disable agents
      - Track consecutive failures for auto-degradation
      - Sync with agents.yaml
      - Query by capability, provider, health status
    """

    def __init__(self, root_dir: str | Path = "data") -> None:
        self._root = Path(root_dir)
        self._db_path = get_db_path("agent_registry")
        self._init_db()

    def _init_db(self) -> None:
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite_connect(self._db_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS registered_agents (
                    name TEXT PRIMARY KEY,
                    cli_path TEXT DEFAULT '',
                    version TEXT DEFAULT '',
                    provider TEXT DEFAULT '',
                    capabilities TEXT DEFAULT '[]',
                    description TEXT DEFAULT '',
                    model TEXT DEFAULT '',
                    driver TEXT DEFAULT 'cli',
                    cli_args TEXT DEFAULT '',
                    timeout_s INTEGER DEFAULT 120,
                    enabled INTEGER DEFAULT 1,
                    health TEXT DEFAULT 'unknown',
                    last_health_check TEXT DEFAULT '',
                    last_latency_ms INTEGER DEFAULT 0,
                    consecutive_failures INTEGER DEFAULT 0,
                    registered_at TEXT DEFAULT '',
                    source TEXT DEFAULT 'scanned'
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS health_log (
                    id TEXT PRIMARY KEY,
                    agent_name TEXT NOT NULL,
                    healthy INTEGER DEFAULT 0,
                    latency_ms INTEGER DEFAULT 0,
                    version TEXT DEFAULT '',
                    error TEXT DEFAULT '',
                    checked_at TEXT NOT NULL
                )
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_health_log_agent
                ON health_log(agent_name, checked_at)
            """)

    def register(self, agent: RegisteredAgent) -> RegisteredAgent:
        if not agent.registered_at:
            agent.registered_at = datetime.now(timezone.utc).isoformat()
        self._upsert_db(agent)
        logger.info("[registry] Registered agent '%s' (provider=%s)", agent.name, agent.provider)
        return agent

    def unregister(self, name: str) -> bool:
        with sqlite_connect(self._db_path) as conn:
            cursor = conn.execute("DELETE FROM registered_agents WHERE name=?", (name,))
        return cursor.rowcount > 0

    def enable(self, name: str) -> bool:
        with sqlite_connect(self._db_path) as conn:
            cursor = conn.execute("UPDATE registered_agents SET enabled=1 WHERE name=?", (name,))
        return cursor.rowcount > 0

    def disable(self, name: str) -> bool:
        with sqlite_connect(self._db_path) as conn:
            cursor = conn.execute("UPDATE registered_agents SET enabled=0 WHERE name=?", (name,))
        return cursor.rowcount > 0

    def health_check(self, name: str) -> HealthCheckResult:
        agent = self.get_agent(name)
        if agent is None:
            return HealthCheckResult(agent_name=name, error="Agent not found")

        if not agent.cli_path:
            result = HealthCheckResult(
                agent_name=name, healthy=False,
                error="No CLI path configured",
                checked_at=datetime.now(timezone.utc).isoformat(),
            )
            self._update_health(agent, result)
            return result

        start = time.monotonic()
        try:
            proc = subprocess.run(
                [agent.cli_path, "--version"],
                capture_output=True, text=True, timeout=10,
                creationflags=subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0,
            )
            latency_ms = int((time.monotonic() - start) * 1000)
            version_out = (proc.stdout or proc.stderr or "").strip().split("\n")[0][:80]

            healthy = proc.returncode == 0 or proc.returncode == 1
            result = HealthCheckResult(
                agent_name=name, healthy=healthy,
                latency_ms=latency_ms, version=version_out,
                checked_at=datetime.now(timezone.utc).isoformat(),
            )
        except subprocess.TimeoutExpired:
            latency_ms = int((time.monotonic() - start) * 1000)
            result = HealthCheckResult(
                agent_name=name, healthy=False,
                latency_ms=latency_ms, error="Timeout (10s)",
                checked_at=datetime.now(timezone.utc).isoformat(),
            )
        except FileNotFoundError:
            result = HealthCheckResult(
                agent_name=name, healthy=False,
                error="CLI not found at path",
                checked_at=datetime.now(timezone.utc).isoformat(),
            )
        except Exception as exc:
            result = HealthCheckResult(
                agent_name=name, healthy=False,
                error=str(exc)[:200],
                checked_at=datetime.now(timezone.utc).isoformat(),
            )

        self._update_health(agent, result)
        return result

    def health_check_all(self) -> list[HealthCheckResult]:
        agents = self.list_agents(enabled_only=True)
        results = []
        for agent in agents:
            results.append(self.health_check(agent.name))
        return results

    def _update_health(self, agent: RegisteredAgent, result: HealthCheckResult) -> None:
        if result.healthy:
            new_health = "healthy"
            failures = 0
        else:
            failures = agent.consecutive_failures + 1
            new_health = "degraded" if failures < 3 else "unhealthy"

        with sqlite_connect(self._db_path) as conn:
            conn.execute(
                """UPDATE registered_agents
                   SET health=?, last_health_check=?, last_latency_ms=?,
                       consecutive_failures=?, version=?
                   WHERE name=?""",
                (new_health, result.checked_at, result.latency_ms,
                 failures, result.version, agent.name),
            )
            import uuid
            conn.execute(
                """INSERT INTO health_log (id, agent_name, healthy, latency_ms, version, error, checked_at)
                   VALUES (?,?,?,?,?,?,?)""",
                (f"hc-{uuid.uuid4().hex[:8]}", agent.name,
                 1 if result.healthy else 0, result.latency_ms,
                 result.version, result.error, result.checked_at),
            )

    def get_agent(self, name: str) -> RegisteredAgent | None:
        with sqlite_connect(self._db_path) as conn:
            row = conn.execute("SELECT * FROM registered_agents WHERE name=?", (name,)).fetchone()
        if row is None:
            return None
        return self._row_to_agent(row)

    def list_agents(
        self,
        *,
        enabled_only: bool = False,
        healthy_only: bool = False,
        capability: str = "",
        provider: str = "",
    ) -> list[RegisteredAgent]:
        clauses = []
        params: list[Any] = []
        if enabled_only:
            clauses.append("enabled=1")
        if healthy_only:
            clauses.append("health IN ('healthy','degraded')")
        if capability:
            clauses.append("capabilities LIKE ?")
            params.append(f'%"{capability}"%')
        if provider:
            clauses.append("provider=?")
            params.append(provider)

        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        with sqlite_connect(self._db_path) as conn:
            rows = conn.execute(
                f"SELECT * FROM registered_agents {where} ORDER BY name",
                params,
            ).fetchall()
        return [self._row_to_agent(r) for r in rows]

    def get_health_log(self, agent_name: str = "", limit: int = 50) -> list[dict]:
        with sqlite_connect(self._db_path) as conn:
            if agent_name:
                rows = conn.execute(
                    "SELECT * FROM health_log WHERE agent_name=? ORDER BY checked_at DESC LIMIT ?",
                    (agent_name, limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM health_log ORDER BY checked_at DESC LIMIT ?",
                    (limit,),
                ).fetchall()
        return [dict(r) for r in rows]

    def sync_from_scanner(self, scanner: Any) -> int:
        scanned = scanner.scan()
        synced = 0
        for sa in scanned:
            existing = self.get_agent(sa.name)
            if existing is None:
                reg = RegisteredAgent(
                    name=sa.name, cli_path=sa.cli_path, version=sa.version,
                    provider=sa.provider, capabilities=sa.capabilities,
                    description=sa.description, model=sa.model,
                    driver=sa.driver, cli_args=sa.cli_args,
                    timeout_s=sa.timeout_s, source=sa.source.value,
                    enabled=sa.status.value == "available",
                    health="healthy" if sa.status.value == "available" else "unhealthy",
                )
                self.register(reg)
                synced += 1
            else:
                if existing.cli_path != sa.cli_path or existing.version != sa.version:
                    existing.cli_path = sa.cli_path
                    existing.version = sa.version
                    existing.health = "healthy" if sa.status.value == "available" else "unhealthy"
                    self._upsert_db(existing)
                    synced += 1
        return synced

    def _upsert_db(self, agent: RegisteredAgent) -> None:
        import json
        with sqlite_connect(self._db_path) as conn:
            conn.execute(
                """INSERT OR REPLACE INTO registered_agents
                   (name, cli_path, version, provider, capabilities, description,
                    model, driver, cli_args, timeout_s, enabled, health,
                    last_health_check, last_latency_ms, consecutive_failures,
                    registered_at, source)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (agent.name, agent.cli_path, agent.version, agent.provider,
                 json.dumps(agent.capabilities), agent.description,
                 agent.model, agent.driver, agent.cli_args, agent.timeout_s,
                 1 if agent.enabled else 0, agent.health,
                 agent.last_health_check, agent.last_latency_ms,
                 agent.consecutive_failures, agent.registered_at, agent.source),
            )

    @staticmethod
    def _row_to_agent(row: sqlite3.Row) -> RegisteredAgent:
        import json
        capabilities: list[Any] = []
        try:
            capabilities = json.loads(row["capabilities"]) if row["capabilities"] else []
        except (json.JSONDecodeError, TypeError):
            pass
        return RegisteredAgent(
            name=row["name"], cli_path=row["cli_path"], version=row["version"],
            provider=row["provider"], capabilities=capabilities,
            description=row["description"], model=row["model"],
            driver=row["driver"], cli_args=row["cli_args"],
            timeout_s=row["timeout_s"], enabled=bool(row["enabled"]),
            health=row["health"], last_health_check=row["last_health_check"],
            last_latency_ms=row["last_latency_ms"],
            consecutive_failures=row["consecutive_failures"],
            registered_at=row["registered_at"], source=row["source"],
        )
