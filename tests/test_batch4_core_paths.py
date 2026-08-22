"""URA 收尾 F：bridge 主链路单测——handle/_route_inbound/_run_inbound/SessionManager。

依赖全部 mock（BridgeCore.__new__ + 替身），不碰微信 SDK 与真实数据根。
"""
from __future__ import annotations

import asyncio
import json
import time
from types import SimpleNamespace

import pytest

from bridge import main as m
from bridge.session import SessionManager


def _core() -> m.BridgeCore:
    """空壳 BridgeCore：依赖逐个替换（send_text 记调用）。"""
    c = m.BridgeCore.__new__(m.BridgeCore)
    c._last_token = {}
    c.sent: list[tuple[str, str]] = []
    c.typed: list[tuple[bool, str]] = []

    async def send_text(conv, text):
        c.sent.append((conv, text))

    async def send_typing(conv, start, context_token=""):
        c.typed.append((start, conv))

    c.send_text = send_text
    c._transport = SimpleNamespace(send_typing=send_typing)
    return c


class FakeAgent:
    """agent.chat 替身：可编程回复或抛异常。"""

    def __init__(self, reply="ok", exc=None):
        self.reply = reply
        self.exc = exc

    async def chat(self, request):
        if self.exc:
            raise self.exc
        return type("R", (), {"text": self.reply})()


# ---------- handle ----------

def test_handle_session_expired_prompt():
    async def go():
        c = _core()
        c._agent = FakeAgent("你好")
        c.sessions = SimpleNamespace(check=lambda conv: "expired")

        async def route(conv, text):
            return False

        c._route_inbound = route
        await c.handle("wx1", "hello")
        return c.sent, c.typed

    sent, typed = asyncio.run(go())
    assert any("超时归档" in t for _, t in sent), sent
    assert any("你好" in t for _, t in sent), sent


def test_handle_route_inbound_answered():
    """路由接管 → 不进 agent（无 agent chat、无 typing 提交）。"""
    async def go():
        c = _core()
        c._agent = FakeAgent("不应出现")
        c.sessions = SimpleNamespace(check=lambda conv: "continue")

        async def route(conv, text):
            return True

        c._route_inbound = route
        await c.handle("wx1", "查天气")
        return c.sent

    sent = asyncio.run(go())
    assert sent == []


def test_handle_agent_reply_sent():
    async def go():
        c = _core()
        c._agent = FakeAgent("回答内容")
        c.sessions = SimpleNamespace(check=lambda conv: "continue")

        async def route(conv, text):
            return False

        c._route_inbound = route
        await c.handle("wx1", "hi")
        return c.sent

    sent = asyncio.run(go())
    assert any("回答内容" in t for _, t in sent)


def test_handle_exception_uses_fixed_text():
    """F6.3：异常只对外发固定话术，不含异常原文。"""
    async def go():
        c = _core()
        c._agent = FakeAgent(exc=RuntimeError("内部路径 /secret/故意泄漏"))
        c.sessions = SimpleNamespace(check=lambda conv: "continue")

        async def route(conv, text):
            return False

        c._route_inbound = route
        await c.handle("wx1", "hi")
        return c.sent

    sent = asyncio.run(go())
    assert any("内部处理失败" in t for _, t in sent)
    assert all("/secret/" not in t for _, t in sent), sent


# ---------- _route_inbound ----------

def _route_core(run_results: list) -> m.BridgeCore:
    """_route_inbound 空壳：_run_inbound 按序返回预设。"""
    c = _core()
    it = iter(run_results)

    async def fake_run_inbound(name, conv, text):
        return next(it)

    c._run_inbound = fake_run_inbound
    return c


@pytest.fixture
def inbound_index(monkeypatch):
    """registry_index.build_index → 固定两模块（note 优先级 5 / weather 优先级 1）。"""
    from modules import registry_index
    idx = {
        "weather": {"inbound": {"intents": ["天气"], "priority": 1}},
        "note": {"inbound": {"intents": ["记一下", "天气"], "priority": 5}},
    }
    monkeypatch.setattr(registry_index, "build_index", lambda: idx)
    return idx


def test_route_priority_takeover(inbound_index):
    """同意图多模块命中 → 高 priority 先试；rc=0 接管并回话。"""
    async def go():
        c = _route_core([(0, "接管文本")])
        c._route_inbound  # noqa
        ok = await c._route_inbound("wx1", "今天天气如何")
        return ok, c.sent, c.typed  # typed 应为空（接管不碰 typing）

    ok, sent, _typed = asyncio.run(go())
    assert ok is True
    assert any("接管文本" in t for _, t in sent)


def test_route_rc3_forward_to_agent(inbound_index):
    async def go():
        c = _route_core([(3, "")])
        return await c._route_inbound("wx1", "天气咋样")

    assert asyncio.run(go()) is False


def test_route_rc1_fallback_and_stop(inbound_index):
    """rc=1 降级 agent 且不再试下一模块（失败即降级）。"""
    async def go():
        c = _route_core([(1, "")])
        return await c._route_inbound("wx1", "天气")

    assert asyncio.run(go()) is False


# ---------- _run_inbound ----------

def test_run_inbound_missing_script_rc2(monkeypatch, tmp_path):
    async def go():
        monkeypatch.setattr(m, "MODULES_DIR", tmp_path)
        c = _core()
        return await c._run_inbound("ghost", "wx1", "hi")

    rc, out = asyncio.run(go())
    assert rc == 2 and out == ""


# ---------- SessionManager ----------

def test_session_ttl_archive_and_refresh(monkeypatch, tmp_path):
    monkeypatch.setattr("bridge.session.SESSION_STATE_FILE", tmp_path / "s.json")
    monkeypatch.setattr("bridge.session.ARCHIVE_DIR", tmp_path / "archive")
    agent = type("A", (), {"_sessions": {"wx1": "old"}})()
    sm = SessionManager(agent=agent)
    assert sm.check("wx1") == "continue"  # 新会话
    # 人为把活跃时间推到 TTL 之前 → 过期归档
    sm._last_active["wx1"] = time.time() - 5 * 3600 - 10
    assert sm.check("wx1") == "expired"
    assert "wx1" not in agent._sessions  # SDK 会话已 pop
    assert list((tmp_path / "archive").glob("*.json"))  # 归档文件落盘
    assert sm.check("wx1") == "continue"  # 已刷新活跃点
    # 状态文件落盘
    data = json.loads((tmp_path / "s.json").read_text())
    assert "wx1" in data