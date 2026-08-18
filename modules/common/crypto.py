"""通用加解密（AES-GCM 主密钥版）。

密钥：路径可配（config.yaml crypto.key_file，默认 <项目根>/.config/crypto.key，
相对项目根解析，自动生成，chmod 600；已配置 opencode deny 读取）。
密文头带版本字段 v1，为将来密钥轮换预留。仅用于隐私数据（如纪念日/生日）。
"""
from __future__ import annotations

import base64
import secrets
from pathlib import Path

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from bridge.config import get, resolve_path

KEY_FILE = resolve_path(get("crypto.key_file"))
KEY_DIR = KEY_FILE.parent


def _ensure_key() -> bytes:
    if KEY_FILE.is_file():
        raw = KEY_FILE.read_bytes()
        if len(raw) == 32:
            return raw
    KEY_DIR.mkdir(parents=True, exist_ok=True)
    key = secrets.token_bytes(32)
    KEY_FILE.write_bytes(key)
    KEY_FILE.chmod(0o600)
    return key


def encrypt(data: str, key: bytes | None = None) -> str:
    """加密为 base64 文本，格式：v1:<b64nonce>:<b64ciphertext>。"""
    key = key or _ensure_key()
    nonce = secrets.token_bytes(12)
    ct = AESGCM(key).encrypt(nonce, data.encode("utf-8"), None)
    return f"v1:{base64.b64encode(nonce).decode()}:{base64.b64encode(ct).decode()}"


def decrypt(ciphertext: str, key: bytes | None = None) -> str:
    """解密 encrypt 的产物；格式不符或密钥不符会抛异常。"""
    key = key or _ensure_key()
    version, nonce_b64, ct_b64 = ciphertext.split(":", 2)
    if version != "v1":
        raise ValueError(f"不支持的密文版本: {version}")
    nonce = base64.b64decode(nonce_b64)
    ct = base64.b64decode(ct_b64)
    pt = AESGCM(key).decrypt(nonce, ct, None)
    return pt.decode("utf-8")