"""Marketplace 目录签名管线（M-X1 D1/D2 批准落地——首个签名目录的工具面）.

签名合同与消费侧严格镜像（``core/mcp/mcp_marketplace._verify_catalog_signature``
）：**被签载荷 = 目录条目去掉 ``signature_hex`` 后的 canonical JSON**，
Ed25519；``signer_key_id``（如提供）在签名**前**写入（属于被签载荷）。

用法::

    python scripts/sign_marketplace_catalog.py \
        --catalog deploy/marketplace/catalog.json \
        --key /path/to/signing_key.pem \
        --out deploy/marketplace/catalog.signed.json \
        [--verify-key config/marketplace_verify_key.pub] \
        [--key-id marketplace-prod]

行为（fail-closed 输出）：逐条签名 → 用 ``--verify-key`` 自验证全部条目 →
全部通过才写出 ``--out``；任一验证失败即退出 1 且**不产出文件**——
签名管线自己就是第一道消费侧验证。

密钥纪律：``--key`` 只读打开（部署方提供的签名私钥，本仓库不持有；
默认验签公钥 = 已随产品内置的 ``config/marketplace_verify_key.pub``，
D2① 通道）。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "py"))

from maop.core.marketplace.signing import (
    PackageVerifier,
    sign_payload,
)


def _load_entries(path: Path) -> tuple[list[dict], dict]:
    """读目录：``{"servers": [...]}`` 或裸数组；返回 (条目列表, 外层元数据)."""
    data = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(data, list):
        return list(data), {}
    if isinstance(data, dict) and isinstance(data.get("servers"), list):
        meta = {k: v for k, v in data.items() if k != "servers"}
        return list(data["servers"]), meta
    raise ValueError(
        "catalog must be a JSON array or {\"servers\": [...]}"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--catalog", required=True, help="输入目录 JSON（未签名）")
    parser.add_argument("--key", required=True, help="Ed25519 签名私钥 PEM（只读）")
    parser.add_argument("--out", required=True, help="签名后目录输出路径")
    parser.add_argument(
        "--verify-key",
        default=str(REPO / "config" / "marketplace_verify_key.pub"),
        help="自验证公钥（默认 = 随产品内置的 D2① 验签公钥）",
    )
    parser.add_argument(
        "--key-id", default="",
        help="signer_key_id（轮换模式 KeyManagement 的 tool_id；可空）",
    )
    args = parser.parse_args()

    from cryptography.hazmat.primitives.serialization import load_pem_private_key

    entries, meta = _load_entries(Path(args.catalog))
    if not entries:
        print("ERROR: catalog has no entries", file=sys.stderr)
        return 1
    private_key = load_pem_private_key(
        Path(args.key).read_bytes(), password=None
    )
    verifier = PackageVerifier(args.verify_key)

    signed: list[dict] = []
    for idx, entry in enumerate(entries):
        payload = dict(entry)
        payload.pop("signature_hex", None)
        if args.key_id:
            payload["signer_key_id"] = args.key_id
        payload["signature_hex"] = sign_payload(payload, private_key)
        # 自验证（消费侧同合同）：不过即整体失败，不产出半签名目录。
        verifier.verify_package(dict(payload))
        signed.append(payload)
        name = payload.get("name", f"entry-{idx}")
        print(f"[sign] {name}: {payload['signature_hex'][:16]}… verified")

    out = Path(args.out)
    if meta:
        meta["signed_entry_count"] = len(signed)
        out.write_text(
            json.dumps({**meta, "servers": signed}, ensure_ascii=False, indent=2)
            + "\n",
            encoding="utf-8",
        )
    else:
        out.write_text(
            json.dumps(signed, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    print(f"[done] {len(signed)} entries signed+verified → {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
