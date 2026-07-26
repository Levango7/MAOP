"""MAOP Memory Search — SearchMixin for MemoryStore.

Extracted from store.py for single-responsibility separation.
Provides FTS5 full-text search, regex fallback, facets, and JSON1 queries.
"""

from __future__ import annotations

import logging
import re
from typing import Any

from maop.memory.models import (
    FacetResult,
    SearchResult,
    expand_keywords,
)

logger = logging.getLogger(__name__)


class SearchMixin:
    """Mixin providing search methods for MemoryStore.

    Requires the host class to have:
      - self._query(sql, params) -> list[dict]
      - self._fts5_available: bool
      - self._vector_store (optional)
    """

    _query: Any
    _fts5_available: bool
    _vector_store: Any

    def search(
        self,
        query: str = "",
        agent: str = "",
        trace_id: str = "",
        entry_id: str = "",
        since: str = "",
        until: str = "",
        top: int = 10,
        highlight: bool = True,
    ) -> list[SearchResult]:
        """Search memory entries with FTS5 (primary) or regex (fallback).

        Parameters
        ----------
        query : str
            Search query. Uses FTS5 MATCH with BM25 ranking when available.
        highlight : bool
            If True, include FTS5 snippet() with <b> markup in results.
        """
        # Filter by ID — direct lookup
        if entry_id:
            rows = self._query(
                "SELECT * FROM memory_entries WHERE id = ? LIMIT 1",
                (entry_id,),
            )
            if rows:
                r = rows[0]
                return [SearchResult(
                    id=r["id"], agent=r["agent"], task=r["task"],
                    tags=r["tags"], topic=r["topic"],
                    trace_id=r["trace_id"], timestamp=r["timestamp"],
                    snippet=r["content"][:120],
                )]
            return []

        # FTS5 path (600x faster for keyword search)
        if query and self._fts5_available:
            fts_results = self._search_fts5(query, agent, trace_id, since, until, top, highlight)
            # Hybrid: combine with vector semantic search if available
            if self._vector_store is not None:
                try:
                    vec_results = self._vector_store.search(query, top=top)
                    # Merge: FTS5 results get priority, vector results supplement
                    fts_ids = {r.id for r in fts_results}
                    for vr in vec_results:
                        if vr.id not in fts_ids:
                            # Find matching memory entry for metadata
                            mem_rows = self._query(
                                "SELECT * FROM memory_entries WHERE id = ? LIMIT 1", (vr.id,)
                            )
                            if mem_rows:
                                m = mem_rows[0]
                                fts_results.append(SearchResult(
                                    id=m["id"], agent=m["agent"], task=m["task"],
                                    tags=m["tags"], topic=m["topic"],
                                    trace_id=m["trace_id"], timestamp=m["timestamp"],
                                    score=vr.score * 10,  # Scale vector score
                                    snippet=m["content"].replace("\n", " ")[:120],
                                ))
                    # Re-sort by score
                    fts_results.sort(key=lambda r: r.score, reverse=True)
                    return fts_results[:top]
                except Exception as exc:
                    logger.debug("[mem] Vector search supplement failed: %s", exc)
            return fts_results

        # Regex fallback (original behavior)
        if query:
            return self._search_regex(query, agent, trace_id, since, until, top)

        # No query — return recent entries
        conditions = []
        params: list[Any] = []
        if agent:
            conditions.append("agent = ?")
            params.append(agent)
        if trace_id:
            conditions.append("trace_id = ?")
            params.append(trace_id)
        if since:
            conditions.append("timestamp >= ?")
            params.append(since)
        if until:
            conditions.append("timestamp <= ?")
            params.append(until)

        where_clause = " AND ".join(conditions)
        where_sql = f"WHERE {where_clause}" if where_clause else ""

        rows = self._query(
            f"SELECT * FROM memory_entries {where_sql} ORDER BY timestamp DESC LIMIT ?",
            params + [top],
        )
        return [SearchResult(
            id=r["id"], agent=r["agent"], task=r["task"],
            tags=r["tags"], topic=r["topic"],
            trace_id=r["trace_id"], timestamp=r["timestamp"],
            snippet=r["content"].replace("\n", " ")[:120],
        ) for r in rows]

    def _search_fts5(
        self,
        query: str,
        agent: str = "",
        trace_id: str = "",
        since: str = "",
        until: str = "",
        top: int = 10,
        highlight: bool = True,
    ) -> list[SearchResult]:
        """FTS5 full-text search with BM25 ranking and snippet highlighting."""
        keywords = expand_keywords(query)
        # Build FTS5 MATCH expression: keyword1 OR keyword2 OR ...
        match_expr = " OR ".join(f'"{kw}"' for kw in keywords)

        # Build filter conditions on the main table
        conditions = []
        params: list[Any] = []
        if agent:
            conditions.append("m.agent = ?")
            params.append(agent)
        if trace_id:
            conditions.append("m.trace_id = ?")
            params.append(trace_id)
        if since:
            conditions.append("m.timestamp >= ?")
            params.append(since)
        if until:
            conditions.append("m.timestamp <= ?")
            params.append(until)

        where_clause = " AND ".join(conditions)
        where_sql = f"AND {where_clause}" if where_clause else ""

        try:
            if highlight:
                sql = f"""
                    SELECT m.id, m.agent, m.task, m.tags, m.topic,
                           m.trace_id, m.timestamp,
                           bm25(memory_fts) as score,
                           snippet(memory_fts, 3, '<b>', '</b>', '...', 30) as highlighted,
                           snippet(memory_fts, 3, '', '', '...', 60) as snippet
                    FROM memory_fts
                    JOIN memory_entries m ON m.id = memory_fts.id
                    WHERE memory_fts MATCH ? {where_sql}
                    ORDER BY bm25(memory_fts)
                    LIMIT ?
                """
            else:
                sql = f"""
                    SELECT m.id, m.agent, m.task, m.tags, m.topic,
                           m.trace_id, m.timestamp,
                           bm25(memory_fts) as score,
                           m.content AS snippet
                    FROM memory_fts
                    JOIN memory_entries m ON m.id = memory_fts.id
                    WHERE memory_fts MATCH ? {where_sql}
                    ORDER BY bm25(memory_fts)
                    LIMIT ?
                """

            rows = self._query(sql, [match_expr] + params + [top])

            results = []
            for r in rows:
                # BM25 returns negative scores; negate for intuitive ranking
                score = -r["score"] if r["score"] < 0 else r["score"]
                results.append(SearchResult(
                    id=r["id"], agent=r["agent"], task=r["task"],
                    tags=r["tags"], topic=r["topic"],
                    trace_id=r["trace_id"], timestamp=r["timestamp"],
                    score=score,
                    snippet=r.get("snippet", "").replace("\n", " ")[:120],
                    highlighted=r.get("highlighted", ""),
                ))
            return results

        except Exception as exc:
            logger.warning("[mem] FTS5 search failed, falling back to regex: %s", exc)
            return self._search_regex(query, agent, trace_id, since, until, top)

    def _search_regex(
        self,
        query: str,
        agent: str = "",
        trace_id: str = "",
        since: str = "",
        until: str = "",
        top: int = 10,
    ) -> list[SearchResult]:
        """Fallback regex search (original O(N) behavior)."""
        keywords = expand_keywords(query)

        conditions = []
        params: list[Any] = []
        if agent:
            conditions.append("agent = ?")
            params.append(agent)
        if trace_id:
            conditions.append("trace_id = ?")
            params.append(trace_id)
        if since:
            conditions.append("timestamp >= ?")
            params.append(since)
        if until:
            conditions.append("timestamp <= ?")
            params.append(until)

        where_clause = " AND ".join(conditions)
        where_sql = f"WHERE {where_clause}" if where_clause else ""

        rows = self._query(
            f"SELECT * FROM memory_entries {where_sql} ORDER BY timestamp DESC",
            params,
        )
        scored: list[SearchResult] = []
        for r in rows:
            search_text = f"{r['task']} {r['content']} {r['agent']} {r['tags']}"
            score = 0
            for kw in keywords:
                score += len(re.findall(re.escape(kw), search_text, re.IGNORECASE))
            if score > 0:
                snippet = r["content"].replace("\n", " ")[:120]
                scored.append(SearchResult(
                    id=r["id"], agent=r["agent"], task=r["task"],
                    tags=r["tags"], topic=r["topic"],
                    trace_id=r["trace_id"], timestamp=r["timestamp"],
                    score=score, snippet=snippet,
                ))
        scored.sort(key=lambda res: res.score, reverse=True)
        return scored[:top]

    # ── FACETS (ES-like aggregation) ─────────────────────

    def facets(
        self,
        query: str = "",
        field: str = "topic",
        top: int = 20,
    ) -> list[FacetResult]:
        """Aggregate search results by a field (ES-like facet/terms aggregation).

        Parameters
        ----------
        query : str
            Search query to filter results before aggregation.
        field : str
            Field to aggregate by: 'topic', 'agent', 'tags'.
        top : int
            Maximum number of facet values to return.

        Returns
        -------
        list[FacetResult]
            Facet values sorted by count descending.
        """
        allowed_fields = {"topic", "agent", "tags"}
        if field not in allowed_fields:
            field = "topic"

        if query and self._fts5_available:
            keywords = expand_keywords(query)
            match_expr = " OR ".join(f'"{kw}"' for kw in keywords)
            sql = f"""
                SELECT m.{field} as facet_val, COUNT(*) as cnt
                FROM memory_fts
                JOIN memory_entries m ON m.id = memory_fts.id
                WHERE memory_fts MATCH ?
                GROUP BY m.{field}
                ORDER BY cnt DESC
                LIMIT ?
            """
            rows = self._query(sql, (match_expr, top))
        else:
            sql = f"""
                SELECT {field} as facet_val, COUNT(*) as cnt
                FROM memory_entries
                GROUP BY {field}
                ORDER BY cnt DESC
                LIMIT ?
            """
            rows = self._query(sql, (top,))

        return [FacetResult(facet=field, value=r["facet_val"], count=r["cnt"]) for r in rows]

    # ── JSON1 semi-structured queries (P2-2) ─────────────

    def search_json(
        self,
        json_path: str,
        value: str,
        top: int = 10,
    ) -> list[SearchResult]:
        """Search entries where json_extract(content, json_path) = value.

        Requires content to be valid JSON. Uses SQLite JSON1 extension.

        Parameters
        ----------
        json_path : str
            JSON path expression, e.g. '$.type' or '$.metadata.key'.
        value : str
            Value to match.
        top : int
            Maximum results.

        Example::

            results = store.search_json("$.type", "bug_report")
        """
        sql = """
            SELECT * FROM memory_entries
            WHERE json_extract(content, ?) = ?
            ORDER BY timestamp DESC LIMIT ?
        """
        try:
            rows = self._query(sql, (json_path, value, top))
        except Exception as exc:
            logger.warning("[mem] JSON1 search failed: %s", exc)
            return []

        return [SearchResult(
            id=r["id"], agent=r["agent"], task=r["task"],
            tags=r["tags"], topic=r["topic"],
            trace_id=r["trace_id"], timestamp=r["timestamp"],
            snippet=r["content"].replace("\n", " ")[:120],
        ) for r in rows]
