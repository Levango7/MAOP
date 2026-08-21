"""Tests for maop.enterprise.tls_auto — auto TLS configuration and dev cert generation."""

from __future__ import annotations

import os
import ssl
import sys
from pathlib import Path

import pytest

pytest.importorskip("maop.enterprise")

try:
    import cryptography  # noqa: F401
    _has_cryptography = True
except ImportError:
    _has_cryptography = False

from maop.enterprise import tls_auto


@pytest.fixture(autouse=True)
def enterprise_mode():
    """Enable enterprise edition so require_feature(FeatureFlag.TLS_AUTO) passes."""
    from maop.config.edition import Edition, reset_edition, set_edition
    set_edition(Edition.ENTERPRISE)
    yield
    reset_edition()


def _generate_self_signed_cert(cert_path: Path, key_path: Path) -> None:
    """Generate a real self-signed certificate using the cryptography package."""
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
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.TraditionalOpenSSL,
            serialization.NoEncryption(),
        )
    )


@pytest.mark.skipif(not _has_cryptography, reason="cryptography package not installed")
def test_auto_configure_with_provided_certs(tmp_path, monkeypatch):
    """auto_configure_tls() uses provided cert/key files from env vars."""
    cert_path = tmp_path / "server.crt"
    key_path = tmp_path / "server.key"
    _generate_self_signed_cert(cert_path, key_path)

    monkeypatch.setenv("MAOP_TLS_CERT", str(cert_path))
    monkeypatch.setenv("MAOP_TLS_KEY", str(key_path))

    result = tls_auto.auto_configure_tls()
    assert "ssl" in result
    assert isinstance(result["ssl"], ssl.SSLContext)


def test_auto_configure_generates_dev_certs(tmp_path, monkeypatch):
    """auto_configure_tls() generates dev certs when none are provided."""
    monkeypatch.delenv("MAOP_TLS_CERT", raising=False)
    monkeypatch.delenv("MAOP_TLS_KEY", raising=False)
    # MAOP_DATA_DIR is set to tmp_path/data by the conftest autouse fixture
    result = tls_auto.auto_configure_tls()
    assert "ssl" in result
    data_dir = os.getenv("MAOP_DATA_DIR")
    assert os.path.isfile(os.path.join(data_dir, "tls", "dev-cert.pem"))
    assert os.path.isfile(os.path.join(data_dir, "tls", "dev-key.pem"))


def test_ensure_dev_certs_creates_files(tmp_path, monkeypatch):
    """_ensure_dev_certs() creates cert and key files in the data dir."""
    monkeypatch.delenv("MAOP_TLS_CERT", raising=False)
    monkeypatch.delenv("MAOP_TLS_KEY", raising=False)
    cert, key = tls_auto._ensure_dev_certs()
    assert cert
    assert key
    assert os.path.isfile(cert)
    assert os.path.isfile(key)


def test_ensure_dev_certs_reuses_existing(tmp_path, monkeypatch):
    """_ensure_dev_certs() reuses existing cert/key files without regenerating."""
    monkeypatch.delenv("MAOP_TLS_CERT", raising=False)
    monkeypatch.delenv("MAOP_TLS_KEY", raising=False)
    data_dir = os.getenv("MAOP_DATA_DIR")
    tls_dir = os.path.join(data_dir, "tls")
    os.makedirs(tls_dir, exist_ok=True)
    cert_path = os.path.join(tls_dir, "dev-cert.pem")
    key_path = os.path.join(tls_dir, "dev-key.pem")
    Path(cert_path).write_bytes(b"existing-cert")
    Path(key_path).write_bytes(b"existing-key")

    cert, key = tls_auto._ensure_dev_certs()
    assert cert == cert_path
    assert key == key_path
    # content unchanged -> cert was reused, not regenerated
    assert Path(cert).read_bytes() == b"existing-cert"
    assert Path(key).read_bytes() == b"existing-key"


def test_build_ssl_kwargs(tmp_path):
    """_build_ssl_kwargs() returns an ssl context for valid cert/key."""
    cert_path = tmp_path / "server.crt"
    key_path = tmp_path / "server.key"
    _generate_self_signed_cert(cert_path, key_path)

    result = tls_auto._build_ssl_kwargs(str(cert_path), str(key_path))
    assert "ssl" in result
    assert isinstance(result["ssl"], ssl.SSLContext)


def test_auto_configure_no_certs_returns_empty(monkeypatch):
    """auto_configure_tls() returns an empty dict when cryptography is unavailable."""
    monkeypatch.delenv("MAOP_TLS_CERT", raising=False)
    monkeypatch.delenv("MAOP_TLS_KEY", raising=False)
    # Simulate cryptography being unavailable so dev cert generation fails
    monkeypatch.setitem(sys.modules, "cryptography", None)
    monkeypatch.setitem(sys.modules, "cryptography.x509", None)
    monkeypatch.setitem(sys.modules, "cryptography.x509.oid", None)
    monkeypatch.setitem(sys.modules, "cryptography.hazmat.primitives", None)
    monkeypatch.setitem(sys.modules, "cryptography.hazmat.primitives.asymmetric", None)

    result = tls_auto.auto_configure_tls()
    assert result == {}
