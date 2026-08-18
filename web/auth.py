"""管理密码：pbkdf2 哈希 + 会话 token（进程内存，30 分钟）。

- 存储：<项目根>/.config/admin.password，内容 "salt$hash"（hex），chmod 600
- 校验：恒定时间比较（hmac.compare_digest）
- 会话：wizard 进程内存表 {token: expires_at}；改密码清空所有会话；
  重启服务会话即失效（重新登录）
- 最少 6 位；密码不存在 = 未设置（向导/后台开放期，不要求密码）
"""
from __future__ import annotations

import hashlib
import hmac
import secrets
import time

from bridge.config import PROJECT_ROOT

PASSWORD_FILE = PROJECT_ROOT / ".config" / "admin.password"

PBKDF2_ITERATIONS = 100_000
SALT_BYTES = 32
SESSION_TTL_SECONDS = 30 * 60
MIN_PASSWORD_LEN = 6

# 会话表（进程内存；wizard.py 常驻期间有效）
_sessions: dict[str, float] = {}  # token -> expires_at


# ---------- 哈希 ----------

def hash_password(password: str) -> str:
    salt = secrets.token_hex(SALT_BYTES)
    digest = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), bytes.fromhex(salt), PBKDF2_ITERATIONS
    ).hex()
    return f"{salt}${digest}"


def verify_password(password: str, stored: str) -> bool:
    try:
        salt, digest = stored.split("$", 1)
        calc = hashlib.pbkdf2_hmac(
            "sha256", password.encode("utf-8"), bytes.fromhex(salt), PBKDF2_ITERATIONS
        ).hex()
        return hmac.compare_digest(calc, digest)
    except (ValueError, TypeError):
        return False


# ---------- 存储 ----------

def password_exists() -> bool:
    return PASSWORD_FILE.is_file()


def set_password(password: str) -> bool:
    """设置/重设管理密码（写新哈希，chmod 600）。"""
    if len(password) < MIN_PASSWORD_LEN:
        return False
    try:
        PASSWORD_FILE.parent.mkdir(parents=True, exist_ok=True)
        PASSWORD_FILE.write_text(hash_password(password), encoding="utf-8")
        PASSWORD_FILE.chmod(0o600)
        return True
    except OSError:
        return False


def check_password(password: str) -> bool:
    """校验密码；未设置时恒 False（需先 set_password）。"""
    if not password_exists():
        return False
    try:
        stored = PASSWORD_FILE.read_text(encoding="utf-8").strip()
    except OSError:
        return False
    return verify_password(password, stored)


def change_password(old: str, new: str) -> bool:
    """改密码：校验旧密码 → 写新哈希 → 清空所有会话。"""
    if not check_password(old):
        return False
    if not set_password(new):
        return False
    clear_sessions()
    return True


# ---------- 会话 ----------

def create_session() -> str:
    """登录成功 → 发会话 token（30 分钟，内存表）。"""
    token = secrets.token_hex(32)
    _sessions[token] = time.time() + SESSION_TTL_SECONDS
    _prune_sessions()
    return token


def check_session(token: str) -> bool:
    """校验会话 token（存在且未过期）。"""
    _prune_sessions()
    return token in _sessions


def clear_sessions() -> None:
    """清空全部会话（改密码/服务退出时）。"""
    _sessions.clear()


def _prune_sessions() -> None:
    now = time.time()
    expired = [t for t, exp in _sessions.items() if exp <= now]
    for t in expired:
        _sessions.pop(t, None)
