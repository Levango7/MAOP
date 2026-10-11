"""Tests for T2.1 — dry-run real rehearsal (sentinel contract + producers).

The roadmap item: the dry-run *gate* existed since 2026-09 but nothing
*produced* the signal it verified — with MAOP_DRY_RUN_ENFORCE=1 the
deploy/pipeline/fileops routes failed closed for a different reason
than "no dry run happened": no executor ever emitted a dry-run signal,
so enforce mode was all-or-nothing broken. This test file covers the
contract, the producers, and the dispatch wiring.

Covered:
- :mod:`maop.core.reliability.dry_run` — the line-anchored sentinel
  (emit/extract/preamble). Prose that merely *mentions* dry-run can no
  longer fake the trusted signal.
- ``maop_verify._gate_dry_run`` — sentinel as primary signal, legacy
  substring kept for one minor release, artifacts verified.
- ``doc_pipeline_adapter.run_pipeline(dry_run=...)`` — real rehearsal
  via ``orch.plan()``: marker printed, no execution (zero side effects).
- ``Dispatcher.dispatch(dry_run=...)`` — per-task wiring: env channel
  (MAOP_DRY_RUN=1) + preamble injection for agents declaring
  ``dry_run_supported``; undeclared agents untouched.
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from maop.core.reliability.dry_run import (
    DRY_RUN_MARKER,
    DRY_RUN_PREAMBLE,
    emit_marker,
    extract_markers,
    has_marker,
)
from maop.core.reliability.error_schema import MaopResult
from maop.delegate.models import AgentConfig
from maop.maop_verify import _gate_dry_run

# ── Sentinel contract ──────────────────────────────────────────


class TestSentinelContract:
    def test_round_trip(self):
        line = emit_marker(["docs/out.md"], agent="doc-pipeline")
        assert line.startswith(DRY_RUN_MARKER + ": ")
        markers = extract_markers(line)
        assert markers == [{"artifacts": ["docs/out.md"], "agent": "doc-pipeline"}]

    def test_prose_mention_is_not_a_signal(self):
        # The legacy fuzzy contract passed on prose like this; the
        # line-anchored one must not.
        text = "I did not run a dry-run here; dry_run is mentioned only in prose.\n"
        assert has_marker(text) is False
        assert extract_markers(text) == []

    def test_marker_must_start_the_line(self):
        # Marker embedded mid-line (e.g. inside a log prefix) is not
        # a protocol line.
        text = f"[worker] log noise {DRY_RUN_MARKER}: {{\"artifacts\": []}}\n"
        assert has_marker(text) is False

    def test_malformed_marker_skipped_not_fatal(self):
        text = f"{DRY_RUN_MARKER}: not-json\n{DRY_RUN_MARKER}: {{\"artifacts\": [\"ok\"]}}\n"
        assert extract_markers(text) == [{"artifacts": ["ok"]}]

    def test_multiple_markers_all_extracted(self):
        text = "\n".join(
            [emit_marker([f"a{i}"]) for i in range(3)],
        )
        assert len(extract_markers(text)) == 3

    def test_empty_text(self):
        assert extract_markers("") == []
        assert has_marker("") is False

    def test_preamble_names_the_marker(self):
        assert DRY_RUN_MARKER in DRY_RUN_PREAMBLE


# ── Gate behaviour ─────────────────────────────────────────────


def _result(stdout: str = "", structured: dict | None = None) -> MaopResult:
    return MaopResult(
        agent="a", task="t", stdout=stdout,
        structured_output=structured if structured is not None else {},
    )


class TestGateSentinel:
    def test_no_dry_run_declared_passes(self):
        gate = _gate_dry_run({}, _result(stdout="irrelevant"))
        assert gate.passed

    def test_marker_line_passes(self):
        plan = {"dry_run": True}
        res = _result(stdout="working...\n" + emit_marker(["x"]) + "\ndone")
        gate = _gate_dry_run(plan, res)
        assert gate.passed, gate.reason

    def test_prose_without_legacy_keywords_fails(self):
        # Trusted channel: prose without even the legacy keywords must
        # fail — only the sentinel (or a structured signal) can pass.
        plan = {"dry_run": True}
        res = _result(stdout="I ran the rehearsal carefully, all good")
        gate = _gate_dry_run(plan, res)
        assert gate.passed is False

    def test_prose_with_legacy_keyword_passes_deprecated(self):
        # DEPRECATED window: prose containing the legacy keywords still
        # passes (kept for one minor release). Documents the exact
        # weakness the sentinel contract replaces.
        plan = {"dry_run": True}
        res = _result(stdout="this was a dry-run, no changes applied")
        gate = _gate_dry_run(plan, res)
        assert gate.passed

    def test_legacy_substring_still_accepted(self):
        # DEPRECATED compat for one minor release.
        plan = {"dry_run": True}
        res = _result(stdout="DRY-RUN: nothing to do")
        gate = _gate_dry_run(plan, res)
        assert gate.passed

    def test_structured_signal_still_accepted(self):
        plan = {"dry_run": True}
        res = _result(structured={"dry_run": True})
        assert _gate_dry_run(plan, res).passed

    def test_nothing_fails(self):
        plan = {"dry_run": True}
        gate = _gate_dry_run(plan, _result(stdout="all done"))
        assert gate.passed is False
        assert DRY_RUN_MARKER in (gate.reason or "")

    def test_missing_expected_artifact_fails(self):
        plan = {"dry_run": True, "expected_dry_run_artifacts": ["docs/a.md"]}
        res = _result(stdout=emit_marker(["docs/b.md"]))
        gate = _gate_dry_run(plan, res)
        assert gate.passed is False
        assert "docs/a.md" in (gate.reason or "")

    def test_expected_artifacts_satisfied(self):
        plan = {"dry_run": True, "expected_dry_run_artifacts": ["docs/a.md"]}
        res = _result(stdout=emit_marker(["docs/b.md", "docs/a.md"]))
        assert _gate_dry_run(plan, res).passed


# ── Pipeline adapter rehearsal ─────────────────────────────────


class _FakeRegistry:
    def list(self) -> list[str]:
        return ["fake-agent"]


class _FakeOrchestrator:
    """Plan-only fake: records calls; has NO run() on purpose."""

    def __init__(self) -> None:
        self.registry = _FakeRegistry()
        self.plan_calls: list[dict[str, Any]] = []
        self.executed = False

    def plan(self, pipeline_name: str, input_file: str, config: dict | None = None) -> list[dict]:
        self.plan_calls.append(
            {"pipeline_name": pipeline_name, "input_file": input_file, "config": config},
        )
        return [
            {"step": 1, "agent": "fake-agent", "priority": 5, "dependencies": [], "description": "x"},
        ]

    def run(self, **kwargs: Any) -> Any:  # pragma: no cover - must never be called
        self.executed = True
        raise AssertionError("dry-run must not execute the pipeline")


class TestPipelineRehearsal:
    def test_dry_run_emits_marker_and_skips_execution(self, capsys: Any) -> None:
        from maop.delegate import doc_pipeline_adapter as adapter

        fake = _FakeOrchestrator()
        with patch.object(adapter, "get_orchestrator", lambda *a, **kw: fake):
            result = adapter.run_pipeline(
                pipeline_name="demo", input_file="in.md", dry_run=True,
            )
        captured = capsys.readouterr()
        # Marker printed as a stdout line of its own.
        marker_lines = [
            ln for ln in captured.out.splitlines()
            if ln.startswith(DRY_RUN_MARKER + ":")
        ]
        assert len(marker_lines) == 1, captured.out
        assert DRY_RUN_MARKER in captured.out
        # Result reports the rehearsal without executing anything.
        assert result["status"] == "success"
        assert result["dry_run"] is True
        assert result["steps"] == 1
        assert fake.plan_calls, "orch.plan() was not used for the rehearsal"
        assert fake.executed is False, "dry-run executed the pipeline!"

    def test_env_autodetect(self, monkeypatch: Any, capsys: Any) -> None:
        from maop.config import settings as settings_mod
        from maop.delegate import doc_pipeline_adapter as adapter

        # get_settings() is a process-wide singleton built on first call;
        # reset it so the (monkeypatched) env var is re-read. In production
        # the driver subprocess starts fresh — no reset needed there.
        monkeypatch.setattr(settings_mod, "_settings", None)
        monkeypatch.setenv("MAOP_DRY_RUN", "1")
        fake = _FakeOrchestrator()
        with patch.object(adapter, "get_orchestrator", lambda *a, **kw: fake):
            result = adapter.run_pipeline(
                pipeline_name="demo", input_file="in.md",  # dry_run omitted → env
            )
        capsys.readouterr()
        assert result["dry_run"] is True

    def test_no_dry_run_by_default(self) -> None:
        from maop.delegate import doc_pipeline_adapter as adapter

        fake = _FakeOrchestrator()
        with patch.object(adapter, "get_orchestrator", lambda *a, **kw: fake), \
                patch.object(adapter, "register_maop_event_hooks", lambda: 0), \
                patch.object(fake, "run", create=True):
            fake.run = MagicMock(return_value=SimpleNamespace(
                id="t1", status=type("S", (), {"value": "completed"})(),
                progress=1.0, result="ok", error=None, steps=[],
            ))
            adapter.run_pipeline(pipeline_name="demo", input_file="in.md")
            assert fake.plan_calls == [], "default path must execute, not plan"


class SimpleNamespace:
    def __init__(self, **kw: Any) -> None:
        self.__dict__.update(kw)


# ── Dispatch wiring ────────────────────────────────────────────


class _RealShapedConfig:
    """最小但真实形状的配置对象。

    必须带 ``routing`` 与 ``_version``：dispatch 链路会触达
    RouteScorer 单例（get_route_scorer 只认第一个 config），缺字段会
    把同一 xdist worker 里的后续测试毒死（AttributeError:
    _RealShapedConfig has no attribute 'routing'）。
    """

    def __init__(self, agents: list[AgentConfig]) -> None:
        self.agents = agents
        self.workflows: list = []
        self.routing: dict = {}
        self._version: int = 0


def _probe_script(tmp_path: Path) -> str:
    """子进程探针：把 argv 与 MAOP_DRY_RUN 落盘，供断言。"""
    out = tmp_path / "probe.json"
    script = tmp_path / "probe.py"
    script.write_text(
        "import json, os, sys\n"
        f"json.dump({{\"argv\": sys.argv, \"dry_run_env\": os.environ.get(\"MAOP_DRY_RUN\")}}, "
        f"open({str(out)!r}, \"w\"))\n",
        encoding="utf-8",
    )
    return str(script).replace("\\", "/")


def _dispatch(tmp_path: Path, *, declared: bool, dry_run: bool) -> dict[str, Any]:
    from maop.delegate.dispatch_core import Dispatcher

    config = _RealShapedConfig([
        AgentConfig(
            name="probe-agent",
            cli=sys.executable,
            driver="cli",
            # {task} placeholder: the CLI driver substitutes the task
            # text (preamble included) here — without it argv never
            # carries the task and the test is vacuous.
            cli_args=_probe_script(tmp_path) + " {task}",
            timeout_s=60,
            dry_run_supported=declared,
        ),
    ])
    dispatcher = Dispatcher(MAOP_config=config)
    asyncio.run(
        dispatcher.dispatch(agent="probe-agent", task="REAL-TASK", dry_run=dry_run),
    )
    return json.loads((tmp_path / "probe.json").read_text(encoding="utf-8"))


@pytest.fixture
def _restore_route_scorer():
    """快照/恢复 RouteScorer 单例——dispatch 会把本文件的 config 注册进
    进程级单例，不带恢复会污染同一 xdist worker 的后续测试文件。"""
    from maop.core.routing import route_scorer as rs

    saved = rs._instance
    yield
    rs._instance = saved


class TestDispatchDryRunWiring:
    @pytest.mark.usefixtures("_restore_route_scorer")
    def test_declared_agent_gets_preamble_and_env(self, tmp_path: Path) -> None:
        probe = _dispatch(tmp_path, declared=True, dry_run=True)
        joined = " ".join(probe["argv"])
        assert DRY_RUN_PREAMBLE.split("[MAOP DRY-RUN MODE] ")[1][:30] in joined
        assert "REAL-TASK" in joined
        assert probe["dry_run_env"] == "1"

    def test_undeclared_agent_untouched(self, tmp_path: Path) -> None:
        probe = _dispatch(tmp_path, declared=False, dry_run=True)
        joined = " ".join(probe["argv"])
        assert "REAL-TASK" in joined
        assert "DRY-RUN MODE" not in joined
        # Env channel still rides (harmless: only declarers act on it).
        assert probe["dry_run_env"] == "1"

    def test_normal_dispatch_unchanged(self, tmp_path: Path) -> None:
        probe = _dispatch(tmp_path, declared=True, dry_run=False)
        joined = " ".join(probe["argv"])
        assert "REAL-TASK" in joined
        assert "DRY-RUN MODE" not in joined
        assert probe["dry_run_env"] is None
