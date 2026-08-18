#!/usr/bin/env python3
"""wechat-claw bridge 入口：组装全部零件并运行。

架构: 微信 ← iLink Bot API ← wechat-agent-sdk transport ← opencode acp
特性:
- 5h 会话窗（同号延续，超时按时间戳归档；归档会话不再是推送目标）
- 按会话串行（同会话内一次一条，多会话并行；消息与 agent 推送共享 per-conv 锁）
- 高危操作"微信确认"：opencode 权限 ask → 发微信问用户 → 回复"允许"/"拒绝"决定
- 主动推送：HTTP 入口（127.0.0.1:9898/push）→ 队列 → 分流 direct/file/agent
- 通用调度引擎：读 registry index 按 module.json 规则触发模块

S1 防护：SessionExpiredError（SDK 清 token 后抛出）→ log.critical + 非零退出，
配合 systemd Restart=on-failure + StartLimit 限制，避免无限重启循环。
"""
from __future__ import annotations

import asyncio
import logging
import sys
from datetime import datetime
from pathlib import Path

# 项目根：按本文件位置定位（bridge/ 的上级），不依赖 cwd 或固定家目录路径；
# 与 registry_index / common.log 的相对定位一致，任意目录部署自洽
WORKDIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(WORKDIR))

from wechat_agent_sdk.api.client import SessionExpiredError
from wechat_agent_sdk.transport import WeChatTransport
from wechat_agent_sdk.types import ChatRequest

from .config import get as get_cfg

from .push_server import PUSH_AGENT_TYPES, PUSH_DIRECT_TYPES, PUSH_HOST, PUSH_PORT, start_push_server
from .scheduler import run_module, scheduler
from .session import ConfirmAcpAgent, PermissionGate, SessionManager
from .paths import classify

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("wechat-bridge")

# bridge 事件同时落 logs/system.log（与模块 log_event 同文件同轮转配置），stderr→journald 保留
from logging.handlers import RotatingFileHandler  # noqa: E402

_LOG_FILE = WORKDIR / "logs" / "system.log"


def _setup_file_logging() -> None:
    _LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(
        _LOG_FILE, maxBytes=1024 * 1024, backupCount=2, encoding="utf-8"
    )
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    log.addHandler(handler)


_setup_file_logging()

INBOX_DIR = WORKDIR / "inbox"


class BridgeCore:
    """运行时编排：transport/agent/会话/推送 worker 组装。"""

    def __init__(self):
        self._last_token: dict[str, str] = {}
        self._conv_locks: dict[str, asyncio.Lock] = {}

    async def send_text(self, conversation_id: str, text: str) -> None:
        token = self._last_token.get(conversation_id, "")
        await self._transport.send_text(conversation_id, text, context_token=token)

    async def send_with_retry(self, conversation_id: str, text: str) -> bool:
        """发送微信；失败入待发队列并落盘。返回是否成功。"""
        try:
            await self.send_text(conversation_id, text)
            return True
        except Exception as e:
            log.error(f"[push] 发送失败，入待发队列: {e}")
            self.retry_queue.append([conversation_id, text])
            from .state import save_retry_queue
            save_retry_queue(self.retry_queue)
            return False

    async def _send_file(self, path_str: str) -> bool:
        """发送文件原件到最近活跃会话（media_type=file）。失败仅记日志，不入重试队列。

        三级路径分级（bridge/paths.py）：
        - default（个人目录）→ 直发
        - gate（其余路径）→ 微信确认后发送（30s 超时默认拒绝）
        - reject（token/密钥相关）→ 任何情况直接拒绝
        """
        try:
            path = Path(path_str)
            if not path.is_file():
                log.error(f"[push] 文件不存在: {path}")
                return False
            from .state import targets_for_text
            conv, _ = targets_for_text("")
            if not conv:
                log.warning("[push] 无目标会话，跳过文件发送")
                return False
            level = classify(path)
            if level == "reject":
                log.warning(f"[push] 拒绝发送敏感文件: {path}")
                await self.send_text(conv, f"拒绝发送：{path}（敏感文件，不允许转发）")
                return False
            if level == "gate":
                allowed = await self.gate.wait(
                    conv,
                    f"⚠️ 请求发送文件：{path}\n回复「允许」或「拒绝」（30 秒无回复默认拒绝）",
                )
                if not allowed:
                    log.warning(f"[push] 用户拒绝/超时发送: {path}")
                    return False
            data = path.read_bytes()
            img_exts = {".png", ".jpg", ".jpeg", ".gif", ".webp"}
            media_type = "image" if path.suffix.lower() in img_exts else "file"
            await self._transport.send_media(conv, data, media_type, path.name)
            log.info(f"[push] 已发送文件: {path.name} (media={media_type})")
            return True
        except Exception as e:
            log.error(f"[push] 发文件失败: {e}")
            return False

    async def _receive_media(self, media_list) -> str:
        """下载微信发来的媒体到 inbox/，返回提示文本。"""
        INBOX_DIR.mkdir(parents=True, exist_ok=True)
        notes = []
        import time
        for m in media_list:
            if getattr(m, "type", "") == "audio":
                continue
            try:
                data = await self._transport.download_media(m)
                name = Path(getattr(m, "file_name", "") or "").name or f"{m.type}-{int(time.time())}"
                path = INBOX_DIR / name
                i = 1
                while path.exists():
                    path = INBOX_DIR / f"{path.stem}-{i}{path.suffix}"
                    i += 1
                path.write_bytes(data)
                notes.append(f"[收到{m.type}: {path.name}，已存 {path}]")
                log.info(f"[media] 已接收 {m.type}: {path}")
            except Exception as e:
                log.error(f"[media] 接收失败: {e}")
        return " ".join(notes)

    async def _agent_process(self, text: str) -> None:
        """agent 类推送：取 agent 加工后发送到目标会话。"""
        from .state import target_conversation_ids
        convs = target_conversation_ids()
        if not convs:
            log.warning("[push] 无目标 conversation_id，跳过")
            return
        for conv in convs:
            try:
                async with self._conv_locks.setdefault(conv, asyncio.Lock()):
                    reply = await self._agent.chat(ChatRequest(conversation_id=conv, text=text))
                    out = reply.text if hasattr(reply, "text") else str(reply)
                    if not out:
                        log.warning("[push] agent 返回空，兜底发原文")
                        await self.send_with_retry(conv, text)
                        continue
                    await self.send_with_retry(conv, out)
            except Exception as e:
                log.error(f"[push] agent 加工失败: {e}")

    async def push_worker(self) -> None:
        """消费外部推送：串行走 semaphore，direct 直接发，agent 类进 agent。"""
        while True:
            item = await self.push_queue.get()
            async with self.semaphore:
                try:
                    ptype = item.get("type")
                    text = item.get("text", "")
                    if ptype == "file":
                        await self._send_file(item.get("path", ""))
                    elif ptype in PUSH_DIRECT_TYPES:
                        from .state import targets_for_text
                        conv, text = targets_for_text(text)
                        if conv:
                            await self.send_with_retry(conv, text)
                        else:
                            log.warning("[push] 无目标会话，跳过 direct 推送（不入重试队列）")
                    elif ptype in PUSH_AGENT_TYPES:
                        await self._agent_process(text)
                except Exception as e:
                    log.error(f"[push] 处理失败: {e}")
            self.push_queue.task_done()

    async def retry_worker(self) -> None:
        """周期重试待发队列，成功后移除。"""
        from .state import load_retry_queue, RETRY_FILE  # noqa: F401
        from .state import save_retry_queue
        interval = 300
        while True:
            await asyncio.sleep(interval)
            if not self.retry_queue:
                continue
            remaining = []
            for conv, text in list(self.retry_queue):
                try:
                    await self.send_text(conv, text)
                except Exception as e:
                    log.warning(f"[push] 重试仍失败: {e}")
                    remaining.append([conv, text])
            self.retry_queue[:] = remaining
            save_retry_queue(self.retry_queue)

    async def handle(self, conversation_id: str, text: str) -> None:
        if self.sessions.check(conversation_id) == "expired":
            await self._transport.send_text(conversation_id, "（上一轮会话已超时归档，本消息开启新会话）", "")
        try:
            await self._transport.send_typing(conversation_id, start=True, context_token=self._last_token.get(conversation_id, ""))
            reply = await self._agent.chat(ChatRequest(conversation_id=conversation_id, text=text))
            out = reply.text if hasattr(reply, "text") else str(reply)
            if out:
                await self._transport.send_text(conversation_id, out, "")
            await self._transport.send_typing(conversation_id, start=False, context_token=self._last_token.get(conversation_id, ""))
        except Exception as e:
            log.error(f"处理失败: {e}")
            await self._transport.send_typing(conversation_id, start=False, context_token=self._last_token.get(conversation_id, ""))
            await self._transport.send_text(conversation_id, f"处理出错：{e}", "")

    async def run(self) -> None:
        from .state import load_or_create_token, load_retry_queue

        WORKDIR.mkdir(parents=True, exist_ok=True)

        self._transport = WeChatTransport(account_id="default")
        # 手动从 storage 加载已保存的 token（WeChatBot.login_terminal 才会自动加载，纯 transport 不会）
        stored = await self._transport._storage.load_token("default")
        if stored:
            self._transport._client.token = stored
        if self._transport.needs_login:
            log.critical("未登录，请先完成登录（扫码流程见 docs/开发文档-03 A3）")
            raise SystemExit(1)  # 非零退出，systemd 可感知（配合 StartLimit 停止循环重启）
        await self._transport.connect()
        log.info("[weixin] 已连接 iLink")

        gate = PermissionGate(self.send_text)
        self._agent = ConfirmAcpAgent(
            command=get_cfg("acp.command"),
            args=["acp", "--port", str(get_cfg("acp.port"))],  # 固定端口，避免与 PWA web(4096) 冲突
            cwd=str(WORKDIR),
            auto_approve=False,  # 由微信确认接管
            gate=gate,
        )
        self.sessions = SessionManager(agent=self._agent)
        await self._agent.on_start()

        self.semaphore = asyncio.Semaphore(1)  # 串行

        # ---- 主动推送 ----
        self.retry_queue = load_retry_queue()
        self.push_queue: asyncio.Queue = asyncio.Queue(maxsize=100)  # 有界：满则 503
        self.gate = gate

        httpd = start_push_server(self.push_queue)
        asyncio.create_task(self.push_worker())
        asyncio.create_task(self.retry_worker())
        asyncio.create_task(scheduler())
        from .state import TOKEN_FILE
        log.info(
            f"[push] HTTP 入口 http://{PUSH_HOST}:{PUSH_PORT}/push"
            f"（token 见 {TOKEN_FILE}）"
        )
        log.info("微信桥接 v2 启动（5h 会话窗 / 串行 / 微信确认权限 / 通用调度引擎）")

        async for raw in self._transport.messages():
            parsed = self._transport.parse(raw)
            if parsed is None:
                continue
            conv = parsed.conversation_id
            text = parsed.text or ""
            if parsed.media:
                media_note = await self._receive_media(parsed.media)
                if media_note:
                    text = f"{media_note}\n{text}".strip()
            self._last_token[conv] = parsed.context_token if hasattr(parsed, "context_token") else ""
            # 权限确认回复优先（不占用串行队列）
            if gate.is_waiting(conv):
                if await gate.resolve(conv, text):
                    log.info(f"[权限] {conv} 回复: {text!r}")
                    continue
            async with self.semaphore:
                lock = self._conv_locks.setdefault(conv, asyncio.Lock())
                asyncio.create_task(self._handle_serial(lock, conv, text))


    async def _handle_serial(self, lock: asyncio.Lock, conversation_id: str, text: str) -> None:
        """per-conv 串行处理消息：同会话内一次一条，不阻塞消息接收循环。"""
        async with lock:
            await self.handle(conversation_id, text)


async def main() -> None:
    core = BridgeCore()
    await core.run()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except SessionExpiredError:
        log.critical("微信登录 token 失效（SessionExpiredError），请重新完成登录（扫码流程见 docs/开发文档-03 A3）")
        raise SystemExit(1)  # 非零退出 → systemd 停止重启循环（配合 StartLimitBurst）
    except KeyboardInterrupt:
        log.info("已停止")