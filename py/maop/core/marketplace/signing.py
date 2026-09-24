"""Marketplace package signing — Ed25519 asymmetric signature verification.

.. warning::

   **本模块未接入任何运行时路径（2026-09-24 取证）。**

   AST 导入分析确认：全仓**零运行时导入方**，仅被
   ``tests/test_marketplace_f301.py`` 导入（该测试全绿，覆盖签名往返 /
   篡改拒绝 / 错误密钥拒绝 / 畸形签名拒绝）。

   功能完整（2026-09-25 起是仓库内**唯一**的 Ed25519 工具/包签名实现），但没有任何
   用户可达路径。已上线的 :mod:`maop.core.mcp.mcp_marketplace` **不认识
   signature 字段**（其信任模型是 SHA-256 checksum + ``trusted`` 注册表标志），
   因此本模块与线上安装路径之间**没有集成点**。

   接入时**必须同时接入** :mod:`maop.core.marketplace.key_management`
   —— 否则密钥无法轮换 / 吊销，一次泄露就只能改代码。

   状态：待决策。ROADMAP.md:173 把签名校验列为**阶段三**（Month 10–21）
   的 Agent Marketplace 事项且标注「仍待排期」，故尚未排期。
   登记于 ``scripts/check_wiring.py`` 的 ``KNOWN_UNWIRED``。

G-01 security fix: replaces the previous HMAC-SHA256 symmetric scheme with
Ed25519 asymmetric signatures. This allows:

  * **Public verification** — anyone with the public key can verify; only
    the marketplace operator holding the private key can sign.
  * **Non-repudiation** — the signer cannot deny having signed a package.
  * **Key compromise isolation** — a leaked verification key does not
    allow forging signatures (unlike HMAC where the same key verifies
    and signs).

Signature format
----------------
Each signed package carries a ``signature_hex`` field which is the
hex-encoded Ed25519 signature of ``SHA256(canonical_json(payload))``.
The ``verify()`` function:

  1. Decodes the hex signature to 64 bytes (Ed25519 sig length).
  2. Re-computes the canonical JSON digest of the payload.
  3. Calls :func:`cryptography.hazmat.primitives.asymmetric.ed25519.Ed25519PublicKey.verify`
     which raises ``InvalidSignature`` on mismatch.
  4. Returns ``True`` only on successful verification — fail-closed.

Public key loading
------------------
The public key is loaded from a PEM file whose path is supplied to
:class:`PackageVerifier`. For tests, a key pair can be generated via
:func:`generate_key_pair`.
"""

from __future__ import annotations

import hashlib
import json
import logging
from pathlib import Path
from typing import Any, cast

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)

logger = logging.getLogger(__name__)

# Ed25519 signatures are always 64 bytes.
_SIGNATURE_LEN = 64


class SignatureError(ValueError):
    """Raised on signature verification failures (malformed, missing, invalid)."""


# ── Canonical JSON digest ──────────────────────────────────────────


def _canonical_json(payload: Any) -> bytes:
    """Serialise *payload* to canonical JSON (sorted keys, no spaces).

    Canonical form ensures that the signer and verifier compute the
    exact same byte string regardless of dict insertion order or
    whitespace.
    """
    return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _payload_digest(payload: Any) -> bytes:
    """SHA-256 digest of the canonical JSON encoding of *payload*."""
    return hashlib.sha256(_canonical_json(payload)).digest()


# ── Key helpers ────────────────────────────────────────────────────


def generate_key_pair() -> tuple[Ed25519PrivateKey, Ed25519PublicKey]:
    """Generate a fresh Ed25519 key pair (for tests / local dev)."""
    private_key = Ed25519PrivateKey.generate()
    public_key = private_key.public_key()
    return private_key, public_key


def private_key_to_pem(private_key: Ed25519PrivateKey) -> bytes:
    """Serialise an Ed25519 private key to PKCS8 PEM bytes."""
    return private_key.private_bytes(  # type: ignore
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )


def public_key_to_pem(public_key: Ed25519PublicKey) -> bytes:
    """Serialise an Ed25519 public key to SubjectPublicKeyInfo PEM bytes."""
    return public_key.public_bytes(  # type: ignore
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )


def load_public_key(pem_path: str | Path) -> Ed25519PublicKey:
    """Load an Ed25519 public key from a PEM file on disk."""
    pem_bytes = Path(pem_path).read_bytes()
    return cast(
        Ed25519PublicKey,
        serialization.load_pem_public_key(pem_bytes),
    )


def load_public_key_from_bytes(pem_bytes: bytes) -> Ed25519PublicKey:
    """Load an Ed25519 public key from PEM bytes in memory."""
    return cast(
        Ed25519PublicKey,
        serialization.load_pem_public_key(pem_bytes),
    )


def generate_keypair() -> tuple[str, str]:
    """Generate a fresh key pair and return it as ``(private_pem, public_pem)``.

    2026-09-25 从已删除的 ``maop.core.mcp.tool_signing`` 移植过来 ——
    那是该模块相对本模块**唯一**的功能补充：本模块原本只有
    :func:`generate_key_pair`（返回密钥**对象**），调用方还要再走
    ``private_key_to_pem`` / ``public_key_to_pem`` 两步才能得到可存储、
    可传输的 PEM 字符串。签发流程（生成 → 存盘 → 分发）几乎总是需要 PEM，
    故补这一站式入口。

    Returns
    -------
    tuple of (str, str)
        ``(private_pem, public_pem)`` —— PKCS8 / SubjectPublicKeyInfo 编码，
        UTF-8 字符串形式（可直接写入文件或存入 :mod:`key_management`）。
    """
    private_key, public_key = generate_key_pair()
    return (
        private_key_to_pem(private_key).decode("utf-8"),
        public_key_to_pem(public_key).decode("utf-8"),
    )


# ── Sign / verify ──────────────────────────────────────────────────


def sign_payload(payload: Any, private_key: Ed25519PrivateKey) -> str:
    """Sign *payload* with an Ed25519 private key.

    Returns the hex-encoded 64-byte signature.
    """
    digest = _payload_digest(payload)
    signature = private_key.sign(digest)
    return signature.hex()  # type: ignore


def verify(
    payload: Any,
    signature_hex: str,
    public_key: Ed25519PublicKey,
) -> bool:
    """Verify an Ed25519 signature over *payload*.

    G-01 fix: uses asymmetric Ed25519 verification instead of HMAC.

    Parameters
    ----------
    payload : Any
        The original payload dict (or any JSON-serialisable value).
    signature_hex : str
        Hex-encoded Ed25519 signature (64 bytes → 128 hex chars).
    public_key : Ed25519PublicKey
        The marketplace operator's public key.

    Returns
    -------
    bool
        ``True`` if the signature is valid.

    Raises
    ------
    SignatureError
        If the signature is malformed (wrong length, bad hex) or invalid
        (does not match the payload).  Fail-closed: never returns False
        silently for a well-formed but incorrect signature — raises
        instead.
    """
    if not signature_hex:
        raise SignatureError("signature_hex is empty")

    # 1. Decode hex signature → bytes.
    try:
        signature = bytes.fromhex(signature_hex)
    except ValueError as exc:
        raise SignatureError(f"signature_hex is not valid hex: {exc}") from exc

    # 2. Enforce Ed25519 signature length (64 bytes).
    if len(signature) != _SIGNATURE_LEN:
        raise SignatureError(
            f"Ed25519 signature must be {_SIGNATURE_LEN} bytes, got {len(signature)}",
        )

    # 3. Compute the canonical JSON digest.
    digest = _payload_digest(payload)

    # 4. Verify — Ed25519PublicKey.verify raises InvalidSignature on mismatch.
    try:
        public_key.verify(signature, digest)
    except InvalidSignature as exc:
        raise SignatureError("Ed25519 signature verification failed") from exc

    logger.debug("[marketplace] Ed25519 signature verified OK")
    return True


# ── PackageVerifier convenience class ──────────────────────────────


class PackageVerifier:
    """Verify marketplace package signatures against a fixed public key.

    Usage::

        verifier = PackageVerifier("/etc/maop/marketplace_pub.pem")
        verifier.verify(package_metadata, package_metadata["signature_hex"])
    """

    def __init__(self, public_key_pem_path: str | Path | None = None) -> None:
        self._public_key: Ed25519PublicKey | None = None
        if public_key_pem_path is not None:
            self._public_key = load_public_key(public_key_pem_path)

    @property
    def public_key(self) -> Ed25519PublicKey:
        if self._public_key is None:
            raise SignatureError("No public key configured")
        return self._public_key

    def set_public_key(self, public_key: Ed25519PublicKey) -> None:
        """Set the public key programmatically (e.g. from config)."""
        self._public_key = public_key

    def verify(self, payload: Any, signature_hex: str) -> bool:
        """Verify *payload* against *signature_hex* using the configured key."""
        return verify(payload, signature_hex, self.public_key)

    def verify_package(self, package: dict[str, Any]) -> bool:
        """Verify a marketplace package dict.

        The package must contain a ``signature_hex`` field; the remainder
        of the dict (with ``signature_hex`` removed) is the signed payload.
        """
        if "signature_hex" not in package:
            raise SignatureError("package missing 'signature_hex' field")
        sig = package["signature_hex"]
        payload = {k: v for k, v in package.items() if k != "signature_hex"}
        return self.verify(payload, sig)

    def verify_with_key_management(
        self,
        payload: Any,
        signature_hex: str,
        tool_id: str,
        key_mgmt: Any,
    ) -> bool:
        """Verify a payload using keys from a :class:`KeyManagement` store.

        Fetches all active (non-expired, non-revoked) public keys for
        *tool_id* from *key_mgmt*, skips any that are blacklisted, and
        tries each remaining key in turn.  Returns ``True`` if any key
        verifies the signature — this supports key rotation overlap
        where multiple keys are simultaneously valid.

        Parameters
        ----------
        payload : Any
            The signed payload.
        signature_hex : str
            Hex-encoded Ed25519 signature.
        tool_id : str
            The tool whose keys should be tried.
        key_mgmt : KeyManagement
            A :class:`maop.core.marketplace.key_management.KeyManagement`
            instance (typed as ``Any`` to avoid a circular import).

        Returns
        -------
        bool
            ``True`` if a non-blacklisted active key verifies.

        Raises
        ------
        SignatureError
            If no active keys exist for the tool, all are blacklisted,
            or none of the candidate keys verify the signature.
        """
        active_keys = key_mgmt.get_active_keys(tool_id)
        if not active_keys:
            raise SignatureError(f"no active keys for tool {tool_id!r}")

        candidate_keys: list[Ed25519PublicKey] = []
        for info in active_keys:
            if key_mgmt.is_blacklisted(tool_id, info.key_id):
                logger.warning(
                    "[marketplace] skipping blacklisted key %s for tool %s",
                    info.key_id, tool_id,
                )
                continue
            try:
                candidate_keys.append(
                    load_public_key_from_bytes(info.public_key.encode("utf-8")),
                )
            except Exception as exc:
                logger.warning(
                    "[marketplace] failed to load key %s: %s", info.key_id, exc,
                )
                continue

        if not candidate_keys:
            raise SignatureError(
                f"all keys for tool {tool_id!r} are blacklisted or unloadable",
            )

        return verify_with_keys(payload, signature_hex, candidate_keys)


# ── Multi-key verification (key management integration) ───────────


def verify_with_keys(
    payload: Any,
    signature_hex: str,
    public_keys: list[Ed25519PublicKey],
) -> bool:
    """Verify a signature against multiple public keys.

    Tries each key in turn; returns ``True`` if any key verifies the
    signature.  This supports key rotation overlap periods where
    multiple keys are simultaneously valid.

    Parameters
    ----------
    payload : Any
        The signed payload.
    signature_hex : str
        Hex-encoded Ed25519 signature.
    public_keys : list[Ed25519PublicKey]
        Candidate public keys to try.

    Returns
    -------
    bool
        ``True`` if any key verifies.

    Raises
    ------
    SignatureError
        If *public_keys* is empty, *signature_hex* is malformed, or
        no key verifies the signature.
    """
    if not public_keys:
        raise SignatureError("no public keys provided for verification")

    last_error: SignatureError | None = None
    for pk in public_keys:
        try:
            verify(payload, signature_hex, pk)
            return True
        except SignatureError as exc:
            last_error = exc

    # All keys failed — raise a consolidated error.
    raise SignatureError(
        f"signature verification failed against all {len(public_keys)} key(s)",
    ) from last_error