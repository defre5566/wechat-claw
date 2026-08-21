#!/usr/bin/env python3
"""生成模块源签名密钥对（作者侧，一次性；私钥绝不入库！）。

用法:
    python scripts/gen_signing_key.py

输出:
- 私钥（hex）：复制到你自己安全的密钥管理位置（绝不提交到任何仓库）
- 公钥（hex）：填入 bridge/config.py 的 SIGNING_PUBLIC_KEY（提交，公钥公开无风险）

之后每次发版模块库仓库，运行:
    python scripts/sign_manifest.py <模块库目录> <私钥hex>
"""
from __future__ import annotations

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

key = Ed25519PrivateKey.generate()
priv = key.private_bytes(
    serialization.Encoding.Raw, serialization.PrivateFormat.Raw, serialization.NoEncryption()
)
pub = key.public_key().public_bytes(
    serialization.Encoding.Raw, serialization.PublicFormat.Raw
)

print("=" * 60)
print("私钥（hex）—— 妥善保管，绝不提交到任何仓库！")
print(priv.hex())
print()
print("公钥（hex）—— 填入 bridge/config.py SIGNING_PUBLIC_KEY：")
print(pub.hex())
print("=" * 60)
print("发版签名：python scripts/sign_manifest.py <模块库目录> <私钥hex>")