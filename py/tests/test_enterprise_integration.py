"""Enterprise integration tests — SAML crypto, CRL HTTP, RabbitMQ/etcd contracts.

These tests exercise real cryptographic operations and local HTTP servers
to validate enterprise features beyond simple mocking.
"""

from __future__ import annotations

import base64
import datetime
import hashlib
import http.server
import json
import threading
import time
from pathlib import Path
from typing import Any

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

from lxml import etree


# ═══════════════════════════════════════════════════════════════════════
# Helpers: generate real X.509 cert + RSA key for SAML tests
# ═══════════════════════════════════════════════════════════════════════

def _generate_test_cert() -> tuple[bytes, Any]:
    """Generate a self-signed X.509 cert + RSA private key for testing."""
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = issuer = x509.Name([
        x509.NameAttribute(NameOID.COMMON_NAME, "test-idp.example.com"),
        x509.NameAttribute(NameOID.ORGANIZATION_NAME, "Test IdP"),
    ])
    now = datetime.datetime.now(datetime.timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(days=1))
        .not_valid_after(now + datetime.timedelta(days=365))
        .sign(key, hashes.SHA256())
    )
    cert_der = cert.public_bytes(serialization.Encoding.DER)
    return cert_der, key


def _build_saml_response(
    cert_der: bytes,
    private_key: Any,
    *,
    audience: str = "maop-sp",
    subject: str = "user@example.com",
    not_before: datetime.datetime | None = None,
    not_after: datetime.datetime | None = None,
) -> str:
    """Build a signed SAML Response XML string using real crypto."""
    from cryptography.hazmat.primitives.asymmetric import padding as asym_padding

    now = datetime.datetime.now(datetime.timezone.utc)
    nb = (not_before or now - datetime.timedelta(minutes=5)).strftime("%Y-%m-%dT%H:%M:%SZ")
    na = (not_after or now + datetime.timedelta(minutes=10)).strftime("%Y-%m-%dT%H:%M:%SZ")
    issue_instant = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    response_id = f"_resp-{hashlib.sha256(str(time.time()).encode()).hexdigest()[:16]}"
    assertion_id = f"_assert-{hashlib.sha256(str(time.time() + 1).encode()).hexdigest()[:16]}"

    assertion_xml = f'''<saml:Assertion xmlns:saml="urn:oasis:names:tc:SAML:2.0:assertion" ID="{assertion_id}" IssueInstant="{issue_instant}" Version="2.0">
  <saml:Issuer>https://idp.example.com</saml:Issuer>
  <saml:Subject>
    <saml:NameID Format="urn:oasis:names:tc:SAML:1.1:nameid-format:emailAddress">{subject}</saml:NameID>
    <saml:SubjectConfirmation Method="urn:oasis:names:tc:SAML:2.0:cm:bearer">
      <saml:SubjectConfirmationData NotOnOrAfter="{na}" Recipient="https://sp.example.com/acs"/>
    </saml:SubjectConfirmation>
  </saml:Subject>
  <saml:Conditions NotBefore="{nb}" NotOnOrAfter="{na}">
    <saml:AudienceRestriction>
      <saml:Audience>{audience}</saml:Audience>
    </saml:AudienceRestriction>
  </saml:Conditions>
  <saml:AuthnStatement AuthnInstant="{issue_instant}" SessionIndex="{assertion_id}">
    <saml:AuthnContext>
      <saml:AuthnContextClassRef>urn:oasis:names:tc:SAML:2.0:ac:classes:PasswordProtectedTransport</saml:AuthnContextClassRef>
    </saml:AuthnContext>
  </saml:AuthnStatement>
  <saml:AttributeStatement>
    <saml:Attribute Name="email">
      <saml:AttributeValue>{subject}</saml:AttributeValue>
    </saml:Attribute>
  </saml:AttributeStatement>
</saml:Assertion>'''

    response_xml = f'''<samlp:Response xmlns:samlp="urn:oasis:names:tc:SAML:2.0:protocol" xmlns:saml="urn:oasis:names:tc:SAML:2.0:assertion" ID="{response_id}" IssueInstant="{issue_instant}" Version="2.0">
  <saml:Issuer>https://idp.example.com</saml:Issuer>
  <samlp:Status>
    <samlp:StatusCode Value="urn:oasis:names:tc:SAML:2.0:status:Success"/>
  </samlp:Status>
  {assertion_xml}
</samlp:Response>'''

    root = etree.fromstring(response_xml.encode())
    assertion_elem = root.find(".//{urn:oasis:names:tc:SAML:2.0:assertion}Assertion")

    c14n_assertion = etree.tostring(assertion_elem, method="c14n2", exclusive=True)
    digest_value = base64.b64encode(hashlib.sha256(c14n_assertion).digest()).decode()

    signed_info_xml = f'''<ds:SignedInfo xmlns:ds="http://www.w3.org/2000/09/xmldsig#">
  <ds:CanonicalizationMethod Algorithm="http://www.w3.org/2001/10/xml-exc-c14n#"/>
  <ds:SignatureMethod Algorithm="http://www.w3.org/2001/04/xmldsig-more#rsa-sha256"/>
  <ds:Reference URI="#{assertion_id}">
    <ds:Transforms>
      <ds:Transform Algorithm="http://www.w3.org/2000/09/xmldsig#enveloped-signature"/>
      <ds:Transform Algorithm="http://www.w3.org/2001/10/xml-exc-c14n#"/>
    </ds:Transforms>
    <ds:DigestMethod Algorithm="http://www.w3.org/2001/04/xmlenc#sha256"/>
    <ds:DigestValue>{digest_value}</ds:DigestValue>
  </ds:Reference>
</ds:SignedInfo>'''

    signed_info_elem = etree.fromstring(signed_info_xml.encode())
    c14n_signed_info = etree.tostring(signed_info_elem, method="c14n2", exclusive=True)
    signature_value = base64.b64encode(
        private_key.sign(c14n_signed_info, asym_padding.PKCS1v15(), hashes.SHA256())
    ).decode()

    cert_b64 = base64.b64encode(cert_der).decode()
    signature_xml = f'''<ds:Signature xmlns:ds="http://www.w3.org/2000/09/xmldsig#">
  {signed_info_xml}
  <ds:SignatureValue>{signature_value}</ds:SignatureValue>
  <ds:KeyInfo>
    <ds:X509Data>
      <ds:X509Certificate>{cert_b64}</ds:X509Certificate>
    </ds:X509Data>
  </ds:KeyInfo>
</ds:Signature>'''

    signature_elem = etree.fromstring(signature_xml.encode())
    issuer_elem = assertion_elem.find("{urn:oasis:names:tc:SAML:2.0:assertion}Issuer")
    issuer_idx = list(assertion_elem).index(issuer_elem)
    assertion_elem.insert(issuer_idx + 1, signature_elem)

    return etree.tostring(root, xml_declaration=False, encoding="unicode")


# ═══════════════════════════════════════════════════════════════════════
# SAML Integration Tests (real crypto)
# ═══════════════════════════════════════════════════════════════════════


class TestSAMLIntegration:
    """Integration tests using real X.509 certs and RSA signatures."""

    @pytest.fixture
    def idp_cert(self):
        cert_der, private_key = _generate_test_cert()
        return cert_der, private_key

    def test_build_saml_response_produces_valid_xml(self, idp_cert):
        """Verify our test helper produces well-formed SAML XML."""
        cert_der, key = idp_cert
        xml_str = _build_saml_response(cert_der, key)
        root = etree.fromstring(xml_str.encode())
        assert root.tag == "{urn:oasis:names:tc:SAML:2.0:protocol}Response"
        assertion = root.find(".//{urn:oasis:names:tc:SAML:2.0:assertion}Assertion")
        assert assertion is not None
        sig = assertion.find(".//{http://www.w3.org/2000/09/xmldsig#}Signature")
        assert sig is not None

    def test_saml_handler_parses_real_signed_response(self, idp_cert):
        """SAMLHandler.handle_response() should parse a real signed response."""
        from maop.enterprise.saml_handler import SAMLHandler
        from maop.enterprise.sso import SSOConfig

        cert_der, key = idp_cert
        cert_b64 = base64.b64encode(cert_der).decode()

        config = SSOConfig(
            provider="saml",
            client_id="maop-sp",
            authorize_url="https://idp.example.com/sso",
            token_url="",
            userinfo_url="",
            saml_idp_metadata_url="",
            saml_idp_cert=cert_b64,
            saml_sp_entity_id="maop-sp",
            saml_acs_url="https://sp.example.com/acs",
        )
        handler = SAMLHandler(config)

        xml_str = _build_saml_response(cert_der, key, audience="maop-sp")
        b64_response = base64.b64encode(xml_str.encode()).decode()

        session = handler.handle_response(b64_response, relay_state="")
        assert session is not None
        assert session.user.email == "user@example.com"

    def test_saml_handler_rejects_tampered_response(self, idp_cert):
        """Tampering with the response should cause signature verification failure."""
        from maop.enterprise.saml_handler import SAMLHandler
        from maop.enterprise.sso import SSOConfig, SSOError

        cert_der, key = idp_cert
        cert_b64 = base64.b64encode(cert_der).decode()

        config = SSOConfig(
            provider="saml",
            client_id="maop-sp",
            authorize_url="https://idp.example.com/sso",
            token_url="",
            userinfo_url="",
            saml_idp_metadata_url="",
            saml_idp_cert=cert_b64,
            saml_sp_entity_id="maop-sp",
            saml_acs_url="https://sp.example.com/acs",
        )
        handler = SAMLHandler(config)

        xml_str = _build_saml_response(cert_der, key, audience="maop-sp")
        xml_str = xml_str.replace("user@example.com", "attacker@evil.com")
        b64_response = base64.b64encode(xml_str.encode()).decode()

        with pytest.raises(SSOError):
            handler.handle_response(b64_response, relay_state="")

    def test_saml_response_with_wrong_audience_rejected(self, idp_cert):
        """Response with wrong audience should be rejected."""
        from maop.enterprise.saml_handler import SAMLHandler
        from maop.enterprise.sso import SSOConfig, SSOError

        cert_der, key = idp_cert
        cert_b64 = base64.b64encode(cert_der).decode()

        config = SSOConfig(
            provider="saml",
            client_id="maop-sp",
            authorize_url="https://idp.example.com/sso",
            token_url="",
            userinfo_url="",
            saml_idp_metadata_url="",
            saml_idp_cert=cert_b64,
            saml_sp_entity_id="maop-sp",
            saml_acs_url="https://sp.example.com/acs",
        )
        handler = SAMLHandler(config)

        xml_str = _build_saml_response(cert_der, key, audience="wrong-audience")
        b64_response = base64.b64encode(xml_str.encode()).decode()

        with pytest.raises(SSOError):
            handler.handle_response(b64_response, relay_state="")


# ═══════════════════════════════════════════════════════════════════════
# CRL Integration Tests (real local HTTP server)
# ═══════════════════════════════════════════════════════════════════════


class _CRLHandler(http.server.BaseHTTPRequestHandler):
    """Simple HTTP handler that serves a CRL JSON response."""

    crl_data: dict[str, Any] = {
        "revoked": [
            {"customer": "revoked-corp", "reason": "license_violation", "revoked_at": "2026-01-01T00:00:00Z"}
        ],
        "issued_at": "2026-01-01T00:00:00Z",
    }

    def do_GET(self):
        if self.path == "/crl":
            body = json.dumps(self.crl_data).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, format, *args):
        pass


class TestCRLIntegration:
    """Integration tests using a real local HTTP server for CRL."""

    @pytest.fixture
    def crl_server(self, tmp_path):
        """Start a local HTTP server serving CRL data."""
        server = http.server.HTTPServer(("127.0.0.1", 0), _CRLHandler)
        port = server.server_address[1]
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        yield f"http://127.0.0.1:{port}/crl", tmp_path
        server.shutdown()

    def test_crl_fetch_from_real_http_server(self, crl_server):
        """CRLChecker should fetch and parse CRL from a real HTTP endpoint."""
        from maop.enterprise.crl import CRLChecker

        url, cache_dir = crl_server
        checker = CRLChecker(crl_url=url, cache_path=cache_dir / "crl.json", cache_ttl_s=60)
        is_revoked, reason = checker.is_revoked("valid-corp")
        assert is_revoked is False

    def test_crl_revoked_customer_rejected(self, crl_server):
        """A revoked customer should be detected."""
        from maop.enterprise.crl import CRLChecker

        url, cache_dir = crl_server
        checker = CRLChecker(crl_url=url, cache_path=cache_dir / "crl.json", cache_ttl_s=60)
        is_revoked, reason = checker.is_revoked("revoked-corp")
        assert is_revoked is True
        assert reason == "license_violation"

    def test_crl_cache_prevents_refetch(self, crl_server):
        """Second check should use cache, not re-fetch."""
        from maop.enterprise.crl import CRLChecker

        url, cache_dir = crl_server
        checker = CRLChecker(crl_url=url, cache_path=cache_dir / "crl.json", cache_ttl_s=300)
        checker.is_revoked("valid-corp")
        # Modify server data - cache should still return old result
        _CRLHandler.crl_data = {
            "revoked": [{"customer": "valid-corp", "reason": "new_violation", "revoked_at": "2026-01-01T00:00:00Z"}],
            "issued_at": "2026-01-01T00:00:00Z",
        }
        is_revoked, _ = checker.is_revoked("valid-corp")
        assert is_revoked is False  # Cached: still valid


# ═══════════════════════════════════════════════════════════════════════
# RabbitMQ / etcd Contract Tests (verify API surface)
# ═══════════════════════════════════════════════════════════════════════


class TestBackendContracts:
    """Verify backends implement the required API contracts."""

    def test_rabbitmq_backend_has_required_methods(self):
        """RabbitMQQueueBackend must implement publish/consume/ack/nack/topic_stats."""
        import ast
        source = (Path(__file__).parent.parent / "maop" / "core" / "backends_rabbitmq.py").read_text()
        tree = ast.parse(source)
        classes = [n for n in ast.walk(tree) if isinstance(n, ast.ClassDef) and n.name == "RabbitMQQueueBackend"]
        assert len(classes) == 1
        methods = {n.name for n in ast.walk(classes[0]) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
        required = {"publish", "consume", "ack", "nack", "topic_stats"}
        assert required.issubset(methods), f"Missing methods: {required - methods}"

    def test_etcd_backend_has_required_methods(self):
        """EtcdKVBackend must implement get/set/delete/list_keys/cas."""
        import ast
        source = (Path(__file__).parent.parent / "maop" / "core" / "backends_distributed.py").read_text()
        tree = ast.parse(source)
        classes = [n for n in ast.walk(tree) if isinstance(n, ast.ClassDef) and n.name == "EtcdKVBackend"]
        assert len(classes) == 1
        methods = {n.name for n in ast.walk(classes[0]) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
        required = {"get", "set", "delete", "list_keys", "cas"}
        assert required.issubset(methods), f"Missing methods: {required - methods}"

    def test_rabbitmq_backend_inherits_queue_backend(self):
        """RabbitMQQueueBackend should inherit from QueueBackend."""
        import ast
        source = (Path(__file__).parent.parent / "maop" / "core" / "backends_rabbitmq.py").read_text()
        tree = ast.parse(source)
        classes = [n for n in ast.walk(tree) if isinstance(n, ast.ClassDef) and n.name == "RabbitMQQueueBackend"]
        assert len(classes) == 1
        bases = [b.id if isinstance(b, ast.Name) else getattr(b, 'attr', '') for b in classes[0].bases]
        assert "QueueBackend" in bases

    def test_etcd_backend_inherits_kv_backend(self):
        """EtcdKVBackend should inherit from KVBackend."""
        import ast
        source = (Path(__file__).parent.parent / "maop" / "core" / "backends_distributed.py").read_text()
        tree = ast.parse(source)
        classes = [n for n in ast.walk(tree) if isinstance(n, ast.ClassDef) and n.name == "EtcdKVBackend"]
        assert len(classes) == 1
        bases = [b.id if isinstance(b, ast.Name) else getattr(b, 'attr', '') for b in classes[0].bases]
        assert "KVBackend" in bases
