"""F2-04-R02 / R03 — evaluator.

Split from plan_quality.py into plan_quality/evaluator.py.
Parent package __init__ re-exports via forwarding import; __all__ unchanged.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from maop.core.backends.db_utils import get_db_path, sqlite_connect
from maop.core.memory.plan_quality.features import (
    PlanFeatureExtractor,
    PlanQualityModel,
    compute_quality_label,
)
from maop.core.memory.plan_quality.pipeline import PlanQualityDataPipeline
from maop.core.memory.plan_quality.versioning import PlanVersionManager

logger = logging.getLogger(__name__)

__all__ = ["PlanQualityEvaluator"]


class PlanQualityEvaluator:
    """Score candidate Plans and rank them (F2-04-R02 / R03).

    Parameters
    ----------
    root_dir : str | Path
        MAOP project root.
    model : PlanQualityModel | None
        Pre-trained model.  If ``None``, loads the promoted version from disk,
        falling back to an untrained neutral model.
    online_learning : bool
        If True, every ``add_execution`` triggers an incremental model update.
    """

    def __init__(
        self,
        root_dir: str | Path,
        *,
        model: PlanQualityModel | None = None,
        online_learning: bool = False,
    ) -> None:
        self._root = Path(root_dir)
        self._extractor = PlanFeatureExtractor()
        self._pipeline: PlanQualityDataPipeline | None = None
        if model is not None:
            self._model = model
        else:
            vm = PlanVersionManager(self._root)
            active = vm.active_version()
            if active is not None:
                loaded = vm.load_model(active.version_id)
                self._model = loaded if loaded is not None else PlanQualityModel()
            else:
                self._model = PlanQualityModel()
        self._online_learning = online_learning

    def score(self, plan: Any) -> float:
        """Return predicted quality ∈ (0, 1) for *plan*."""
        rk = getattr(plan, "routing_key", "") or ""
        agent = getattr(plan, "selected_agent", "") or ""
        am, ts = self._agent_match_and_samples(rk, agent)
        feat = self._extractor.extract(plan, agent_match=am, total_agent_samples=ts)
        return self._model.predict(feat)

    def rank(self, plans: Sequence[Any]) -> list[tuple[Any, float]]:
        """Score and sort *plans* by descending predicted quality.

        Returns ``[(plan, score), ...]`` where index 0 is the best plan.
        """
        scored = [(p, self.score(p)) for p in plans]
        scored.sort(key=lambda x: -x[1])
        return scored

    def best(self, plans: Any) -> Any | None:
        """Return the highest-scoring plan from *plans*, or None."""
        if not plans:
            return None
        return self.rank(plans)[0][0]

    def add_execution(self, plan: Any, result: dict[str, Any]) -> str:
        """Record an execution feedback sample (F2-04-R04 online learning)."""
        pipe = self._pipeline or PlanQualityDataPipeline(Path(self._root))
        pid: str = pipe.add_execution(plan, result)

        if self._online_learning and self._model._trained:
            rk = getattr(plan, "routing_key", "") or ""
            agent = getattr(plan, "selected_agent", "") or ""
            am, ts = self._agent_match_and_samples(rk, agent)
            feat = self._extractor.extract(plan, agent_match=am, total_agent_samples=ts)
            label = compute_quality_label(
                result.get("outcome", ""),
                float(result.get("cost_usd", 0)),
                float(result.get("latency_ms", 0)),
                int(result.get("retries_used", 0)),
            )
            self._model.online_update(feat, label)
        return pid

    # -- helpers -------------------------------------------------------

    def _agent_match_and_samples(self, rk: str, agent: str) -> tuple[float, float]:
        if not rk:
            return 0.0, 0.0
        try:
            with sqlite_connect(get_db_path("plan_quality"), foreign_keys=False) as conn:
                rows = conn.execute(
                    "SELECT selected_agent, outcome FROM plan_quality_dataset WHERE routing_key = ?",
                    (rk,),
                ).fetchall()
        except Exception:
            return 0.0, 0.0
        if not rows:
            return 0.0, 0.0
        agent_stats: dict[str, dict[str, int]] = {}
        for r in rows:
            a = r[0] or "unknown"
            ag = agent_stats.setdefault(a, {"success": 0, "total": 0})
            ag["total"] += 1
            if (r[1] or "") == "success":
                ag["success"] += 1
        best_a, best_sr = "", -1.0
        for a, st in agent_stats.items():
            sr = st["success"] / max(1, st["total"])
            if sr > best_sr:
                best_sr = sr
                best_a = a
        ts = float(len(rows))
        return (1.0 if agent == best_a else 0.0), ts
