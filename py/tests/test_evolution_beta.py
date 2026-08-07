"""Tests for Phase β self-evolution components.

Covers:
  - HistoryAnalyzer (history_analyzer.py)
  - ABTestFramework (ab_test_framework.py)
  - EvolveEngine.auto_evolve (evolve.py)

Each test uses the overridden ``tmp_path`` fixture (see conftest.py) and the
``_isolate_data_dir`` autouse fixture which points ``MAOP_DATA_DIR`` at an
isolated temp directory, so DB state never leaks between tests.
"""

from __future__ import annotations

import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import pytest

from maop.ab_test_framework import ABTestFramework, Experiment, ExperimentResult
from maop.history_analyzer import (
    AnalysisReport,
    HistoryAnalyzer,
)

# ── Helpers ────────────────────────────────────────────────────────

def _make_delegations_db(db_path: Path, rows: list[dict]) -> None:
    """Create a delegations table and insert rows for testing."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        """CREATE TABLE IF NOT EXISTS delegations (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          timestamp TEXT,
          agent TEXT,
          task TEXT,
          routing_key TEXT,
          exit_code INT,
          stdout TEXT,
          stderr TEXT,
          duration_ms INT,
          trace_id TEXT
        )"""
    )
    for r in rows:
        conn.execute(
            """INSERT INTO delegations
               (timestamp, agent, task, routing_key, exit_code, stdout, stderr, duration_ms, trace_id)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                r.get("timestamp", datetime.now(timezone.utc).isoformat()),
                r.get("agent", "claude"),
                r.get("task", ""),
                r.get("routing_key", ""),
                r.get("exit_code", 0),
                r.get("stdout", ""),
                r.get("stderr", ""),
                r.get("duration_ms", 0),
                r.get("trace_id", ""),
            ),
        )
    conn.commit()
    conn.close()


def _reset_cost_tracker_singleton() -> None:
    """Reset the process-wide CostTracker singleton so tests are isolated."""
    import maop.core.monitoring.cost_tracker as ct
    ct._cost_tracker_instance = None


# ── HistoryAnalyzer tests ──────────────────────────────────────────


class TestHistoryAnalyzer:
    """Tests for the HistoryAnalyzer class."""

    def test_analyze_returns_report(self, tmp_path: Path) -> None:
        """analyze() returns an AnalysisReport instance."""
        _reset_cost_tracker_singleton()
        analyzer = HistoryAnalyzer(root_dir=tmp_path)
        report = analyzer.analyze(hours=1)
        assert isinstance(report, AnalysisReport)

    def test_report_has_required_fields(self, tmp_path: Path) -> None:
        """AnalysisReport has all required fields populated."""
        _reset_cost_tracker_singleton()
        analyzer = HistoryAnalyzer(root_dir=tmp_path)
        report = analyzer.analyze(hours=24)
        assert report.period_hours == 24
        assert isinstance(report.total_loops, int)
        assert isinstance(report.success_rate, float)
        assert isinstance(report.failure_clusters, list)
        assert isinstance(report.bottlenecks, list)
        assert isinstance(report.cost_drivers, list)
        assert isinstance(report.recommendations, list)
        assert report.generated_at > 0

    def test_failure_clusters_empty_without_data(self, tmp_path: Path) -> None:
        """No delegations data → failure_clusters is empty."""
        _reset_cost_tracker_singleton()
        analyzer = HistoryAnalyzer(root_dir=tmp_path)
        report = analyzer.analyze(hours=1)
        assert report.failure_clusters == []

    def test_bottlenecks_empty_without_data(self, tmp_path: Path) -> None:
        """No timeseries data → bottlenecks is empty."""
        _reset_cost_tracker_singleton()
        analyzer = HistoryAnalyzer(root_dir=tmp_path)
        report = analyzer.analyze(hours=1)
        assert report.bottlenecks == []

    def test_cost_drivers_empty_without_tracker(self, tmp_path: Path) -> None:
        """No CostTracker available → cost_drivers is empty."""
        analyzer = HistoryAnalyzer(root_dir=tmp_path)
        # Force _get_cost_tracker to return None
        analyzer._cost_tracker = None
        with patch.object(analyzer, "_get_cost_tracker", return_value=None):
            report = analyzer.analyze(hours=1)
        assert report.cost_drivers == []

    def test_failure_clusters_with_data(self, tmp_path: Path) -> None:
        """Failed delegations are clustered by error type + agent."""
        _reset_cost_tracker_singleton()
        _make_delegations_db(
            tmp_path / "data" / "maop.db",
            [
                {"agent": "claude", "exit_code": 1, "stderr": "timeout: exec took too long"},
                {"agent": "claude", "exit_code": 1, "stderr": "timeout: exec took too long"},
                {"agent": "claude", "exit_code": 1, "stderr": "timeout: exec took too long"},
                {"agent": "gpt", "exit_code": 2, "stderr": "auth: invalid key"},
            ],
        )
        analyzer = HistoryAnalyzer(root_dir=tmp_path)
        report = analyzer.analyze(hours=24)
        assert len(report.failure_clusters) > 0
        # The timeout cluster should have count 3
        timeout_cluster = next(
            c for c in report.failure_clusters if "timeout" in c.pattern
        )
        assert timeout_cluster.count == 3
        assert "claude" in timeout_cluster.agents

    def test_recommendations_generated(self, tmp_path: Path) -> None:
        """Recommendations are generated when issues exist."""
        _reset_cost_tracker_singleton()
        _make_delegations_db(
            tmp_path / "data" / "maop.db",
            [
                {"agent": "claude", "exit_code": 1, "stderr": "timeout: too slow"},
                {"agent": "claude", "exit_code": 1, "stderr": "timeout: too slow"},
                {"agent": "claude", "exit_code": 1, "stderr": "timeout: too slow"},
            ],
        )
        analyzer = HistoryAnalyzer(root_dir=tmp_path)
        report = analyzer.analyze(hours=24)
        assert len(report.recommendations) > 0
        # At least one recommendation should mention the failure pattern
        assert any("timeout" in r or "failure" in r.lower() for r in report.recommendations)

    def test_root_cause_hypothesis(self, tmp_path: Path) -> None:
        """_hypothesize_root_cause classifies errors correctly."""
        analyzer = HistoryAnalyzer(root_dir=tmp_path)
        # Timeout
        h = analyzer._hypothesize_root_cause("timeout exceeded", "claude")
        assert "overloaded" in h.lower() or "timeout" in h.lower()
        # Rate limit
        h = analyzer._hypothesize_root_cause("rate limit exceeded", "gpt")
        assert "rate" in h.lower()
        # Auth
        h = analyzer._hypothesize_root_cause("auth failed", "claude")
        assert "auth" in h.lower() or "api key" in h.lower()
        # Context / token
        h = analyzer._hypothesize_root_cause("context length exceeded", "claude")
        assert "context" in h.lower() or "truncat" in h.lower()
        # Unknown
        h = analyzer._hypothesize_root_cause("something weird", "claude")
        assert "claude" in h

    def test_bottlenecks_with_timeseries_data(self, tmp_path: Path) -> None:
        """Bottlenecks are identified from timeseries data."""
        _reset_cost_tracker_singleton()
        from maop.core.monitoring.timeseries import TimeSeriesStore
        ts = TimeSeriesStore(db_path=tmp_path / "data" / "timeseries.db")
        now = time.time()
        # Record slow exec phase (> 10s → max impact)
        for i in range(5):
            ts.record("exec_duration_ms", 15000, timestamp=now - i * 60)
        ts.record("loop_duration_ms", 20000, timestamp=now)
        del ts

        analyzer = HistoryAnalyzer(root_dir=tmp_path)
        report = analyzer.analyze(hours=1)
        assert len(report.bottlenecks) > 0
        # The exec phase should be identified as a bottleneck
        exec_bn = next(
            (b for b in report.bottlenecks if "exec" in b.component), None
        )
        assert exec_bn is not None
        assert exec_bn.avg_duration_ms > 0


# ── ABTestFramework tests ──────────────────────────────────────────


class TestABTestFramework:
    """Tests for the ABTestFramework class."""

    def test_create_experiment(self, tmp_path: Path) -> None:
        """create_experiment returns an Experiment with correct fields."""
        fw = ABTestFramework(root_dir=tmp_path)
        exp = fw.create_experiment(
            name="test_exp",
            template_name="tpl",
            control_version="v1",
            treatment_version="v2",
            traffic_split=0.2,
        )
        assert isinstance(exp, Experiment)
        assert exp.name == "test_exp"
        assert exp.template_name == "tpl"
        assert exp.control_version == "v1"
        assert exp.treatment_version == "v2"
        assert exp.traffic_split == 0.2
        assert exp.status == "running"
        assert len(exp.id) > 0

    def test_create_experiment_invalid_split(self, tmp_path: Path) -> None:
        """traffic_split outside (0,1) raises ValueError."""
        fw = ABTestFramework(root_dir=tmp_path)
        with pytest.raises(ValueError):
            fw.create_experiment("x", "t", "v1", "v2", traffic_split=0.0)
        with pytest.raises(ValueError):
            fw.create_experiment("x", "t", "v1", "v2", traffic_split=1.0)

    def test_assign_variant_returns_valid_value(self, tmp_path: Path) -> None:
        """assign_variant returns either 'control' or 'treatment'."""
        fw = ABTestFramework(root_dir=tmp_path)
        exp = fw.create_experiment("test", "tpl", "v1", "v2", 0.5)
        for _ in range(20):
            v = fw.assign_variant(exp.id, user_id="user123")
            assert v in ("control", "treatment")

    def test_assign_variant_distribution(self, tmp_path: Path) -> None:
        """With traffic_split=0.2, roughly 20% go to treatment."""
        fw = ABTestFramework(root_dir=tmp_path)
        exp = fw.create_experiment("test", "tpl", "v1", "v2", 0.2)
        treatment_count = 0
        n = 500
        for i in range(n):
            if fw.assign_variant(exp.id, user_id=f"user{i}") == "treatment":
                treatment_count += 1
        ratio = treatment_count / n
        # Should be roughly 0.20 — allow a wide margin for hash randomness
        assert 0.10 < ratio < 0.35, f"treatment ratio {ratio:.2f} outside expected range"

    def test_assign_variant_stopped_returns_control(self, tmp_path: Path) -> None:
        """A stopped experiment always assigns control."""
        fw = ABTestFramework(root_dir=tmp_path)
        exp = fw.create_experiment("test", "tpl", "v1", "v2", 0.5)
        fw.stop_experiment(exp.id)
        assert fw.assign_variant(exp.id, user_id="u1") == "control"

    def test_record_result(self, tmp_path: Path) -> None:
        """record_result appends to the correct variant list."""
        fw = ABTestFramework(root_dir=tmp_path)
        exp = fw.create_experiment("test", "tpl", "v1", "v2", 0.5)
        fw.record_result(exp.id, "control", success=True, latency_ms=100, cost=0.01)
        fw.record_result(exp.id, "treatment", success=False, latency_ms=200, cost=0.02)
        loaded = fw.get_experiment(exp.id)
        assert loaded is not None
        assert len(loaded.control_results) == 1
        assert len(loaded.treatment_results) == 1
        assert loaded.control_results[0]["success"] is True
        assert loaded.treatment_results[0]["success"] is False
        assert loaded.control_results[0]["latency_ms"] == 100

    def test_evaluate_inconclusive_low_samples(self, tmp_path: Path) -> None:
        """Insufficient samples → inconclusive result."""
        fw = ABTestFramework(root_dir=tmp_path)
        exp = fw.create_experiment("test", "tpl", "v1", "v2", 0.5)
        # Only a few samples — below the 30 minimum
        for _ in range(5):
            fw.record_result(exp.id, "control", success=True)
            fw.record_result(exp.id, "treatment", success=False)
        result = fw.evaluate_experiment(exp.id)
        assert isinstance(result, ExperimentResult)
        assert result.winner == "inconclusive"
        assert result.is_significant is False
        assert result.confidence == "low"

    def test_evaluate_significant(self, tmp_path: Path) -> None:
        """Enough samples with clear difference → significant result."""
        fw = ABTestFramework(root_dir=tmp_path)
        exp = fw.create_experiment("test", "tpl", "v1", "v2", 0.5)
        # Control: 50% success, Treatment: 90% success — clear difference
        for i in range(40):
            fw.record_result(exp.id, "control", success=(i % 2 == 0))
        for i in range(40):
            fw.record_result(exp.id, "treatment", success=(i % 10 != 0))
        result = fw.evaluate_experiment(exp.id)
        assert result.control_samples == 40
        assert result.treatment_samples == 40
        assert result.is_significant is True
        assert result.winner == "treatment"
        assert result.p_value < 0.05

    def test_evaluate_nonexistent_experiment(self, tmp_path: Path) -> None:
        """Evaluating a non-existent experiment returns inconclusive."""
        fw = ABTestFramework(root_dir=tmp_path)
        result = fw.evaluate_experiment("nonexistent-id")
        assert result.winner == "inconclusive"
        assert result.control_samples == 0
        assert result.treatment_samples == 0

    def test_fisher_exact_returns_probability(self, tmp_path: Path) -> None:
        """_fisher_exact returns a float in [0, 1]."""
        fw = ABTestFramework(root_dir=tmp_path)
        # Identical distributions → high p-value (not significant)
        p = fw._fisher_exact(10, 10, 10, 10)
        assert 0.0 <= p <= 1.0
        assert p > 0.5  # Identical → not significant
        # Very different distributions → low p-value (significant)
        p2 = fw._fisher_exact(40, 0, 0, 40)
        assert 0.0 <= p2 <= 1.0
        assert p2 < 0.05

    def test_stop_experiment(self, tmp_path: Path) -> None:
        """stop_experiment sets status to 'stopped'."""
        fw = ABTestFramework(root_dir=tmp_path)
        exp = fw.create_experiment("test", "tpl", "v1", "v2", 0.5)
        assert fw.stop_experiment(exp.id) is True
        loaded = fw.get_experiment(exp.id)
        assert loaded is not None
        assert loaded.status == "stopped"
        assert loaded.ended_at > 0

    def test_stop_nonexistent_returns_false(self, tmp_path: Path) -> None:
        """Stopping a non-existent experiment returns False."""
        fw = ABTestFramework(root_dir=tmp_path)
        assert fw.stop_experiment("nonexistent") is False

    def test_persistence(self, tmp_path: Path) -> None:
        """Experiments are persisted to JSON and reloaded."""
        fw1 = ABTestFramework(root_dir=tmp_path)
        exp = fw1.create_experiment("persist_test", "tpl", "v1", "v2", 0.3)
        fw1.record_result(exp.id, "control", success=True)

        # Create a new framework instance pointing at the same root
        fw2 = ABTestFramework(root_dir=tmp_path)
        loaded = fw2.get_experiment(exp.id)
        assert loaded is not None
        assert loaded.name == "persist_test"
        assert loaded.traffic_split == 0.3
        assert len(loaded.control_results) == 1

        # Also verify list_experiments
        all_exps = fw2.list_experiments()
        assert len(all_exps) >= 1
        assert any(e.id == exp.id for e in all_exps)

    def test_promote_treatment(self, tmp_path: Path) -> None:
        """promote_treatment updates current_version in PromptManager DB."""
        from maop.prompt_manager import PromptManager

        # Create a template with v1 as current, and a v2 version
        pm = PromptManager(root_dir=tmp_path)
        pm.create("tpl1", content="v1 content", version="v1")
        pm.create("tpl1", content="v2 content", version="v2")
        # Verify current is v1
        assert pm.get("tpl1").version == "v1"

        # Create experiment and promote
        fw = ABTestFramework(root_dir=tmp_path)
        exp = fw.create_experiment(
            name="promote_test",
            template_name="tpl1",
            control_version="v1",
            treatment_version="v2",
            traffic_split=0.5,
        )
        assert fw.promote_treatment(exp.id) is True

        # Verify current_version is now v2
        assert pm.get("tpl1").version == "v2"

        # Verify experiment status updated
        loaded = fw.get_experiment(exp.id)
        assert loaded.status == "completed"

    def test_promote_treatment_nonexistent_template(self, tmp_path: Path) -> None:
        """promote_treatment returns False for non-existent template."""
        fw = ABTestFramework(root_dir=tmp_path)
        exp = fw.create_experiment(
            name="promote_fail",
            template_name="nonexistent_tpl",
            control_version="v1",
            treatment_version="v2",
            traffic_split=0.5,
        )
        assert fw.promote_treatment(exp.id) is False


# ── EvolveEngine.auto_evolve tests ─────────────────────────────────


class TestAutoEvolve:
    """Tests for EvolveEngine.auto_evolve (Phase β)."""

    def test_auto_evolve_returns_dict(self, tmp_path: Path) -> None:
        """auto_evolve returns a dict with expected keys."""
        _reset_cost_tracker_singleton()
        from maop.evolve import EvolveEngine
        (tmp_path / "data").mkdir(parents=True, exist_ok=True)
        eng = EvolveEngine(root_dir=tmp_path)
        result = eng.auto_evolve(hours=1)
        assert isinstance(result, dict)
        assert "analysis_report" in result
        assert "new_suggestions" in result
        assert "auto_applied" in result
        assert isinstance(result["new_suggestions"], int)
        assert isinstance(result["auto_applied"], int)

    def test_auto_evolve_with_failures(self, tmp_path: Path) -> None:
        """auto_evolve generates suggestions when failures exist."""
        _reset_cost_tracker_singleton()
        from maop.evolve import EvolveEngine
        _make_delegations_db(
            tmp_path / "data" / "maop.db",
            [
                {"agent": "claude", "exit_code": 1, "stderr": "timeout: too slow"},
                {"agent": "claude", "exit_code": 1, "stderr": "timeout: too slow"},
                {"agent": "claude", "exit_code": 1, "stderr": "timeout: too slow"},
            ],
        )
        eng = EvolveEngine(root_dir=tmp_path)
        result = eng.auto_evolve(hours=24)
        assert result["new_suggestions"] >= 1
        # The suggestions should be saved
        saved = eng._load_suggestions()
        assert any(s.type == "recurring_failure" for s in saved)

    def test_auto_evolve_no_data(self, tmp_path: Path) -> None:
        """auto_evolve handles empty data gracefully."""
        _reset_cost_tracker_singleton()
        from maop.evolve import EvolveEngine
        (tmp_path / "data").mkdir(parents=True, exist_ok=True)
        eng = EvolveEngine(root_dir=tmp_path)
        result = eng.auto_evolve(hours=1)
        assert result["new_suggestions"] == 0
        assert result["auto_applied"] == 0
