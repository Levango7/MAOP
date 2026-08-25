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
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from maop.core.backends.db_utils import sqlite_connect

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

# ── Circuit Breaker State Machine ──────────────────────────────
#
# States (BreakerState enum):
#   CLOSED    - Normal operation. Requests pass through.
#   OPEN      - Circuit tripped. All requests rejected immediately.
#   HALF_OPEN - Trial state. Limited requests allowed to test recovery.
#
# State Transitions:
#
#   CLOSED -> OPEN
#     Condition: consecutive failures >= threshold (default: 3)
#     Action:    record trip time (last_failure), persist breaker_events row
#                resolve_failover() advances chain -> fallback agent if configured
#
#   OPEN -> HALF_OPEN
#     Condition: time since last_failure >= cooldown_s (default: 60s)
#     Action:    allow next request through (trial)
#                (checked in is_available() on each call)
#
#   HALF_OPEN -> CLOSED
#     Condition: trial request succeeds (record_success)
#     Action:    reset failures to 0, clear last_failure
#                reset failover chain back to primary
#
#   HALF_OPEN -> OPEN
#     Condition: trial request fails (record_failure)
#     Action:    record new trip time (cooldown extended)
#                advance failover chain if this agent is current
#
# 线程安全（统一单锁模型，P3-3 修复）：
#   - 所有同步方法使用 _sync_lock (threading.RLock)
#   - 所有异步方法通过 asyncio.to_thread 委托给同步方法，
#     确保对 _data / _failover_chains 的访问全部经过同一把 _sync_lock
#   - 不再使用独立的 _async_lock，避免同步/异步两条访问路径互不感知
#     导致 failure count 丢失与状态翻转
#
# Failover:
#   register_failover(name, agents: list) - registers primary -> fallback -> tertiary chain
#   resolve_failover(name) - returns FailoverResult; skips OPEN agents, advances chain
#   When OPEN, dispatcher calls resolve_failover to redirect requests

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
        # 统一使用单把 _sync_lock：同步方法直接获取，异步方法通过
        # asyncio.to_thread 委托给同步方法，避免双锁并发问题。
        self._sync_lock = threading.RLock()
        # Short-TTL cache for all_states() snapshot (avoid re-copying _data
        # under the lock on every call). Accepts up to _STATES_CACHE_TTL of
        # staleness; mutating operations rely on the TTL for expiry.
        self._states_cache: tuple[float, dict[str, BreakerEntry]] | None = None
        self._STATES_CACHE_TTL = 0.5  # 500ms
        self._init_db()

    @staticmethod
    def _load_agent_names_from_config() -> list[str]:
        """Load agent names from config/agents.yaml.

        Returns an empty list if config cannot be loaded (caller should
        fall back to DEFAULT_AGENTS).
        """
        try:
            from maop.config.loader import ConfigLoader
            loader = ConfigLoader(project_root=Path(__file__).resolve().parents[3])
            config = loader.load()
            names = list(config.agents.keys()) if hasattr(config, "agents") and config.agents else []
            return names
        except Exception:
            logger.debug("Silent exception in core/circuit_breaker.py:221", exc_info=True)
            return []

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
                agent_names = self._load_agent_names_from_config() or DEFAULT_AGENTS
                for agent in agent_names:
                    self._data[agent] = BreakerEntry()
                self._save_all()

        except Exception as exc:
            logger.warning("Failed to initialize circuit breaker DB: %s", exc)
            # Fallback to in-memory only
            if not self._data:
                agent_names = self._load_agent_names_from_config() or DEFAULT_AGENTS
                for agent in agent_names:
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
                    # H8 修复：记录熔断器状态指标（1=closed, 0.5=half, 0=open）
                    try:
                        from maop.core.monitoring.monitoring import (
                            MAOP_CIRCUIT_BREAKER_STATE,
                        )

                        state_value = {
                            "closed": 1.0,
                            "half-open": 0.5,
                            "open": 0.0,
                        }.get(entry.state.value, 0.0)
                        MAOP_CIRCUIT_BREAKER_STATE.set(
                            state_value, labels={"agent": agent_name}
                        )
                    except Exception:
                        # 指标更新失败不应影响熔断器状态持久化
                        logger.debug("set MAOP_CIRCUIT_BREAKER_STATE failed", exc_info=True)
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

            # If this agent is in a failover chain, reset chain back to primary.
            # Two reset paths (C1 fix adds the second):
            #   1. The current (fallback) agent succeeds — optimistic reset;
            #      resolve_failover skips OPEN agents, so this is safe and is
            #      the original, test-covered behaviour.
            #   2. The PRIMARY agent (agents[0]) recovers while the chain has
            #      advanced past it — previously this never matched because
            #      chain.current was the fallback, so a recovered primary was
            #      never restored until the fallback happened to succeed.
            for name, chain in self._failover_chains.items():
                if chain.current_index > 0 and (
                    chain.current == agent_name
                    or (chain.agents and chain.agents[0] == agent_name)
                ):
                    chain.reset()
                    self._save_failover_chain(name, chain)
                    logger.info("[breaker] Failover chain '%s' reset to primary (trigger=%s)", name, agent_name)

            return entry

    def record_failure(self, agent_name: str) -> BreakerEntry:
        """Record a failed call: increment failures, transition if threshold reached."""
        with self._sync_lock:
            entry = self._data.get(agent_name, BreakerEntry())
            old_state = entry.state
            entry.failures += 1
            entry.last_failure = time.time()

            if entry.state == BreakerState.HALF_OPEN or entry.failures >= entry.threshold:
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
        """Return a snapshot of all agent breaker states, with a short TTL cache.

        Repeated calls within _STATES_CACHE_TTL (500ms) return the same
        cached snapshot without re-acquiring the lock or re-copying _data.
        Falls back to a fresh snapshot on miss.
        """
        now = time.monotonic()
        if self._states_cache is not None and (now - self._states_cache[0]) < self._STATES_CACHE_TTL:
            return self._states_cache[1]
        with self._sync_lock:
            snapshot = dict(self._data)
        self._states_cache = (now, snapshot)
        return snapshot

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
        """Get a failover chain by name (thread-safe snapshot).

        High fix: read under _sync_lock and return a copy so callers never
        observe a chain mid-mutation (resolve_failover/record_success mutate
        current_index under the lock).
        """
        with self._sync_lock:
            chain = self._failover_chains.get(name)
            if chain is None:
                return None
            return FailoverChain(
                agents=list(chain.agents),
                current_index=chain.current_index,
            )

    # ── Health check ─────────────────────────────────────────

    def _get_half_open_agents_sync(self) -> list[tuple[str, BreakerEntry]]:
        """同步获取所有处于 HALF_OPEN 状态的 agent（持 _sync_lock）。"""
        with self._sync_lock:
            return [
                (name, entry)
                for name, entry in self._data.items()
                if entry.state == BreakerState.HALF_OPEN
            ]

    def _get_all_states_sync(self) -> dict[str, BreakerState]:
        """同步获取所有 agent 的当前状态快照（持 _sync_lock）。"""
        with self._sync_lock:
            return {name: entry.state for name, entry in self._data.items()}

    def _recover_agent_sync(self, agent_name: str) -> bool:
        """同步将 HALF_OPEN agent 恢复为 CLOSED（持 _sync_lock）。

        Returns True if recovery happened, False if agent was missing or
        not in HALF_OPEN state (可能被其他线程改动)。
        """
        with self._sync_lock:
            entry = self._data.get(agent_name)
            if entry is None or entry.state != BreakerState.HALF_OPEN:
                return False
            old_state = entry.state
            entry.state = BreakerState.CLOSED
            entry.failures = 0
            self._save_agent(agent_name, entry, old_state=old_state)
            return True

    async def health_check(self, probe: Any = None) -> dict[str, BreakerState]:
        """Probe all agents and recover half-open → closed if healthy.

        Args:
            probe: Optional async or sync callable(agent_name) -> bool.
                   If provided, half-open agents are only recovered when
                   probe returns True. If omitted, HALF_OPEN agents are
                   NOT auto-recovered — they must wait for a real request
                   to test the agent (record_success/record_failure).

        P2-14 fix: Now async to support async probe callables. Sync probes
        are still supported via asyncio.iscoroutine check.

        B-P0-5 fix: Previously, when probe=None, HALF_OPEN agents were
        immediately recovered to CLOSED because last_failure was not
        updated on OPEN→HALF_OPEN transition, making elapsed >= cooldown_s
        always true. This bypassed the probe semantics. Now, without a
        probe, HALF_OPEN agents stay in HALF_OPEN until a real request
        tests them.

        P3-3 fix: 统一单锁模型——所有对 _data 的访问通过 asyncio.to_thread
        委托给持 _sync_lock 的同步辅助方法，不再使用 _async_lock。
        """
        # 通过 to_thread 获取 half-open agent 快照（持 _sync_lock）
        half_open_agents: list[tuple[str, BreakerEntry]] = await asyncio.to_thread(
            self._get_half_open_agents_sync
        )

        if not half_open_agents:
            return await asyncio.to_thread(self._get_all_states_sync)

        # Probe outside lock to avoid blocking other operations
        for agent_name, _entry in half_open_agents:
            if probe is None:
                continue
            try:
                is_healthy = probe(agent_name)
                if asyncio.iscoroutine(is_healthy):
                    is_healthy = await is_healthy
            except Exception as exc:
                logger.warning("[breaker] Probe for %s failed: %s", agent_name, exc)
                continue
            if not is_healthy:
                continue
            # 通过 to_thread 在 _sync_lock 下恢复 agent
            recovered = await asyncio.to_thread(self._recover_agent_sync, agent_name)
            if recovered:
                logger.info("[breaker] Health check: %s recovered to CLOSED (probed)", agent_name)

        return await asyncio.to_thread(self._get_all_states_sync)

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
            logger.debug("Silent exception in core/circuit_breaker.py:699", exc_info=True)
            return 0

    # ── Async wrappers ─────────────────────────────────────
    #
    # P3-3 修复：所有异步方法通过 asyncio.to_thread 委托给同步方法，
    # 确保对 _data / _failover_chains 的访问全部经过同一把 _sync_lock，
    # 避免同步/异步两条锁互不感知导致 failure count 丢失与状态翻转。

    async def arecord_success(self, agent_name: str) -> BreakerEntry:
        return await asyncio.to_thread(self.record_success, agent_name)

    async def arecord_failure(self, agent_name: str) -> BreakerEntry:
        return await asyncio.to_thread(self.record_failure, agent_name)

    async def ais_available(self, agent_name: str) -> bool:
        return await asyncio.to_thread(self.is_available, agent_name)
