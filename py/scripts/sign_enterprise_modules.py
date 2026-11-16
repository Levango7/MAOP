#!/usr/bin/env python3
"""Sign enterprise modules with the commercial team's Ed25519 private key.

Produces ``maop/enterprise/_integrity_manifest.json``, containing:
  - SHA-256 of every enterprise module (except ``__init__.py`` / ``keys/``)
  - A single Ed25519 signature over the canonicalised JSON

The manifest is verified by :func:`maop.enterprise._verify_module_integrity`
at enterprise-feature activation time.

USAGE (commercial team only — requires the private key)::

    python scripts/sign_enterprise_modules.py \\
        --private-key ~/.maop/keys/prod_private_key.pem

SECURITY MODEL: this raises the cost of casual tampering — a user who
edits ``enterprise/rbac.py`` must either also edit ``license.py``'s public
key (which would break normal license validation), or patch this verifier
out of ``edition.py``. This is *defence-in-depth*, not absolute protection:
determined attackers can still strip checks. True hardening requires
optionally obfuscated releases (see docs/enterprise/obfuscation-guide.md).
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

_ENT_MODULES_GLOB = "maop/enterprise/**/*.py"

# Modules excluded from the manifest.
# - __init__.py: import-time side effects trigger the verify that reads this manifest
# - license.py: contains the validator itself; hash-checking the checker is self-
#   defeating (attacker who patches license.py disables the check), and legitimate
#   docstring/comment edits on license.py during development would false-positive.
_EXCLUDED = {"__init__.py", "license.py"}


def canonical_payload(files_hashes: dict[str, str], signed_at: str) -> bytes:
    """Build the canonical JSON payload to be signed.

    Deterministic key order + no whitespace ensures byte-identical output
    across platforms, so signature verification is stable.
    """
    payload = {
        "files": files_hashes,
        "signed_at": signed_at,
        "tool": "sign_enterprise_modules.py",
        "version": 1,
    }
    return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--private-key",
        required=True,
        help="Path to Ed25519 private key (PEM) used for signing",
    )
    parser.add_argument(
        "--root",
        default=None,
        help="Root directory containing maop/enterprise/ (default: repo py/ dir). "
             "Used by obfuscation pipeline to sign OBFUSCATED files, not source.",
    )
    args = parser.parse_args()

    priv_path = Path(args.private_key)
    if not priv_path.exists():
        print(f"ERROR: private key not found: {priv_path}", file=sys.stderr)
        return 1

    # Root: default to repo py/ dir; --root switches to staging dir (e.g. dist/obf)
    repo_root = Path(args.root).resolve() if args.root else Path(__file__).resolve().parent.parent
    manifest_path = repo_root / "maop" / "enterprise" / "_integrity_manifest.json"

    # Hash every enterprise module except excluded names
    files_hashes: dict[str, str] = {}
    for file_path in sorted(repo_root.glob(_ENT_MODULES_GLOB)):
        rel = file_path.relative_to(repo_root).as_posix()
        if "__pycache__" in rel or any(file_path.name == ex for ex in _EXCLUDED):
            continue
        digest = hashlib.sha256(file_path.read_bytes()).hexdigest()
        files_hashes[rel] = digest

    if not files_hashes:
        print("ERROR: no enterprise modules found", file=sys.stderr)
        return 1

    # Load private key
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    key_data = priv_path.read_bytes()
    # Skip comment prefix if present
    if key_data.startswith(b"DEVELOPMENT"):
        key_data = b"\n".join(key_data.split(b"\n")[1:])
    private_key = serialization.load_pem_private_key(key_data, password=None)
    if not isinstance(private_key, Ed25519PrivateKey):
        print("ERROR: key is not Ed25519", file=sys.stderr)
        return 1

    signed_at = datetime.now(timezone.utc).isoformat()
    payload = canonical_payload(files_hashes, signed_at)
    signature = private_key.sign(payload)

    manifest = {
        "version": 1,
        "signed_at": signed_at,
        "files": files_hashes,
        "signature": base64.urlsafe_b64encode(signature).decode("ascii"),
        "algorithm": "Ed25519",
    }

    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(f"Signed {len(files_hashes)} modules at {signed_at}")
    print(f"Manifest written to: {manifest_path}")
    print(f"Signature (base64url, first 32 chars): {manifest['signature'][:32]}...")
    return 0


if __name__ == "__main__":
    sys.exit(main())
