"""批次Ⅸ（260830）：config_schema 收敛与高级设置弹窗修复回归。

覆盖：validate_settings 的 boolean/list 分支、settings_set 合并写入（不再清空
schema 外段）、模块页自动更新开关的数据链。
"""
from __future__ import annotations

import types

import yaml


# ---------- validate_settings：boolean 分支（旧代码 else: continue 静默丢弃） ----------

def test_validate_settings_accepts_boolean(tmp_path, monkeypatch):
    import web.schema.config_schema as cs
    monkeypatch.setattr(cs, "CONFIG_SCHEMA", [
        {"group": "update", "title": "模块自动更新", "fields": [
            {"key": "auto_enabled", "type": "boolean", "default": True},
        ]},
    ])
    r = cs.validate_settings({"update": {"auto_enabled": False}})
    assert r["ok"] and r["clean"]["update"]["auto_enabled"] is False
    r2 = cs.validate_settings({"update": {"auto_enabled": "true"}})  # 字符串归一
    assert r2["ok"] and r2["clean"]["update"]["auto_enabled"] is True


# ---------- settings_set：合并写入（不再整文件覆盖清空 schema 外段） ----------

def test_settings_set_merges_not_overwrites(tmp_path, monkeypatch):
    import web.handlers.admin as admin
    import web.schema.config_schema as cs

    cfg_file = tmp_path / ".config" / "config.yaml"
    cfg_file.parent.mkdir(parents=True)
    # 预置：acp 段（schema 内）+ update 段（schema 外但必须保留）
    cfg_file.write_text(yaml.safe_dump({
        "acp": {"port": 45678},
        "update": {"auto_enabled": True, "check_time": "04:00"},
    }), encoding="utf-8")
    monkeypatch.setattr(admin, "CONFIG_FILE", cfg_file)
    monkeypatch.setattr(cs, "CONFIG_SCHEMA", [
        {"group": "acp", "title": "t", "fields": [
            {"key": "command", "type": "text", "default": "opencode"},
            {"key": "port", "type": "number", "default": 45678},
        ]},
    ])

    r = admin.settings_set(None, {"settings": {
        "acp": {"port": 55555, "command": "opencode"},
    }})
    assert r["ok"]
    saved = yaml.safe_load(cfg_file.read_text(encoding="utf-8"))
    assert saved["acp"]["port"] == 55555        # schema 内组已更新
    assert saved["update"]["auto_enabled"] is True  # schema 外段保留（旧版整文件覆盖会清掉）


def test_settings_set_preserves_acp_when_other_group_saved(tmp_path, monkeypatch):
    """旧 bug 复现锚点：旧版提交非 acp 组时 clean 不含 acp → 整文件覆盖清空 acp。"""
    import web.handlers.admin as admin
    import web.schema.config_schema as cs

    cfg_file = tmp_path / ".config" / "config.yaml"
    cfg_file.parent.mkdir(parents=True)
    cfg_file.write_text(yaml.safe_dump({
        "acp": {"command": "/usr/bin/opencode", "port": 45678},
    }), encoding="utf-8")
    monkeypatch.setattr(admin, "CONFIG_FILE", cfg_file)
    monkeypatch.setattr(cs, "CONFIG_SCHEMA", [
        {"group": "file_send", "title": "t", "fields": [
            {"key": "reject_suffixes", "type": "list", "default": [".key"]},
        ]},
    ])

    r = admin.settings_set(None, {"settings": {
        "file_send": {"reject_suffixes": [".key", ".pem"]},
    }})
    assert r["ok"]
    saved = yaml.safe_load(cfg_file.read_text(encoding="utf-8"))
    assert saved["acp"]["port"] == 45678        # acp 段幸存
    assert saved["file_send"]["reject_suffixes"] == [".key", ".pem"]
