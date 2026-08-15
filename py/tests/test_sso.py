"""Tests for maop.enterprise.sso — t12 real OAuth code exchange + SAML SSO.

Mocks urllib.request.urlopen via monkeypatch to avoid real network calls.
Sets MAOP_EDITION=enterprise so require_feature(FeatureFlag.SSO) passes.
"""

from __future__ import annotations

import base64
import datetime
import hashlib
import io
import json
import time
from typing import Any

import pytest
from typing_extensions import Self

from maop.config.edition import Edition, reset_edition, set_edition
from maop.enterprise.sso import (
    SSOConfig,
    SSOError,
    SSOManager,
    SSOProvider,
    SSOSession,
)


@pytest.fixture(autouse=True)
def _enterprise_edition(monkeypatch: pytest.MonkeyPatch) -> None:
    """Force enterprise edition so SSOManager init succeeds."""
    monkeypatch.setenv("MAOP_EDITION", "enterprise")
    set_edition(Edition.ENTERPRISE)
    yield
    reset_edition()


def _make_oidc_config(**overrides: Any) -> SSOConfig:
    defaults: dict[str, Any] = {
        "provider": SSOProvider.OIDC,
        "client_id": "test-client-id",
        "client_secret": "test-client-secret",
        "issuer_url": "https://idp.example.com/",
        "authorize_url": "https://idp.example.com/authorize",
        "token_url": "https://idp.example.com/token",
        "userinfo_url": "https://idp.example.com/userinfo",
        "redirect_uri": "https://maop.local/cb",
        "scopes": ["openid", "profile", "email"],
        "default_role": "viewer",
    }
    defaults.update(overrides)
    return SSOConfig(**defaults)


def _make_saml_config(**overrides: Any) -> SSOConfig:
    """构造 SAML 测试用 SSOConfig。"""
    defaults: dict[str, Any] = {
        "provider": SSOProvider.SAML,
        "saml_entity_id": "maop-sp",
        "saml_acs_url": "https://maop.local/saml/acs",
        "saml_metadata_url": "https://idp.example.com/metadata",
        "redirect_uri": "https://maop.local/saml/acs",
        "default_role": "viewer",
    }
    defaults.update(overrides)
    return SSOConfig(**defaults)


class _FakeResponse:
    """Minimal file-like response object compatible with urllib context mgr."""

    def __init__(self, payload: bytes, status: int = 200) -> None:
        self._buf = io.BytesIO(payload)
        self.status = status
        self._closed = False

    def read(self, n: int = -1) -> bytes:
        return self._buf.read(n if n != -1 else -1)

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *args: object) -> None:
        self._closed = True


# ── SAML 测试辅助：生成密钥对 + 构造签名 SAML Response ──────────────


def _generate_test_cert():
    """生成测试用 RSA 密钥对和自签名证书。

    返回 (private_key, cert_b64)：
      - private_key: cryptography RSA 私钥对象（用于签名 SAML Response）
      - cert_b64: X.509 证书的 base64 DER 编码（用于 SSOConfig.saml_idp_cert）
    """
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = issuer = x509.Name([
        x509.NameAttribute(NameOID.COMMON_NAME, "test-idp"),
    ])
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer)
        .public_key(key.public_key())
        .serial_number(1)
        .not_valid_before(datetime.datetime.now(datetime.timezone.utc))
        .not_valid_after(datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(days=365))
        .sign(key, hashes.SHA256())
    )
    cert_der = cert.public_bytes(serialization.Encoding.DER)
    cert_b64 = base64.b64encode(cert_der).decode("ascii")
    return key, cert_b64


# SAML 命名空间常量
_SAML_NS = "urn:oasis:names:tc:SAML:2.0:assertion"
_SAMLP_NS = "urn:oasis:names:tc:SAML:2.0:protocol"
_DS_NS = "http://www.w3.org/2000/09/xmldsig#"


def _build_signed_saml_response(
    private_key,
    *,
    assertion_id: str = "_assertion_1",
    name_id: str = "testuser@example.com",
    audience: str = "maop-sp",
    not_before: datetime.datetime | None = None,
    not_on_or_after: datetime.datetime | None = None,
    attributes: dict[str, list[str]] | None = None,
    response_id: str = "_response_1",
    tamper_signature: bool = False,
) -> str:
    """构造一个 RSA-SHA256 签名的 SAML Response XML，返回 base64 编码。

    用于测试 SAMLHandler.handle_response()：
      1. 构建 <saml:Assertion>（含 Subject/Conditions/AttributeStatement）
      2. 对 Assertion 做 exclusive c14n + SHA256 摘要
      3. 构建 <ds:Signature>（SignedInfo 含 Reference + DigestValue）
      4. 对 SignedInfo 做 exclusive c14n + RSA-SHA256 签名
      5. 将 Signature 插入 Assertion，再包装进 <samlp:Response>
      6. base64 编码返回

    Args:
        tamper_signature: 若 True，将 SignatureValue 篡改一个字节（用于测试签名验证失败）。
    """
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import padding
    from lxml import etree

    now = datetime.datetime.now(datetime.timezone.utc)
    if not_before is None:
        not_before = now - datetime.timedelta(minutes=5)
    if not_on_or_after is None:
        not_on_or_after = now + datetime.timedelta(minutes=55)
    if attributes is None:
        attributes = {
            "email": ["testuser@example.com"],
            "name": ["Test User"],
            "roles": ["admin", "viewer"],
        }

    nb_str = not_before.strftime("%Y-%m-%dT%H:%M:%SZ")
    noa_str = not_on_or_after.strftime("%Y-%m-%dT%H:%M:%SZ")
    issue_instant = now.strftime("%Y-%m-%dT%H:%M:%SZ")

    # 1. 构建 Assertion（不含 Signature）
    assertion = etree.Element(
        f"{{{_SAML_NS}}}Assertion",
        nsmap={"saml": _SAML_NS, "ds": _DS_NS},
        attrib={
            "ID": assertion_id,
            "IssueInstant": issue_instant,
            "Version": "2.0",
        },
    )
    issuer_elem = etree.SubElement(assertion, f"{{{_SAML_NS}}}Issuer")
    issuer_elem.text = "test-idp"

    subject = etree.SubElement(assertion, f"{{{_SAML_NS}}}Subject")
    name_id_elem = etree.SubElement(subject, f"{{{_SAML_NS}}}NameID")
    name_id_elem.text = name_id
    subject_confirmation = etree.SubElement(
        subject,
        f"{{{_SAML_NS}}}SubjectConfirmation",
        attrib={"Method": "urn:oasis:names:tc:SAML:2.0:cm:bearer"},
    )
    etree.SubElement(
        subject_confirmation,
        f"{{{_SAML_NS}}}SubjectConfirmationData",
        attrib={
            "NotOnOrAfter": noa_str,
            "Recipient": "https://maop.local/saml/acs",
            "InResponseTo": "_authn_request_1",
        },
    )

    conditions = etree.SubElement(
        assertion,
        f"{{{_SAML_NS}}}Conditions",
        attrib={"NotBefore": nb_str, "NotOnOrAfter": noa_str},
    )
    audience_restriction = etree.SubElement(
        conditions, f"{{{_SAML_NS}}}AudienceRestriction"
    )
    audience_elem = etree.SubElement(audience_restriction, f"{{{_SAML_NS}}}Audience")
    audience_elem.text = audience

    attr_stmt = etree.SubElement(assertion, f"{{{_SAML_NS}}}AttributeStatement")
    for attr_name, values in attributes.items():
        attr = etree.SubElement(
            attr_stmt, f"{{{_SAML_NS}}}Attribute", attrib={"Name": attr_name}
        )
        for v in values:
            av = etree.SubElement(attr, f"{{{_SAML_NS}}}AttributeValue")
            av.text = v

    # 2. 计算 Assertion 的 exclusive c14n + SHA256 摘要
    assertion_c14n = etree.tostring(
        assertion, method="c14n", exclusive=True, with_comments=False
    )
    digest = hashlib.sha256(assertion_c14n).digest()
    digest_b64 = base64.b64encode(digest).decode("ascii")

    # 3. 构建 Signature 元素（含 SignedInfo）
    signature = etree.Element(f"{{{_DS_NS}}}Signature", nsmap={"ds": _DS_NS})
    signed_info = etree.SubElement(signature, f"{{{_DS_NS}}}SignedInfo")
    etree.SubElement(
        signed_info,
        f"{{{_DS_NS}}}CanonicalizationMethod",
        attrib={"Algorithm": "http://www.w3.org/2001/10/xml-exc-c14n#"},
    )
    etree.SubElement(
        signed_info,
        f"{{{_DS_NS}}}SignatureMethod",
        attrib={"Algorithm": "http://www.w3.org/2001/04/xmldsig-more#rsa-sha256"},
    )
    reference = etree.SubElement(
        signed_info, f"{{{_DS_NS}}}Reference", attrib={"URI": f"#{assertion_id}"}
    )
    transforms = etree.SubElement(reference, f"{{{_DS_NS}}}Transforms")
    etree.SubElement(
        transforms,
        f"{{{_DS_NS}}}Transform",
        attrib={"Algorithm": "http://www.w3.org/2000/09/xmldsig#enveloped-signature"},
    )
    etree.SubElement(
        transforms,
        f"{{{_DS_NS}}}Transform",
        attrib={"Algorithm": "http://www.w3.org/2001/10/xml-exc-c14n#"},
    )
    etree.SubElement(
        reference,
        f"{{{_DS_NS}}}DigestMethod",
        attrib={"Algorithm": "http://www.w3.org/2001/04/xmlenc#sha256"},
    )
    digest_value_elem = etree.SubElement(reference, f"{{{_DS_NS}}}DigestValue")
    digest_value_elem.text = digest_b64

    # 4. 对 SignedInfo 做 exclusive c14n + RSA-SHA256 签名
    signed_info_c14n = etree.tostring(
        signed_info, method="c14n", exclusive=True, with_comments=False
    )
    sig_value = private_key.sign(
        signed_info_c14n, padding.PKCS1v15(), hashes.SHA256()
    )

    if tamper_signature:
        # 篡改签名值：翻转第一个字节，使签名验证失败
        sig_bytes = bytearray(sig_value)
        sig_bytes[0] ^= 0xFF
        sig_value = bytes(sig_bytes)

    sig_value_b64 = base64.b64encode(sig_value).decode("ascii")
    signature_value_elem = etree.SubElement(signature, f"{{{_DS_NS}}}SignatureValue")
    signature_value_elem.text = sig_value_b64

    # 5. 将 Signature 插入 Assertion 作为第一个子元素
    assertion.insert(0, signature)

    # 6. 构建 Response
    response = etree.Element(
        f"{{{_SAMLP_NS}}}Response",
        nsmap={"samlp": _SAMLP_NS, "saml": _SAML_NS},
        attrib={
            "ID": response_id,
            "IssueInstant": issue_instant,
            "Version": "2.0",
            "Destination": "https://maop.local/saml/acs",
            "InResponseTo": "_authn_request_1",
        },
    )
    response.append(assertion)

    # 7. 序列化 + base64 编码
    response_xml = etree.tostring(response, xml_declaration=False, encoding="utf-8")
    return base64.b64encode(response_xml).decode("ascii")


# ── handle_callback OIDC real exchange ──────────────────────────────


class TestHandleCallbackOIDC:
    def test_exchange_code_success_builds_real_session(self, monkeypatch):
        config = _make_oidc_config()
        mgr = SSOManager(config)

        token_payload = json.dumps({
            "access_token": "AT-abc-123",
            "refresh_token": "RT-def-456",
            "expires_in": 1800,
            "token_type": "Bearer",
            "id_token": "eyJ.idtoken.payload",
        }).encode()
        # Default config has userinfo_url set, so handle_callback will make
        # TWO urlopen calls: token endpoint + userinfo. Capture all of them
        # and assert on the FIRST (token) call.
        calls: list[dict[str, Any]] = []

        def fake_urlopen(req, timeout=None):
            calls.append({
                "url": req.full_url,
                "method": req.method,
                "data": req.data.decode() if req.data else "",
                "headers": dict(req.headers),
            })
            # Return token JSON for the token call; empty userinfo for the
            # userinfo call (so we don't need to fabricate claims here).
            if "token" in req.full_url:
                return _FakeResponse(token_payload)
            return _FakeResponse(b"{}")

        monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)

        session = mgr.handle_callback("AUTH_CODE_123", state="xyz")

        assert isinstance(session, SSOSession)
        assert session.access_token == "AT-abc-123"
        assert session.refresh_token == "RT-def-456"
        # expires_at = now + 1800 (tolerate small clock drift)
        assert abs((session.expires_at - session.created_at) - 1800) < 5
        # First call must be the token endpoint.
        assert len(calls) >= 1
        token_call = calls[0]
        assert token_call["url"] == "https://idp.example.com/token"
        assert token_call["method"] == "POST"
        assert "grant_type=authorization_code" in token_call["data"]
        assert "code=AUTH_CODE_123" in token_call["data"]
        assert "client_id=test-client-id" in token_call["data"]
        assert "client_secret=test-client-secret" in token_call["data"]

    def test_handle_callback_fetches_userinfo_and_builds_user(self, monkeypatch):
        config = _make_oidc_config()
        mgr = SSOManager(config)

        token_payload = json.dumps({
            "access_token": "AT-xyz",
            "refresh_token": "",
            "expires_in": 3600,
        }).encode()
        userinfo_payload = json.dumps({
            "sub": "user-42",
            "email": "alice@example.com",
            "name": "Alice Lee",
            "roles": ["admin", "viewer"],
            "tenant_id": "tenant-9",
        }).encode()
        call_count = {"n": 0}

        def fake_urlopen(req, timeout=None):
            call_count["n"] += 1
            if "token" in req.full_url:
                return _FakeResponse(token_payload)
            if "userinfo" in req.full_url:
                # Verify Bearer header is set.
                assert req.headers.get("Authorization") == "Bearer AT-xyz"
                return _FakeResponse(userinfo_payload)
            raise AssertionError(f"unexpected URL: {req.full_url}")

        monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)

        session = mgr.handle_callback("CODE", state="")

        assert call_count["n"] == 2  # token + userinfo
        user = session.user
        assert user.external_id == "oidc:user-42"
        assert user.email == "alice@example.com"
        assert user.display_name == "Alice Lee"
        assert user.roles == ["admin", "viewer"]
        assert user.tenant_id == "tenant-9"

    def test_handle_callback_userinfo_failure_is_non_fatal(self, monkeypatch):
        config = _make_oidc_config()
        mgr = SSOManager(config)

        token_payload = json.dumps({
            "access_token": "AT",
            "expires_in": 3600,
        }).encode()

        def fake_urlopen(req, timeout=None):
            if "token" in req.full_url:
                return _FakeResponse(token_payload)
            # userinfo endpoint raises.
            raise OSError("userinfo down")

        monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)

        session = mgr.handle_callback("CODE")

        # Session still created; user built from token_resp only.
        assert session.access_token == "AT"
        assert session.user.external_id == "oidc:unknown"
        assert session.user.roles == ["viewer"]  # default role

    def test_handle_callback_empty_code_raises(self):
        config = _make_oidc_config()
        mgr = SSOManager(config)
        with pytest.raises(ValueError, match="code.*must not be empty"):
            mgr.handle_callback("")

    def test_handle_callback_missing_token_url_raises(self):
        config = _make_oidc_config(token_url="")
        mgr = SSOManager(config)
        with pytest.raises(ValueError, match="token_url is required"):
            mgr.handle_callback("CODE")

    def test_handle_callback_token_endpoint_http_error_raises(self, monkeypatch):
        import urllib.error
        config = _make_oidc_config()
        mgr = SSOManager(config)

        def fake_urlopen(req, timeout=None):
            raise urllib.error.HTTPError(
                url=req.full_url, code=400,
                msg="Bad Request",
                hdrs=None,
                fp=io.BytesIO(b'{"error":"invalid_grant"}'),
            )

        monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)

        with pytest.raises(RuntimeError, match="HTTP 400"):
            mgr.handle_callback("CODE")

    def test_handle_callback_token_endpoint_error_field_raises(self, monkeypatch):
        config = _make_oidc_config()
        mgr = SSOManager(config)

        error_payload = json.dumps({
            "error": "invalid_grant",
            "error_description": "The authorization code is invalid",
        }).encode()

        def fake_urlopen(req, timeout=None):
            return _FakeResponse(error_payload)

        monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)

        with pytest.raises(RuntimeError, match="invalid_grant"):
            mgr.handle_callback("CODE")

    def test_handle_callback_non_json_response_raises(self, monkeypatch):
        config = _make_oidc_config()
        mgr = SSOManager(config)

        def fake_urlopen(req, timeout=None):
            return _FakeResponse(b"<html>Not Found</html>")

        monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)

        with pytest.raises(RuntimeError, match="non-JSON"):
            mgr.handle_callback("CODE")

    def test_handle_callback_url_error_raises_runtime(self, monkeypatch):
        import urllib.error
        config = _make_oidc_config()
        mgr = SSOManager(config)

        def fake_urlopen(req, timeout=None):
            raise urllib.error.URLError("connection refused")

        monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)

        with pytest.raises(RuntimeError, match="unreachable"):
            mgr.handle_callback("CODE")

    def test_handle_callback_non_numeric_expires_in_defaults(self, monkeypatch):
        config = _make_oidc_config()
        mgr = SSOManager(config)

        token_payload = json.dumps({
            "access_token": "AT",
            "expires_in": "not-a-number",
        }).encode()

        def fake_urlopen(req, timeout=None):
            return _FakeResponse(token_payload)

        monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)

        session = mgr.handle_callback("CODE")
        # Default 3600 applied.
        assert abs((session.expires_at - session.created_at) - 3600) < 5


# ── handle_callback SAML — 通过 SSOManager 集成测试 ─────────────────


class TestHandleCallbackSAML:
    def test_saml_callback_with_valid_response_returns_session(self):
        # SAML 回调：handle_callback 接收 base64 SAMLResponse，返回真实 SSOSession。
        key, cert_b64 = _generate_test_cert()
        config = _make_saml_config(saml_idp_cert=cert_b64)
        mgr = SSOManager(config)

        saml_response_b64 = _build_signed_saml_response(key)
        session = mgr.handle_callback(saml_response_b64, state="relay-state")

        assert isinstance(session, SSOSession)
        assert session.user.provider == SSOProvider.SAML
        assert session.user.external_id == "saml:testuser@example.com"
        assert session.user.email == "testuser@example.com"
        assert session.user.display_name == "Test User"
        assert session.user.roles == ["admin", "viewer"]
        assert session.session_id.startswith("sess_")

    def test_saml_empty_code_still_raises(self):
        # Even SAML must reject empty code (fail-closed).
        config = _make_saml_config()
        mgr = SSOManager(config)
        with pytest.raises(ValueError):
            mgr.handle_callback("")

    def test_saml_callback_invalid_signature_rejected(self):
        # 签名篡改后，handle_callback 应抛 SSOError。
        key, cert_b64 = _generate_test_cert()
        config = _make_saml_config(saml_idp_cert=cert_b64)
        mgr = SSOManager(config)

        saml_response_b64 = _build_signed_saml_response(key, tamper_signature=True)
        with pytest.raises(SSOError, match="signature|digest|verification"):
            mgr.handle_callback(saml_response_b64)


# ── SAMLHandler 单元测试 ────────────────────────────────────────────


class TestSAMLHandler:
    """测试 SAMLHandler 的核心功能：AuthnRequest 构造、Response 验证。"""

    def test_get_authorize_url_returns_redirect_url(self, monkeypatch):
        # get_authorize_url 应返回 {sso_url}?SAMLRequest=...&RelayState=...
        from maop.enterprise.saml_handler import SAMLHandler

        _key, cert_b64 = _generate_test_cert()
        config = _make_saml_config(saml_idp_cert=cert_b64)
        handler = SAMLHandler(config)

        # Monkeypatch _get_idp_metadata 以避免网络请求，直接返回带 sso_url 的 metadata
        handler._idp_metadata = {
            "entity_id": "https://idp.example.com",
            "sso_url": "https://idp.example.com/sso",
            "slo_url": "https://idp.example.com/slo",
            "x509_cert": cert_b64,
        }

        url = handler.get_authorize_url(state="relay123")

        assert url.startswith("https://idp.example.com/sso?SAMLRequest=")
        assert "RelayState=relay123" in url
        # SAMLRequest 参数非空
        from urllib.parse import parse_qs, urlparse
        parsed = urlparse(url)
        params = parse_qs(parsed.query)
        assert "SAMLRequest" in params
        assert len(params["SAMLRequest"][0]) > 0

    def test_get_authorize_url_without_state(self, monkeypatch):
        # 不传 state 时，URL 不应包含 RelayState
        from maop.enterprise.saml_handler import SAMLHandler

        _key, cert_b64 = _generate_test_cert()
        config = _make_saml_config(saml_idp_cert=cert_b64)
        handler = SAMLHandler(config)
        handler._idp_metadata = {
            "entity_id": "https://idp.example.com",
            "sso_url": "https://idp.example.com/sso",
            "slo_url": "",
            "x509_cert": cert_b64,
        }

        url = handler.get_authorize_url()
        assert "SAMLRequest=" in url
        assert "RelayState" not in url

    def test_handle_valid_response_returns_session(self):
        # 有效签名的 SAML Response 应返回 SSOSession
        from maop.enterprise.saml_handler import SAMLHandler

        key, cert_b64 = _generate_test_cert()
        config = _make_saml_config(
            saml_idp_cert=cert_b64,
            saml_entity_id="maop-sp",
        )
        handler = SAMLHandler(config)

        saml_response_b64 = _build_signed_saml_response(key, audience="maop-sp")
        session = handler.handle_response(saml_response_b64, relay_state="state")

        assert isinstance(session, SSOSession)
        assert session.user.provider == SSOProvider.SAML
        assert session.user.external_id == "saml:testuser@example.com"
        assert session.user.email == "testuser@example.com"
        assert session.user.display_name == "Test User"
        assert session.user.roles == ["admin", "viewer"]
        # session 应有过期时间（来自 Conditions.NotOnOrAfter 或默认 8h）
        assert session.expires_at > session.created_at

    def test_handle_response_invalid_signature_rejected(self):
        # 签名篡改后应抛 SSOError（fail-closed）
        from maop.enterprise.saml_handler import SAMLHandler

        key, cert_b64 = _generate_test_cert()
        config = _make_saml_config(saml_idp_cert=cert_b64)
        handler = SAMLHandler(config)

        saml_response_b64 = _build_signed_saml_response(key, tamper_signature=True)
        with pytest.raises(SSOError, match="signature|digest|verification"):
            handler.handle_response(saml_response_b64)

    def test_handle_response_wrong_cert_rejected(self):
        # 用 key A 签名，但配置 key B 的证书 → 验证失败
        from maop.enterprise.saml_handler import SAMLHandler

        key_a, _ = _generate_test_cert()
        _, cert_b64_b = _generate_test_cert()  # 不同的密钥对
        config = _make_saml_config(saml_idp_cert=cert_b64_b)
        handler = SAMLHandler(config)

        saml_response_b64 = _build_signed_saml_response(key_a)
        with pytest.raises(SSOError, match="signature|digest|verification"):
            handler.handle_response(saml_response_b64)

    def test_handle_response_expired_rejected(self):
        # NotOnOrAfter 已过期 → 抛 SSOError
        from maop.enterprise.saml_handler import SAMLHandler

        key, cert_b64 = _generate_test_cert()
        config = _make_saml_config(saml_idp_cert=cert_b64)
        handler = SAMLHandler(config)

        now = datetime.datetime.now(datetime.timezone.utc)
        saml_response_b64 = _build_signed_saml_response(
            key,
            not_before=now - datetime.timedelta(hours=2),
            not_on_or_after=now - datetime.timedelta(hours=1),  # 1 小时前过期
        )
        with pytest.raises(SSOError, match="NotOnOrAfter.*passed|expired"):
            handler.handle_response(saml_response_b64)

    def test_handle_response_future_notbefore_rejected(self):
        # NotBefore 在未来 → 抛 SSOError
        from maop.enterprise.saml_handler import SAMLHandler

        key, cert_b64 = _generate_test_cert()
        config = _make_saml_config(saml_idp_cert=cert_b64)
        handler = SAMLHandler(config)

        now = datetime.datetime.now(datetime.timezone.utc)
        saml_response_b64 = _build_signed_saml_response(
            key,
            not_before=now + datetime.timedelta(hours=1),  # 1 小时后才生效
            not_on_or_after=now + datetime.timedelta(hours=2),
        )
        with pytest.raises(SSOError, match="NotBefore.*future"):
            handler.handle_response(saml_response_b64)

    def test_handle_response_wrong_audience_rejected(self):
        # Audience 不匹配 → 抛 SSOError
        from maop.enterprise.saml_handler import SAMLHandler

        key, cert_b64 = _generate_test_cert()
        # SP entity_id 是 "maop-sp"，但 Response 中 Audience 是 "other-sp"
        config = _make_saml_config(saml_idp_cert=cert_b64, saml_entity_id="maop-sp")
        handler = SAMLHandler(config)

        saml_response_b64 = _build_signed_saml_response(key, audience="other-sp")
        with pytest.raises(SSOError, match="Audience mismatch"):
            handler.handle_response(saml_response_b64)

    def test_handle_response_empty_rejected(self):
        from maop.enterprise.saml_handler import SAMLHandler

        _key, cert_b64 = _generate_test_cert()
        config = _make_saml_config(saml_idp_cert=cert_b64)
        handler = SAMLHandler(config)

        with pytest.raises(SSOError, match="empty"):
            handler.handle_response("")

    def test_handle_response_malformed_xml_rejected(self):
        from maop.enterprise.saml_handler import SAMLHandler

        _key, cert_b64 = _generate_test_cert()
        config = _make_saml_config(saml_idp_cert=cert_b64)
        handler = SAMLHandler(config)

        bad_b64 = base64.b64encode(b"<not valid xml<").decode("ascii")
        with pytest.raises(SSOError, match="XML parse|parse failed"):
            handler.handle_response(bad_b64)

    def test_handle_response_missing_assertion_rejected(self):
        from lxml import etree

        from maop.enterprise.saml_handler import SAMLHandler

        _key, cert_b64 = _generate_test_cert()
        config = _make_saml_config(saml_idp_cert=cert_b64)
        handler = SAMLHandler(config)

        # 构造一个没有 Assertion 的 Response
        now = datetime.datetime.now(datetime.timezone.utc)
        issue_instant = now.strftime("%Y-%m-%dT%H:%M:%SZ")
        response = etree.Element(
            f"{{{_SAMLP_NS}}}Response",
            nsmap={"samlp": _SAMLP_NS, "saml": _SAML_NS},
            attrib={
                "ID": "_response_1",
                "IssueInstant": issue_instant,
                "Version": "2.0",
            },
        )
        response_xml = etree.tostring(response, encoding="utf-8")
        response_b64 = base64.b64encode(response_xml).decode("ascii")

        with pytest.raises(SSOError, match="missing.*Assertion"):
            handler.handle_response(response_b64)

    def test_parse_idp_metadata_extracts_sso_url_and_cert(self):
        # 测试 _parse_idp_metadata 能从 metadata XML 提取 SSO URL 和证书
        from maop.enterprise.saml_handler import SAMLHandler

        _key, cert_b64 = _generate_test_cert()
        config = _make_saml_config(saml_idp_cert=cert_b64)
        handler = SAMLHandler(config)

        # 构造 IdP metadata XML
        metadata_xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<md:EntityDescriptor xmlns:md="urn:oasis:names:tc:SAML:2.0:metadata"
    xmlns:ds="http://www.w3.org/2000/09/xmldsig#"
    entityID="https://idp.example.com">
  <md:IDPSSODescriptor protocolSupportEnumeration="urn:oasis:names:tc:SAML:2.0:protocol">
    <md:KeyDescriptor use="signing">
      <ds:KeyInfo>
        <ds:X509Certificate>{cert_b64}</ds:X509Certificate>
      </ds:KeyInfo>
    </md:KeyDescriptor>
    <md:SingleSignOnService Binding="urn:oasis:names:tc:SAML:2.0:bindings:HTTP-Redirect"
      Location="https://idp.example.com/sso/redirect"/>
    <md:SingleSignOnService Binding="urn:oasis:names:tc:SAML:2.0:bindings:HTTP-POST"
      Location="https://idp.example.com/sso/post"/>
    <md:SingleLogoutService Binding="urn:oasis:names:tc:SAML:2.0:bindings:HTTP-Redirect"
      Location="https://idp.example.com/slo"/>
  </md:IDPSSODescriptor>
</md:EntityDescriptor>""".encode()

        parsed = handler._parse_idp_metadata(metadata_xml)
        assert parsed["entity_id"] == "https://idp.example.com"
        # 应优先选择 Redirect binding
        assert parsed["sso_url"] == "https://idp.example.com/sso/redirect"
        assert parsed["slo_url"] == "https://idp.example.com/slo"
        assert parsed["x509_cert"] == cert_b64


# ── _roles_from_claims ────────────────────────────────────────────────


class TestRolesFromClaims:
    def test_roles_from_list(self):
        mgr = SSOManager(_make_oidc_config())
        assert mgr._roles_from_claims({"roles": ["admin", "ops"]}) == ["admin", "ops"]

    def test_role_from_string(self):
        mgr = SSOManager(_make_oidc_config())
        assert mgr._roles_from_claims({"role": "editor"}) == ["editor"]

    def test_groups_fallback(self):
        mgr = SSOManager(_make_oidc_config())
        assert mgr._roles_from_claims({"groups": ["g1", "g2"]}) == ["g1", "g2"]

    def test_no_roles_returns_empty(self):
        mgr = SSOManager(_make_oidc_config())
        assert mgr._roles_from_claims({"sub": "x"}) == []

    def test_empty_list_returns_empty(self):
        mgr = SSOManager(_make_oidc_config())
        assert mgr._roles_from_claims({"roles": []}) == []


# ── get_authorize_url ─────────────────────────────────────────────────


class TestGetAuthorizeUrl:
    def test_oidc_authorize_url_includes_params(self):
        mgr = SSOManager(_make_oidc_config())
        url = mgr.get_authorize_url(state="rand123")
        assert url.startswith("https://idp.example.com/authorize?")
        assert "client_id=test-client-id" in url
        assert "response_type=code" in url
        # get_authorize_url joins scopes with a raw space (not URL-encoded).
        assert "scope=openid profile email" in url
        assert "state=rand123" in url

    def test_saml_authorize_url_returns_redirect_url(self, monkeypatch):
        # SAML get_authorize_url 应返回带 SAMLRequest 参数的重定向 URL。
        _key, cert_b64 = _generate_test_cert()
        config = _make_saml_config(saml_idp_cert=cert_b64)
        mgr = SSOManager(config)

        # Monkeypatch SAMLHandler._get_idp_metadata 以避免网络请求
        from maop.enterprise import saml_handler as _sh_module

        def _mock_get_idp_metadata(self):
            return {
                "entity_id": "https://idp.example.com",
                "sso_url": "https://idp.example.com/sso",
                "slo_url": "",
                "x509_cert": cert_b64,
            }

        monkeypatch.setattr(
            _sh_module.SAMLHandler, "_get_idp_metadata", _mock_get_idp_metadata
        )

        url = mgr.get_authorize_url(state="relay-state")
        assert url.startswith("https://idp.example.com/sso?SAMLRequest=")
        assert "RelayState=relay-state" in url


# ── validate_session / logout ─────────────────────────────────────────


class TestSessionLifecycle:
    def test_validate_session_returns_session(self, monkeypatch):
        mgr = SSOManager(_make_oidc_config())
        token_payload = json.dumps({
            "access_token": "AT", "expires_in": 3600,
        }).encode()
        monkeypatch.setattr(
            "urllib.request.urlopen",
            lambda req, timeout=None: _FakeResponse(token_payload),
        )
        session = mgr.handle_callback("CODE")
        assert mgr.validate_session(session.session_id) is not None

    def test_validate_expired_session_returns_none(self, monkeypatch):
        mgr = SSOManager(_make_oidc_config())
        token_payload = json.dumps({
            "access_token": "AT", "expires_in": 3600,
        }).encode()
        monkeypatch.setattr(
            "urllib.request.urlopen",
            lambda req, timeout=None: _FakeResponse(token_payload),
        )
        session = mgr.handle_callback("CODE")
        # Force expiry by setting expires_at to a clearly past timestamp.
        # (Using 0.0 would be treated as "no expiry set" by validate_session's
        # truthy guard, so use time.time() - 100 instead.)
        session.expires_at = time.time() - 100
        assert mgr.validate_session(session.session_id) is None

    def test_logout_removes_session(self, monkeypatch):
        mgr = SSOManager(_make_oidc_config())
        token_payload = json.dumps({"access_token": "AT", "expires_in": 3600}).encode()
        monkeypatch.setattr(
            "urllib.request.urlopen",
            lambda req, timeout=None: _FakeResponse(token_payload),
        )
        session = mgr.handle_callback("CODE")
        assert mgr.logout(session.session_id) is True
        assert mgr.validate_session(session.session_id) is None
        assert mgr.logout(session.session_id) is False
