"""Tests for BYOK Gateway, Skill Version Manager, and Multi-Tenancy."""
from __future__ import annotations

import pytest

from maop.core.evolution.skill_version import SkillVersionManager
from maop.core.security.byok import BYOKGateway, KeyRoute, KeySource
from maop.core.security.tenant import TenantManager


class TestBYOKGateway:
    def test_register_source(self):
        gw = BYOKGateway()
        gw.register_source(KeySource(provider="openai", source_type="env", key_ref="OPENAI_API_KEY"))
        assert "openai" in gw._sources

    def test_add_route(self):
        gw = BYOKGateway()
        gw.add_route(KeyRoute(provider="openai", model="gpt-4", fallback_provider="anthropic"))
        assert len(gw._routes) == 1

    def test_route_default(self):
        gw = BYOKGateway()
        assert gw.route("openai") == "openai"

    def test_route_with_model(self):
        gw = BYOKGateway()
        gw.add_route(KeyRoute(provider="openai", model="gpt-4", key_source="azure"))
        assert gw.route("openai", model="gpt-4") == "azure"

    @pytest.mark.asyncio
    async def test_resolve_env_key(self):
        gw = BYOKGateway()
        gw.register_source(KeySource(provider="test", source_type="env", key_ref="TEST_KEY_BYOK"))
        with pytest.MonkeyPatch.context() as mp:
            mp.setenv("TEST_KEY_BYOK", "sk-test-123")
            result = await gw.resolve("test")
            assert result is not None
            assert result.key == "sk-test-123"
            assert result.source == "env"

    @pytest.mark.asyncio
    async def test_resolve_no_key(self):
        gw = BYOKGateway()
        gw.register_source(KeySource(provider="missing", source_type="env", key_ref="NONEXISTENT_KEY"))
        result = await gw.resolve("missing")
        assert result is None

    @pytest.mark.asyncio
    async def test_resolve_tenant_key(self):
        # High-round fix: tenant sources are fail-closed — allowed_tenants
        # must be configured explicitly (or ["*"] wildcard).
        gw = BYOKGateway()
        gw.register_source(KeySource(
            provider="openai", source_type="tenant", key_ref="",
            metadata={"allowed_tenants": ["acme"]},
        ))
        with pytest.MonkeyPatch.context() as mp:
            mp.setenv("MAOP_KEY_ACME_OPENAI", "sk-tenant-key")
            result = await gw.resolve("openai", tenant_id="acme")
            assert result is not None
            assert result.source == "tenant"

    @pytest.mark.asyncio
    async def test_resolve_tenant_key_fail_closed_without_allowlist(self):
        # High-round fix: no allowed_tenants configured -> deny (fail-closed)
        gw = BYOKGateway()
        gw.register_source(KeySource(provider="openai", source_type="tenant", key_ref=""))
        with pytest.MonkeyPatch.context() as mp:
            mp.setenv("MAOP_KEY_ACME_OPENAI", "sk-tenant-key")
            result = await gw.resolve("openai", tenant_id="acme")
            assert result is None

    @pytest.mark.asyncio
    async def test_resolve_direct_key(self):
        gw = BYOKGateway()
        gw.register_source(KeySource(provider="test", source_type="direct", key_ref="sk-direct-key"))
        result = await gw.resolve("test")
        # Direct source is rejected by design (byok.py:143) for security
        assert result is None

    def test_usage_stats(self):
        gw = BYOKGateway()
        gw._key_usage["openai"] = 5
        gw._key_usage["anthropic"] = 3
        stats = gw.get_usage_stats()
        assert stats["openai"] == 5


class TestSkillVersionManager:
    def test_save_and_load(self, tmp_path):
        mgr = SkillVersionManager(root_dir=tmp_path)
        meta = mgr.save_skill("test-skill", "# Test Skill\nHello world")
        assert meta.name == "test-skill"
        assert meta.version == "1.0.0"

        content = mgr.load_skill("test-skill")
        assert content is not None
        assert "Hello world" in content

    def test_version_increment(self, tmp_path):
        mgr = SkillVersionManager(root_dir=tmp_path)
        m1 = mgr.save_skill("inc-skill", "v1")
        assert m1.version == "1.0.0"
        m2 = mgr.save_skill("inc-skill", "v2")
        assert m2.version == "1.0.1"
        m3 = mgr.save_skill("inc-skill", "v3")
        assert m3.version == "1.0.2"

    def test_list_skills(self, tmp_path):
        mgr = SkillVersionManager(root_dir=tmp_path)
        mgr.save_skill("skill-a", "content a")
        mgr.save_skill("skill-b", "content b")
        skills = mgr.list_skills()
        assert len(skills) == 2

    def test_delete_skill(self, tmp_path):
        mgr = SkillVersionManager(root_dir=tmp_path)
        mgr.save_skill("del-skill", "to be deleted")
        assert mgr.delete_skill("del-skill") is True
        assert mgr.load_skill("del-skill") is None

    def test_delete_nonexistent(self, tmp_path):
        mgr = SkillVersionManager(root_dir=tmp_path)
        assert mgr.delete_skill("nonexistent") is False

    def test_load_nonexistent(self, tmp_path):
        mgr = SkillVersionManager(root_dir=tmp_path)
        assert mgr.load_skill("nonexistent") is None

    def test_get_history_no_git(self, tmp_path):
        mgr = SkillVersionManager(root_dir=tmp_path)
        mgr._git_available = False
        history = mgr.get_history("test")
        assert history == []


class TestTenantManager:
    def test_create_and_get(self, tmp_path):
        db_dir = tmp_path / "data"
        db_dir.mkdir(parents=True, exist_ok=True)
        (db_dir / "maop.db").touch()
        mgr = TenantManager(root_dir=tmp_path)
        config = mgr.create_tenant("acme", display_name="Acme Corp", quota_tokens=100000)
        assert config.tenant_id == "acme"
        assert config.quota_tokens == 100000

        retrieved = mgr.get_tenant("acme")
        assert retrieved is not None
        assert retrieved.display_name == "Acme Corp"

    def test_list_tenants(self, tmp_path):
        db_dir = tmp_path / "data"
        db_dir.mkdir(parents=True, exist_ok=True)
        (db_dir / "maop.db").touch()
        mgr = TenantManager(root_dir=tmp_path)
        mgr.create_tenant("t1")
        mgr.create_tenant("t2")
        assert len(mgr.list_tenants()) == 2

    def test_delete_tenant(self, tmp_path):
        db_dir = tmp_path / "data"
        db_dir.mkdir(parents=True, exist_ok=True)
        (db_dir / "maop.db").touch()
        mgr = TenantManager(root_dir=tmp_path)
        mgr.create_tenant("del-me")
        assert mgr.delete_tenant("del-me") is True
        assert mgr.get_tenant("del-me") is None

    def test_check_quota(self, tmp_path):
        db_dir = tmp_path / "data"
        db_dir.mkdir(parents=True, exist_ok=True)
        (db_dir / "maop.db").touch()
        mgr = TenantManager(root_dir=tmp_path)
        mgr.create_tenant("quota-test", quota_tokens=100, quota_requests=10)
        assert mgr.check_quota("quota-test", tokens_used=50) is True
        assert mgr.check_quota("quota-test", tokens_used=200) is False

    def test_check_agent_access(self, tmp_path):
        db_dir = tmp_path / "data"
        db_dir.mkdir(parents=True, exist_ok=True)
        (db_dir / "maop.db").touch()
        mgr = TenantManager(root_dir=tmp_path)
        mgr.create_tenant("agent-test", allowed_agents=["coder", "reviewer"])
        assert mgr.check_agent_access("agent-test", "coder") is True
        assert mgr.check_agent_access("agent-test", "unknown") is False

    def test_check_model_access(self, tmp_path):
        db_dir = tmp_path / "data"
        db_dir.mkdir(parents=True, exist_ok=True)
        (db_dir / "maop.db").touch()
        mgr = TenantManager(root_dir=tmp_path)
        mgr.create_tenant("model-test", allowed_models=["gpt-4"])
        assert mgr.check_model_access("model-test", "gpt-4") is True
        assert mgr.check_model_access("model-test", "claude-3") is False

    def test_disabled_tenant(self, tmp_path):
        db_dir = tmp_path / "data"
        db_dir.mkdir(parents=True, exist_ok=True)
        (db_dir / "maop.db").touch()
        mgr = TenantManager(root_dir=tmp_path)
        mgr.create_tenant("disabled", enabled=False)
        assert mgr.check_quota("disabled") is False
        assert mgr.check_agent_access("disabled", "any") is False

    def test_nonexistent_tenant(self, tmp_path):
        db_dir = tmp_path / "data"
        db_dir.mkdir(parents=True, exist_ok=True)
        (db_dir / "maop.db").touch()
        mgr = TenantManager(root_dir=tmp_path)
        assert mgr.get_tenant("nonexistent") is None
        assert mgr.check_quota("nonexistent") is False
