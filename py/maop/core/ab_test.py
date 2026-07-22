"""MAOP A/B Testing Framework — Structured experiments with statistical significance.

Supports:
  - Traffic splitting (percentage-based)
  - Metric collection per variant
  - Statistical significance check (simple Z-test for proportions)
  - Auto-selection of winning variant

Usage::

    from maop.core.ab_test import ABTestManager

    mgr = ABTestManager(root_dir="/path/to/MAOP")

    exp = mgr.create_experiment(
        name="prompt_v2",
        variants={"control": 50, "treatment": 50},
    )

    # Assign a user/request to a variant
    variant = mgr.assign("prompt_v2", "user-123")

    # Record a metric
    mgr.record("prompt_v2", variant, success=True)

    # Check significance
    result = mgr.evaluate("prompt_v2")
    if result.is_significant:
        print(f"Winner: {result.winner}")
"""

from __future__ import annotations

import json
import logging
import math
from pathlib import Path
from typing import cast

from pydantic import BaseModel, Field

from maop.core.db_utils import get_db_path, sqlite_connect

logger = logging.getLogger(__name__)


class ExperimentConfig(BaseModel):
    name: str
    variants: dict[str, int] = Field(default_factory=dict)
    min_samples: int = 30
    confidence_level: float = 0.95


class VariantStats(BaseModel):
    name: str
    samples: int = 0
    successes: int = 0
    success_rate: float = 0.0


class EvaluationResult(BaseModel):
    experiment: str
    is_significant: bool = False
    winner: str = ""
    variants: list[VariantStats] = Field(default_factory=list)
    p_value: float = 1.0


_AB_TEST_DDL = """
CREATE TABLE IF NOT EXISTS ab_experiments (
    name TEXT PRIMARY KEY,
    variants TEXT NOT NULL,
    min_samples INTEGER DEFAULT 30,
    confidence_level REAL DEFAULT 0.95,
    created_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS ab_assignments (
    experiment TEXT NOT NULL,
    entity_id TEXT NOT NULL,
    variant TEXT NOT NULL,
    assigned_at REAL NOT NULL,
    PRIMARY KEY (experiment, entity_id)
);

CREATE TABLE IF NOT EXISTS ab_metrics (
    experiment TEXT NOT NULL,
    variant TEXT NOT NULL,
    entity_id TEXT NOT NULL,
    success INTEGER NOT NULL,
    recorded_at REAL NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_ab_metrics_exp_var ON ab_metrics(experiment, variant);
"""


class ABTestManager:
    def __init__(self, root_dir: str | Path) -> None:
        self._root = Path(root_dir)
        self._data_dir = self._root / "data"
        self._data_dir.mkdir(parents=True, exist_ok=True)
        self._db_path = get_db_path("ab_test")
        self._init_db()

    def _init_db(self) -> None:
        with self._db_connect() as conn:
            conn.executescript(_AB_TEST_DDL)

    def _db_connect(self):
        return sqlite_connect(self._db_path, foreign_keys=False)

    def create_experiment(
        self,
        name: str,
        variants: dict[str, int],
        min_samples: int = 30,
        confidence_level: float = 0.95,
    ) -> ExperimentConfig:
        total = sum(variants.values())
        if total != 100:
            raise ValueError(f"Variant percentages must sum to 100, got {total}")
        config = ExperimentConfig(
            name=name, variants=variants,
            min_samples=min_samples, confidence_level=confidence_level,
        )
        import time as _time
        with self._db_connect() as conn:
            conn.execute(
                """INSERT OR REPLACE INTO ab_experiments (name, variants, min_samples, confidence_level, created_at)
                   VALUES (?, ?, ?, ?, ?)""",
                (name, json.dumps(variants), min_samples, confidence_level, _time.time()),
            )
        logger.info("A/B experiment created: %s", name)
        return config

    def assign(self, experiment: str, entity_id: str) -> str:
        with self._db_connect() as conn:
            row = conn.execute(
                "SELECT variant FROM ab_assignments WHERE experiment = ? AND entity_id = ?",
                (experiment, entity_id),
            ).fetchone()
            if row:
                return cast(str, row[0])

            exp_row = conn.execute(
                "SELECT variants FROM ab_experiments WHERE name = ?", (experiment,)
            ).fetchone()
            if not exp_row:
                raise ValueError(f"Experiment '{experiment}' not found")

            variants = json.loads(exp_row[0])
            hash_val = hash(f"{experiment}:{entity_id}") % 100
            cumulative = 0
            chosen = ""
            for vname, pct in variants.items():
                cumulative += pct
                if hash_val < cumulative:
                    chosen = vname
                    break
            if not chosen:
                chosen = list(variants.keys())[-1]

            import time as _time
            conn.execute(
                """INSERT INTO ab_assignments (experiment, entity_id, variant, assigned_at)
                   VALUES (?, ?, ?, ?)""",
                (experiment, entity_id, chosen, _time.time()),
            )
        return chosen

    def record(self, experiment: str, variant: str, entity_id: str, success: bool) -> None:
        import time as _time
        with self._db_connect() as conn:
            conn.execute(
                """INSERT INTO ab_metrics (experiment, variant, entity_id, success, recorded_at)
                   VALUES (?, ?, ?, ?, ?)""",
                (experiment, variant, entity_id, int(success), _time.time()),
            )

    def evaluate(self, experiment: str) -> EvaluationResult:
        with self._db_connect() as conn:
            rows = conn.execute(
                """SELECT variant, COUNT(*), SUM(success) FROM ab_metrics
                   WHERE experiment = ? GROUP BY variant""",
                (experiment,),
            ).fetchall()

        if len(rows) < 2:
            return EvaluationResult(experiment=experiment, variants=[
                VariantStats(name=r[0], samples=r[1], successes=r[2],
                             success_rate=r[2] / r[1] if r[1] else 0.0)
                for r in rows
            ])

        stats = []
        for r in rows:
            s = VariantStats(name=r[0], samples=r[1], successes=r[2],
                             success_rate=r[2] / r[1] if r[1] else 0.0)
            stats.append(s)

        best = max(stats, key=lambda s: s.success_rate)
        second = sorted(stats, key=lambda s: s.success_rate, reverse=True)[1]

        p_value = 1.0
        if best.samples >= 30 and second.samples >= 30:
            p_value = _z_test_p_value(second.success_rate, best.success_rate, second.samples, best.samples)

        alpha = 1.0 - 0.95
        is_sig = p_value < alpha and best.samples >= 30

        return EvaluationResult(
            experiment=experiment,
            is_significant=is_sig,
            winner=best.name if is_sig else "",
            variants=stats,
            p_value=round(p_value, 4),
        )


def _z_test_p_value(p1: float, p2: float, n1: int, n2: int) -> float:
    """Two-proportion Z-test p-value (one-tailed: p2 > p1)."""
    p_pool = (p1 * n1 + p2 * n2) / (n1 + n2)
    if p_pool == 0 or p_pool == 1:
        return 1.0
    se = math.sqrt(p_pool * (1 - p_pool) * (1 / n1 + 1 / n2))
    if se == 0:
        return 1.0
    z = (p2 - p1) / se
    return 0.5 * (1 + math.erf(-z / math.sqrt(2)))