"""Tests for maop.enterprise.license."""

from __future__ import annotations

import base64
import json
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

# P2 修复：无条件 skip 改为 import 条件化 —— maop.enterprise 可导入
# （企业版）时才真正运行测试；个人版（未安装）时才跳过。
try:
    import maop.enterprise  # noqa: F401
except ImportError:
    pytest.skip(reason="maop.enterprise 未发布", allow_module_level=True)
import maop.enterprise.license as _license_mod
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from maop.enterprise.license import (
    LicenseExpiredError,
    LicenseFormatError,
    LicenseSignatureError,
    LicenseValidator,
)


@pytest.fixture(autouse=True)
def _clean_crl_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """清除 CRL 相关环境变量，避免 test_license_crl.py 的测试污染本模块。

    LicenseValidator.__init__ 会读取 MAOP_CRL_URL 决定是否启用 CRL 检查，
    如果 CRL 被启用，篡改签名的 license 可能抛 CRLError 而非
    LicenseSignatureError，导致 test_tampered_signature_raises 失败。

    MAOP_SKIP_INTEGRITY=1 是本模块的通用隔离开关：这里 license 测试的目标是
    验证 key 签名/过期/撤销逻辑，与模块完整性校验（由 prod key 签名的 manifest）
    属于不同的防破解层，不应混测。
    """
    for var in ("MAOP_CRL_URL", "MAOP_CRL_CACHE_TTL_S", "MAOP_CRL_STRICT"):
        monkeypatch.delenv(var, raising=False)
    # 本文件用临时 keypair patch 了 _PUBLIC_KEY_PATH,而磁盘 manifest 是用
    # 生产/开发私钥签的——两套 keypair 不兼容,完整性校验会误报。本测试 suite
    # 只关心 license-key 校验,不关心模块 code-signing。
    monkeypatch.setenv("MAOP_SKIP_INTEGRITY", "1")

_TEST_KEY_DIR = Path(tempfile.mkdtemp(prefix="maop_test_keys_"))
_TEST_PRIVATE_PATH = _TEST_KEY_DIR / "private.pem"
_TEST_PUBLIC_PATH = _TEST_KEY_DIR / "public.pem"

_test_private_key = Ed25519PrivateKey.generate()
_TEST_PRIVATE_PATH.write_bytes(
    _test_private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
)
_TEST_PUBLIC_PATH.write_bytes(
    _test_private_key.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
)
_license_mod._PUBLIC_KEY_PATH = _TEST_PUBLIC_PATH
_DEV_PRIVATE_KEY = _TEST_PRIVATE_PATH


import tempfile

import maop.enterprise.license as _license_mod
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey


def _generate_test_license(
    customer: str = "Test Customer",
    expires_at: datetime | None = None,
    private_key_path: Path | None = None,
) -> str:
    """Generate a test license key using the test private key."""
    if expires_at is None:
        expires_at = datetime.now(timezone.utc) + timedelta(days=365)
    if private_key_path is None:
        private_key_path = _DEV_PRIVATE_KEY

    key_data = private_key_path.read_bytes()
    if key_data.startswith(b"DEVELOPMENT"):
        lines = key_data.split(b"\n")
        key_data = b"\n".join(lines[1:])
    private_key = serialization.load_pem_private_key(key_data, password=None)

    payload = {
        "customer": customer,
        "edition": "enterprise",
        "issued_at": datetime.now(timezone.utc).isoformat(),
        "expires_at": expires_at.isoformat(),
    }
    payload_json = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    signature = private_key.sign(payload_json)

    payload_b64 = base64.urlsafe_b64encode(payload_json).rstrip(b"=").decode("ascii")
    sig_b64 = base64.urlsafe_b64encode(signature).rstrip(b"=").decode("ascii")

    return f"MAOP-ENT-{payload_b64}.{sig_b64}"



class TestLicenseValidator:
    """Test LicenseValidator class."""

    def test_valid_license(self):
        """A properly signed, non-expired license should validate."""
        key = _generate_test_license(customer="ACME Corp")
        validator = LicenseValidator()
        info = validator.validate(key)
        assert info.customer == "ACME Corp"
        assert info.edition == "enterprise"
        assert not info.is_expired

    def test_validate_from_env_with_key(self, monkeypatch):
        """validate_from_env should read MAOP_LICENSE_KEY."""
        key = _generate_test_license()
        monkeypatch.setenv("MAOP_LICENSE_KEY", key)
        validator = LicenseValidator()
        info = validator.validate_from_env()
        assert info is not None
        assert info.customer == "Test Customer"

    def test_validate_from_env_without_key(self, monkeypatch):
        """validate_from_env should return None if no key is configured."""
        monkeypatch.delenv("MAOP_LICENSE_KEY", raising=False)
        # Also ensure data/license.key doesn't interfere
        monkeypatch.setenv("MAOP_ROOT_DIR", "/nonexistent")
        validator = LicenseValidator()
        info = validator.validate_from_env()
        assert info is None

    def test_expired_license_raises(self):
        """An expired license (beyond grace period) should raise."""
        expired = datetime.now(timezone.utc) - timedelta(days=30)
        key = _generate_test_license(expires_at=expired)
        validator = LicenseValidator()
        with pytest.raises(LicenseExpiredError):
            validator.validate(key)

    def test_grace_period_license(self):
        """A license expired but within grace period should validate with warning."""
        recently_expired = datetime.now(timezone.utc) - timedelta(days=2)
        key = _generate_test_license(expires_at=recently_expired)
        validator = LicenseValidator()
        info = validator.validate(key)  # should not raise
        assert info.is_in_grace_period

    def test_tampered_signature_raises(self):
        """A tampered signature should raise LicenseSignatureError."""
        key = _generate_test_license()
        # Flip the last char of the signature
        # Flip one bit of the DECODED signature bytes, then re-encode.
        # Flipping the last base64 char of the raw key is unreliable: a
        # 64-byte Ed25519 signature encodes to 86 base64url chars whose
        # final char carries only 2 real bits + 4 padding bits, so an
        # A<->B flip on it is a no-op ~25% of the time and the test
        # would spuriously pass validation.
        payload_b64, sig_b64 = key[len("MAOP-ENT-"):].rsplit(".", 1)
        sig = bytearray(base64.urlsafe_b64decode(sig_b64 + "=="))
        sig[0] ^= 0x01
        tampered_sig_b64 = base64.urlsafe_b64encode(bytes(sig)).rstrip(b"=").decode("ascii")
        tampered = f"MAOP-ENT-{payload_b64}.{tampered_sig_b64}"
        validator = LicenseValidator()
        with pytest.raises(LicenseSignatureError):
            validator.validate(tampered)

    def test_invalid_format_raises(self):
        """Malformed keys should raise LicenseFormatError."""
        validator = LicenseValidator()
        with pytest.raises(LicenseFormatError):
            validator.validate("")
        with pytest.raises(LicenseFormatError):
            validator.validate("not-a-license")
        with pytest.raises(LicenseFormatError):
            validator.validate("MAOP-ENT-nosignature")
        with pytest.raises(LicenseFormatError):
            validator.validate("MAOP-ENT-!!!.!!!")

    def test_wrong_signing_key_raises(self, tmp_path):
        """A license signed by a different key should fail signature verification."""

        # Generate a rogue key pair (written to tmp_path, not the source tree:
        # writing/deleting files inside tests/ trips sandbox file-protection
        # on some hosts, and pytest cleans tmp_path automatically).
        rogue_key = Ed25519PrivateKey.generate()
        rogue_pem = rogue_key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        )
        rogue_path = tmp_path / "_rogue_key.pem"
        rogue_path.write_bytes(rogue_pem)
        key = _generate_test_license(private_key_path=rogue_path)
        validator = LicenseValidator()
        with pytest.raises(LicenseSignatureError):
            validator.validate(key)

    def test_payload_with_optional_fields(self):
        """License with max_users and fingerprint should parse correctly."""

        # Use module-level test private key so the signature matches LicenseValidator default public key
        key_data = _DEV_PRIVATE_KEY.read_bytes()
        private_key = serialization.load_pem_private_key(key_data, password=None)

        payload = {
            "customer": "Enterprise Corp",
            "edition": "enterprise",
            "issued_at": datetime.now(timezone.utc).isoformat(),
            "expires_at": (datetime.now(timezone.utc) + timedelta(days=365)).isoformat(),
            "max_users": 100,
            "fingerprint": "abc123",
            "features": ["rbac", "audit_log", "sso"],
        }
        payload_json = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        signature = private_key.sign(payload_json)
        payload_b64 = base64.urlsafe_b64encode(payload_json).rstrip(b"=").decode()
        sig_b64 = base64.urlsafe_b64encode(signature).rstrip(b"=").decode()
        key = f"MAOP-ENT-{payload_b64}.{sig_b64}"

        validator = LicenseValidator()
        info = validator.validate(key)
        assert info.max_users == 100
        assert info.fingerprint == "abc123"
        assert info.features == ["rbac", "audit_log", "sso"]


class TestEditionLicenseIntegration:
    """Test license integration with edition detection."""

    def test_personal_edition_ignores_license(self, monkeypatch):
        """Personal edition should not check license."""
        from maop.config.edition import detect_edition, reset_edition
        reset_edition()
        monkeypatch.setenv("MAOP_EDITION", "personal")
        monkeypatch.setenv("MAOP_LICENSE_KEY", "invalid-key")
        assert detect_edition().value == "personal"

    def test_enterprise_without_license_degrades_to_personal(self, monkeypatch):
        """Enterprise requested but no license key = degrade to personal (2026-08-11 hardening).

        Pre-hardening behavior was honor-system (package importable = enterprise).
        That was a trivial bypass (just delete the license file); now a missing
        key always degrades, regardless of MAOP_ENV.
        """
        from maop.config.edition import detect_edition, reset_edition
        reset_edition()
        monkeypatch.setenv("MAOP_EDITION", "enterprise")
        monkeypatch.delenv("MAOP_LICENSE_KEY", raising=False)
        monkeypatch.setenv("MAOP_ROOT_DIR", "/nonexistent")
        assert detect_edition().value == "personal"

    def test_enterprise_with_valid_license(self, monkeypatch):
        """Enterprise with valid license should be enterprise."""
        from maop.config.edition import detect_edition, reset_edition
        reset_edition()
        key = _generate_test_license(customer="Edition Test Corp")
        monkeypatch.setenv("MAOP_EDITION", "enterprise")
        monkeypatch.setenv("MAOP_LICENSE_KEY", key)
        assert detect_edition().value == "enterprise"

    def test_enterprise_with_invalid_license_degrades(self, monkeypatch):
        """Enterprise with invalid license should degrade to personal."""
        from maop.config.edition import detect_edition, reset_edition
        reset_edition()
        monkeypatch.setenv("MAOP_EDITION", "enterprise")
        monkeypatch.setenv("MAOP_LICENSE_KEY", "MAOP-ENT-invalid.invalid")
        assert detect_edition().value == "personal"
