"""session：会话与消息面。

- PermissionGate：权限确认门（微信回复"允许/拒绝"决定 opencode 高危操作）
- ConfirmAcpAgent：AcpAgent 改造（修复 SDK 引用了不存在的 PermissionOutcome 的 bug）
- SessionManager：5h 会话窗管理；归档时移除会话键（归档会话不再成为推送目标）
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from datetime import datetime

from acp.schema import (
    RequestPermissionResponse,
    AllowedOutcome,
    DeniedOutcome,
)
from wechat_agent_sdk.acp.adapter import AcpAgent

from .state import (
    ARCHIVE_DIR,
    PERM_TIMEOUT_SECONDS,
    SESSION_STATE_FILE,
    SESSION_TTL_SECONDS,
    WORKDIR,
)

log = logging.getLogger("wechat-bridge")


class PermissionGate:
    """权限确认门：等待微信用户回复"允许/拒绝"。"""

    def __init__(self, send_func):
        self._send = send_func
        self._pending: dict[str, asyncio.Future] = {}  # conversation_id -> future
        self._lock = asyncio.Lock()

    def is_waiting(self, conversation_id: str) -> bool:
        fut = self._pending.get(conversation_id)
        return fut is not None and not fut.done()

    async def resolve(self, conversation_id: str, text: str) -> bool:
        """收到微信消息时调用；若存在等待中的确认且文本匹配，则完成。"""
        async with self._lock:
            fut = self._pending.get(conversation_id)
            if fut is None or fut.done():
                return False
            t = text.strip().lower()
            if t in ("允许", "allow", "ok", "好的", "可以", "yes", "y"):
                fut.set_result(True)
                return True
            if t in ("拒绝", "deny", "no", "n", "不行", "不要"):
                fut.set_result(False)
                return True
            return False  # 不是确认回复，交给正常处理

    async def wait(self, conversation_id: str, prompt: str) -> bool:
        """发起确认：发消息问用户，等待回复（超时默认拒绝）。"""
        async with self._lock:
            fut = asyncio.get_running_loop().create_future()
            self._pending[conversation_id] = fut
        try:
            await self._send(conversation_id, prompt)
            try:
                return await asyncio.wait_for(fut, timeout=PERM_TIMEOUT_SECONDS)
            except asyncio.TimeoutError:
                log.warning(f"[权限] {conversation_id} {PERM_TIMEOUT_SECONDS}s 超时，默认拒绝")
                return False
        finally:
            async with self._lock:
                self._pending.pop(conversation_id, None)


class ConfirmAcpAgent(AcpAgent):
    """AcpAgent 改造版：权限请求转发微信确认（修复 SDK 的 PermissionOutcome bug）。"""

    def __init__(self, gate: PermissionGate, **kwargs):
        self._gate = gate
        super().__init__(**kwargs)

    async def on_start(self) -> None:
        """重写 on_start：内部 client 的 request_permission 改为微信确认。"""
        from acp import Client, PROTOCOL_VERSION, spawn_agent_process
        from acp.schema import Implementation, ClientCapabilities

        agent_ref = self

        class ConfirmClient(Client):
            async def session_update(self, session_id, update, **kwargs):
                agent_ref._handle_session_update(session_id, update)

            async def request_permission(self, options, session_id, tool_call, **kwargs):
                tool_name = getattr(tool_call, "name", None) or getattr(tool_call, "tool", "?")
                conv_id = agent_ref._active_conversations.get(session_id, "unknown")
                opt_labels = [o.name for o in (options or [])]
                log.info(f"[权限] 请求 tool={tool_name} options={opt_labels} conv={conv_id}")

                if not options:
                    log.warning(f"[权限] 无选项，拒绝 {tool_name}")
                    return RequestPermissionResponse(outcome=DeniedOutcome(outcome="cancelled"))

                prompt = (
                    f"⚠️ 请求执行：{tool_name}\n"
                    f"选项：{' / '.join(opt_labels)}\n"
                    f"回复「允许」或「拒绝」（{PERM_TIMEOUT_SECONDS} 秒无回复默认拒绝）"
                )
                allowed = await agent_ref._gate.wait(conv_id, prompt)

                if allowed:
                    log.info(f"[权限] 用户允许 {tool_name}")
                    opt_id = getattr(options[0], "option_id", None) or getattr(options[0], "id", None)
                    return RequestPermissionResponse(
                        outcome=AllowedOutcome(outcome="selected", optionId=opt_id)
                    )
                log.warning(f"[权限] 用户拒绝 {tool_name}")
                return RequestPermissionResponse(outcome=DeniedOutcome(outcome="cancelled"))

        client = ConfirmClient()

        spawn_env = {**os.environ, **(self._env or {})}
        if self._permission_mode:
            spawn_env.setdefault("ACP_PERMISSION_MODE", self._permission_mode)

        self._ctx = spawn_agent_process(
            client, self._command, *self._args, env=spawn_env, cwd=self._cwd,
        )
        self._conn, self._process = await self._ctx.__aenter__()

        await self._conn.initialize(
            protocol_version=PROTOCOL_VERSION,
            client_info=Implementation(name="wechat-agent-sdk", version="0.2.1-wechat-confirm"),
            client_capabilities=ClientCapabilities(),
        )
        log.info("[acp] Connection initialized (wechat-confirm mode)")


class SessionManager:
    """5h 会话窗管理：同号 5h 内延续，超时归档并开新会话。"""

    def __init__(self, agent=None):
        self._agent = agent
        self._last_active: dict[str, float] = {}
        self._load()

    def _load(self):
        try:
            if SESSION_STATE_FILE.exists():
                self._last_active = json.loads(SESSION_STATE_FILE.read_text())
        except Exception as e:
            log.warning(f"会话状态加载失败: {e}")

    def _save(self):
        try:
            WORKDIR.mkdir(parents=True, exist_ok=True)
            SESSION_STATE_FILE.write_text(json.dumps(self._last_active))
        except Exception as e:
            log.warning(f"会话状态保存失败: {e}")

    def check(self, conversation_id: str) -> str:
        now = time.time()
        last = self._last_active.get(conversation_id)
        if last is None:
            self._last_active[conversation_id] = now
            self._save()
            return "continue"
        if now - last > SESSION_TTL_SECONDS:
            self._archive(conversation_id)
            if self._agent is not None:
                self._agent._sessions.pop(conversation_id, None)
            self._last_active.pop(conversation_id, None)  # 归档会话不再是推送目标
            self._last_active[conversation_id] = now
            self._save()
            return "expired"
        self._last_active[conversation_id] = now
        self._save()
        return "continue"

    def _archive(self, conversation_id: str):
        ts = datetime.now().strftime("%Y%m%d-%H%M%S")
        ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
        record = {
            "conversation_id": conversation_id,
            "archived_at": datetime.now().isoformat(),
            "last_active": datetime.fromtimestamp(
                self._last_active.get(conversation_id, 0)
            ).isoformat(),
        }
        f = ARCHIVE_DIR / f"session-{conversation_id}-{ts}.json"
        f.write_text(json.dumps(record, ensure_ascii=False, indent=2))
        log.info(f"[归档] 会话 {conversation_id} 已超时归档: {f}")