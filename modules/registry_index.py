"""模块注册索引：扫描 modules/*/module.json 动态汇总。

真源 = 各模块自己的 module.json；本模块只是生成物（每次调用实时扫描，
模块目录增删自动反映，基础设施不随模块增长而膨胀）。供 scheduler / push_server 读取。
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

MODULES_DIR = Path(__file__).resolve().parent


def _token_hash(name: str) -> str | None:
    tok = MODULES_DIR / name / "token"
    if tok.is_file():
        try:
            return hashlib.sha256(tok.read_text().strip().encode()).hexdigest()
        except OSError:
            return None
    return None


def build_index() -> dict:
    """返回 {模块名: {name, purpose, spec, schedule, retry, args, token_hash}}。"""
    index: dict[str, dict] = {}
    for mod_dir in sorted(MODULES_DIR.iterdir()):
        if not mod_dir.is_dir():
            continue
        mj = mod_dir / "module.json"
        if not mj.is_file():
            continue
        try:
            data = json.loads(mj.read_text(encoding="utf-8"))
        except Exception:
            continue
        name = data.get("name") or mod_dir.name
        index[name] = {
            "name": name,
            "purpose": data.get("purpose", ""),
            "spec": data.get("spec", "规范.md"),
            "schedule": data.get("schedule", []),
            "retry": data.get("retry"),
            "token_hash": _token_hash(name),
        }
    return index


def load(name: str) -> dict | None:
    """按名取单个模块配置；不存在返回 None。"""
    return build_index().get(name)