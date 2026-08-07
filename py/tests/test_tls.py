"""Tests for MAOP.core.tls - TLS/SSL context management."""

from __future__ import annotations

import ssl
from pathlib import Path

import pytest

from maop.core.security.tls import TLSSettings, create_ssl_context


def _generate_self_signed_cert(cert_path: Path, key_path: Path) -> None:
    """Generate a self-signed certificate using Python cryptography lib.

    Replaces the old openssl CLI approach that skipped on Windows.
    """
    from datetime import datetime, timedelta, timezone

    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = issuer = x509.Name([
        x509.NameAttribute(NameOID.COMMON_NAME, "test"),
        x509.NameAttribute(NameOID.ORGANIZATION_NAME, "MAOP"),
    ])
    now = datetime.now(timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now)
        .not_valid_after(now + timedelta(days=1))
        .sign(key, hashes.SHA256())
    )
    cert_path.write_bytes(
        cert.public_bytes(serialization.Encoding.PEM)
    )
    key_path.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.TraditionalOpenSSL,
            serialization.NoEncryption(),
        )
    )


class TestTLSSettings:
    def test_default_disabled(self):
        s = TLSSettings()
        assert not s.enabled

    def test_enabled_with_paths(self):
        s = TLSSettings(enabled=True, cert_file="/tmp/cert.pem", key_file="/tmp/key.pem")
        assert s.enabled
        assert s.cert_file == "/tmp/cert.pem"

    def test_min_version_default(self):
        s = TLSSettings()
        assert s.min_version == "TLSv1_2"


class TestCreateSSLContext:
    def test_returns_none_when_disabled(self):
        s = TLSSettings(enabled=False)
        assert create_ssl_context(s) is None

    def test_raises_when_cert_missing(self, tmp_path):
        s = TLSSettings(
            enabled=True,
            cert_file=str(tmp_path / "nonexistent.crt"),
            key_file=str(tmp_path / "nonexistent.key"),
        )
        with pytest.raises(FileNotFoundError, match="cert file not found"):
            create_ssl_context(s)

    def test_creates_context_with_valid_cert(self, tmp_path):
        """Generate a self-signed cert and verify SSLContext creation."""
        cert_path = tmp_path / "test.crt"
        key_path = tmp_path / "test.key"
        _generate_self_signed_cert(cert_path, key_path)

        s = TLSSettings(
            enabled=True,
            cert_file=str(cert_path),
            key_file=str(key_path),
            min_version="TLSv1_2",
        )
        ctx = create_ssl_context(s)
        assert ctx is not None
        assert isinstance(ctx, ssl.SSLContext)
        assert ctx.minimum_version == ssl.TLSVersion.TLSv1_2

    def test_tls13_version(self, tmp_path):
        cert_path = tmp_path / "test.crt"
        key_path = tmp_path / "test.key"
        _generate_self_signed_cert(cert_path, key_path)

        s = TLSSettings(
            enabled=True,
            cert_file=str(cert_path),
            key_file=str(key_path),
            min_version="TLSv1_3",
        )
        ctx = create_ssl_context(s)
        assert ctx is not None
        assert ctx.minimum_version == ssl.TLSVersion.TLSv1_3
