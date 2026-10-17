"""MAOP Plan Quality Learning — F2-04 (M2.3 MVP).

Personal-Edition lightweight plan-quality scorer that learns from historical
execution outcomes to rank/reroute candidate plans before dispatch.

Scope (MVP for M2.3; full GA deferred to M2.4)
----------------------------------------------
* **F2-04-R01 Data pipeline** — Collect Plan + execution result + quality
  label into a SQLite table (``plan_quality_dataset``).  Quality label is
  a deterministic function of (outcome, cost_usd, latency_ms, retries):
  success + low cost + low latency + no retries → high label.
* **F2-04-R02 Quality model** — A pure-Python logistic-style regressor
  trained by mini-batch SGD on plan features:

      - structural: gate_count, has_content_safety, has_dry_run,
        log2(timeout_s + 1), retries, budget_count
      - match: agent_match (1 iff agent equals the historical best agent
        for this routing_key), log2(best_agent_samples + 1)
      - task: task_lengthchars (log-scaled), task_has_verb
  Label ∈ (0, 1).  Model is intentionally tiny (~10 weights), converges
  in < 30 epochs on 500+ samples.
* **F2-04-R03 Generation hook** — ``PlanQualityEvaluator.rank(plans)``
  scores each candidate Plan and returns them sorted by descending
  predicted quality.
* **F2-04-R04 Online learning** — ``add_execution(plan, result)`` appends
  a new sample and triggers an incremental mini-batch update
  (if ``online_learning=True``), so the model improves without a full
  retrain.
* **F2-04-R05 / R06 A/B + versioning** — ``PlanVersion`` snapshots every
  trained model to ``data/plan_quality_versions.json`` with atomic
  promote/rollback.  Consumers can read ``active_version`` to decide
  whether to use the latest or the promoted (A/B control) version.

Enterprise boundary (ADR-016 D5)
---------------------------------
This module is the **Personal-Edition** reference: no torch / no scikit /
no GPU.  The enterprise repo injects a SageMaker-backed replacement with
the same ``PlanQualityEvaluator`` surface.

Usage::

    from maop.core.memory.plan_quality import (
        PlanQualityEvaluator, PlanQualityDataPipeline,
    )

    pipe = PlanQualityDataPipeline(root_dir)
    pipe.ingest_from_agent_performance()        # populate dataset
    model = pipe.train()                        # fit + version snapshot
    evaltr = PlanQualityEvaluator(root_dir, model=model)
    ranked = evaltr.rank(candidate_plans)       # best plan first
"""

from __future__ import annotations

import json
import logging
import math
import random
import time
import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from maop.core.backends.db_utils import get_db_path, sqlite_connect

logger = logging.getLogger(__name__)

__all__ = [
    "PlanFeatureExtractor",
    "PlanQualityDataPipeline",
    "PlanQualityEvaluator",
    "PlanQualityModel",
    "PlanVersion",
    "PlanVersionManager",
    "compute_quality_label",
]

# ── DDL ──────────────────────────────────────────────────────────────

_PLAN_QUALITY_DDL = """
CREATE TABLE IF NOT EXISTS plan_quality_dataset (
    id TEXT PRIMARY KEY,
    created_at REAL NOT NULL,
    -- Plan snapshot (F2-04-R01)
    task TEXT DEFAULT '',
    selected_agent TEXT DEFAULT '',
    routing_key TEXT DEFAULT '',
    gates_json TEXT DEFAULT '[]',
    timeout_s REAL DEFAULT 120,
    max_retries INTEGER DEFAULT 1,
    has_content_safety INTEGER DEFAULT 0,
    has_dry_run INTEGER DEFAULT 0,
    -- Features (denormalised — read path does not touch Python)
    f_gate_count REAL DEFAULT 0,
    f_has_content_safety REAL DEFAULT 0,
    f_has_dry_run REAL DEFAULT 0,
    f_log2_timeout REAL DEFAULT 0,
    f_retries REAL DEFAULT 0,
    f_agent_match REAL DEFAULT 0,
    f_total_agent_samples REAL DEFAULT 0,
    f_budget_count REAL DEFAULT 1,
    f_task_len_log2 REAL DEFAULT 0,
    f_task_has_verb REAL DEFAULT 0,
    -- Outcome / quality label
    outcome TEXT DEFAULT '',
    cost_usd REAL DEFAULT 0,
    latency_ms REAL DEFAULT 0,
    retries_used INTEGER DEFAULT 0,
    quality_label REAL DEFAULT 0.5
);

CREATE INDEX IF NOT EXISTS idx_pqd_agent ON plan_quality_dataset(selected_agent);
CREATE INDEX IF NOT EXISTS idx_pqd_rk ON plan_quality_dataset(routing_key);
CREATE INDEX IF NOT EXISTS idx_pqd_label ON plan_quality_dataset(quality_label);
CREATE INDEX IF NOT EXISTS idx_pqd_created ON plan_quality_dataset(created_at DESC);
"""

# First eight verbs that often start a task description — cheap heuristic
_TASK_VERBS = (
    "create", "write", "fix", "build", "deploy", "test", "refactor",
    "add", "update", "remove", "delete", "analyze", "review",
    "implement", "debug", "optimize", "run", "generate", "migrate",
    "convert", "setup", "configure", "explain", "list", "find",
    "search", "get", "show", "check", "validate", "compare",
    "extract", "download", "upload", "send", "receive",
)


# ════════════════════════════════════════════════════════════════════
# F2-04-R01 — quality label
# ════════════════════════════════════════════════════════════════════


def compute_quality_label(
    outcome: str,
    cost_usd: float = 0.0,
    latency_ms: float = 0.0,
    retries_used: int = 0,
) -> float:
    """Deterministic quality label ∈ (0, 1) for a single execution.

    Weighted formula — "good" = success + cheap + fast + no retries.

      * base from outcome: success=1.0, partial=0.6, failure=0.1
      * discount on success: geometric mean of cost/latency/retries discount,
        clipped to [0.55, 1.0]

    Parameters
    ----------
    outcome : str
        ``"success"``, ``"partial"``, ``"failure"``.
    cost_usd : float
        Execution cost in USD.
    latency_ms : float
        Wall-clock latency in milliseconds.
    retries_used : int
        How many retries were required.

    Returns
    -------
    float
        Scalar in (0, 1).  Higher = better.
    """
    outcome_mult = {"success": 1.0, "partial": 0.6, "failure": 0.1}.get(
        outcome.lower().strip(), 0.5,
    )
    if outcome_mult < 1.0:
        return round(outcome_mult, 4)

    # Cost: $0.005 → ~0.8, $0.01 → ~0.67, $0.02 → 0.5 (clipped min 0.55)
    cost_disc = max(0.55, 1.0 / (1.0 + cost_usd * 50))
    # Latency: < 2 000 ms → > 0.8; 8 000 ms → ~0.5 (clipped)
    lat_disc = max(0.55, 1.0 / (1.0 + max(0.0, latency_ms) / 6000.0))
    # Retry: no retry → 1.0; each retry × 0.82
    retry_disc = 0.82 ** max(0, retries_used)

    multiplier = (cost_disc * lat_disc * retry_disc) ** (1.0 / 3)
    return float(round(max(0.55, min(1.0, outcome_mult * multiplier)), 4))


# ════════════════════════════════════════════════════════════════════
# F2-04-R02 — feature extraction & model
# ════════════════════════════════════════════════════════════════════


class PlanFeatureExtractor:
    """Extract a fixed-length float vector from a Plan-like object.

    10-dim layout:
        [0]  gate_count          (#gates / 4, capped at 1)
        [1]  has_content_safety  (0/1)
        [2]  has_dry_run         (0/1)
        [3]  log2_timeout        (log2(timeout_s+1) ÷ 8)
        [4]  retries             (max_retries raw)
        [5]  agent_match         (1.0 iff agent == best historical agent for this rk)
        [6]  log2_samples        (log2(best_agent_historical_samples+1) ÷ 6)
        [7]  budget_count        (1.0 — structural richness placeholder)
        [8]  log2_task_len       (log2(task_length+1) / 10)
        [9]  task_has_verb       (1.0 if task starts with a known verb)
    """

    FEATURE_DIM: int = 10

    def extract(
        self,
        plan: Any,
        *,
        agent_match: float = 0.0,
        total_agent_samples: float = 0.0,
    ) -> list[float]:
        """Return a 10-dim feature vector for *plan*."""
        gates = list(getattr(plan, "gates", []) or [])
        budget = getattr(plan, "budget", {}) or {}
        task = str(getattr(plan, "task", "") or "")

        timeout_s = float(budget.get("timeout_s", 120) or 120)
        retries = int(budget.get("max_retries", 1) or 1)

        gate_count = min(len(gates) / 4.0, 1.0)
        has_cs = 1.0 if "content-safety" in gates else 0.0
        has_dry = 1.0 if "dry-run" in gates else 0.0
        log2_timeout = math.log2(timeout_s + 1) / 8.0    # ~128s → 1.0
        log2_task = math.log2(len(task) + 1) / 10.0
        task_has_verb = 1.0 if self._starts_with_verb(task) else 0.0
        samples_feat = math.log2(total_agent_samples + 1) / 6.0

        return [
            gate_count,
            has_cs,
            has_dry,
            log2_timeout,
            float(retries),
            float(agent_match),
            samples_feat,
            1.0,  # structural placeholder
            log2_task,
            task_has_verb,
        ]

    @staticmethod
    def _starts_with_verb(task: str) -> bool:
        stripped = task.lstrip().lower()
        return any(stripped.startswith(v) for v in _TASK_VERBS)


class PlanQualityModel:
    """Logistic-style quality predictor trained by mini-batch SGD.

    Pure-Python, no torch / scikit.  Input_dim = 10 (matching
    :class:`PlanFeatureExtractor`).  Forward: h = sigmoid(w·x + b).

    Parameters
    ----------
    feature_dim : int
        Length of the input feature vector (default 10).
    learning_rate : float
        SGD step size.
    epochs : int
        Max training epochs over the in-memory sample batch.
    batch_size : int
        Mini-batch size for online updates.
    seed : int
        RNG seed for reproducible training.
    """

    def __init__(
        self,
        feature_dim: int = 10,
        learning_rate: float = 0.05,
        epochs: int = 30,
        batch_size: int = 64,
        seed: int = 42,
    ) -> None:
        self._dim = feature_dim
        self._lr = learning_rate
        self._epochs = epochs
        self._batch_size = batch_size
        self._rng = random.Random(seed)
        scale = (2.0 / max(1, feature_dim)) ** 0.5
        self._w: list[float] = [self._rng.uniform(-scale, scale) for _ in range(feature_dim)]
        self._b: float = 0.0
        self._trained: bool = False
        self._train_loss: float = float("inf")
        self._version: str = ""
        self._n_samples_trained: int = 0

    # -- public --------------------------------------------------------

    def predict(self, x: Sequence[float]) -> float:
        """Return σ(w·x + b) ∈ (0, 1).  Untrained → neutral 0.5."""
        if not self._trained:
            return 0.5
        z = self._b + sum(w * xi for w, xi in zip(self._w, x))
        return _sigmoid(z)

    def fit(
        self,
        X: Sequence[Sequence[float]],
        y: Sequence[float],
    ) -> float:
        """Mini-batch SGD over (X, y).  Returns final epoch mean loss."""
        X_list = [list(xi) for xi in X]
        y_list = list(y)
        n = len(X_list)
        if n == 0:
            return float("inf")

        self._n_samples_trained = n
        indices = list(range(n))

        for epoch in range(self._epochs):
            self._rng.shuffle(indices)
            epoch_loss = 0.0
            for start in range(0, n, self._batch_size):
                batch_idx = indices[start : start + self._batch_size]
                grad_w = [0.0] * self._dim
                grad_b = 0.0
                for i in batch_idx:
                    xi = X_list[i]
                    yi = y_list[i]
                    y_hat = _sigmoid(self._b + sum(w * xij for w, xij in zip(self._w, xi)))
                    err = y_hat - yi
                    epoch_loss += _bce(y_hat, yi)
                    for d in range(self._dim):
                        grad_w[d] += err * xi[d]
                    grad_b += err
                inv = self._lr / len(batch_idx)
                for d in range(self._dim):
                    self._w[d] -= inv * grad_w[d]
                self._b -= inv * grad_b
            epoch_loss /= n
            if epoch_loss < 1e-3:
                break

        self._trained = True
        self._train_loss = epoch_loss
        return epoch_loss

    def online_update(self, x: Sequence[float], y: float, n_steps: int = 5) -> None:
        """Incremental SGD step for online learning (F2-04-R04)."""
        if not self._trained:
            return
        xi = list(x)
        for _ in range(n_steps):
            y_hat = _sigmoid(self._b + sum(w * xij for w, xij in zip(self._w, xi)))
            err = y_hat - y
            for d in range(self._dim):
                self._w[d] -= self._lr * err * xi[d]
            self._b -= self._lr * err

    # -- serialisation -------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        return {
            "w": list(self._w),
            "b": self._b,
            "trained": self._trained,
            "train_loss": self._train_loss,
            "n_samples": self._n_samples_trained,
            "version": self._version,
            "feature_dim": self._dim,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> PlanQualityModel:
        m = cls(feature_dim=int(d.get("feature_dim", 10)))
        m._w = list(d.get("w", m._w))
        m._b = float(d.get("b", 0.0))
        m._trained = bool(d.get("trained", False))
        m._train_loss = float(d.get("train_loss", float("inf")))
        m._n_samples_trained = int(d.get("n_samples", 0))
        m._version = str(d.get("version", ""))
        return m

    def snapshot_version(self) -> str:
        """Record a version id and return it (F2-04-R06)."""
        self._version = f"pq-{uuid.uuid4().hex[:8]}"
        return self._version


# ════════════════════════════════════════════════════════════════════
# F2-04-R06 — model version management
# ════════════════════════════════════════════════════════════════════


@dataclass
class PlanVersion:
    """One immutable entry in the version chain (F2-04-R06)."""

    version_id: str
    created_at: float
    n_samples: int
    train_loss: float
    promoted: bool = False
    """If True, this version is the canonical one served by default."""

    def to_dict(self) -> dict[str, Any]:
        return {
            "version_id": self.version_id,
            "created_at": self.created_at,
            "n_samples": self.n_samples,
            "train_loss": self.train_loss,
            "promoted": self.promoted,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> PlanVersion:
        return cls(**d)


class PlanVersionManager:
    """Persisted version chain stored as JSON (``data/plan_quality_versions.json``).

    Each training run appends a new version.  ``promote(version_id)`` flips
    the active pointer; ``rollback()`` is simply "promote the previous version".
    """

    def __init__(self, root_dir: str | Path) -> None:
        self._root = Path(root_dir)
        self._path = self._root / "data" / "plan_quality_versions.json"
        self._path.parent.mkdir(parents=True, exist_ok=True)

    # -- persistence helpers -------------------------------------------

    def _load_chain(self) -> list[dict[str, Any]]:
        if not self._path.exists():
            return []
        try:
            with open(self._path, encoding="utf-8") as f:
                data = json.load(f)
            return data if isinstance(data, list) else []
        except (json.JSONDecodeError, OSError):
            return []

    def _save_chain(self, chain: list[dict[str, Any]]) -> None:
        tmp = self._path.with_suffix(".tmp")
        tmp.write_text(json.dumps(chain, indent=2, ensure_ascii=False), encoding="utf-8")
        tmp.replace(self._path)

    # -- public --------------------------------------------------------

    def append(self, model: PlanQualityModel, n_samples: int) -> PlanVersion:
        """Snapshot *model* as a new version and persist it."""
        vid = model.snapshot_version()
        ver = PlanVersion(
            version_id=vid,
            created_at=time.time(),
            n_samples=n_samples,
            train_loss=model._train_loss,
        )
        chain = self._load_chain()
        chain.append(ver.to_dict())

        # Persist model weights alongside the chain entry.
        models_dir = self._path.parent / "plan_quality_models"
        models_dir.mkdir(parents=True, exist_ok=True)
        m_path = models_dir / f"{vid}.json"
        m_path.write_text(json.dumps(model.to_dict(), ensure_ascii=False), encoding="utf-8")

        # Auto-promote the very first version.
        if len(chain) == 1:
            chain[0]["promoted"] = True
        self._save_chain(chain)
        return ver

    def promote(self, version_id: str) -> bool:
        """Promote *version_id* to active (F2-04-R05 / R06)."""
        chain = self._load_chain()
        found = False
        for entry in chain:
            is_match = entry.get("version_id") == version_id
            entry["promoted"] = is_match
            if is_match:
                found = True
        if found:
            self._save_chain(chain)
            logger.info("[pq] promoted version %s", version_id)
        return found

    def rollback(self) -> PlanVersion | None:
        """Re-promote the version immediately before the currently promoted one."""
        chain = self._load_chain()
        idx = next((i for i, e in enumerate(chain) if e.get("promoted")), -1)
        if idx <= 0:
            return None
        prev = chain[idx - 1]
        self.promote(prev["version_id"])
        return PlanVersion.from_dict(prev)

    def active_version(self) -> PlanVersion | None:
        """Return the currently promoted version, or None."""
        for e in self._load_chain():
            if e.get("promoted"):
                return PlanVersion.from_dict(e)
        return None

    def load_model(self, version_id: str) -> PlanQualityModel | None:
        """Load persisted weights for *version_id*."""
        m_path = self._path.parent / "plan_quality_models" / f"{version_id}.json"
        if not m_path.exists():
            return None
        try:
            data = json.loads(m_path.read_text(encoding="utf-8"))
            return PlanQualityModel.from_dict(data)
        except (json.JSONDecodeError, OSError):
            return None

    def list_versions(self) -> list[PlanVersion]:
        return [PlanVersion.from_dict(e) for e in self._load_chain()]


# ════════════════════════════════════════════════════════════════════
# F2-04-R01 — data pipeline
# ════════════════════════════════════════════════════════════════════


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
                    """INSERT OR IGNORE INTO plan_quality_dataset
                       (id, created_at, task, selected_agent, routing_key, gates_json,
                        timeout_s, max_retries, has_content_safety, has_dry_run,
                        f_gate_count, f_has_content_safety, f_has_dry_run,
                        f_log2_timeout, f_retries, f_agent_match, f_total_agent_samples,
                        f_budget_count, f_task_len_log2, f_task_has_verb,
                        outcome, cost_usd, latency_ms, quality_label)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
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
                """INSERT INTO plan_quality_dataset
                   (id, created_at, task, selected_agent, routing_key, gates_json,
                    timeout_s, max_retries, has_content_safety, has_dry_run,
                    f_gate_count, f_has_content_safety, f_has_dry_run,
                    f_log2_timeout, f_retries, f_agent_match, f_total_agent_samples,
                    f_budget_count, f_task_len_log2, f_task_has_verb,
                    outcome, cost_usd, latency_ms, retries_used, quality_label)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
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
            sql = (
                "SELECT f_gate_count, f_has_content_safety, f_has_dry_run, "
                "       f_log2_timeout, f_retries, f_agent_match, f_total_agent_samples, "
                "       f_budget_count, f_task_len_log2, f_task_has_verb, "
                "       quality_label "
                "FROM plan_quality_dataset WHERE quality_label > 0"
            )
            params: list[Any] = []
            if since > 0:
                sql += " AND created_at >= ?"
                params.append(since)
            sql += " ORDER BY created_at DESC"
            if limit > 0:
                sql += f" LIMIT {int(limit)}"
            rows = conn.execute(sql, params).fetchall()
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


# ════════════════════════════════════════════════════════════════════
# F2-04-R02 / R03 — evaluator
# ════════════════════════════════════════════════════════════════════


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
        pid = pipe.add_execution(plan, result)

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


# ════════════════════════════════════════════════════════════════════
# Math helpers (private)
# ════════════════════════════════════════════════════════════════════


def _sigmoid(z: float) -> float:
    """Numerically stable sigmoid."""
    if z >= 0:
        return 1.0 / (1.0 + math.exp(-z))
    ez = math.exp(z)
    return ez / (1.0 + ez)


def _bce(y_hat: float, y: float) -> float:
    """Binary cross-entropy."""
    eps = 1e-7
    y_hat = max(eps, min(1 - eps, y_hat))
    if y == 1:
        return -math.log(y_hat)
    return -math.log(1 - y_hat)
