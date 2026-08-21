"""Pluggable account state persistence."""

from __future__ import annotations

import base64
import json
import logging
import os
import secrets
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

# wechat-claw 补丁：支持 WECHAT_AGENT_SDK_STATE_DIR 环境变量重定向存储目录
# （默认 ~/.wechat-agent-sdk，部署时由 bridge.config 收敛到数据根）
DEFAULT_STATE_DIR = Path(os.environ.get("WECHAT_AGENT_SDK_STATE_DIR")
                            or (Path.home() / ".wechat-agent-sdk"))

# wechat-claw 补丁：accounts.json 静态加密（AES-GCM，密钥 = WECHAT_AGENT_SDK_KEY_FILE
# 指向的 16/24/32B 文件，与 wechat-claw crypto.key 同源；密文带 enc:v1: 前缀，
# 旧明文文件兼容读取、下次保存自动转密；SDK 无 cryptography/密钥时明文运行并告警）
_ENCRYPT_PREFIX = "enc:v1:"


def _make_cipher() -> Optional[Any]:
    try:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    except ImportError:
        logger.warning("[storage] 无 cryptography，accounts.json 将明文保存")
        return None
    key_file = os.environ.get("WECHAT_AGENT_SDK_KEY_FILE")
    if not key_file:
        logger.warning("[storage] 未设置 WECHAT_AGENT_SDK_KEY_FILE，accounts.json 将明文保存")
        return None
    try:
        raw = Path(key_file).read_bytes()
        if len(raw) not in (16, 24, 32):
            logger.warning("[storage] 密钥长度异常（%d B），accounts.json 将明文保存", len(raw))
            return None
        return AESGCM(raw)
    except OSError as e:
        logger.warning("[storage] 读取密钥失败: %s，accounts.json 将明文保存", e)
        return None


def _encrypt_text(plain: str, cipher: Any) -> str:
    nonce = secrets.token_bytes(12)
    ct = cipher.encrypt(nonce, plain.encode("utf-8"), None)
    return _ENCRYPT_PREFIX + base64.b64encode(nonce + ct).decode("ascii")


def _decrypt_text(raw: str, cipher: Any) -> Optional[str]:
    if not raw.startswith(_ENCRYPT_PREFIX):
        return None  # 旧明文格式（兼容首读）
    try:
        blob = base64.b64decode(raw[len(_ENCRYPT_PREFIX):])
        nonce, ct = blob[:12], blob[12:]
        return cipher.decrypt(nonce, ct, None).decode("utf-8")
    except Exception:
        logger.warning("[storage] accounts.json 解密失败（密钥变更？），按未登录处理")
        return None


class AccountStorage(ABC):
    """Abstract interface for persisting account state (token, cursor, metadata)."""

    @abstractmethod
    async def load_token(self, account_id: str) -> Optional[str]:
        ...

    @abstractmethod
    async def save_token(self, account_id: str, token: str) -> None:
        ...

    @abstractmethod
    async def load_cursor(self, account_id: str) -> Optional[str]:
        ...

    @abstractmethod
    async def save_cursor(self, account_id: str, cursor: str) -> None:
        ...

    async def load_meta(self, account_id: str) -> Optional[dict]:
        """Load account metadata (bot_id, base_url, etc.)."""
        return None

    async def save_meta(self, account_id: str, meta: dict) -> None:
        """Save account metadata."""

    async def close(self) -> None:
        """Close underlying connections (Redis, SQLite, etc.)."""


class JsonFileStorage(AccountStorage):
    """
    Simple JSON file persistence.

    Stores data in ``~/.wechat-agent-sdk/accounts.json``.
    """

    def __init__(self, state_dir: Path = DEFAULT_STATE_DIR):
        self._state_dir = state_dir
        self._file = state_dir / "accounts.json"
        self._data: Optional[dict] = None

    def _load(self) -> dict:
        if self._data is not None:
            return self._data

        if self._file.exists():
            try:
                raw = self._file.read_text()
                cipher = _make_cipher()
                if raw.startswith(_ENCRYPT_PREFIX):
                    if cipher is None:
                        logger.warning("[storage] 无法解密 accounts.json（无密钥/cryptography），按未登录处理")
                        self._data = {}
                        return self._data
                    plain = _decrypt_text(raw, cipher)
                    self._data = json.loads(plain) if plain is not None else {}
                else:
                    self._data = json.loads(raw)  # 兼容：旧明文文件
            except (json.JSONDecodeError, OSError) as e:
                logger.warning("[storage] accounts.json 读取失败: %s", e)
                self._data = {}
        else:
            self._data = {}

        return self._data

    def _save(self) -> None:
        self._state_dir.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(self._data or {}, indent=2)
        cipher = _make_cipher()
        if cipher is not None:
            payload = _encrypt_text(payload, cipher)  # 密钥可用 → 密文落盘
        else:
            logger.warning("[storage] accounts.json 将以明文保存（无可用密钥）")
        self._file.write_text(payload)
        # 敏感凭据：收紧到 0600（wechat-claw 补丁；默认 umask 644 可被本机其他用户读取）
        try:
            self._file.chmod(0o600)
        except OSError:
            pass

    def _get_account(self, account_id: str) -> dict:
        data = self._load()
        return data.setdefault(account_id, {})

    async def load_token(self, account_id: str) -> Optional[str]:
        return self._get_account(account_id).get("token") or None

    async def save_token(self, account_id: str, token: str) -> None:
        self._get_account(account_id)["token"] = token
        self._save()

    async def load_cursor(self, account_id: str) -> Optional[str]:
        return self._get_account(account_id).get("cursor") or None

    async def save_cursor(self, account_id: str, cursor: str) -> None:
        self._get_account(account_id)["cursor"] = cursor
        self._save()

    async def load_meta(self, account_id: str) -> Optional[dict]:
        return self._get_account(account_id).get("meta") or None

    async def save_meta(self, account_id: str, meta: dict) -> None:
        self._get_account(account_id)["meta"] = meta
        self._save()
