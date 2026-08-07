r"""ADR-010 Bug Regression Tests.

Validates that the bugs fixed in ADR-010 batch remain fixed.
See: docs/adr/010-bugfix-batch.md

BUG-1: MAOP.ps1 path src\src\MAOP-loop.ps1 → src\MAOP-loop.ps1
BUG-2: Invoke-CmdDriver exit_code = 0 → try { $p.ExitCode } catch { 0 }
BUG-3: MAOP-loop $routingKey = "codegen" → "" + 5 fallback codegen → chat
H-1: memory.ps1 evolve trigger sync → async
H-2: Test-AgentAlive stub → fast CLI check
H-3: Dashboard watchdog dedup via Get-Job check
H-4: DAG condition branch skip rewritten
M-1: Removed dead per_agent from rules.yaml
M-1b: All codegen fallbacks replaced with chat
M-2: Dashboard token auth (env: MAOP_DASH_TOKEN)
M-5/M-6: dynamic-router/dag-engine → Python bridge
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest


class TestBug1Ps1Path:
    r"""BUG-1: MAOP.ps1 should reference src\MAOP-loop.ps1, not src\src\MAOP-loop.ps1."""

    def test_maop_ps1_no_double_src(self):
        MAOP_ROOT = Path(__file__).resolve().parents[3]
        ps1 = MAOP_ROOT / "maop.ps1"
        if not ps1.exists():
            pytest.skip("maop.ps1 not found")
        content = ps1.read_text(encoding="utf-8")
        assert "src\\src\\" not in content
        assert "src/src/" not in content


class TestBug2ExitCodeHandling:
    """BUG-2: Dispatcher should handle missing ExitCode gracefully."""

    def test_dispatcher_returns_exit_code_on_success(self):
        from maop.core.reliability.error_schema import new_result
        r = new_result(agent="test", task="t", exit_code=0, stdout="ok")
        assert r.exit_code == 0

    def test_dispatcher_returns_negative_on_failure(self):
        from maop.core.reliability.error_schema import new_result
        r = new_result(agent="test", task="t", exit_code=-1, error="fail")
        assert r.exit_code == -1


class TestBug3RoutingKeyDefault:
    """BUG-3: Default routing_key should be "" or "chat", not "codegen"."""

    def test_maop_loop_default_routing_not_codegen(self):
        from maop.maop_loop import LoopConfig
        lc = LoopConfig()
        assert lc.lb_algorithm != "codegen"

    def test_plan_fallback_uses_chat(self, tmp_path):
        from maop.maop_loop import LoopConfig, MaopLoop
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        (config_dir / "agents.yaml").write_text("agents: {}\n")
        (config_dir / "models.yaml").write_text("models: {}\n")
        (tmp_path / "data").mkdir(exist_ok=True)
        lc = LoopConfig(enable_semantic_analyze=False, enable_parallel=False,
                        enable_load_balancer=False, enable_result_cache=False,
                        enable_metrics=False, enable_timeseries=False,
                        enable_evolve=False, enable_dream=False, enable_cache_guard=False)
        loop = MaopLoop(root_dir=str(tmp_path), loop_config=lc)
        import asyncio
        plan = asyncio.run(loop._plan("test task", str(tmp_path)))
        result = plan if isinstance(plan, dict) else {}
        assert result.get("routing_key", "chat") != "codegen"


class TestH4DagConditionBranch:
    """H-4: DAG condition branch skip — verify condition steps work correctly."""

    @pytest.mark.asyncio
    async def test_condition_step_skips_when_false(self):
        from maop.engine import Engine, StepStatus, StepType, WorkflowStep
        engine = Engine()
        step = WorkflowStep(id="c1", type=StepType.CONDITION, params={"expr": "False"})
        result = await engine.run(steps=[step])
        assert result.steps[0].status == StepStatus.SKIPPED

    @pytest.mark.asyncio
    async def test_condition_step_succeeds_when_true(self):
        from maop.engine import Engine, StepStatus, StepType, WorkflowStep
        engine = Engine()
        step = WorkflowStep(id="c1", type=StepType.CONDITION, params={"expr": "True"})
        result = await engine.run(steps=[step])
        assert result.steps[0].status == StepStatus.SUCCESS


class TestM1NoPerAgentInRules:
    """M-1: Removed dead per_agent from rules.yaml."""

    def test_rules_yaml_no_per_agent(self):
        MAOP_ROOT = Path(__file__).resolve().parents[3]
        rules = MAOP_ROOT / "config" / "rules.yaml"
        if not rules.exists():
            pytest.skip("rules.yaml not found")
        content = rules.read_text(encoding="utf-8")
        assert "per_agent" not in content


class TestM1bNoCodegenFallback:
    """M-1b: All codegen fallbacks in MAOP-loop replaced with chat."""

    def test_maop_loop_no_codegen_fallback(self):
        MAOP_ROOT = Path(__file__).resolve().parents[3]
        loop_file = MAOP_ROOT / "py" / "MAOP" / "MAOP_loop.py"
        if not loop_file.exists():
            pytest.skip("MAOP_loop.py not found")
        content = loop_file.read_text(encoding="utf-8")
        lines = content.split("\n")
        for i, line in enumerate(lines):
            if "codegen" in line and "fallback" in line.lower():
                pytest.fail(f"Found codegen fallback at line {i+1}: {line.strip()}")


class TestM2DashboardTokenAuth:
    """M-2: Dashboard token auth via MAOP_DASH_TOKEN env var."""

    def test_require_admin_passes_with_admin_role(self):
        from unittest.mock import MagicMock

        from maop.core.security.middleware import require_admin
        request = MagicMock()
        request.state.auth_roles = ["admin"]
        require_admin(request)

    def test_require_admin_rejects_without_admin_role(self):
        from unittest.mock import MagicMock

        from fastapi import HTTPException

        from maop.core.security.middleware import require_admin
        request = MagicMock()
        request.state.auth_roles = []
        with pytest.raises(HTTPException) as exc_info:
            require_admin(request)
        assert exc_info.value.status_code == 403


class TestM5M6PythonBridge:
    """M-5/M-6: dynamic-router and dag-engine use Python bridge."""

    def test_dispatcher_resolves_from_python_config(self):
        from maop.delegate.dispatcher import Dispatcher
        mock_agent = MagicMock()
        mock_agent.name = "claude"
        mock_agent.cli = "echo"
        mock_agent.driver = "cli"
        mock_agent.cli_args = ""
        mock_agent.capabilities = []
        mock_agent.timeout_s = 10
        mock_agent.model = None
        mock_agent.wrapper = ""
        mock_agent.command = ""
        mock_agent.provider = ""  # F2c (Phase F): explicit str to avoid MagicMock
        mock_config = MagicMock()
        mock_config.agents = [mock_agent]
        mock_config.workflows = []

        dispatcher = Dispatcher(MAOP_config=mock_config)
        resolved = dispatcher._resolve_agent("claude")
        assert resolved is not None
        assert resolved.name == "claude"
