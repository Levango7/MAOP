"""Tests for Agent Platform (v4.1): AgentScanner, AgentRegistry, CapabilityMatcher, Dispatcher integration."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from maop.core.agent.lifecycle.agent_registry import (
    AgentRegistry,
    RegisteredAgent,
)
from maop.core.agent.lifecycle.agent_scanner import (
    KNOWN_AGENTS,
    AgentScanner,
    AgentSource,
    AgentStatus,
    KnownAgentDef,
    ScannedAgent,
)
from maop.core.agent.tools.capability_matcher import (
    TASK_KEYWORD_MAP,
    CapabilityMatcher,
    MatcherConfig,
    MatchScore,
)

# ═══════════════════════════════════════════════════════════════════
# AgentScanner Tests
# ═══════════════════════════════════════════════════════════════════

class TestScannedAgent:
    def test_defaults(self):
        a = ScannedAgent(name="test")
        assert a.name == "test"
        assert a.cli_path == ""
        assert a.source == AgentSource.SCANNED
        assert a.status == AgentStatus.UNKNOWN
        assert a.capabilities == []

    def test_custom(self):
        a = ScannedAgent(
            name="claude", cli_path="/usr/bin/claude", version="1.0",
            source=AgentSource.MANUAL, status=AgentStatus.AVAILABLE,
            capabilities=["chat", "code"], provider="anthropic",
        )
        assert a.cli_path == "/usr/bin/claude"
        assert a.status == AgentStatus.AVAILABLE


class TestKnownAgentDef:
    def test_defaults(self):
        d = KnownAgentDef(name="test", cli_names=["test"])
        assert d.version_args == ["--version"]
        assert d.capabilities == []
        assert d.driver == "cli"

    def test_known_agents_registry(self):
        names = [k.name for k in KNOWN_AGENTS]
        assert "claude" in names
        assert "codex" in names
        assert "maop" in names
        assert len(KNOWN_AGENTS) >= 10


class TestAgentScanner:
    def test_init(self, tmp_path):
        scanner = AgentScanner(root_dir=str(tmp_path))
        assert scanner._db_path.name == "maop.db"
        assert scanner._db_path.exists()

    @patch.object(AgentScanner, "_find_cli", return_value=None)
    @patch.object(AgentScanner, "_probe_version", return_value="")
    def test_scan_unavailable(self, mock_probe, mock_find, tmp_path):
        scanner = AgentScanner(root_dir=str(tmp_path))
        results = scanner.scan()
        assert len(results) == len(KNOWN_AGENTS)
        for r in results:
            assert r.status == AgentStatus.UNAVAILABLE

    @patch.object(AgentScanner, "_find_cli", return_value="/usr/bin/claude")
    @patch.object(AgentScanner, "_probe_version", return_value="1.2.3")
    def test_scan_available(self, mock_probe, mock_find, tmp_path):
        scanner = AgentScanner(root_dir=str(tmp_path))
        results = scanner.scan()
        assert all(r.status == AgentStatus.AVAILABLE for r in results)
        assert all(r.cli_path == "/usr/bin/claude" for r in results)

    def test_register_manual(self, tmp_path):
        scanner = AgentScanner(root_dir=str(tmp_path))
        with patch.object(scanner, "_probe_version", return_value="2.0"):
            agent = scanner.register_manual(
                name="custom", cli_path="/opt/custom",
                provider="test", capabilities=["chat"],
            )
        assert agent.name == "custom"
        assert agent.source == AgentSource.MANUAL
        assert agent.status == AgentStatus.AVAILABLE

    def test_unregister(self, tmp_path):
        scanner = AgentScanner(root_dir=str(tmp_path))
        with patch.object(scanner, "_probe_version", return_value=""):
            scanner.register_manual(name="temp", cli_path="/tmp/temp")
        assert scanner.unregister("temp") is True
        assert scanner.unregister("nonexistent") is False

    def test_list_agents(self, tmp_path):
        scanner = AgentScanner(root_dir=str(tmp_path))
        with patch.object(scanner, "_find_cli", return_value=None), \
             patch.object(scanner, "_probe_version", return_value=""):
            scanner.scan()
        all_agents = scanner.list_agents()
        assert len(all_agents) >= len(KNOWN_AGENTS)

    def test_get_agent(self, tmp_path):
        scanner = AgentScanner(root_dir=str(tmp_path))
        with patch.object(scanner, "_find_cli", return_value="/usr/bin/claude"), \
             patch.object(scanner, "_probe_version", return_value="1.0"):
            scanner.scan()
        agent = scanner.get_agent("claude")
        assert agent is not None
        assert agent.name == "claude"
        assert scanner.get_agent("nonexistent") is None

    def test_check_agent(self, tmp_path):
        scanner = AgentScanner(root_dir=str(tmp_path))
        with patch.object(scanner, "_find_cli", return_value="/usr/bin/claude"), \
             patch.object(scanner, "_probe_version", return_value="1.0"):
            agent = scanner.check_agent("claude")
        assert agent is not None
        assert agent.name == "claude"
        assert scanner.check_agent("nonexistent_in_known") is None


# ═══════════════════════════════════════════════════════════════════
# AgentRegistry Tests
# ═══════════════════════════════════════════════════════════════════

class TestRegisteredAgent:
    def test_defaults(self):
        a = RegisteredAgent(name="test")
        assert a.enabled is True
        assert a.health == "unknown"
        assert a.capabilities == []
        assert a.driver == "cli"

    def test_custom(self):
        a = RegisteredAgent(
            name="claude", cli_path="/usr/bin/claude",
            provider="anthropic", capabilities=["chat", "code"],
            health="healthy",
        )
        assert a.provider == "anthropic"
        assert a.health == "healthy"


class TestAgentRegistry:
    def test_init(self, tmp_path):
        reg = AgentRegistry(root_dir=str(tmp_path))
        assert reg._db_path.name == "maop.db"

    def test_register_and_get(self, tmp_path):
        reg = AgentRegistry(root_dir=str(tmp_path))
        agent = RegisteredAgent(
            name="claude", cli_path="/usr/bin/claude",
            provider="anthropic", capabilities=["chat", "code"],
        )
        reg.register(agent)
        got = reg.get_agent("claude")
        assert got is not None
        assert got.name == "claude"
        assert got.provider == "anthropic"
        assert got.capabilities == ["chat", "code"]

    def test_register_sets_timestamp(self, tmp_path):
        reg = AgentRegistry(root_dir=str(tmp_path))
        agent = RegisteredAgent(name="test")
        reg.register(agent)
        got = reg.get_agent("test")
        assert got.registered_at != ""

    def test_unregister(self, tmp_path):
        reg = AgentRegistry(root_dir=str(tmp_path))
        reg.register(RegisteredAgent(name="temp"))
        assert reg.unregister("temp") is True
        assert reg.get_agent("temp") is None
        assert reg.unregister("nonexistent") is False

    def test_enable_disable(self, tmp_path):
        reg = AgentRegistry(root_dir=str(tmp_path))
        reg.register(RegisteredAgent(name="test"))
        assert reg.disable("test") is True
        agent = reg.get_agent("test")
        assert agent.enabled is False
        assert reg.enable("test") is True
        agent = reg.get_agent("test")
        assert agent.enabled is True

    def test_list_agents(self, tmp_path):
        reg = AgentRegistry(root_dir=str(tmp_path))
        reg.register(RegisteredAgent(name="a", capabilities=["chat"]))
        reg.register(RegisteredAgent(name="b", capabilities=["code"], provider="openai"))
        reg.register(RegisteredAgent(name="c", capabilities=["chat", "code"]))

        all_agents = reg.list_agents()
        names = [a.name for a in all_agents]
        assert "a" in names
        assert "b" in names
        assert "c" in names

        enabled = reg.list_agents(enabled_only=True)
        enabled_names = [a.name for a in enabled]
        assert "a" in enabled_names

        reg.disable("a")
        enabled = reg.list_agents(enabled_only=True)
        enabled_names = [a.name for a in enabled]
        assert "a" not in enabled_names

    def test_list_agents_by_capability(self, tmp_path):
        reg = AgentRegistry(root_dir=str(tmp_path))
        reg.register(RegisteredAgent(name="a", capabilities=["chat"]))
        reg.register(RegisteredAgent(name="b", capabilities=["code"]))
        chat_agents = reg.list_agents(capability="chat")
        assert any(a.name == "a" for a in chat_agents)

    def test_list_agents_by_provider(self, tmp_path):
        reg = AgentRegistry(root_dir=str(tmp_path))
        reg.register(RegisteredAgent(name="a", provider="anthropic"))
        reg.register(RegisteredAgent(name="b", provider="openai"))
        result = reg.list_agents(provider="anthropic")
        result_names = [a.name for a in result]
        assert "a" in result_names
        assert "b" not in result_names

    def test_health_check_agent_not_found(self, tmp_path):
        reg = AgentRegistry(root_dir=str(tmp_path))
        result = reg.health_check("nonexistent")
        assert result.healthy is False
        assert "not found" in result.error.lower()

    def test_health_check_no_cli(self, tmp_path):
        reg = AgentRegistry(root_dir=str(tmp_path))
        reg.register(RegisteredAgent(name="test", cli_path=""))
        result = reg.health_check("test")
        assert result.healthy is False

    @patch("subprocess.run")
    def test_health_check_success(self, mock_run, tmp_path):
        mock_run.return_value = MagicMock(returncode=0, stdout="1.0.0\n")
        reg = AgentRegistry(root_dir=str(tmp_path))
        reg.register(RegisteredAgent(name="test", cli_path="/usr/bin/test"))
        result = reg.health_check("test")
        assert result.healthy is True
        assert result.version == "1.0.0"

    @patch("subprocess.run")
    def test_health_check_timeout(self, mock_run, tmp_path):
        import subprocess
        mock_run.side_effect = subprocess.TimeoutExpired(cmd="test", timeout=10)
        reg = AgentRegistry(root_dir=str(tmp_path))
        reg.register(RegisteredAgent(name="test", cli_path="/usr/bin/test"))
        result = reg.health_check("test")
        assert result.healthy is False
        assert "timeout" in result.error.lower()

    @patch("subprocess.run")
    def test_health_check_file_not_found(self, mock_run, tmp_path):
        mock_run.side_effect = FileNotFoundError()
        reg = AgentRegistry(root_dir=str(tmp_path))
        reg.register(RegisteredAgent(name="test", cli_path="/usr/bin/test"))
        result = reg.health_check("test")
        assert result.healthy is False

    def test_health_log(self, tmp_path):
        reg = AgentRegistry(root_dir=str(tmp_path))
        reg.register(RegisteredAgent(name="test", cli_path=""))
        reg.health_check("test")
        log = reg.get_health_log(agent_name="test")
        assert len(log) >= 1

    def test_sync_from_scanner(self, tmp_path):
        scanner = AgentScanner(root_dir=str(tmp_path))
        reg = AgentRegistry(root_dir=str(tmp_path))
        with patch.object(scanner, "_find_cli", return_value="/usr/bin/claude"), \
             patch.object(scanner, "_probe_version", return_value="1.0"):
            synced = reg.sync_from_scanner(scanner)
        assert synced > 0
        agent = reg.get_agent("claude")
        assert agent is not None

    def test_sync_updates_existing(self, tmp_path):
        reg = AgentRegistry(root_dir=str(tmp_path))
        reg.register(RegisteredAgent(name="claude", cli_path="/old/path", version="0.1"))
        scanner = AgentScanner(root_dir=str(tmp_path))
        with patch.object(scanner, "_find_cli", return_value="/new/path"), \
             patch.object(scanner, "_probe_version", return_value="2.0"):
            reg.sync_from_scanner(scanner)
        agent = reg.get_agent("claude")
        assert agent.cli_path == "/new/path"
        assert agent.version == "2.0"


# ═══════════════════════════════════════════════════════════════════
# CapabilityMatcher Tests
# ═══════════════════════════════════════════════════════════════════

class TestMatcherConfig:
    def test_defaults(self):
        c = MatcherConfig()
        assert c.weight_capability == 0.50
        assert c.weight_health == 0.20
        assert c.weight_latency == 0.15
        assert c.weight_provider == 0.15
        assert c.unhealthy_penalty == 0.1

    def test_custom(self):
        c = MatcherConfig(weight_capability=0.6, unhealthy_penalty=0.2)
        assert c.weight_capability == 0.6


class TestMatchScore:
    def test_defaults(self):
        s = MatchScore(agent_name="test")
        assert s.total_score == 0.0
        assert s.matched_capabilities == []
        assert s.missing_capabilities == []


class TestTaskKeywordMap:
    def test_common_keywords(self):
        assert "fix" in TASK_KEYWORD_MAP
        assert "bug" in TASK_KEYWORD_MAP
        assert "test" in TASK_KEYWORD_MAP
        assert "deploy" in TASK_KEYWORD_MAP

    def test_keyword_maps_to_capabilities(self):
        for kw, caps in TASK_KEYWORD_MAP.items():
            assert len(caps) > 0, f"Keyword '{kw}' maps to empty capabilities"


class TestCapabilityMatcher:
    def test_infer_requirements(self):
        matcher = CapabilityMatcher()
        reqs = matcher.infer_requirements("Fix the authentication bug")
        assert "code" in reqs
        assert "edit" in reqs
        assert "search" in reqs

    def test_infer_requirements_default(self):
        matcher = CapabilityMatcher()
        reqs = matcher.infer_requirements("hello world xyz")
        assert "chat" in reqs
        assert "code" in reqs

    def test_match_no_registry(self):
        matcher = CapabilityMatcher(registry=None)
        results = matcher.match(task="fix bug")
        assert results == []

    def test_match_with_registry(self, tmp_path):
        reg = AgentRegistry(root_dir=str(tmp_path))
        reg.register(RegisteredAgent(
            name="claude", capabilities=["chat", "code", "edit", "search"],
            health="healthy", provider="anthropic",
        ))
        reg.register(RegisteredAgent(
            name="codex", capabilities=["chat", "code"],
            health="healthy", provider="openai",
        ))
        matcher = CapabilityMatcher(registry=reg)
        results = matcher.match(task="fix the bug")
        assert len(results) > 0
        assert results[0].agent_name in ("claude", "codex")

    def test_match_ranks_by_capability(self, tmp_path):
        reg = AgentRegistry(root_dir=str(tmp_path))
        reg.register(RegisteredAgent(
            name="weak", capabilities=["chat"],
            health="healthy",
        ))
        reg.register(RegisteredAgent(
            name="strong", capabilities=["chat", "code", "edit", "search"],
            health="healthy",
        ))
        matcher = CapabilityMatcher(registry=reg)
        results = matcher.match(task="fix the bug", requirements=["code", "edit", "search"], exclude=["claude", "codex", "copilot", "aider", "gemini", "cursor", "cline", "goose", "trae", "maop"])
        strong_results = [r for r in results if r.agent_name == "strong"]
        weak_results = [r for r in results if r.agent_name == "weak"]
        if strong_results and weak_results:
            assert strong_results[0].total_score >= weak_results[0].total_score

    def test_match_excludes(self, tmp_path):
        reg = AgentRegistry(root_dir=str(tmp_path))
        reg.register(RegisteredAgent(name="a", capabilities=["chat", "code"], health="healthy"))
        reg.register(RegisteredAgent(name="b", capabilities=["chat", "code"], health="healthy"))
        matcher = CapabilityMatcher(registry=reg)
        results = matcher.match(task="test", exclude=["a"])
        assert all(r.agent_name != "a" for r in results)

    def test_match_top_k(self, tmp_path):
        reg = AgentRegistry(root_dir=str(tmp_path))
        for i in range(5):
            reg.register(RegisteredAgent(name=f"agent{i}", capabilities=["chat"], health="healthy"))
        matcher = CapabilityMatcher(registry=reg)
        results = matcher.match(task="hello", top_k=2)
        assert len(results) <= 2

    def test_health_score_healthy(self, tmp_path):
        reg = AgentRegistry(root_dir=str(tmp_path))
        reg.register(RegisteredAgent(name="a", capabilities=["code"], health="healthy"))
        matcher = CapabilityMatcher(registry=reg)
        results = matcher.match(task="test", requirements=["code"])
        assert results[0].health_score == 1.0

    def test_health_score_unhealthy_penalty(self, tmp_path):
        reg = AgentRegistry(root_dir=str(tmp_path))
        reg.register(RegisteredAgent(name="a", capabilities=["code"], health="healthy"))
        reg.register(RegisteredAgent(name="b", capabilities=["code"], health="unhealthy"))
        matcher = CapabilityMatcher(registry=reg)
        results = matcher.match(task="test", requirements=["code"], exclude=["claude", "codex", "copilot", "aider", "gemini", "cursor", "cline", "goose", "trae", "maop"])
        healthy = [r for r in results if r.agent_name == "a"]
        unhealthy = [r for r in results if r.agent_name == "b"]
        if healthy and unhealthy:
            assert healthy[0].total_score > unhealthy[0].total_score

    def test_provider_preference(self, tmp_path):
        reg = AgentRegistry(root_dir=str(tmp_path))
        reg.register(RegisteredAgent(name="a", capabilities=["code"], health="healthy", provider="anthropic"))
        reg.register(RegisteredAgent(name="b", capabilities=["code"], health="healthy", provider="openai"))
        config = MatcherConfig(provider_preferences={"anthropic": 1.0, "openai": 0.2})
        matcher = CapabilityMatcher(registry=reg, config=config)
        results = matcher.match(task="test", requirements=["code"], exclude=["claude", "codex", "copilot", "aider", "gemini", "cursor", "cline", "goose", "trae", "maop"])
        anthropic = [r for r in results if r.agent_name == "a"]
        openai = [r for r in results if r.agent_name == "b"]
        if anthropic and openai:
            assert anthropic[0].provider_score > openai[0].provider_score

    def test_disabled_agents_excluded(self, tmp_path):
        reg = AgentRegistry(root_dir=str(tmp_path))
        reg.register(RegisteredAgent(name="enabled", capabilities=["code"], health="healthy"))
        reg.register(RegisteredAgent(name="disabled", capabilities=["code"], health="healthy"))
        reg.disable("disabled")
        matcher = CapabilityMatcher(registry=reg)
        results = matcher.match(task="test", requirements=["code"])
        assert all(r.agent_name != "disabled" for r in results)


# ═══════════════════════════════════════════════════════════════════
# Dispatcher Integration Tests
# ═══════════════════════════════════════════════════════════════════

class TestDispatcherRegistryIntegration:
    def test_resolve_from_registry(self, tmp_path):
        from maop.delegate.dispatcher import Dispatcher
        reg = AgentRegistry(root_dir=str(tmp_path))
        reg.register(RegisteredAgent(
            name="claude", cli_path="/usr/bin/claude",
            capabilities=["chat", "code"], provider="anthropic",
        ))
        d = Dispatcher(registry=reg)
        cfg = d._resolve_agent("claude")
        assert cfg is not None
        assert cfg.name == "claude"
        assert cfg.cli == "/usr/bin/claude"

    def test_resolve_from_registry_disabled(self, tmp_path):
        from maop.delegate.dispatcher import Dispatcher
        reg = AgentRegistry(root_dir=str(tmp_path))
        reg.register(RegisteredAgent(name="test", cli_path="/usr/bin/test"))
        reg.disable("test")
        d = Dispatcher(registry=reg)
        cfg = d._resolve_agent("test")
        assert cfg is None

    def test_match_agent(self, tmp_path):
        from maop.delegate.dispatcher import Dispatcher
        reg = AgentRegistry(root_dir=str(tmp_path))
        reg.register(RegisteredAgent(
            name="claude", capabilities=["chat", "code", "edit", "search"],
            health="healthy", provider="anthropic",
        ))
        matcher = CapabilityMatcher(registry=reg)
        d = Dispatcher(registry=reg, capability_matcher=matcher)
        cfg = d.match_agent("fix the bug")
        assert cfg is not None
        assert cfg.name == "claude"

    def test_match_agent_no_match(self, tmp_path):
        from maop.delegate.dispatcher import Dispatcher
        reg = AgentRegistry(root_dir=str(tmp_path))
        matcher = CapabilityMatcher(registry=reg)
        d = Dispatcher(registry=reg, capability_matcher=matcher)
        cfg = d.match_agent("xyzzy_plugh_no_such_task_12345")
        assert cfg is None or cfg.name not in ("weak", "strong", "a", "b")

    def test_yaml_config_takes_priority(self, tmp_path):
        from maop.delegate.dispatcher import Dispatcher

        reg = AgentRegistry(root_dir=str(tmp_path))
        reg.register(RegisteredAgent(
            name="claude", cli_path="/registry/path",
            capabilities=["chat"],
        ))

        mock_config = MagicMock()
        mock_agent = MagicMock()
        mock_agent.cli = "/yaml/path"
        mock_agent.driver = "cli"
        mock_agent.cli_args = ""
        mock_agent.capabilities = ["chat", "code"]
        mock_agent.timeout_s = 120
        mock_agent.model = ""
        mock_agent.wrapper = ""
        mock_agent.command = ""
        mock_agent.provider = ""  # F2c (Phase F): explicit str to avoid MagicMock
        mock_config.agents = {"claude": mock_agent}
        mock_config.workflows = None

        d = Dispatcher(MAOP_config=mock_config, registry=reg)
        cfg = d._resolve_agent("claude")
        assert cfg is not None
        assert cfg.cli == "/yaml/path"
