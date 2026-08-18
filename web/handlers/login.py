"""⑤ 扫码登录：SDK request_login / check_login 轮询。

- app.login.transport 常驻（WeChatTransport account=default）
- request_login：已有 token 返回空 session（qr_url=""）→ 前端显示"已登录"
- check_login：CONFIRMED 时 SDK 自动写 storage（~/.wechat-agent-sdk/accounts.json），
  bridge 启动自动复用（同 storage 同 account）
- WEB_SELFTEST=1：mock（setup 返回假 qr_url，status 前 2 次 pending 后 confirmed）
"""
from __future__ import annotations

import asyncio
import os

from wechat_agent_sdk.transport import WeChatTransport

SELFTEST = os.environ.get("WEB_SELFTEST") == "1"
_MOCK_COUNT = 0


def _get_transport(app) -> WeChatTransport:
    if app.login.get("transport") is None:
        app.login["transport"] = WeChatTransport(account_id="default")
    return app.login["transport"]


def setup(app, body: dict | None = None) -> dict:
    global _MOCK_COUNT
    _MOCK_COUNT = 0
    if SELFTEST:
        app.login["session"] = {"mock": True}
        return {"ok": True, "qr_url": "https://example.com/mock-qr"}
    transport = _get_transport(app)
    if not transport.needs_login:
        app.login["already"] = True
        app.steps["login"] = True
        return {"ok": True, "already": True}
    app.login["already"] = False
    app.login["session"] = None
    session = asyncio.run(transport.request_login())
    if not session.qr_url:
        # 竞态：storage 已有 token（request_login 内部加载）
        app.login["already"] = True
        app.steps["login"] = True
        return {"ok": True, "already": True}
    app.login["session"] = session
    return {"ok": True, "qr_url": session.qr_url}


def status(app, body: dict | None = None) -> dict:
    global _MOCK_COUNT
    if SELFTEST:
        _MOCK_COUNT += 1
        state = "pending" if _MOCK_COUNT <= 2 else "confirmed"
        if state == "confirmed":
            app.steps["login"] = True
        return {"ok": True, "status": state}
    transport = _get_transport(app)
    session = app.login.get("session")
    if app.login.get("already"):
        return {"ok": True, "status": "confirmed", "already": True}
    if session is None:
        return {"ok": True, "status": "pending"}
    result = asyncio.run(transport.check_login(session))
    state = result.status.value  # pending/scanned/confirmed/expired/error
    if state == "confirmed":
        app.steps["login"] = True
    return {"ok": True, "status": state, "error": result.error}
