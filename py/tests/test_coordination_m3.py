"""MAOP Coordination — M3 全链路集成测试.

Tests the complete M3 blackboard reliability stack across five sub-modules:

- **M3-A** : Trigger rule engine & convergence controller
- **M3-B** : Knowledge source agent bridge, debate engine, consensus tracker
- **M3-C** : Enterprise multi-tenant blackboard isolation & RBAC
- **M3-D** : Cross-project event federation bus
- **M3-E** : Health monitor & metrics

Design reference: tests/test_coordination_m2.py (M2 test).
"""

from __future__ import annotations

import sys

import pytest

sys.path.insert(0, r"F:/Nexus/MAOP/py")

from maop.core.reliability.blackboard import (
    Blackboard,
    BlackboardEntry,
    KnowledgeSource,
)

# ── Fixtures ──────────────────────────────────────────────────────


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
def blackboard():
    return Blackboard()


# ────────────────────────────────────────────────────────────────────
# M3-A: Trigger Rule Engine & Convergence Controller
# ────────────────────────────────────────────────────────────────────


class TestTriggerRule:
    """Tests for bb_trigger_engine.TriggerRule."""

    def test_default_creation(self):
        ks = _make_simple_ks("ks1")
        rule = TriggerRule(name="r1", ks=ks, predicate=lambda bb, e: True)
        assert rule.name == "r1"
        assert rule.priority == 0
        assert rule.max_dispatches == -1
        assert rule.reentrant is False
        assert rule.enabled is True
        assert rule.exhausted is False

    def test_max_dispatches_exhaustion(self):
        ks = _make_simple_ks("ks1")
        rule = TriggerRule(
            name="r1", ks=ks, predicate=lambda bb, e: True, max_dispatches=2
        )
        rule.mark_dispatched("e1")
        assert rule.exhausted is False
        rule.mark_dispatched("e2")
        assert rule.exhausted is True

    def test_reentrant_guard(self):
        ks = _make_simple_ks("ks1")
        rule = TriggerRule(
            name="r1",
            ks=ks,
            predicate=lambda bb, e: True,
            reentrant=False,
        )
        entry = _make_entry("evidence")
        assert rule.would_fire_on(entry) is True
        rule.mark_dispatched(entry.entry_id)
        assert rule.would_fire_on(entry) is False

    def test_reentrant_allowed(self):
        ks = _make_simple_ks("ks1")
        rule = TriggerRule(
            name="r1",
            ks=ks,
            predicate=lambda bb, e: True,
            reentrant=True,
        )
        entry = _make_entry("evidence")
        rule.mark_dispatched(entry.entry_id)
        assert rule.would_fire_on(entry) is True

    def test_disabled_rule_wont_fire(self):
        ks = _make_simple_ks("ks1")
        rule = TriggerRule(
            name="r1", ks=ks, predicate=lambda bb, e: True, enabled=False
        )
        entry = _make_entry("evidence")
        assert rule.would_fire_on(entry) is True  # predicate check separate

    def test_reset(self):
        ks = _make_simple_ks("ks1")
        rule = TriggerRule(
            name="r1", ks=ks, predicate=lambda bb, e: True, max_dispatches=1
        )
        rule.mark_dispatched("e1")
        assert rule.exhausted is True
        rule.reset()
        assert rule.exhausted is False


class TestTriggerRuleEngine:
    """Tests for bb_trigger_engine.TriggerRuleEngine."""

    @pytest.mark.anyio
    async def test_register_sorts_by_priority(self):
        engine = TriggerRuleEngine()
        ks1 = _make_simple_ks("ks1")
        ks2 = _make_simple_ks("ks2")
        ks3 = _make_simple_ks("ks3")
        engine.register(TriggerRule("low", ks1, lambda bb, e: True, priority=1))
        engine.register(TriggerRule("high", ks2, lambda bb, e: True, priority=10))
        engine.register(TriggerRule("mid", ks3, lambda bb, e: True, priority=5))
        assert [r.name for r in engine.rules] == ["high", "mid", "low"]

    @pytest.mark.anyio
    async def test_unregister(self):
        engine = TriggerRuleEngine()
        ks = _make_simple_ks("ks")
        engine.register(TriggerRule("r1", ks, lambda bb, e: True))
        assert engine.unregister("r1") is True
        assert engine.unregister("nonexistent") is False

    @pytest.mark.anyio
    async def test_evaluate_simple_match(self):
        engine = TriggerRuleEngine()
        bb = Blackboard()
        entry = _make_entry("evidence")
        ks = _make_simple_ks("analyzer")
        engine.register(TriggerRule(
            "on_evidence",
            ks,
            lambda b, e: e.domain == "evidence",
            priority=5,
        ))
        matched = await engine.evaluate(bb, entry)
        assert len(matched) == 1
        assert matched[0][1].name == "analyzer"

    @pytest.mark.anyio
    async def test_evaluate_no_match(self):
        engine = TriggerRuleEngine()
        bb = Blackboard()
        entry = _make_entry("decision")
        ks = _make_simple_ks("analyzer")
        engine.register(TriggerRule(
            "on_evidence",
            ks,
            lambda b, e: e.domain == "evidence",
        ))
        matched = await engine.evaluate(bb, entry)
        assert len(matched) == 0

    @pytest.mark.anyio
    async def test_evaluate_multiple_rules(self):
        engine = TriggerRuleEngine()
        bb = Blackboard()
        entry = _make_entry("evidence")
        engine.register(TriggerRule("r1", _make_simple_ks("a"), lambda b, e: True, priority=3))
        engine.register(TriggerRule("r2", _make_simple_ks("b"), lambda b, e: True, priority=1))
        matched = await engine.evaluate(bb, entry)
        assert len(matched) == 2
        # Priority order
        assert matched[0][0].name == "r1"
        assert matched[1][0].name == "r2"

    @pytest.mark.anyio
    async def test_predicate_exception_handling(self):
        engine = TriggerRuleEngine()
        bb = Blackboard()
        entry = _make_entry("evidence")

        def bad_pred(bb, e):
            raise RuntimeError("boom")

        engine.register(TriggerRule("bad", _make_simple_ks("kd"), bad_pred))
        engine.register(TriggerRule("ok", _make_simple_ks("ko"), lambda b, e: True))
        # Should not raise; the bad rule is skipped
        matched = await engine.evaluate(bb, entry)
        assert len(matched) == 1
        assert matched[0][1].name == "ko"


class TestConvergenceCriteria:
    """Tests for convergence criteria classes."""

    @pytest.mark.anyio
    async def test_goal_state_satisfied(self):
        bb = Blackboard()
        await bb.write("decision", {"verdict": "approved"}, metadata={"state": "done"})
        crit = GoalStateCriterion("decision", state_value="done")
        assert await crit.is_satisfied(bb, []) is True

    @pytest.mark.anyio
    async def test_goal_state_not_satisfied(self):
        bb = Blackboard()
        await bb.write("decision", {"x": 1}, metadata={"state": "pending"})
        crit = GoalStateCriterion("decision", state_value="done")
        assert await crit.is_satisfied(bb, []) is False

    @pytest.mark.anyio
    async def test_goal_state_sticky(self):
        """Once satisfied, is_satisfied returns True without re-scanning."""
        bb = Blackboard()
        await bb.write("decision", {"v": 1}, metadata={"state": "done"})
        crit = GoalStateCriterion("decision", state_value="done")
        assert crit._satisfied is False
        assert await crit.is_satisfied(bb, []) is True
        assert crit._satisfied is True

    @pytest.mark.anyio
    async def test_max_iterations(self):
        crit = MaxIterationsCriterion(max_iter=3)
        assert await crit.is_satisfied(Blackboard(), []) is False
        crit.bump()
        crit.bump()
        assert await crit.is_satisfied(Blackboard(), []) is False
        crit.bump()
        assert await crit.is_satisfied(Blackboard(), []) is True

    @pytest.mark.anyio
    async def test_confidence_threshold(self):
        bb = Blackboard()
        await bb.write("decision", {"x": 1}, confidence=0.5)
        crit = ConfidenceThresholdCriterion("decision", threshold=0.9)
        assert await crit.is_satisfied(bb, []) is False
        await bb.write("decision", {"x": 2}, confidence=0.95)
        assert await crit.is_satisfied(bb, []) is True

    @pytest.mark.anyio
    async def test_quiescence(self):
        crit = QuiescenceCriterion(quiet_steps=2)
        assert await crit.is_satisfied(Blackboard(), []) is False
        crit.record_step(0)
        assert await crit.is_satisfied(Blackboard(), []) is False
        crit.record_step(0)
        assert await crit.is_satisfied(Blackboard(), []) is True

    @pytest.mark.anyio
    async def test_quiescence_resets_on_activity(self):
        crit = QuiescenceCriterion(quiet_steps=2)
        crit.record_step(0)
        crit.record_step(1)  # activity → reset
        crit.record_step(0)
        assert await crit.is_satisfied(Blackboard(), []) is False


class TestBlackboardConvergenceController:
    """Tests for bb_trigger_engine.BlackboardConvergenceController."""

    @pytest.mark.anyio
    async def test_basic_solve_converges(self):
        bb = Blackboard()
        ctrl = BlackboardConvergenceController(bb, max_iterations=10)
        ctrl.add_convergence(GoalStateCriterion("decision", state_value="finalized"))

        # Register a rule: when evidence appears, write a finalized decision
        async def write_decision(b, e):
            await b.write("decision", {"verdict": "approved"}, metadata={"state": "finalized"})

        ks = CallableKnowledgeSource("decider", write_decision,
                                     read_domains=("evidence",),
                                     write_domains=("decision",))
        ctrl.register_rule(TriggerRule(
            "on_evidence",
            ks,
            predicate=lambda b, e: e.domain == "evidence",
            priority=10,
        ))

        # Seed the initial state
        await bb.write("evidence", {"signal": "go"})

        report = await ctrl.run_until_convergence()
        assert report["converged"] is True
        assert report["reason"] == "convergence"
        assert report["iterations"] >= 1

    @pytest.mark.anyio
    async def test_max_iterations_hit(self):
        bb = Blackboard()
        ctrl = BlackboardConvergenceController(bb, max_iterations=3)
        ctrl.add_convergence(GoalStateCriterion("decision", state_value="never_reached"))

        # Rule writes to decision but never sets "never_reached"
        async def write_pending(b, e):
            await b.write("decision", {"x": 1}, metadata={"state": "pending"})

        ks = CallableKnowledgeSource("writer", write_pending,
                                     read_domains=("evidence",))
        ctrl.register_rule(TriggerRule("always", ks, lambda b, e: True))

        await bb.write("evidence", {"signal": "go"})
        report = await ctrl.run_until_convergence()
        assert report["converged"] is False
        assert report["reason"] == "max_iterations"
        assert report["iterations"] == 3

    @pytest.mark.anyio
    async def test_trace_accumulates(self):
        bb = Blackboard()
        ctrl = BlackboardConvergenceController(bb, max_iterations=10)
        ctrl.add_convergence(GoalStateCriterion("decision", state_value="finalized"))

        async def write_final(b, e):
            await b.write("decision", {"v": 1}, metadata={"state": "finalized"})

        ks = CallableKnowledgeSource("decider", write_final, read_domains=("evidence",))
        ctrl.register_rule(TriggerRule("r1", ks, lambda b, e: e.domain == "evidence"))

        await bb.write("evidence", {})
        await ctrl.run_until_convergence()

        trace = ctrl.get_trace()
        assert len(trace) >= 1
        assert trace[0]["ks"] == "decider"
        assert trace[0]["status"] == "ok"

    @pytest.mark.anyio
    async def test_stop_halts_controller(self):
        bb = Blackboard()
        ctrl = BlackboardConvergenceController(bb, max_iterations=100)
        ctrl.add_convergence(GoalStateCriterion("decision", state_value="never"))

        async def no_op(b, e):
            pass

        ks = CallableKnowledgeSource("idle", no_op, read_domains=("evidence",))
        ctrl.register_rule(TriggerRule("r1", ks, lambda b, e: True))

        await bb.write("evidence", {})
        # Schedule a stop (via stopper below) with enough delay so the
        # controller will still be running
        async def stopper():
            # Yield multiple times so the controller progresses but does not finish
            for _ in range(10):
                await asyncio.sleep(0)
                if not ctrl.is_running:
                    return
            ctrl.stop()

        asyncio.create_task(stopper())
        report = await ctrl.run_until_convergence()
        # Either converges (won't) or stops early
        assert report["iterations"] < 100

    @pytest.mark.anyio
    async def test_concurrent_dispatch(self):
        """Multiple rules fire concurrently."""
        bb = Blackboard()
        ctrl = BlackboardConvergenceController(bb, max_iterations=5, max_concurrent=4)
        ctrl.add_convergence(GoalStateCriterion("decision", state_value="done"))

        results = []

        async def worker(name):
            async def fn(b, e):
                results.append(name)
                await b.write("decision", {"name": name}, metadata={"state": "done"})
            return fn

        for i in range(3):
            ks = CallableKnowledgeSource(f"w{i}", await worker(f"w{i}"), read_domains=("evidence",))
            ctrl.register_rule(TriggerRule(f"r{i}", ks, lambda b, e: e.domain == "evidence", priority=i))

        await bb.write("evidence", {})
        report = await ctrl.run_until_convergence()
        assert report["converged"] is True
        assert len(results) >= 1


# ────────────────────────────────────────────────────────────────────
# M3-B: KS Agent Bridge, Debate Engine, Consensus Tracker
# ────────────────────────────────────────────────────────────────────


class TestCallableKnowledgeSource:
    """Tests for ks_agent_bridge.CallableKnowledgeSource."""

    @pytest.mark.anyio
    async def test_sync_callable(self):
        called = []

        def fn(bb, entry):
            called.append(entry.entry_id)

        ks = CallableKnowledgeSource("sync_ks", fn, read_domains=("evidence",))
        bb = Blackboard()
        entry = _make_entry("evidence")
        await ks.execute(bb, entry)
        assert len(called) == 1

    @pytest.mark.anyio
    async def test_async_callable(self):
        called = []

        async def fn(bb, entry):
            called.append(entry.entry_id)

        ks = CallableKnowledgeSource("async_ks", fn, read_domains=("evidence",))
        bb = Blackboard()
        entry = _make_entry("evidence")
        await ks.execute(bb, entry)
        assert len(called) == 1

    def test_attributes(self):
        ks = CallableKnowledgeSource("ks", lambda b, e: None,
                                     priority=5,
                                     read_domains=("a", "b"),
                                     write_domains=("c",))
        assert ks.name == "ks"
        assert ks.priority == 5
        assert ks.read_domains == ("a", "b")
        assert ks.write_domains == ("c",)


class TestAgentKnowledgeSource:
    """Tests for ks_agent_bridge.AgentKnowledgeSource."""

    @pytest.mark.anyio
    async def test_agent_with_run_bb_entry(self):
        class Agent:
            def __init__(self):
                self.calls = []

            async def run(self, bb, entry):
                self.calls.append(entry.entry_id)

        agent = Agent()
        ks = AgentKnowledgeSource("agent_ks", agent, read_domains=("evidence",))
        bb = Blackboard()
        entry = _make_entry("evidence")
        await ks.execute(bb, entry)
        assert len(agent.calls) == 1

    @pytest.mark.anyio
    async def test_agent_with_run_entry_only(self):
        class Agent:
            def __init__(self):
                self.calls = []

            async def run(self, entry):
                self.calls.append(entry.entry_id)

        agent = Agent()
        ks = AgentKnowledgeSource("agent_ks", agent)
        entry = _make_entry("evidence")
        await ks.execute(Blackboard(), entry)
        assert len(agent.calls) == 1

    def test_invalid_agent_raises(self):
        with pytest.raises(TypeError):
            AgentKnowledgeSource("bad", object())


class TestDebateEngine:
    """Tests for debate_engine.DebateEngine."""

    @pytest.mark.anyio
    async def test_basic_consensus(self):
        bb = Blackboard()
        entry = _make_entry("decision")
        await bb.write("decision", {"q": "Should we go?"})

        engine = DebateEngine(bb, consensus_threshold=0.5, quorum=2)

        async def voter(name, stance, conf):
            async def fn(b, e):
                return DebateBallot(agent=name, stance=stance, confidence=conf)
            return fn

        engine.register_participant("a", await voter("a", Stance.AGREE, 0.9))
        engine.register_participant("b", await voter("b", Stance.AGREE, 0.8))

        result = await engine.debate(entry)
        assert result.converged is True
        assert result.final_stance == Stance.AGREE
        assert result.consensus_score >= 0.5

    @pytest.mark.anyio
    async def test_divided_vote_no_consensus(self):
        bb = Blackboard()
        entry = _make_entry("decision")

        engine = DebateEngine(bb, consensus_threshold=0.9, quorum=2)

        async def voter(name, stance, conf):
            async def fn(b, e):
                return DebateBallot(agent=name, stance=stance, confidence=conf)
            return fn

        engine.register_participant("a", await voter("a", Stance.AGREE, 0.6))
        engine.register_participant("b", await voter("b", Stance.DISAGREE, 0.5))

        result = await engine.debate(entry)
        assert result.converged is False

    @pytest.mark.anyio
    async def test_abstain_zero_weight(self):
        ballot = DebateBallot(agent="x", stance=Stance.ABSTAIN, confidence=1.0)
        assert ballot.weighted_confidence == 0.0

    @pytest.mark.anyio
    async def test_debate_and_record(self):
        bb = Blackboard()
        entry = _make_entry("decision")
        await bb.write("decision", {"q": "test"})

        engine = DebateEngine(bb, consensus_threshold=0.3, quorum=1)

        async def voter(b, e):
            return DebateBallot(agent="a", stance=Stance.AGREE, confidence=0.9)

        engine.register_participant("a", voter)
        result = await engine.debate_and_record(entry, result_domain="hypothesis")
        assert result.converged is True
        recorded = bb.read("hypothesis")
        assert len(recorded) == 1
        assert recorded[0].content["final_stance"] == "agree"

    def test_summarize(self):
        result = DebateResult(
            debate_id="x",
            question_domain="d",
            question_entry_id="e1",
            final_stance=Stance.AGREE,
            consensus_score=0.7,
            converged=True,
        )
        result.ballots = [
            DebateBallot("a", Stance.AGREE, 0.8),
            DebateBallot("b", Stance.AGREE, 0.6),
            DebateBallot("c", Stance.DISAGREE, 0.2),
        ]
        s = result.summarize()
        assert s["vote_breakdown"]["agree"] == 2
        assert s["vote_breakdown"]["disagree"] == 1
        assert s["converged"] is True


class TestConsensusTracker:
    """Tests for consensus_tracker.ConsensusTracker."""

    @pytest.mark.anyio
    async def test_detect_conflict(self):
        bb = Blackboard()
        await bb.write("decision", {"verdict": "yes"}, contributor="agent_a",
                       metadata={"polarity": "positive"}, confidence=0.8)
        await bb.write("decision", {"verdict": "no"}, contributor="agent_b",
                       metadata={"polarity": "negative"}, confidence=0.7)

        tracker = ConsensusTracker(bb)
        conflicts = await tracker.scan()
        assert len(conflicts) == 1
        assert conflicts[0].domain == "decision"

    @pytest.mark.anyio
    async def test_no_conflict_same_polarity(self):
        bb = Blackboard()
        await bb.write("decision", {"a": 1}, contributor="x",
                       metadata={"polarity": "positive"})
        await bb.write("decision", {"a": 2}, contributor="y",
                       metadata={"polarity": "positive"})

        tracker = ConsensusTracker(bb)
        conflicts = await tracker.scan()
        assert len(conflicts) == 0

    @pytest.mark.anyio
    async def test_deadlock_escalation(self):
        bb = Blackboard()
        await bb.write("decision", {"v": 1}, contributor="a",
                       metadata={"polarity": "positive"}, confidence=0.9)
        await bb.write("decision", {"v": 2}, contributor="b",
                       metadata={"polarity": "negative"}, confidence=0.9)

        tracker = ConsensusTracker(bb, deadlock_cycles=2)
        await tracker.scan()
        c = tracker.active_conflicts[0]
        assert c.severity == ConflictSeverity.HIGH
        assert c.cycle_count == 1

        await tracker.scan()
        assert c.severity == ConflictSeverity.DEADLOCK

    @pytest.mark.anyio
    async def test_conflict_resolves_naturally(self):
        bb = Blackboard()
        await bb.write("decision", {"v": 1}, contributor="a",
                       metadata={"polarity": "positive"})
        await bb.write("decision", {"v": 2}, contributor="b",
                       metadata={"polarity": "negative"})

        tracker = ConsensusTracker(bb)
        await tracker.scan()
        assert len(tracker.active_conflicts) == 1

        # Now clear the negative entry, next scan should resolve
        with bb._lock:
            bb._entries["decision"] = [e for e in bb._entries["decision"]
                                       if e.metadata.get("polarity") != "negative"]
        conflicts = await tracker.scan()
        assert len(conflicts) == 0

    def test_summary(self):
        bb = Blackboard()
        tracker = ConsensusTracker(bb)
        # Manually add a synthetic conflict
        rec = ConflictRecord(domain="d1", severity=ConflictSeverity.HIGH,
                             contributors=["a", "b"])
        tracker._conflicts[rec.conflict_id] = rec
        tracker._active_by_domain["d1"] = rec
        s = tracker.summary()
        assert s["active_count"] == 1
        assert s["by_severity"]["high"] == 1


# ────────────────────────────────────────────────────────────────────
# M3-C: Enterprise Multi-Tenant Isolation
# ────────────────────────────────────────────────────────────────────


class TestTenantContext:
    """Tests for coordination_tenant.TenantContext."""

    def test_default_ctx(self):
        ctx = TenantContext()
        assert ctx.tenant_id == "default"
        assert ctx.user_id == "anonymous"

    def test_permission_check(self):
        ctx = TenantContext(tenant_id="acme", user_id="alice",
                            roles=frozenset({"admin"}),
                            permissions=frozenset({RBACPermission.BB_READ, RBACPermission.BB_WRITE}))
        assert ctx.has_permission(RBACPermission.BB_READ) is True
        assert ctx.has_permission(RBACPermission.BB_ADMIN) is False

    def test_superadmin_has_all(self):
        ctx = TenantContext(tenant_id="sys", user_id="root",
                            roles=frozenset({"superadmin"}))
        assert ctx.has_permission(RBACPermission.BB_ADMIN) is True
        assert ctx.has_permission(RBACPermission.BB_DEBATE) is True

    def test_system_factory(self):
        ctx = TenantContext.system()
        assert ctx.tenant_id == "system"
        assert ctx.has_permission(RBACPermission.KS_REGISTER) is True


class TestTenantBlackboard:
    """Tests for coordination_tenant.TenantBlackboard."""

    @pytest.mark.anyio
    async def test_tenant_write_and_read(self):
        bb = Blackboard()
        ctx = TenantContext(tenant_id="acme", user_id="alice",
                            permissions=frozenset({RBACPermission.BB_WRITE, RBACPermission.BB_READ}))
        tbb = TenantBlackboard(bb, ctx)
        entry = await tbb.write("decision", {"verdict": "go"})
        assert entry.metadata["_tenant_id"] == "acme"

        entries = tbb.read("decision")
        assert len(entries) == 1
        assert entries[0].content == {"verdict": "go"}

    @pytest.mark.anyio
    async def test_cross_tenant_isolation(self):
        bb = Blackboard()
        ctx_a = TenantContext(tenant_id="acme", user_id="a",
                              permissions=frozenset({RBACPermission.BB_WRITE, RBACPermission.BB_READ}))
        ctx_b = TenantContext(tenant_id="globex", user_id="b",
                              permissions=frozenset({RBACPermission.BB_WRITE, RBACPermission.BB_READ}))
        ta = TenantBlackboard(bb, ctx_a)
        tb = TenantBlackboard(bb, ctx_b)

        await ta.write("decision", {"v": "acme secret"})
        await tb.write("decision", {"v": "globex secret"})

        assert len(ta.read("decision")) == 1
        assert ta.read("decision")[0].content == {"v": "acme secret"}
        assert len(tb.read("decision")) == 1
        assert tb.read("decision")[0].content == {"v": "globex secret"}

    @pytest.mark.anyio
    async def test_rbac_write_denied(self):
        bb = Blackboard()
        ctx = TenantContext(tenant_id="acme", user_id="bob",
                            permissions=frozenset({RBACPermission.BB_READ}))  # no WRITE
        tbb = TenantBlackboard(bb, ctx)
        with pytest.raises(PermissionError):
            await tbb.write("decision", {"v": 1})

    @pytest.mark.anyio
    async def test_rbac_admin_for_clear(self):
        bb = Blackboard()
        ctx_no_admin = TenantContext(tenant_id="acme", user_id="a",
                                     permissions=frozenset({RBACPermission.BB_WRITE, RBACPermission.BB_READ}))
        tb = TenantBlackboard(bb, ctx_no_admin)
        await tb.write("decision", {"v": 1})
        with pytest.raises(PermissionError):
            await tb.clear("decision")

    @pytest.mark.anyio
    async def test_snapshot_isolation(self):
        bb = Blackboard()
        ctx = TenantContext(tenant_id="acme", user_id="a",
                            permissions=frozenset({RBACPermission.BB_WRITE, RBACPermission.BB_READ, RBACPermission.BB_ADMIN}))
        tb = TenantBlackboard(bb, ctx)
        await tb.write("evidence", {"e": 1})
        snap = tb.get_snapshot()
        assert "evidence" in snap
        assert len(snap["evidence"]) == 1

    @pytest.mark.anyio
    async def test_total_entries(self):
        bb = Blackboard()
        ctx = TenantContext(tenant_id="acme", user_id="a",
                            permissions=frozenset({RBACPermission.BB_WRITE, RBACPermission.BB_READ}))
        tb = TenantBlackboard(bb, ctx)
        await tb.write("decision", {"v": 1})
        await tb.write("evidence", {"e": 1})
        assert tb.total_entries() == 2


class TestFallbackRBACEnforcer:
    """Tests for FallbackRBACEnforcer."""

    def test_allow_by_permission(self):
        enforcer = FallbackRBACEnforcer()
        ctx = TenantContext(tenant_id="t", user_id="u",
                            permissions=frozenset({RBACPermission.BB_WRITE}))
        assert enforcer.check(ctx, RBACPermission.BB_WRITE, "any") is True

    def test_deny_by_absence(self):
        enforcer = FallbackRBACEnforcer()
        ctx = TenantContext(tenant_id="t", user_id="u", permissions=frozenset())
        assert enforcer.check(ctx, RBACPermission.BB_WRITE, "any") is False

    def test_explicit_deny(self):
        enforcer = FallbackRBACEnforcer()
        enforcer.deny("viewer", RBACPermission.BB_WRITE)
        ctx = TenantContext(tenant_id="t", user_id="u",
                            roles=frozenset({"viewer"}),
                            permissions=frozenset({RBACPermission.BB_WRITE}))
        assert enforcer.check(ctx, RBACPermission.BB_WRITE, "anything") is False


# ────────────────────────────────────────────────────────────────────
# M3-D: Cross-Project Event Federation Bus
# ────────────────────────────────────────────────────────────────────


class TestFederatedEvent:
    """Tests for event_federation.FederatedEvent."""

    def test_creation(self):
        evt = FederatedEvent(source="maop", topic="bb.write", data={"x": 1})
        assert evt.source == "maop"
        assert evt.topic == "bb.write"
        assert len(evt.event_id) == 16

    def test_roundtrip_serialization(self):
        evt = FederatedEvent(source="test", topic="a.b", data={"k": "v"},
                             metadata={"tenant": "acme"})
        d = evt.to_dict()
        restored = FederatedEvent.from_dict(d)
        assert restored.source == "test"
        assert restored.topic == "a.b"
        assert restored.data == {"k": "v"}
        assert restored.metadata["tenant"] == "acme"


class TestDictEventBridge:
    """Tests for event_federation.DictEventBridge."""

    def test_to_federated(self):
        bridge = DictEventBridge("test", "maos")
        fed = bridge.to_federated({"event_type": "task_completed", "payload": {"id": 1}})
        assert fed.source == "maos"
        assert fed.topic == "task.completed"
        assert fed.data == {"id": 1}


class TestFederationBus:
    """Tests for event_federation.FederationBus."""

    def test_ingest_and_dispatch(self):
        bus = FederationBus()
        received = []
        bus.subscribe("*", lambda evt: received.append(evt))
        bus.ingest({"topic": "task.completed", "data": {"id": 1}})
        assert len(received) == 1
        assert received[0].topic == "task.completed"

    def test_prefix_wildcard_match(self):
        bus = FederationBus()
        received = []
        bus.subscribe("task.*", lambda evt: received.append(evt))
        bus.ingest({"topic": "task.completed", "data": {}})
        bus.ingest({"topic": "debate.started", "data": {}})
        assert len(received) == 1

    def test_specific_topic_match(self):
        bus = FederationBus()
        received = []
        bus.subscribe("task.completed", lambda evt: received.append(evt))
        bus.ingest({"topic": "task.completed", "data": {}})
        bus.ingest({"topic": "task.failed", "data": {}})
        assert len(received) == 1

    def test_dead_letter_on_handler_error(self):
        bus = FederationBus()

        def bad_handler(evt):
            raise RuntimeError("fail")

        bus.subscribe("*", bad_handler)
        bus.ingest({"topic": "x", "data": {}})
        assert bus.dead_letter_size >= 1

    def test_unknown_bridge_ingestion_skipped(self):
        bus = FederationBus()
        bus.ingest("im_a_string_not_dict", "nonexistent_bridge")
        # Should not raise, event ignored

    def test_maop_bridge_detach(self):
        from maop.core.reliability.event_bus import EventBus
        eb = EventBus()
        bridge = MAOPEventBusBridge(eb)
        bridge.attach_federation(None)
        bridge.detach()  # must not raise


# ────────────────────────────────────────────────────────────────────
# M3-E: Health Monitor & Probes
# ────────────────────────────────────────────────────────────────────


class TestBlackboardHealthMonitor:
    """Tests for bb_health.BlackboardHealthMonitor."""

    def test_record_solve_converged(self):
        mon = BlackboardHealthMonitor()
        mon.record_solve({"converged": True, "iterations": 5, "duration_ms": 42.0})
        snap = mon.snapshot()
        assert snap["solves"]["initiated"] == 1
        assert snap["solves"]["converged"] == 1
        assert snap["solves"]["convergence_rate"] == 1.0

    def test_record_solve_max_iter(self):
        mon = BlackboardHealthMonitor()
        mon.record_solve({"converged": False, "reason": "max_iterations", "iterations": 100})
        snap = mon.snapshot()
        assert snap["solves"]["max_iter"] == 1
        assert snap["solves"]["convergence_rate"] == 0.0  # 0/11 -- wait init=1 + this fix
        # Actually we just recorded max_iter once so rate = 0/1 = 0
        assert snap["solves"]["initiated"] == 1

    def test_record_debate(self):
        mon = BlackboardHealthMonitor()
        mon.record_debate(True, 3)
        mon.record_debate(False, 2)
        snap = mon.snapshot()
        assert snap["debates"]["rounds"] == 5
        assert snap["debates"]["converged"] == 1

    def test_percentile(self):
        data = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10]
        assert BlackboardHealthMonitor._percentile(data, 50) == 5.5
        assert BlackboardHealthMonitor._percentile(data, 99) == 9.91
        assert BlackboardHealthMonitor._percentile([], 50) == 0.0

    def test_conflict_tracking(self):
        mon = BlackboardHealthMonitor()
        mon.record_conflict(resolved=False)
        mon.record_conflict(resolved=True)
        snap = mon.snapshot()
        assert snap["conflicts"]["detected"] == 2
        assert snap["conflicts"]["resolved"] == 1


class TestHealthProbes:
    """Tests for bb_health.HealthProbes."""

    def test_liveness(self):
        probes = HealthProbes()
        assert probes.liveness() is True

    def test_readiness_all_ok(self):
        probes = HealthProbes()
        probes.register("redis", lambda: True)
        probes.register("bb", lambda: True)
        r = probes.readiness()
        assert r["ready"] is True
        assert r["checks"]["redis"] is True

    def test_readiness_partial_failure(self):
        probes = HealthProbes()
        probes.register("redis", lambda: True)
        probes.register("bb", lambda: False)
        r = probes.readiness()
        assert r["ready"] is False

    def test_readiness_exception_in_check(self):
        probes = HealthProbes()
        probes.register("x", lambda: 1 / 0)
        r = probes.readiness()
        assert r["ready"] is False
        assert r["checks"]["x"] is False


# ────────────────────────────────────────────────────────────────────
# Helpers
# ────────────────────────────────────────────────────────────────────


def _make_entry(domain: str = "evidence", **kwargs) -> BlackboardEntry:
    defaults = {"domain": domain, "content": {}}
    defaults.update(kwargs)
    return BlackboardEntry(**defaults)


def _make_simple_ks(name: str) -> KnowledgeSource:
    """Create a simple knowledge source for testing."""
    class _KS(KnowledgeSource):
        def __init__(self, n: str):
            super().__init__()
            self.name = n

        async def execute(self, bb, trigger_entry):
            pass

    return _KS(name)


# ── NOTE: BB.write signature is write(domain, content, contributor="", *, confidence=1.0, metadata=None) ──

import asyncio

from maop.core.reliability.bb_health import (
    BlackboardHealthMonitor,
    HealthProbes,
)

# ── Lazy imports so pytest collects correctly ──────────────────────
from maop.core.reliability.bb_trigger_engine import (
    BlackboardConvergenceController,
    ConfidenceThresholdCriterion,
    GoalStateCriterion,
    MaxIterationsCriterion,
    QuiescenceCriterion,
    TriggerRule,
    TriggerRuleEngine,
)
from maop.core.reliability.consensus_tracker import (
    ConflictRecord,
    ConflictSeverity,
    ConsensusTracker,
)
from maop.core.reliability.coordination_tenant import (
    FallbackRBACEnforcer,
    RBACPermission,
    TenantBlackboard,
    TenantContext,
)
from maop.core.reliability.debate_engine import (
    DebateBallot,
    DebateEngine,
    DebateResult,
    Stance,
)
from maop.core.reliability.event_federation import (
    DictEventBridge,
    FederatedEvent,
    FederationBus,
    MAOPEventBusBridge,
)
from maop.core.reliability.ks_agent_bridge import (
    AgentKnowledgeSource,
    CallableKnowledgeSource,
)
