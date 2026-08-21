"""Tests for SSO provider CRUD + Registry + PKCE + SAML Metadata + 脱敏.

PRD: docs/prd-sso-integration.md
覆盖：
  - SSOProviderStore CRUD（SQLite + Fernet 加密/解密 + 脱敏）
  - SSOProviderRegistry 多 IdP 管理 + PKCE + state 校验
  - SAML SP Metadata 生成
  - 属性映射 + 角色映射
  - 连接测试（mock urllib）
  - Personal 版 404 守卫
"""

from __future__ import annotations

import urllib.error
from typing import Any

import pytest

pytest.importorskip("maop.enterprise")
from maop.enterprise.sso import (
    SSOConfig,
    SSOManager,
    SSOProvider,
    generate_pkce_pair,
)
from maop.enterprise.sso_registry import (
    SSOProviderRegistry,
    generate_sp_metadata,
)
from maop.enterprise.sso_store import (
    SENSITIVE_MASK,
    SSOProviderCreate,
    SSOProviderStore,
    SSOProviderUpdate,
    mask_sensitive_fields,
)
from typing_extensions import Self

from maop.config.edition import Edition, reset_edition, set_edition

# ── Edition fixture ──────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _enterprise_edition(monkeypatch: pytest.MonkeyPatch) -> Any:
    """Force enterprise edition so SSO modules init succeeds."""
    monkeypatch.setenv("MAOP_EDITION", "enterprise")
    set_edition(Edition.ENTERPRISE)
    yield
    reset_edition()


@pytest.fixture
def store(tmp_path: Any) -> SSOProviderStore:
    """Isolated SSOProviderStore backed by a tmp SQLite DB."""
    db_path = tmp_path / "sso_test.db"
    return SSOProviderStore(db_path=db_path)


@pytest.fixture
def registry(store: SSOProviderStore) -> SSOProviderRegistry:
    return SSOProviderRegistry(store=store)


# ── Test data helpers ────────────────────────────────────────────────


def _oidc_config(**overrides: Any) -> dict[str, Any]:
    cfg: dict[str, Any] = {
        "client_id": "test-client-id",
        "client_secret": "super-secret-value",
        "issuer_url": "https://idp.example.com/v2.0",
        "authorize_url": "https://idp.example.com/authorize",
        "token_url": "https://idp.example.com/token",
        "userinfo_url": "https://idp.example.com/userinfo",
        "redirect_uri": "https://maop.local/api/v1/sso/oidc/{provider_id}/callback",
        "scopes": ["openid", "profile", "email"],
        "use_pkce": True,
    }
    cfg.update(overrides)
    return cfg


def _saml_config(**overrides: Any) -> dict[str, Any]:
    cfg: dict[str, Any] = {
        "entity_id": "https://idp.example.com",
        "sso_url": "https://idp.example.com/saml/sso",
        "slo_url": "https://idp.example.com/saml/slo",
        "x509_cert": "FAKE_CERT_BASE64_DER",
        "acs_url": "https://maop.local/api/v1/sso/saml/{provider_id}/acs",
        "sp_entity_id": "maop-sp",
        "want_signed": True,
    }
    cfg.update(overrides)
    return cfg


# ════════════════════════════════════════════════════════════════════
# 1. PKCE 生成
# ════════════════════════════════════════════════════════════════════


class TestPKCE:
    def test_generate_pkce_pair_length(self) -> None:
        v, c = generate_pkce_pair()
        # verifier: 43-128 chars (RFC 7636)
        assert 43 <= len(v) <= 128
        # challenge: base64url without padding
        assert "=" not in c
        assert len(c) > 0

    def test_generate_pkce_pair_uniqueness(self) -> None:
        pairs = {generate_pkce_pair() for _ in range(50)}
        assert len(pairs) == 50  # 全部唯一

    def test_generate_pkce_pair_challenge_is_sha256_of_verifier(self) -> None:
        import base64
        import hashlib

        v, c = generate_pkce_pair()
        expected = base64.urlsafe_b64encode(
            hashlib.sha256(v.encode("ascii")).digest()
        ).rstrip(b"=").decode("ascii")
        assert c == expected


# ════════════════════════════════════════════════════════════════════
# 2. 脱敏
# ════════════════════════════════════════════════════════════════════


class TestMaskSensitive:
    def test_mask_client_secret(self) -> None:
        out = mask_sensitive_fields({"client_id": "x", "client_secret": "shh"})
        assert out["client_secret"] == SENSITIVE_MASK
        assert out["client_id"] == "x"

    def test_mask_x509_cert(self) -> None:
        out = mask_sensitive_fields({"x509_cert": "cert", "entity_id": "e"})
        assert out["x509_cert"] == SENSITIVE_MASK
        assert out["entity_id"] == "e"

    def test_mask_enc_suffix(self) -> None:
        out = mask_sensitive_fields({"client_secret_enc": "cipher", "x509_cert_enc": "c2"})
        assert out["client_secret"] == SENSITIVE_MASK
        assert out["x509_cert"] == SENSITIVE_MASK
        # 原 _enc key 不应出现
        assert "client_secret_enc" not in out
        assert "x509_cert_enc" not in out

    def test_mask_no_sensitive(self) -> None:
        out = mask_sensitive_fields({"authorize_url": "u", "scopes": ["openid"]})
        assert out == {"authorize_url": "u", "scopes": ["openid"]}


# ════════════════════════════════════════════════════════════════════
# 3. SSOProviderStore CRUD
# ════════════════════════════════════════════════════════════════════


class TestStoreCRUD:
    def test_create_and_get_oidc(self, store: SSOProviderStore) -> None:
        resp = store.create(
            SSOProviderCreate(
                name="Azure AD",
                protocol="oidc",
                config=_oidc_config(),
            )
        )
        assert resp.id > 0
        assert resp.name == "Azure AD"
        assert resp.protocol == "oidc"
        assert resp.enabled is True
        # 读回的 config 应包含明文 client_secret（已解密）
        fetched = store.get(resp.id)
        assert fetched is not None
        assert fetched.config["client_secret"] == "super-secret-value"
        assert fetched.config["client_id"] == "test-client-id"

    def test_create_and_get_saml(self, store: SSOProviderStore) -> None:
        resp = store.create(
            SSOProviderCreate(
                name="Keycloak",
                protocol="saml",
                config=_saml_config(),
            )
        )
        fetched = store.get(resp.id)
        assert fetched is not None
        assert fetched.config["x509_cert"] == "FAKE_CERT_BASE64_DER"
        assert fetched.config["sp_entity_id"] == "maop-sp"

    def test_create_name_conflict(self, store: SSOProviderStore) -> None:
        store.create(SSOProviderCreate(name="P1", protocol="oidc", config=_oidc_config()))
        with pytest.raises(ValueError, match="conflict"):
            store.create(SSOProviderCreate(name="P1", protocol="oidc", config=_oidc_config()))

    def test_create_name_conflict_different_tenant_ok(
        self, store: SSOProviderStore
    ) -> None:
        store.create(
            SSOProviderCreate(name="P1", protocol="oidc", tenant_id="t1", config=_oidc_config())
        )
        # 不同 tenant_id 同名应该允许
        resp2 = store.create(
            SSOProviderCreate(name="P1", protocol="oidc", tenant_id="t2", config=_oidc_config())
        )
        assert resp2.id > 0

    def test_get_not_found(self, store: SSOProviderStore) -> None:
        assert store.get(99999) is None

    def test_list_with_filters(self, store: SSOProviderStore) -> None:
        store.create(SSOProviderCreate(name="A", protocol="oidc", config=_oidc_config()))
        store.create(SSOProviderCreate(name="B", protocol="saml", config=_saml_config()))
        store.create(
            SSOProviderCreate(name="C", protocol="oidc", enabled=False, config=_oidc_config())
        )

        all_rows, total = store.list()
        assert total == 3
        assert len(all_rows) == 3

        oidc_rows, _ = store.list(protocol="oidc")
        assert len(oidc_rows) == 2

        enabled_rows, _ = store.list(enabled=True)
        assert len(enabled_rows) == 2

        disabled_rows, _ = store.list(enabled=False)
        assert len(disabled_rows) == 1
        assert disabled_rows[0].name == "C"

    def test_list_pagination(self, store: SSOProviderStore) -> None:
        for i in range(5):
            store.create(
                SSOProviderCreate(name=f"P{i}", protocol="oidc", config=_oidc_config())
            )
        rows, total = store.list(limit=2, offset=0)
        assert total == 5
        assert len(rows) == 2
        rows2, _ = store.list(limit=2, offset=2)
        assert len(rows2) == 2
        # 不重叠
        ids1 = {r.id for r in rows}
        ids2 = {r.id for r in rows2}
        assert not (ids1 & ids2)

    def test_update_partial(self, store: SSOProviderStore) -> None:
        resp = store.create(
            SSOProviderCreate(name="P1", protocol="oidc", config=_oidc_config())
        )
        updated = store.update(
            resp.id,
            SSOProviderUpdate(name="P1-renamed", enabled=False),
        )
        assert updated is not None
        assert updated.name == "P1-renamed"
        assert updated.enabled is False
        # config 应保持不变
        assert updated.config["client_id"] == "test-client-id"

    def test_update_config_keep_secret_on_empty(
        self, store: SSOProviderStore
    ) -> None:
        """更新 config 时 client_secret 空串表示不修改。"""
        resp = store.create(
            SSOProviderCreate(name="P1", protocol="oidc", config=_oidc_config())
        )
        updated = store.update(
            resp.id,
            SSOProviderUpdate(config={"client_secret": "", "client_id": "new-id"}),
        )
        assert updated is not None
        assert updated.config["client_id"] == "new-id"
        # 原 secret 保留
        assert updated.config["client_secret"] == "super-secret-value"

    def test_update_config_replace_secret(self, store: SSOProviderStore) -> None:
        resp = store.create(
            SSOProviderCreate(name="P1", protocol="oidc", config=_oidc_config())
        )
        updated = store.update(
            resp.id,
            SSOProviderUpdate(config={"client_secret": "new-secret"}),
        )
        assert updated is not None
        assert updated.config["client_secret"] == "new-secret"

    def test_update_not_found(self, store: SSOProviderStore) -> None:
        result = store.update(99999, SSOProviderUpdate(name="x"))
        assert result is None

    def test_delete(self, store: SSOProviderStore) -> None:
        resp = store.create(
            SSOProviderCreate(name="P1", protocol="oidc", config=_oidc_config())
        )
        assert store.delete(resp.id) is True
        assert store.get(resp.id) is None
        # 二次删除返回 False
        assert store.delete(resp.id) is False

    def test_list_enabled(self, store: SSOProviderStore) -> None:
        store.create(SSOProviderCreate(name="A", protocol="oidc", config=_oidc_config()))
        store.create(
            SSOProviderCreate(name="B", protocol="saml", enabled=False, config=_saml_config())
        )
        enabled = store.list_enabled()
        assert len(enabled) == 1
        assert enabled[0].name == "A"

    def test_attribute_mapping_persisted(self, store: SSOProviderStore) -> None:
        mapping = {
            "external_id": "sub",
            "email": "email",
            "display_name": "name",
            "roles": "groups",
            "role_mapping": {"admins": "admin"},
        }
        resp = store.create(
            SSOProviderCreate(
                name="P1",
                protocol="oidc",
                config=_oidc_config(),
                attribute_mapping=mapping,
            )
        )
        fetched = store.get(resp.id)
        assert fetched is not None
        assert fetched.attribute_mapping == mapping
        assert fetched.attribute_mapping["role_mapping"] == {"admins": "admin"}


# ════════════════════════════════════════════════════════════════════
# 4. SSOProviderRegistry
# ════════════════════════════════════════════════════════════════════


class TestRegistry:
    def test_prepare_oidc_authorize_with_pkce(
        self, registry: SSOProviderRegistry
    ) -> None:
        resp = registry.store.create(
            SSOProviderCreate(name="A", protocol="oidc", config=_oidc_config())
        )
        url, state = registry.prepare_oidc_authorize(resp.id)
        assert "code_challenge" in url
        assert "code_challenge_method=S256" in url
        assert state  # 非空
        # state 已暂存
        assert state in registry._pending

    def test_prepare_oidc_authorize_no_pkce(
        self, registry: SSOProviderRegistry
    ) -> None:
        resp = registry.store.create(
            SSOProviderCreate(
                name="A",
                protocol="oidc",
                config=_oidc_config(use_pkce=False),
            )
        )
        url, _ = registry.prepare_oidc_authorize(resp.id)
        assert "code_challenge" not in url

    def test_prepare_oidc_authorize_not_found(
        self, registry: SSOProviderRegistry
    ) -> None:
        with pytest.raises(KeyError):
            registry.prepare_oidc_authorize(99999)

    def test_prepare_oidc_authorize_disabled(
        self, registry: SSOProviderRegistry
    ) -> None:
        resp = registry.store.create(
            SSOProviderCreate(
                name="A",
                protocol="oidc",
                enabled=False,
                config=_oidc_config(),
            )
        )
        with pytest.raises(KeyError):
            registry.prepare_oidc_authorize(resp.id)

    def test_prepare_oidc_wrong_protocol(
        self, registry: SSOProviderRegistry
    ) -> None:
        resp = registry.store.create(
            SSOProviderCreate(name="A", protocol="saml", config=_saml_config())
        )
        with pytest.raises(ValueError):
            registry.prepare_oidc_authorize(resp.id)

    def test_handle_oidc_callback_state_mismatch(
        self, registry: SSOProviderRegistry
    ) -> None:
        resp = registry.store.create(
            SSOProviderCreate(name="A", protocol="oidc", config=_oidc_config())
        )
        with pytest.raises(ValueError, match="state"):
            registry.handle_oidc_callback(resp.id, code="c", state="bogus-state")

    def test_handle_oidc_callback_missing_code(
        self, registry: SSOProviderRegistry
    ) -> None:
        resp = registry.store.create(
            SSOProviderCreate(name="A", protocol="oidc", config=_oidc_config())
        )
        with pytest.raises(ValueError, match="code"):
            registry.handle_oidc_callback(resp.id, code="", state="")

    def test_prepare_saml_authorize(self, registry: SSOProviderRegistry) -> None:
        resp = registry.store.create(
            SSOProviderCreate(name="K", protocol="saml", config=_saml_config())
        )
        url, rs = registry.prepare_saml_authorize(resp.id)
        # SAML URL 应包含 SAMLRequest 参数
        assert "SAMLRequest=" in url or "sso_url" in url
        assert rs  # 非空 RelayState

    def test_invalidate_clears_cache(self, registry: SSOProviderRegistry) -> None:
        resp = registry.store.create(
            SSOProviderCreate(name="A", protocol="oidc", config=_oidc_config())
        )
        registry._get_manager(resp.id)
        assert resp.id in registry._managers
        registry.invalidate(resp.id)
        assert resp.id not in registry._managers

    def test_list_enabled_for_login(self, registry: SSOProviderRegistry) -> None:
        registry.store.create(
            SSOProviderCreate(name="A", protocol="oidc", config=_oidc_config())
        )
        registry.store.create(
            SSOProviderCreate(
                name="B",
                protocol="saml",
                enabled=False,
                config=_saml_config(),
            )
        )
        result = registry.list_enabled_for_login()
        assert result["count"] == 1
        assert result["providers"][0]["name"] == "A"
        # 不应包含敏感配置
        assert "config" not in result["providers"][0]

    def test_list_enabled_auto_redirect_single(
        self, registry: SSOProviderRegistry
    ) -> None:
        """单 IdP + auto_redirect=true → auto_redirect_provider_id 返回该 IdP。"""
        resp = registry.store.create(
            SSOProviderCreate(
                name="A",
                protocol="oidc",
                auto_redirect=True,
                config=_oidc_config(),
            )
        )
        result = registry.list_enabled_for_login()
        assert result["auto_redirect_provider_id"] == resp.id

    def test_list_enabled_auto_redirect_multiple_no_auto(
        self, registry: SSOProviderRegistry
    ) -> None:
        """多 IdP → auto_redirect_provider_id=None。"""
        registry.store.create(
            SSOProviderCreate(name="A", protocol="oidc", config=_oidc_config())
        )
        registry.store.create(
            SSOProviderCreate(name="B", protocol="saml", config=_saml_config())
        )
        result = registry.list_enabled_for_login()
        assert result["auto_redirect_provider_id"] is None

    def test_to_masked_response(self, registry: SSOProviderRegistry) -> None:
        resp = registry.store.create(
            SSOProviderCreate(name="A", protocol="oidc", config=_oidc_config())
        )
        d = registry.to_masked_response(resp)
        assert d["config"]["client_secret"] == SENSITIVE_MASK
        assert d["config"]["client_id"] == "test-client-id"


# ════════════════════════════════════════════════════════════════════
# 5. SAML SP Metadata 生成
# ════════════════════════════════════════════════════════════════════


class TestSPMetadata:
    def test_generate_sp_metadata_basic(self) -> None:
        xml = generate_sp_metadata("maop-sp", "https://maop.local/saml/1/acs")
        assert "EntityDescriptor" in xml
        assert 'entityID="maop-sp"' in xml
        assert "SPSSODescriptor" in xml
        assert "AssertionConsumerService" in xml
        assert 'Location="https://maop.local/saml/1/acs"' in xml
        assert 'WantAssertionsSigned="true"' in xml

    def test_generate_sp_metadata_no_signed(self) -> None:
        xml = generate_sp_metadata(
            "maop-sp", "https://maop.local/acs", want_signed=False
        )
        assert 'WantAssertionsSigned="false"' in xml

    def test_generate_sp_metadata_escaping(self) -> None:
        xml = generate_sp_metadata(
            "maop-sp&co", 'https://x.local/a?b="c"<d>'
        )
        # & → &amp;，" → &quot;，< → &lt;
        assert 'entityID="maop-sp&amp;co"' in xml
        assert "&quot;" in xml
        assert "&lt;" in xml

    def test_registry_get_sp_metadata(self, registry: SSOProviderRegistry) -> None:
        resp = registry.store.create(
            SSOProviderCreate(name="K", protocol="saml", config=_saml_config())
        )
        xml = registry.get_sp_metadata(resp.id)
        assert 'entityID="maop-sp"' in xml
        # {provider_id} 应被替换
        assert f"/saml/{resp.id}/acs" in xml

    def test_registry_get_sp_metadata_not_saml(
        self, registry: SSOProviderRegistry
    ) -> None:
        resp = registry.store.create(
            SSOProviderCreate(name="A", protocol="oidc", config=_oidc_config())
        )
        with pytest.raises(ValueError):
            registry.get_sp_metadata(resp.id)

    def test_registry_get_sp_metadata_not_found(
        self, registry: SSOProviderRegistry
    ) -> None:
        with pytest.raises(KeyError):
            registry.get_sp_metadata(99999)


# ════════════════════════════════════════════════════════════════════
# 6. 属性映射 + 角色映射
# ════════════════════════════════════════════════════════════════════


class TestAttributeMapping:
    def test_default_mapping_no_config(self) -> None:
        """无 attribute_mapping → 用默认 precedence。"""
        cfg = SSOConfig(provider=SSOProvider.OIDC, default_role="viewer")
        mgr = SSOManager(config=cfg)
        user = mgr._build_user_from_claims(
            {"sub": "u1", "email": "e@x.com", "name": "N", "groups": ["g1", "g2"]},
            {},
        )
        assert user.external_id == "oidc:u1"
        assert user.email == "e@x.com"
        assert user.display_name == "N"
        assert user.roles == ["g1", "g2"]

    def test_custom_attribute_mapping(self) -> None:
        cfg = SSOConfig(
            provider=SSOProvider.OIDC,
            default_role="viewer",
            attribute_mapping={
                "external_id": "oid",
                "email": "mail",
                "display_name": "fullname",
                "roles": "groups",
            },
        )
        mgr = SSOManager(config=cfg)
        user = mgr._build_user_from_claims(
            {"oid": "u1", "mail": "e@x.com", "fullname": "N", "groups": ["g1"]},
            {},
        )
        assert user.external_id == "oidc:u1"
        assert user.email == "e@x.com"
        assert user.display_name == "N"
        assert user.roles == ["g1"]

    def test_role_mapping(self) -> None:
        cfg = SSOConfig(
            provider=SSOProvider.OIDC,
            default_role="viewer",
            attribute_mapping={"roles": "groups"},
            role_mapping={"admins": "admin", "viewers": "viewer"},
        )
        mgr = SSOManager(config=cfg)
        user = mgr._build_user_from_claims(
            {"sub": "u1", "groups": ["admins", "viewers", "unknown"]},
            {},
        )
        assert user.roles == ["admin", "viewer", "unknown"]

    def test_default_role_when_no_roles(self) -> None:
        cfg = SSOConfig(
            provider=SSOProvider.OIDC,
            default_role="viewer",
            attribute_mapping={"roles": "groups"},
        )
        mgr = SSOManager(config=cfg)
        user = mgr._build_user_from_claims({"sub": "u1"}, {})
        assert user.roles == ["viewer"]


# ════════════════════════════════════════════════════════════════════
# 7. 连接测试（mock urllib）
# ════════════════════════════════════════════════════════════════════


class _FakeResp:
    def __init__(self, status: int = 200) -> None:
        self.status = status

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *args: object) -> None:
        pass


class TestConnection:
    def test_oidc_reachable(
        self, registry: SSOProviderRegistry, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        resp = registry.store.create(
            SSOProviderCreate(name="A", protocol="oidc", config=_oidc_config())
        )
        monkeypatch.setattr(
            "urllib.request.urlopen", lambda *a, **k: _FakeResp(200)
        )
        result = registry.test_connection(resp.id)
        assert result["protocol"] == "oidc"
        assert result["reachable"] is True
        assert result["details"]["authorize_url_resolved"] is True
        assert result["details"]["token_url_resolved"] is True

    def test_oidc_token_unreachable(
        self, registry: SSOProviderRegistry, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        resp = registry.store.create(
            SSOProviderCreate(name="A", protocol="oidc", config=_oidc_config())
        )

        def fake_urlopen(req: Any, *a: Any, **k: Any) -> Any:
            url = getattr(req, "full_url", "")
            if "token" in url:
                raise urllib.error.URLError("connection refused")
            return _FakeResp(200)

        monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
        result = registry.test_connection(resp.id)
        assert result["reachable"] is False
        assert result["details"]["token_url_resolved"] is False
        assert "error" in result

    def test_saml_reachable(
        self, registry: SSOProviderRegistry, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        resp = registry.store.create(
            SSOProviderCreate(name="K", protocol="saml", config=_saml_config())
        )
        monkeypatch.setattr(
            "urllib.request.urlopen", lambda *a, **k: _FakeResp(200)
        )
        result = registry.test_connection(resp.id)
        assert result["protocol"] == "saml"
        assert result["reachable"] is True

    def test_connection_not_found(
        self, registry: SSOProviderRegistry
    ) -> None:
        with pytest.raises(KeyError):
            registry.test_connection(99999)


# ════════════════════════════════════════════════════════════════════
# 8. Personal 版 404 守卫
# ════════════════════════════════════════════════════════════════════


class TestPersonalEditionGuard:
    def test_store_init_fails_in_personal(
        self, tmp_path: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from maop.config.edition import FeatureNotAvailable

        reset_edition()
        monkeypatch.setenv("MAOP_EDITION", "personal")
        set_edition(Edition.PERSONAL)
        try:
            with pytest.raises(FeatureNotAvailable):
                SSOProviderStore(db_path=tmp_path / "x.db")
        finally:
            reset_edition()
            set_edition(Edition.ENTERPRISE)