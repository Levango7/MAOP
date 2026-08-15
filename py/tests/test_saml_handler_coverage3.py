"""Coverage tests (round 3) for enterprise/saml_handler.py — focus on
metadata fetch, cert parsing, signature verification branches, conditions
validation, and attribute extraction edge cases.

Targets missing lines: 138, 208-239, 249-256, 302-317, 393-461, 481-482,
507-592, 659.
"""
from __future__ import annotations

import base64
from unittest.mock import MagicMock, patch

import pytest

# ── Helpers ─────────────────────────────────────────────────────────


def _make_handler(**kwargs):
    from maop.enterprise.saml_handler import SAMLHandler
    from maop.enterprise.sso import SSOConfig
    defaults = {
        "client_id": "sp",
        "client_secret": "secret",
        "redirect_uri": "https://sp.example.com/acs",
        "saml_entity_id": "maop-sp",
        "saml_acs_url": "https://sp.example.com/acs",
    }
    defaults.update(kwargs)
    cfg = SSOConfig(**defaults)
    return SAMLHandler(cfg)


def _b64(s: str) -> str:
    return base64.b64encode(s.encode()).decode()


# ── _get_idp_metadata with direct cert + metadata URL (208-217) ─────


class TestGetIdpMetadataWithUrl:
    def test_direct_cert_with_metadata_url_success(self):
        """Cover branch where direct cert + metadata_url fetch succeeds (208-215)."""
        handler = _make_handler(
            saml_idp_cert="dummy-cert",
            saml_metadata_url="https://idp.example.com/metadata",
        )
        mock_metadata = b"""<?xml version="1.0"?>
<EntityDescriptor xmlns="urn:oasis:names:tc:SAML:2.0:metadata" entityID="https://idp.example.com">
  <IDPSSODescriptor>
    <SingleSignOnService Binding="urn:oasis:names:tc:SAML:2.0:bindings:HTTP-Redirect" Location="https://idp.example.com/sso"/>
    <KeyDescriptor use="signing">
      <KeyInfo xmlns="http://www.w3.org/2000/09/xmldsig#">
        <X509Data><X509Certificate>certdata</X509Certificate></X509Data>
      </KeyInfo>
    </KeyDescriptor>
  </IDPSSODescriptor>
</EntityDescriptor>"""
        with patch.object(handler, "_fetch_idp_metadata", return_value=mock_metadata):
            meta = handler._get_idp_metadata()
        assert meta["sso_url"] == "https://idp.example.com/sso"
        assert meta["x509_cert"] == "dummy-cert"  # direct cert takes priority

    def test_direct_cert_with_metadata_url_fetch_fails(self):
        """Cover branch where metadata fetch fails with SSOError (216-217)."""
        from maop.enterprise.sso import SSOError
        handler = _make_handler(
            saml_idp_cert="dummy-cert",
            saml_metadata_url="https://idp.example.com/metadata",
        )
        with patch.object(handler, "_fetch_idp_metadata", side_effect=SSOError("fetch failed")):
            meta = handler._get_idp_metadata()
        # Should fall back to direct cert config
        assert meta["x509_cert"] == "dummy-cert"
        assert meta["sso_url"] == ""  # no sso_url from failed fetch


# ── _get_idp_metadata without direct cert (222-223) ────────────────


class TestGetIdpMetadataFromUrl:
    def test_fetch_from_metadata_url(self):
        handler = _make_handler(
            saml_metadata_url="https://idp.example.com/metadata",
        )
        mock_metadata = b"""<?xml version="1.0"?>
<EntityDescriptor xmlns="urn:oasis:names:tc:SAML:2.0:metadata" entityID="https://idp.example.com">
  <IDPSSODescriptor>
    <SingleSignOnService Binding="urn:oasis:names:tc:SAML:2.0:bindings:HTTP-Redirect" Location="https://idp.example.com/sso"/>
    <KeyDescriptor use="signing">
      <KeyInfo xmlns="http://www.w3.org/2000/09/xmldsig#">
        <X509Data><X509Certificate>certdata</X509Certificate></X509Data>
      </KeyInfo>
    </KeyDescriptor>
  </IDPSSODescriptor>
</EntityDescriptor>"""
        with patch.object(handler, "_fetch_idp_metadata", return_value=mock_metadata):
            meta = handler._get_idp_metadata()
        assert meta["sso_url"] == "https://idp.example.com/sso"
        assert meta["x509_cert"] == "certdata"


# ── _get_idp_cert_b64 from metadata (233-239) ──────────────────────


class TestGetIdpCertFromMetadata:
    def test_cert_from_metadata(self):
        """Cover branch where cert comes from metadata (233-239)."""
        handler = _make_handler()
        handler._idp_metadata = {
            "entity_id": "idp",
            "sso_url": "https://idp/sso",
            "x509_cert": "metadata-cert",
        }
        cert = handler._get_idp_cert_b64()
        assert cert == "metadata-cert"


# ── _fetch_idp_metadata with URL (249-256) ─────────────────────────


class TestFetchIdpMetadata:
    def test_fetch_success(self):
        handler = _make_handler(saml_metadata_url="https://idp.example.com/metadata")
        mock_resp = MagicMock()
        mock_resp.read.return_value = b"<xml>metadata</xml>"
        mock_resp.__enter__ = MagicMock(return_value=mock_resp)
        mock_resp.__exit__ = MagicMock(return_value=False)
        with patch("urllib.request.urlopen", return_value=mock_resp):
            result = handler._fetch_idp_metadata()
        assert result == b"<xml>metadata</xml>"

    def test_fetch_url_error(self):
        """Cover URLError branch (255-258)."""
        import urllib.error

        from maop.enterprise.sso import SSOError
        handler = _make_handler(saml_metadata_url="https://idp.example.com/metadata")
        with patch("urllib.request.urlopen", side_effect=urllib.error.URLError("conn refused")):  # noqa: SIM117
            with pytest.raises(SSOError, match="Failed to fetch"):
                handler._fetch_idp_metadata()


# ── _parse_idp_metadata edge cases (302, 309-317) ──────────────────


class TestParseIdpMetadataEdgeCases:
    def test_non_signing_key_descriptor_skipped(self):
        """Cover branch where KeyDescriptor use != 'signing' (302)."""
        handler = _make_handler()
        xml = b"""<?xml version="1.0"?>
<EntityDescriptor xmlns="urn:oasis:names:tc:SAML:2.0:metadata" entityID="https://idp">
  <IDPSSODescriptor>
    <SingleSignOnService Binding="urn:oasis:names:tc:SAML:2.0:bindings:HTTP-Redirect" Location="https://idp/sso"/>
    <KeyDescriptor use="encryption">
      <KeyInfo xmlns="http://www.w3.org/2000/09/xmldsig#">
        <X509Data><X509Certificate>enccert</X509Certificate></X509Data>
      </KeyInfo>
    </KeyDescriptor>
    <KeyDescriptor use="signing">
      <KeyInfo xmlns="http://www.w3.org/2000/09/xmldsig#">
        <X509Data><X509Certificate>signcert</X509Certificate></X509Data>
      </KeyInfo>
    </KeyDescriptor>
  </IDPSSODescriptor>
</EntityDescriptor>"""
        parsed = handler._parse_idp_metadata(xml)
        assert parsed["x509_cert"] == "signcert"

    def test_fallback_x509_certificate(self):
        """Cover fallback when no KeyDescriptor (308-312)."""
        handler = _make_handler()
        xml = b"""<?xml version="1.0"?>
<EntityDescriptor xmlns="urn:oasis:names:tc:SAML:2.0:metadata" entityID="https://idp">
  <IDPSSODescriptor>
    <SingleSignOnService Binding="urn:oasis:names:tc:SAML:2.0:bindings:HTTP-Redirect" Location="https://idp/sso"/>
    <KeyInfo xmlns="http://www.w3.org/2000/09/xmldsig#">
      <X509Data><X509Certificate>fallbackcert</X509Certificate></X509Data>
    </KeyInfo>
  </IDPSSODescriptor>
</EntityDescriptor>"""
        parsed = handler._parse_idp_metadata(xml)
        assert parsed["x509_cert"] == "fallbackcert"

    def test_missing_sso_url(self):
        """Cover missing sso_url error (314-315)."""
        from maop.enterprise.sso import SSOError
        handler = _make_handler()
        xml = b"""<?xml version="1.0"?>
<EntityDescriptor xmlns="urn:oasis:names:tc:SAML:2.0:metadata" entityID="https://idp">
  <IDPSSODescriptor>
    <KeyDescriptor use="signing">
      <KeyInfo xmlns="http://www.w3.org/2000/09/xmldsig#">
        <X509Data><X509Certificate>cert</X509Certificate></X509Data>
      </KeyInfo>
    </KeyDescriptor>
  </IDPSSODescriptor>
</EntityDescriptor>"""
        with pytest.raises(SSOError, match="missing SingleSignOnService"):
            handler._parse_idp_metadata(xml)

    def test_missing_x509_cert(self):
        """Cover missing x509_cert error (316-317)."""
        from maop.enterprise.sso import SSOError
        handler = _make_handler()
        xml = b"""<?xml version="1.0"?>
<EntityDescriptor xmlns="urn:oasis:names:tc:SAML:2.0:metadata" entityID="https://idp">
  <IDPSSODescriptor>
    <SingleSignOnService Binding="urn:oasis:names:tc:SAML:2.0:bindings:HTTP-Redirect" Location="https://idp/sso"/>
  </IDPSSODescriptor>
</EntityDescriptor>"""
        with pytest.raises(SSOError, match="missing X509Certificate"):
            handler._parse_idp_metadata(xml)


# ─– handle_response wrong root element (138) ───────────────────────


class TestHandleResponseWrongRoot:
    def test_wrong_root_element(self):
        """Cover branch where root is not samlp:Response (138)."""
        from maop.enterprise.sso import SSOError
        handler = _make_handler(saml_idp_cert="dummy")
        xml = b'<NotAResponse xmlns="urn:oasis:names:tc:SAML:2.0:protocol"/>'
        b64_xml = base64.b64encode(xml).decode()
        with pytest.raises(SSOError, match="Expected samlp:Response"):
            handler.handle_response(b64_xml)


# ─– _verify_signature branches (393-461, 481-482) ──────────────────


class TestVerifySignatureBranches:
    def _patch_cert(self):
        """Patch load_der_x509_certificate to return a mock cert."""
        mock_key = MagicMock()
        mock_cert = MagicMock()
        mock_cert.public_key.return_value = mock_key
        return patch("maop.enterprise.saml_handler.load_der_x509_certificate", return_value=mock_cert)

    def test_cert_parse_failed(self):
        """Cover cert parse failure (393-394)."""
        from maop.enterprise.sso import SSOError
        handler = _make_handler(saml_idp_cert="dummy")
        # Valid base64 but not a valid DER cert
        bad_cert = base64.b64encode(b"not a cert").decode()
        with pytest.raises(SSOError, match="cert parse failed"):
            handler._verify_signature(b"<resp/>", bad_cert)

    def test_xml_parse_failed(self):
        """Cover XML parse failure in verify (400-401)."""
        from maop.enterprise.sso import SSOError
        handler = _make_handler(saml_idp_cert="dummy")
        cert_b64 = base64.b64encode(b"dummy-cert-bytes").decode()
        with self._patch_cert(), pytest.raises(SSOError, match="XML parse failed"):
            handler._verify_signature(b"not valid xml <<<>", cert_b64)

    def test_no_signature_element(self):
        """Cover missing Signature element (406-411)."""
        from maop.enterprise.sso import SSOError
        handler = _make_handler(saml_idp_cert="dummy")
        cert_b64 = base64.b64encode(b"dummy-cert-bytes").decode()
        xml = b'<Response xmlns="urn:oasis:names:tc:SAML:2.0:protocol"/>'
        with self._patch_cert(), pytest.raises(SSOError, match="missing.*Signature"):
            handler._verify_signature(xml, cert_b64)

    def test_missing_signed_info(self):
        """Cover missing SignedInfo (422)."""
        from maop.enterprise.sso import SSOError
        handler = _make_handler(saml_idp_cert="dummy")
        cert_b64 = base64.b64encode(b"dummy-cert-bytes").decode()
        xml = b"""<Response xmlns="urn:oasis:names:tc:SAML:2.0:protocol" xmlns:ds="http://www.w3.org/2000/09/xmldsig#">
  <ds:Signature>
    <ds:SignatureValue>val</ds:SignatureValue>
  </ds:Signature>
</Response>"""
        with self._patch_cert(), pytest.raises(SSOError, match="missing SignedInfo"):
            handler._verify_signature(xml, cert_b64)

    def test_missing_reference(self):
        """Cover missing Reference (426)."""
        from maop.enterprise.sso import SSOError
        handler = _make_handler(saml_idp_cert="dummy")
        cert_b64 = base64.b64encode(b"dummy-cert-bytes").decode()
        xml = b"""<Response xmlns="urn:oasis:names:tc:SAML:2.0:protocol" xmlns:ds="http://www.w3.org/2000/09/xmldsig#">
  <ds:Signature>
    <ds:SignedInfo>
      <ds:SignatureMethod Algorithm="rsa-sha256"/>
    </ds:SignedInfo>
    <ds:SignatureValue>val</ds:SignatureValue>
  </ds:Signature>
</Response>"""
        with self._patch_cert(), pytest.raises(SSOError, match="missing Reference"):
            handler._verify_signature(xml, cert_b64)

    def test_missing_digest_value(self):
        """Cover missing DigestValue (430)."""
        from maop.enterprise.sso import SSOError
        handler = _make_handler(saml_idp_cert="dummy")
        cert_b64 = base64.b64encode(b"dummy-cert-bytes").decode()
        xml = b"""<Response xmlns="urn:oasis:names:tc:SAML:2.0:protocol" xmlns:ds="http://www.w3.org/2000/09/xmldsig#">
  <ds:Signature>
    <ds:SignedInfo>
      <ds:Reference URI="">
        <ds:Transforms><ds:Transform Algorithm="http://www.w3.org/2000/09/xmldsig#enveloped-signature"/></ds:Transforms>
      </ds:Reference>
    </ds:SignedInfo>
    <ds:SignatureValue>val</ds:SignatureValue>
  </ds:Signature>
</Response>"""
        with self._patch_cert(), pytest.raises(SSOError, match="missing DigestValue"):
            handler._verify_signature(xml, cert_b64)

    def test_digest_value_decode_failed(self):
        """Cover DigestValue base64 decode failure (460-461)."""
        from maop.enterprise.sso import SSOError
        handler = _make_handler(saml_idp_cert="dummy")
        cert_b64 = base64.b64encode(b"dummy-cert-bytes").decode()
        xml = b"""<Response xmlns="urn:oasis:names:tc:SAML:2.0:protocol" xmlns:ds="http://www.w3.org/2000/09/xmldsig#">
  <ds:Signature>
    <ds:SignedInfo>
      <ds:Reference URI="">
        <ds:Transforms><ds:Transform Algorithm="http://www.w3.org/2000/09/xmldsig#enveloped-signature"/></ds:Transforms>
        <ds:DigestValue>not!valid!base64!!!</ds:DigestValue>
      </ds:Reference>
    </ds:SignedInfo>
    <ds:SignatureValue>val</ds:SignatureValue>
  </ds:Signature>
</Response>"""
        with self._patch_cert(), pytest.raises(SSOError, match="DigestValue base64 decode"):
            handler._verify_signature(xml, cert_b64)


# ─– _extract_attributes / _extract_name_id branches (507-526) ──────


class TestExtractBranches:
    def test_no_attribute_statement(self):
        """Cover no AttributeStatement (507)."""
        from lxml import etree
        handler = _make_handler()
        assertion = etree.fromstring(
            b'<Assertion xmlns="urn:oasis:names:tc:SAML:2.0:assertion"/>'
        )
        result = handler._extract_attributes(assertion)
        assert result == {}

    def test_attribute_with_no_name(self):
        """Cover Attribute with no Name (511)."""
        from lxml import etree
        handler = _make_handler()
        xml = b"""<Assertion xmlns="urn:oasis:names:tc:SAML:2.0:assertion">
  <AttributeStatement>
    <Attribute>
      <AttributeValue>val</AttributeValue>
    </Attribute>
    <Attribute Name="email">
      <AttributeValue>test@example.com</AttributeValue>
    </Attribute>
  </AttributeStatement>
</Assertion>"""
        assertion = etree.fromstring(xml)
        result = handler._extract_attributes(assertion)
        assert "email" in result
        assert result["email"] == ["test@example.com"]

    def test_no_subject(self):
        """Cover no Subject (523)."""
        from lxml import etree
        handler = _make_handler()
        assertion = etree.fromstring(
            b'<Assertion xmlns="urn:oasis:names:tc:SAML:2.0:assertion"/>'
        )
        assert handler._extract_name_id(assertion) == ""

    def test_no_name_id(self):
        """Cover no NameID (525-526)."""
        from lxml import etree
        handler = _make_handler()
        xml = b"""<Assertion xmlns="urn:oasis:names:tc:SAML:2.0:assertion">
  <Subject/>
</Assertion>"""
        assertion = etree.fromstring(xml)
        assert handler._extract_name_id(assertion) == ""


# ─– _validate_conditions branches (537, 547-548, 560-561, 573) ─────


class TestValidateConditionsBranches:
    def test_no_conditions(self):
        """Cover no Conditions (537)."""
        from lxml import etree
        handler = _make_handler()
        assertion = etree.fromstring(
            b'<Assertion xmlns="urn:oasis:names:tc:SAML:2.0:assertion"/>'
        )
        handler._validate_conditions(assertion, "maop-sp")  # should not raise

    def test_not_before_parse_exception(self):
        """Cover NotBefore parse failure (547-548)."""
        from lxml import etree

        from maop.enterprise.sso import SSOError
        handler = _make_handler()
        xml = b"""<Assertion xmlns="urn:oasis:names:tc:SAML:2.0:assertion">
  <Conditions NotBefore="not-a-date"/>
</Assertion>"""
        assertion = etree.fromstring(xml)
        with pytest.raises(SSOError, match="Failed to parse NotBefore"):
            handler._validate_conditions(assertion, "maop-sp")

    def test_not_on_or_after_parse_exception(self):
        """Cover NotOnOrAfter parse failure (560-561)."""
        from lxml import etree

        from maop.enterprise.sso import SSOError
        handler = _make_handler()
        xml = b"""<Assertion xmlns="urn:oasis:names:tc:SAML:2.0:assertion">
  <Conditions NotOnOrAfter="not-a-date"/>
</Assertion>"""
        assertion = etree.fromstring(xml)
        with pytest.raises(SSOError, match="Failed to parse NotOnOrAfter"):
            handler._validate_conditions(assertion, "maop-sp")

    def test_audience_restriction_no_audience(self):
        """Cover AudienceRestriction with no Audience (573)."""
        from lxml import etree

        from maop.enterprise.sso import SSOError
        handler = _make_handler()
        xml = b"""<Assertion xmlns="urn:oasis:names:tc:SAML:2.0:assertion">
  <Conditions>
    <AudienceRestriction/>
  </Conditions>
</Assertion>"""
        assertion = etree.fromstring(xml)
        with pytest.raises(SSOError, match="no Audience"):
            handler._validate_conditions(assertion, "maop-sp")


# ─– _get_not_on_or_after branches (585, 588, 591-592) ─────────────


class TestGetNotOnOrAfter:
    def test_no_conditions(self):
        """Cover no Conditions (585)."""
        from lxml import etree
        handler = _make_handler()
        assertion = etree.fromstring(
            b'<Assertion xmlns="urn:oasis:names:tc:SAML:2.0:assertion"/>'
        )
        assert handler._get_not_on_or_after(assertion) is None

    def test_no_not_on_or_after(self):
        """Cover no NotOnOrAfter string (588)."""
        from lxml import etree
        handler = _make_handler()
        xml = b"""<Assertion xmlns="urn:oasis:names:tc:SAML:2.0:assertion">
  <Conditions/>
</Assertion>"""
        assertion = etree.fromstring(xml)
        assert handler._get_not_on_or_after(assertion) is None

    def test_parse_exception(self):
        """Cover _parse_saml_time exception (591-592)."""
        from lxml import etree
        handler = _make_handler()
        xml = b"""<Assertion xmlns="urn:oasis:names:tc:SAML:2.0:assertion">
  <Conditions NotOnOrAfter="not-a-date"/>
</Assertion>"""
        assertion = etree.fromstring(xml)
        assert handler._get_not_on_or_after(assertion) is None


# ─– _parse_saml_time no tzinfo (659) ───────────────────────────────


class TestParseSamlTimeNoTz:
    def test_no_tzinfo(self):
        """Cover branch where dt.tzinfo is None (659)."""
        from maop.enterprise.saml_handler import _parse_saml_time
        # A time string without Z and without timezone
        result = _parse_saml_time("2024-01-01T12:00:00+00:00")
        assert result is not None
        assert result.year == 2024


# ─– _build_user_from_saml ──────────────────────────────────────────


class TestBuildUserFromSaml:
    def test_with_attributes(self):
        handler = _make_handler()
        attrs = {
            "email": ["user@example.com"],
            "displayname": ["Test User"],
            "roles": ["admin", "user"],
            "tenant_id": ["t1"],
        }
        user = handler._build_user_from_saml("nameid", attrs)
        assert user.email == "user@example.com"
        assert user.display_name == "Test User"
        assert "admin" in user.roles
        assert user.tenant_id == "t1"

    def test_with_empty_attributes(self):
        handler = _make_handler()
        user = handler._build_user_from_saml("", {})
        assert user.email == ""
        assert user.external_id == "saml:unknown"

    def test_with_adfs_claim_uris(self):
        handler = _make_handler()
        attrs = {
            "http://schemas.xmlsoap.org/ws/2005/05/identity/claims/emailaddress": ["adfs@example.com"],
            "http://schemas.microsoft.com/ws/2008/06/identity/claims/role": ["adfs-role"],
        }
        user = handler._build_user_from_saml("adfsuser", attrs)
        assert user.email == "adfs@example.com"
        assert "adfs-role" in user.roles