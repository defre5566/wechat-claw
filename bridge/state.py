"""state：纯文件 IO 层（无闭包依赖，全部模块级函数）。

- 推送 token：生成 / 读取（含 chmod 600）
- retry 队列 / 调度状态：落盘读写
- 会话状态：读取推送目标
"""
from __future__ import annotations

import json
import os
import secrets
import sys
import time
from pathlib import Path

from modules.common.config import get as get_cfg

WORKDIR = Path.home() / "wechat-claw"
ARCHIVE_DIR = WORKDIR / "_archive"
SDK_DIR = WORKDIR / "agent-SDK"
TOKEN_FILE = SDK_DIR / "push_token"
SESSION_STATE_FILE = WORKDIR / ".session_state.json"
SCHED_STATE_FILE = SDK_DIR / "scheduler_state.json"
RETRY_FILE = SDK_DIR / "retry_queue.json"

SESSION_TTL_SECONDS = get_cfg("session.ttl_seconds")         # 5 小时会话窗（可配）
PERM_TIMEOUT_SECONDS = get_cfg("session.perm_timeout_seconds")  # 权限确认超时（默认拒绝）


def load_or_create_token() -> str:
    """读取推送 token；不存在则生成随机 token 写入文件（0600，幂等）。"""
    if TOKEN_FILE.exists():
        tok = TOKEN_FILE.read_text().strip()
        if tok:
            TOKEN_FILE.chmod(0o600)
            return tok
    SDK_DIR.mkdir(parents=True, exist_ok=True)
    tok = secrets.token_hex(32)
    TOKEN_FILE.write_text(tok)
    TOKEN_FILE.chmod(0o600)
    print(f"[push] 已生成新 token: {TOKEN_FILE}")
    return tok


def load_retry_queue() -> list[list[str]]:
    """加载落盘待发队列（conversation_id, text）。"""
    try:
        if RETRY_FILE.exists():
            data = json.loads(RETRY_FILE.read_text())
            return [list(x) for x in data if isinstance(x, (list, tuple)) and len(x) >= 2]
    except Exception as e:
        print(f"[push] 读取待发队列失败: {e}", file=sys.stderr)
    return []


def save_retry_queue(items: list[list[str]]) -> None:
    try:
        SDK_DIR.mkdir(parents=True, exist_ok=True)
        RETRY_FILE.write_text(json.dumps(items))
    except Exception as e:
        print(f"[push] 保存待发队列失败: {e}", file=sys.stderr)


def load_sched_state() -> dict:
    try:
        if SCHED_STATE_FILE.is_file():
            return json.loads(SCHED_STATE_FILE.read_text())
    except Exception as e:
        print(f"[sched] 读取状态失败: {e}", file=sys.stderr)
    return {}


def save_sched_state(state: dict) -> None:
    try:
        SDK_DIR.mkdir(parents=True, exist_ok=True)
        SCHED_STATE_FILE.write_text(json.dumps(state))
    except Exception as e:
        print(f"[sched] 保存状态失败: {e}", file=sys.stderr)


def target_conversation_ids() -> list[str]:
    """从会话状态文件取所有 conversation_id 作为推送目标（已归档的已移除）。"""
    try:
        if SESSION_STATE_FILE.exists():
            data = json.loads(SESSION_STATE_FILE.read_text())
            return [k for k in data if isinstance(k, str) and k]
    except Exception as e:
        print(f"[push] 读取会话状态失败: {e}", file=sys.stderr)
    return []


def targets_for_text(text: str) -> tuple[str, str]:
    """返回 (conversation_id, text)；取最近活跃会话，失败给空 id。"""
    try:
        data = {}
        if SESSION_STATE_FILE.exists():
            data = json.loads(SESSION_STATE_FILE.read_text())
    except Exception:
        data = {}
    if not data:
        return ("", text)
    conv = max(data, key=lambda k: data.get(k, 0))
    return (conv, text)