"""MAOP Agent Bridge — Adapter pattern for external agent integration.

Provides a standard interface (ABC) for connecting to external agents,
enabling MAOP to interact with diverse agent systems (Claude, GPT,
AutoGen, CrewAI, etc.) through a unified API.

Usage::

    from maop.core.agent.delegation.agent_proxy import AgentAdapter, AgentProxy

    class MyAdapter(AgentAdapter):
        def connect(self) -> bool: ...
        def execute(self, task: str, **kwargs) -> str: ...
        def health_check(self) -> bool: ...
        def sync_config(self, config: dict) -> None: ...
        def disconnect(self) -> None: ...

    bridge = AgentProxy(root_dir="/path/to/MAOP")
    bridge.register("my_agent", MyAdapter())
    result = bridge.call("my_agent", "Analyze this code")
"""

from __future__ import annotations

import json
import logging
import time
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from maop.core.backends.db_utils import get_db_path, sqlite_connect

logger = logging.getLogger(__name__)


class AdapterStatus(BaseModel):
    """Status of an agent adapter."""
    name: str = ""
    connected: bool = False
    healthy: bool = False
    last_call_at: float = 0.0
    call_count: int = 0
    error_count: int = 0
    adapter_type: str = ""


class AdapterConfig(BaseModel):
    """Configuration for an agent adapter."""
    name: str = ""
    adapter_type: str = ""
    connection_params: dict[str, Any] = Field(default_factory=dict)
    max_retries: int = 3
    timeout_s: float = 30.0


class AgentAdapter(ABC):
    """Abstract base class for agent adapters.

    Subclasses must implement:
      - connect(): Establish connection to the external agent
      - execute(): Send a task to the agent and return the result
      - health_check(): Verify the agent is responsive
      - sync_config(): Push configuration to the agent
      - disconnect(): Clean up the connection
    """

    @abstractmethod
    def connect(self) -> bool:
        """Establish connection to the external agent. Returns True on success."""

    @abstractmethod
    def execute(self, task: str, **kwargs: Any) -> str:
        """Execute a task on the external agent. Returns the result string."""

    @abstractmethod
    def health_check(self) -> bool:
        """Check if the agent is responsive. Returns True if healthy."""

    @abstractmethod
    def sync_config(self, config: dict[str, Any]) -> None:
        """Push configuration to the external agent."""

    @abstractmethod
    def disconnect(self) -> None:
        """Clean up the connection to the external agent."""


_ADAPTER_DDL = """
CREATE TABLE IF NOT EXISTS agent_proxy_state (
    adapter_name TEXT PRIMARY KEY,
    adapter_type TEXT DEFAULT '',
    connected INTEGER DEFAULT 0,
    config TEXT DEFAULT '{}',
    last_call_at REAL DEFAULT 0.0,
    call_count INTEGER DEFAULT 0,
    error_count INTEGER DEFAULT 0,
    created_at REAL NOT NULL,
    updated_at REAL DEFAULT 0.0
);
"""


class AgentProxy:
    """Registry and dispatcher for agent adapters.

    Provides a unified interface to call any registered agent adapter
    by name, with connection management, health checks, and error tracking.

    Parameters
    ----------
    root_dir : str | Path
        MAOP project root directory.
    """

    def __init__(self, root_dir: str | Path) -> None:
        self._root = Path(root_dir)
        self._data_dir = self._root / "data"
        self._data_dir.mkdir(parents=True, exist_ok=True)
        self._db_path = get_db_path("agent_proxy")
        self._adapters: dict[str, AgentAdapter] = {}
        self._init_db()

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.executescript(_ADAPTER_DDL)

    def _connect(self):
        return sqlite_connect(self._db_path, foreign_keys=False)

    def register(self, name: str, adapter: AgentAdapter) -> None:
        """Register an agent adapter by name."""
        self._adapters[name] = adapter
        now = time.time()
        with self._connect() as conn:
            conn.execute(
                """INSERT OR REPLACE INTO agent_proxy_state
                   (adapter_name, adapter_type, connected, config, last_call_at,
                    call_count, error_count, created_at, updated_at)
                   VALUES (?, ?, 0, '{}', 0, 0, 0, ?, ?)""",
                (name, type(adapter).__name__, now, now),
            )
        logger.info("[agent_proxy] Registered adapter: %s (%s)", name, type(adapter).__name__)

    def unregister(self, name: str) -> None:
        """Unregister an agent adapter and disconnect it."""
        adapter = self._adapters.pop(name, None)
        if adapter is not None:
            try:
                adapter.disconnect()
            except Exception as exc:
                logger.warning("Disconnect failed for %s: %s", name, exc)
        with self._connect() as conn:
            conn.execute("DELETE FROM agent_proxy_state WHERE adapter_name = ?", (name,))

    def get(self, name: str) -> AgentAdapter | None:
        """Get a registered adapter by name."""
        return self._adapters.get(name)

    def list_adapters(self) -> list[str]:
        """List all registered adapter names."""
        return list(self._adapters.keys())

    def call(self, name: str, task: str, **kwargs: Any) -> str:
        """Execute a task on a named agent adapter.

        Automatically connects if not already connected, and tracks
        call counts and errors.

        Raises KeyError if the adapter is not registered.
        """
        adapter = self._adapters.get(name)
        if adapter is None:
            raise KeyError(f"Adapter not registered: {name}")

        now = time.time()
        try:
            result = adapter.execute(task, **kwargs)
            with self._connect() as conn:
                conn.execute(
                    """UPDATE agent_proxy_state
                       SET last_call_at = ?, call_count = call_count + 1,
                           updated_at = ?, connected = 1
                       WHERE adapter_name = ?""",
                    (now, now, name),
                )
            return result
        except Exception as exc:
            with self._connect() as conn:
                conn.execute(
                    """UPDATE agent_proxy_state
                       SET error_count = error_count + 1, updated_at = ?
                       WHERE adapter_name = ?""",
                    (now, name),
                )
            logger.error("[agent_proxy] Call failed for %s: %s", name, exc)
            raise

    def connect_all(self) -> dict[str, bool]:
        """Connect all registered adapters. Returns name→success map."""
        results: dict[str, bool] = {}
        for name, adapter in self._adapters.items():
            try:
                ok = adapter.connect()
                results[name] = ok
                with self._connect() as conn:
                    conn.execute(
                        "UPDATE agent_proxy_state SET connected = ?, updated_at = ? WHERE adapter_name = ?",
                        (1 if ok else 0, time.time(), name),
                    )
            except Exception as exc:
                results[name] = False
                logger.warning("[agent_proxy] Connect failed for %s: %s", name, exc)
        return results

    def health_check_all(self) -> dict[str, bool]:
        """Health-check all registered adapters. Returns name→healthy map."""
        results: dict[str, bool] = {}
        for name, adapter in self._adapters.items():
            try:
                results[name] = adapter.health_check()
            except Exception:
                results[name] = False
        return results

    def sync_config(self, name: str, config: dict[str, Any]) -> None:
        """Push configuration to a specific adapter."""
        adapter = self._adapters.get(name)
        if adapter is None:
            raise KeyError(f"Adapter not registered: {name}")
        adapter.sync_config(config)
        with self._connect() as conn:
            conn.execute(
                "UPDATE agent_proxy_state SET config = ?, updated_at = ? WHERE adapter_name = ?",
                (json.dumps(config, default=str), time.time(), name),
            )

    def get_status(self, name: str) -> AdapterStatus:
        """Get the status of a registered adapter."""
        with self._connect() as conn:
            cursor = conn.execute(
                "SELECT * FROM agent_proxy_state WHERE adapter_name = ?", (name,)
            )
            cols = [d[0] for d in cursor.description] if cursor.description else []
            row = cursor.fetchone()
        if row is None:
            return AdapterStatus(name=name)

        d = dict(zip(cols, row))

        adapter = self._adapters.get(name)
        healthy = False
        if adapter is not None:
            try:
                healthy = adapter.health_check()
            except Exception:
                healthy = False

        return AdapterStatus(
            name=d.get("adapter_name", name),
            connected=bool(d.get("connected", 0)),
            healthy=healthy,
            last_call_at=d.get("last_call_at", 0.0),
            call_count=d.get("call_count", 0),
            error_count=d.get("error_count", 0),
            adapter_type=d.get("adapter_type", ""),
        )

    def disconnect_all(self) -> None:
        """Disconnect all registered adapters."""
        for name, adapter in self._adapters.items():
            try:
                adapter.disconnect()
                with self._connect() as conn:
                    conn.execute(
                        "UPDATE agent_proxy_state SET connected = 0, updated_at = ? WHERE adapter_name = ?",
                        (time.time(), name),
                    )
            except Exception as exc:
                logger.warning("[agent_proxy] Disconnect failed for %s: %s", name, exc)
