"""Configuration change history & rollback support.

Every time the runtime configuration is mutated (agent update, route
edit, edition switch, …) a snapshot of the resulting config is persisted
to a SQLite table.  Operators can then:

* list the change timeline (``list_history``)
* inspect a specific version (``get_version``)
* restore a previous known-good state (``rollback``)

Rollback fires a ``config_changed`` event on the global event bus so
hot-reload subscribers (RouteScorer, ConfigLoader cache, dashboard WS
push, …) react exactly as if the config had been edited on disk.

Schema (table ``config_snapshots``)::

    id            INTEGER PRIMARY KEY AUTOINCREMENT
    version       INTEGER UNIQUE NOT NULL        -- monotonic 1..N
    snapshot_json TEXT    NOT NULL               -- JSON-serialised config
    changed_by    TEXT    NOT NULL               -- actor (user / system)
    changed_at    TEXT    NOT NULL               -- ISO-8601 UTC timestamp

The module is deliberately self-contained: it only depends on the
standard library plus ``maop.core.event_bus`` (lazy-imported inside
``rollback`` to avoid a circular import at module load time).
"""

from __future__ import annotations

import json
import logging
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# ── Schema constants ───────────────────────────────────────────────
_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS config_snapshots (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    version       INTEGER UNIQUE NOT NULL,
    snapshot_json TEXT    NOT NULL,
    changed_by    TEXT    NOT NULL,
    changed_at    TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_config_snapshots_version
    ON config_snapshots(version DESC);
"""

# Event topic fired after a successful rollback. Subscribers (hot-reload,
# RouteScorer, dashboard WS) treat this identically to a disk edit.
CONFIG_CHANGED_TOPIC = "config_changed"


def _utc_now_iso() -> str:
    """Return current UTC time as an ISO-8601 string (seconds precision)."""
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _default_db_path() -> Path:
    """Resolve the default snapshots DB path from MAOP settings.

    Falls back to ``<cwd>/data/config_history.db`` when settings cannot be
    imported (e.g. during early bootstrap or unit tests that patch
    ``sys.modules``).
    """
    try:
        from maop.config.settings import get_settings
        return get_settings().resolved_data_dir() / "config_history.db"
    except Exception:  # pragma: no cover — bootstrap fallback
        return Path("data") / "config_history.db"


class ConfigHistory:
    """Persist configuration snapshots and support rollback.

    The class is thread-safe (every public method acquires the instance
    lock around the SQLite call).  A single instance is intended to be
    shared per-process via :func:`get_config_history`.

    Parameters
    ----------
    db_path:
        Path to the SQLite database file.  ``None`` resolves to the
        default location under ``<data_dir>/config_history.db``.
    """

    def __init__(self, db_path: str | Path | None = None) -> None:
        if db_path is None:
            db_path = _default_db_path()
        self._db_path = Path(db_path)
        # Ensure parent dir exists so sqlite3.connect doesn't fail.
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn: sqlite3.Connection | None = None
        self._init_schema()

    # ── Connection management ─────────────────────────────────────
    def _get_conn(self) -> sqlite3.Connection:
        """Return the lazily-opened connection (call under lock)."""
        if self._conn is None:
            self._conn = sqlite3.connect(
                str(self._db_path),
                check_same_thread=False,
                isolation_level=None,  # autocommit; we manage txns explicitly
            )
            self._conn.row_factory = sqlite3.Row
        return self._conn

    def _init_schema(self) -> None:
        """Create the snapshots table & index if not present."""
        with self._lock:
            conn = self._get_conn()
            conn.executescript(_SCHEMA_SQL)

    def close(self) -> None:
        """Close the underlying SQLite connection."""
        with self._lock:
            if self._conn is not None:
                self._conn.close()
                self._conn = None

    # ── Snapshot serialisation ────────────────────────────────────
    @staticmethod
    def _to_jsonable(obj: Any) -> Any:
        """Recursively convert an object to a JSON-serialisable form.

        Handles nested dicts / lists / tuples containing Pydantic models
        (``model_dump(mode="json")``) so ``json.dumps`` never falls back
        to ``str(model)`` which would lose structure.
        """
        if isinstance(obj, dict):
            return {str(k): ConfigHistory._to_jsonable(v) for k, v in obj.items()}
        if isinstance(obj, (list, tuple)):
            return [ConfigHistory._to_jsonable(v) for v in obj]
        if hasattr(obj, "model_dump"):
            try:
                return obj.model_dump(mode="json")
            except TypeError:  # older pydantic without mode kwarg
                return obj.model_dump()
        # Primitives (str/int/float/bool/None) and anything else pass through;
        # json.dumps(default=str) below catches the remaining edge cases.
        return obj

    @staticmethod
    def _serialise(config: Any) -> str:
        """Convert a config object to a JSON string.

        Accepts:
          * ``dict`` — serialised directly (nested pydantic models handled)
          * Pydantic ``BaseModel`` — uses ``model_dump(mode="json")``
          * objects with ``__dict__`` — best-effort ``vars()`` dump
          * anything else — wrapped as ``{"value": <repr>}`` if JSON-safe
        """
        if isinstance(config, dict):
            data = ConfigHistory._to_jsonable(config)
        elif hasattr(config, "model_dump"):
            try:
                data = config.model_dump(mode="json")
            except TypeError:  # older pydantic without mode kwarg
                data = config.model_dump()
        elif hasattr(config, "__dict__"):
            data = ConfigHistory._to_jsonable(vars(config))
        else:
            data = {"value": repr(config)}
        return json.dumps(data, default=str, ensure_ascii=False, sort_keys=True)

    @staticmethod
    def _deserialise(snapshot_json: str) -> Any:
        """Parse a stored snapshot JSON back to a Python object."""
        return json.loads(snapshot_json)

    # ── Public API ────────────────────────────────────────────────
    def save_snapshot(self, config: Any, changed_by: str = "system") -> dict[str, Any]:
        """Persist a new configuration snapshot.

        Returns the stored record dict (``version``, ``changed_by``,
        ``changed_at``, ``snapshot``).
        """
        snapshot_json = self._serialise(config)
        changed_at = _utc_now_iso()
        with self._lock:
            conn = self._get_conn()
            # Determine next version atomically.
            row = conn.execute(
                "SELECT COALESCE(MAX(version), 0) + 1 AS next_v FROM config_snapshots"
            ).fetchone()
            next_version = int(row["next_v"])
            conn.execute(
                "INSERT INTO config_snapshots(version, snapshot_json, changed_by, changed_at) "
                "VALUES (?, ?, ?, ?)",
                (next_version, snapshot_json, changed_by, changed_at),
            )
        logger.info(
            "[config-history] Saved snapshot version=%d by=%s", next_version, changed_by,
        )
        return {
            "version": next_version,
            "changed_by": changed_by,
            "changed_at": changed_at,
            "snapshot": self._deserialise(snapshot_json),
        }

    def list_history(self, limit: int = 50) -> list[dict[str, Any]]:
        """Return recent snapshots (newest first), excluding snapshot payload.

        ``limit`` is clamped to [1, 500] to bound response size.
        """
        limit = max(1, min(int(limit), 500))
        with self._lock:
            conn = self._get_conn()
            rows = conn.execute(
                "SELECT version, changed_by, changed_at FROM config_snapshots "
                "ORDER BY version DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [
            {"version": r["version"], "changed_by": r["changed_by"], "changed_at": r["changed_at"]}
            for r in rows
        ]

    def get_version(self, version: int) -> dict[str, Any] | None:
        """Return a single snapshot record including the parsed payload.

        Returns ``None`` if ``version`` does not exist.
        """
        with self._lock:
            conn = self._get_conn()
            row = conn.execute(
                "SELECT version, snapshot_json, changed_by, changed_at "
                "FROM config_snapshots WHERE version = ?",
                (int(version),),
            ).fetchone()
        if row is None:
            return None
        return {
            "version": row["version"],
            "changed_by": row["changed_by"],
            "changed_at": row["changed_at"],
            "snapshot": self._deserialise(row["snapshot_json"]),
        }

    def latest_version(self) -> int | None:
        """Return the highest version number, or ``None`` if no snapshots."""
        with self._lock:
            conn = self._get_conn()
            row = conn.execute(
                "SELECT MAX(version) AS v FROM config_snapshots"
            ).fetchone()
        if row is None or row["v"] is None:
            return None
        return int(row["v"])

    def rollback(self, version: int) -> dict[str, Any]:
        """Restore the configuration to a previously saved snapshot.

        Steps:
          1. Load the target snapshot from the DB.
          2. Save a *new* snapshot with the restored payload so the
             rollback itself is auditable (version N+1 = copy of N).
          3. Fire a ``config_changed`` event on the global event bus.

        Raises
        ------
        ValueError
            If ``version`` does not exist.
        """
        record = self.get_version(version)
        if record is None:
            raise ValueError(f"Config version {version} not found")

        # Re-save the restored snapshot so the rollback is itself recorded
        # in the timeline (audit trail: who rolled back to what, when).
        restored = self.save_snapshot(
            config=record["snapshot"],
            changed_by=f"rollback:v{version}",
        )

        # Fire event bus notification (lazy import avoids circular dep).
        try:
            from maop.core.event_bus import Event, get_event_bus
            bus = get_event_bus()
            event = Event(
                topic=CONFIG_CHANGED_TOPIC,
                data={
                    "action": "rollback",
                    "restored_from_version": int(version),
                    "new_version": restored["version"],
                    "changed_by": restored["changed_by"],
                    "changed_at": restored["changed_at"],
                },
                source="config_history",
            )
            # publish_sync works in both sync and async contexts.
            bus.publish_sync(event)
        except Exception as exc:  # pragma: no cover — event bus best-effort
            logger.warning("[config-history] Failed to fire %s event: %s", CONFIG_CHANGED_TOPIC, exc)

        logger.info(
            "[config-history] Rolled back to version=%d (new version=%d)",
            version, restored["version"],
        )
        return restored


# ── Process-wide singleton ─────────────────────────────────────────
_global_history: ConfigHistory | None = None
_global_history_lock = threading.Lock()


def get_config_history(db_path: str | Path | None = None) -> ConfigHistory:
    """Return the process-wide :class:`ConfigHistory` singleton.

    The first caller may pass ``db_path`` to override the default
    location; subsequent calls ignore the argument and return the
    existing instance.
    """
    global _global_history
    if _global_history is None:
        with _global_history_lock:
            if _global_history is None:
                _global_history = ConfigHistory(db_path=db_path)
    return _global_history


def reset_config_history() -> None:
    """Drop the singleton (used by tests for isolation)."""
    global _global_history
    with _global_history_lock:
        if _global_history is not None:
            _global_history.close()
        _global_history = None