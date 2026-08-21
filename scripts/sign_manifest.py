#!/usr/bin/env python3
"""对模块源仓库根部的 manifest.json 签名（Ed25519，作者发版时运行），产出 manifest.sig。

用法:
    python scripts/sign_manifest.py <模块库目录> <私钥hex>

说明:
- 签的是 manifest.json 原始字节；部署机以内置公钥验签（bridge.module_source.verify_manifest_signature）
- manifest.sig 与 manifest.json 同目录随仓库发布；发布后不要改动 manifest.json
  （改了要重新签名），否则部署机校验失败拒绝更新
"""
from __future__ import annotations

import sys
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey


def main() -> int:
    if len(sys.argv) != 3:
        print(__doc__)
        return 1
    root = Path(sys.argv[1]).resolve()
    priv_hex = sys.argv[2].strip()

    mani = root / "manifest.json"
    if not mani.is_file():
        print(f"[FAIL] 缺少 {mani}")
        return 1
    try:
        key = Ed25519PrivateKey.from_private_bytes(bytes.fromhex(priv_hex))
    except ValueError:
        print("[FAIL] 私钥不是合法 hex")
        return 1

    sig = key.sign(mani.read_bytes())
    target = root / "manifest.sig"
    target.write_text(sig.hex() + "\n", encoding="utf-8")
    print(f"[OK] 已签名 manifest.json → {target}（{len(sig)}B）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())