"""MAOP Supervisor — health checker.

Probes registered agents and produces
:class:`~maop.core.scheduling.models.HealthProbe` snapshots consumed by
the rule engine and the Supervisor patrol loop. Extracted from
``supervisor.py`` to isolate probe execution (asyncio + dispatcher +
MetricsCollector) from the orchestration logic.

References
----------
- docs/design-supervisor-agent.md §2.4 (patrol / probe design)
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from maop.core.scheduling.failure_detector import FailurePatternDetector
from maop.core.scheduling.models import HealthProbe

logger = logging.getLogger(__name__)

# Default patrol concurrency (parallel probes per patrol round).
_DEFAULT_PATROL_CONCURRENCY = 10

# ── Health checker ─────────────────────────────────────────────


class HealthChecker:
    """Health probe executor for agents.

    Probes are intentionally lightweight and reuse existing
    infrastructure rather than requiring agents to expose a dedicated
    HTTP health endpoint:

    - **ping** — dispatch a sentinel task ``__health_ping__`` and measure
      round-trip latency. Reuses the Dispatcher path so breaker /
      budget / guardrail all stay in effect. When no dispatcher is
      wired in (tests), ``reachable`` defaults to True and latency 0.
    - **metrics** — read :meth:`FailurePatternDetector.get_agent_health`
      (in-memory sliding window, no I/O).
    - **resource** — read agent-exposed Prometheus gauges from the
      shared :class:`MetricsCollector`. When the agent exposes no
      resource metrics, ``resource_usage`` is an empty dict.
    """

    def __init__(
        self,
        *,
        probe_timeout_s: float = 5.0,
        probe_types: list[str] | None = None,
        registry: Any = None,
        dispatcher: Any = None,
        detector: FailurePatternDetector | None = None,
    ) -> None:
        self._probe_timeout_s = float(probe_timeout_s)
        self._probe_types = list(probe_types or ["ping", "metrics", "resource"])
        self._registry = registry
        self._dispatcher = dispatcher
        self._detector = detector

    async def check(self, agent_id: str) -> HealthProbe:
        """Execute health probes for one agent and return the snapshot."""
        probed_at = time.time()
        reachable = True
        latency_ms = 0.0
        failure_rate = 0.0
        avg_latency = 0.0
        timeout_rate = 0.0
        breaker_open = False
        resource_usage: dict[str, Any] = {}

        # ── ping probe ──
        if "ping" in self._probe_types:
            try:
                reachable, latency_ms = await asyncio.wait_for(
                    self._ping(agent_id), timeout=self._probe_timeout_s,
                )
            except asyncio.TimeoutError:
                reachable = False
                latency_ms = self._probe_timeout_s * 1000.0
            except Exception as exc:
                logger.debug("[health-checker] ping %s failed: %s", agent_id, exc)
                reachable = False

        # ── metrics probe (in-memory, no I/O) ──
        if "metrics" in self._probe_types and self._detector is not None:
            try:
                health = self._detector.get_agent_health(agent_id)
                if health is not None:
                    failure_rate = health.failure_rate
                    avg_latency = health.avg_latency
                    timeout_rate = health.timeout_rate
                    # breaker_open inferred from weight==0 or drained status.
                    breaker_open = health.weight <= 0.0 or health.status == "drained"
            except Exception as exc:
                logger.debug("[health-checker] metrics %s failed: %s", agent_id, exc)

        # ── resource probe (read MetricsCollector gauges) ──
        if "resource" in self._probe_types:
            try:
                resource_usage = self._read_resource_metrics(agent_id)
            except Exception as exc:
                logger.debug("[health-checker] resource %s failed: %s", agent_id, exc)
                resource_usage = {}

        return HealthProbe(
            agent_id=agent_id,
            reachable=reachable,
            latency_ms=round(latency_ms, 3),
            failure_rate=round(failure_rate, 4),
            avg_latency=round(avg_latency, 4),
            timeout_rate=round(timeout_rate, 4),
            breaker_open=breaker_open,
            resource_usage=resource_usage,
            probed_at=probed_at,
        )

    async def _ping(self, agent_id: str) -> tuple[bool, float]:
        """Dispatch a sentinel ping task and measure round-trip latency.

        When no dispatcher is wired in (tests / passive-only mode),
        returns ``(True, 0.0)`` so the agent is treated as reachable
        by default — the metrics probe is then the source of truth.
        """
        if self._dispatcher is None:
            return (True, 0.0)
        start = time.monotonic()
        try:
            result = await self._dispatcher.dispatch(
                agent=agent_id,
                task="__health_ping__",
                routing_key="",
                workdir="",
                timeout_seconds=int(self._probe_timeout_s),
                trace_id="supervisor-ping",
            )
            elapsed_ms = (time.monotonic() - start) * 1000.0
            ok = bool(result) and bool(getattr(result, "result", result))
            return (ok, elapsed_ms)
        except Exception:
            elapsed_ms = (time.monotonic() - start) * 1000.0
            return (False, elapsed_ms)

    def _read_resource_metrics(self, agent_id: str) -> dict[str, Any]:
        """Read agent-exposed resource metrics from the MetricsCollector.

        Returns an empty dict when the agent exposes no resource metrics
        (the common case — agents currently only expose maop_agent_*
        gauges which are already captured by the metrics probe).
        """
        # The MetricsCollector does not currently expose a per-label
        # read API; resource_usage is left empty in v1. When agents
        # start exposing cpu_percent / memory_percent gauges, this
        # method will be extended to read them.
        _ = agent_id
        return {}

    async def check_all(self, agent_ids: list[str]) -> list[HealthProbe]:
        """Probe all given agents concurrently (bounded by semaphore)."""
        sem = asyncio.Semaphore(_DEFAULT_PATROL_CONCURRENCY)

        async def _bounded(aid: str) -> HealthProbe:
            async with sem:
                return await self.check(aid)

        return await asyncio.gather(*[_bounded(a) for a in agent_ids])

    async def check_sample(
        self,
        agent_ids: list[str],
        sample_size: int,
        priority_weight: bool = True,
    ) -> list[HealthProbe]:
        """Sample-based patrol (v1: forwards to check_all).

        Sampling by anomaly-score is deferred to a future version per
        design §2.4.2 [F-4]. The signature is retained for forward
        compatibility.
        """
        _ = priority_weight
        return await self.check_all(agent_ids[:max(1, sample_size)])

    async def check_adaptive(self, agent_ids: list[str]) -> list[HealthProbe]:
        """Adaptive patrol (v1: forwards to check_all).

        Load-aware adaptive sizing is deferred per design §2.4.2 [F-4].
        """
        return await self.check_all(agent_ids)


__all__ = [
    "HealthChecker",
]