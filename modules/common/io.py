"""防重/状态 IO + 共享数据读写（数据公共库的落盘层）。

- load_sent_json / save_sent_json：模块防重与状态文件读写
- shared_load / shared_save：跨模块偶发数据交换（唯一通道，禁止模块互读文件）
- prune_state_file：修剪天数可配置（config.yaml scheduler.prune_days，默认 30）
"""
from __future__ import annotations

import json
import re
import sys
import time
from datetime import date, timedelta
from pathlib import Path

from bridge.config import get

SHARED_DIR = Path(__file__).resolve().parent / "shared"

_DATE_PREFIX = re.compile(r"^(\d{4}-\d{2}-\d{2})")


def prune_state_file(path: Path, days: int | None = None) -> None:
    """修剪防重/状态文件：仅保留最近 days 天键（递归，支持嵌套状态对象）。

    - 日期开头键：按 ISO 日期比较
    - 非日期键：值为 epoch 时间戳且早于 cutoff → 过期删除（如遗留的 ts 键）；
      last_ts（值实时）、_off 偏移缓存（分钟数）、字符串值一律保留
    """
    if days is None:
        days = get("scheduler.prune_days")
    data = load_sent_json(path)
    if not data:
        return
    cutoff = (date.today() - timedelta(days=days)).isoformat()
    cutoff_ts = time.time() - days * 86400
    keep = _prune_dict(data, cutoff, cutoff_ts)
    if len(keep) != len(data):
        save_sent_json(path, keep)


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


def load_sent_json(path: Path) -> dict:
    try:
        if path.is_file():
            return json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:
        print(f"[io] 读文件失败 {path}: {e}", file=sys.stderr)
    return {}


def save_sent_json(path: Path, data: dict) -> bool:
    """写防重/状态文件；成功返回 True。失败仅告警（调用方可视情况感知）。"""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, ensure_ascii=False))
        return True
    except Exception as e:
        print(f"[io] 写文件失败 {path}: {e}", file=sys.stderr)
        return False


def shared_load(name: str) -> dict:
    """读共享数据（modules/common/shared/<name>.json）。不存在返回 {}。"""
    return load_sent_json(SHARED_DIR / f"{name}.json")


def shared_save(name: str, data: dict) -> bool:
    """写共享数据；自动带 ts 时间戳。返回是否成功。"""
    data.setdefault("ts", time.time())
    return save_sent_json(SHARED_DIR / f"{name}.json", data)