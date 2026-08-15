"""MAOP A/B Testing Framework — Structured experiments with statistical significance.

Supports:
  - Traffic splitting (percentage-based)
  - Metric collection per variant
  - Statistical significance check (simple Z-test for proportions)
  - Auto-selection of winning variant

Usage::

    from maop.core.evolution.ab_test import ABTestManager

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

import contextlib
import json
import logging
import math
import sqlite3
import time
from enum import Enum
from pathlib import Path
from typing import Any, cast

from pydantic import BaseModel, Field

from maop.core.backends.db_utils import get_db_path, sqlite_connect

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

    def _db_connect(self) -> contextlib.AbstractContextManager[sqlite3.Connection]:
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
        with self._db_connect() as conn:
            conn.execute(
                """INSERT OR REPLACE INTO ab_experiments (name, variants, min_samples, confidence_level, created_at)
                   VALUES (?, ?, ?, ?, ?)""",
                (name, json.dumps(variants), min_samples, confidence_level, time.time()),
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

            conn.execute(
                """INSERT INTO ab_assignments (experiment, entity_id, variant, assigned_at)
                   VALUES (?, ?, ?, ?)""",
                (experiment, entity_id, chosen, time.time()),
            )
        return chosen

    def record(self, experiment: str, variant: str, entity_id: str, success: bool) -> None:
        with self._db_connect() as conn:
            conn.execute(
                """INSERT INTO ab_metrics (experiment, variant, entity_id, success, recorded_at)
                   VALUES (?, ?, ?, ?, ?)""",
                (experiment, variant, entity_id, int(success), time.time()),
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


# ════════════════════════════════════════════════════════════════════
# F2-01: Sequential Probability Ratio Test (SPRT) — 序贯统计检验
# ════════════════════════════════════════════════════════════════════
# 在每个样本后更新对数似然比 (LLR)，达到边界即停止并决策：
#   H1: treatment 成功率 = p1 (优于 control)
#   H0: treatment 成功率 = p0 (无改进 / 与 control 相同)
#   LLR >= log(A) → 接受 H1 (treatment 显著更优)
#   LLR <= log(B) → 接受 H0 (无显著差异)
#   否则继续采样
# 优势：无需预先固定样本量，显著时立即停止，节省实验成本。


class SPRTDecision(str, Enum):
    CONTINUE = "continue"
    ACCEPT_H1 = "accept_h1"  # treatment 显著更优
    ACCEPT_H0 = "accept_h0"  # 无显著差异


class SPRTConfig(BaseModel):
    """SPRT 检验参数。"""

    alpha: float = 0.05  # 第一类错误率（假阳性）
    beta: float = 0.20  # 第二类错误率（假阴性）
    p0: float = 0.50  # H0: treatment 成功率（无改进基线）
    p1: float = 0.65  # H1: treatment 成功率（期望改进）
    max_samples: int = 10000  # 安全上限，避免无限采样

    def model_post_init(self, __context: Any) -> None:  # noqa: PYI063
        if not (0 < self.alpha < 1):
            raise ValueError(f"alpha must be in (0,1), got {self.alpha}")
        if not (0 < self.beta < 1):
            raise ValueError(f"beta must be in (0,1), got {self.beta}")
        if not (0 < self.p0 < 1) or not (0 < self.p1 < 1):
            raise ValueError("p0, p1 must be in (0,1)")
        if self.p1 <= self.p0:
            raise ValueError(f"p1 ({self.p1}) must be > p0 ({self.p0})")


class SPRTState(BaseModel):
    """SPRT 检验的运行时状态。"""

    samples: int = 0
    successes: int = 0
    llr: float = 0.0  # 对数似然比
    decision: SPRTDecision = SPRTDecision.CONTINUE
    winner: str = ""  # ACCEPT_H1 时为 treatment 名
    stopped_at: int = 0  # 停止时的样本数（0 表示未停止）

    @property
    def is_stopped(self) -> bool:
        return self.decision != SPRTDecision.CONTINUE

    @property
    def success_rate(self) -> float:
        return self.successes / self.samples if self.samples else 0.0


class SPRTTest:
    """序贯概率比检验器（伯努利样本）。

    每调用 :meth:`update` 处理一个样本，自动更新 LLR 并检查边界。

    Parameters
    ----------
    config : SPRTConfig
        检验参数。
    treatment_name : str
        treatment 变体名（用于 winner 标记）。
    """

    def __init__(self, config: SPRTConfig | None = None, *, treatment_name: str = "treatment") -> None:
        self._config = config or SPRTConfig()
        self._treatment = treatment_name
        self._state = SPRTState()
        # 预计算边界与每样本增量
        self._upper = math.log((1 - self._config.beta) / self._config.alpha)
        self._lower = math.log(self._config.beta / (1 - self._config.alpha))
        # 伯努利 LLR 增量：x=1 → log(p1/p0), x=0 → log((1-p1)/(1-p0))
        self._llr_success = math.log(self._config.p1 / self._config.p0)
        self._llr_failure = math.log((1 - self._config.p1) / (1 - self._config.p0))

    @property
    def config(self) -> SPRTConfig:
        return self._config

    @property
    def state(self) -> SPRTState:
        return self._state

    @property
    def boundaries(self) -> tuple[float, float]:
        """返回 (lower, upper) 对数似然比边界。"""
        return self._lower, self._upper

    def update(self, success: bool) -> SPRTState:
        """处理一个样本，更新 LLR 并检查停止边界。"""
        if self._state.is_stopped:
            return self._state
        self._state.samples += 1
        if success:
            self._state.successes += 1
            self._state.llr += self._llr_success
        else:
            self._state.llr += self._llr_failure

        if self._state.llr >= self._upper:
            self._state.decision = SPRTDecision.ACCEPT_H1
            self._state.winner = self._treatment
            self._state.stopped_at = self._state.samples
        elif self._state.llr <= self._lower:
            self._state.decision = SPRTDecision.ACCEPT_H0
            self._state.stopped_at = self._state.samples
        elif self._state.samples >= self._config.max_samples:
            # 达到上限仍未决策：按当前成功率软决策
            self._state.decision = (
                SPRTDecision.ACCEPT_H1
                if self._state.success_rate >= (self._config.p0 + self._config.p1) / 2
                else SPRTDecision.ACCEPT_H0
            )
            if self._state.decision == SPRTDecision.ACCEPT_H1:
                self._state.winner = self._treatment
            self._state.stopped_at = self._state.samples
        return self._state

    def update_batch(self, successes: int, failures: int) -> SPRTState:
        """批量更新（等价于逐个 update 但更快）。"""
        for _ in range(successes):
            if self._state.is_stopped:
                break
            self.update(True)
        for _ in range(failures):
            if self._state.is_stopped:
                break
            self.update(False)
        return self._state

    def reset(self) -> None:
        """重置检验器状态（保留 config）。"""
        self._state = SPRTState()


# ── 高层 AB Test Framework（SPRT 驱动）──────────────────────────


class SPSRTExperimentResult(BaseModel):
    """SPRT 实验评估结果。"""

    experiment: str
    control: VariantStats
    treatment: VariantStats
    sprt: SPRTState
    decision: SPRTDecision
    winner: str = ""
    is_significant: bool = False


class ABTestFramework:
    """F2-01 ABTestFramework — SPRT 驱动的序贯 A/B 测试框架。

    在 :class:`ABTestManager` 的流量分配 / 指标记录之上，叠加 SPRT
    序贯检验层：每个实验维护一个 :class:`SPRTTest`，记录样本时
    实时更新，达到边界自动判定显著性。

    Usage::

        from maop.core.evolution.ab_test import ABTestFramework, SPRTConfig

        fw = ABTestFramework(root_dir="/path/to/MAOP")
        fw.create_experiment(
            name="prompt_v2",
            variants={"control": 50, "treatment": 50},
            sprt_config=SPRTConfig(p0=0.6, p1=0.75),
        )
        variant = fw.assign("prompt_v2", "user-123")
        fw.record("prompt_v2", variant, "user-123", success=True)
        result = fw.evaluate_sprt("prompt_v2")
        if result.is_significant:
            print(f"Winner: {result.winner}")
    """

    def __init__(self, root_dir: str | Path) -> None:
        self._manager = ABTestManager(root_dir=root_dir)
        self._sprt_configs: dict[str, SPRTConfig] = {}
        self._sprt_tests: dict[str, SPRTTest] = {}
        self._treatment_names: dict[str, str] = {}

    # 委托底层 manager
    def _db_connect(self) -> contextlib.AbstractContextManager[sqlite3.Connection]:
        return self._manager._db_connect()

    def create_experiment(
        self,
        name: str,
        variants: dict[str, int],
        *,
        min_samples: int = 30,
        confidence_level: float = 0.95,
        sprt_config: SPRTConfig | None = None,
    ) -> ExperimentConfig:
        """创建实验并初始化 SPRT 检验器。

        ``variants`` 应包含 ``control`` 与一个 treatment 变体；
        treatment 取第一个非 ``control`` 的变体名。
        """
        config = self._manager.create_experiment(
            name=name, variants=variants,
            min_samples=min_samples, confidence_level=confidence_level,
        )
        sprt_cfg = sprt_config or SPRTConfig()
        treatment = next((v for v in variants if v != "control"), "treatment")
        self._sprt_configs[name] = sprt_cfg
        self._treatment_names[name] = treatment
        self._sprt_tests[name] = SPRTTest(sprt_cfg, treatment_name=treatment)
        logger.info("[ab-fw] SPRT experiment created: %s (treatment=%s)", name, treatment)
        return config

    def assign(self, experiment: str, entity_id: str) -> str:
        return self._manager.assign(experiment, entity_id)

    def record(self, experiment: str, variant: str, entity_id: str, success: bool) -> SPRTState:
        """记录样本并实时更新 SPRT 状态。

        仅 treatment 变体的样本会喂给 SPRT（检验 treatment 是否优于
        control 的基线 p0）。
        """
        self._manager.record(experiment, variant, entity_id, success)
        test = self._sprt_tests.get(experiment)
        if test is None:
            # 实验可能由旧 ABTestManager 创建，惰性初始化 SPRT
            treatment = variant if variant != "control" else "treatment"
            cfg = self._sprt_configs.get(experiment) or SPRTConfig()
            test = SPRTTest(cfg, treatment_name=treatment)
            self._sprt_tests[experiment] = test
            self._treatment_names.setdefault(experiment, treatment)
            self._sprt_configs.setdefault(experiment, cfg)
        # 仅 treatment 样本更新 SPRT
        if variant == self._treatment_names.get(experiment, "treatment"):
            test.update(success)
        return test.state

    def evaluate_sprt(self, experiment: str) -> SPSRTExperimentResult:
        """返回当前 SPRT 状态 + 双方 VariantStats。"""
        test = self._sprt_tests.get(experiment)
        if test is None:
            cfg = self._sprt_configs.get(experiment, SPRTConfig())
            treatment_name_init = self._treatment_names.get(experiment, "treatment")
            test = SPRTTest(cfg, treatment_name=treatment_name_init)
            self._sprt_tests[experiment] = test

        # 从 DB 拉取双方统计
        with self._db_connect() as conn:
            rows = conn.execute(
                """SELECT variant, COUNT(*), SUM(success) FROM ab_metrics
                   WHERE experiment = ? GROUP BY variant""",
                (experiment,),
            ).fetchall()
        stats_map: dict[str, VariantStats] = {}
        for r in rows:
            stats_map[r[0]] = VariantStats(
                name=r[0], samples=r[1], successes=r[2],
                success_rate=r[2] / r[1] if r[1] else 0.0,
            )
        control = stats_map.get("control", VariantStats(name="control"))
        treatment_name = self._treatment_names.get(experiment, "treatment")
        treatment = stats_map.get(treatment_name, VariantStats(name=treatment_name))

        decision = test.state.decision
        winner = test.state.winner if decision == SPRTDecision.ACCEPT_H1 else ""
        is_sig = decision == SPRTDecision.ACCEPT_H1

        return SPSRTExperimentResult(
            experiment=experiment,
            control=control,
            treatment=treatment,
            sprt=test.state,
            decision=decision,
            winner=winner,
            is_significant=is_sig,
        )

    def get_sprt_state(self, experiment: str) -> SPRTState:
        test = self._sprt_tests.get(experiment)
        return test.state if test else SPRTState()

    def list_experiments(self) -> list[str]:
        with self._db_connect() as conn:
            rows = conn.execute("SELECT name FROM ab_experiments ORDER BY created_at DESC").fetchall()
        return [r[0] for r in rows]
