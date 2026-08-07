"""Tests for EvolutionLoop — closed-loop self-evolution orchestrator."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from maop.core.evolution.evolution_loop import EvolutionLoop, LoopPhase, LoopReport, PhaseResult


@pytest.fixture
def tmp_root(tmp_path: Path) -> Path:
    (tmp_path / "data").mkdir(parents=True, exist_ok=True)
    (tmp_path / "config").mkdir(parents=True, exist_ok=True)
    return tmp_path


@pytest.fixture
def loop(tmp_root: Path) -> EvolutionLoop:
    return EvolutionLoop(root_dir=str(tmp_root), heal_threshold=1, suggest_threshold=2)


class TestPhaseResult:
    def test_phase_result_defaults(self):
        r = PhaseResult(phase=LoopPhase.OBSERVE)
        assert r.success is True
        assert r.duration_s == 0.0
        assert r.details == {}

    def test_phase_result_with_error(self):
        r = PhaseResult(phase=LoopPhase.HEAL, success=False, error="boom")
        assert r.error == "boom"


class TestLoopReport:
    def test_summary_format(self):
        report = LoopReport(
            cycle_id="abc123",
            errors_observed=5,
            heal_attempts=3,
            heal_successes=2,
            suggestions_generated=4,
            suggestions_applied=1,
            validation_improved=True,
            consolidated=2,
            total_duration_s=1.5,
        )
        s = report.summary()
        assert "abc123" in s
        assert "5 errors" in s
        assert "2/3 healed" in s
        assert "4 suggestions" in s
        assert "1 applied" in s
        assert "improved=True" in s
        assert "2 consolidated" in s


class TestEvolutionLoopObserve:
    def test_observe_no_errors(self, loop: EvolutionLoop, tmp_root: Path):
        result = loop._phase_observe()
        assert result.phase == LoopPhase.OBSERVE
        assert result.details.get("hotspot_count", 0) >= 0

    def test_observe_with_errors(self, loop: EvolutionLoop, tmp_root: Path):
        from maop.core.reliability.error_ledger import ErrorLedger
        ledger = ErrorLedger(root_dir=str(tmp_root))
        for _ in range(3):
            ledger.record(error_type="test_err", pattern="test_pattern")
        result = loop._phase_observe()
        assert result.success is True
        assert result.details["hotspot_count"] >= 1
        assert "test_pattern" in result.details["hotspot_patterns"]


class TestEvolutionLoopHeal:
    def test_heal_no_patterns(self, loop: EvolutionLoop):
        result = loop._phase_heal([])
        assert result.success is True
        assert result.details["attempts"] == 0

    def test_heal_with_patterns(self, loop: EvolutionLoop, tmp_root: Path):
        result = loop._phase_heal(["database is locked"])
        assert result.success is True
        assert result.details["attempts"] == 1


class TestEvolutionLoopSuggest:
    def test_suggest_no_patterns(self, loop: EvolutionLoop):
        result = loop._phase_suggest([])
        assert result.success is True
        assert result.details["count"] == 0

    def test_suggest_with_promoted_rules(self, loop: EvolutionLoop, tmp_root: Path):
        from maop.core.reliability.error_ledger import ErrorLedger
        ledger = ErrorLedger(root_dir=str(tmp_root))
        for _ in range(4):
            ledger.record(error_type="routing_err", pattern="routing_mismatch")
        result = loop._phase_suggest(["routing_mismatch"])
        assert result.success is True
        assert result.details["count"] >= 1

    def test_suggest_writes_json(self, loop: EvolutionLoop, tmp_root: Path):
        from maop.core.reliability.error_ledger import ErrorLedger
        ledger = ErrorLedger(root_dir=str(tmp_root))
        for _ in range(4):
            ledger.record(error_type="test_err", pattern="unique_test_pattern")
        loop._phase_suggest(["unique_test_pattern"])
        suggestions_file = tmp_root / "data" / "evolve-suggestions.json"
        assert suggestions_file.exists()
        data = json.loads(suggestions_file.read_text(encoding="utf-8"))
        assert len(data) >= 1


class TestEvolutionLoopEvaluate:
    def test_evaluate_empty(self, loop: EvolutionLoop):
        result = loop._phase_evaluate([])
        assert result.success is True
        assert result.details["approved_count"] == 0

    def test_evaluate_with_suggestions(self, loop: EvolutionLoop, tmp_root: Path):
        suggestions = [
            {
                "id": "S001",
                "type": "error_pattern_rule",
                "severity": "HIGH",
                "auto_applicable": True,
                "description": "Test suggestion",
            }
        ]
        result = loop._phase_evaluate(suggestions)
        assert result.success is True
        assert result.details["total"] == 1


class TestEvolutionLoopApply:
    def test_apply_empty(self, loop: EvolutionLoop):
        result = loop._phase_apply([])
        assert result.success is True
        assert result.details["applied"] == 0


class TestEvolutionLoopValidate:
    def test_validate(self, loop: EvolutionLoop):
        result = loop._phase_validate(baseline_errors=5)
        assert result.success is True
        assert "improved" in result.details


class TestEvolutionLoopConsolidate:
    def test_consolidate(self, loop: EvolutionLoop):
        result = loop._phase_consolidate()
        assert result.success is True
        assert "consolidated" in result.details


class TestEvolutionLoopFullCycle:
    def test_empty_cycle(self, loop: EvolutionLoop):
        report = loop.run_cycle()
        assert report.cycle_id
        assert report.total_duration_s >= 0
        assert len(report.phases) >= 1  # at least observe

    def test_full_cycle_with_errors(self, loop: EvolutionLoop, tmp_root: Path):
        from maop.core.reliability.error_ledger import ErrorLedger
        ledger = ErrorLedger(root_dir=str(tmp_root))
        for _ in range(3):
            ledger.record(error_type="test_error", pattern="test_pattern_full")
        report = loop.run_cycle()
        assert report.errors_observed >= 1
        assert report.heal_attempts >= 1
        assert report.suggestions_generated >= 0
        assert report.total_duration_s >= 0

    def test_cycle_saves_to_db(self, loop: EvolutionLoop):
        loop.run_cycle()
        history = loop.get_cycle_history(limit=5)
        assert len(history) >= 1
        assert history[0].cycle_id

    def test_cycle_stats(self, loop: EvolutionLoop):
        loop.run_cycle()
        stats = loop.get_stats()
        assert stats["total_cycles"] >= 1
        assert "improvement_rate" in stats
        assert "avg_duration_s" in stats


class TestEvolutionLoopPersistence:
    def test_multiple_cycles(self, loop: EvolutionLoop):
        loop.run_cycle()
        loop.run_cycle()
        loop.run_cycle()
        history = loop.get_cycle_history(limit=10)
        assert len(history) == 3
        stats = loop.get_stats()
        assert stats["total_cycles"] == 3

    def test_suggestions_dedup(self, loop: EvolutionLoop, tmp_root: Path):
        from maop.core.reliability.error_ledger import ErrorLedger
        ledger = ErrorLedger(root_dir=str(tmp_root))
        for _ in range(4):
            ledger.record(error_type="dedup_test", pattern="dedup_pattern")
        loop._phase_suggest(["dedup_pattern"])
        loop._phase_suggest(["dedup_pattern"])
        suggestions_file = tmp_root / "data" / "evolve-suggestions.json"
        data = json.loads(suggestions_file.read_text(encoding="utf-8"))
        ids = [s.get("id") for s in data]
        assert len(ids) == len(set(ids)), "Suggestions should not be duplicated"



# ── t10: dry-run + rollback ───────────────────────────────────────


class TestDryRunMode:
    """Verify that dry_run=True runs all phases without side effects."""

    def test_run_cycle_dry_run_returns_report(self, loop: EvolutionLoop, tmp_root: Path):
        # Seed an error so the cycle has work to do.
        from maop.core.reliability.error_ledger import ErrorLedger
        ledger = ErrorLedger(root_dir=str(tmp_root))
        for _ in range(3):
            ledger.record(error_type="TestError", pattern="routing.timeout", context="dry_run")

        report = loop.run_cycle(dry_run=True)
        assert report.dry_run is True
        # APPLY phase should record proposed changes without executing.
        apply_phase = next(p for p in report.phases if p.phase.value == "apply")
        assert apply_phase.details.get("dry_run") is True
        assert "proposed" in apply_phase.details

    def test_dry_run_does_not_snapshot(self, loop: EvolutionLoop, tmp_root: Path):
        from maop.core.reliability.error_ledger import ErrorLedger
        ledger = ErrorLedger(root_dir=str(tmp_root))
        for _ in range(3):
            ledger.record(error_type="TestError", pattern="routing.timeout", context="dry_run")

        report = loop.run_cycle(dry_run=True)
        # In dry_run mode we should not have taken a snapshot.
        assert report.snapshot_id == ""
        assert report.rolled_back is False


class TestRollbackCycle:
    """Verify that rollback_cycle restores files from a pre-APPLY snapshot."""

    def test_rollback_cycle_with_explicit_snapshot(self, loop: EvolutionLoop, tmp_root: Path):
        # Manually create a ChangeTracker snapshot, then call rollback_cycle.
        from maop.core.reliability.change_tracker import ChangeTracker
        ct = ChangeTracker(root_dir=str(tmp_root))
        # Create a sample file so snapshot has something to back up.
        (tmp_root / "config.yaml").write_text("version: 1\n", encoding="utf-8")
        snap_id = ct.snapshot(str(tmp_root), label="manual-test")
        # Modify the file after snapshot.
        (tmp_root / "config.yaml").write_text("version: 2\n", encoding="utf-8")
        assert (tmp_root / "config.yaml").read_text(encoding="utf-8") == "version: 2\n"

        restored = loop.rollback_cycle("fake-cycle-id", snapshot_id=snap_id)
        assert restored >= 1
        # File should be restored to version 1.
        assert (tmp_root / "config.yaml").read_text(encoding="utf-8") == "version: 1\n"

    def test_rollback_cycle_unknown_cycle_returns_zero(self, loop: EvolutionLoop):
        restored = loop.rollback_cycle("nonexistent-cycle-id")
        assert restored == 0

    def test_rollback_cycle_no_snapshot_returns_zero(self, loop: EvolutionLoop, tmp_root: Path):
        # Persist a cycle with no snapshot_id; rollback should return 0.
        from maop.core.evolution.evolution_loop import LoopReport
        empty_report = LoopReport(cycle_id="no-snap-cycle")
        loop._save_report(empty_report)
        restored = loop.rollback_cycle("no-snap-cycle")
        assert restored == 0
