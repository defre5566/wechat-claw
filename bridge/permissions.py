"""模块权限汇总（豁免模型·模块化定稿）。

规则：
- 模块数据区 `modules/modules_data/<name>/**`：无条件放行给该模块（运行时目录规则保证，无需声明）
- 模块代码/本应用之外：默认禁止；模块在 module.json 声明 `permissions`（越界申请）才放行
- 设置中 type=path 字段（如 Obsidian vault_path）：保存后**自动豁免**（无需手动声明）
- 用户负责安全：不安装来路不明的模块

产物：`.config/module-permissions.json`（opencode permission.edit/write 片段，供部署合并进 opencode.jsonc）。
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from bridge.config import MODULES_ROOT, WORK_ROOT

MODULES_DIR = MODULES_ROOT
DATA_ROOT = MODULES_DIR / "modules_data"
CONFIG_DIR = WORK_ROOT / ".config"
PERMS_FILE = CONFIG_DIR / "module-permissions.json"

# 测试隔离点
if os.environ.get("OPENCODE_PERMS_ROOT"):
    MODULES_DIR = Path(os.environ["OPENCODE_PERMS_ROOT"]) / "modules"
    DATA_ROOT = MODULES_DIR / "modules_data"
    CONFIG_DIR = Path(os.environ["OPENCODE_PERMS_ROOT"]) / ".config"
    PERMS_FILE = CONFIG_DIR / "module-permissions.json"


def module_data_dir(name: str) -> Path:
    """模块用户数据目录 modules/modules_data/<name>/。"""
    return DATA_ROOT / name


def _load_json(path: Path) -> dict | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except Exception:
        return None


def _resolve(p: str) -> str:
    """展开 ~ 并转为绝对路径（保留通配符）；空值返回空串。"""
    p = (p or "").strip()
    if not p:
        return ""
    p = os.path.expanduser(p)
    if "**" in p or "*" in p:
        return p  # 通配符路径不 resolve（保持原样）
    return str(Path(p).resolve())


def _path_fields(mj: dict) -> list[str]:
    """module.json settings_schema 中 type=path 的字段 key（设置驱动豁免用）。"""
    keys: list[str] = []
    schema = mj.get("settings_schema") or []
    if isinstance(schema, list):
        for section in schema:
            if not isinstance(section, dict):
                continue
            for f in section.get("fields", []) or []:
                if isinstance(f, dict) and f.get("type") == "path" and f.get("key"):
                    keys.append(f["key"])
    return keys


def collect_permissions() -> dict:
    """扫描所有已注册模块，汇总豁免：
    - module.json 顶层 `permissions`（越界申请：edit/write 路径列表）
    - 设置中 path 字段值（自动豁免，如 vault_path）
    返回 {"edit": {<绝对路径>: "allow"}, "write": {<绝对路径>: "allow"}}。
    """
    perms: dict[str, dict[str, str]] = {"edit": {}, "write": {}}
    if not MODULES_DIR.is_dir():
        return perms
    for mod_dir in sorted(MODULES_DIR.iterdir()):
        mj = mod_dir / "module.json"
        if not mj.is_file():
            continue
        name = mod_dir.name
        data = _load_json(mj) or {}

        # 1. 模块自声明越界豁免
        declared = data.get("permissions") or {}
        if isinstance(declared, dict):
            for op in ("edit", "write"):
                for p in declared.get(op, []) or []:
                    rp = _resolve(str(p))
                    if rp:
                        perms[op][rp] = "allow"

        # 2. 设置 path 字段自动豁免
        settings = _load_json(module_data_dir(name) / "settings.json") or {}
        for key in _path_fields(data):
            val = settings.get(key)
            if isinstance(val, str) and val.strip():
                rp = _resolve(val)
                if rp:
                    perms["edit"][rp] = "allow"
                    perms["write"][rp] = "allow"
    return perms


def apply_permissions(perms: dict | None = None) -> bool:
    """写 .config/module-permissions.json（opencode 权限片段）。"""
    perms = perms if perms is not None else collect_permissions()
    try:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        tmp = PERMS_FILE.with_name(PERMS_FILE.name + ".tmp")
        tmp.write_text(json.dumps(perms, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        tmp.replace(PERMS_FILE)  # 原子写
        return True
    except OSError:
        return False


def refresh_permissions() -> bool:
    """重算并落盘（模块安装/设置保存/卸载时调用）。"""
    return apply_permissions(collect_permissions())
