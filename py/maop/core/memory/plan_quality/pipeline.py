# F2-04-R01 — data pipeline.
# Split from plan_quality.py into plan_quality/pipeline.py.

from __future__ import annotations

import json
import logging
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from maop.core.backends.db_utils import get_db_path, sqlite_connect
from maop.core.memory.plan_quality.features import (
    PlanFeatureExtractor,
    PlanQualityModel,
    compute_quality_label,
)
from maop.core.memory.plan_quality.versioning import PlanVersionManager

logger = logging.getLogger(__name__)

__all__ = ["PlanQualityDataPipeline"]

_DDL = [
    "CREATE TABLE IF NOT EXISTS plan_quality_dataset (",
    "    id TEXT PRIMARY KEY,",
    "    created_at REAL NOT NULL,",
    "    task TEXT DEFAULT '',",
    "    selected_agent TEXT DEFAULT '',",
    "    routing_key TEXT DEFAULT '',",
    "    gates_json TEXT DEFAULT '[]',",
    "    timeout_s REAL DEFAULT 120,",
    "    max_retries INTEGER DEFAULT 1,",
    "    has_content_safety INTEGER DEFAULT 0,",
    "    has_dry_run INTEGER DEFAULT 0,",
    "    f_gate_count REAL DEFAULT 0,",
    "    f_has_content_safety REAL DEFAULT 0,",
    "    f_has_dry_run REAL DEFAULT 0,",
    "    f_log2_timeout REAL DEFAULT 0,",
    "    f_retries REAL DEFAULT 0,",
    "    f_agent_match REAL DEFAULT 0,",
    "    f_total_agent_samples REAL DEFAULT 0,",
    "    f_budget_count REAL DEFAULT 1,",
    "    f_task_len_log2 REAL DEFAULT 0,",
    "    f_task_has_verb REAL DEFAULT 0,",
    "    outcome TEXT DEFAULT '',",
    "    cost_usd REAL DEFAULT 0,",
    "    latency_ms REAL DEFAULT 0,",
    "    retries_used INTEGER DEFAULT 0,",
    "    quality_label REAL DEFAULT 0.5",
    ");",
    "CREATE INDEX IF NOT EXISTS idx_pqd_agent ON plan_quality_dataset(selected_agent);",
    "CREATE INDEX IF NOT EXISTS idx_pqd_rk ON plan_quality_dataset(routing_key);",
    "CREATE INDEX IF NOT EXISTS idx_pqd_label ON plan_quality_dataset(quality_label);",
    "CREATE INDEX IF NOT EXISTS idx_pqd_created ON plan_quality_dataset(created_at DESC);",
]
_PLAN_QUALITY_DDL = "\n".join(_DDL)


@dataclass
class _SyntheticPlan:
    """Lightweight stand-in when reconstructing training rows from
    ``agent_performance`` (which has no original Plan fields)."""

    task: str
    selected_agent: str
    routing_key: str
    gates: list[str] = field(default_factory=list)
    budget: dict[str, Any] = field(default_factory=dict)


class PlanQualityDataPipeline:
    """Collect Plan + outcome → SQLite dataset.

    Training data source is :class:`AgentPerformanceTracker`'s
    ``agent_performance`` table.  Each row becomes one training sample:
    we synthesise structural features from ``agent`` / ``routing_key``
    and compute the quality label from ``outcome`` / ``cost_usd`` /
    ``latency_ms``.
    """

    def __init__(self, root_dir: str | Path) -> None:
        self._root = Path(root_dir)
        self._data_dir = self._root / "data"
        self._data_dir.mkdir(parents=True, exist_ok=True)
        self._db_path = get_db_path("plan_quality")
        self._extractor = PlanFeatureExtractor()
        self._perf_db = get_db_path("agent_performance")
        self._init_db()

    def _init_db(self) -> None:
        with self._db_connect() as conn:
            conn.executescript(_PLAN_QUALITY_DDL)

    def _db_connect(self):
        return sqlite_connect(self._db_path, foreign_keys=False)

    # -- Ingestion (F2-04-R01) ---------------------------------------

    def ingest_from_agent_performance(self) -> int:
        """Bulk-import every row from ``agent_performance``.

        We don't have the original ``Plan`` object, so we reconstruct a
        synthetic plan from ``routing_key`` / ``agent``.
        Returns the number of rows inserted.
        """
        try:
            with sqlite_connect(self._perf_db, foreign_keys=False) as pconn:
                rows = pconn.execute(
                    "SELECT id, agent, routing_key, outcome, cost_usd, latency_ms, created_at "
                    "FROM agent_performance"
                ).fetchall()
        except Exception as exc:
            logger.warning("[pq-pipeline] perf DB read failed: %s", exc)
            return 0

        if not rows:
            return 0

        best_agent_rk = self._best_agents_by_rk(rows)

        count = 0
        with self._db_connect() as conn:
            for row in rows:
                rid, agent, rk = row[0], row[1] or "", row[2] or ""
                outcome, cost, latency, created = row[3] or "", float(row[4] or 0), float(row[5] or 0), float(row[6] or 0)
                label = compute_quality_label(outcome, cost, latency)
                synth_plan = _SyntheticPlan(
                    task=f"{rk} task for {agent}",
                    selected_agent=agent,
                    routing_key=rk,
                    gates=self._infer_gates(rk),
                    budget={"timeout_s": 120, "max_retries": 1},
                )
                am = 1.0 if agent == best_agent_rk.get(rk, agent) else 0.0
                ts = self._total_agent_samples(rows, rk)
                feat = self._extractor.extract(synth_plan, agent_match=am, total_agent_samples=ts)
                conn.execute(
                    "INSERT OR IGNORE INTO plan_quality_dataset "
                    "(id, created_at, task, selected_agent, routing_key, gates_json, "
                    "timeout_s, max_retries, has_content_safety, has_dry_run, "
                    "f_gate_count, f_has_content_safety, f_has_dry_run, "
                    "f_log2_timeout, f_retries, f_agent_match, f_total_agent_samples, "
                    "f_budget_count, f_task_len_log2, f_task_has_verb, "
                    "outcome, cost_usd, latency_ms, quality_label) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        f"pq-{rid}" if rid else f"pq-{uuid.uuid4().hex[:12]}",
                        created, synth_plan.task, synth_plan.selected_agent,
                        synth_plan.routing_key, json.dumps(synth_plan.gates),
                        120, 1,
                        int("content-safety" in synth_plan.gates),
                        int("dry-run" in synth_plan.gates),
                        *feat, outcome, cost, latency, label,
                    ),
                )
                count += 1

        logger.info("[pq-pipeline] ingested %d rows", count)
        return count

    def add_execution(self, plan: Any, result: dict[str, Any]) -> str:
        """Add one observed execution to the dataset (F2-04-R04).

        Parameters
        ----------
        plan : Plan | dict
            The Plan that was dispatched.
        result : dict
            Execution feedback with keys ``outcome`` (str),
            ``cost_usd`` (float), ``latency_ms`` (float), ``retries_used`` (int).
        """
        agent = getattr(plan, "selected_agent", "") or ""
        rk = getattr(plan, "routing_key", "") or ""

        best_agent, ts = self._best_and_samples(rk)
        am = 1.0 if agent == best_agent else 0.0
        feat = self._extractor.extract(plan, agent_match=am, total_agent_samples=ts)

        outcome = result.get("outcome", "")
        cost = float(result.get("cost_usd", 0))
        latency = float(result.get("latency_ms", 0))
        retries_used = int(result.get("retries_used", 0))
        label = compute_quality_label(outcome, cost, latency, retries_used)
        gates = list(getattr(plan, "gates", []) or [])
        budget = getattr(plan, "budget", {}) or {}

        eid = f"pq-live-{uuid.uuid4().hex[:12]}"
        with self._db_connect() as conn:
            conn.execute(
                "INSERT INTO plan_quality_dataset "
                "(id, created_at, task, selected_agent, routing_key, gates_json, "
                "timeout_s, max_retries, has_content_safety, has_dry_run, "
                "f_gate_count, f_has_content_safety, f_has_dry_run, "
                "f_log2_timeout, f_retries, f_agent_match, f_total_agent_samples, "
                "f_budget_count, f_task_len_log2, f_task_has_verb, "
                "outcome, cost_usd, latency_ms, retries_used, quality_label) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    eid, time.time(),
                    str(getattr(plan, "task", "")), agent, rk, json.dumps(gates),
                    float(budget.get("timeout_s", 120)),
                    int(budget.get("max_retries", 1)),
                    int("content-safety" in gates),
                    int("dry-run" in gates),
                    *feat, outcome, cost, latency, retries_used, label,
                ),
            )
        return eid

    # -- Training (F2-04-R02) ----------------------------------------

    def load_training_data(
        self, since: float = 0.0, limit: int = 0,
    ) -> tuple[list[list[float]], list[float]]:
        """Return (X, y) from the dataset.

        Parameters
        ----------
        since : float
            Only include samples created_at >= this epoch.
        limit : int
            Cap to the most recent *limit* rows (0 = no limit).
        """
        with self._db_connect() as conn:
            if since > 0 and limit > 0:
                rows = conn.execute(
                    "SELECT f_gate_count, f_has_content_safety, f_has_dry_run, "
                    "f_log2_timeout, f_retries, f_agent_match, f_total_agent_samples, "
                    "f_budget_count, f_task_len_log2, f_task_has_verb, "
                    "quality_label "
                    "FROM plan_quality_dataset WHERE quality_label > 0 "
                    "AND created_at >= ? "
                    "ORDER BY created_at DESC LIMIT ?",
                    (since, int(limit)),
                ).fetchall()
            elif since > 0:
                rows = conn.execute(
                    "SELECT f_gate_count, f_has_content_safety, f_has_dry_run, "
                    "f_log2_timeout, f_retries, f_agent_match, f_total_agent_samples, "
                    "f_budget_count, f_task_len_log2, f_task_has_verb, "
                    "quality_label "
                    "FROM plan_quality_dataset WHERE quality_label > 0 "
                    "AND created_at >= ? "
                    "ORDER BY created_at DESC",
                    (since,),
                ).fetchall()
            elif limit > 0:
                rows = conn.execute(
                    "SELECT f_gate_count, f_has_content_safety, f_has_dry_run, "
                    "f_log2_timeout, f_retries, f_agent_match, f_total_agent_samples, "
                    "f_budget_count, f_task_len_log2, f_task_has_verb, "
                    "quality_label "
                    "FROM plan_quality_dataset WHERE quality_label > 0 "
                    "ORDER BY created_at DESC LIMIT ?",
                    (int(limit),),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT f_gate_count, f_has_content_safety, f_has_dry_run, "
                    "f_log2_timeout, f_retries, f_agent_match, f_total_agent_samples, "
                    "f_budget_count, f_task_len_log2, f_task_has_verb, "
                    "quality_label "
                    "FROM plan_quality_dataset WHERE quality_label > 0 "
                    "ORDER BY created_at DESC"
                ).fetchall()
        X: list[list[float]] = []
        y: list[float] = []
        for r in rows:
            X.append([float(r[i]) for i in range(10)])
            y.append(float(r[10]))
        return X, y

    def train(self, *, save_version: bool = True) -> PlanQualityModel:
        """Train a :class:`PlanQualityModel` and optionally snapshot it.

        Returns the trained model.  When ``save_version=True``, a version entry
        is appended by :class:`PlanVersionManager` and (for the first call)
        promoted.
        """
        X, y = self.load_training_data()
        if not X:
            logger.warning("[pq-pipeline] no training data available")
            return PlanQualityModel()
        model = PlanQualityModel()
        model.fit(X, y)
        if save_version:
            vm = PlanVersionManager(self._root)
            vm.append(model, n_samples=len(X))
        return model

    # -- Internal helpers ---------------------------------------------

    @staticmethod
    def _best_agents_by_rk(rows: list[Any]) -> dict[str, str]:
        agent_stats: dict[str, dict[str, dict[str, int]]] = {}
        for r in rows:
            rk = r[2] or ""
            if not rk:
                continue
            a = r[1] or "unknown"
            inner = agent_stats.setdefault(rk, {}).setdefault(a, {"success": 0, "total": 0})
            inner["total"] += 1
            if (r[3] or "") == "success":
                inner["success"] += 1
        result: dict[str, str] = {}
        for rk, adict in agent_stats.items():
            best_a, best_sr = "", -1.0
            for a, st in adict.items():
                sr = st["success"] / max(1, st["total"])
                if sr > best_sr:
                    best_sr = sr
                    best_a = a
            result[rk] = best_a
        return result

    def _infer_gates(self, routing_key: str) -> list[str]:
        """Reconstruct likely gate set from a routing key (heuristic)."""
        gates = ["exit_code", "output"]
        if routing_key in ("security", "quickfix", "review"):
            gates.append("content-safety")
        if routing_key in ("deploy", "pipeline", "fileops"):
            gates.append("dry-run")
        return gates

    @staticmethod
    def _total_agent_samples(rows: list[Any], rk: str) -> float:
        return float(sum(1 for r in rows if (r[2] or "") == rk))

    def _best_and_samples(self, rk: str) -> tuple[str, float]:
        """Return (best_agent_for_rk, total_samples_for_rk)."""
        if not rk:
            return "", 0.0
        try:
            with self._db_connect() as conn:
                rows = conn.execute(
                    "SELECT selected_agent, outcome FROM plan_quality_dataset WHERE routing_key = ?",
                    (rk,),
                ).fetchall()
        except Exception:
            return "", 0.0
        if not rows:
            return "", 0.0
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
        return best_a, float(len(rows))
