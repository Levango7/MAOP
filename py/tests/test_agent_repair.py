"""Tests for maop.core.agent_repair — agent CLI diagnosis and repair."""

from __future__ import annotations

from pathlib import Path

import pytest

from maop.core.agent_repair import (
    AgentRepair,
    DiagnosisResult,
    RepairResult,
)


@pytest.fixture
def repair(tmp_path: Path) -> AgentRepair:
    return AgentRepair(root_dir=tmp_path)


# ── Dataclass tests ──────────────────────────────────────────────


class TestDiagnosisResult:
    def test_defaults(self) -> None:
        r = DiagnosisResult()
        assert r.agent_name == ""
        assert r.cli_exists is False
        assert r.overall_status == "healthy"
        assert r.missing_dependencies == []

    def test_model_dump(self) -> None:
        r = DiagnosisResult(
            agent_name="claude", cli_name="claude",
            cli_exists=True, overall_status="healthy",
        )
        d = r.model_dump()
        assert d["agent_name"] == "claude"
        assert d["cli_exists"] is True
        assert d["overall_status"] == "healthy"
        assert len(d) == 10


class TestRepairResult:
    def test_defaults(self) -> None:
        r = RepairResult()
        assert r.success is False
        assert r.actions_taken == []
        assert r.errors == []

    def test_model_dump(self) -> None:
        r = RepairResult(
            agent_name="claude", success=True,
            actions_taken=["installed"],
        )
        d = r.model_dump()
        assert d["agent_name"] == "claude"
        assert d["success"] is True
        assert d["actions_taken"] == ["installed"]
        assert len(d) == 6


# ── _detect_install_method ───────────────────────────────────────


class TestDetectInstallMethod:
    def test_known_npm(self, repair: AgentRepair) -> None:
        assert repair._detect_install_method("claude") == "npm"
        assert repair._detect_install_method("codex") == "npm"

    def test_known_binary(self, repair: AgentRepair) -> None:
        assert repair._detect_install_method("cursor") == "binary"
        assert repair._detect_install_method("copilot") == "binary"

    def test_known_system(self, repair: AgentRepair) -> None:
        assert repair._detect_install_method("python") == "system"

    def test_unknown(self, repair: AgentRepair) -> None:
        assert repair._detect_install_method("nonexistent-cli") == "unknown"


# ── diagnose ─────────────────────────────────────────────────────


class TestDiagnose:
    @pytest.mark.asyncio
    async def test_no_cli_configured(self, repair: AgentRepair) -> None:
        result = await repair.diagnose("my-agent", agent_config=None)
        assert result.agent_name == "my-agent"
        assert result.overall_status == "broken"
        assert "No CLI configured" in result.config_issues[0]

    @pytest.mark.asyncio
    async def test_no_cli_in_dict_config(self, repair: AgentRepair) -> None:
        result = await repair.diagnose("my-agent", agent_config={"model": "gpt-4"})
        assert result.overall_status == "broken"
        assert result.cli_name == ""

    @pytest.mark.asyncio
    async def test_nonexistent_cli(self, repair: AgentRepair) -> None:
        result = await repair.diagnose(
            "my-agent",
            agent_config={"cli": "definitely-not-a-real-cli-xyz"},
        )
        assert result.cli_exists is False
        assert result.overall_status == "broken"
        assert "not found in PATH" in result.config_issues[0]
        assert result.install_method == "unknown"

    @pytest.mark.asyncio
    async def test_nonexistent_known_cli(
        self, repair: AgentRepair, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("shutil.which", lambda _: None)
        result = await repair.diagnose(
            "my-agent",
            agent_config={"cli": "claude"},
        )
        assert result.cli_exists is False
        assert result.overall_status == "broken"
        assert result.install_method == "npm"

    @pytest.mark.asyncio
    async def test_python_cli_exists(self, repair: AgentRepair) -> None:

        result = await repair.diagnose(
            "python-agent",
            agent_config={"cli": "python"},
        )
        assert result.cli_name == "python"
        assert result.cli_exists is True
        assert result.install_method == "system"


# ── diagnose_all ─────────────────────────────────────────────────


class TestDiagnoseAll:
    @pytest.mark.asyncio
    async def test_batch_diagnosis(self, repair: AgentRepair) -> None:
        agents = {
            "agent-a": {"cli": "definitely-not-real-a"},
            "agent-b": {"cli": "definitely-not-real-b"},
        }
        results = await repair.diagnose_all(agents)
        assert len(results) == 2
        assert all(r.overall_status == "broken" for r in results)

    @pytest.mark.asyncio
    async def test_empty_config(self, repair: AgentRepair) -> None:
        results = await repair.diagnose_all({})
        assert results == []

    @pytest.mark.asyncio
    async def test_mixed_agents(self, repair: AgentRepair) -> None:
        agents = {
            "good": {"cli": "python"},
            "bad": {"cli": "nonexistent-xyz"},
        }
        results = await repair.diagnose_all(agents)
        assert len(results) == 2
        statuses = {r.agent_name for r in results}
        assert statuses == {"good", "bad"}


# ── repair ───────────────────────────────────────────────────────


class TestRepair:
    @pytest.mark.asyncio
    async def test_no_cli_returns_error(self, repair: AgentRepair) -> None:
        result = await repair.repair("my-agent", agent_config=None)
        assert result.success is False
        assert "No CLI configured" in result.errors[0]

    @pytest.mark.asyncio
    async def test_binary_cli_cannot_auto_install(
        self, repair: AgentRepair, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("shutil.which", lambda _: None)
        result = await repair.repair(
            "my-agent",
            agent_config={"cli": "cursor"},
        )
        assert result.success is False
        assert any("binary-distributed" in e for e in result.errors)

    @pytest.mark.asyncio
    async def test_repair_includes_before_after_diagnosis(
        self, repair: AgentRepair, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("shutil.which", lambda _: None)
        result = await repair.repair(
            "my-agent",
            agent_config={"cli": "cursor"},
        )
        assert "agent_name" in result.diagnosis_before
        assert "agent_name" in result.diagnosis_after
        assert result.diagnosis_before["cli_name"] == "cursor"

    @pytest.mark.asyncio
    async def test_repair_unknown_cli_attempts_pip(
        self, repair: AgentRepair
    ) -> None:
        result = await repair.repair(
            "my-agent",
            agent_config={"cli": "some-unknown-pip-cli-xyz"},
        )
        assert len(result.actions_taken) > 0
        assert any("pip install" in a for a in result.actions_taken)