"""MAOP Memory — SQLite-backed persistent memory store with FTS5 full-text search.

Three-tier memory store with FTS5 and vector search.: store/search/trace/trajectory/inject/stats/prune
with SQLite-backed storage, FTS5 full-text index (replaces O(N) regex scan),
synonym expansion, snippet highlighting, and facet aggregation.

FTS5 provides:
  - Inverted index with BM25 ranking (600x faster than regex for 100k+ entries)
  - snippet() for highlighted search results (ES-like)
  - Facet aggregation via GROUP BY on FTS results
  - Zero external dependencies (SQLite built-in)

Architecture: models and DDL live in ``MAOP.memory.models``, search logic
in ``MAOP.memory.search.SearchMixin``.  This module re-exports all public
symbols for backward compatibility.
"""

from __future__ import annotations

import asyncio
import logging
import sqlite3
import uuid
from collections.abc import Generator
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from maop.core.db_utils import get_db_path

# ── Re-exports from models.py (backward compatibility) ───────
from maop.memory.models import (  # noqa: F401
    _FTS5_DDL,
    _MEMORY_DDL,
    SYNONYM_MAP,
    FacetResult,
    MemoryEntry,
    MemoryStats,
    SearchResult,
    TraceEntry,
    TrajectoryStep,
    _is_valid_id,
    _new_id,
    expand_keywords,
)

# ── Search logic from search.py ──────────────────────────────
from maop.memory.search import SearchMixin

logger = logging.getLogger(__name__)


# ── MemoryStore ───────────────────────────────────────────────

class MemoryStore(SearchMixin):
    """SQLite-backed persistent memory store with FTS5 full-text search.

    Usage::

        store = MemoryStore(root_dir="/path/to/MAOP")
        entry_id = store.store(agent="claude", task="fix bug", content="...")
        results = store.search(query="bug fix", top=5)
        facets = store.facets(query="bug", field="topic")
    """

    def __init__(self, root_dir: str | Path | None = None) -> None:
        if root_dir is None:
            from maop.core.db_utils import find_project_root
            root_dir = find_project_root()
        self._root = Path(root_dir)
        self._data_dir = self._root / "data"
        self._data_dir.mkdir(parents=True, exist_ok=True)
        self._db_path = get_db_path("memory")
        self._initialized = False
        self._fts5_available = False
        self._dirty = False
        self._dirty_count = 0
        self._init_db()
        self._bloom = self._init_bloom()
        self._vector_store = self._init_vector_store()


    # ── SQLite connection ─────────────────────────────────────

    @contextmanager
    def _connect(self) -> Generator[sqlite3.Connection, None, None]:
        from maop.core.db_utils import sqlite_connect
        with sqlite_connect(self._db_path, timeout=10, wal=True, foreign_keys=True) as conn:
            yield conn

    def _init_db(self) -> None:
        """Initialize SQLite schema with FTS5."""
        try:
            with self._connect() as conn:
                conn.executescript(_MEMORY_DDL)
                # Try to create FTS5 virtual table
                try:
                    conn.executescript(_FTS5_DDL)
                    self._fts5_available = True
                    logger.info("[mem] FTS5 full-text index initialized")
                except Exception as fts_exc:
                    logger.warning("[mem] FTS5 not available, falling back to regex: %s", fts_exc)
                    self._fts5_available = False
            self._initialized = True
        except Exception as exc:
            logger.warning("Failed to initialize memory DB: %s", exc)

    def _init_bloom(self):
        """Initialize bloom filter with existing entry IDs for fast dedup."""
        try:
            from maop.core.bloom_filter import BloomFilter
            bf = BloomFilter(expected_items=50_000, fp_rate=0.01)
            rows = self._query("SELECT id FROM memory_entries")
            for r in rows:
                bf.add(r["id"])
            logger.info("[mem] Bloom filter initialized: %d IDs loaded", len(rows))
            return bf
        except Exception as exc:
            logger.warning("[mem] Bloom filter init failed: %s", exc)
            return None

    def _init_vector_store(self):
        """Initialize VectorStore for hybrid FTS5 + semantic search."""
        try:
            from maop.core.vector import VectorStore
            vs = VectorStore(db_path=self._data_dir / "vectors.db")
            logger.info("[mem] VectorStore initialized for hybrid search")
            return vs
        except Exception as exc:
            logger.debug("[mem] VectorStore init skipped: %s", exc)
            return None

    # ── STORE ─────────────────────────────────────────────

    def store(
        self,
        agent: str,
        task: str,
        content: str = "",
        tags: str | list[str] = "",
        topic: str = "general",
        trace_id: str = "",
        session_id: str = "",
    ) -> str | None:
        """Store a memory entry. Returns entry ID or None on validation failure."""
        if isinstance(tags, str):
            tag_list = [t.strip() for t in tags.split(",") if t.strip()] if tags else []
        else:
            tag_list = list(tags)

        entry = MemoryEntry(
            agent=agent, task=task, content=content,
            tags=tag_list, topic=topic or "general",
            trace_id=trace_id, session_id=session_id,
        )

        if not _is_valid_id(entry.id):
            logger.warning("[mem] Invalid id rejected: %s", entry.id)
            return None

        tags_str = ",".join(tag_list)
        try:
            with self._connect() as conn:
                conn.execute(
                    """INSERT OR REPLACE INTO memory_entries
                       (id, agent, task, content, tags, topic, trace_id,
                        session_id, exit_code, duration_ms, timestamp)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (entry.id, entry.agent, entry.task, entry.content,
                     tags_str, entry.topic, entry.trace_id,
                     entry.session_id, entry.exit_code, entry.duration_ms,
                     entry.timestamp),
                )
            logger.info("[mem] Stored: %s (%s, %s)", entry.id, agent, tags_str)
        except Exception as exc:
            logger.warning("[mem] Store failed: %s", exc)
            return None

        # Add to bloom filter for future dedup
        if self._bloom is not None:
            self._bloom.add(entry.id)

        # Index to VectorStore for hybrid semantic search
        if self._vector_store is not None:
            try:
                index_text = f"{entry.task} {entry.content}"
                self._vector_store.index(
                    entry_id=entry.id,
                    text=index_text[:500],
                    metadata={"agent": entry.agent, "topic": entry.topic, "tags": tags_str},
                )
            except Exception as exc:
                logger.debug("[mem] VectorStore index skipped: %s", exc)

        self._dirty = True
        self._dirty_count += 1
        if self._dirty_count >= 10:
            self._flush_json()

        return entry.id

    def _flush_json(self) -> None:
        """No-op: JSON dual-write removed (T3-1, ADR-011 single source of truth).
        SQLite memory_entries table is the canonical store. Kept for backward compat.
        """
        self._dirty = False
        self._dirty_count = 0
        return
    def close(self) -> None:
        """Flush pending JSON writes and clean up."""
        self._flush_json()

    def _sync_to_wiki(self, entry: MemoryEntry, skip_dedup: bool = False) -> None:
        """No-op: wiki.json dual-write removed (T3-1, ADR-011). SQLite is single source."""
        return
    def _sync_to_memory_index(self, entry: MemoryEntry, skip_dedup: bool = False) -> None:
        """No-op: memory.json dual-write removed (T3-1, ADR-011). SQLite is single source."""
        return
    def _query(self, sql: str, params: tuple | list = ()) -> list[dict[str, Any]]:
        """Execute a SELECT query and return rows as dicts."""
        try:
            with self._connect() as conn:
                cursor = conn.execute(sql, params)
                columns = [desc[0] for desc in cursor.description]
                return [dict(zip(columns, row)) for row in cursor.fetchall()]
        except Exception as exc:
            logger.warning("[mem] Query failed: %s", exc)
            return []

    # ── TRACE ─────────────────────────────────────────────

    def trace(
        self,
        trace_id: str = "",
        parent_trace_id: str = "",
        session_id: str = "",
        task: str = "",
        agent: str = "",
    ) -> str:
        """Create or update a session trace. Returns trace_id."""
        ts = datetime.now(timezone.utc).isoformat()

        try:
            with self._connect() as conn:
                if trace_id:
                    row = conn.execute(
                        "SELECT agents FROM memory_traces WHERE trace_id = ?",
                        (trace_id,),
                    ).fetchone()

                    if row:
                        agents = row["agents"]
                        agent_list = [a for a in agents.split(",") if a] if agents else []
                        if agent and agent not in agent_list:
                            agent_list.append(agent)
                        conn.execute(
                            """UPDATE memory_traces
                               SET agents = ?, last_active = ?
                               WHERE trace_id = ?""",
                            (",".join(agent_list), ts, trace_id),
                        )
                    else:
                        agents_str = agent or ""
                        conn.execute(
                            """INSERT INTO memory_traces
                               (trace_id, parent_trace_id, session_id, task,
                                agents, created, last_active, status)
                               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                            (trace_id, parent_trace_id, session_id, task,
                             agents_str, ts, ts, "active"),
                        )
                else:
                    trace_id = uuid.uuid4().hex
                    agents_str = agent or ""
                    conn.execute(
                        """INSERT INTO memory_traces
                           (trace_id, parent_trace_id, session_id, task,
                            agents, created, last_active, status)
                           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                        (trace_id, parent_trace_id, session_id, task,
                         agents_str, ts, ts, "active"),
                    )
        except Exception as exc:
            logger.warning("[mem] Trace failed: %s", exc)

        return trace_id

    def _load_traces(self) -> list[TraceEntry]:
        """Load all traces from SQLite."""
        rows = self._query("SELECT * FROM memory_traces ORDER BY created DESC")
        traces = []
        for r in rows:
            agents = [a for a in r["agents"].split(",") if a] if r["agents"] else []
            traces.append(TraceEntry(
                trace_id=r["trace_id"],
                parent_trace_id=r["parent_trace_id"],
                session_id=r["session_id"],
                task=r["task"],
                agents=agents,
                created=r["created"],
                last_active=r["last_active"],
                status=r["status"],
            ))
        return traces

    # ── TRAJECTORY ────────────────────────────────────────

    def trajectory(
        self,
        trace_id: str,
        agent: str = "",
        task: str = "",
        tool_name: str = "",
        tool_input: str = "",
        tool_output: str = "",
        duration_ms: int = 0,
        exit_code: int = 0,
    ) -> str:
        """Record a trajectory step. Returns step ID."""
        step = TrajectoryStep(
            trace_id=trace_id, agent=agent, task=task,
            tool_name=tool_name, tool_input=tool_input,
            tool_output=tool_output, duration_ms=duration_ms,
            exit_code=exit_code,
        )
        try:
            with self._connect() as conn:
                conn.execute(
                    """INSERT OR REPLACE INTO memory_trajectory
                       (id, trace_id, agent, task, tool_name, tool_input,
                        tool_output, duration_ms, exit_code, timestamp)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (step.id, step.trace_id, step.agent, step.task,
                     step.tool_name, step.tool_input, step.tool_output,
                     step.duration_ms, step.exit_code, step.timestamp),
                )
        except Exception as exc:
            logger.warning("[mem] Trajectory store failed: %s", exc)
        return step.id

    def get_trajectory(self, trace_id: str) -> list[TrajectoryStep]:
        """Get all trajectory steps for a trace."""
        rows = self._query(
            "SELECT * FROM memory_trajectory WHERE trace_id = ? ORDER BY timestamp",
            (trace_id,),
        )
        return [TrajectoryStep(
            id=r["id"], trace_id=r["trace_id"], agent=r["agent"],
            task=r["task"], tool_name=r["tool_name"],
            tool_input=r["tool_input"], tool_output=r["tool_output"],
            duration_ms=r["duration_ms"], exit_code=r["exit_code"],
            timestamp=r["timestamp"],
        ) for r in rows]

    # ── INJECT ────────────────────────────────────────────

    def inject(self, trace_id: str, top: int = 5) -> str:
        """Build context injection string from memory for a trace."""
        steps = self.get_trajectory(trace_id)
        if not steps:
            return ""

        lines = ["[Memory Context]"]
        for s in steps[-top:]:
            lines.append(f"  {s.agent}: {s.task}")
            if s.tool_output:
                lines.append(f"    >> {s.tool_output[:200]}")
        return "\n".join(lines)

    # ── STATS ─────────────────────────────────────────────

    def stats(self) -> MemoryStats:
        """Compute memory store statistics."""
        entries = self._query("SELECT * FROM memory_entries")
        traces = self._query("SELECT COUNT(*) as cnt FROM memory_traces")
        traj = self._query("SELECT COUNT(*) as cnt FROM memory_trajectory")

        by_agent: dict[str, int] = {}
        by_topic: dict[str, int] = {}
        timestamps = []
        for r in entries:
            by_agent[r["agent"]] = by_agent.get(r["agent"], 0) + 1
            by_topic[r["topic"]] = by_topic.get(r["topic"], 0) + 1
            if r["timestamp"]:
                timestamps.append(r["timestamp"])

        trace_count = traces[0]["cnt"] if traces else 0
        traj_count = traj[0]["cnt"] if traj else 0

        return MemoryStats(
            total_entries=len(entries),
            total_traces=trace_count,
            total_trajectory_steps=traj_count,
            by_agent=by_agent, by_topic=by_topic,
            oldest=min(timestamps) if timestamps else "",
            newest=max(timestamps) if timestamps else "",
        )

    # ── PRUNE ─────────────────────────────────────────────

    def prune(self, ttl_days: int = 30, dry_run: bool = False) -> list[str]:
        """Remove entries older than ttl_days. Returns list of pruned IDs."""
        if ttl_days <= 0:
            return []

        cutoff = datetime.now(timezone.utc) - timedelta(days=ttl_days)
        cutoff_iso = cutoff.isoformat()

        rows = self._query(
            "SELECT id FROM memory_entries WHERE timestamp < ?",
            (cutoff_iso,),
        )
        pruned_ids = [r["id"] for r in rows]

        if not dry_run and pruned_ids:
            try:
                with self._connect() as conn:
                    for eid in pruned_ids:
                        conn.execute("DELETE FROM memory_entries WHERE id = ?", (eid,))
            except Exception as exc:
                logger.warning("[mem] Prune delete failed: %s", exc)

        logger.info("[mem] Pruned %d entries (ttl=%d days, dry_run=%s)",
                     len(pruned_ids), ttl_days, dry_run)
        return pruned_ids

    # ── Async wrappers ────────────────────────────────────

    async def astore(self, *args: Any, **kwargs: Any) -> str | None:
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, lambda: self.store(*args, **kwargs))

    async def asearch(self, *args: Any, **kwargs: Any) -> Any:
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, lambda: self.search(*args, **kwargs))

    async def ainject(self, *args: Any, **kwargs: Any) -> str:
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, lambda: self.inject(*args, **kwargs))
