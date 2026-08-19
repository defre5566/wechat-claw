"""state：纯文件 IO 层（无闭包依赖，全部模块级函数）。

- 推送 token：生成 / 读取（含 chmod 600）
- retry 队列 / 调度状态：落盘读写
- 会话状态：读取推送目标
- prune_state_file：状态文件按日期键修剪（通用状态 IO，bridge 自持；
  modules/common/io.py 薄包装 re-export，worker 骨架零破坏）
"""
from __future__ import annotations

import json
import os
import re
import secrets
import sys
import time
from datetime import date, timedelta
from pathlib import Path

from .config import get as get_cfg

WORKDIR = Path(__file__).resolve().parent.parent  # 项目根（相对定位，任意目录部署自洽）
ARCHIVE_DIR = WORKDIR / "_archive"
SDK_DIR = WORKDIR / "agent-SDK"
TOKEN_FILE = SDK_DIR / "push_token"
SESSION_STATE_FILE = WORKDIR / ".session_state.json"
SCHED_STATE_FILE = SDK_DIR / "scheduler_state.json"
RETRY_FILE = SDK_DIR / "retry_queue.json"

SESSION_TTL_SECONDS = get_cfg("session.ttl_seconds")         # 5 小时会话窗（可配）
PERM_TIMEOUT_SECONDS = get_cfg("session.perm_timeout_seconds")  # 权限确认超时（默认拒绝）

_DATE_PREFIX = re.compile(r"^(\d{4}-\d{2}-\d{2})")


def _load_json_file(path: Path) -> dict:
    try:
        if path.is_file():
            return json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:
        print(f"[state] 读文件失败 {path}: {e}", file=sys.stderr)
    return {}


def _save_json_file(path: Path, data: dict) -> bool:
    """原子写 JSON（tmp + os.replace，防写一半崩溃损坏文件，H3）。"""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, path)
        return True
    except Exception as e:
        print(f"[state] 写文件失败 {path}: {e}", file=sys.stderr)
        try:
            tmp.unlink(missing_ok=True)
        except Exception:
            pass
        return False


def prune_state_file(path: Path, days: int | None = None) -> None:
    """修剪防重/状态文件：仅保留最近 days 天键（递归，支持嵌套状态对象）。

    - 日期开头键：按 ISO 日期比较
    - 非日期键：值为 epoch 时间戳且早于 cutoff → 过期删除（如遗留的 ts 键）；
      last_ts（值实时）、_off 偏移缓存（分钟数）、字符串值一律保留
    """
    if days is None:
        days = get_cfg("scheduler.prune_days")
    data = _load_json_file(path)
    if not data:
        return
    cutoff = (date.today() - timedelta(days=days)).isoformat()
    cutoff_ts = time.time() - days * 86400
    keep = _prune_dict(data, cutoff, cutoff_ts)
    if len(keep) != len(data):
        _save_json_file(path, keep)


def _prune_dict(data: dict, cutoff: str, cutoff_ts: float) -> dict:
    out: dict = {}
    for k, v in data.items():
        if not _keep_key(k, cutoff):
            continue
        if isinstance(v, dict):
            out[k] = _prune_dict(v, cutoff, cutoff_ts)
        elif not _keep_ts_key(v, cutoff_ts):
            continue
        else:
            out[k] = v
    return out


def _keep_key(key: str, cutoff: str) -> bool:
    m = _DATE_PREFIX.match(key)
    if not m:
        return True  # 非日期键由 _keep_ts_key 判定
    return m.group(1) >= cutoff


def _keep_ts_key(value, cutoff_ts: float) -> bool:
    """非日期键保留规则：epoch 时间戳（≥1e9）且早于 cutoff → 过期删除。"""
    if isinstance(value, (int, float)) and value >= 1e9:
        return value >= cutoff_ts
    return True


def load_or_create_token() -> str:
    """读取推送 token；不存在则生成随机 token 写入文件（0600 原子创建，幂等）。"""
    if TOKEN_FILE.exists():
        tok = TOKEN_FILE.read_text().strip()
        if tok:
            try:
                TOKEN_FILE.chmod(0o600)
            except OSError:
                pass
            return tok
    SDK_DIR.mkdir(parents=True, exist_ok=True)
    tok = secrets.token_hex(32)
    # O_CREAT|O_EXCL + 0600：原子创建，避免"先写后 chmod"短暂 644 窗口
    fd = os.open(str(TOKEN_FILE), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(tok)
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
    """落盘调度状态（H3：走 _save_json_file 原子写，防半写损坏）。"""
    if not _save_json_file(SCHED_STATE_FILE, state):
        print(f"[sched] 保存状态失败", file=sys.stderr)


def target_conversation_ids() -> list[str]:
    """从会话状态文件取所有 conversation_id 作为推送目标（已归档的已移除）。"""
    try:
        if SESSION_STATE_FILE.exists():
            data = json.loads(SESSION_STATE_FILE.read_text())
            if isinstance(data, dict):
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
            if not isinstance(data, dict):
                data = {}
    except Exception:
        data = {}
    if not data:
        return ("", text)
    conv = max(data, key=lambda k: data.get(k, 0))
    return (conv, text)