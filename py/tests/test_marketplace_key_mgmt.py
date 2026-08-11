"""Tests for marketplace key management (distribution / rotation / revocation).

Covers:
  - Key registration and retrieval (register_key / get_keys / get_active_keys)
  - Key rotation with grace period (rotate_key)
  - Key revocation (revoke_key)
  - Blacklist mechanism (blacklist_key / is_blacklisted / get_blacklist)
  - Multiple keys coexisting for a single tool
  - Edge cases (empty tool_id, empty public_key, non-existent keys, …)
  - signing.py integration (verify_with_keys, verify_with_key_management)
"""

from __future__ import annotations

from pathlib import Path

import pytest

from maop.core.marketplace.key_management import (
    DEFAULT_GRACE_DAYS,
    BlacklistEntry,
    KeyManagement,
    KeyManagementError,
    PublicKeyInfo,
)
from maop.core.marketplace.signing import (
    PackageVerifier,
    SignatureError,
    generate_key_pair,
    public_key_to_pem,
    sign_payload,
    verify_with_keys,
)


# ── Fixtures ───────────────────────────────────────────────────────


@pytest.fixture
def km(tmp_path: Path) -> KeyManagement:
    """A fresh KeyManagement store backed by an isolated temp DB."""
    return KeyManagement(db_path=tmp_path / "keys.db")


def _make_pem() -> bytes:
    """Generate a fresh Ed25519 key pair and return the public PEM bytes."""
    _, public_key = generate_key_pair()
    return public_key_to_pem(public_key)


def _make_pair() -> tuple[bytes, bytes]:
    """Generate a key pair; return (private_pem, public_pem)."""
    from maop.core.marketplace.signing import private_key_to_pem

    private_key, public_key = generate_key_pair()
    return private_key_to_pem(private_key), public_key_to_pem(public_key)


# ── Key registration & retrieval ──────────────────────────────────


class TestKeyRegistration:
    """Test register_key / get_keys / get_active_keys."""

    def test_register_returns_key_id(self, km: KeyManagement):
        """register_key returns a non-empty key ID."""
        pem = _make_pem()
        kid = km.register_key("tool-1", pem)
        assert isinstance(kid, str)
        assert kid.startswith("mk-")
        assert len(kid) > len("mk-")

    def test_register_and_get_key(self, km: KeyManagement):
        """A registered key can be retrieved by tool_id."""
        pem = _make_pem()
        kid = km.register_key("tool-1", pem)
        keys = km.get_keys("tool-1")
        assert len(keys) == 1
        assert keys[0].key_id == kid
        assert keys[0].tool_id == "tool-1"
        assert keys[0].public_key == pem.decode("utf-8")
        assert keys[0].status == "active"
        assert keys[0].expires_at is None
        assert keys[0].created_at  # non-empty

    def test_register_with_explicit_key_id(self, km: KeyManagement):
        """An explicit key_id is honoured."""
        pem = _make_pem()
        kid = km.register_key("tool-1", pem, key_id="my-custom-id")
        assert kid == "my-custom-id"
        keys = km.get_keys("tool-1")
        assert keys[0].key_id == "my-custom-id"

    def test_register_accepts_bytes_and_str(self, km: KeyManagement):
        """public_key can be supplied as bytes or str."""
        pem_bytes = _make_pem()
        kid1 = km.register_key("tool-1", pem_bytes)
        pem_str = _make_pem().decode("utf-8")
        kid2 = km.register_key("tool-2", pem_str)
        assert km.get_keys("tool-1")[0].key_id == kid1
        assert km.get_keys("tool-2")[0].key_id == kid2

    def test_get_keys_nonexistent_tool(self, km: KeyManagement):
        """get_keys for an unknown tool returns an empty list."""
        assert km.get_keys("no-such-tool") == []

    def test_get_active_keys_filters_revoked(self, km: KeyManagement):
        """get_active_keys excludes revoked keys."""
        pem = _make_pem()
        kid = km.register_key("tool-1", pem)
        assert len(km.get_active_keys("tool-1")) == 1
        km.revoke_key("tool-1", kid)
        assert km.get_active_keys("tool-1") == []
        # get_keys still returns it.
        assert len(km.get_keys("tool-1")) == 1

    def test_public_key_info_model(self, km: KeyManagement):
        """PublicKeyInfo is a proper Pydantic model with expected fields."""
        pem = _make_pem()
        km.register_key("tool-1", pem)
        info = km.get_keys("tool-1")[0]
        assert isinstance(info, PublicKeyInfo)
        # Round-trip via dict.
        d = info.model_dump()
        assert d["tool_id"] == "tool-1"
        assert d["status"] == "active"


# ── Key rotation ──────────────────────────────────────────────────


class TestKeyRotation:
    """Test rotate_key with grace period."""

    def test_rotate_registers_new_key(self, km: KeyManagement):
        """rotate_key registers a new active key."""
        old_pem = _make_pem()
        old_kid = km.register_key("tool-1", old_pem)
        new_pem = _make_pem()
        new_kid = km.rotate_key("tool-1", new_pem, old_key_id=old_kid, grace_days=30)
        assert new_kid != old_kid
        assert new_kid.startswith("mk-")
        keys = km.get_keys("tool-1")
        assert len(keys) == 2

    def test_rotate_old_key_gets_expiry(self, km: KeyManagement):
        """The old key gets an expires_at within the grace period."""
        old_pem = _make_pem()
        old_kid = km.register_key("tool-1", old_pem)
        new_pem = _make_pem()
        km.rotate_key("tool-1", new_pem, old_key_id=old_kid, grace_days=30)

        keys = km.get_keys("tool-1")
        old_key = next(k for k in keys if k.key_id == old_kid)
        assert old_key.expires_at is not None
        assert old_key.status == "active"  # still active during grace

    def test_rotate_both_keys_active_during_grace(self, km: KeyManagement):
        """Both old and new keys are active during the grace period."""
        old_pem = _make_pem()
        old_kid = km.register_key("tool-1", old_pem)
        new_pem = _make_pem()
        new_kid = km.rotate_key("tool-1", new_pem, old_key_id=old_kid, grace_days=30)

        active = km.get_active_keys("tool-1")
        active_ids = {k.key_id for k in active}
        assert old_kid in active_ids
        assert new_kid in active_ids

    def test_rotate_default_grace_days(self, km: KeyManagement):
        """The default grace period is DEFAULT_GRACE_DAYS (30)."""
        old_pem = _make_pem()
        old_kid = km.register_key("tool-1", old_pem)
        new_pem = _make_pem()
        km.rotate_key("tool-1", new_pem, old_key_id=old_kid)

        keys = km.get_keys("tool-1")
        old_key = next(k for k in keys if k.key_id == old_kid)
        assert old_key.expires_at is not None
        # The default is 30 days; just check it's set.
        assert DEFAULT_GRACE_DAYS == 30

    def test_rotate_zero_grace_expires_immediately(self, km: KeyManagement):
        """grace_days=0 makes the old key expire immediately."""
        old_pem = _make_pem()
        old_kid = km.register_key("tool-1", old_pem)
        new_pem = _make_pem()
        km.rotate_key("tool-1", new_pem, old_key_id=old_kid, grace_days=0)

        # Old key should no longer be active (expires_at <= now).
        active_ids = {k.key_id for k in km.get_active_keys("tool-1")}
        assert old_kid not in active_ids

    def test_rotate_nonexistent_old_key(self, km: KeyManagement):
        """Rotating with a non-existent old_key_id raises."""
        new_pem = _make_pem()
        with pytest.raises(KeyManagementError, match="not found"):
            km.rotate_key("tool-1", new_pem, old_key_id="no-such-key")

    def test_rotate_wrong_tool(self, km: KeyManagement):
        """Rotating with a mismatched tool_id raises."""
        old_pem = _make_pem()
        old_kid = km.register_key("tool-A", old_pem)
        new_pem = _make_pem()
        with pytest.raises(KeyManagementError, match="not found"):
            km.rotate_key("tool-B", new_pem, old_key_id=old_kid)

    def test_rotate_negative_grace_raises(self, km: KeyManagement):
        """Negative grace_days is rejected."""
        old_pem = _make_pem()
        old_kid = km.register_key("tool-1", old_pem)
        new_pem = _make_pem()
        with pytest.raises(KeyManagementError, match="grace_days"):
            km.rotate_key("tool-1", new_pem, old_key_id=old_kid, grace_days=-1)

    def test_rotate_empty_new_key_raises(self, km: KeyManagement):
        """Empty new_public_key is rejected."""
        old_pem = _make_pem()
        old_kid = km.register_key("tool-1", old_pem)
        with pytest.raises(KeyManagementError, match="public_key"):
            km.rotate_key("tool-1", "   ", old_key_id=old_kid)


# ── Key revocation ────────────────────────────────────────────────


class TestKeyRevocation:
    """Test revoke_key."""

    def test_revoke_active_key(self, km: KeyManagement):
        """Revoking an active key returns True and marks it revoked."""
        pem = _make_pem()
        kid = km.register_key("tool-1", pem)
        assert km.revoke_key("tool-1", kid) is True
        keys = km.get_keys("tool-1")
        assert keys[0].status == "revoked"

    def test_revoke_already_revoked(self, km: KeyManagement):
        """Revoking an already-revoked key returns False."""
        pem = _make_pem()
        kid = km.register_key("tool-1", pem)
        assert km.revoke_key("tool-1", kid) is True
        assert km.revoke_key("tool-1", kid) is False

    def test_revoke_nonexistent_key(self, km: KeyManagement):
        """Revoking a non-existent key returns False."""
        assert km.revoke_key("tool-1", "no-such-key") is False

    def test_revoke_wrong_tool(self, km: KeyManagement):
        """Revoking with a mismatched tool_id returns False."""
        pem = _make_pem()
        kid = km.register_key("tool-A", pem)
        assert km.revoke_key("tool-B", kid) is False

    def test_revoked_key_excluded_from_active(self, km: KeyManagement):
        """A revoked key is not in get_active_keys."""
        pem = _make_pem()
        kid = km.register_key("tool-1", pem)
        km.revoke_key("tool-1", kid)
        assert km.get_active_keys("tool-1") == []


# ── Blacklist ─────────────────────────────────────────────────────


class TestBlacklist:
    """Test blacklist_key / is_blacklisted / get_blacklist."""

    def test_blacklist_key(self, km: KeyManagement):
        """blacklist_key adds an entry and returns True."""
        pem = _make_pem()
        kid = km.register_key("tool-1", pem)
        assert km.blacklist_key("tool-1", kid, reason="compromised") is True
        assert km.is_blacklisted("tool-1", kid) is True

    def test_blacklist_does_not_revoke_key(self, km: KeyManagement):
        """Blacklisting a key does NOT change its status (independent layer).

        blacklist is an emergency compromise marker separate from
        planned revocation.  The key remains 'active' but is rejected
        during verification via is_blacklisted.
        """
        pem = _make_pem()
        kid = km.register_key("tool-1", pem)
        km.blacklist_key("tool-1", kid, reason="leaked")
        keys = km.get_keys("tool-1")
        assert keys[0].status == "active"
        assert km.is_blacklisted("tool-1", kid) is True

    def test_blacklist_idempotent(self, km: KeyManagement):
        """Blacklisting an already-blacklisted key returns False."""
        pem = _make_pem()
        kid = km.register_key("tool-1", pem)
        assert km.blacklist_key("tool-1", kid, reason="r1") is True
        assert km.blacklist_key("tool-1", kid, reason="r2") is False

    def test_blacklist_nonexistent_key_still_works(self, km: KeyManagement):
        """A key can be blacklisted even if never registered (defence-in-depth)."""
        assert km.blacklist_key("tool-1", "phantom-key", reason="suspicious") is True
        assert km.is_blacklisted("tool-1", "phantom-key") is True

    def test_is_blacklisted_false_for_unknown(self, km: KeyManagement):
        """is_blacklisted returns False for unknown keys."""
        assert km.is_blacklisted("tool-1", "no-such-key") is False

    def test_is_blacklisted_scoped_to_tool(self, km: KeyManagement):
        """Blacklist entries are scoped to (tool_id, key_id)."""
        pem = _make_pem()
        kid = km.register_key("tool-A", pem)
        km.blacklist_key("tool-A", kid, reason="r")
        assert km.is_blacklisted("tool-A", kid) is True
        assert km.is_blacklisted("tool-B", kid) is False

    def test_get_blacklist_returns_entries(self, km: KeyManagement):
        """get_blacklist returns all entries for a tool."""
        pem1 = _make_pem()
        kid1 = km.register_key("tool-1", pem1)
        pem2 = _make_pem()
        kid2 = km.register_key("tool-1", pem2)
        km.blacklist_key("tool-1", kid1, reason="leaked")
        km.blacklist_key("tool-1", kid2, reason="rotated-out")
        entries = km.get_blacklist("tool-1")
        assert len(entries) == 2
        assert all(isinstance(e, BlacklistEntry) for e in entries)
        reasons = {e.reason for e in entries}
        assert reasons == {"leaked", "rotated-out"}

    def test_get_blacklist_empty(self, km: KeyManagement):
        """get_blacklist for a tool with no entries returns []."""
        assert km.get_blacklist("tool-1") == []

    def test_get_blacklist_scoped_to_tool(self, km: KeyManagement):
        """get_blacklist only returns entries for the given tool."""
        pem = _make_pem()
        kid = km.register_key("tool-A", pem)
        km.blacklist_key("tool-A", kid, reason="r")
        assert len(km.get_blacklist("tool-A")) == 1
        assert km.get_blacklist("tool-B") == []

    def test_blacklist_empty_reason_raises(self, km: KeyManagement):
        """An empty reason is rejected."""
        pem = _make_pem()
        kid = km.register_key("tool-1", pem)
        with pytest.raises(KeyManagementError, match="reason"):
            km.blacklist_key("tool-1", kid, reason="")
        with pytest.raises(KeyManagementError, match="reason"):
            km.blacklist_key("tool-1", kid, reason="   ")


# ── Multiple keys coexistence ─────────────────────────────────────


class TestMultipleKeys:
    """Test that multiple keys can coexist for a single tool."""

    def test_multiple_keys_for_same_tool(self, km: KeyManagement):
        """Several keys can be registered for one tool."""
        kids = []
        for _ in range(5):
            pem = _make_pem()
            kids.append(km.register_key("tool-1", pem))
        keys = km.get_keys("tool-1")
        assert len(keys) == 5
        assert {k.key_id for k in keys} == set(kids)
        assert all(k.status == "active" for k in keys)

    def test_multiple_tools_isolated(self, km: KeyManagement):
        """Keys for different tools are isolated."""
        km.register_key("tool-A", _make_pem())
        km.register_key("tool-B", _make_pem())
        km.register_key("tool-B", _make_pem())
        assert len(km.get_keys("tool-A")) == 1
        assert len(km.get_keys("tool-B")) == 2

    def test_rotation_preserves_history(self, km: KeyManagement):
        """After multiple rotations, all keys remain in get_keys."""
        pem0 = _make_pem()
        kid0 = km.register_key("tool-1", pem0)
        pem1 = _make_pem()
        kid1 = km.rotate_key("tool-1", pem1, old_key_id=kid0, grace_days=30)
        pem2 = _make_pem()
        kid2 = km.rotate_key("tool-1", pem2, old_key_id=kid1, grace_days=30)
        keys = km.get_keys("tool-1")
        assert len(keys) == 3
        all_ids = {k.key_id for k in keys}
        assert {kid0, kid1, kid2} <= all_ids


# ── Edge cases ────────────────────────────────────────────────────


class TestEdgeCases:
    """Test edge cases and invalid inputs."""

    def test_empty_tool_id_rejected(self, km: KeyManagement):
        """Empty tool_id is rejected."""
        pem = _make_pem()
        with pytest.raises(KeyManagementError, match="tool_id"):
            km.register_key("", pem)
        with pytest.raises(KeyManagementError, match="tool_id"):
            km.register_key("   ", pem)

    def test_empty_public_key_rejected(self, km: KeyManagement):
        """Empty public_key is rejected."""
        with pytest.raises(KeyManagementError, match="public_key"):
            km.register_key("tool-1", "")
        with pytest.raises(KeyManagementError, match="public_key"):
            km.register_key("tool-1", b"   ")

    def test_invalid_public_key_type_rejected(self, km: KeyManagement):
        """A non-str/bytes public_key is rejected."""
        with pytest.raises(KeyManagementError, match="public_key"):
            km.register_key("tool-1", 12345)  # type: ignore[arg-type]
        with pytest.raises(KeyManagementError, match="public_key"):
            km.register_key("tool-1", None)  # type: ignore[arg-type]

    def test_get_keys_empty_tool_id(self, km: KeyManagement):
        """get_keys with an empty tool_id returns an empty list (no error)."""
        assert km.get_keys("") == []

    def test_persistence_across_instances(self, tmp_path: Path):
        """Data persists across KeyManagement instances (same DB file)."""
        db = tmp_path / "persist.db"
        km1 = KeyManagement(db_path=db)
        pem = _make_pem()
        kid = km1.register_key("tool-1", pem)

        km2 = KeyManagement(db_path=db)
        keys = km2.get_keys("tool-1")
        assert len(keys) == 1
        assert keys[0].key_id == kid


# ── signing.py integration ────────────────────────────────────────


class TestSigningIntegration:
    """Test verify_with_keys and PackageVerifier.verify_with_key_management."""

    def test_verify_with_keys_single_key_success(self):
        """verify_with_keys succeeds with the correct key."""
        priv, pub = generate_key_pair()
        payload = {"name": "plugin", "version": "1.0"}
        sig = sign_payload(payload, priv)
        assert verify_with_keys(payload, sig, [pub]) is True

    def test_verify_with_keys_multiple_one_matches(self):
        """verify_with_keys succeeds if any key matches."""
        priv, pub = generate_key_pair()
        _, pub2 = generate_key_pair()
        _, pub3 = generate_key_pair()
        payload = {"name": "plugin"}
        sig = sign_payload(payload, priv)
        assert verify_with_keys(payload, sig, [pub2, pub3, pub]) is True

    def test_verify_with_keys_none_match_raises(self):
        """verify_with_keys raises if no key matches."""
        priv, _ = generate_key_pair()
        _, pub2 = generate_key_pair()
        _, pub3 = generate_key_pair()
        payload = {"name": "plugin"}
        sig = sign_payload(payload, priv)
        with pytest.raises(SignatureError, match="all 2 key"):
            verify_with_keys(payload, sig, [pub2, pub3])

    def test_verify_with_keys_empty_list_raises(self):
        """verify_with_keys raises on an empty key list."""
        with pytest.raises(SignatureError, match="no public keys"):
            verify_with_keys({"a": 1}, "00" * 64, [])

    def test_verify_with_key_management_success(self, km: KeyManagement):
        """verify_with_key_management verifies with a registered key."""
        priv, pub = generate_key_pair()
        pub_pem = public_key_to_pem(pub)
        km.register_key("tool-1", pub_pem)

        payload = {"name": "plugin", "version": "2.0"}
        sig = sign_payload(payload, priv)

        verifier = PackageVerifier()
        assert verifier.verify_with_key_management(payload, sig, "tool-1", km) is True

    def test_verify_with_key_management_rotation_overlap(self, km: KeyManagement):
        """During rotation, signatures from both old and new keys verify."""
        # Register old key.
        priv_old, pub_old = generate_key_pair()
        old_pem = public_key_to_pem(pub_old)
        old_kid = km.register_key("tool-1", old_pem)

        # Rotate to new key.
        priv_new, pub_new = generate_key_pair()
        new_pem = public_key_to_pem(pub_new)
        km.rotate_key("tool-1", new_pem, old_key_id=old_kid, grace_days=30)

        payload = {"name": "plugin", "version": "3.0"}
        sig_old = sign_payload(payload, priv_old)
        sig_new = sign_payload(payload, priv_new)

        verifier = PackageVerifier()
        # Both signatures should verify (rotation overlap).
        assert verifier.verify_with_key_management(payload, sig_old, "tool-1", km) is True
        assert verifier.verify_with_key_management(payload, sig_new, "tool-1", km) is True

    def test_verify_with_key_management_blacklisted_rejected(self, km: KeyManagement):
        """A signature from a blacklisted key is rejected."""
        priv, pub = generate_key_pair()
        pub_pem = public_key_to_pem(pub)
        kid = km.register_key("tool-1", pub_pem)
        km.blacklist_key("tool-1", kid, reason="compromised")

        payload = {"name": "plugin"}
        sig = sign_payload(payload, priv)

        verifier = PackageVerifier()
        with pytest.raises(SignatureError, match="blacklisted"):
            verifier.verify_with_key_management(payload, sig, "tool-1", km)

    def test_verify_with_key_management_no_keys(self, km: KeyManagement):
        """Verification fails when the tool has no keys."""
        verifier = PackageVerifier()
        with pytest.raises(SignatureError, match="no active keys"):
            verifier.verify_with_key_management({"a": 1}, "00" * 64, "no-tool", km)

    def test_verify_with_key_management_all_blacklisted(self, km: KeyManagement):
        """Verification fails when all keys are blacklisted."""
        priv, pub = generate_key_pair()
        pub_pem = public_key_to_pem(pub)
        kid = km.register_key("tool-1", pub_pem)
        km.blacklist_key("tool-1", kid, reason="leaked")

        payload = {"name": "plugin"}
        sig = sign_payload(payload, priv)
        verifier = PackageVerifier()
        with pytest.raises(SignatureError, match="blacklisted"):
            verifier.verify_with_key_management(payload, sig, "tool-1", km)

    def test_verify_with_key_management_revoked_key_excluded(self, km: KeyManagement):
        """A revoked (non-blacklisted) key is excluded from candidates."""
        priv, pub = generate_key_pair()
        pub_pem = public_key_to_pem(pub)
        kid = km.register_key("tool-1", pub_pem)
        km.revoke_key("tool-1", kid)

        payload = {"name": "plugin"}
        sig = sign_payload(payload, priv)
        verifier = PackageVerifier()
        # No active keys left → raises.
        with pytest.raises(SignatureError, match="no active keys"):
            verifier.verify_with_key_management(payload, sig, "tool-1", km)

    def test_verify_with_key_management_partial_blacklist(self, km: KeyManagement):
        """If one key is blacklisted, other active keys still work."""
        # Key 1 (will be blacklisted).
        priv1, pub1 = generate_key_pair()
        kid1 = km.register_key("tool-1", public_key_to_pem(pub1))
        # Key 2 (remains valid).
        priv2, pub2 = generate_key_pair()
        km.register_key("tool-1", public_key_to_pem(pub2))

        km.blacklist_key("tool-1", kid1, reason="compromised")

        payload = {"name": "plugin", "v": 1}
        # Signature from key 2 should still verify.
        sig2 = sign_payload(payload, priv2)
        verifier = PackageVerifier()
        assert verifier.verify_with_key_management(payload, sig2, "tool-1", km) is True

        # Signature from blacklisted key 1 should be rejected (no key matches).
        sig1 = sign_payload(payload, priv1)
        with pytest.raises(SignatureError):
            verifier.verify_with_key_management(payload, sig1, "tool-1", km)