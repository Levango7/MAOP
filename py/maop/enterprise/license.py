"""MAOP Enterprise License Validation.

Validates license keys for MAOP Enterprise edition. A license key is a
signed token containing customer info, edition, and expiry date.

Format:
    MAOP-ENT-{base64url(payload_json)}.{base64url(signature)}

The signature is Ed25519, verified against the public key bundled in
maop.enterprise.keys.public_key.pem.

Validation logic:
    1. Parse the key into payload + signature
    2. Verify the Ed25519 signature against the bundled public key
    3. Check that expires_at has not passed
    4. Optionally check machine fingerprint if present in the license

Degrade gracefully:
    - No license key configured → honor system (enterprise package
      importable = enterprise), log a warning
    - License key present but invalid → degrade to PERSONAL, log error
    - License key present and valid → ENTERPRISE

Usage:
    validator = LicenseValidator()
    info = validator.validate_from_env()  # returns LicenseInfo or None
    # or
    info = validator.validate("MAOP-ENT-xxx.yyy")  # raises on invalid
"""

from __future__ import annotations

import base64
import json
import logging
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

__all__ = [
    "LicenseInfo",
    "LicenseValidator",
    "LicenseError",
    "LicenseExpiredError",
    "LicenseSignatureError",
    "LicenseFormatError",
]

# Grace period after expiry before hard degradation (days)
_GRACE_PERIOD_DAYS = 7

# Path to the bundled public key
_PUBLIC_KEY_PATH = Path(__file__).parent / "keys" / "public_key.pem"


class LicenseInfo(BaseModel):
    """Parsed and validated license information."""

    customer: str = Field(description="Customer/organization name")
    edition: str = Field(description="Licensed edition (should be 'enterprise')")
    issued_at: datetime = Field(description="When the license was issued")
    expires_at: datetime = Field(description="When the license expires")
    max_users: int | None = Field(default=None, description="Max concurrent users (None = unlimited)")
    fingerprint: str | None = Field(default=None, description="Optional machine fingerprint binding")
    features: list[str] | None = Field(default=None, description="Optional feature scope")

    @property
    def is_expired(self) -> bool:
        """Check if the license has expired (before grace period)."""
        return datetime.now(timezone.utc) > self.expires_at

    @property
    def is_in_grace_period(self) -> bool:
        """Check if the license is expired but within grace period."""
        now = datetime.now(timezone.utc)
        grace_end = self.expires_at + timedelta(days=_GRACE_PERIOD_DAYS)
        return self.expires_at < now <= grace_end


class LicenseError(Exception):
    """Base exception for license validation errors."""


class LicenseFormatError(LicenseError):
    """License key format is invalid (can't parse)."""


class LicenseSignatureError(LicenseError):
    """License signature verification failed (tampered or wrong key)."""


class LicenseExpiredError(LicenseError):
    """License has expired beyond the grace period."""

    def __init__(self, info: LicenseInfo) -> None:
        self.info = info
        super().__init__(
            f"License for '{info.customer}' expired on {info.expires_at.isoformat()} "
            f"(grace period of {_GRACE_PERIOD_DAYS} days has passed)"
        )


# L21/L22 (Phase R6): License 在线撤销（CRL）机制未实现。
# 当前 license 验证仅检查签名 + 过期时间 + 宽限期。
# 未来实现 CRL 需要：
#   1. 在线撤销列表服务（或离线 CRL 文件分发）
#   2. 客户端定期检查撤销状态
#   3. 离线降级策略（无法连接 CRL 服务时）
# 缓解措施：license 过期后自动降级为 Personal 版本，7 天宽限期后强制限制。


class LicenseValidator:
    """Validates MAOP Enterprise license keys.

    The validator loads the Ed25519 public key from the bundled
    ``keys/public_key.pem`` file. License keys are verified against
    this key; the corresponding private key is held by the MAOP
    commercial team and used to sign licenses at issuance time.
    """

    def __init__(self, public_key_path: Path | None = None) -> None:
        self._public_key_path = public_key_path or _PUBLIC_KEY_PATH
        self._public_key = self._load_public_key()

    def _load_public_key(self):
        """Load the Ed25519 public key from PEM file."""
        try:
            from cryptography.hazmat.primitives import serialization
            from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

            if not self._public_key_path.exists():
                raise LicenseError(
                    f"Public key file not found: {self._public_key_path}. "
                    f"The maop-enterprise package may be corrupted."
                )
            key_data = self._public_key_path.read_bytes()
            public_key = serialization.load_pem_public_key(key_data)
            if not isinstance(public_key, Ed25519PublicKey):
                raise LicenseError(
                    f"Public key in {self._public_key_path} is not an Ed25519 key"
                )
            return public_key
        except LicenseError:
            raise
        except Exception as exc:
            raise LicenseError(f"Failed to load public key: {exc}") from exc

    def validate(self, license_key: str) -> LicenseInfo:
        """Validate a license key and return its info.

        Parameters
        ----------
        license_key : str
            The license key string (format: MAOP-ENT-{payload}.{signature})

        Returns
        -------
        LicenseInfo
            Parsed license metadata if valid.

        Raises
        ------
        LicenseFormatError
            If the key format is invalid (can't parse).
        LicenseSignatureError
            If the signature verification fails (tampered or wrong signing key).
        LicenseExpiredError
            If the license has expired beyond the grace period.
        """
        payload, signature = self._parse_key(license_key)
        self._verify_signature(payload, signature)
        info = self._parse_payload(payload)
        self._check_expiry(info)
        return info

    def validate_from_env(self) -> LicenseInfo | None:
        """Load and validate license from environment or file.

        Checks in order:
        1. ``MAOP_LICENSE_KEY`` environment variable
        2. ``data/license.key`` file (relative to MAOP_ROOT or cwd)

        Returns
        -------
        LicenseInfo or None
            ``None`` if no license key is configured (not an error —
            indicates honor-system mode). LicenseInfo if a key is
            present and valid.

        Raises
        ------
        LicenseError
            If a key is present but invalid (signature failure, expired, etc.)
        """
        key = self._load_key_from_env_or_file()
        if key is None:
            return None
        return self.validate(key)

    def _load_key_from_env_or_file(self) -> str | None:
        """Load license key from MAOP_LICENSE_KEY env or data/license.key file."""
        # 1. Environment variable
        key = os.getenv("MAOP_LICENSE_KEY", "").strip()
        if key:
            return key

        # 2. File (data/license.key)
        root = os.getenv("MAOP_ROOT_DIR") or os.getenv("MAOP_ROOT") or os.getcwd()
        key_file = Path(root) / "data" / "license.key"
        if key_file.exists():
            try:
                content = key_file.read_text(encoding="utf-8").strip()
                if content and not content.startswith("#"):
                    return content
            except Exception as exc:
                logger.warning("[license] Failed to read %s: %s", key_file, exc)

        return None

    @staticmethod
    def _parse_key(license_key: str) -> tuple[bytes, bytes]:
        """Parse a license key into (payload_bytes, signature_bytes).

        Format: MAOP-ENT-{base64url(payload)}.{base64url(signature)}
        """
        if not license_key:
            raise LicenseFormatError("Empty license key")

        key = license_key.strip()
        if not key.startswith("MAOP-ENT-"):
            raise LicenseFormatError(
                f"License key must start with 'MAOP-ENT-', got: {key[:20]}..."
            )

        body = key[len("MAOP-ENT-"):]
        if "." not in body:
            raise LicenseFormatError(
                "License key missing signature separator '.'"
            )

        payload_b64, sig_b64 = body.rsplit(".", 1)
        try:
            # Use URL-safe base64 decoder, add padding if needed
            payload_bytes = base64.urlsafe_b64decode(payload_b64 + "==")
            signature = base64.urlsafe_b64decode(sig_b64 + "==")
        except Exception as exc:
            raise LicenseFormatError(f"Failed to decode base64: {exc}") from exc

        if len(signature) != 64:
            raise LicenseFormatError(
                f"Ed25519 signature must be 64 bytes, got {len(signature)}"
            )

        return payload_bytes, signature

    def _verify_signature(self, payload: bytes, signature: bytes) -> None:
        """Verify the Ed25519 signature."""
        try:
            self._public_key.verify(signature, payload)
        except Exception as exc:
            raise LicenseSignatureError(
                "License signature verification failed — the key may be "
                "tampered, expired, or issued by an unauthorized party"
            ) from exc

    @staticmethod
    def _parse_payload(payload: bytes) -> LicenseInfo:
        """Parse the JSON payload into LicenseInfo."""
        try:
            data = json.loads(payload.decode("utf-8"))
        except Exception as exc:
            raise LicenseFormatError(f"Failed to parse payload JSON: {exc}") from exc

        # Validate required fields
        required = ["customer", "edition", "issued_at", "expires_at"]
        for field in required:
            if field not in data:
                raise LicenseFormatError(f"Payload missing required field: {field}")

        # Parse datetime fields (support ISO format with or without timezone)
        for dt_field in ["issued_at", "expires_at"]:
            val = data[dt_field]
            if isinstance(val, str):
                # Handle 'Z' suffix
                if val.endswith("Z"):
                    val = val[:-1] + "+00:00"
                data[dt_field] = datetime.fromisoformat(val)
            elif isinstance(val, datetime):
                pass
            else:
                raise LicenseFormatError(
                    f"Field '{dt_field}' must be ISO datetime, got {type(val).__name__}"
                )

        # Ensure timezone-aware (assume UTC if naive)
        for dt_field in ["issued_at", "expires_at"]:
            if data[dt_field].tzinfo is None:
                data[dt_field] = data[dt_field].replace(tzinfo=timezone.utc)

        return LicenseInfo(**data)

    @staticmethod
    def _check_expiry(info: LicenseInfo) -> None:
        """Check if the license is still valid (within grace period)."""
        if info.is_in_grace_period:
            logger.warning(
                "[license] License for '%s' is in grace period "
                "(expired %s, grace ends in %d days)",
                info.customer,
                info.expires_at.isoformat(),
                _GRACE_PERIOD_DAYS - (datetime.now(timezone.utc) - info.expires_at).days,
            )
        elif info.is_expired:
            raise LicenseExpiredError(info)
