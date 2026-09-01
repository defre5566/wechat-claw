"""批次Ⅹ（260830）：classify 模块数据区内置直发 + default_dirs 默认清空。"""
from __future__ import annotations

from pathlib import Path

import pytest

from bridge import paths
from bridge.config import DATA_ROOT


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    """隔离 DATA_ROOT（paths 的 from-import 是调用时求值 → patch config.DATA_ROOT 生效）。"""
    monkeypatch.setattr("bridge.config.DATA_ROOT", tmp_path)
    monkeypatch.setattr(paths, "DATA_ROOT", tmp_path)
    monkeypatch.setattr(paths, "REJECT_NAME_RE", __import__("re").compile(r"token|secret"))
    monkeypatch.setattr(paths, "REJECT_SUFFIXES", {".key"})
    monkeypatch.setattr(paths, "REJECT_DIRS", [])
    monkeypatch.setattr(paths, "DEFAULT_ALLOW_DIRS", [])
    return tmp_path


def test_modules_data_artifact_is_default(isolated):
    p = isolated / "modules" / "modules_data" / "Planner" / "briefing" / "2026-08-30.html"
    p.parent.mkdir(parents=True)
    p.write_text("<html></html>", encoding="utf-8")
    assert paths.classify(p) == "default"


def test_modules_data_inner_token_still_rejected(isolated):
    """modules_data 内的 token 文件：reject 优先于内置直发。"""
    p = isolated / "modules" / "modules_data" / "agent-SDK" / "token"
    p.parent.mkdir(parents=True)
    p.write_text("tok", encoding="utf-8")
    assert paths.classify(p) == "reject"


def test_modules_data_inner_key_suffix_rejected(isolated):
    p = isolated / "modules" / "modules_data" / "Planner" / "secret.key"
    p.parent.mkdir(parents=True)
    p.write_text("k", encoding="utf-8")
    assert paths.classify(p) == "reject"


def test_default_dirs_default_empty(monkeypatch):
    from bridge.config import DEFAULTS_USER
    assert DEFAULTS_USER["file_send"]["default_dirs"] == []
    from web.schema import config_schema as cs
    m = {f["key"]: f for f in cs.CONFIG_SCHEMA[1]["fields"] if cs.CONFIG_SCHEMA[1]["group"] == "file_send"} or {}
    field = next(f for g in cs.CONFIG_SCHEMA if g["group"] == "file_send" for f in g["fields"] if f["key"] == "default_dirs")
    assert field["default"] == []
