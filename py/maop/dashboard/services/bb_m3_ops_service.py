"""MAOP Dashboard — M3 Blackboard Operations Service.

Business logic for M3 modules exposed via the dashboard:

- DebateEngine lifecycle (start a debate, query result)
- ConsensusTracker scans (conflict detection)
- BlackboardConvergenceController solve (trigger rules + convergence)
- BlackboardHealthMonitor snapshot + probe status

The service is framework-agnostic: it does not import FastAPI and can be
unit-tested or invoked from CLI/CI without an HTTP context.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from typing import Any

from maop.core.reliability.bb_health import BlackboardHealthMonitor, HealthProbes
from maop.core.reliability.bb_trigger_engine import (
    BlackboardConvergenceController,
    GoalStateCriterion,
    MaxIterationsCriterion,
)
from maop.core.reliability.blackboard import (
    Blackboard,
    BlackboardEntry,
    InvalidDomainError,
    get_blackboard,
)
from maop.core.reliability.consensus_tracker import ConsensusTracker
from maop.core.reliability.debate_engine import (
    DebateEngine,
    Stance,
)

logger = logging.getLogger(__name__)

# ── Module-level singletons (lazy) ──────────────────────────────────

_health_monitor = BlackboardHealthMonitor()
_health_probes = HealthProbes()
_active_controllers: dict[str, BlackboardConvergenceController] = {}
_solve_tasks: dict[str, asyncio.Task[None]] = {}


# ── WebSocket event helpers ──────────────────────────────────────────


async def _ws_emit(event_type: str, data: dict[str, Any]) -> None:
    """Emit a WebSocket event to all connected dashboard clients.

    Best-effort: silently swallows ImportError (dashboard not running) and
    transient send errors.
    """
    try:
        from maop.dashboard.ws_broadcast import _ws_broadcast
        await _ws_broadcast({"type": event_type, "data": data})
    except ImportError:  # silent-ok: optional-dep — dashboard 广播模块未加载
        pass
    except Exception:  # silent-ok: ws-disconnect — 广播为 best-effort，瞬时发送失败可忽略
        pass


# ── Helpers ──────────────────────────────────────────────────────────


def _get_bb() -> Blackboard:
    return get_blackboard()


# ── Debate Engine ────────────────────────────────────────────────────


async def run_debate(
    question_domain: str,
    question_entry_id: str | None = None,
    participants: list[dict[str, Any]] | None = None,
    consensus_threshold: float = 0.6,
    quorum: int = 1,
) -> dict[str, Any]:
    """Run a debate round on a blackboard entry.

    Parameters
    ----------
    question_domain : str
        Domain containing the question entry.
    question_entry_id : str | None
        Specific entry ID to debate. If None, uses the latest entry in domain.
    participants : list[dict] | None
        List of ``{"name": str, "stance": str, "confidence": float}``.
        If None, uses default AGREE/DISAGREE voters.
    consensus_threshold : float
        Weighted confidence threshold to reach consensus.
    quorum : int
        Minimum number of ballots required.
    """
    bb = _get_bb()

    # Find the entry to debate
    try:
        entries = bb.read(question_domain)
    except InvalidDomainError as exc:
        raise ValueError(str(exc)) from exc
    if not entries:
        raise ValueError(f"No entries found in domain {question_domain!r}")

    target_entry: BlackboardEntry
    if question_entry_id:
        target_entry = next(
            (e for e in entries if e.entry_id == question_entry_id), entries[-1]
        )
    else:
        target_entry = entries[-1]

    engine = DebateEngine(bb, consensus_threshold=consensus_threshold, quorum=quorum)

    # Register participants
    if participants:
        for p in participants:
            _stance = Stance(p.get("stance", "agree"))
            _conf = float(p.get("confidence", 0.5))
            _name = p.get("name", f"voter_{uuid.uuid4().hex[:6]}")

            async def voter(b: Any, e: Any, stance=_stance, conf=_conf, name=_name) -> Any:
                return Stance  # placeholder — real voters would analyze the entry

            # Build a proper ballot-returning closure
            async def make_voter(st: Stance, cf: float, nm: str):
                async def _v(b: Any, e: Any):
                    from maop.core.reliability.debate_engine import DebateBallot
                    return DebateBallot(agent=nm, stance=st, confidence=cf)
                return _v

            engine.register_participant(_name, await make_voter(_stance, _conf, _name))
    else:
        # Default: two voters with opposing stances
        async def voter_agree(b: Any, e: Any):
            from maop.core.reliability.debate_engine import DebateBallot
            return DebateBallot(agent="agree_voter", stance=Stance.AGREE, confidence=0.8)

        async def voter_disagree(b: Any, e: Any):
            from maop.core.reliability.debate_engine import DebateBallot
            return DebateBallot(agent="disagree_voter", stance=Stance.DISAGREE, confidence=0.3)

        engine.register_participant("agree_voter", voter_agree)
        engine.register_participant("disagree_voter", voter_disagree)

    await _ws_emit("bb_m3.debate_started", {
        "question_domain": question_domain,
        "question_entry_id": target_entry.entry_id,
        "participants": list(engine.participants),
    })
    result = await engine.debate_and_record(target_entry, result_domain="hypothesis")
    summary = result.summarize()
    await _ws_emit("bb_m3.debate_completed", summary)
    return summary


# ── Consensus Tracker ────────────────────────────────────────────────


async def scan_conflicts(
    domain: str | None = None,
    deadlock_cycles: int = 3,
) -> dict[str, Any]:
    """Scan the blackboard for cross-agent conflicts.

    Parameters
    ----------
    domain : str | None
        Restrict scan to one domain. If None, scans all domains.
    deadlock_cycles : int
        Number of consecutive scans with unresolved conflict before escalating
        to DEADLOCK severity.
    """
    bb = _get_bb()
    tracker = ConsensusTracker(bb, deadlock_cycles=deadlock_cycles)

    if domain:
        # Wrap domain-specific scan by temporarily filtering
        all_conflicts = await tracker.scan()
        conflicts = [c for c in all_conflicts if c.domain == domain]
    else:
        conflicts = await tracker.scan()

    result = {
        "conflicts": [
            {
                "conflict_id": c.conflict_id,
                "domain": c.domain,
                "severity": c.severity.value,
                "contributors": c.contributors,
                "cycle_count": c.cycle_count,
            }
            for c in conflicts
        ],
        "active_count": len(tracker.active_conflicts),
        "summary": tracker.summary(),
    }
    # Emit conflict alert if any conflicts found
    if conflicts:
        await _ws_emit("bb_m3.conflict_alert", {
            "domain": domain,
            "conflicts": result["conflicts"],
        })
    return result


# ── Convergence Controller ──────────────────────────────────────────


async def start_convergence_solve(
    solve_id: str | None = None,
    max_iterations: int = 50,
    goal_domain: str = "decision",
    goal_state: str = "finalized",
) -> dict[str, Any]:
    """Start a convergence solve (async — returns immediately with solve_id).

    The solve runs as a background task so the API is non-blocking.
    """
    bb = _get_bb()
    ctrl = BlackboardConvergenceController(bb, max_iterations=max_iterations)
    ctrl.add_convergence(GoalStateCriterion(goal_domain, state_value=goal_state))
    ctrl.add_convergence(MaxIterationsCriterion(max_iterations))

    sid = solve_id or uuid.uuid4().hex[:12]
    _active_controllers[sid] = ctrl

    # Progress callback — pushes iteration milestones to WS clients
    async def _on_progress(iteration: int, total: int) -> None:
        await _ws_emit("bb_m3.solve_progress", {
            "solve_id": sid,
            "iteration": iteration,
            "max_iterations": total,
        })

    # Launch solve in background
    async def _run():
        try:
            await _ws_emit("bb_m3.solve_started", {
                "solve_id": sid,
                "max_iterations": max_iterations,
                "goal_domain": goal_domain,
                "goal_state": goal_state,
            })
            report = await ctrl.run_until_convergence()
            _health_monitor.record_solve(report)
            # Emit final status
            await _ws_emit("bb_m3.solve_completed", {
                "solve_id": sid,
                "converged": report.get("converged"),
                "iterations": report.get("iterations"),
                "reason": report.get("reason"),
            })
        except Exception as exc:
            logger.error("[bb_m3_ops] solve %s failed: %s", sid, exc)
            await _ws_emit("bb_m3.solve_failed", {
                "solve_id": sid,
                "error": str(exc),
            })

    task = asyncio.create_task(_run())
    _active_controllers[sid] = ctrl
    _solve_tasks[sid] = task
    task.add_done_callback(lambda t: _solve_tasks.pop(sid, None))
    return {"solve_id": sid, "status": "started"}


async def get_convergence_status(solve_id: str) -> dict[str, Any]:
    """Get the current status of a running/completed solve."""
    ctrl = _active_controllers.get(solve_id)
    if ctrl is None:
        raise ValueError(f"Solve {solve_id!r} not found")

    return {
        "solve_id": solve_id,
        "is_running": ctrl.is_running,
        "iteration": ctrl.iteration,
        "rules": [r.name for r in ctrl.rules],
        "trace_len": len(ctrl.get_trace()),
    }


# ── Health & Metrics ────────────────────────────────────────────────


def get_health_snapshot() -> dict[str, Any]:
    """Return full health monitor snapshot."""
    return _health_monitor.snapshot()


def get_probe_status() -> dict[str, Any]:
    """Return liveness + readiness probe results."""
    return {
        "liveness": _health_probes.liveness(),
        "readiness": _health_probes.readiness(),
    }


def register_health_check(name: str, check_fn) -> None:
    """Register a named readiness check function."""
    _health_probes.register(name, check_fn)


# ── Prometheus exposition ────────────────────────────────────────────


def get_prometheus_metrics() -> str:
    """Return Prometheus-text-format metrics from the health monitor."""
    snap = _health_monitor.snapshot()
    lines: list[str] = []

    # Helper to emit a metric line
    def _emit(name: str, value: float, labels: str = "") -> None:
        if labels:
            lines.append(f"maop_blackboard_{name}{{{labels}}} {value}")
        else:
            lines.append(f"maop_blackboard_{name} {value}")

    # Solves
    solves = snap.get("solves", {})
    _emit("solves_initiated_total", solves.get("initiated", 0))
    _emit("solves_converged_total", solves.get("converged", 0))
    _emit("solves_max_iter_total", solves.get("max_iter", 0))
    _emit("solves_convergence_rate", solves.get("convergence_rate", 0.0))

    # Iterations
    _emit("iterations_total", snap.get("iterations_total", 0))

    # Dispatches
    dispatches = snap.get("dispatches", {})
    _emit("ks_dispatches_total", dispatches.get("total", 0))
    _emit("ks_errors_total", dispatches.get("errors", 0))

    # Debates
    debates = snap.get("debates", {})
    _emit("debate_rounds_total", debates.get("rounds", 0))
    _emit("debates_converged_total", debates.get("converged", 0))

    # Conflicts
    conflicts = snap.get("conflicts", {})
    _emit("conflicts_detected_total", conflicts.get("detected", 0))
    _emit("conflicts_resolved_total", conflicts.get("resolved", 0))

    # Dead letters
    _emit("dead_letters_total", snap.get("dead_letters", 0))

    durations = snap.get("solve_duration_ms", {})
    _emit("solve_duration_count", durations.get("count", 0))
    _emit("solve_duration_avg_ms", durations.get("avg", 0.0))
    _emit("solve_duration_p99_ms", durations.get("p99", 0.0))

    # Probe status
    probes = get_probe_status()
    _emit("probe_liveness", 1 if probes["liveness"] else 0)
    _emit("probe_readiness", 1 if probes["readiness"].get("ready", False) else 0)

    return "\n".join(lines) + "\n"
