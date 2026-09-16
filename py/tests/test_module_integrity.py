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

import maop.enterprise.license as license_mod
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey


@pytest.fixture()
def signed_tree(tmp_path, monkeypatch):
    """Sign a manifest over the REAL installed maop/enterprise modules.

    MAOS 防篡改校验（2026-08 强化）将模块文件哈希基准固定在
    ``Path(license.py).__file__.parent``（真实 enterprise 目录），
    manifest 可隔离到 tmp 路径。因此本 fixture 对真实安装的
    enterprise .py 文件计算哈希并签名（与 MAOS 侧 test_integrity.py
    同一模式），虚拟目录不再有效。
    """
    ent = Path(license_mod.__file__).resolve().parent

    # Fresh keypair; patch the public key path to it
    priv = Ed25519PrivateKey.generate()
    pub_pem = priv.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    key_dir = tmp_path / "keys"
    key_dir.mkdir()
    (key_dir / "public_key.pem").write_bytes(pub_pem)
    monkeypatch.setattr(license_mod, "_PUBLIC_KEY_PATH", key_dir / "public_key.pem")
    # P1-1 公钥指纹固定：临时公钥必须同步覆盖期望指纹，否则
    # _verify_public_key_fingerprint 用硬编码生产指纹拒绝它（fixture 即失败）。
    monkeypatch.setenv("MAOP_LICENSE_KEY_FP", hashlib.sha256(pub_pem).hexdigest())

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
        mpath = tmp_path / "_integrity_manifest.json"
        mpath.write_text(json.dumps(manifest), encoding="utf-8")
        return mpath

    mpath = _sign()
    monkeypatch.setattr(license_mod, "_MANIFEST_PATH", mpath)

    class Ctx:
        ent_dir = ent
        manifest_path = mpath
        private_key = priv
        # Pick a real module file (excluding __init__.py, which the
        # manifest signing tool skips) for tamper tests
        _mods = [f for f in sorted(ent.glob("*.py")) if f.name != "__init__.py"]
        sample_module = _mods[0] if _mods else None

    return Ctx


class TestModuleIntegrity:
    def test_intact_tree_verifies(self, signed_tree, monkeypatch):
        monkeypatch.delenv("MAOP_SKIP_INTEGRITY", raising=False)
        ok, reason = license_mod.verify_module_integrity(strict=False)
        assert ok is True
        assert reason == "ok"

    def test_tampered_module_detected(self, signed_tree, monkeypatch):
        """改 manifest 内的哈希为伪造值模拟模块被篡改（不动真实安装文件）。

        MAOS 校验的文件基准是真实安装目录，直接改写安装文件会污染
        运行环境，因此用「manifest 声明的哈希与实际文件不匹配」来
        模拟篡改检测路径。
        """
        monkeypatch.delenv("MAOP_SKIP_INTEGRITY", raising=False)
        manifest = json.loads(signed_tree.manifest_path.read_text(encoding="utf-8"))
        # 找一个真实文件并把 manifest 中的哈希改成伪造值
        real_file = signed_tree.sample_module
        rel = f"maop/enterprise/{real_file.name}"
        assert rel in manifest["files"]
        manifest["files"][rel] = "0" * 64  # forged hash
        # 用原私钥重新签名（篡改者持有私钥的场景），签名校验通过
        # 但哈希校验必须抓住不一致
        payload = json.dumps(
            {"files": manifest["files"], "signed_at": manifest["signed_at"],
             "tool": "sign_enterprise_modules.py", "version": 1},
            sort_keys=True, separators=(",", ":"),
        ).encode("utf-8")
        sig = signed_tree.private_key.sign(payload)
        manifest["signature"] = base64.urlsafe_b64encode(sig).decode("ascii")
        signed_tree.manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        ok, reason = license_mod.verify_module_integrity(strict=False)
        assert ok is False
        assert real_file.name in reason

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
