"""部署状态迁移测试：enabled/retry/auto_update 归数据区 settings.json（module.json 纯声明化）。

隔离：monkeypatch register.MODULES_DIR/DATA_ROOT 到临时目录。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from modules import register


def _mk(tmp: Path, module_json: dict | None = None):
    """构造隔离模块（代码目录 + 数据区），monkeypatch register 路径。"""
    mod_dir = tmp / "modules" / "demo"
    data_dir = tmp / "modules" / "modules_data" / "demo"
    mod_dir.mkdir(parents=True)
    data_dir.mkdir(parents=True)
    (mod_dir / "module.json").write_text(
        json.dumps(module_json or {"name": "demo"}), encoding="utf-8")
    import pytest
    mp = pytest.MonkeyPatch()
    mp.setattr(register, "MODULES_DIR", tmp / "modules")
    mp.setattr(register, "DATA_ROOT", tmp / "modules" / "modules_data")
    # 启停钩子会写指令索引目录（register → web.agent_gen），一并隔离防泄漏真实数据根
    import web.agent_gen as ag
    mp.setattr(ag, "INDEX_DIR", tmp / "instructions" / "index")
    mp.setattr(ag, "DATA_ROOT", tmp)
    return mod_dir, data_dir, mp


# ---------- set_enabled：写 settings.json，module.json 不再承载 ----------

def test_set_enabled_writes_settings_json(tmp_path):
    mod_dir, data_dir, _ = _mk(tmp_path)
    assert register.set_enabled("demo", True)
    mj = json.loads((mod_dir / "module.json").read_text(encoding="utf-8"))
    assert "enabled" not in mj
    sv = json.loads((data_dir / "settings.json").read_text(encoding="utf-8"))
    assert sv["enabled"] is True
    assert register.set_enabled("demo", False)
    sv = json.loads((data_dir / "settings.json").read_text(encoding="utf-8"))
    assert sv["enabled"] is False


# ---------- update_module：业务设置合并写，部署键保留 ----------

def test_update_module_preserves_deployment_keys(tmp_path):
    _, data_dir, _ = _mk(tmp_path)
    register._save_settings_json("demo", {
        "enabled": True, "retry": {"interval_seconds": 900, "max": 3},
        "auto_update": False, "planner_on": True,
    })
    assert register.update_module("demo", settings={"morning_time": "08:30", "planner_on": True})
    sv = json.loads((data_dir / "settings.json").read_text(encoding="utf-8"))
    assert sv["enabled"] is True          # 部署键保留
    assert sv["retry"] == {"interval_seconds": 900, "max": 3}
    assert sv["auto_update"] is False
    assert sv["morning_time"] == "08:30"  # 业务键更新


def test_update_module_retry_to_settings(tmp_path):
    _, data_dir, _ = _mk(tmp_path)
    assert register.update_module("demo", retry={"interval_seconds": 60, "max": 5}, retry_set=True)
    sv = json.loads((data_dir / "settings.json").read_text(encoding="utf-8"))
    assert sv["retry"] == {"interval_seconds": 60, "max": 5}


# ---------- auto_update 开关 ----------

def test_auto_update_default_and_set(tmp_path):
    _, data_dir, _ = _mk(tmp_path)
    assert register.get_auto_update("demo") is True        # 缺省跟随全局
    assert register.set_auto_update("demo", False)
    assert register.get_auto_update("demo") is False
    sv = json.loads((data_dir / "settings.json").read_text(encoding="utf-8"))
    assert sv["auto_update"] is False


# ---------- installed.json 版本记录 ----------

def test_module_state_roundtrip(tmp_path):
    _, _, _ = _mk(tmp_path)
    assert register.save_module_state("demo", version="0.2.0", source_id="official")
    st = register.get_module_state("demo")
    assert st["version"] == "0.2.0"
    assert st["source_id"] == "official"
    assert st.get("installed_at")
    assert register.get_module_state("nope") == {}


# ---------- get_module / list_modules 聚合 ----------

def test_get_module_aggregates_deployment_state(tmp_path):
    _, data_dir, _ = _mk(tmp_path)
    register._save_settings_json("demo", {"enabled": True, "retry": None, "auto_update": False})
    register.save_module_state("demo", version="0.1.0")
    m = register.get_module("demo")
    assert m["enabled"] is True
    assert m["auto_update"] is False
    assert m["version"] == "0.1.0"
    assert m["settings"]["enabled"] is True


def test_list_modules_contains_version(tmp_path):
    _, data_dir, _ = _mk(tmp_path)
    register._save_settings_json("demo", {"enabled": True})
    register.save_module_state("demo", version="0.1.0")
    items = register.list_modules()
    assert len(items) == 1
    assert items[0]["version"] == "0.1.0"
    assert items[0]["enabled"] is True
