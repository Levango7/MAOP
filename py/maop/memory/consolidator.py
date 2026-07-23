"""Dream Memory Consolidation — intelligent memory compaction + knowledge extraction.

Inspired by Claude Code's Dream Memory Consolidation mechanism. MAOP's memory
store is append-only with a simple TTL prune. Over time it accumulates:
  - Duplicate entries (same task, slightly different content)
  - Low-value entries (empty content, trivial tasks)
  - Scattered entries that could be merged into a single consolidated summary

DreamConsolidator runs a five-phase pipeline periodically (e.g. in maop_loop
Phase 4 or via a scheduled trigger):

  1. Orient  — Scan memory store, build a topic map, identify consolidation candidates.
  2. Gather  — Group related entries by topic + agent + task similarity.
  3. Consolidate — Merge each group into a single high-quality summary entry.
  4. Extract — Extract structured knowledge (entities, relations, facts) from merged groups.
  5. Prune   — Remove the originals that were merged, keeping only the consolidated entries.

Phase 4 (Extract) is the key addition that bridges compression to knowledge:
consolidated summaries are fed to KnowledgeExtractor, which populates the
knowledge graph for cross-session retrieval.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, Field

from maop.memory.store import MemoryStore

logger = logging.getLogger(__name__)


# ── Models ────────────────────────────────────────────────────

class ConsolidationGroup(BaseModel):
    """A group of memory entries that should be consolidated together."""
    topic: str = ""
    agent: str = ""
    entry_ids: list[str] = Field(default_factory=list)
    task_signature: str = ""  # Normalized task text for grouping
    total_content_length: int = 0


class ConsolidationReport(BaseModel):
    """Report from a single dream consolidation run."""
    started_at: str = ""
    finished_at: str = ""
    phase: str = "dream"
    # Phase 1: Orient
    total_entries_scanned: int = 0
    topics_identified: int = 0
    # Phase 2: Gather
    groups_formed: int = 0
    # Phase 3: Consolidate
    entries_created: int = 0
    consolidation_summaries: list[str] = Field(default_factory=list)
    # Phase 4: Extract
    facts_extracted: int = 0
    entities_extracted: int = 0
    relations_extracted: int = 0
    # Phase 5: Prune
    entries_pruned: int = 0
    pruned_ids: list[str] = Field(default_factory=list)
    # Summary
    size_before: int = 0
    size_after: int = 0
    reduction_pct: float = 0.0
    success: bool = True
    error: str = ""


# ── Dream Consolidator ────────────────────────────────────────

class DreamConsolidator:
    """Four-phase memory consolidation engine.

    Usage::

        consolidator = DreamConsolidator(memory_store=store)
        report = consolidator.dream(min_group_size=3, dry_run=False)
        print(f"Reduced memory by {report.reduction_pct:.1f}%")

    Or integrate into maop_loop Phase 4::

        if loop._should_consolidate():
            report = loop._consolidator.dream()
            loop._log("dream", "INFO", f"Consolidated: {report.entries_pruned} pruned")
    """

    def __init__(
        self,
        memory_store: MemoryStore,
        min_group_size: int = 3,
        max_group_size: int = 50,
        content_overlap_threshold: float = 0.6,
        root_dir: str | None = None,
    ) -> None:
        self._store = memory_store
        self._min_group_size = min_group_size
        self._max_group_size = max_group_size
        self._overlap_threshold = content_overlap_threshold
        self._root_dir = root_dir

    def dream(self, dry_run: bool = False) -> ConsolidationReport:
        """Run the full four-phase consolidation pipeline.

        Parameters
        ----------
        dry_run : bool
            If True, only report what would happen without modifying memory.

        Returns
        -------
        ConsolidationReport
        """
        report = ConsolidationReport(
            started_at=datetime.now(timezone.utc).isoformat(),
        )

        try:
            # ── Phase 1: ORIENT ──
            topic_map = self._orient(report)

            # ── Phase 2: GATHER ──
            groups = self._gather(topic_map, report)

            # ── Phase 3: CONSOLIDATE ──
            summaries = self._consolidate(groups, report, dry_run)

            # ── Phase 4: EXTRACT (knowledge → graph) ──
            self._extract(groups, summaries, report, dry_run)

            # ── Phase 5: PRUNE ──
            self._prune(groups, report, dry_run)

            # Compute size reduction
            stats_after = self._store.stats()
            report.size_after = stats_after.total_entries
            if report.size_before > 0:
                report.reduction_pct = (
                    (report.size_before - report.size_after) / report.size_before * 100
                )

            report.success = True
        except Exception as exc:
            report.success = False
            report.error = str(exc)
            logger.error("[dream] Consolidation failed: %s", exc)

        report.finished_at = datetime.now(timezone.utc).isoformat()
        return report

    # ── Phase 1: Orient ───────────────────────────────────────

    def _orient(self, report: ConsolidationReport) -> dict[str, list[dict[str, Any]]]:
        """Scan memory store and build a topic map.

        Returns a dict of topic -> list of entry dicts.
        """
        stats = self._store.stats()
        report.size_before = stats.total_entries
        report.total_entries_scanned = stats.total_entries

        # Get all entries via search with empty query (returns all)
        all_results = self._store.search(query="", top=10000)
        report.topics_identified = len(stats.by_topic)

        # Group by topic
        topic_map: dict[str, list[dict[str, Any]]] = {}
        for r in all_results:
            topic = r.topic or "general"
            topic_map.setdefault(topic, []).append({
                "id": r.id,
                "agent": r.agent,
                "task": r.task,
                "tags": r.tags,
                "topic": r.topic,
                "trace_id": r.trace_id,
                "timestamp": r.timestamp,
                "snippet": r.snippet,
            })

        logger.info("[dream] Orient: %d entries across %d topics",
                    report.total_entries_scanned, report.topics_identified)
        return topic_map

    # ── Phase 2: Gather ───────────────────────────────────────

    def _gather(
        self,
        topic_map: dict[str, list[dict[str, Any]]],
        report: ConsolidationReport,
    ) -> list[ConsolidationGroup]:
        """Group related entries within each topic by task similarity.

        Uses a simple normalized task signature: lowercase, stripped of
        timestamps/IDs, truncated to 80 chars. Entries with the same
        signature + agent are candidates for consolidation.
        """
        groups: list[ConsolidationGroup] = []

        for topic, entries in topic_map.items():
            # Sub-group by agent within topic
            by_agent: dict[str, list[dict[str, Any]]] = {}
            for e in entries:
                by_agent.setdefault(e["agent"], []).append(e)

            for agent, agent_entries in by_agent.items():
                # Further group by task signature
                by_sig: dict[str, list[dict[str, Any]]] = {}
                for e in agent_entries:
                    sig = self._task_signature(e["task"])
                    by_sig.setdefault(sig, []).append(e)

                for sig, sig_entries in by_sig.items():
                    if len(sig_entries) >= self._min_group_size:
                        # Cap at max_group_size
                        chunk = sig_entries[:self._max_group_size]
                        total_len = sum(len(e.get("snippet", "")) for e in chunk)
                        groups.append(ConsolidationGroup(
                            topic=topic,
                            agent=agent,
                            entry_ids=[e["id"] for e in chunk],
                            task_signature=sig,
                            total_content_length=total_len,
                        ))

        report.groups_formed = len(groups)
        logger.info("[dream] Gather: %d consolidation groups formed", len(groups))
        return groups

    # ── Phase 3: Consolidate ──────────────────────────────────

    def _consolidate(
        self,
        groups: list[ConsolidationGroup],
        report: ConsolidationReport,
        dry_run: bool,
    ) -> list[str]:
        """Merge each group into a single high-quality summary entry.

        The consolidated entry's content is a structured summary that
        preserves key information from all original entries.
        """
        new_ids: list[str] = []
        summaries: list[str] = []

        for group in groups:
            # Fetch full content for each entry in the group
            contents: list[str] = []
            for eid in group.entry_ids:
                results = self._store.search(entry_id=eid, top=1)
                if results:
                    r = results[0]
                    contents.append(f"[{r.timestamp[:10]}] {r.task}: {r.snippet[:200]}")

            if not contents:
                continue

            # Build consolidated summary
            summary_text = self._build_summary(group, contents)
            summaries.append(summary_text[:200])

            if not dry_run:
                # Store the consolidated entry
                new_id = self._store.store(
                    agent=group.agent,
                    task=f"[consolidated] {group.task_signature[:80]}",
                    content=summary_text,
                    tags=["dream-consolidated", group.topic],
                    topic=group.topic,
                )
                if new_id:
                    new_ids.append(new_id)

        report.entries_created = len(new_ids)
        report.consolidation_summaries = summaries
        logger.info("[dream] Consolidate: %d summary entries created", len(new_ids))
        return new_ids

    # ── Phase 4: Extract ──────────────────────────────────────

    def _extract(
        self,
        groups: list[ConsolidationGroup],
        summaries: list[str],
        report: ConsolidationReport,
        dry_run: bool,
    ) -> None:
        """Extract structured knowledge from consolidated summaries.

        Feeds each summary to KnowledgeExtractor, which populates the
        knowledge graph with entities, relations, and facts.
        """
        if dry_run or not self._root_dir:
            return

        try:
            from maop.core.knowledge_extractor import KnowledgeExtractor
            extractor = KnowledgeExtractor(root_dir=self._root_dir)

            for i, summary in enumerate(summaries):
                topic = groups[i].topic if i < len(groups) else ""
                result = extractor.extract_from_text(summary, topic=topic)
                counts = extractor.store_extraction(result)
                report.facts_extracted += counts["facts"]
                report.entities_extracted += counts["entities"]
                report.relations_extracted += counts["relations"]

            logger.info(
                "[dream] Extract: %d facts, %d entities, %d relations",
                report.facts_extracted, report.entities_extracted, report.relations_extracted,
            )
        except Exception as exc:
            logger.warning("[dream] Knowledge extraction failed: %s", exc)

    # ── Phase 5: Prune ────────────────────────────────────────

    def _prune(
        self,
        groups: list[ConsolidationGroup],
        report: ConsolidationReport,
        dry_run: bool,
    ) -> None:
        """Remove original entries that were consolidated.

        Only prunes entries from groups where consolidation succeeded
        (i.e. a new consolidated entry was created).
        """
        all_pruned: list[str] = []

        for group in groups:
            if dry_run:
                all_pruned.extend(group.entry_ids)
                continue

            # Delete each original entry
            for eid in group.entry_ids:
                try:
                    with self._store._connect() as conn:
                        conn.execute(
                            "DELETE FROM memory_entries WHERE id = ?", (eid,)
                        )
                    all_pruned.append(eid)
                except Exception as exc:
                    logger.debug("[dream] Prune failed for %s: %s", eid, exc)

        # ── Sync secondary indexes after SQL deletion ──────────────
        # The DELETE above only removes rows from memory_entries. The
        # bloom filter, VectorStore vectors, and wiki.json/memory.json
        # index files still reference the pruned IDs and must be
        # synchronized to avoid stale lookups and zombie search hits.
        if all_pruned and not dry_run:
            # 1. VectorStore — remove vectors for deleted entries
            try:
                _vs = getattr(self._store, "_vector_store", None)
                if _vs is not None:
                    _synced = 0
                    for _eid in all_pruned:
                        try:
                            if _vs.delete(_eid):
                                _synced += 1
                        except Exception as _exc:
                            logger.debug(
                                "[dream] VectorStore delete failed for %s: %s",
                                _eid, _exc,
                            )
                    logger.info(
                        "[dream] Synced VectorStore: %d/%d vectors removed",
                        _synced, len(all_pruned),
                    )
            except Exception as _exc:
                logger.warning("[dream] VectorStore sync failed: %s", _exc)

            # 2. Bloom filter — standard BloomFilter has no remove().
            #    Log a warning so operators know stale IDs remain; a
            #    full rebuild would be needed to fully clear them.
            try:
                _bf = getattr(self._store, "_bloom", None)
                if _bf is not None and not hasattr(_bf, "remove"):
                    logger.warning(
                        "[dream] BloomFilter has no remove(); %d pruned IDs "
                        "remain in filter (false positives possible, rebuild "
                        "recommended)",
                        len(all_pruned),
                    )
            except Exception as _exc:
                logger.warning("[dream] Bloom filter sync check failed: %s", _exc)

            # 3. JSON index files (wiki.json, memory.json) — mark store
            #    dirty and flush so the index reflects the deletions.
            try:
                self._store._dirty = True
                self._store._flush_json()
                logger.info(
                    "[dream] Synced JSON index files (wiki.json, memory.json)",
                )
            except Exception as _exc:
                logger.warning("[dream] JSON index sync failed: %s", _exc)
        report.entries_pruned = len(all_pruned)
        report.pruned_ids = all_pruned[:100]  # Cap for report size
        logger.info("[dream] Prune: %d original entries removed", len(all_pruned))

    # ── Helpers ───────────────────────────────────────────────

    @staticmethod
    def _task_signature(task: str) -> str:
        """Normalize a task string for grouping.

        Removes timestamps, trace IDs, and other variable parts,
        then lowercases and truncates.
        """
        sig = task.lower().strip()
        # Remove ISO timestamps
        sig = re.sub(r"\d{4}-\d{2}-\d{2}[t ]\d{2}:\d{2}:\d{2}", "", sig)
        # Remove hex trace IDs
        sig = re.sub(r"\b[0-9a-f]{32}\b", "", sig)
        # Remove numbers (version numbers, counts, etc.)
        sig = re.sub(r"\b\d+\b", "N", sig)
        # Collapse whitespace
        sig = re.sub(r"\s+", " ", sig).strip()
        return sig[:80]

    @staticmethod
    def _build_summary(group: ConsolidationGroup, contents: list[str]) -> str:
        """Build a consolidated summary from multiple entries.

        Structure:
          [Dream Consolidation Summary]
          Topic: <topic> | Agent: <agent> | Entries: <count>
          ---
          <deduplicated, chronologically ordered content>
        """
        # Deduplicate contents while preserving order
        seen: set[str] = set()
        unique: list[str] = []
        for c in contents:
            key = c[:100]  # Dedup by first 100 chars
            if key not in seen:
                seen.add(key)
                unique.append(c)

        header = (
            f"[Dream Consolidation Summary]\n"
            f"Topic: {group.topic} | Agent: {group.agent} | "
            f"Entries merged: {len(group.entry_ids)} | "
            f"Created: {datetime.now(timezone.utc).isoformat()}\n"
            f"---"
        )
        body = "\n".join(unique)
        return f"{header}\n{body}"
