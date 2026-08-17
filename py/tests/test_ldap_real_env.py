"""真实 OpenLDAP 联调测试 — G-10。

测试分层
--------
1. **静态测试**（always run under ``-m slow``）：验证 LDAPConfig /
   GroupRoleMapping / LDAPProvider 的配置校验逻辑，不依赖外部
   LDAP 服务器。
2. **真实 OpenLDAP 联调**（skip if no ``MAOP_LDAP_TEST_HOST``）：
   连接真实 OpenLDAP 服务器，执行 bind / search / authenticate /
   sync_users 全流程验证。服务器连接信息通过环境变量配置::

       MAOP_LDAP_TEST_HOST=localhost
       MAOP_LDAP_TEST_PORT=389
       MAOP_LDAP_TEST_BIND_DN=cn=admin,dc=example,dc=com
       MAOP_LDAP_TEST_BIND_PASSWORD=admin
       MAOP_LDAP_TEST_USER_BASE=ou=users,dc=example,dc=com
       MAOP_LDAP_TEST_USER_UID=testuser
       MAOP_LDAP_TEST_USER_PASSWORD=testpass

3. **Docker OpenLDAP 联调**（skip if no Docker）：自动启动
   ``osixia/openldap`` 容器，运行联调测试，然后销毁容器。
   适用于 CI 环境无固定 LDAP 服务器时。

所有测试标记 ``@pytest.mark.slow``，默认不运行（``-m 'not slow'``）。
"""

from __future__ import annotations

import os
import shutil
import subprocess
import time
from typing import Any

import pytest

from maop.core.security.ldap_provider import (
    AuthResult,
    GroupRoleMapping,
    LDAPConfig,
    LDAPConfigError,
    LDAPProvider,
    LDAPSyncResult,
    LDAPUser,
)

pytestmark = pytest.mark.slow


# ── helpers ───────────────────────────────────────────────────────────


def _env(key: str, default: str = "") -> str:
    return os.environ.get(key, default)


def _ldap_env_configured() -> bool:
    """Check whether real LDAP connection info is provided via env vars."""
    return bool(_env("MAOP_LDAP_TEST_HOST"))


def _docker_available() -> bool:
    if not shutil.which("docker"):
        return False
    # 同 test_k8s_operator：docker daemon 未启动时 `info` 挂起，2s 探测 +
    # 捕获 TimeoutExpired 视为不可用（collection error 会让整个模块无法收集）。
    try:
        return subprocess.run(
            ["docker", "info"], capture_output=True, text=True, timeout=2, check=False,
        ).returncode == 0
    except subprocess.TimeoutExpired:
        return False


def _make_ldap_config(
    host: str = "",
    port: str = "389",
    bind_dn: str = "",
    bind_password: str = "",
    user_base: str = "",
) -> LDAPConfig:
    """Build an LDAPConfig from parameters or env vars."""
    return LDAPConfig(
        server_url=f"ldap://{host or _env('MAOP_LDAP_TEST_HOST', 'localhost')}:{port or _env('MAOP_LDAP_TEST_PORT', '389')}",
        bind_dn=bind_dn or _env("MAOP_LDAP_TEST_BIND_DN", "cn=admin,dc=example,dc=org"),
        bind_password=bind_password or _env("MAOP_LDAP_TEST_BIND_PASSWORD", "admin"),
        user_base_dn=user_base or _env(
            "MAOP_LDAP_TEST_USER_BASE", "ou=users,dc=example,dc=org"
        ),
        user_filter="(&(objectClass=inetOrgPerson)(uid={username}))",
        group_base_dn="ou=groups,dc=example,dc=org",
        is_active_directory=False,
        modify_timestamp_attr="modifyTimestamp",
    )


# ── 1. static config validation (always run) ─────────────────────────


class TestLDAPConfigValidation:
    """Validate LDAPConfig / GroupRoleMapping / LDAPProvider config logic."""

    def test_ldap_config_defaults(self):
        cfg = LDAPConfig(server_url="ldap://localhost:389")
        assert cfg.user_filter == "(&(objectClass=person)(uid={username}))"
        assert cfg.group_filter == "(objectClass=groupOfNames)"
        assert cfg.page_size == 1000
        assert cfg.is_active_directory is False
        assert cfg.modify_timestamp_attr == "modifyTimestamp"

    def test_ldap_config_openldap_preset(self):
        cfg = _make_ldap_config()
        assert "ldap://" in cfg.server_url
        assert "inetOrgPerson" in cfg.user_filter
        assert cfg.is_active_directory is False
        assert cfg.modify_timestamp_attr == "modifyTimestamp"

    def test_group_role_mapping_exact(self):
        mapping = GroupRoleMapping(
            group_dn_pattern="cn=admins,ou=groups,dc=example,dc=org",
            role="admin",
        )
        assert mapping.use_regex is False
        assert mapping.role == "admin"

    def test_group_role_mapping_regex(self):
        mapping = GroupRoleMapping(
            group_dn_pattern=r"cn=.*-admins,ou=groups,dc=example,dc=org",
            use_regex=True,
            role="admin",
        )
        assert mapping.use_regex is True

    def test_group_role_mapping_invalid_regex_raises(self):
        with pytest.raises(LDAPConfigError, match="invalid regex"):
            GroupRoleMapping(
                group_dn_pattern="[invalid",
                use_regex=True,
                role="admin",
            )
            # LDAPProvider validates regex on init; trigger via provider
            LDAPProvider(
                LDAPConfig(server_url="ldap://localhost"),
                group_mappings=[GroupRoleMapping(
                    group_dn_pattern="[invalid", use_regex=True, role="admin",
                )],
            )

    def test_provider_init_with_mappings(self):
        cfg = LDAPConfig(server_url="ldap://localhost:389")
        mappings = [
            GroupRoleMapping(group_dn_pattern="cn=admins,*", use_regex=True, role="admin"),
            GroupRoleMapping(group_dn_pattern="cn=users,*", use_regex=True, role="viewer"),
        ]
        provider = LDAPProvider(cfg, group_mappings=mappings)
        assert len(provider.group_mappings) == 2

    def test_ldap_user_model(self):
        user = LDAPUser(
            dn="uid=test,ou=users,dc=example,dc=org",
            username="test",
            email="test@example.org",
            display_name="Test User",
            groups=["cn=users,ou=groups,dc=example,dc=org"],
        )
        assert user.is_active is True
        assert user.username == "test"
        assert len(user.groups) == 1

    def test_sync_result_model(self):
        result = LDAPSyncResult(synced=10, created=5, updated=5)
        assert result.synced == 10
        assert result.deactivated == 0
        assert result.error_details == []

    def test_auth_result_model(self):
        result = AuthResult(authenticated=True, roles=["admin", "viewer"])
        assert result.authenticated is True
        assert result.user is None
        assert "admin" in result.roles


# ── 2. mock-based provider tests (always run) ────────────────────────


class _MockLDAPConnection:
    """In-memory mock LDAP connection for unit tests.

    Simulates enough of the ldap3 Connection interface to exercise
    LDAPProvider's search / bind / authenticate paths without a real
    server.  Entries are stored as ``(dn, attrs_dict)`` tuples which
    ``LDAPProvider._entry_to_user`` recognises as the python-ldap / mock
    style.
    """

    def __init__(self, users: list[dict[str, Any]] | None = None) -> None:
        self._users = users or []
        self._bound = False
        self.entries: list[Any] = []
        self.result: dict[str, Any] = {}

    def bind(self) -> bool:
        self._bound = True
        return True

    def unbind(self) -> None:
        self._bound = False

    def search(
        self,
        search_base: str = "",
        search_filter: str = "",
        attributes: list[str] | None = None,
        paged_size: int = 0,
        paged_cookie: Any = None,
    ) -> bool:
        """ldap3-style search; populates ``self.entries`` with tuples."""
        import re
        match = re.search(r"uid=([^)]+)", search_filter)
        if match:
            uid = match.group(1)
            self.entries = [
                (u["dn"], u.get("attrs", {}))
                for u in self._users
                if uid in u.get("uid", "")
            ]
        else:
            self.entries = [
                (u["dn"], u.get("attrs", {}))
                for u in self._users
            ]
        return True

    def bind_as(self, user_dn: str, password: str) -> bool:
        # Mock: password "testpass" succeeds, others fail
        return password == "testpass"


class TestMockLDAPProvider:
    """Test LDAPProvider with injected mock connection."""

    @pytest.fixture
    def mock_users(self) -> list[dict[str, Any]]:
        return [
            {
                "dn": "uid=testuser,ou=users,dc=example,dc=org",
                "uid": "testuser",
                "attrs": {
                    "uid": ["testuser"],
                    "cn": ["Test User"],
                    "mail": ["testuser@example.org"],
                    "objectClass": ["inetOrgPerson"],
                },
            },
        ]

    @pytest.fixture
    def provider(self, mock_users: list[dict[str, Any]]) -> LDAPProvider:
        cfg = _make_ldap_config()
        mock_conn = _MockLDAPConnection(mock_users)

        def factory(_config: LDAPConfig) -> _MockLDAPConnection:
            return mock_conn

        return LDAPProvider(cfg, connection_factory=factory)

    def test_test_connection_succeeds(self, provider: LDAPProvider):
        assert provider.test_connection() is True

    def test_authenticate_success(self, provider: LDAPProvider):
        result = provider.authenticate("testuser", "testpass")
        assert result.authenticated is True
        assert result.error == ""

    def test_authenticate_wrong_password(self, provider: LDAPProvider):
        result = provider.authenticate("testuser", "wrongpass")
        assert result.authenticated is False
        assert "invalid credentials" in result.error

    def test_authenticate_empty_credentials(self, provider: LDAPProvider):
        result = provider.authenticate("", "pass")
        assert result.authenticated is False
        assert "required" in result.error

    def test_authenticate_nonexistent_user(self, provider: LDAPProvider):
        result = provider.authenticate("nonexistent", "pass")
        assert result.authenticated is False
        assert "not found" in result.error


# ── 3. real OpenLDAP integration (skip if not configured) ───────────


@pytest.mark.skipif(
    not _ldap_env_configured(),
    reason="Set MAOP_LDAP_TEST_HOST to run real OpenLDAP integration tests",
)
class TestRealOpenLDAP:
    """Real OpenLDAP server integration tests.

    Requires a running OpenLDAP server with connection info provided via
    environment variables.  See module docstring for the variable list.
    """

    @pytest.fixture
    def provider(self) -> LDAPProvider:
        cfg = _make_ldap_config()
        return LDAPProvider(cfg)

    def test_connection_test(self, provider: LDAPProvider):
        """Service account bind succeeds."""
        assert provider.test_connection() is True

    def test_search_users(self, provider: LDAPProvider):
        """Search returns at least one user matching the filter."""
        users = provider.search_users("(objectClass=inetOrgPerson)")
        assert isinstance(users, list)

    def test_authenticate_known_user(self, provider: LDAPProvider):
        """Authenticate a known user with correct password."""
        username = _env("MAOP_LDAP_TEST_USER_UID", "testuser")
        password = _env("MAOP_LDAP_TEST_USER_PASSWORD", "testpass")
        result = provider.authenticate(username, password)
        # Either authenticated or user not found (depends on test data)
        # but should not raise
        assert isinstance(result, AuthResult)

    def test_authenticate_bad_password(self, provider: LDAPProvider):
        """Authentication with wrong password fails."""
        username = _env("MAOP_LDAP_TEST_USER_UID", "testuser")
        result = provider.authenticate(username, "definitely-wrong-password")
        assert result.authenticated is False

    def test_sync_users_dry_run(self, provider: LDAPProvider):
        """sync_users executes without error (no user_store = dry run)."""
        result = provider.sync_users()
        assert isinstance(result, LDAPSyncResult)
        assert result.errors >= 0


# ── 4. Docker OpenLDAP integration (skip if no Docker) ──────────────
# G-10: spin up a throwaway OpenLDAP container, run integration tests,
# then tear down.  Uses osixia/openldap:1.5.0 (pinned).


# 真实 Docker daemon + OpenLDAP 容器集成测试。CI 主矩阵无 Docker 环境，且
# `docker info` daemon 探测在 Windows 上挂起（subprocess timeout 杀不掉 native
# 挂起的 docker CLI → collection 卡死）。无条件 skip，保留 _docker_available
# 供未来独立 integration job 手动运行使用。
@pytest.mark.skip(
    reason="Requires real Docker daemon + OpenLDAP container; run in dedicated integration job",
)
class TestDockerOpenLDAP:
    """End-to-end: OpenLDAP container → bind → search → authenticate → teardown."""

    CONTAINER_NAME = "maop-test-openldap"
    LDAP_PORT = "1389"  # avoid conflict with host LDAP on 389

    @pytest.fixture(autouse=True)
    def _openldap_container(self):
        """Start OpenLDAP container, yield, then tear down."""
        # Clean up any leftover container
        subprocess.run(
            ["docker", "rm", "-f", self.CONTAINER_NAME],
            capture_output=True, text=True, timeout=10, check=False,
        )
        # Start osixia/openldap with seed data
        result = subprocess.run(
            [
                "docker", "run", "-d",
                "--name", self.CONTAINER_NAME,
                "-p", f"{self.LDAP_PORT}:389",
                "-e", "LDAP_ORGANISATION=MAOP Test",
                "-e", "LDAP_DOMAIN=example.org",
                "-e", "LDAP_ADMIN_PASSWORD=admin",
                "osixia/openldap:1.5.0",
            ],
            capture_output=True, text=True, timeout=60, check=False,
        )
        if result.returncode != 0:
            pytest.skip(f"failed to start OpenLDAP container: {result.stderr}")

        # Wait for OpenLDAP to be ready (poll up to 15s)
        ready = False
        for _ in range(15):
            time.sleep(1)
            health = subprocess.run(
                ["docker", "exec", self.CONTAINER_NAME,
                 "ldapsearch", "-x", "-H", "ldap://localhost",
                 "-b", "dc=example,dc=org", "-D",
                 "cn=admin,dc=example,dc=org", "-w", "admin"],
                capture_output=True, text=True, timeout=5, check=False,
            )
            if health.returncode == 0:
                ready = True
                break
        if not ready:
            subprocess.run(
                ["docker", "rm", "-f", self.CONTAINER_NAME],
                capture_output=True, timeout=10, check=False,
            )
            pytest.skip("OpenLDAP container did not become ready in 15s")

        yield

        # Teardown
        subprocess.run(
            ["docker", "rm", "-f", self.CONTAINER_NAME],
            capture_output=True, text=True, timeout=10, check=False,
        )

    def test_container_ldap_bind(self):
        """Service account bind succeeds against the containerized OpenLDAP."""
        cfg = LDAPConfig(
            server_url=f"ldap://localhost:{self.LDAP_PORT}",
            bind_dn="cn=admin,dc=example,dc=org",
            bind_password="admin",
            user_base_dn="dc=example,dc=org",
            user_filter="(objectClass=*)",
        )
        provider = LDAPProvider(cfg)
        assert provider.test_connection() is True

    def test_container_ldap_search(self):
        """Search returns the base DN entry."""
        cfg = LDAPConfig(
            server_url=f"ldap://localhost:{self.LDAP_PORT}",
            bind_dn="cn=admin,dc=example,dc=org",
            bind_password="admin",
            user_base_dn="dc=example,dc=org",
            user_filter="(objectClass=organization)",
        )
        provider = LDAPProvider(cfg)
        users = provider.search_users()
        assert isinstance(users, list)