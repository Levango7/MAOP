"""Worker-selection mixin for the distributed scheduler.

Extracted from ``distributed_scheduler.py`` during the subpackage split.
Holds :meth:`_WorkerSelectionMixin._select_worker`, which scores capable
workers using the failure-pattern detector's weights and in-flight load.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from maop.core.scheduling.failure_detector import FailurePatternDetector
    from maop.core.scheduling.worker_pool import WorkerRegistry

logger: logging.Logger = logging.getLogger(__name__)


class _WorkerSelectionMixin:
    """Mixin providing :meth:`_select_worker` for :class:`DistributedScheduler`.

    Requires ``self._registry`` and ``self._failure_detector`` to be set
    by the concrete class's ``__init__``.
    """

    _registry: WorkerRegistry
    _failure_detector: FailurePatternDetector

    def _select_worker(
        self,
        required: str | set[str] | None = None,
        capable: list[str] | None = None,
    ) -> str | None:
        """Pick the best worker for the next dispatch.

        Combines the registry's capability filter with the
        :class:`FailurePatternDetector` weights and worker load:

        1. Ask the registry for capable workers (affinity match).
        2. Score each candidate as
           ``detector_weight / (1 + in_flight_count)`` — the
           health weight (1.0 normal, 0.0 drained, 0.3/0.6
           grey-recovery) discounted by how many tasks the worker
           is already running. Workers with weight 0 are dropped
           entirely: a drained worker is never picked, however
           idle it is.
        3. Return the highest-scoring worker id. Ties break by
           registry order (``capable_workers`` returns sorted
           ids) for determinism. When all capable workers are
           drained, fall back to the first capable worker rather
           than failing the dispatch — the detector will keep
           recording outcomes and re-drain as needed. This
           preserves the existing "always dispatch" behaviour
           when no failure data is available.

        ``capable`` may be supplied by callers that already
        computed the registry's capable list, so a dispatch
        does not scan it twice.

        Returns ``None`` only when the registry has no capable
        workers at all (same as the pre-F1-02 behaviour).

        The load term is best-effort: the registry's in-flight
        map is process-local (not persisted to Redis), so it
        sharing a pool each see their own slice.
        """
        if capable is None:
            capable = self._registry.capable_workers(required)
        if not capable:
            return None
        # Score: detector weight discounted by load. Workers never
        # seen by the detector default to weight 1.0 (full traffic).
        weighted = [
            (
                wid,
                self._failure_detector.get_weight(wid) / (1.0 + len(self._registry.in_flight(wid))),
            )
            for wid in capable
        ]
        # Drop fully-drained workers (weight == 0).
        live = [(wid, w) for wid, w in weighted if w > 0.0]
        if not live:
            # All capable workers are drained. Fall back to the first
            # capable worker so the dispatch still proceeds (better to
            # try and record another outcome than to silently stall).
            logger.warning(
                "[dist-sched] all %d capable workers drained — falling back to %s",
                len(capable),
                capable[0],
            )
            return capable[0]
        # Pick the highest-scoring worker; ties broken by registry
        # order (capable_workers returns sorted ids) for determinism.
        best_wid, best_w = live[0]
        for wid, w in live[1:]:
            if w > best_w:
                best_wid, best_w = wid, w
        return best_wid
