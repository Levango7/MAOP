#!/usr/bin/env python3
"""Generate a MAOP Enterprise license key.

Usage:
    python scripts/generate_license.py \
        --customer "ACME Corp" \
        --expires 2027-07-25

The license key is printed to stdout and optionally saved to a file
with --output.

NOTE: This script uses the DEVELOPMENT private key by default, resolved
in order: --private-key > ~/.maop/keys/dev_private_key.pem > legacy
scripts/dev_private_key.pem. Production licenses MUST be signed with
the MAOP commercial team's private key (never committed to the repository).
"""

from __future__ import annotations

import argparse
import base64
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey


def generate_license(
    customer: str,
    expires_at: datetime,
    private_key_path: Path,
    issued_at: datetime | None = None,
    max_users: int | None = None,
    fingerprint: str | None = None,
    features: list[str] | None = None,
) -> str:
    """Generate a signed MAOP Enterprise license key."""
    if issued_at is None:
        issued_at = datetime.now(timezone.utc)

    # Load private key
    key_data = private_key_path.read_bytes()
    # Skip comment line if present
    if key_data.startswith(b"DEVELOPMENT"):
        lines = key_data.split(b"\n")
        key_data = b"\n".join(lines[1:])
    private_key = serialization.load_pem_private_key(key_data, password=None)

    if not isinstance(private_key, Ed25519PrivateKey):
        raise ValueError("Private key must be Ed25519")

    # Build payload
    payload = {
        "customer": customer,
        "edition": "enterprise",
        "issued_at": issued_at.isoformat(),
        "expires_at": expires_at.isoformat(),
    }
    if max_users is not None:
        payload["max_users"] = max_users
    if fingerprint is not None:
        payload["fingerprint"] = fingerprint
    if features is not None:
        payload["features"] = features

    payload_json = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    signature = private_key.sign(payload_json)

    # Encode
    payload_b64 = base64.urlsafe_b64encode(payload_json).rstrip(b"=").decode("ascii")
    sig_b64 = base64.urlsafe_b64encode(signature).rstrip(b"=").decode("ascii")

    return f"MAOP-ENT-{payload_b64}.{sig_b64}"


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate MAOP Enterprise license")
    parser.add_argument("--customer", required=True, help="Customer name")
    parser.add_argument("--expires", required=True, help="Expiry date (YYYY-MM-DD)")
    parser.add_argument("--private-key", default=None,
                        help="Path to Ed25519 private key PEM file (default: ~/.maop/keys/dev_private_key.pem)")
    parser.add_argument("--max-users", type=int, default=None, help="Max concurrent users")
    parser.add_argument("--fingerprint", default=None, help="Machine fingerprint binding")
    parser.add_argument("--output", default=None, help="Output file (default: stdout)")
    args = parser.parse_args()

    # Resolve private key path: CLI arg > ~/.maop/keys/ > legacy scripts/
    if args.private_key:
        key_path = Path(args.private_key)
    else:
        key_path = Path.home() / ".maop" / "keys" / "dev_private_key.pem"
        if not key_path.exists():
            # Fallback to legacy location
            legacy = Path("scripts/dev_private_key.pem")
            if legacy.exists():
                key_path = legacy
                print("WARNING: Using legacy key location scripts/dev_private_key.pem", file=sys.stderr)
                print("  Move to ~/.maop/keys/ for security: mv scripts/dev_private_key.pem ~/.maop/keys/", file=sys.stderr)

    if not key_path.exists():
        print(f"ERROR: Private key not found: {key_path}", file=sys.stderr)
        print("  Expected at ~/.maop/keys/dev_private_key.pem or specify --private-key", file=sys.stderr)
        return 1

    expires_at = datetime.strptime(args.expires, "%Y-%m-%d").replace(
        hour=23, minute=59, second=59, tzinfo=timezone.utc
    )

    license_key = generate_license(
        customer=args.customer,
        expires_at=expires_at,
        private_key_path=key_path,
        max_users=args.max_users,
        fingerprint=args.fingerprint,
    )

    if args.output:
        Path(args.output).write_text(license_key, encoding="utf-8")
        print(f"License written to {args.output}", file=sys.stderr)
    else:
        print(license_key)

    return 0


if __name__ == "__main__":
    sys.exit(main())
