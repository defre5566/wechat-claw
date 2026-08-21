"""防重/状态 IO + 共享数据读写 + 通用小工具（数据公共库的落盘层/基础设施）。

- load_sent_json / save_sent_json：模块防重与状态文件读写
- shared_load / shared_save：跨模块偶发数据交换（唯一通道，禁止模块互读文件）
- load_json：读 JSON 失败回退默认（多模块曾各自复制，收敛单点）
- time_to_cron：'HH:MM' → cron（register/jobs 曾逐字重复，收敛单点）
- prune_state_file：**已迁 bridge/state.py**（bridge 必须能不依赖模块运行），
  此处薄包装 re-export——worker 骨架 `from common import prune_state_file` 零破坏
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

from bridge.config import MODULES_ROOT
from bridge.state import _keep_key, _keep_ts_key, prune_state_file  # noqa: F401  re-export

SHARED_DIR = MODULES_ROOT / "common" / "shared"


def load_json(path: Path, default=None):
    """读 JSON 文件；不存在/非法/非目标类型 → default（不抛错，调用方自行决定语义）。"""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, dict) or default is None:
            return data
        return default
    except Exception:
        return default


def time_to_cron(t) -> str | None:
    """'HH:MM' → cron（'MM HH * * *'）；非法/非字符串 → None。"""
    if not isinstance(t, str):
        return None
    try:
        hh, mm = t.strip().split(":")
        h, m = int(hh), int(mm)
        if not (0 <= h < 24 and 0 <= m < 60):
            return None
        return f"{m} {h} * * *"
    except Exception:
        return None


def load_sent_json(path: Path) -> dict:
    try:
        if path.is_file():
            return json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:
        print(f"[io] 读文件失败 {path}: {e}", file=sys.stderr)
    return {}


def save_sent_json(path: Path, data: dict) -> bool:
    """写防重/状态文件（A1：tmp + os.replace 原子写，防半写损坏）。成功返回 True。失败仅告警。"""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, path)
        return True
    except Exception as e:
        print(f"[io] 写文件失败 {path}: {e}", file=sys.stderr)
        try:
            tmp.unlink(missing_ok=True)
        except Exception:
            pass
        return False


def shared_load(name: str, max_age: float | None = None) -> dict:
    """读共享数据（modules/common/shared/<name>.json）。不存在返回 {}。

    max_age（秒，可选）：显式传入时校验 ts——数据过期（ts 距今 > max_age）返回 {}
    （铁律 4：缓存带 TTL；默认不过期向后兼容，消费方自管新鲜度）。
    """
    data = load_sent_json(SHARED_DIR / f"{name}.json")
    if max_age is not None and isinstance(data.get("ts"), (int, float)):
        try:
            if time.time() - float(data["ts"]) > max_age:
                return {}
        except (TypeError, ValueError):
            pass
    return data


def shared_save(name: str, data: dict) -> bool:
    """写共享数据；自动带 ts 时间戳。返回是否成功。"""
    data.setdefault("ts", time.time())
    return save_sent_json(SHARED_DIR / f"{name}.json", data)