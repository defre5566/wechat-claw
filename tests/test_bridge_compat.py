"""批次 B（260830）：bridge_compat 兼容门禁（issue #4）四场景 + registry 兜底排除。"""
from __future__ import annotations

import json
from pathlib import Path

import pytest


# ---------- compat_ok 四场景 ----------

def test_compat_default_baseline_passes():
    from bridge.compat import compat_ok
    assert compat_ok({"name": "t", "bridge_compat": ["0.1"]}) == (True, "")


def test_compat_cross_baseline_rejected():
    from bridge.compat import compat_ok
    ok, why = compat_ok({"name": "t", "bridge_compat": ["0.2"]})
    assert not ok and "0.1" in why and "0.2" in why


def test_compat_missing_or_malformed_rejected():
    """强制声明：未声明/格式错 = 不兼容（防损坏 module.json 混过门禁）。"""
    from bridge.compat import compat_ok
    ok1, why1 = compat_ok({"name": "t"})
    assert not ok1 and "bridge_compat" in why1
    ok2, _ = compat_ok({"name": "t", "bridge_compat": "0.1"})   # 字符串非数组
    assert not ok2
    ok3, _ = compat_ok({"name": "t", "bridge_compat": []})      # 空数组
    assert not ok3


def test_compat_multi_baseline():
    from bridge.compat import compat_ok
    assert compat_ok({"name": "t", "bridge_compat": ["0.1", "0.2"]})[0] is True


# ---------- register.set_enabled 门禁（签名 tuple + 原因可见） ----------

def test_set_enabled_rejected_cross_baseline(tmp_path, monkeypatch):
    """声明 0.2 的模块在 0.1 主程序 → 启用被拒并带原因；settings.json 不落盘。"""
    import bridge.config as cfg
    import modules.register as register
    from tests.test_module_state import _mk

    mod_dir, data_dir, mp = _mk(tmp_path, {"name": "demo", "bridge_compat": ["0.2"]})
    mp.setattr(register, "VERSION", None, raising=False) if False else None

    ok, why = register.set_enabled("demo", True)
    assert ok is False and "0.2" in why and "0.1" in why
    sf = data_dir / "settings.json"
    enabled = json.loads(sf.read_text(encoding="utf-8")).get("enabled") if sf.is_file() else None
    assert enabled is not True  # 未落盘启用状态


def test_set_enabled_disable_bypasses_compat(tmp_path):
    """停用不受门禁约束（关掉一个不兼容模块总是被允许的）。"""
    import modules.register as register
    from tests.test_module_state import _mk
    _mk(tmp_path, {"name": "demo", "bridge_compat": ["0.2"]})
    register._save_settings_json("demo", {"enabled": True})
    ok, why = register.set_enabled("demo", False)
    assert ok


def test_set_enabled_monkeypatched_upgrade(tmp_path, monkeypatch):
    """monkeypatch VERSION 模拟主程序升级 0.2：声明 ["0.1"] 的模块被拦、["0.2"] 放行。"""
    import bridge.compat as compat
    import modules.register as register
    from tests.test_module_state import _mk
    _mk(tmp_path, {"name": "demo", "bridge_compat": ["0.1"]})
    monkeypatch.setattr(compat, "VERSION", "0.2.0")
    ok, why = register.set_enabled("demo", True)
    assert not ok and "0.2" in why


# ---------- registry_index 兜底排除 ----------

def test_registry_excludes_incompatible(tmp_path, monkeypatch):
    """直改 settings.json 硬启用不兼容模块 → build_index 兜底排除 + log.error。"""
    import logging
    import bridge.config as cfg
    import modules.registry_index as ri
    from web import agent_gen  # noqa 确保命名空间

    mod_dir = tmp_path / "modules" / "Old"
    mod_dir.mkdir(parents=True)
    (mod_dir / "module.json").write_text(json.dumps(
        {"name": "Old", "bridge_compat": ["0.2"]}), encoding="utf-8")
    (mod_dir / "token").write_text("tok", encoding="utf-8")
    (tmp_path / "modules" / "modules_data" / "Old").mkdir(parents=True)
    (tmp_path / "modules" / "modules_data" / "Old" / "settings.json").write_text(
        json.dumps({"enabled": True}), encoding="utf-8")

    monkeypatch.setattr(ri, "MODULES_DIR", tmp_path / "modules")
    monkeypatch.setattr(ri, "_token_hash", lambda n: "hash")
    monkeypatch.setattr(cfg, "DATA_ROOT", tmp_path)
    monkeypatch.setattr(ri, "DATA_ROOT", tmp_path, raising=False)
    idx = ri.build_index()
    assert "Old" not in idx
