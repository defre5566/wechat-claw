"""web admin 模块弹窗渲染数据装配测试（choice 候选 / 方向名校验 / prompt 接口行为）。

纯函数级：_valid_direction / _module_choices；register 路径 monkeypatch 隔离。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from web.handlers.admin import _valid_direction, _module_choices


# ---------- _valid_direction（防路径穿越白名单） ----------

def test_valid_direction_accepts():
    assert _valid_direction("科技")
    assert _valid_direction("Tech2026")
    assert _valid_direction("科技-2")
    assert _valid_direction("a_b")


def test_valid_direction_rejects():
    assert not _valid_direction("../etc")
    assert not _valid_direction("a/b")
    assert not _valid_direction("a\b")
    assert not _valid_direction("")
    assert not _valid_direction("a" * 25)          # 超 24 字符
    assert not _valid_direction("a b")


# ---------- _module_choices（候选 = directions 预设 + custom 自定义） ----------

def test_module_choices(tmp_path, monkeypatch):
    from modules import register
    mod_dir = tmp_path / "modules" / "Planner"
    (mod_dir).mkdir(parents=True)
    (mod_dir / "directions.json").write_text(
        json.dumps({"时政": {}, "热点": {}}), encoding="utf-8")
    custom = tmp_path / "modules" / "modules_data" / "Planner" / "prompts" / "custom"
    custom.mkdir(parents=True)
    (custom / "科技.json").write_text('{"prompt": "x"}', encoding="utf-8")

    monkeypatch.setattr(register, "MODULES_DIR", tmp_path / "modules")
    monkeypatch.setattr(register, "DATA_ROOT", tmp_path / "modules" / "modules_data")

    out = _module_choices("Planner")
    assert {"value": "时政", "preset": True} in out
    assert {"value": "热点", "preset": True} in out
    assert {"value": "科技", "preset": False} in out


def test_module_choices_no_directions(tmp_path, monkeypatch):
    from modules import register
    (tmp_path / "modules" / "plain").mkdir(parents=True)
    monkeypatch.setattr(register, "MODULES_DIR", tmp_path / "modules")
    monkeypatch.setattr(register, "DATA_ROOT", tmp_path / "modules" / "modules_data")
    assert _module_choices("plain") == []


def test_enrich_module_choice_and_services(tmp_path, monkeypatch):
    """_enrich_module：choice 字段注入候选 + location_services 附上。"""
    from web.handlers.admin import _enrich_module
    from modules import register
    mod_dir = tmp_path / "modules" / "Planner"
    (mod_dir).mkdir(parents=True)
    (mod_dir / "directions.json").write_text(json.dumps({"热点": {}}), encoding="utf-8")
    monkeypatch.setattr(register, "MODULES_DIR", tmp_path / "modules")
    monkeypatch.setattr(register, "DATA_ROOT", tmp_path / "modules" / "modules_data")

    m = {"settings_schema": [{"section": "s", "fields": [
        {"key": "briefing_topics", "type": "choice", "max": 3},
    ]}]}
    _enrich_module(m, "Planner")
    assert m["settings_schema"][0]["fields"][0]["candidates"] == [{"value": "热点", "preset": True}]
    assert isinstance(m.get("location_services"), list)
