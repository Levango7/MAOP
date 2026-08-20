"""Memory / guardrail / providers / graph endpoints for :class:`DataProxy`."""

from __future__ import annotations

import asyncio
import logging
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


class ModelsMixin:
    """Memory, guardrail, providers, and graph endpoints.

    Provides:
        - ``memory_stats``     — memory.db statistics
        - ``guardrail_report`` — guardrail rules report
        - ``providers_report`` — agent availability from circuit breaker
        - ``graph_nodes``      — memory graph nodes
        - ``graph_edges``      — memory graph edges
    """

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
