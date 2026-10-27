"""F2-04-R02 — feature extraction & quality model.

Split from plan_quality.py into plan_quality/features.py.
Parent module re-exports via forwarding import; __all__ unchanged.
"""

from __future__ import annotations

import math
import random
import uuid
from collections.abc import Sequence
from typing import Any

__all__ = [
    "PlanFeatureExtractor",
    "PlanQualityModel",
    "compute_quality_label",
]

# ── quality label (F2-04-R01) ───────────────────────────────────────

# First verbs that often start a task description — cheap heuristic
_TASK_VERBS = (
    "create", "write", "fix", "build", "deploy", "test", "refactor",
    "add", "update", "remove", "delete", "analyze", "review",
    "implement", "debug", "optimize", "run", "generate", "migrate",
    "convert", "setup", "configure", "explain", "list", "find",
    "search", "get", "show", "check", "validate", "compare",
    "extract", "download", "upload", "send", "receive",
)


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


# ── feature extraction (F2-04-R02) ──────────────────────────────────


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


# ── quality model (F2-04-R02) ──────────────────────────────────────


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


# ── math helpers (private) ──────────────────────────────────────────


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
