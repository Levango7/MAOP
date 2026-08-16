"""MAOP Tool Signing — Ed25519 signature generation & verification for MCP tools.

Tools distributed through the marketplace carry a cryptographic signature so
installers can verify they come from a trusted publisher and haven't been
tampered with.  This module implements Ed25519 signing (RFC 8032) via the
``cryptography`` library — Ed25519 is chosen because it is fast, has small
keys (32-byte public / 64-byte private seed), and is deterministic (no
nonce-misuse footgun like ECDSA).

Two layers are provided:

  1. **Low-level** — :func:`generate_keypair`, :func:`sign_bytes`,
     :func:`verify_bytes` operating on raw bytes / PEM strings.
  2. **Tool-manifest** — :meth:`ToolSigner.sign_manifest` /
     :meth:`ToolSigner.verify_manifest` operating on tool-definition dicts.
     The manifest is canonicalized (keys sorted, compact JSON) before
     signing so that semantically-identical dicts produced by different
     code paths produce the same signature.

Usage::

    from maop.core.mcp.tool_signing import ToolSigner

    signer = ToolSigner()
    priv, pub = signer.generate_keypair()

    manifest = {"name": "fs", "version": "1.0", "command": "npx fs"}
    signed = signer.sign_manifest(manifest, priv)
    assert signer.verify_manifest(signed, pub)
"""

from __future__ import annotations

import json
import logging
from typing import Any

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)

logger = logging.getLogger(__name__)


# ── Exceptions ────────────────────────────────────────────────


class ToolSignatureError(Exception):
    """Raised when a tool signature is missing, malformed, or invalid."""


# ── Low-level key / sign / verify helpers ─────────────────────


def generate_keypair() -> tuple[str, str]:
    """Generate a fresh Ed25519 keypair.

    Returns
    -------
    (private_pem, public_pem) : tuple[str, str]
        PEM-encoded PKCS#8 private key and PEM-encoded SubjectPublicKeyInfo
        public key, both as ASCII strings.
    """
    priv = Ed25519PrivateKey.generate()
    pub = priv.public_key()
    priv_pem = priv.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode("ascii")
    pub_pem = pub.public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode("ascii")
    return priv_pem, pub_pem


def _load_private(pem: str | bytes) -> Ed25519PrivateKey:
    if isinstance(pem, str):
        pem = pem.encode("ascii")
    key = serialization.load_pem_private_key(pem, password=None)
    if not isinstance(key, Ed25519PrivateKey):
        raise ToolSignatureError(f"Expected Ed25519 private key, got {type(key).__name__}")
    return key


def _load_public(pem: str | bytes) -> Ed25519PublicKey:
    if isinstance(pem, str):
        pem = pem.encode("ascii")
    key = serialization.load_pem_public_key(pem)
    if not isinstance(key, Ed25519PublicKey):
        raise ToolSignatureError(f"Expected Ed25519 public key, got {type(key).__name__}")
    return key


def sign_bytes(data: bytes, private_pem: str | bytes) -> bytes:
    """Sign *data* with the Ed25519 private key → 64-byte signature."""
    key = _load_private(private_pem)
    return key.sign(data)  # type: ignore[no-any-return]


def verify_bytes(data: bytes, signature: bytes, public_pem: str | bytes) -> bool:
    """Verify an Ed25519 signature.  Returns ``True`` / ``False``."""
    key = _load_public(public_pem)
    try:
        key.verify(signature, data)
        return True
    except InvalidSignature:
        return False


# ── Canonical serialization ──────────────────────────────────


def canonical_bytes(obj: dict[str, Any]) -> bytes:
    """Serialize *obj* to deterministic compact JSON bytes.

    Keys are sorted recursively so that dict insertion order (which varies
    across Python versions and code paths) does not affect the signature.
    """
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


# ── Tool manifest signing ─────────────────────────────────────


# Fields injected by sign_manifest; excluded from the signed payload so the
# signature is stable across re-signing of the same logical manifest.
_SIGNATURE_FIELDS = ("signature", "signing_key_id", "signing_algorithm")


class ToolSigner:
    """High-level Ed25519 signer/verifier for tool manifests.

    A single instance is reusable across many manifests.  The signing
    algorithm identifier is embedded in the signed manifest so verifiers
    can reject unsupported algorithms without guessing.
    """

    ALGORITHM = "ed25519"

    def __init__(self, *, key_id: str = "default") -> None:
        self._key_id = key_id

    # ── manifest operations ───────────────────────────────────

    def sign_manifest(self, manifest: dict[str, Any], private_pem: str | bytes) -> dict[str, Any]:
        """Sign a tool manifest and return a copy with signature fields added.

        The signature covers the canonical encoding of *manifest* with any
        pre-existing signature fields stripped, so re-signing an already-
        signed manifest produces a fresh (but consistent) signature rather
        than a signature-of-signature.
        """
        payload = {k: v for k, v in manifest.items() if k not in _SIGNATURE_FIELDS}
        data = canonical_bytes(payload)
        sig = sign_bytes(data, private_pem)
        signed = dict(manifest)
        signed["signature"] = sig.hex()
        signed["signing_key_id"] = self._key_id
        signed["signing_algorithm"] = self.ALGORITHM
        return signed

    def verify_manifest(self, manifest: dict[str, Any], public_pem: str | bytes) -> bool:
        """Verify a signed tool manifest.

        Returns ``True`` only when:
          - ``signing_algorithm`` is ``"ed25519"``
          - a hex ``signature`` field is present and decodes to 64 bytes
          - the signature is valid over the canonical encoding of the
            manifest with signature fields stripped.

        Any structural problem raises :class:`ToolSignatureError`; a
        cryptographically invalid signature returns ``False``.
        """
        algo = manifest.get("signing_algorithm", "")
        if algo != self.ALGORITHM:
            raise ToolSignatureError(
                f"Unsupported signing algorithm {algo!r}; expected {self.ALGORITHM!r}"
            )
        sig_hex = manifest.get("signature")
        if not sig_hex:
            raise ToolSignatureError("Manifest has no 'signature' field")
        try:
            sig = bytes.fromhex(sig_hex)
        except ValueError as exc:
            raise ToolSignatureError(f"Signature is not valid hex: {exc}") from exc
        if len(sig) != 64:
            raise ToolSignatureError(
                f"Ed25519 signature must be 64 bytes, got {len(sig)}"
            )
        payload = {k: v for k, v in manifest.items() if k not in _SIGNATURE_FIELDS}
        data = canonical_bytes(payload)
        return verify_bytes(data, sig, public_pem)

    # ── key management ────────────────────────────────────────

    @staticmethod
    def generate_keypair() -> tuple[str, str]:
        """Generate a (private_pem, public_pem) pair."""
        return generate_keypair()

    @property
    def key_id(self) -> str:
        return self._key_id