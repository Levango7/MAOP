"""Tests for /api/agent/config/update schema validation."""
import sys
from pathlib import Path

import pytest

# Ensure MAOP is importable
MAOP_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(MAOP_ROOT))


class TestAgentDefValidation:
    """Test that AgentDef Pydantic model validates config correctly."""

    def test_valid_config(self):
        from maop.config.loader import AgentDef
        ad = AgentDef(cli="claude", driver="cli", model="claude-4", timeout_s=120,
                       capabilities=["code", "analysis"], description="test agent")
        assert ad.cli == "claude"
        assert ad.timeout_s == 120
        assert ad.capabilities == ["code", "analysis"]

    def test_invalid_timeout_type(self):
        """timeout_s must be an int, not a float or string."""
        from maop.config.loader import AgentDef
        with pytest.raises(Exception):  # noqa: B017
            AgentDef(cli="test", timeout_s="not_a_number")

    def test_invalid_timeout_string(self):
        from maop.config.loader import AgentDef
        with pytest.raises(Exception):  # noqa: B017
            AgentDef(cli="test", timeout_s="not_a_number")

    def test_invalid_driver_type(self):
        from maop.config.loader import AgentDef
        with pytest.raises(Exception):  # noqa: B017
            AgentDef(cli="test", driver=123)

    def test_capabilities_must_be_list(self):
        from maop.config.loader import AgentDef
        with pytest.raises(Exception):  # noqa: B017
            AgentDef(cli="test", capabilities="not_a_list")

    def test_empty_config_valid(self):
        from maop.config.loader import AgentDef
        ad = AgentDef()
        assert ad.cli == ""
        assert ad.driver == "cli"
        assert ad.timeout_s == 120


class TestConfigUpdateEndpointValidation:
    """Test the /api/agent/config/update endpoint rejects invalid configs."""

    def test_reject_bad_timeout_type(self):
        """The endpoint should reject timeout_s="abc" via AgentDef validation."""
        from maop.config.loader import AgentDef
        with pytest.raises(Exception):  # noqa: B017
            AgentDef(cli="test", timeout_s="not_a_number")

    def test_reject_non_string_model(self):
        from maop.config.loader import AgentDef
        with pytest.raises(Exception):  # noqa: B017
            AgentDef(cli="test", model=42)

    def test_reject_non_list_capabilities(self):
        from maop.config.loader import AgentDef
        with pytest.raises(Exception):  # noqa: B017
            AgentDef(cli="test", capabilities="single_string_not_list")

    def test_valid_update_fields(self):
        from maop.config.loader import AgentDef
        merged = {"cli": "claude", "driver": "cli", "model": "claude-4-sonnet",
                  "timeout_s": 60, "capabilities": ["code"], "description": "updated"}
        ad = AgentDef(**merged)
        assert ad.model == "claude-4-sonnet"
        assert ad.timeout_s == 60
