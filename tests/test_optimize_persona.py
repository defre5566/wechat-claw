"""opencode 优化人设：双字段截取纯函数回归测试。"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from web.handlers.admin import _extract_sections


def _patch_optimize_runtime(tmp_path, monkeypatch, run):
    import bridge.config as cfg
    import web.handlers.admin as admin

    monkeypatch.setattr(admin, "DATA_ROOT", tmp_path)
    monkeypatch.setattr(cfg, "WORK_ROOT", tmp_path)
    monkeypatch.setattr(cfg, "resolve_opencode", lambda: "/usr/bin/opencode")
    monkeypatch.setattr(cfg, "xdg_env", lambda: {})
    monkeypatch.setattr(cfg, "no_window_flags", lambda: 0)
    monkeypatch.setattr(cfg, "get", lambda _key, default=None: None)
    monkeypatch.setattr(admin.agent_gen, "get_identity", lambda: {
        "address": "鑫", "assistant_name": "鱼",
    })
    monkeypatch.setattr(admin.agent_gen, "get_rules", lambda: [])
    monkeypatch.setattr(admin, "get_habits", lambda: [])
    monkeypatch.setattr(admin, "_load_lifestyle", lambda: "")
    monkeypatch.setattr("subprocess.run", run)
    return admin


def test_extract_sections_two_blocks():
    out = "前言\n【角色设定】\n第一句。第二句。\n【语言习惯】\n甲。乙。"
    result = _extract_sections(out)
    assert result["role"] == "第一句。第二句。"
    assert result["language"] == "甲。乙。"


def test_extract_sections_missing_language():
    out = "【角色设定】\n只有角色。"
    result = _extract_sections(out)
    assert result["role"] == "只有角色。"
    assert result["language"] == ""


def test_extract_sections_language_only():
    out = "【语言习惯】\n只有语言。"
    result = _extract_sections(out)
    assert result["role"] == ""
    assert result["language"] == "只有语言。"


def test_extract_sections_no_markers_fallback():
    out = "纯文本输出"
    result = _extract_sections(out)
    assert result["role"] == "纯文本输出"
    assert result["language"] == ""


def test_extract_sections_strips_whitespace():
    out = "【角色设定】\n  第一句。\n\n【语言习惯】\n  甲。  "
    result = _extract_sections(out)
    assert result["role"] == "第一句。"
    assert result["language"] == "甲。"


def test_optimize_persona_does_not_parse_error_stderr(tmp_path, monkeypatch):
    import types

    def fake_run(*_args, **_kwargs):
        return types.SimpleNamespace(
            returncode=1,
            stdout="",
            stderr='Error: {"name":"UnknownError","data":{"message":"Unexpected server error"}}',
        )

    admin = _patch_optimize_runtime(tmp_path, monkeypatch, fake_run)
    monkeypatch.setattr(admin, "_extract_sections", lambda *_: (_ for _ in ()).throw(
        AssertionError("错误输出不得进入正常解析")))
    result, status = admin.optimize_persona(None, {"role": "原角色"})
    assert status == 502
    assert result["ok"] is False
    assert "UnknownError" not in result.get("role", "")
    assert "UnknownError" not in result.get("language", "")
    assert "Unexpected server error" in (tmp_path / "logs" / "web.log").read_text(encoding="utf-8")


def test_optimize_persona_empty_stdout_is_failure(tmp_path, monkeypatch):
    import types

    def fake_run(*_args, **_kwargs):
        return types.SimpleNamespace(returncode=0, stdout="", stderr="")

    admin = _patch_optimize_runtime(tmp_path, monkeypatch, fake_run)
    result, status = admin.optimize_persona(None, {"role": "原角色"})
    assert status == 502
    assert result == {"ok": False, "error": "opencode 未返回任何输出"}


def test_optimize_persona_success_parses_stdout_only(tmp_path, monkeypatch):
    import types

    def fake_run(argv, **_kwargs):
        assert "-m" not in argv  # 未配置覆盖模型时沿用 opencode 默认模型
        return types.SimpleNamespace(
            returncode=0,
            stdout="【角色设定】\n优化角色。\n【语言习惯】\n优化语言。",
            stderr="",
        )

    admin = _patch_optimize_runtime(tmp_path, monkeypatch, fake_run)
    result = admin.optimize_persona(None, {"role": "原角色"})
    assert result == {"ok": True, "role": "优化角色。", "language": "优化语言。"}


def test_optimize_persona_error_marker_in_success_stdout_is_failure(tmp_path, monkeypatch):
    import types

    def fake_run(*_args, **_kwargs):
        return types.SimpleNamespace(returncode=0, stdout="Error: UnknownError", stderr="")

    admin = _patch_optimize_runtime(tmp_path, monkeypatch, fake_run)
    result, status = admin.optimize_persona(None, {"role": "原角色"})
    assert status == 502
    assert result["ok"] is False
