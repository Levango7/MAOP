"""Prompts / human-proxy / coordination endpoints for :class:`DataProxy`."""

from __future__ import annotations

import logging
import time
from typing import Any

logger = logging.getLogger(__name__)


class PromptsMixin:
    """Prompt, human-proxy, and coordination endpoints.

    Provides:
        - ``prompts_list``         — prompt templates
        - ``human_pending``        — human proxy pending requests
        - ``coordination_report``  — coordination/teams report
    """

    async def human_pending(self) -> dict[str, Any]:
        """Human proxy pending requests — replaces human-proxy.ps1 -Action pending."""
        start = time.monotonic()
        try:
            if self._human_proxy is None:
                from maop.core.agent.delegation.human_proxy import HumanProxy
                self._human_proxy = HumanProxy(root_dir=self._root)
            pending = self._human_proxy.pending()
            stats = self._human_proxy.stats()
            result = {
                "pending": [r.model_dump() for r in pending],
                "stats": stats,
            }
        except Exception as exc:
            logger.warning("[bridge] human_pending failed: %s", exc)
            result = {"pending": [], "stats": {}}
        self._record_latency(start)
        return result

    async def prompts_list(self) -> dict[str, Any]:
        """Prompt templates — replaces prompt-manager.ps1 -Action list."""
        start = time.monotonic()
        try:
            from maop.prompt_manager import PromptManager
            mgr = PromptManager(root_dir=self._root)
            templates = mgr.list_templates()
            stats = mgr.stats()
            result = {
                "templates": [t.model_dump() for t in templates],
                "stats": stats,
            }
        except Exception as exc:
            logger.warning("[bridge] prompts_list failed: %s", exc)
            result = {"templates": [], "stats": {}}
        self._record_latency(start)
        return result

    async def coordination_report(self) -> dict[str, Any]:
        """Coordination/teams report — pure Python from queue.db + config."""
        start = time.monotonic()
        try:
            queue = await self.queue_stats()
            config_agents = await self._query_maop(
                "SELECT agent, COUNT(*) as cnt FROM delegations GROUP BY agent"
            )
            # Build teams from agent config groups
            teams = []
            try:
                from maop.config.loader import ConfigLoader
                cfg = ConfigLoader(project_root=str(self._root)).load()
                groups: dict[str, list[str]] = {}
                for name, ad in cfg.agents.items():
                    group = getattr(ad, "group", "default")
                    groups.setdefault(group, []).append(name)
                teams = [{"team": k, "agents": v, "count": len(v)} for k, v in groups.items()]
            except Exception as exc:
                # H1: log instead of silently swallowing
                logger.warning("[bridge] silent except: %s", exc)
            result = {
                "queue": queue,
                "active_agents": [r["agent"] for r in config_agents],
                "teams": teams,
            }
        except Exception as exc:
            logger.warning("[bridge] coordination_report failed: %s", exc)
            result = {"queue": {}, "active_agents": [], "teams": []}
        self._record_latency(start)
        return result