"""Tests for maop.core.agent.llm_chat.model_gateway — 模型授权网关。

覆盖：
  - ModelPermission 构造与通配符匹配
  - ModelGatewayConfig 默认值与构造
  - ModelAccessDecision 构造
  - ModelGateway 访问检查（默认 allow/deny、通配符、优先级、无规则）
  - ModelGateway 使用量记录与限额检查
  - ModelGateway 权限规则管理（添加/移除/列表/更新配置）
  - ModelGateway 持久化（权限、使用量跨实例）
"""

from __future__ import annotations

from pathlib import Path

import pytest

from maop.core.agent.llm_chat.model_gateway import (
    ModelAccessDecision,
    ModelGateway,
    ModelGatewayConfig,
    ModelPermission,
)

# ── Fixtures ──────────────────────────────────────────────────────


@pytest.fixture
def gw(tmp_path: Path) -> ModelGateway:
    """默认 allow 策略的网关。"""
    return ModelGateway(db_path=tmp_path / "gw.db")


@pytest.fixture
def gw_deny(tmp_path: Path) -> ModelGateway:
    """默认 deny 策略的网关。"""
    return ModelGateway(
        config=ModelGatewayConfig(default_policy="deny"),
        db_path=tmp_path / "gw_deny.db",
    )


# ── TestModelPermission ───────────────────────────────────────────


class TestModelPermission:
    def test_defaults(self):
        p = ModelPermission(model_pattern="gpt-4*")
        assert p.model_pattern == "gpt-4*"
        assert p.allowed is True
        assert p.max_tokens_per_call == 0
        assert p.daily_token_limit == 0
        assert p.priority == 100

    def test_with_values(self):
        p = ModelPermission(
            model_pattern="claude-*",
            allowed=False,
            max_tokens_per_call=8192,
            daily_token_limit=100000,
            priority=10,
        )
        assert p.allowed is False
        assert p.max_tokens_per_call == 8192
        assert p.daily_token_limit == 100000
        assert p.priority == 10

    def test_wildcard_star(self):
        ModelPermission(model_pattern="*")
        gw = ModelGateway()
        assert gw._match_pattern("*", "gpt-4o") is True
        assert gw._match_pattern("*", "claude-3") is True
        assert gw._match_pattern("*", "any-model") is True

    def test_wildcard_prefix(self):
        gw = ModelGateway()
        assert gw._match_pattern("gpt-4*", "gpt-4o") is True
        assert gw._match_pattern("gpt-4*", "gpt-4-turbo") is True
        assert gw._match_pattern("gpt-4*", "gpt-3.5") is False

    def test_wildcard_question_mark(self):
        gw = ModelGateway()
        assert gw._match_pattern("gpt-?o", "gpt-4o") is True
        assert gw._match_pattern("gpt-?o", "gpt-ao") is True
        assert gw._match_pattern("gpt-?o", "gpt-4oo") is False

    def test_exact_match(self):
        gw = ModelGateway()
        assert gw._match_pattern("gpt-4o", "gpt-4o") is True
        assert gw._match_pattern("gpt-4o", "gpt-4") is False


# ── TestModelGatewayConfig ────────────────────────────────────────


class TestModelGatewayConfig:
    def test_defaults(self):
        c = ModelGatewayConfig()
        assert c.default_policy == "allow"
        assert c.permissions == []
        assert c.global_daily_token_limit == 0
        assert c.enable_quota_check is True

    def test_with_permissions(self):
        perms = [
            ModelPermission(model_pattern="gpt-*", priority=1),
            ModelPermission(model_pattern="claude-*", allowed=False, priority=2),
        ]
        c = ModelGatewayConfig(
            default_policy="deny",
            permissions=perms,
            global_daily_token_limit=500000,
            enable_quota_check=False,
        )
        assert c.default_policy == "deny"
        assert len(c.permissions) == 2
        assert c.global_daily_token_limit == 500000
        assert c.enable_quota_check is False

    def test_permissions_default_factory_independent(self):
        c1 = ModelGatewayConfig()
        c2 = ModelGatewayConfig()
        c1.permissions.append(ModelPermission(model_pattern="x"))
        assert c2.permissions == []


# ── TestModelAccessDecision ───────────────────────────────────────


class TestModelAccessDecision:
    def test_defaults(self):
        d = ModelAccessDecision(allowed=True, model="gpt-4o")
        assert d.allowed is True
        assert d.model == "gpt-4o"
        assert d.reason == ""
        assert d.matched_permission is None
        assert d.max_tokens_per_call == 0
        assert d.remaining_daily_tokens == -1

    def test_denied_with_reason(self):
        d = ModelAccessDecision(
            allowed=False,
            model="claude-3",
            reason="not allowed by rule",
        )
        assert d.allowed is False
        assert d.reason == "not allowed by rule"

    def test_with_matched_permission(self):
        perm = ModelPermission(model_pattern="gpt-*", max_tokens_per_call=4096)
        d = ModelAccessDecision(
            allowed=True,
            model="gpt-4o",
            matched_permission=perm,
            max_tokens_per_call=4096,
            remaining_daily_tokens=5000,
        )
        assert d.matched_permission is not None
        assert d.matched_permission.model_pattern == "gpt-*"
        assert d.remaining_daily_tokens == 5000


# ── TestModelGatewayAccess ────────────────────────────────────────


class TestModelGatewayAccess:
    def test_default_allow_no_rules(self, gw: ModelGateway):
        d = gw.check_access("gpt-4o")
        assert d.allowed is True
        assert d.model == "gpt-4o"
        assert d.matched_permission is None
        assert "default_policy" in d.reason

    def test_default_deny_no_rules(self, gw_deny: ModelGateway):
        d = gw_deny.check_access("gpt-4o")
        assert d.allowed is False
        assert "default_policy" in d.reason

    def test_wildcard_match_allowed(self, gw: ModelGateway):
        gw.add_permission(ModelPermission(model_pattern="gpt-*", allowed=True))
        d = gw.check_access("gpt-4o")
        assert d.allowed is True
        assert d.matched_permission is not None
        assert d.matched_permission.model_pattern == "gpt-*"

    def test_wildcard_match_denied(self, gw: ModelGateway):
        gw.add_permission(ModelPermission(model_pattern="claude-*", allowed=False))
        d = gw.check_access("claude-3-opus")
        assert d.allowed is False
        assert d.matched_permission is not None

    def test_wildcard_not_match_other_family(self, gw: ModelGateway):
        gw.add_permission(ModelPermission(model_pattern="gpt-*", allowed=True))
        d = gw.check_access("claude-3")
        # 未匹配到规则，走 default_policy=allow
        assert d.allowed is True
        assert d.matched_permission is None

    def test_priority_lower_wins(self, gw: ModelGateway):
        gw.add_permission(ModelPermission(model_pattern="*", allowed=True, priority=50))
        gw.add_permission(
            ModelPermission(model_pattern="gpt-*", allowed=False, priority=10)
        )
        d = gw.check_access("gpt-4o")
        # priority=10 的 gpt-* 更优先
        assert d.allowed is False
        assert d.matched_permission is not None
        assert d.matched_permission.model_pattern == "gpt-*"

    def test_priority_higher_number_loses(self, gw: ModelGateway):
        gw.add_permission(
            ModelPermission(model_pattern="gpt-*", allowed=True, priority=200)
        )
        gw.add_permission(
            ModelPermission(model_pattern="gpt-4*", allowed=False, priority=100)
        )
        d = gw.check_access("gpt-4o")
        # 两个都匹配，priority=100 的 gpt-4* 胜出
        assert d.allowed is False
        assert d.matched_permission.model_pattern == "gpt-4*"

    def test_no_rule_falls_back_to_policy(self, tmp_path: Path):
        cfg = ModelGatewayConfig(default_policy="deny")
        gw = ModelGateway(config=cfg, db_path=tmp_path / "x.db")
        d = gw.check_access("unknown-model")
        assert d.allowed is False

    def test_max_tokens_per_call_propagated(self, gw: ModelGateway):
        gw.add_permission(
            ModelPermission(model_pattern="gpt-*", max_tokens_per_call=8192)
        )
        d = gw.check_access("gpt-4o")
        assert d.allowed is True
        assert d.max_tokens_per_call == 8192


# ── TestModelGatewayUsage ─────────────────────────────────────────


class TestModelGatewayUsage:
    def test_record_usage_basic(self, gw: ModelGateway):
        gw.record_usage("gpt-4o", 100)
        usage = gw.get_daily_usage()
        assert usage.get("gpt-4o", 0) == 100

    def test_record_usage_accumulates(self, gw: ModelGateway):
        gw.record_usage("gpt-4o", 100)
        gw.record_usage("gpt-4o", 200)
        usage = gw.get_daily_usage()
        assert usage.get("gpt-4o", 0) == 300

    def test_record_usage_zero_ignored(self, gw: ModelGateway):
        gw.record_usage("gpt-4o", 0)
        usage = gw.get_daily_usage()
        assert usage.get("gpt-4o", 0) == 0

    def test_record_usage_negative_ignored(self, gw: ModelGateway):
        gw.record_usage("gpt-4o", -50)
        usage = gw.get_daily_usage()
        assert usage.get("gpt-4o", 0) == 0

    def test_get_daily_usage_specific_model(self, gw: ModelGateway):
        gw.record_usage("gpt-4o", 100)
        gw.record_usage("claude-3", 200)
        result = gw.get_daily_usage("gpt-4o")
        assert result == {"gpt-4o": 100}

    def test_get_daily_usage_all_models(self, gw: ModelGateway):
        gw.record_usage("gpt-4o", 100)
        gw.record_usage("claude-3", 200)
        result = gw.get_daily_usage()
        assert result.get("gpt-4o") == 100
        assert result.get("claude-3") == 200

    def test_daily_token_limit_blocks_when_exceeded(self, tmp_path: Path):
        cfg = ModelGatewayConfig(
            permissions=[
                ModelPermission(
                    model_pattern="gpt-*",
                    daily_token_limit=1000,
                )
            ]
        )
        gw = ModelGateway(config=cfg, db_path=tmp_path / "lim.db")
        gw.record_usage("gpt-4o", 1000)
        d = gw.check_access("gpt-4o")
        assert d.allowed is False
        assert "daily_token_limit" in d.reason

    def test_daily_token_limit_allows_below_limit(self, tmp_path: Path):
        cfg = ModelGatewayConfig(
            permissions=[
                ModelPermission(
                    model_pattern="gpt-*",
                    daily_token_limit=1000,
                )
            ]
        )
        gw = ModelGateway(config=cfg, db_path=tmp_path / "lim.db")
        gw.record_usage("gpt-4o", 400)
        d = gw.check_access("gpt-4o")
        assert d.allowed is True
        assert d.remaining_daily_tokens == 600

    def test_global_daily_token_limit_blocks(self, tmp_path: Path):
        cfg = ModelGatewayConfig(
            global_daily_token_limit=500,
        )
        gw = ModelGateway(config=cfg, db_path=tmp_path / "g.db")
        gw.record_usage("gpt-4o", 300)
        gw.record_usage("claude-3", 200)
        d = gw.check_access("any-model")
        assert d.allowed is False
        assert "global_daily_token_limit" in d.reason

    def test_global_limit_remaining_takes_min(self, tmp_path: Path):
        cfg = ModelGatewayConfig(
            permissions=[
                ModelPermission(
                    model_pattern="gpt-*",
                    daily_token_limit=1000,
                )
            ],
            global_daily_token_limit=500,
        )
        gw = ModelGateway(config=cfg, db_path=tmp_path / "g.db")
        gw.record_usage("gpt-4o", 200)
        d = gw.check_access("gpt-4o")
        assert d.allowed is True
        # 规则剩余 800，全局剩余 300，取较小值
        assert d.remaining_daily_tokens == 300

    def test_quota_check_disabled(self, tmp_path: Path):
        cfg = ModelGatewayConfig(
            permissions=[
                ModelPermission(
                    model_pattern="gpt-*",
                    daily_token_limit=100,
                )
            ],
            enable_quota_check=False,
        )
        gw = ModelGateway(config=cfg, db_path=tmp_path / "nc.db")
        gw.record_usage("gpt-4o", 99999)
        d = gw.check_access("gpt-4o")
        assert d.allowed is True
        assert d.remaining_daily_tokens == -1


# ── TestModelGatewayPermissions ───────────────────────────────────


class TestModelGatewayPermissions:
    def test_add_permission(self, gw: ModelGateway):
        perm = ModelPermission(model_pattern="gpt-*", allowed=True)
        gw.add_permission(perm)
        perms = gw.list_permissions()
        assert len(perms) == 1
        assert perms[0].model_pattern == "gpt-*"

    def test_add_permission_overwrites_same_pattern(self, gw: ModelGateway):
        gw.add_permission(ModelPermission(model_pattern="gpt-*", allowed=True))
        gw.add_permission(ModelPermission(model_pattern="gpt-*", allowed=False))
        perms = gw.list_permissions()
        assert len(perms) == 1
        assert perms[0].allowed is False

    def test_remove_permission_existing(self, gw: ModelGateway):
        gw.add_permission(ModelPermission(model_pattern="gpt-*"))
        assert gw.remove_permission("gpt-*") is True
        assert gw.list_permissions() == []

    def test_remove_permission_nonexistent(self, gw: ModelGateway):
        assert gw.remove_permission("nope-*") is False

    def test_list_permissions_sorted_by_priority(self, gw: ModelGateway):
        gw.add_permission(ModelPermission(model_pattern="a-*", priority=200))
        gw.add_permission(ModelPermission(model_pattern="b-*", priority=50))
        gw.add_permission(ModelPermission(model_pattern="c-*", priority=100))
        perms = gw.list_permissions()
        assert [p.model_pattern for p in perms] == ["b-*", "c-*", "a-*"]

    def test_update_config_replaces_permissions(self, gw: ModelGateway):
        gw.add_permission(ModelPermission(model_pattern="old-*"))
        new_cfg = ModelGatewayConfig(
            permissions=[ModelPermission(model_pattern="new-*")],
            default_policy="deny",
        )
        gw.update_config(new_cfg)
        perms = gw.list_permissions()
        assert len(perms) == 1
        assert perms[0].model_pattern == "new-*"

    def test_update_config_changes_default_policy(self, gw: ModelGateway):
        d = gw.check_access("any")
        assert d.allowed is True
        gw.update_config(ModelGatewayConfig(default_policy="deny"))
        d = gw.check_access("any")
        assert d.allowed is False


# ── TestModelGatewayPersistence ───────────────────────────────────


class TestModelGatewayPersistence:
    def test_permissions_persist_across_instances(self, tmp_path: Path):
        db = tmp_path / "persist.db"
        gw1 = ModelGateway(db_path=db)
        gw1.add_permission(
            ModelPermission(
                model_pattern="gpt-*",
                allowed=True,
                max_tokens_per_call=4096,
                daily_token_limit=10000,
                priority=10,
            )
        )
        gw1.add_permission(
            ModelPermission(model_pattern="claude-*", allowed=False, priority=20)
        )
        # 新实例从同一 DB 加载
        gw2 = ModelGateway(db_path=db)
        perms = gw2.list_permissions()
        assert len(perms) == 2
        patterns = {p.model_pattern for p in perms}
        assert patterns == {"gpt-*", "claude-*"}
        # 验证字段完整保留
        gpt_perm = next(p for p in perms if p.model_pattern == "gpt-*")
        assert gpt_perm.allowed is True
        assert gpt_perm.max_tokens_per_call == 4096
        assert gpt_perm.daily_token_limit == 10000
        assert gpt_perm.priority == 10

    def test_usage_persists_across_instances(self, tmp_path: Path):
        db = tmp_path / "usage.db"
        gw1 = ModelGateway(db_path=db)
        gw1.record_usage("gpt-4o", 500, agent="agent-1")
        gw1.record_usage("gpt-4o", 300, agent="agent-2")
        gw1.record_usage("claude-3", 200)
        # 新实例从同一 DB 加载今日使用量
        gw2 = ModelGateway(db_path=db)
        usage = gw2.get_daily_usage()
        assert usage.get("gpt-4o", 0) == 800
        assert usage.get("claude-3", 0) == 200

    def test_persisted_permission_affects_access(self, tmp_path: Path):
        db = tmp_path / "acc.db"
        gw1 = ModelGateway(
            config=ModelGatewayConfig(default_policy="allow"),
            db_path=db,
        )
        gw1.add_permission(ModelPermission(model_pattern="denied-*", allowed=False))
        # 新实例：denied-* 规则应从 DB 加载并生效
        gw2 = ModelGateway(db_path=db)
        d = gw2.check_access("denied-model")
        assert d.allowed is False
        assert d.matched_permission is not None

    def test_remove_permission_persists(self, tmp_path: Path):
        db = tmp_path / "rm.db"
        gw1 = ModelGateway(db_path=db)
        gw1.add_permission(ModelPermission(model_pattern="gpt-*", allowed=False))
        gw1.remove_permission("gpt-*")
        # 新实例：规则应已被删除
        gw2 = ModelGateway(db_path=db)
        assert gw2.list_permissions() == []

    def test_config_permissions_synced_on_init(self, tmp_path: Path):
        db = tmp_path / "sync.db"
        cfg = ModelGatewayConfig(
            permissions=[
                ModelPermission(model_pattern="init-*", allowed=True, priority=5),
            ]
        )
        ModelGateway(config=cfg, db_path=db)
        # 新实例不传 config，应从 DB 加载
        gw2 = ModelGateway(db_path=db)
        perms = gw2.list_permissions()
        assert len(perms) == 1
        assert perms[0].model_pattern == "init-*"
        assert perms[0].priority == 5