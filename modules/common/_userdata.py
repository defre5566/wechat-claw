"""用户数据面通用读写（私有，不导出）。

机制：`.config/<name>.json` 存数据，写前自动备份旧值到 `<name>.prev.json`；
`undo` = 恢复到上一次修改（prev 换回并清空）。供各用户数据面模块复用。
"""
from __future__ import annotations

import json

from bridge.config import PROJECT_ROOT

CONFIG_DIR = PROJECT_ROOT / ".config"


def _path(name: str) -> Path:
    return CONFIG_DIR / f"{name}.json"


def _prev_path(name: str) -> Path:
    return CONFIG_DIR / f"{name}.prev.json"


def load(name: str, default=None):
    """读数据文件；不存在/损坏返回 default。"""
    try:
        p = _path(name)
        if p.is_file():
            return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        pass
    return default


def save(name: str, data) -> bool:
    """写数据（写前自动备份旧值到 .prev.json）；成功返回 True。"""
    try:
        target = _path(name)
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.is_file():
            _prev_path(name).write_text(target.read_text(encoding="utf-8"), encoding="utf-8")
        target.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        return True
    except Exception:
        return False


def undo(name: str, default=None):
    """撤销 = 恢复到上一次修改（prev 换回并清）；无 prev 返回 default。"""
    try:
        prev = _prev_path(name)
        if prev.is_file():
            data = json.loads(prev.read_text(encoding="utf-8"))
            _path(name).write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
            prev.unlink(missing_ok=True)
            return data
    except Exception:
        pass
    return default
