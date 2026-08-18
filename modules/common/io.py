"""防重/状态 IO + 共享数据读写（数据公共库的落盘层）。

- load_sent_json / save_sent_json：模块防重与状态文件读写
- shared_load / shared_save：跨模块偶发数据交换（唯一通道，禁止模块互读文件）
- prune_state_file：**已迁 bridge/state.py**（bridge 必须能不依赖模块运行），
  此处薄包装 re-export——worker 骨架 `from common import prune_state_file` 零破坏
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

from bridge.state import _keep_key, _keep_ts_key, prune_state_file  # noqa: F401  re-export

SHARED_DIR = Path(__file__).resolve().parent / "shared"


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