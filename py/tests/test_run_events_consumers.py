"""Non-vacuous tests for the run_events consumers converted in #55.

The point is not the shape of the response but that real ledger rows flow
through: a reader that still queried the writerless ``delegations`` table would
return zeros here.
"""
from __future__ import annotations

import asyncio
import time

from maop.core.run_events import record_run_event
from maop.history_analyzer import HistoryAnalyzer


def _seed(agent: str, *, ok: bool, error: str = "", routing_key: str = "code") -> None:
    record_run_event(
        f"trace-{agent}-{time.time_ns()}",
        "run.step",
        agent=agent,
        status="ok" if ok else "failed",
        duration_ms=120,
        payload={
            "task": "t",
            "exit_code": 0 if ok else 1,
            "routing_key": routing_key,
            "error": "" if ok else error,
        },
    )


def test_history_clusters_real_failures(tmp_path, monkeypatch):
    monkeypatch.setenv("MAOP_DATA_DIR", str(tmp_path))
    _seed("claude", ok=False, error="timeout: command exceeded 30s")
    _seed("codex", ok=False, error="timeout: command exceeded 30s")
    _seed("claude", ok=True)

    analyzer = HistoryAnalyzer(root_dir=tmp_path)
    clusters = analyzer._cluster_failures(since=time.time() - 3600)

    assert clusters, "reader returned no clusters — is it still reading the empty delegations table?"
    assert sum(c.count for c in clusters) == 2, "the successful step must not be clustered"
    assert all(c.recent_examples for c in clusters)
    assert clusters[0].recent_examples[0]["routing_key"] == "code"


def test_coordination_report_lists_agents_from_events(tmp_path, monkeypatch):
    monkeypatch.setenv("MAOP_DATA_DIR", str(tmp_path))
    _seed("claude", ok=True)
    _seed("codex", ok=False, error="boom")

    from maop.dashboard.channels.prompts import PromptsMixin

    class Host(PromptsMixin):
        _root = tmp_path
        _human_proxy = None

        async def queue_stats(self):
            return {}

        def _record_latency(self, _start):
            return None

        async def _query_maop(self, *_args, **_kwargs):
            raise AssertionError("coordination_report must not query delegations")

    report = asyncio.run(Host().coordination_report())
    assert set(report["active_agents"]) == {"claude", "codex"}
