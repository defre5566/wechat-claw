"""issue #12：渲染模型可切换——acp.model schema 字段 + 候选列表注入。

覆盖：validate_settings 的 select 分支（动态候选不在此强校验）、
models_list 缓存语义、schema_get 注入 options。
"""
from __future__ import annotations

import time

import bridge.config as bcfg
import web.schema.config_schema as cs
import web.handlers.admin as admin


# ---------- validate_settings：select 分支（acp.model） ----------

def test_validate_settings_select_accepts_model_and_empty(monkeypatch):
    """select 类型：候选是动态的（opencode models）不在 validate 强校验；空串 = 不指定合法。"""
    monkeypatch.setattr(cs, "CONFIG_SCHEMA", [
        {"group": "acp", "title": "t", "fields": [
            {"key": "model", "type": "select", "default": ""},
        ]},
    ])
    r = cs.validate_settings({"acp": {"model": "opencode/big-pickle"}})
    assert r["ok"] and r["clean"]["acp"]["model"] == "opencode/big-pickle"
    r2 = cs.validate_settings({"acp": {"model": ""}})
    assert r2["ok"] and r2["clean"]["acp"]["model"] == ""  # 恢复默认（不指定）


def test_validate_settings_select_skips_unknown_type(monkeypatch):
    """回归锚点：旧代码 else: continue 静默丢弃未知类型——select 必须进分支而非被丢。"""
    monkeypatch.setattr(cs, "CONFIG_SCHEMA", [
        {"group": "acp", "title": "t", "fields": [
            {"key": "model", "type": "select", "default": ""},
        ]},
    ])
    r = cs.validate_settings({"acp": {"model": "自定义/provider-model"}})
    assert r["ok"] and r["clean"]["acp"]["model"] == "自定义/provider-model"
    assert "model" in r["clean"]["acp"]  # 关键：不被静默丢弃


# ---------- models_list：缓存 5 分钟 + 失败回退空列表 ----------

def test_models_list_caches_and_falls_back(monkeypatch):
    called = {"n": 0}

    def fake_fetch():
        called["n"] += 1
        return ["opencode/a", "opencode/b"]

    monkeypatch.setattr(admin, "_fetch_opencode_models", fake_fetch)
    admin._MODELS_CACHE = None
    admin._MODELS_CACHE_TS = 0.0

    r1 = admin.models_list(None)
    r2 = admin.models_list(None)
    assert r1["ok"] and r1["models"] == ["opencode/a", "opencode/b"]
    assert called["n"] == 1  # 第二次走缓存，不重复跑子进程
    assert r2["models"] == r1["models"]

    # TTL 过期 → 重新拉取
    admin._MODELS_CACHE_TS -= admin._MODELS_TTL + 1
    r3 = admin.models_list(None)
    assert called["n"] == 2

    # 拉取失败 → 空列表（前端回退文本框），不抛异常
    monkeypatch.setattr(admin, "_fetch_opencode_models", lambda: [])
    admin._MODELS_CACHE_TS -= admin._MODELS_TTL + 1
    r4 = admin.models_list(None)
    assert r4["ok"] and r4["models"] == []


def test_fetch_opencode_models_missing_binary(monkeypatch):
    """无 opencode 可执行 → []（不阻塞配置页）。"""
    monkeypatch.setattr(bcfg, "resolve_opencode", lambda: None)
    assert admin._fetch_opencode_models() == []


def test_fetch_opencode_models_parses_lines(monkeypatch):
    """正常输出：按行取非空、跳过 # 注释行。"""
    class Fake:
        returncode = 0
        stdout = "opencode/a\nopencode/b\n# comment\n\n"

    def fake_run(*a, **k):
        return Fake()

    monkeypatch.setattr(bcfg, "resolve_opencode", lambda: "/usr/bin/opencode")
    import subprocess
    monkeypatch.setattr(subprocess, "run", fake_run)
    assert admin._fetch_opencode_models() == ["opencode/a", "opencode/b"]


# ---------- schema_get：acp.model 注入 options ----------

def test_schema_get_injects_model_options(monkeypatch):
    admin._MODELS_CACHE = None
    admin._MODELS_CACHE_TS = 0.0
    monkeypatch.setattr(admin, "_fetch_opencode_models", lambda: ["opencode/x", "opencode/y"])
    monkeypatch.setattr(cs, "CONFIG_SCHEMA", [
        {"group": "acp", "title": "t", "fields": [
            {"key": "model", "type": "select", "default": ""},
        ]},
    ])
    r = admin.schema_get(None)
    assert r["ok"]
    acp = r["schema"][0]
    model = [f for f in acp["fields"] if f["key"] == "model"][0]
    assert model["options"] == ["opencode/x", "opencode/y"]


def test_schema_get_injects_empty_options_on_failure(monkeypatch):
    """探测失败 → options=[]（前端回退文本框），schema 仍正常返回。"""
    admin._MODELS_CACHE = None
    admin._MODELS_CACHE_TS = 0.0
    monkeypatch.setattr(admin, "_fetch_opencode_models", lambda: [])
    monkeypatch.setattr(cs, "CONFIG_SCHEMA", [
        {"group": "acp", "title": "t", "fields": [
            {"key": "model", "type": "select", "default": ""},
        ]},
    ])
    r = admin.schema_get(None)
    assert r["ok"]
    model = [f for f in r["schema"][0]["fields"] if f["key"] == "model"][0]
    assert model["options"] == []


# ---------- settings_set：model 保存落盘 + 清空回默认 ----------

def test_settings_set_persists_model(tmp_path, monkeypatch):
    import yaml
    cfg_file = tmp_path / ".config" / "config.yaml"
    cfg_file.parent.mkdir(parents=True)
    cfg_file.write_text(yaml.safe_dump({"acp": {"port": 45678}}), encoding="utf-8")
    monkeypatch.setattr(admin, "CONFIG_FILE", cfg_file)
    monkeypatch.setattr(cs, "CONFIG_SCHEMA", [
        {"group": "acp", "title": "t", "fields": [
            {"key": "model", "type": "select", "default": ""},
            {"key": "port", "type": "number", "default": 45678},
        ]},
    ])

    r = admin.settings_set(None, {"settings": {"acp": {"model": "opencode/big-pickle"}}})
    assert r["ok"]
    saved = yaml.safe_load(cfg_file.read_text(encoding="utf-8"))
    assert saved["acp"]["model"] == "opencode/big-pickle"

    # 清空 → 空串落盘（= 不指定，用部署默认）
    r2 = admin.settings_set(None, {"settings": {"acp": {"model": ""}}})
    assert r2["ok"]
    saved2 = yaml.safe_load(cfg_file.read_text(encoding="utf-8"))
    assert saved2["acp"]["model"] == ""