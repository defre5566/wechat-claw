"""schedule_from_settings 联动测试（register._time_to_cron / _sync_schedule_from_settings）。

隔离：monkeypatch register.MODULES_DIR/DATA_ROOT 到临时目录（不碰真实 modules/）。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from modules import register


# ---------- _time_to_cron ----------

def test_time_to_cron_valid():
    assert register.time_to_cron("08:30") == "30 8 * * *"
    assert register.time_to_cron("21:00") == "0 21 * * *"
    assert register.time_to_cron("00:05") == "5 0 * * *"
    assert register.time_to_cron(" 08:30 ") == "30 8 * * *"


def test_time_to_cron_invalid():
    assert register.time_to_cron(None) is None
    assert register.time_to_cron("") is None
    assert register.time_to_cron("8:30") == "30 8 * * *"   # 一位小时宽松接受
    assert register.time_to_cron("25:00") is None
    assert register.time_to_cron("08:60") is None
    assert register.time_to_cron("abc") is None
    assert register.time_to_cron(830) is None


# ---------- _sync_schedule_from_settings ----------

def _mk(tmp_path, sfs, settings):
    """构造隔离模块目录（代码 + 数据区），monkeypatch register 路径。"""
    mod_dir = tmp_path / "modules" / "Planner"
    data_dir = tmp_path / "modules" / "modules_data" / "Planner"
    mod_dir.mkdir(parents=True)
    data_dir.mkdir(parents=True)
    (mod_dir / "module.json").write_text(
        json.dumps({"name": "Planner", "schedule_from_settings": sfs}), encoding="utf-8")
    (data_dir / "settings.json").write_text(json.dumps(settings), encoding="utf-8")
    import pytest
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(register, "MODULES_DIR", tmp_path / "modules")
    monkeypatch.setattr(register, "DATA_ROOT", tmp_path / "modules" / "modules_data")
    return mod_dir, monkeypatch


def test_sync_schedule_two_phases(tmp_path):
    sfs = [
        {"phase": "morning", "time_field": "morning_time", "enabled_field": "planner_on"},
        {"phase": "evening", "time_field": "evening_time"},
    ]
    mod_dir, mp = _mk(tmp_path, sfs, {"planner_on": True, "morning_time": "08:30", "evening_time": "21:00"})
    register._sync_schedule_from_settings("Planner")
    mj = json.loads((mod_dir / "module.json").read_text(encoding="utf-8"))
    assert mj["schedule"] == [
        {"id": "morning", "cron": "30 8 * * *", "args": ["--phase", "morning"]},
        {"id": "evening", "cron": "0 21 * * *", "args": ["--phase", "evening"]},
    ]


def test_sync_schedule_enabled_field_off(tmp_path):
    """planner_on=false → schedule 清空（总开关关闭不调度）。"""
    sfs = [{"phase": "morning", "time_field": "morning_time", "enabled_field": "planner_on"}]
    mod_dir, mp = _mk(tmp_path, sfs, {"planner_on": False, "morning_time": "08:30"})
    register._sync_schedule_from_settings("Planner")
    mj = json.loads((mod_dir / "module.json").read_text(encoding="utf-8"))
    assert mj["schedule"] == []


def test_sync_schedule_invalid_time_skipped(tmp_path):
    """时刻非法 → 该 phase 不生成调度（不崩溃）。"""
    sfs = [{"phase": "morning", "time_field": "morning_time"}]
    mod_dir, mp = _mk(tmp_path, sfs, {"morning_time": "25:99"})
    register._sync_schedule_from_settings("Planner")
    mj = json.loads((mod_dir / "module.json").read_text(encoding="utf-8"))
    assert mj["schedule"] == []


def test_sync_schedule_no_declaration(tmp_path):
    """模块无 schedule_from_settings 声明 → 不动 module.json。"""
    mod_dir, mp = _mk(tmp_path, None, {})
    (mod_dir / "module.json").write_text(json.dumps({"name": "Planner"}), encoding="utf-8")
    register._sync_schedule_from_settings("Planner")
    mj = json.loads((mod_dir / "module.json").read_text(encoding="utf-8"))
    assert "schedule" not in mj
