"""Tests for maop.enterprise.license.verify_module_integrity.

Covers the anti-tamper manifest pipeline:
  - intact module set verifies OK
  - modified module content is detected
  - forged manifest signature is rejected
  - MAOP_SKIP_INTEGRITY escape hatch works
  - missing manifest degrades (non-strict) / raises (strict)
"""

from __future__ import annotations

import base64
import hashlib
import json
from pathlib import Path

import pytest

# H4 修复：将 importorskip 改为显式 pytest.skip，让测试报告显式统计跳过数。
pytest.skip(reason="maop.enterprise 未发布", allow_module_level=True)
import maop.enterprise.license as license_mod
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey


@pytest.fixture()
def signed_tree(tmp_path, monkeypatch):
    """Build a fake maop/enterprise tree, sign a manifest, patch paths to it."""
    ent = tmp_path / "maop" / "enterprise"
    ent.mkdir(parents=True)
    (ent / "keys").mkdir()

    # Two fake modules
    (ent / "alpha.py").write_text("X = 1\n", encoding="utf-8")
    (ent / "beta.py").write_text("Y = 2\n", encoding="utf-8")

    # Fresh keypair; patch the public key path to it
    priv = Ed25519PrivateKey.generate()
    pub_pem = priv.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    (ent / "keys" / "public_key.pem").write_bytes(pub_pem)
    monkeypatch.setattr(license_mod, "_PUBLIC_KEY_PATH", ent / "keys" / "public_key.pem")

    def _sign() -> Path:
        files = {}
        for f in sorted(ent.glob("*.py")):
            if f.name == "__init__.py":
                continue
            rel = f"maop/enterprise/{f.name}"
            files[rel] = hashlib.sha256(f.read_bytes()).hexdigest()
        signed_at = "2026-08-11T00:00:00+00:00"
        payload = json.dumps(
            {"files": files, "signed_at": signed_at,
             "tool": "sign_enterprise_modules.py", "version": 1},
            sort_keys=True, separators=(",", ":"),
        ).encode("utf-8")
        sig = priv.sign(payload)
        manifest = {
            "version": 1, "signed_at": signed_at, "files": files,
            "signature": base64.urlsafe_b64encode(sig).decode("ascii"),
            "algorithm": "Ed25519",
        }
        mpath = ent / "_integrity_manifest.json"
        mpath.write_text(json.dumps(manifest), encoding="utf-8")
        return mpath

    mpath = _sign()
    monkeypatch.setattr(license_mod, "_MANIFEST_PATH", mpath)

    class Ctx:
        ent_dir = ent
        manifest_path = mpath
        private_key = priv

    return Ctx


class TestModuleIntegrity:
    def test_intact_tree_verifies(self, signed_tree, monkeypatch):
        monkeypatch.delenv("MAOP_SKIP_INTEGRITY", raising=False)
        ok, reason = license_mod.verify_module_integrity(strict=False)
        assert ok is True
        assert reason == "ok"

    def test_tampered_module_detected(self, signed_tree, monkeypatch):
        monkeypatch.delenv("MAOP_SKIP_INTEGRITY", raising=False)
        (signed_tree.ent_dir / "alpha.py").write_text("X = 999  # cracked\n", encoding="utf-8")
        ok, reason = license_mod.verify_module_integrity(strict=False)
        assert ok is False
        assert "alpha.py" in reason

    def test_forged_signature_rejected(self, signed_tree, monkeypatch):
        monkeypatch.delenv("MAOP_SKIP_INTEGRITY", raising=False)
        # Re-sign manifest with a DIFFERENT key (attacker without the real private key)
        evil = Ed25519PrivateKey.generate()
        manifest = json.loads(signed_tree.manifest_path.read_text(encoding="utf-8"))
        payload = json.dumps(
            {"files": manifest["files"], "signed_at": manifest["signed_at"],
             "tool": "sign_enterprise_modules.py", "version": 1},
            sort_keys=True, separators=(",", ":"),
        ).encode("utf-8")
        manifest["signature"] = base64.urlsafe_b64encode(evil.sign(payload)).decode("ascii")
        signed_tree.manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

        ok, reason = license_mod.verify_module_integrity(strict=False)
        assert ok is False
        assert "signature" in reason

    def test_missing_manifest_strict_raises(self, signed_tree, monkeypatch):
        monkeypatch.delenv("MAOP_SKIP_INTEGRITY", raising=False)
        monkeypatch.setattr(license_mod, "_MANIFEST_PATH", signed_tree.ent_dir / "nope.json")
        ok, reason = license_mod.verify_module_integrity(strict=False)
        assert ok is False and "not found" in reason
        with pytest.raises(license_mod.ModuleTamperError):
            license_mod.verify_module_integrity(strict=True)

    def test_skip_env_shortcircuits(self, signed_tree, monkeypatch):
        monkeypatch.setenv("MAOP_SKIP_INTEGRITY", "1")
        # Even with a deleted manifest, skip wins
        signed_tree.manifest_path.unlink()
        ok, reason = license_mod.verify_module_integrity(strict=False)
        assert ok is True and reason == "skipped"
