"""防重/状态 IO + 共享数据读写（数据公共库的落盘层）。

- load_sent_json / save_sent_json：模块防重与状态文件读写
- shared_load / shared_save：跨模块偶发数据交换（唯一通道，禁止模块互读文件）
- prune_state_file：**已迁 bridge/state.py**（bridge 必须能不依赖模块运行），
  此处薄包装 re-export——worker 骨架 `from common import prune_state_file` 零破坏
"""
from __future__ import annotations

import json
import os
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