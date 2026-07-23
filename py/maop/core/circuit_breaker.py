"""MAOP Circuit Breaker — agent call circuit-breaker with SQLite persistence,
failover chains, and health-check recovery.

State machine per agent:
    closed → (failures >= threshold) → open
    open   → (cooldown elapsed)     → half-open
    half-open → (success) → closed
    half-open → (failure) → open

Failover:
    primary → fallback → tertiary (automatic degradation chain)

Health check:
    Periodic probe recovers half-open agents back to closed.
"""

from __future__ import annotations

import asyncio
import json
import logging
import threading
import time
from enum import Enum
from functools import partial
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from maop.core.db_utils import sqlite_connect

logger = logging.getLogger(__name__)

# Default agents to pre-seed on first init
DEFAULT_AGENTS: list[str] = [
    "claude", "kimi", "codewhale", "autoclaw", "codex", "qwenpaw",
    "qoder", "openclaw", "mimo", "cursor", "hermes", "kilo",
    "mavis", "doc-pipeline",
]


class BreakerState(str, Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half-open"


class BreakerEntry(BaseModel):
    """One agent's breaker state."""
    state: BreakerState = BreakerState.CLOSED
    failures: int = 0
    threshold: int = 3
    last_failure: float | None = None  # Unix timestamp
    cooldown_s: int = 60


# ── Failover chain ────────────────────────────────────────────

class FailoverChain(BaseModel):
    """Ordered list of agents for failover: primary → fallback → tertiary."""
    agents: list[str] = Field(default_factory=list)
    current_index: int = 0

    @property
    def current(self) -> str | None:
        """Current active agent in the chain."""
        if 0 <= self.current_index < len(self.agents):
            return self.agents[self.current_index]
        return None

    def advance(self) -> str | None:
        """Move to next agent in chain. Returns new agent or None if exhausted."""
        self.current_index += 1
        if self.current_index < len(self.agents):
            return self.agents[self.current_index]
        return None

    def reset(self) -> None:
        """Reset chain back to primary."""
        self.current_index = 0


class FailoverResult(BaseModel):
    """Result of a failover resolution."""
    agent: str
    is_primary: bool
    degraded: bool  # True if using fallback/tertiary


# ── SQLite DDL ────────────────────────────────────────────────

_BREAKER_DDL = """
CREATE TABLE IF NOT EXISTS circuit_breaker_state (
  agent TEXT PRIMARY KEY,
  state TEXT NOT NULL DEFAULT 'closed',
  failures INTEGER NOT NULL DEFAULT 0,
  threshold INTEGER NOT NULL DEFAULT 3,
  last_failure REAL,
  cooldown_s INTEGER NOT NULL DEFAULT 60,
  updated REAL NOT NULL DEFAULT 0.0
);

CREATE TABLE IF NOT EXISTS failover_chains (
  name TEXT PRIMARY KEY,
  agents TEXT NOT NULL DEFAULT '[]',
  current_index INTEGER NOT NULL DEFAULT 0,
  updated REAL NOT NULL DEFAULT 0.0
);

-- Time-series: breaker state transition events (P1-2)
CREATE TABLE IF NOT EXISTS breaker_events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  agent TEXT NOT NULL,
  old_state TEXT NOT NULL DEFAULT '',
  new_state TEXT NOT NULL DEFAULT '',
  failures INTEGER NOT NULL DEFAULT 0,
  timestamp REAL NOT NULL DEFAULT 0.0
);

CREATE INDEX IF NOT EXISTS idx_be_agent_ts ON breaker_events(agent, timestamp);
CREATE INDEX IF NOT EXISTS idx_be_ts ON breaker_events(timestamp);
"""


# ── CircuitBreaker ────────────────────────────────────────────

class CircuitBreaker:
    """SQLite-backed circuit breaker with failover and health-check.

    Usage::

        cb = CircuitBreaker(db_path="data/maop.db")
        entry = cb.get("claude")
        if entry.state == BreakerState.OPEN:
            # skip call, use failover
            ...
        else:
            try:
                result = call_agent(...)
                cb.record_success("claude")
            except Exception:
                cb.record_failure("claude")

    Failover::

        cb.register_failover("codegen", ["claude", "kimi", "codex"])
        result = cb.resolve_failover("codegen")
        # result.agent = "claude" (or fallback if claude is open)
    """

    def __init__(self, path: Path | str | None = None) -> None:
        if path is None:
            path = Path(__file__).resolve().parents[3] / "data" / "maop.db"
        self._path = Path(path)
        self._data: dict[str, BreakerEntry] = {}
        self._failover_chains: dict[str, FailoverChain] = {}
        self._sync_lock = threading.RLock()
        self._async_lock = asyncio.Lock()
        self._init_db()

    # ── SQLite connection ─────────────────────────────────────

    def _connect(self):
        return sqlite_connect(self._path, timeout=10, wal=True, foreign_keys=False)

    def _init_db(self) -> None:
        """Initialize SQLite schema and load existing state."""
        self._path.parent.mkdir(parents=True, exist_ok=True)
        try:
            with self._connect() as conn:
                conn.executescript(_BREAKER_DDL)
                # Load existing breaker states
                for row in conn.execute("SELECT * FROM circuit_breaker_state").fetchall():
                    self._data[row["agent"]] = BreakerEntry(
                        state=BreakerState(row["state"]),
                        failures=row["failures"],
                        threshold=row["threshold"],
                        last_failure=row["last_failure"],
                        cooldown_s=row["cooldown_s"],
                    )
                # Load failover chains
                for row in conn.execute("SELECT * FROM failover_chains").fetchall():
                    agents = json.loads(row["agents"])
                    self._failover_chains[row["name"]] = FailoverChain(
                        agents=agents,
                        current_index=row["current_index"],
                    )

            # Seed default agents if DB is empty
            if not self._data:
                for agent in DEFAULT_AGENTS:
                    self._data[agent] = BreakerEntry()
                self._save_all()

        except Exception as exc:
            logger.warning("Failed to initialize circuit breaker DB: %s", exc)
            # Fallback to in-memory only
            if not self._data:
                for agent in DEFAULT_AGENTS:
                    self._data[agent] = BreakerEntry()

    def _save_all(self) -> None:
        """Persist all breaker states to SQLite."""
        try:
            with self._connect() as conn:
                now = time.time()
                rows = [
                    (agent, entry.state.value, entry.failures, entry.threshold,
                     entry.last_failure, entry.cooldown_s, now)
                    for agent, entry in self._data.items()
                ]
                conn.executemany(
                    """INSERT OR REPLACE INTO circuit_breaker_state
                       (agent, state, failures, threshold, last_failure, cooldown_s, updated)
                       VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    rows,
                )
        except Exception as exc:
            logger.warning("Breaker save failed: %s", exc)

    def _save_agent(self, agent_name: str, entry: BreakerEntry, old_state: BreakerState | None = None) -> None:
        """Persist a single agent's breaker state and record event."""
        try:
            with self._connect() as conn:
                now = time.time()
                conn.execute(
                    """INSERT OR REPLACE INTO circuit_breaker_state
                       (agent, state, failures, threshold, last_failure, cooldown_s, updated)
                       VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    (agent_name, entry.state.value, entry.failures, entry.threshold,
                     entry.last_failure, entry.cooldown_s, now),
                )
                # Record state transition event (P1-2 time-series)
                if old_state is not None and old_state != entry.state:
                    conn.execute(
                        """INSERT INTO breaker_events (agent, old_state, new_state, failures, timestamp)
                           VALUES (?, ?, ?, ?, ?)""",
                        (agent_name, old_state.value, entry.state.value, entry.failures, now),
                    )
        except Exception as exc:
            logger.warning("Breaker save agent %s failed: %s", agent_name, exc)

    def _save_failover_chain(self, name: str, chain: FailoverChain) -> None:
        """Persist a failover chain."""
        try:
            with self._connect() as conn:
                now = time.time()
                conn.execute(
                    """INSERT OR REPLACE INTO failover_chains
                       (name, agents, current_index, updated)
                       VALUES (?, ?, ?, ?)""",
                    (name, json.dumps(chain.agents), chain.current_index, now),
                )
        except Exception as exc:
            logger.warning("Failover chain save failed: %s", exc)

    # ── public API ───────────────────────────────────────────

    def get(self, agent_name: str) -> BreakerEntry | None:
        """Return the breaker entry for *agent_name*, or None if unknown."""
        with self._sync_lock:
            return self._data.get(agent_name)

    def set_state(
        self,
        agent_name: str,
        state: BreakerState,
        *,
        failures: int = -1,
        threshold: int = -1,
        last_failure: str | None = None,
    ) -> BreakerEntry:
        """Set breaker state for an agent. Returns the updated entry."""
        with self._sync_lock:
            entry = self._data.get(agent_name)
            if entry is None:
                entry = BreakerEntry()
                self._data[agent_name] = entry

            entry.state = state
            if failures >= 0:
                entry.failures = failures
            if threshold >= 0:
                entry.threshold = threshold
            if last_failure is not None:
                entry.last_failure = time.time() if last_failure else None

            self._save_agent(agent_name, entry)
            return entry

    def record_success(self, agent_name: str) -> BreakerEntry:
        """Record a successful call: reset to CLOSED."""
        with self._sync_lock:
            entry = self._data.get(agent_name, BreakerEntry())
            old_state = entry.state
            entry.state = BreakerState.CLOSED
            entry.failures = 0
            entry.last_failure = None
            self._data[agent_name] = entry
            self._save_agent(agent_name, entry, old_state=old_state)

            # If this agent is in a failover chain, reset chain back to primary
            for name, chain in self._failover_chains.items():
                if chain.current and chain.current == agent_name and chain.current_index > 0:
                    chain.reset()
                    self._save_failover_chain(name, chain)

            return entry

    def record_failure(self, agent_name: str) -> BreakerEntry:
        """Record a failed call: increment failures, transition if threshold reached."""
        with self._sync_lock:
            entry = self._data.get(agent_name, BreakerEntry())
            old_state = entry.state
            entry.failures += 1
            entry.last_failure = time.time()

            if entry.state == BreakerState.HALF_OPEN:
                entry.state = BreakerState.OPEN
            elif entry.failures >= entry.threshold:
                entry.state = BreakerState.OPEN

            self._data[agent_name] = entry
            self._save_agent(agent_name, entry, old_state=old_state)

            # Advance failover chain if this agent is the current one
            for name, chain in self._failover_chains.items():
                if chain.current == agent_name:
                    next_agent = chain.advance()
                    if next_agent:
                        self._save_failover_chain(name, chain)
                        logger.info("[breaker] Failover %s: %s → %s", name, agent_name, next_agent)

            return entry

    def is_available(self, agent_name: str) -> bool:
        """Check if the agent is callable (closed or half-open with cooldown elapsed)."""
        with self._sync_lock:
            entry = self._data.get(agent_name)
            if entry is None:
                return True
            if entry.state == BreakerState.CLOSED:
                return True
            if entry.state == BreakerState.HALF_OPEN:
                return True
            if entry.state == BreakerState.OPEN:
                if entry.last_failure is not None:
                    elapsed = time.time() - entry.last_failure
                    if elapsed >= entry.cooldown_s:
                        # Auto-transition to half-open
                        old_state = entry.state
                        entry.state = BreakerState.HALF_OPEN
                        self._save_agent(agent_name, entry, old_state=old_state)
                        return True
                return False
            return False

    def all_states(self) -> dict[str, BreakerEntry]:
        """Return a snapshot of all agent breaker states."""
        with self._sync_lock:
            return dict(self._data)

    # ── Failover ─────────────────────────────────────────────

    def register_failover(self, name: str, agents: list[str]) -> None:
        """Register a failover chain: primary → fallback → tertiary.

        Parameters
        ----------
        name : str
            Chain identifier (e.g. "codegen", "search").
        agents : list[str]
            Ordered list of agent names from primary to last fallback.
        """
        with self._sync_lock:
            chain = FailoverChain(agents=agents)
            self._failover_chains[name] = chain
            self._save_failover_chain(name, chain)
            logger.info("[breaker] Registered failover chain '%s': %s", name, " → ".join(agents))

    def resolve_failover(
        self,
        name: str,
        *,
        required_capability: str | None = None,
        agent_capabilities: dict[str, list[str]] | None = None,
    ) -> FailoverResult | None:
        """Resolve the current available agent in a failover chain.

        Skips OPEN agents and advances the chain automatically.
        If *required_capability* and *agent_capabilities* are provided,
        agents lacking the capability are also skipped (P1 fix).

        Returns
        -------
        FailoverResult | None
            The resolved agent info, or None if all agents in chain are unavailable.
        """
        with self._sync_lock:
            chain = self._failover_chains.get(name)
            if chain is None:
                return None

            # Try from current index forward
            for i in range(chain.current_index, len(chain.agents)):
                agent = chain.agents[i]
                if not self.is_available(agent):
                    continue
                # P1 fix: check capability if filter is provided
                if required_capability and agent_capabilities:
                    caps = agent_capabilities.get(agent, [])
                    if required_capability not in caps:
                        logger.debug(
                            "[breaker] Skipping %s in failover '%s': "
                            "missing capability '%s'",
                            agent, name, required_capability,
                        )
                        continue
                chain.current_index = i
                self._save_failover_chain(name, chain)
                return FailoverResult(
                    agent=agent,
                    is_primary=(i == 0),
                    degraded=(i > 0),
                )

            # All agents unavailable or lack required capability
            return None

    def get_failover_chain(self, name: str) -> FailoverChain | None:
        """Get a failover chain by name."""
        return self._failover_chains.get(name)

    # ── Health check ─────────────────────────────────────────

    def health_check(self, probe: Any = None) -> dict[str, BreakerState]:
        """Probe all agents and recover half-open → closed if healthy.

        Args:
            probe: Optional async or sync callable(agent_name) -> bool.
                   If provided, half-open agents are only recovered when
                   probe returns True. If omitted, HALF_OPEN agents are
                   NOT auto-recovered — they must wait for a real request
                   to test the agent (record_success/record_failure).

        B-P0-5 fix: Previously, when probe=None, HALF_OPEN agents were
        immediately recovered to CLOSED because last_failure was not
        updated on OPEN→HALF_OPEN transition, making elapsed >= cooldown_s
        always true. This bypassed the probe semantics. Now, without a
        probe, HALF_OPEN agents stay in HALF_OPEN until a real request
        tests them.
        """
        with self._sync_lock:
            recovered: dict[str, BreakerState] = {}
            for agent_name, entry in list(self._data.items()):
                if entry.state == BreakerState.HALF_OPEN:
                    if probe is None:
                        # B-P0-5 fix: require real probe or actual request
                        # to recover from HALF_OPEN
                        continue
                    try:
                        is_healthy = probe(agent_name)
                    except Exception as exc:
                        logger.warning("[breaker] Probe for %s failed: %s", agent_name, exc)
                        continue
                    if not is_healthy:
                        continue
                    old_state = entry.state
                    entry.state = BreakerState.CLOSED
                    entry.failures = 0
                    self._save_agent(agent_name, entry, old_state=old_state)
                    recovered[agent_name] = BreakerState.CLOSED
                    logger.info("[breaker] Health check: %s recovered to CLOSED (probed)", agent_name)

            return recovered

    def get_open_agents(self) -> list[str]:
        """Return list of agents currently in OPEN state."""
        with self._sync_lock:
            return [name for name, entry in self._data.items() if entry.state == BreakerState.OPEN]

    def get_half_open_agents(self) -> list[str]:
        """Return list of agents currently in HALF_OPEN state."""
        with self._sync_lock:
            return [name for name, entry in self._data.items() if entry.state == BreakerState.HALF_OPEN]

    # ── Time-series events (P1-2) ──────────────────────────

    def get_events(
        self,
        agent: str = "",
        since: float = 0.0,
        until: float = 0.0,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """Query breaker state transition events (time-series).

        Parameters
        ----------
        agent : str
            Filter by agent name (empty = all).
        since : float
            Unix timestamp lower bound.
        until : float
            Unix timestamp upper bound.
        limit : int
            Maximum events to return.

        Returns
        -------
        list[dict]
            Events with keys: id, agent, old_state, new_state, failures, timestamp.
        """
        conditions = []
        params: list[Any] = []
        if agent:
            conditions.append("agent = ?")
            params.append(agent)
        if since > 0:
            conditions.append("timestamp >= ?")
            params.append(since)
        if until > 0:
            conditions.append("timestamp <= ?")
            params.append(until)

        where = " AND ".join(conditions)
        where_sql = f"WHERE {where}" if where else ""

        try:
            with self._connect() as conn:
                cursor = conn.execute(
                    f"SELECT * FROM breaker_events {where_sql} ORDER BY timestamp DESC LIMIT ?",
                    params + [limit],
                )
                columns = [desc[0] for desc in cursor.description]
                return [dict(zip(columns, row)) for row in cursor.fetchall()]
        except Exception as exc:
            logger.warning("Get events failed: %s", exc)
            return []

    def get_event_count(self, agent: str = "", since: float = 0.0) -> int:
        """Count breaker events (for metrics aggregation)."""
        conditions = []
        params: list[Any] = []
        if agent:
            conditions.append("agent = ?")
            params.append(agent)
        if since > 0:
            conditions.append("timestamp >= ?")
            params.append(since)
        where = " AND ".join(conditions)
        where_sql = f"WHERE {where}" if where else ""

        try:
            with self._connect() as conn:
                row = conn.execute(
                    f"SELECT COUNT(*) as cnt FROM breaker_events {where_sql}",
                    params,
                ).fetchone()
                return row["cnt"] if row else 0
        except Exception:
            return 0

    # ── Async wrappers ─────────────────────────────────────

    async def arecord_success(self, agent_name: str) -> BreakerEntry:
        async with self._async_lock:
            entry = self._data.get(agent_name, BreakerEntry())
            old_state = entry.state
            entry.state = BreakerState.CLOSED
            entry.failures = 0
            entry.last_failure = None
            self._data[agent_name] = entry
            for name, chain in self._failover_chains.items():
                if chain.current and chain.current == agent_name and chain.current_index > 0:
                    chain.reset()
                    await asyncio.get_running_loop().run_in_executor(
                        None, partial(self._save_failover_chain, name, chain)
                    )
            await asyncio.get_running_loop().run_in_executor(
                None, partial(self._save_agent, agent_name, entry, old_state=old_state)
            )
            return entry

    async def arecord_failure(self, agent_name: str) -> BreakerEntry:
        async with self._async_lock:
            entry = self._data.get(agent_name, BreakerEntry())
            old_state = entry.state
            entry.failures += 1
            entry.last_failure = time.time()
            if entry.state == BreakerState.HALF_OPEN:
                entry.state = BreakerState.OPEN
            elif entry.failures >= entry.threshold:
                entry.state = BreakerState.OPEN
            self._data[agent_name] = entry
            for name, chain in self._failover_chains.items():
                if chain.current == agent_name:
                    next_agent = chain.advance()
                    if next_agent:
                        await asyncio.get_running_loop().run_in_executor(
                            None, partial(self._save_failover_chain, name, chain)
                        )
                        logger.info("[breaker] Failover %s: %s → %s", name, agent_name, next_agent)
            await asyncio.get_running_loop().run_in_executor(
                None, partial(self._save_agent, agent_name, entry, old_state=old_state)
            )
            return entry

    async def ais_available(self, agent_name: str) -> bool:
        async with self._async_lock:
            entry = self._data.get(agent_name)
            if entry is None:
                return True
            if entry.state == BreakerState.CLOSED:
                return True
            if entry.state == BreakerState.HALF_OPEN:
                return True
            if entry.state == BreakerState.OPEN:
                if entry.last_failure is not None:
                    elapsed = time.time() - entry.last_failure
                    if elapsed >= entry.cooldown_s:
                        entry.state = BreakerState.HALF_OPEN
                        await asyncio.get_running_loop().run_in_executor(
                            None, lambda: self._save_agent(agent_name, entry)
                        )
                        return True
                return False
            return False
