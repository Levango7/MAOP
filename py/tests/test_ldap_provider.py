"""Tests for maop.core.security.ldap_provider — LDAP/AD 集成。

使用 mock connection，不需要真实 LDAP 服务器。

覆盖：
* 配置验证
* 用户搜索（OpenLDAP / AD 风格）
* 用户同步（全量 / 增量）
* 认证（bind 验证）
* 组 → role 映射（精确 / 正则 / 默认）
* 异步包装
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import pytest

from maop.core.security.ldap_provider import (
    GroupRoleMapping,
    LDAPConfig,
    LDAPConfigError,
    LDAPConnectionError,
    LDAPProvider,
    LDAPUser,
    authenticate_async,
    sync_users_async,
)

# ── Mock 连接 ────────────────────────────────────────────────


class MockLDAPEntry:
    """模拟 ldap3 Entry。"""

    def __init__(self, dn: str, attributes: dict[str, Any]):
        self.entry_dn = dn
        self.entry_attributes = list(attributes.keys())
        self._attrs = attributes

    def __getitem__(self, name: str) -> Any:
        values = self._attrs.get(name, [])

        class _Attr:
            def __init__(self, vals: list):
                self.values = vals

        return _Attr(values)


class MockLDAPConnection:
    """模拟 ldap3 Connection。"""

    def __init__(self, entries: list[MockLDAPEntry] | None = None):
        self.entries = entries or []
        self._bind_user: str | None = None
        self._bind_password: str | None = None
        self._bind_success_users: dict[str, str] = {}

    def search(self, search_base: str = "", search_filter: str = "",
                attributes: list | None = None, **kwargs: Any) -> Any:
        # 简单过滤：如果 filter 包含 (uid=xxx) 或 (sAMAccountName=xxx)，
        # 仅返回匹配的条目；否则返回全部。
        import re as _re
        uid_match = _re.search(r"\(uid=([^)]+)\)", search_filter or "")
        sam_match = _re.search(r"\(sAMAccountName=([^)]+)\)", search_filter or "")
        target = None
        if uid_match:
            target = uid_match.group(1)
        elif sam_match:
            target = sam_match.group(1)
        if target and target != "*":
            filtered = []
            for entry in self.entries:
                uid_vals = entry._attrs.get("uid", [])
                sam_vals = entry._attrs.get("sAMAccountName", [])
                if target in uid_vals or target in sam_vals:
                    filtered.append(entry)
            self.entries = filtered
        return self.entries

    def bind_as(self, user_dn: str, password: str) -> bool:
        return self._bind_success_users.get(user_dn) == password

    def unbind(self) -> None:
        pass

    def close(self) -> None:
        pass


def make_mock_factory(entries: list[MockLDAPEntry],
                      bind_users: dict[str, str] | None = None):
    """创建 mock connection factory。

    Parameters
    ----------
    entries : list
        search 返回的条目列表。
    bind_users : dict
        {user_dn: password} 允许成功 bind 的凭据。
    """
    bind_users = bind_users or {}

    def factory(config: LDAPConfig) -> MockLDAPConnection:
        # 每次创建新连接时复制 entries，避免前一次过滤影响后一次
        conn = MockLDAPConnection(list(entries))
        conn._bind_success_users = bind_users
        return conn

    return factory


# ── fixtures ─────────────────────────────────────────────────


@pytest.fixture
def ldap_config() -> LDAPConfig:
    return LDAPConfig(
        server_url="ldap://test-server:389",
        bind_dn="cn=admin,dc=example,dc=com",
        bind_password="admin-password",
        user_base_dn="ou=users,dc=example,dc=com",
        group_base_dn="ou=groups,dc=example,dc=com",
    )


@pytest.fixture
def ad_config() -> LDAPConfig:
    return LDAPConfig(
        server_url="ldap://dc.example.com:389",
        bind_dn="CN=admin,DC=example,DC=com",
        bind_password="admin-password",
        user_base_dn="OU=users,DC=example,DC=com",
        group_base_dn="OU=groups,DC=example,DC=com",
        is_active_directory=True,
    )


def _make_openldap_entries() -> list[MockLDAPEntry]:
    return [
        MockLDAPEntry(
            "uid=alice,ou=users,dc=example,dc=com",
            {
                "uid": ["alice"],
                "mail": ["alice@example.com"],
                "cn": ["Alice Smith"],
                "givenName": ["Alice"],
                "sn": ["Smith"],
                "memberOf": [
                    "cn=engineers,ou=groups,dc=example,dc=com",
                    "cn=admins,ou=groups,dc=example,dc=com",
                ],
            },
        ),
        MockLDAPEntry(
            "uid=bob,ou=users,dc=example,dc=com",
            {
                "uid": ["bob"],
                "mail": ["bob@example.com"],
                "cn": ["Bob Jones"],
                "givenName": ["Bob"],
                "sn": ["Jones"],
                "memberOf": ["cn=engineers,ou=groups,dc=example,dc=com"],
            },
        ),
    ]


def _make_ad_entries() -> list[MockLDAPEntry]:
    return [
        MockLDAPEntry(
            "CN=alice,OU=users,DC=example,DC=com",
            {
                "sAMAccountName": ["alice"],
                "userPrincipalName": ["alice@example.com"],
                "displayName": ["Alice Smith"],
                "givenName": ["Alice"],
                "sn": ["Smith"],
                "memberOf": ["CN=Domain Admins,CN=Users,DC=example,DC=com"],
                "userAccountControl": ["512"],  # 正常账户
            },
        ),
        MockLDAPEntry(
            "CN=bob,OU=users,DC=example,DC=com",
            {
                "sAMAccountName": ["bob"],
                "userPrincipalName": ["bob@example.com"],
                "displayName": ["Bob Jones"],
                "givenName": ["Bob"],
                "sn": ["Jones"],
                "memberOf": ["CN=Engineers,OU=groups,DC=example,DC=com"],
                "userAccountControl": ["514"],  # 禁用账户 (512 + 2)
            },
        ),
    ]


# ── 配置验证 ─────────────────────────────────────────────────


class TestLDAPConfig:
    def test_config_defaults(self, ldap_config):
        assert ldap_config.page_size == 1000
        assert ldap_config.is_active_directory is False
        assert ldap_config.modify_timestamp_attr == "modifyTimestamp"

    def test_ad_config(self, ad_config):
        assert ad_config.is_active_directory is True

    def test_invalid_regex_mapping_raises(self, ldap_config):
        with pytest.raises(LDAPConfigError, match="invalid regex"):
            LDAPProvider(
                ldap_config,
                group_mappings=[GroupRoleMapping(
                    group_dn_pattern="[invalid", use_regex=True, role="r",
                )],
            )


# ── 用户搜索 ─────────────────────────────────────────────────


class TestSearchUsers:
    def test_search_openldap(self, ldap_config):
        entries = _make_openldap_entries()
        provider = LDAPProvider(
            ldap_config,
            connection_factory=make_mock_factory(entries),
        )
        users = provider.search_users()
        assert len(users) == 2
        assert users[0].username == "alice"
        assert users[0].email == "alice@example.com"
        assert users[0].display_name == "Alice Smith"
        assert len(users[0].groups) == 2

    def test_search_ad(self, ad_config):
        entries = _make_ad_entries()
        provider = LDAPProvider(
            ad_config,
            connection_factory=make_mock_factory(entries),
        )
        users = provider.search_users()
        assert len(users) == 2
        assert users[0].username == "alice"
        # AD: 禁用账户
        assert users[0].is_active is True
        assert users[1].is_active is False

    def test_search_empty(self, ldap_config):
        provider = LDAPProvider(
            ldap_config,
            connection_factory=make_mock_factory([]),
        )
        users = provider.search_users()
        assert users == []

    def test_search_with_filter(self, ldap_config):
        entries = _make_openldap_entries()
        provider = LDAPProvider(
            ldap_config,
            connection_factory=make_mock_factory(entries),
        )
        users = provider.search_users("(uid=alice)")
        # mock 现在支持简单过滤
        assert len(users) == 1
        assert users[0].username == "alice"

    def test_entry_to_user_groups_parsed(self, ldap_config):
        entries = _make_openldap_entries()
        provider = LDAPProvider(
            ldap_config,
            connection_factory=make_mock_factory(entries),
        )
        users = provider.search_users()
        assert "cn=engineers,ou=groups,dc=example,dc=com" in users[0].groups
        assert "cn=admins,ou=groups,dc=example,dc=com" in users[0].groups

    def test_raw_attributes_preserved(self, ldap_config):
        entries = _make_openldap_entries()
        provider = LDAPProvider(
            ldap_config,
            connection_factory=make_mock_factory(entries),
        )
        users = provider.search_users()
        assert "uid" in users[0].raw_attributes
        assert "mail" in users[0].raw_attributes


# ── 用户同步 ─────────────────────────────────────────────────


class MockUserStore:
    """模拟 MAOP 用户存储。"""

    def __init__(self):
        self.users: dict[str, LDAPUser] = {}
        self.active_dns: set[str] = set()
        self.login_times: dict[str, str] = {}

    def upsert_user(self, user: LDAPUser) -> str:
        if user.dn in self.users:
            self.users[user.dn] = user
            return "updated"
        self.users[user.dn] = user
        self.active_dns.add(user.dn)
        return "created"

    def deactivate_user(self, dn: str) -> bool:
        if dn in self.active_dns:
            self.active_dns.discard(dn)
            return True
        return False

    def list_active_dns(self) -> list[str]:
        return list(self.active_dns)

    def update_last_login(self, dn: str) -> None:
        from datetime import datetime, timezone
        self.login_times[dn] = datetime.now(timezone.utc).isoformat()


class TestSyncUsers:
    def test_sync_creates_users(self, ldap_config):
        entries = _make_openldap_entries()
        provider = LDAPProvider(
            ldap_config,
            connection_factory=make_mock_factory(entries),
        )
        store = MockUserStore()
        result = provider.sync_users(user_store=store)
        assert result.synced == 2
        assert result.created == 2
        assert result.updated == 0
        assert result.errors == 0
        assert len(store.users) == 2

    def test_sync_updates_existing(self, ldap_config):
        entries = _make_openldap_entries()
        provider = LDAPProvider(
            ldap_config,
            connection_factory=make_mock_factory(entries),
        )
        store = MockUserStore()
        # 第一次同步
        provider.sync_users(user_store=store)
        # 第二次同步应全部更新
        result2 = provider.sync_users(user_store=store)
        assert result2.created == 0
        assert result2.updated == 2

    def test_sync_without_store(self, ldap_config):
        entries = _make_openldap_entries()
        provider = LDAPProvider(
            ldap_config,
            connection_factory=make_mock_factory(entries),
        )
        result = provider.sync_users()
        assert result.synced == 2
        assert len(result.synced_users) == 2

    def test_sync_deactivates_missing(self, ldap_config):
        """全量同步应停用不在 LDAP 中的用户。"""
        entries = _make_openldap_entries()
        provider = LDAPProvider(
            ldap_config,
            connection_factory=make_mock_factory(entries),
        )
        store = MockUserStore()
        # 预置一个不在 LDAP 中的用户
        store.users["uid=charlie,ou=users,dc=example,dc=com"] = LDAPUser(
            dn="uid=charlie,ou=users,dc=example,dc=com",
            username="charlie",
        )
        store.active_dns.add("uid=charlie,ou=users,dc=example,dc=com")
        result = provider.sync_users(user_store=store)
        assert result.deactivated == 1
        assert "uid=charlie,ou=users,dc=example,dc=com" not in store.active_dns

    def test_sync_incremental_skips_deactivation(self, ldap_config):
        """增量同步（since 参数）不应执行停用。"""
        entries = _make_openldap_entries()
        provider = LDAPProvider(
            ldap_config,
            connection_factory=make_mock_factory(entries),
        )
        store = MockUserStore()
        store.users["uid=charlie,ou=users,dc=example,dc=com"] = LDAPUser(
            dn="uid=charlie,ou=users,dc=example,dc=com",
            username="charlie",
        )
        store.active_dns.add("uid=charlie,ou=users,dc=example,dc=com")
        since = datetime.now(timezone.utc)
        result = provider.sync_users(since=since, user_store=store)
        assert result.deactivated == 0

    def test_sync_filter_includes_timestamp(self, ldap_config):
        """增量同步的过滤器应包含 modifyTimestamp 条件。"""
        provider = LDAPProvider(
            ldap_config,
            connection_factory=make_mock_factory([]),
        )
        since = datetime(2026, 1, 1, tzinfo=timezone.utc)
        filter_str = provider._build_sync_filter(since)
        assert "modifyTimestamp" in filter_str
        assert "20260101" in filter_str

    def test_sync_connection_error(self, ldap_config):
        def failing_factory(config: LDAPConfig):
            raise LDAPConnectionError("connection failed")

        provider = LDAPProvider(
            ldap_config, connection_factory=failing_factory,
        )
        result = provider.sync_users()
        assert result.errors == 1
        assert "search failed" in result.error_details[0]


# ── 认证 ─────────────────────────────────────────────────────


class TestAuthenticate:
    def test_authenticate_success(self, ldap_config):
        entries = _make_openldap_entries()
        alice_dn = "uid=alice,ou=users,dc=example,dc=com"
        provider = LDAPProvider(
            ldap_config,
            connection_factory=make_mock_factory(
                entries, bind_users={alice_dn: "alice-password"},
            ),
        )
        result = provider.authenticate("alice", "alice-password")
        assert result.authenticated is True
        assert result.user is not None
        assert result.user.username == "alice"

    def test_authenticate_wrong_password(self, ldap_config):
        entries = _make_openldap_entries()
        alice_dn = "uid=alice,ou=users,dc=example,dc=com"
        provider = LDAPProvider(
            ldap_config,
            connection_factory=make_mock_factory(
                entries, bind_users={alice_dn: "alice-password"},
            ),
        )
        result = provider.authenticate("alice", "wrong-password")
        assert result.authenticated is False
        assert "invalid credentials" in result.error

    def test_authenticate_user_not_found(self, ldap_config):
        provider = LDAPProvider(
            ldap_config,
            connection_factory=make_mock_factory([]),
        )
        result = provider.authenticate("nobody", "password")
        assert result.authenticated is False
        assert "not found" in result.error

    def test_authenticate_empty_credentials(self, ldap_config):
        provider = LDAPProvider(
            ldap_config,
            connection_factory=make_mock_factory([]),
        )
        result = provider.authenticate("", "password")
        assert result.authenticated is False
        result2 = provider.authenticate("user", "")
        assert result2.authenticated is False

    def test_authenticate_disabled_user(self, ad_config):
        entries = _make_ad_entries()
        provider = LDAPProvider(
            ad_config,
            connection_factory=make_mock_factory(
                entries,
                bind_users={
                    "CN=bob,OU=users,DC=example,DC=com": "bob-password",
                },
            ),
        )
        result = provider.authenticate("bob", "bob-password")
        assert result.authenticated is False
        assert "disabled" in result.error

    def test_authenticate_with_roles(self, ldap_config):
        entries = _make_openldap_entries()
        alice_dn = "uid=alice,ou=users,dc=example,dc=com"
        provider = LDAPProvider(
            ldap_config,
            group_mappings=[
                GroupRoleMapping(
                    group_dn_pattern="cn=admins,ou=groups,dc=example,dc=com",
                    role="admin",
                ),
                GroupRoleMapping(
                    group_dn_pattern="cn=engineers,ou=groups,dc=example,dc=com",
                    role="engineer",
                ),
            ],
            connection_factory=make_mock_factory(
                entries, bind_users={alice_dn: "pw"},
            ),
        )
        result = provider.authenticate("alice", "pw")
        assert result.authenticated is True
        assert "admin" in result.roles
        assert "engineer" in result.roles

    def test_authenticate_records_login(self, ldap_config):
        entries = _make_openldap_entries()
        alice_dn = "uid=alice,ou=users,dc=example,dc=com"
        provider = LDAPProvider(
            ldap_config,
            connection_factory=make_mock_factory(
                entries, bind_users={alice_dn: "pw"},
            ),
        )
        store = MockUserStore()
        result = provider.authenticate("alice", "pw", user_store=store)
        assert result.authenticated is True
        assert alice_dn in store.login_times


# ── 组 → role 映射 ──────────────────────────────────────────


class TestGroupRoleMapping:
    def test_exact_match(self, ldap_config):
        provider = LDAPProvider(
            ldap_config,
            group_mappings=[
                GroupRoleMapping(
                    group_dn_pattern="cn=admins,ou=groups,dc=example,dc=com",
                    role="admin",
                ),
            ],
        )
        roles = provider.map_groups_to_roles([
            "cn=admins,ou=groups,dc=example,dc=com",
            "cn=other,ou=groups,dc=example,dc=com",
        ])
        assert roles == ["admin"]

    def test_regex_match(self, ldap_config):
        provider = LDAPProvider(
            ldap_config,
            group_mappings=[
                GroupRoleMapping(
                    group_dn_pattern=r"cn=(\w+)-admins,",
                    use_regex=True,
                    role="admin",
                ),
            ],
        )
        roles = provider.map_groups_to_roles([
            "cn=engineering-admins,ou=groups,dc=example,dc=com",
        ])
        assert roles == ["admin"]

    def test_default_role(self, ldap_config):
        provider = LDAPProvider(
            ldap_config,
            group_mappings=[
                GroupRoleMapping(
                    group_dn_pattern="",
                    role="user",
                    is_default=True,
                ),
                GroupRoleMapping(
                    group_dn_pattern="cn=admins,ou=groups,dc=example,dc=com",
                    role="admin",
                ),
            ],
        )
        roles = provider.map_groups_to_roles([
            "cn=admins,ou=groups,dc=example,dc=com",
        ])
        assert "user" in roles
        assert "admin" in roles

    def test_no_match_returns_empty(self, ldap_config):
        provider = LDAPProvider(
            ldap_config,
            group_mappings=[
                GroupRoleMapping(
                    group_dn_pattern="cn=nonexistent",
                    role="r",
                ),
            ],
        )
        roles = provider.map_groups_to_roles(["cn=other"])
        assert roles == []

    def test_dedup_roles(self, ldap_config):
        """多个 group 映射到同一 role 应去重。"""
        provider = LDAPProvider(
            ldap_config,
            group_mappings=[
                GroupRoleMapping(group_dn_pattern="cn=a", role="admin"),
                GroupRoleMapping(group_dn_pattern="cn=b", role="admin"),
            ],
        )
        roles = provider.map_groups_to_roles(["cn=a", "cn=b"])
        assert roles == ["admin"]

    def test_case_insensitive_match(self, ldap_config):
        provider = LDAPProvider(
            ldap_config,
            group_mappings=[
                GroupRoleMapping(
                    group_dn_pattern="CN=Admins,OU=Groups,DC=Example,DC=Com",
                    role="admin",
                ),
            ],
        )
        roles = provider.map_groups_to_roles([
            "cn=admins,ou=groups,dc=example,dc=com",
        ])
        assert roles == ["admin"]

    def test_add_group_mapping(self, ldap_config):
        provider = LDAPProvider(ldap_config)
        provider.add_group_mapping(GroupRoleMapping(
            group_dn_pattern="cn=test", role="tester",
        ))
        assert len(provider.group_mappings) == 1

    def test_add_invalid_regex_mapping_raises(self, ldap_config):
        provider = LDAPProvider(ldap_config)
        with pytest.raises(LDAPConfigError):
            provider.add_group_mapping(GroupRoleMapping(
                group_dn_pattern="[invalid", use_regex=True, role="r",
            ))


# ── 连接测试 ─────────────────────────────────────────────────


class TestConnection:
    def test_test_connection_success(self, ldap_config):
        provider = LDAPProvider(
            ldap_config,
            connection_factory=make_mock_factory([]),
        )
        assert provider.test_connection() is True

    def test_test_connection_failure(self, ldap_config):
        def failing_factory(config: LDAPConfig):
            raise LDAPConnectionError("failed")

        provider = LDAPProvider(
            ldap_config, connection_factory=failing_factory,
        )
        assert provider.test_connection() is False


# ── 异步包装 ─────────────────────────────────────────────────


class TestAsyncWrappers:
    @pytest.mark.asyncio
    async def test_sync_users_async(self, ldap_config):
        entries = _make_openldap_entries()
        provider = LDAPProvider(
            ldap_config,
            connection_factory=make_mock_factory(entries),
        )
        result = await sync_users_async(provider)
        assert result.synced == 2

    @pytest.mark.asyncio
    async def test_authenticate_async(self, ldap_config):
        entries = _make_openldap_entries()
        alice_dn = "uid=alice,ou=users,dc=example,dc=com"
        provider = LDAPProvider(
            ldap_config,
            connection_factory=make_mock_factory(
                entries, bind_users={alice_dn: "pw"},
            ),
        )
        result = await authenticate_async(provider, "alice", "pw")
        assert result.authenticated is True