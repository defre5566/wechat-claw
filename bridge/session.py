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
from contextlib import contextmanager
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


@contextmanager
def _quiet_asyncio_exec():
    """在作用域内给 asyncio.create_subprocess_exec 注入 Windows 无窗口标志。

    acp 包（site-packages）的 spawn_stdio_transport 不接受 creationflags 参数，
    exe 形态（windowed 无控制台）下拉起控制台程序 opencode.exe 会弹新控制台窗口。
    此处运行时包装注入（setdefault 不覆盖显式传参），退出即还原——不修改
    site-packages 磁盘文件（venv 重建/重打包不失效），不碰命令解析链。
    作用域仅覆盖 ACP 这一次 spawn；POSIX 上 flags=0 无影响。
    """
    from .config import no_window_flags
    if not no_window_flags():
        yield  # POSIX：无需注入
        return
    orig = asyncio.create_subprocess_exec

    def _quiet_exec(*args, **kwargs):
        kwargs.setdefault("creationflags", no_window_flags())
        return orig(*args, **kwargs)

    asyncio.create_subprocess_exec = _quiet_exec
    try:
        yield
    finally:
        asyncio.create_subprocess_exec = orig


class PermissionGate:
    """权限确认门：等待微信用户回复"允许/拒绝"。

    per-conv 队列：同会话多个并发确认请求排队，回复"允许/拒绝"匹配队首 pending
    future，避免后者覆盖前者导致前者 30s 超时默认拒绝。
    """

    def __init__(self, send_func):
        self._send = send_func
        self._pending: dict[str, list[asyncio.Future]] = {}  # conversation_id -> 队列
        self._lock = asyncio.Lock()

    def is_waiting(self, conversation_id: str) -> bool:
        q = self._pending.get(conversation_id)
        return bool(q)

    async def resolve(self, conversation_id: str, text: str) -> bool:
        """收到微信消息时调用；匹配队首 pending future 完成。"""
        async with self._lock:
            q = self._pending.get(conversation_id)
            if not q:
                return False
            fut = q[0]
            if fut.done():
                q.pop(0)
                return False
            t = text.strip().lower()
            if t in ("允许", "allow", "ok", "好的", "可以", "yes", "y"):
                fut.set_result(True)
                q.pop(0)
                return True
            if t in ("拒绝", "deny", "no", "n", "不行", "不要"):
                fut.set_result(False)
                q.pop(0)
                return True
            return False  # 不是确认回复，交给正常处理

    async def wait(self, conversation_id: str, prompt: str) -> bool:
        """发起确认：发消息问用户，等待回复（超时默认拒绝）。"""
        fut = asyncio.get_running_loop().create_future()
        async with self._lock:
            self._pending.setdefault(conversation_id, []).append(fut)
        try:
            await self._send(conversation_id, prompt)
            try:
                return await asyncio.wait_for(fut, timeout=PERM_TIMEOUT_SECONDS)
            except asyncio.TimeoutError:
                log.warning(f"[权限] {conversation_id} {PERM_TIMEOUT_SECONDS}s 超时，默认拒绝")
                return False
        finally:
            async with self._lock:
                q = self._pending.get(conversation_id)
                if q:
                    try:
                        q.remove(fut)
                    except ValueError:
                        pass
                    if not q:
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
        # opencode 收敛隔离：仅向导安装的（标记存在）把 XDG 数据收敛到 wechat-claw 数据根；
        # 用户原有的 opencode 不注入，保持默认位置（原配置/登录态照常用）
        from bridge.config import DATA_ROOT
        if (DATA_ROOT / ".config" / "opencode-installed.json").is_file():
            spawn_env.setdefault("XDG_DATA_HOME", str(DATA_ROOT / "opencode" / "data"))
            spawn_env.setdefault("XDG_CONFIG_HOME", str(DATA_ROOT / "opencode" / "config"))
            spawn_env.setdefault("XDG_CACHE_HOME", str(DATA_ROOT / "opencode" / "cache"))

        self._ctx = spawn_agent_process(
            client, self._command, *self._args, env=spawn_env, cwd=self._cwd,
        )
        try:
            with _quiet_asyncio_exec():  # Windows：ACP 子进程无窗口（防 opencode 控制台弹框）
                self._conn, self._process = await self._ctx.__aenter__()
        except FileNotFoundError as e:
            # 自启/nssm 等场景 PATH 受限：带实际 command 值落地诊断，WinError 2 不再裸抛
            log.error(
                "[acp] 拉起 opencode 失败（找不到可执行文件）: command=%r args=%r (%s)。"
                "请确认 opencode 在 PATH / <数据根>/bin / ~/.opencode/bin 之一，"
                "或到 web 向导重新安装", self._command, self._args, e,
            )
            raise

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
                data = json.loads(SESSION_STATE_FILE.read_text())
                # 防御：文件被截断/写坏时可能得到 null/list 等非 dict，回退空状态
                if isinstance(data, dict):
                    self._last_active = data
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