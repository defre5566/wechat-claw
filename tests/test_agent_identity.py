"""助理名称元数据回归测试。"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from web import agent_gen


def test_default_name_is_not_customized(monkeypatch):
    monkeypatch.setattr(agent_gen._userdata, "load", lambda name, default=None: {})
    identity = agent_gen.get_identity()
    assert identity["assistant_name"] == "小助手"
    assert identity["assistant_name_customized"] is False


def test_legacy_non_default_name_is_customized(monkeypatch):
    monkeypatch.setattr(agent_gen._userdata, "load", lambda name, default=None: {"assistant_name": "小言"})
    identity = agent_gen.get_identity()
    assert identity["assistant_name_customized"] is True


def test_explicit_default_name_can_be_customized(monkeypatch):
    saved = {}

    def load(name, default=None):
        return saved.get(name, default)

    def save(name, data):
        saved[name] = data
        return True

    monkeypatch.setattr(agent_gen._userdata, "load", load)
    monkeypatch.setattr(agent_gen._userdata, "save", save)
    assert agent_gen.set_identity({"assistant_name": "小助手", "assistant_name_customized": True})
    assert agent_gen.get_identity()["assistant_name_customized"] is True
