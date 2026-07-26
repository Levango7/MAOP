"""MAOP A/B Testing Framework for Prompt Optimization.

Runs controlled experiments comparing prompt versions to determine
which performs better based on success rate, latency, and cost.

Features:
  - Create experiments with control/treatment prompt versions
  - Traffic splitting (e.g., 80% control / 20% treatment)
  - Statistical significance testing (Fisher's exact test)
  - Auto-promote treatment if significantly better

Usage:
    framework = ABTestFramework(root_dir="/path/to/MAOP")
    exp = framework.create_experiment(
        name="system_prompt_v2",
        template_name="system_prompt",
        control_version="v1",
        treatment_version="v2",
        traffic_split=0.2,  # 20% to treatment
    )
    # ... run tasks ...
    result = framework.evaluate_experiment(exp.id)
    if result.is_significant and result.winner == "treatment":
        framework.promote_treatment(exp.id)
"""

from __future__ import annotations

import logging
import math
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)


@dataclass
class ExperimentResult:
    """Result of an A/B test experiment."""
    experiment_id: str
    control_samples: int
    treatment_samples: int
    control_success_rate: float
    treatment_success_rate: float
    p_value: float
    is_significant: bool  # p_value < 0.05
    winner: str  # "control", "treatment", or "inconclusive"
    confidence: str  # "high", "medium", "low"


@dataclass
class Experiment:
    """A/B test experiment configuration."""
    id: str
    name: str
    template_name: str
    control_version: str
    treatment_version: str
    traffic_split: float  # 0.0-1.0, fraction to treatment
    status: str  # "running", "completed", "stopped"
    created_at: float
    started_at: float = 0
    ended_at: float = 0
    control_results: list[dict] = field(default_factory=list)
    treatment_results: list[dict] = field(default_factory=list)


class ABTestFramework:
    """Framework for running A/B tests on prompt versions."""

    def __init__(self, root_dir: str | Path | None = None) -> None:
        from maop.core.db_utils import find_project_root
        self._root = Path(root_dir or find_project_root())
        self._experiments_file = self._root / "data" / "ab_experiments.json"
        self._prompt_manager = None

    def _get_prompt_manager(self):
        if self._prompt_manager is None:
            try:
                from maop.core.services import ServiceContainer
                svc = ServiceContainer(root_dir=self._root)
                self._prompt_manager = svc.get("prompt_manager", raise_on_failure=False)
            except Exception as exc:
                logger.warning("[ab_test] PromptManager init failed: %s", exc)
                self._prompt_manager = None
        return self._prompt_manager

    def create_experiment(
        self,
        name: str,
        template_name: str,
        control_version: str,
        treatment_version: str,
        traffic_split: float = 0.2,
    ) -> Experiment:
        """Create a new A/B test experiment."""
        if not 0 < traffic_split < 1:
            raise ValueError("traffic_split must be between 0 and 1 (exclusive)")

        exp = Experiment(
            id=uuid.uuid4().hex[:12],
            name=name,
            template_name=template_name,
            control_version=control_version,
            treatment_version=treatment_version,
            traffic_split=traffic_split,
            status="running",
            created_at=time.time(),
            started_at=time.time(),
        )

        self._save_experiment(exp)
        logger.info("[ab_test] Created experiment %s: %s (split=%.0f%%)",
                    exp.id, exp.name, traffic_split * 100)
        return exp

    def assign_variant(self, experiment_id: str, user_id: str = "") -> str:
        """Determine which variant (control/treatment) a request gets.

        Uses hash-based assignment for consistent routing.
        """
        exp = self._load_experiment(experiment_id)
        if not exp or exp.status != "running":
            return "control"

        # Hash-based assignment for consistency. Uses Python's built-in hash
        # which is randomized per process — that is fine for traffic split
        # purposes (we only need statistical distribution, not cross-process
        # consistency for the same user).
        hash_input = f"{experiment_id}:{user_id}" if user_id else f"{experiment_id}:{time.time()}"
        hash_val = hash(hash_input) % 1000 / 1000.0

        return "treatment" if hash_val < exp.traffic_split else "control"

    def record_result(
        self,
        experiment_id: str,
        variant: str,
        success: bool,
        latency_ms: float = 0,
        cost: float = 0,
    ) -> None:
        """Record a result for an experiment variant."""
        exp = self._load_experiment(experiment_id)
        if not exp:
            return

        result = {
            "success": success,
            "latency_ms": latency_ms,
            "cost": cost,
            "timestamp": time.time(),
        }

        if variant == "treatment":
            exp.treatment_results.append(result)
        else:
            exp.control_results.append(result)

        self._save_experiment(exp)

    def evaluate_experiment(self, experiment_id: str) -> ExperimentResult:
        """Evaluate experiment results with statistical significance testing."""
        exp = self._load_experiment(experiment_id)
        if not exp:
            return ExperimentResult(
                experiment_id=experiment_id,
                control_samples=0, treatment_samples=0,
                control_success_rate=0, treatment_success_rate=0,
                p_value=1.0, is_significant=False,
                winner="inconclusive", confidence="low",
            )

        control_n = len(exp.control_results)
        treatment_n = len(exp.treatment_results)
        control_success = sum(1 for r in exp.control_results if r["success"])
        treatment_success = sum(1 for r in exp.treatment_results if r["success"])

        control_rate = control_success / control_n if control_n > 0 else 0
        treatment_rate = treatment_success / treatment_n if treatment_n > 0 else 0

        # Fisher's exact test (simplified for 2x2 contingency table)
        p_value = self._fisher_exact(
            control_success, control_n - control_success,
            treatment_success, treatment_n - treatment_success,
        )

        is_significant = p_value < 0.05 and min(control_n, treatment_n) >= 30

        if is_significant:
            winner = "treatment" if treatment_rate > control_rate else "control"
            confidence = "high" if p_value < 0.01 else "medium"
        else:
            winner = "inconclusive"
            confidence = "low" if min(control_n, treatment_n) < 30 else "medium"

        return ExperimentResult(
            experiment_id=experiment_id,
            control_samples=control_n,
            treatment_samples=treatment_n,
            control_success_rate=control_rate,
            treatment_success_rate=treatment_rate,
            p_value=p_value,
            is_significant=is_significant,
            winner=winner,
            confidence=confidence,
        )

    def promote_treatment(self, experiment_id: str) -> bool:
        """Promote treatment version as the current version in PromptManager.

        PromptManager does not expose a ``set_current_version`` method, so we
        update the ``prompt_templates.current_version`` column directly via
        SQL on the same DB path that PromptManager uses (``get_db_path``).
        """
        exp = self._load_experiment(experiment_id)
        if not exp:
            return False

        try:
            from maop.core.db_utils import get_db_path
            db_path = get_db_path("prompt_manager")
            if not db_path.exists():
                logger.warning("[ab_test] Prompt DB not found at %s", db_path)
                return False

            import sqlite3
            with sqlite3.connect(str(db_path)) as conn:
                cur = conn.execute(
                    "UPDATE prompt_templates SET current_version=? WHERE id=?",
                    (exp.treatment_version, exp.template_name),
                )
                if cur.rowcount == 0:
                    # Template id may actually be the *name* — try that too.
                    cur = conn.execute(
                        "UPDATE prompt_templates SET current_version=? WHERE name=?",
                        (exp.treatment_version, exp.template_name),
                    )
                conn.commit()
                updated = cur.rowcount
            if updated == 0:
                logger.warning(
                    "[ab_test] Template '%s' not found; cannot promote", exp.template_name
                )
                return False

            exp.status = "completed"
            exp.ended_at = time.time()
            self._save_experiment(exp)
            logger.info("[ab_test] Promoted treatment %s for template %s",
                        exp.treatment_version, exp.template_name)
            return True
        except Exception as exc:
            logger.error("[ab_test] Promotion failed: %s", exc)
            return False

    def stop_experiment(self, experiment_id: str) -> bool:
        """Stop a running experiment."""
        exp = self._load_experiment(experiment_id)
        if not exp:
            return False
        exp.status = "stopped"
        exp.ended_at = time.time()
        self._save_experiment(exp)
        return True

    def list_experiments(self) -> list[Experiment]:
        """List all experiments."""
        return self._load_all_experiments()

    def get_experiment(self, experiment_id: str) -> Experiment | None:
        """Get an experiment by ID."""
        return self._load_experiment(experiment_id)

    def _fisher_exact(self, a: int, b: int, c: int, d: int) -> float:
        """Simplified Fisher's exact test for 2x2 table.

        a,b = success,failure for control
        c,d = success,failure for treatment

        Returns two-tailed p-value.
        """
        # Use scipy if available, otherwise use normal approximation
        try:
            from scipy.stats import fisher_exact
            _, p = fisher_exact([[a, b], [c, d]], alternative="two-sided")
            return float(p)
        except ImportError:
            # Normal approximation (good for large samples)
            n = a + b + c + d
            if n == 0:
                return 1.0
            # Chi-square approximation
            expected_a = (a + b) * (a + c) / n
            expected_c = (c + d) * (a + c) / n
            if expected_a == 0 or expected_c == 0:
                return 1.0
            chi2 = ((a - expected_a) ** 2 / expected_a +
                    (c - expected_c) ** 2 / expected_c)
            # p-value from chi-square with 1 df (simplified)
            # P(X > chi2) for chi-square(1) = erfc(sqrt(chi2/2))
            return float(math.erfc(math.sqrt(chi2 / 2)))

    # ── Persistence ──────────────────────────────────────────────

    def _save_experiment(self, exp: Experiment) -> None:
        """Save experiment to JSON file."""
        import json
        self._experiments_file.parent.mkdir(parents=True, exist_ok=True)

        all_exps = self._load_all_experiments()
        # Update or append
        for i, existing in enumerate(all_exps):
            if existing.id == exp.id:
                all_exps[i] = exp
                break
        else:
            all_exps.append(exp)

        # Serialize
        data = []
        for e in all_exps:
            data.append({
                "id": e.id,
                "name": e.name,
                "template_name": e.template_name,
                "control_version": e.control_version,
                "treatment_version": e.treatment_version,
                "traffic_split": e.traffic_split,
                "status": e.status,
                "created_at": e.created_at,
                "started_at": e.started_at,
                "ended_at": e.ended_at,
                "control_results": e.control_results,
                "treatment_results": e.treatment_results,
            })

        with open(self._experiments_file, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    def _load_experiment(self, experiment_id: str) -> Experiment | None:
        """Load a single experiment by ID."""
        for exp in self._load_all_experiments():
            if exp.id == experiment_id:
                return exp
        return None

    def _load_all_experiments(self) -> list[Experiment]:
        """Load all experiments from JSON file."""
        import json
        if not self._experiments_file.exists():
            return []
        try:
            with open(self._experiments_file, encoding="utf-8") as f:
                data = json.load(f)
            experiments = []
            for d in data:
                experiments.append(Experiment(
                    id=d["id"],
                    name=d["name"],
                    template_name=d["template_name"],
                    control_version=d["control_version"],
                    treatment_version=d["treatment_version"],
                    traffic_split=d["traffic_split"],
                    status=d["status"],
                    created_at=d["created_at"],
                    started_at=d.get("started_at", 0),
                    ended_at=d.get("ended_at", 0),
                    control_results=d.get("control_results", []),
                    treatment_results=d.get("treatment_results", []),
                ))
            return experiments
        except Exception as exc:
            logger.warning("[ab_test] Failed to load experiments: %s", exc)
            return []
