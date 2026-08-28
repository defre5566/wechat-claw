"""批 1（260827）：push_render 单轮渲染器——清洗规则 / tier0 回退 / 失败回退 / 主流程解耦。"""
from __future__ import annotations

import asyncio
import subprocess
import types

import pytest


# ---------- _clean_output ----------

def test_clean_output_strips_cli_prefix_and_blank():
    from bridge.push_render import _clean_output
    raw = "> build · glm-5.3-flash\n\n该取快递啦～\n"
    assert _clean_output(raw) == "该取快递啦～"


def test_clean_output_truncates_overlong():
    from bridge.push_render import _clean_output
    out = _clean_output("x" * 500)
    assert len(out) == 200 and out.endswith("…")


# ---------- _load_tier0 ----------

def test_load_tier0_prefers_file(tmp_path, monkeypatch):
    import bridge.push_render as pr
    d = tmp_path / "instructions"
    d.mkdir()
    (d / "tier0.md").write_text("自定义基调。", encoding="utf-8")
    monkeypatch.setattr(pr, "WORK_ROOT", tmp_path)
    assert pr._load_tier0() == "自定义基调。"


def test_load_tier0_falls_back_builtin(tmp_path, monkeypatch):
    import bridge.push_render as pr
    monkeypatch.setattr(pr, "WORK_ROOT", tmp_path)  # 无 instructions 目录
    assert pr._load_tier0() == pr.FALLBACK_TIER0


# ---------- render_push_text 失败路径 ----------

def test_render_none_when_binary_missing(monkeypatch):
    import bridge.push_render as pr
    monkeypatch.setattr(pr, "resolve_opencode", lambda: None, raising=False)
    import bridge.config as cfg
    monkeypatch.setattr(cfg, "resolve_opencode", lambda: None)
    # 函数内 from .config import resolve_opencode → patch config 模块生效
    assert pr.render_push_text("下午2:05提醒我取快递") is None


def test_render_none_on_timeout(monkeypatch):
    import bridge.config as cfg
    import bridge.push_render as pr

    def fake_run(*a, **k):
        raise subprocess.TimeoutExpired(cmd="opencode", timeout=120)

    monkeypatch.setattr(cfg, "resolve_opencode", lambda: "/usr/bin/opencode")
    monkeypatch.setattr(cfg, "xdg_env", lambda: {})
    monkeypatch.setattr(pr.subprocess, "run", fake_run)
    assert pr.render_push_text("素材") is None


def test_render_none_on_empty_text():
    import bridge.push_render as pr
    assert pr.render_push_text("  ") is None


def test_render_ok(monkeypatch):
    import bridge.config as cfg
    import bridge.push_render as pr

    def fake_run(argv, **k):
        assert "-m" in argv and "test/model" in argv
        assert "不要使用任何工具" in argv[-1]  # 单轮无工具指令在场
        assert "素材正文" in argv[-1]
        return types.SimpleNamespace(stdout="> build\n 下午两点快到了，该取快递啦～ \n", stderr="")

    monkeypatch.setattr(cfg, "resolve_opencode", lambda: "/usr/bin/opencode")
    monkeypatch.setattr(cfg, "xdg_env", lambda: {})
    monkeypatch.setattr(pr.subprocess, "run", fake_run)
    assert pr.render_push_text("素材正文", model="test/model") == "下午两点快到了，该取快递啦～"


# ---------- main._agent_process：不进会话、不占 per-conv 锁、失败回退 ----------

def _core_with_stubs(monkeypatch, rendered):
    from bridge import main as m

    core = m.BridgeCore.__new__(m.BridgeCore)
    core.sessions = types.SimpleNamespace(check=lambda _c: "continue")
    sent = []

    async def fake_send_text(_c, t):
        sent.append(("text", t))

    async def fake_send_with_retry(_c, t):
        sent.append(("retry", t))

    core.send_text = fake_send_text
    core.send_with_retry = fake_send_with_retry
    monkeypatch.setattr("bridge.push_render.render_push_text", lambda t, model=None: rendered)
    import bridge.state as st
    monkeypatch.setattr(st, "target_conversation_ids", lambda: ["wx-1"])
    return core, sent


def test_agent_process_sends_rendered_without_chat_or_conv_lock(monkeypatch):
    from bridge import main as m

    core, sent = _core_with_stubs(monkeypatch, rendered="该取快递啦～")
    core._conv_locks = {}
    core._agent = types.SimpleNamespace(
        chat=lambda req: (_ for _ in ()).throw(AssertionError("推送不得进 agent 会话"))
    )

    asyncio.run(core._agent_process("提醒我取快递"))
    assert ("retry", "该取快递啦～") in sent
    assert core._conv_locks == {}  # 未创建任何 per-conv 锁


def test_agent_process_falls_back_to_raw_on_render_failure(monkeypatch):
    from bridge import main as m

    core, sent = _core_with_stubs(monkeypatch, rendered=None)
    asyncio.run(core._agent_process("提醒我取快递"))
    assert ("retry", "提醒我取快递") in sent  # 原文直发
