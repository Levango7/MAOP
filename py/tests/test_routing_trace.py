"""Tests for Phase γ-4 — Scheduling decision full-chain trace + explanation API.

Covers seven areas:

1. **RoutingDecisionRecord** — data-model construction + ``to_dict``.
2. **RoutingDecisionStore** — SQLite persistence (record / query_by_trace /
   query_recent / count / stats / prune).
3. **RouteScorer trace** — OTel span creation + attribute payload +
   decision-record persistence.
4. **LoadBalancer trace** — span + attributes + decision record (incl.
   sticky-session hit/miss).
5. **ModelSelector trace** — span + attributes + decision record.
6. **Dispatcher trace** — parent span ``routing.dispatcher.dispatch`` +
   decision record.
7. **Dashboard API** — ``/api/routing/decisions/recent`` / ``/{trace_id}`` /
   ``/stats`` via FastAPI TestClient.
8. **Metrics** — the two new metrics in :mod:`maop.core.monitoring`.

The span tests patch :func:`maop.core.otel.span` with a recording
context manager so we can assert call arguments without spinning up a
real OTel provider (mirrors the approach in ``test_mcp_observability.py``).
"""
from __future__ import annotations

import json
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from maop.core import otel as otel_module
from maop.core.monitoring.monitoring import (
    MAOP_ROUTING_DECISION_DURATION_MS,
    MAOP_ROUTING_DECISION_TOTAL,
    metrics,
)
from maop.core.routing.routing_decision import (
    RoutingDecisionRecord,
    RoutingDecisionStore,
    get_active_span_context,
)

# ─────────────────────────────────────────────────────────────────
# Shared span-recording helpers (mirrors test_mcp_observability.py)
# ─────────────────────────────────────────────────────────────────

_span_calls: list[dict[str, Any]] = []


@contextmanager
def _recording_span(tracer: Any, name: str, *, kind: Any = None,
                    attributes: dict[str, Any] | None = None,
                    trace_id: str = "") -> Any:
    """Recording stand-in for :func:`maop.core.otel.span`.

    Captures ``(name, attributes, trace_id)`` for each opened span and
    yields a MagicMock so callers can call ``set_attribute`` on it.
    """
    _span_calls.append({
        "tracer": tracer,
        "name": name,
        "kind": kind,
        "attributes": dict(attributes or {}),
        "trace_id": trace_id,
    })
    mock_span = MagicMock()
    yield mock_span


@pytest.fixture
def recording_spans():
    """Patch ``otel.span`` with ``_recording_span`` for the duration of
    the test, returning the shared call list (cleared first).

    Each subsystem module (route_scorer / load_balancer / selector /
    dispatcher) does ``from maop.core.monitoring.otel import span as otel_span`` at
    import time, binding the original function into its own namespace.
    Patching only ``maop.core.otel.span`` wouldn't affect those bound
    references — so we patch the ``otel_span`` attribute on each
    subsystem module directly. This makes the fixture robust to test
    ordering (earlier tests may have already imported the subsystem
    modules, freezing the original binding).
    """
    _span_calls.clear()
    import maop.core.routing.load_balancer as _lb
    import maop.core.routing.route_scorer as _rs
    import maop.delegate.dispatch_core as _dcore
    import maop.delegate.dispatcher as _disp
    import maop.model.selector as _sel
    with patch.object(otel_module, "span", _recording_span), \
         patch.object(_rs, "otel_span", _recording_span), \
         patch.object(_lb, "otel_span", _recording_span), \
         patch.object(_sel, "otel_span", _recording_span), \
         patch.object(_disp, "otel_span", _recording_span), \
         patch.object(_dcore, "otel_span", _recording_span):
        yield _span_calls


@pytest.fixture
def isolated_store(tmp_path: Path) -> RoutingDecisionStore:
    """Fresh :class:`RoutingDecisionStore` backed by a temp DB file.

    Also patches the module-level singleton used by
    :func:`record_decision_safe` so the subsystem tests write to the
    same isolated store.
    """
    store = RoutingDecisionStore(db_path=tmp_path / "routing_decisions.db")
    with patch(
        "maop.core.routing.routing_decision.get_routing_decision_store",
        return_value=store,
    ):
        yield store


def _make_record(
    *,
    trace_id: str = "trace-1",
    span_id: str = "span-1",
    parent_span_id: str | None = None,
    stage: str = "route_scorer",
    timestamp: float | None = None,
    input_summary: dict[str, Any] | None = None,
    output_summary: dict[str, Any] | None = None,
    explanation: str = "test decision",
    duration_ms: float = 1.5,
    attributes: dict[str, Any] | None = None,
) -> RoutingDecisionRecord:
    return RoutingDecisionRecord(
        trace_id=trace_id,
        span_id=span_id,
        parent_span_id=parent_span_id,
        timestamp=timestamp if timestamp is not None else time.time(),
        stage=stage,
        input_summary=input_summary or {"routing_key": "codegen"},
        output_summary=output_summary or {"selected_agent": "claude"},
        explanation=explanation,
        duration_ms=duration_ms,
        attributes=attributes or {"decision_mode": "weighted_sum"},
    )


# ═════════════════════════════════════════════════════════════════
# 1. RoutingDecisionRecord
# ═════════════════════════════════════════════════════════════════


class TestRoutingDecisionRecord:
    def test_default_construction(self):
        r = _make_record()
        assert r.trace_id == "trace-1"
        assert r.span_id == "span-1"
        assert r.parent_span_id is None
        assert r.stage == "route_scorer"
        assert r.duration_ms == 1.5
        assert r.input_summary == {"routing_key": "codegen"}
        assert r.output_summary == {"selected_agent": "claude"}
        assert r.attributes == {"decision_mode": "weighted_sum"}

    def test_to_dict_round_trip(self):
        r = _make_record(
            parent_span_id="parent-1",
            input_summary={"a": 1, "b": "two"},
            output_summary={"selected_model": "gpt-4"},
            attributes={"k": True},
        )
        d = r.to_dict()
        assert d["trace_id"] == "trace-1"
        assert d["parent_span_id"] == "parent-1"
        assert d["input_summary"] == {"a": 1, "b": "two"}
        assert d["output_summary"] == {"selected_model": "gpt-4"}
        assert d["attributes"] == {"k": True}
        # Must be JSON-serialisable (API response).
        json.dumps(d)

    def test_to_dict_is_safe_for_empty_fields(self):
        r = RoutingDecisionRecord(
            trace_id="t", span_id="", parent_span_id=None,
            timestamp=0.0, stage="",
        )
        d = r.to_dict()
        assert d["input_summary"] == {}
        assert d["output_summary"] == {}
        assert d["attributes"] == {}
        assert d["explanation"] == ""
        json.dumps(d)


# ═════════════════════════════════════════════════════════════════
# 2. RoutingDecisionStore
# ═════════════════════════════════════════════════════════════════


class TestRoutingDecisionStore:
    def test_record_returns_row_id(self, isolated_store: RoutingDecisionStore):
        rid = isolated_store.record(_make_record())
        assert isinstance(rid, int)
        assert rid > 0

    def test_query_by_trace_returns_oldest_first(
        self, isolated_store: RoutingDecisionStore,
    ):
        base = time.time()
        isolated_store.record(_make_record(
            trace_id="t1", stage="dispatcher", timestamp=base,
        ))
        isolated_store.record(_make_record(
            trace_id="t1", stage="route_scorer", timestamp=base + 0.5,
        ))
        isolated_store.record(_make_record(
            trace_id="t1", stage="model_selector", timestamp=base + 1.0,
        ))
        # Different trace should not leak.
        isolated_store.record(_make_record(
            trace_id="t2", stage="dispatcher", timestamp=base,
        ))

        chain = isolated_store.query_by_trace("t1")
        assert [r.stage for r in chain] == [
            "dispatcher", "route_scorer", "model_selector",
        ]
        # Different trace should not leak into t1's chain; it has its
        # own single decision.
        t2_chain = isolated_store.query_by_trace("t2")
        assert len(t2_chain) == 1
        assert t2_chain[0].stage == "dispatcher"
        assert isolated_store.query_by_trace("") == []

    def test_query_recent_newest_first(
        self, isolated_store: RoutingDecisionStore,
    ):
        base = time.time()
        isolated_store.record(_make_record(
            trace_id="t1", stage="route_scorer", timestamp=base,
        ))
        isolated_store.record(_make_record(
            trace_id="t2", stage="load_balancer", timestamp=base + 1.0,
        ))
        isolated_store.record(_make_record(
            trace_id="t3", stage="route_scorer", timestamp=base + 2.0,
        ))

        all_recent = isolated_store.query_recent(limit=10)
        assert [r.trace_id for r in all_recent] == ["t3", "t2", "t1"]

        filtered = isolated_store.query_recent(limit=10, stage="route_scorer")
        assert [r.trace_id for r in filtered] == ["t3", "t1"]

    def test_count_with_and_without_stage_filter(
        self, isolated_store: RoutingDecisionStore,
    ):
        isolated_store.record(_make_record(stage="route_scorer"))
        isolated_store.record(_make_record(stage="route_scorer"))
        isolated_store.record(_make_record(stage="dispatcher"))

        assert isolated_store.count() == 3
        assert isolated_store.count(stage="route_scorer") == 2
        assert isolated_store.count(stage="dispatcher") == 1
        assert isolated_store.count(stage="missing") == 0

    def test_stats_aggregates(self, isolated_store: RoutingDecisionStore):
        now = time.time()
        isolated_store.record(_make_record(
            stage="dispatcher", timestamp=now,
        ))
        isolated_store.record(_make_record(
            stage="route_scorer", timestamp=now,
        ))
        isolated_store.record(_make_record(
            stage="route_scorer", timestamp=now - 86_400 * 2,  # 2 days ago
        ))

        stats = isolated_store.stats()
        assert stats["total"] == 3
        assert stats["by_stage"]["route_scorer"] == 2
        assert stats["by_stage"]["dispatcher"] == 1
        # last_24h should only count the 2 recent ones.
        assert stats["last_24h"] == 2

    def test_prune_removes_old_records(
        self, isolated_store: RoutingDecisionStore,
    ):
        now = time.time()
        isolated_store.record(_make_record(
            trace_id="old", timestamp=now - 86_400 * 30,
        ))
        isolated_store.record(_make_record(
            trace_id="new", timestamp=now,
        ))
        removed = isolated_store.prune(older_than_days=7)
        assert removed == 1
        assert isolated_store.count() == 1
        assert isolated_store.query_by_trace("old") == []
        assert isolated_store.query_by_trace("new") != []

    def test_record_preserves_json_fields(
        self, isolated_store: RoutingDecisionStore,
    ):
        rec = _make_record(
            input_summary={"routing_key": "codegen", "candidate_count": 3},
            output_summary={"selected_agent": "claude", "score": 0.85},
            attributes={"decision_mode": "weighted_sum", "matched_by": "regex"},
            explanation="Selected agent 'claude' with confidence 0.85 (high).",
        )
        isolated_store.record(rec)
        results = isolated_store.query_by_trace(rec.trace_id)
        assert len(results) == 1
        out = results[0]
        assert out.input_summary == rec.input_summary
        assert out.output_summary == rec.output_summary
        assert out.attributes == rec.attributes
        assert out.explanation == rec.explanation
        assert out.duration_ms == rec.duration_ms


# ═════════════════════════════════════════════════════════════════
# 3. RouteScorer trace
# ═════════════════════════════════════════════════════════════════


class TestRouteScorerTrace:
    def test_match_emits_span_and_records_decision(
        self,
        recording_spans: list[dict[str, Any]],
        isolated_store: RoutingDecisionStore,
    ):
        from maop.config.loader import AgentDef, MaopConfig, RouteEntry
        from maop.core.routing.route_scorer import RouteScorer

        config = MaopConfig(
            agents={
                "claude": AgentDef(
                    cli="claude", driver="cli",
                    capabilities=["codegen", "chat"],
                ),
            },
            routing={
                "codegen": RouteEntry(
                    primary="claude",
                    match=r"write|implement",
                    keywords=["code", "function"],
                ),
            },
        )
        scorer = RouteScorer(config=config)
        result = scorer.match("write a function", trace_id="trace-rs-1")

        assert result is not None
        assert result.agent == "claude"
        assert result.routing_key == "codegen"

        # Span assertions.
        match_spans = [s for s in recording_spans
                       if s["name"] == "routing.route_scorer.match"]
        assert len(match_spans) == 1
        attrs = match_spans[0]["attributes"]
        assert attrs["routing.decision_mode"] == "weighted_sum"
        assert match_spans[0]["trace_id"] == "trace-rs-1"

        # Decision record assertions.
        chain = isolated_store.query_by_trace("trace-rs-1")
        assert len(chain) == 1
        assert chain[0].stage == "route_scorer"
        assert chain[0].output_summary["selected_agent"] == "claude"
        assert chain[0].output_summary["routing_key"] == "codegen"
        assert "claude" in chain[0].explanation
        assert "weighted_sum" in chain[0].explanation
        assert chain[0].input_summary["candidate_count"] >= 1

    def test_match_no_candidates_still_records(
        self,
        recording_spans: list[dict[str, Any]],
        isolated_store: RoutingDecisionStore,
    ):
        from maop.config.loader import AgentDef, MaopConfig, RouteEntry
        from maop.core.routing.route_scorer import RouteScorer

        config = MaopConfig(
            agents={
                "claude": AgentDef(cli="claude", driver="cli"),
            },
            routing={
                "codegen": RouteEntry(
                    primary="claude",
                    match=r"write",
                    keywords=["code"],
                ),
            },
        )
        scorer = RouteScorer(config=config)
        result = scorer.match("totally unrelated text", trace_id="trace-rs-2")
        assert result is None

        chain = isolated_store.query_by_trace("trace-rs-2")
        # No-candidates path still records a decision for observability.
        assert len(chain) == 1
        assert chain[0].stage == "route_scorer"
        assert chain[0].input_summary["candidate_count"] == 0
        assert "No route matched" in chain[0].explanation


# ═════════════════════════════════════════════════════════════════
# 4. LoadBalancer trace
# ═════════════════════════════════════════════════════════════════


class TestLoadBalancerTrace:
    def test_select_emits_span_and_records_decision(
        self,
        recording_spans: list[dict[str, Any]],
        isolated_store: RoutingDecisionStore,
    ):
        from maop.core.routing.load_balancer import LBAlgorithm, LoadBalancer

        lb = LoadBalancer(algorithm=LBAlgorithm.ADAPTIVE)
        lb.register("claude", weight=10)
        lb.register("kimi", weight=5)

        selected = lb.select(routing_key="codegen", trace_id="trace-lb-1")
        assert selected in {"claude", "kimi"}

        lb_spans = [s for s in recording_spans
                    if s["name"] == "routing.load_balancer.select"]
        assert len(lb_spans) == 1
        attrs = lb_spans[0]["attributes"]
        assert attrs["routing.algorithm"] == "adaptive"
        assert lb_spans[0]["trace_id"] == "trace-lb-1"

        chain = isolated_store.query_by_trace("trace-lb-1")
        assert len(chain) == 1
        assert chain[0].stage == "load_balancer"
        assert chain[0].output_summary["selected_agent"] == selected
        assert "adaptive" in chain[0].explanation

    def test_select_no_pool_records_decision(
        self,
        recording_spans: list[dict[str, Any]],
        isolated_store: RoutingDecisionStore,
    ):
        from maop.core.routing.load_balancer import LoadBalancer

        lb = LoadBalancer()
        result = lb.select(trace_id="trace-lb-empty")
        assert result is None

        chain = isolated_store.query_by_trace("trace-lb-empty")
        assert len(chain) == 1
        assert chain[0].output_summary == {"selected_agent": None}
        assert "No agent selected" in chain[0].explanation

    def test_select_sticky_session_hit_records_decision(
        self,
        recording_spans: list[dict[str, Any]],
        isolated_store: RoutingDecisionStore,
    ):
        from maop.core.routing.load_balancer import LoadBalancer

        lb = LoadBalancer(sticky_sessions=True, sticky_session_ttl_s=60.0)
        lb.register("claude", weight=10)

        # First call: miss, then records sticky.
        first = lb.select(session_id="sess-1", trace_id="trace-lb-sticky")
        assert first == "claude"
        # Second call: should be a sticky hit.
        second = lb.select(session_id="sess-1", trace_id="trace-lb-sticky")
        assert second == "claude"

        chain = isolated_store.query_by_trace("trace-lb-sticky")
        # Two decisions: one miss + one hit.
        assert len(chain) == 2
        hit_decision = chain[1]
        assert hit_decision.input_summary["sticky_session_hit"] is True
        assert "sticky session hit" in hit_decision.explanation


# ═════════════════════════════════════════════════════════════════
# 5. ModelSelector trace
# ═════════════════════════════════════════════════════════════════


class TestModelSelectorTrace:
    def test_select_emits_span_and_records_decision(
        self,
        recording_spans: list[dict[str, Any]],
        isolated_store: RoutingDecisionStore,
    ):
        from unittest.mock import MagicMock

        from maop.model.schema import (
            LatencyTier,
            ModelDef,
            QualityTier,
        )
        from maop.model.selector import ModelSelector

        reg = MagicMock()
        models = {
            "gpt-4": ModelDef(
                name="gpt-4", provider="openai", capabilities=["chat"],
                quality_tier=QualityTier.EXCELLENT, latency_tier=LatencyTier.SLOW,
                cost_per_1k_input=0.03, cost_per_1k_output=0.06, enabled=True,
            ),
            "built-in": ModelDef(
                name="built-in", provider="local", capabilities=["chat"],
                quality_tier=QualityTier.POOR, latency_tier=LatencyTier.FAST,
                cost_per_1k_input=0.0, cost_per_1k_output=0.0, enabled=True,
            ),
        }
        reg.get_model = lambda n: models.get(n)
        reg.get_policy = lambda n="default": None
        reg.resolve_agent_model = lambda agent_model, model_ref="": None
        reg.best_model = lambda cap, strategy="best_quality", max_cost=None: models.get("gpt-4")
        reg.models_by_capability = lambda cap: [m for m in models.values() if cap in m.capabilities]
        reg.providers = MagicMock()
        reg.providers.is_healthy = lambda name: True

        selector = ModelSelector(reg)
        effective = selector.select(
            capability="chat", policy_name="default",
            trace_id="trace-ms-1",
        )

        assert effective.model_name == "gpt-4"
        assert effective.provider == "openai"

        ms_spans = [s for s in recording_spans
                    if s["name"] == "routing.model_selector.select"]
        assert len(ms_spans) == 1
        attrs = ms_spans[0]["attributes"]
        assert attrs["routing.capability"] == "chat"
        assert ms_spans[0]["trace_id"] == "trace-ms-1"

        chain = isolated_store.query_by_trace("trace-ms-1")
        assert len(chain) == 1
        assert chain[0].stage == "model_selector"
        assert chain[0].output_summary["selected_model"] == "gpt-4"
        assert chain[0].output_summary["selected_provider"] == "openai"
        assert "gpt-4" in chain[0].explanation


# ═════════════════════════════════════════════════════════════════
# 6. Dispatcher trace
# ═════════════════════════════════════════════════════════════════


class TestDispatcherTrace:
    def test_dispatch_emits_parent_span_and_records_decision(
        self,
        recording_spans: list[dict[str, Any]],
        isolated_store: RoutingDecisionStore,
    ):
        import asyncio

        from maop.delegate.dispatcher import Dispatcher

        dispatcher = Dispatcher()
        # Dispatch to a non-existent agent — the dispatch will fail to
        # resolve but the outer routing span + decision record must
        # still be emitted.
        result = asyncio.run(dispatcher.dispatch(
            agent="nonexistent", task="test",
            routing_key="codegen", trace_id="trace-disp-1",
            priority=2,
        ))

        # The dispatch itself failed (agent not found).
        assert result.result is not None
        assert not result.result.is_success()

        # Outer parent span must be present.
        routing_spans = [s for s in recording_spans
                         if s["name"] == "routing.dispatcher.dispatch"]
        assert len(routing_spans) == 1
        attrs = routing_spans[0]["attributes"]
        assert attrs["routing.agent"] == "nonexistent"
        assert attrs["sla.priority"] == 2
        assert attrs["sla.tier"] == "standard"
        assert routing_spans[0]["trace_id"] == "trace-disp-1"

        # Inner dispatch.{agent} span must also be present (child).
        inner_spans = [s for s in recording_spans
                       if s["name"] == "dispatch.nonexistent"]
        assert len(inner_spans) == 1

        chain = isolated_store.query_by_trace("trace-disp-1")
        assert len(chain) == 1
        assert chain[0].stage == "dispatcher"
        assert chain[0].output_summary["selected_agent"] == "nonexistent"
        assert "priority=2" in chain[0].explanation
        assert "standard" in chain[0].explanation


# ═════════════════════════════════════════════════════════════════
# 7. Dashboard API
# ═════════════════════════════════════════════════════════════════


@pytest.fixture
def api_client(tmp_path: Path):
    """Build a FastAPI TestClient with the routing router mounted and
    the store singleton patched to a temp DB."""
    from maop.dashboard.routers import routing as routing_router_module

    store = RoutingDecisionStore(db_path=tmp_path / "routing_decisions.db")
    # Patch the lazy singleton used by the router's ``_get_store``.
    routing_router_module._decision_store = store

    app = FastAPI()
    app.include_router(routing_router_module.router)
    client = TestClient(app)
    yield client, store
    routing_router_module._decision_store = None


class TestRoutingDecisionsAPI:
    def test_recent_endpoint_returns_decisions(self, api_client):
        client, store = api_client
        store.record(_make_record(
            trace_id="api-1", stage="route_scorer",
        ))
        store.record(_make_record(
            trace_id="api-2", stage="dispatcher",
        ))

        resp = client.get("/api/routing/decisions/recent?limit=10")
        assert resp.status_code == 200
        body = resp.json()
        assert body["count"] == 2
        assert body["total"] == 2
        assert len(body["decisions"]) == 2
        # Newest-first: api-2 was inserted last.
        assert body["decisions"][0]["trace_id"] == "api-2"

    def test_recent_endpoint_filters_by_stage(self, api_client):
        client, store = api_client
        store.record(_make_record(
            trace_id="api-1", stage="route_scorer",
        ))
        store.record(_make_record(
            trace_id="api-2", stage="dispatcher",
        ))

        resp = client.get(
            "/api/routing/decisions/recent?stage=dispatcher"
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["count"] == 1
        assert body["decisions"][0]["stage"] == "dispatcher"
        assert body["stage"] == "dispatcher"

    def test_by_trace_endpoint_returns_chain(self, api_client):
        client, store = api_client
        base = time.time()
        store.record(_make_record(
            trace_id="chain-1", stage="dispatcher", timestamp=base,
        ))
        store.record(_make_record(
            trace_id="chain-1", stage="route_scorer", timestamp=base + 0.5,
        ))
        store.record(_make_record(
            trace_id="chain-1", stage="model_selector", timestamp=base + 1.0,
        ))

        resp = client.get("/api/routing/decisions/chain-1")
        assert resp.status_code == 200
        body = resp.json()
        assert body["trace_id"] == "chain-1"
        assert body["count"] == 3
        # Oldest-first (call order).
        assert body["stages"] == [
            "dispatcher", "route_scorer", "model_selector",
        ]

    def test_by_trace_endpoint_returns_empty_for_unknown(self, api_client):
        client, _ = api_client
        resp = client.get("/api/routing/decisions/unknown-trace")
        assert resp.status_code == 200
        body = resp.json()
        assert body["count"] == 0
        assert body["decisions"] == []
        assert body["stages"] == []

    def test_stats_endpoint_returns_aggregates(self, api_client):
        client, store = api_client
        now = time.time()
        store.record(_make_record(
            stage="dispatcher", timestamp=now,
        ))
        store.record(_make_record(
            stage="route_scorer", timestamp=now,
        ))
        store.record(_make_record(
            stage="route_scorer", timestamp=now - 86_400 * 2,
        ))

        resp = client.get("/api/routing/decisions/stats")
        assert resp.status_code == 200
        body = resp.json()
        assert body["total"] == 3
        assert body["by_stage"]["route_scorer"] == 2
        assert body["by_stage"]["dispatcher"] == 1
        assert body["last_24h"] == 2


# ═════════════════════════════════════════════════════════════════
# 8. Metrics
# ═════════════════════════════════════════════════════════════════


class TestRoutingDecisionMetrics:
    def test_both_metrics_registered(self):
        # Verify the two new metrics exist in the global registry.
        assert "MAOP_routing_decision_total" in metrics._counters
        assert "MAOP_routing_decision_duration_ms" in metrics._histograms

    def test_counter_accepts_stage_label(self):
        before = MAOP_ROUTING_DECISION_TOTAL.get(labels={"stage": "route_scorer"})
        MAOP_ROUTING_DECISION_TOTAL.inc(labels={"stage": "route_scorer"})
        after = MAOP_ROUTING_DECISION_TOTAL.get(labels={"stage": "route_scorer"})
        assert after == before + 1.0

    def test_histogram_observes_duration(self):
        # The histogram's _total count should increase after observe.
        before = MAOP_ROUTING_DECISION_DURATION_MS._total
        MAOP_ROUTING_DECISION_DURATION_MS.observe(2.5)
        after = MAOP_ROUTING_DECISION_DURATION_MS._total
        assert after == before + 1

    def test_route_scorer_match_increments_counter(
        self, recording_spans, isolated_store: RoutingDecisionStore,
    ):
        from maop.config.loader import AgentDef, MaopConfig, RouteEntry
        from maop.core.routing.route_scorer import RouteScorer

        config = MaopConfig(
            agents={
                "claude": AgentDef(
                    cli="claude", driver="cli", capabilities=["codegen"],
                ),
            },
            routing={
                "codegen": RouteEntry(
                    primary="claude", match=r"write",
                ),
            },
        )
        scorer = RouteScorer(config=config)

        before = MAOP_ROUTING_DECISION_TOTAL.get(labels={"stage": "route_scorer"})
        scorer.match("write a function", trace_id="metrics-test")
        after = MAOP_ROUTING_DECISION_TOTAL.get(labels={"stage": "route_scorer"})
        assert after == before + 1.0


# ═════════════════════════════════════════════════════════════════
# 9. get_active_span_context helper
# ═════════════════════════════════════════════════════════════════


class TestGetActiveSpanContext:
    def test_returns_empty_when_no_otel(self):
        # With OTel disabled (default in tests), the helper returns
        # empty strings and None parent.
        trace_id, span_id, parent_span_id = get_active_span_context()
        assert trace_id == ""
        assert span_id == ""
        assert parent_span_id is None
