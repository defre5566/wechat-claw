#!/usr/bin/env python3
"""wechat-claw bridge 入口：组装全部零件并运行。

架构: 微信 ← iLink Bot API ← wechat-agent-sdk transport ← opencode acp
特性:
- 5h 会话窗（同号延续，超时按时间戳归档；归档会话不再是推送目标）
- 按会话串行（同会话内一次一条，多会话并行，per-conv 锁仅入站消息使用；
  agent 类推送走独立单轮渲染，不占会话锁、不入会话历史）
- 高危操作"微信确认"：opencode 权限 ask → 发微信问用户 → 回复"允许"/"拒绝"决定
- 主动推送：HTTP 入口（127.0.0.1:9898/push）→ 队列 → 分流 direct/file/agent
- 通用调度引擎：读 registry index 按 module.json 规则触发模块

S1 防护：SessionExpiredError（SDK 清 token 后抛出）→ log.critical + 非零退出，
配合 systemd Restart=on-failure + StartLimit 限制，避免无限重启循环。
"""
from __future__ import annotations

import asyncio
import logging
import subprocess
import sys
from datetime import datetime
from pathlib import Path

from .config import get as get_cfg, WORK_ROOT, no_window_flags

# 运行时根：按部署形态定位（打包形态 = 可执行文件旁，用户数据区）
WORKDIR = WORK_ROOT
sys.path.insert(0, str(WORKDIR))

from wechat_agent_sdk.api.client import SessionExpiredError
from wechat_agent_sdk.transport import WeChatTransport
from wechat_agent_sdk.types import ChatRequest

from .push_server import PUSH_AGENT_TYPES, PUSH_DIRECT_TYPES, PUSH_HOST, PUSH_PORT, start_push_server
from .scheduler import MODULES_DIR, RUN_TIMEOUT, run_module, scheduler
from .session import ConfirmAcpAgent, PermissionGate, SessionManager
from .paths import classify

# windowed 打包形态无 stderr（sys.stderr=None），basicConfig 的 StreamHandler 会崩 → 仅控制台形态加
if sys.stderr is not None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
else:
    logging.basicConfig(level=logging.INFO)
log = logging.getLogger("wechat-bridge")

# bridge 事件同时落 logs/system.log（与模块 log_event 同文件同轮转配置），stderr→journald 保留
from logging.handlers import RotatingFileHandler  # noqa: E402

_LOG_FILE = WORK_ROOT / "logs" / "system.log"


def _setup_file_logging() -> None:
    _LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(
        _LOG_FILE, maxBytes=1024 * 1024, backupCount=2, encoding="utf-8"
    )
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    log.addHandler(handler)


# 待发队列上限（网络故障期防内存无界；满则丢弃新条目并告警）
RETRY_QUEUE_LIMIT = 1000

# opencode 查找位置说明（resolve 失败时报错用，admin.start_bridge 复用）
OPENCODE_LOOKUP_HINT = "PATH → <数据根>/bin → ~/.opencode/bin"


def resolve_acp_command() -> str:
    """解析 opencode 可执行文件（fail-fast）：找不到直接退出，不裸名盲试 spawn。

    旧实现回退裸 "opencode"：服务化（nssm/systemd）PATH 不含用户目录时
    spawn 抛 WinError 2 / FileNotFoundError，信息量为零。现改为启动即明确报错，
    并逐路径列存在性（排查"检测得到、启动没有"类问题不用再猜）。
    """
    from .config import resolve_opencode, WORK_ROOT
    cmd = resolve_opencode()
    if cmd:
        log.info("[acp] opencode 解析成功: %s", cmd)
        return cmd
    import shutil as _shutil
    from pathlib import Path as _Path
    checked = {
        "PATH": _shutil.which("opencode") or "(无)",
        "数据根bin": str(WORK_ROOT / "bin" / ("opencode.exe" if sys.platform == "win32" else "opencode")),
        "官方目录": str(_Path.home() / ".opencode" / "bin"),
    }
    log.critical(
        "[acp] opencode 未找到。逐路径存在性: PATH=%s | %s 存在=%s | %s 存在=%s。"
        "请到 web 初始化向导第二步安装 opencode，或在 config.yaml 的 acp.command "
        "配置绝对路径后重启 bridge",
        checked["PATH"],
        checked["数据根bin"], _Path(checked["数据根bin"]).is_file(),
        checked["官方目录"], _Path(checked["官方目录"]).exists(),
    )
    raise SystemExit(1)  # 非零退出，nssm/systemd 可感知


def spawn_task(coro, name: str):
    """创建后台任务并挂监护：异常死亡 → 明确日志（防静默消失），返回 task。"""
    task = asyncio.create_task(coro)

    def _done(t: asyncio.Task):
        if t.cancelled():
            log.warning("[task] %s 已取消", name)
        elif t.exception() is not None:
            log.error("[task] %s 异常退出: %s", name, t.exception())

    task.add_done_callback(_done)
    return task


_setup_file_logging()

INBOX_DIR = WORK_ROOT / "inbox"
MAX_SEND_FILE = 50 * 1024 * 1024  # _send_file 单文件上限 50MB（防超大文件 OOM）


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
            if len(self.retry_queue) >= RETRY_QUEUE_LIMIT:
                log.warning(f"[push] 待发队列已满（{RETRY_QUEUE_LIMIT}），本条放弃: conv={conversation_id}")
                return False
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
            fsize = path.stat().st_size
            if fsize > MAX_SEND_FILE:
                log.warning(f"[push] 文件过大 ({fsize}B > {MAX_SEND_FILE}B)，拒绝发送: {path}")
                await self.send_text(conv, f"文件过大（{fsize // 1024 // 1024}MB 超上限），未发送")
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
        """agent 类推送：单轮无工具渲染为带人设的播报文本后发送（不进任何会话）。

        渲染走独立一次性进程（push_render，无会话/无历史），与入站消息的
        per-conv 锁零交集——切断"推送加工写入会话历史"的上下文雪球正反馈；
        渲染失败回退原文直发。
        会话窗接入保留：目标会话已过期 → 归档并刷新活跃点，用户回复可直接接续。
        """
        from .push_render import render_push_text
        from .state import target_conversation_ids
        convs = target_conversation_ids()
        if not convs:
            log.warning("[push] 无目标 conversation_id，跳过")
            return
        rendered = await asyncio.to_thread(render_push_text, text)
        out = rendered or text
        if rendered is None:
            log.warning("[push] 渲染失败，回退原文直发")
        for conv in convs:
            try:
                expired = self.sessions.check(conv) == "expired"
                if expired:
                    await self.send_text(
                        conv, "（上一轮会话已超时归档，本轮推送已开启新会话，可直接回复继续）"
                    )
                await self.send_with_retry(conv, out)
            except Exception as e:
                log.error(f"[push] 发送失败: {e}")

    async def push_worker(self) -> None:
        """消费外部推送：direct 走发送串行锁（防微信限流）；agent 类独立单轮渲染
        （不占 per-conv 锁、不入会话，失败回退原文）；file 类不占串行区。
        """
        while True:
            item = await self.push_queue.get()
            ptype = item.get("type")
            try:
                if ptype == "file":
                    # 文件发送含 gate 确认（最长 30s），不占 send_lock，避免阻塞 direct
                    await self._send_file(item.get("path", ""))
                elif ptype in PUSH_DIRECT_TYPES:
                    async with self.send_lock:
                        from .state import targets_for_text
                        text = item.get("text", "")
                        conv, text = targets_for_text(text)
                        if conv:
                            await self.send_with_retry(conv, text)
                        else:
                            log.warning("[push] 无目标会话，跳过 direct 推送（不入重试队列）")
                elif ptype in PUSH_AGENT_TYPES:
                    await self._agent_process(item.get("text", ""))
            except Exception as e:
                log.error(f"[push] 处理失败: {e}")
            self.push_queue.task_done()

    async def retry_worker(self) -> None:
        """周期重试待发队列，成功后移除（间隔读 push.retry_worker_interval，内置不可覆盖）。"""
        from .state import load_retry_queue, RETRY_FILE  # noqa: F401
        from .state import save_retry_queue
        interval = int(get_cfg("push.retry_worker_interval") or 300)
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
            await self.send_text(conversation_id, "（上一轮会话已超时归档，本消息开启新会话）")
        try:
            # B：入站路由——模块订阅优先（接管则不再进 agent）
            if await self._route_inbound(conversation_id, text):
                return
            # 硬索引 → 模糊兜底（≤6字跳过；to_thread 防阻塞事件循环）→ 档位阶梯增量
            from .indexer import build_material_block, fuzzy_match, tier_increment
            material = build_material_block(text)
            if not material:
                hits = await asyncio.to_thread(fuzzy_match, text)
                material = build_material_block(text, hits=hits) if hits else ""
            increment = tier_increment(conversation_id, text)
            prompt = text + increment + material
            await self._transport.send_typing(conversation_id, start=True, context_token=self._last_token.get(conversation_id, ""))
            reply = await self._agent.chat(ChatRequest(conversation_id=conversation_id, text=prompt))
            out = reply.text if hasattr(reply, "text") else str(reply)
            if out:
                await self.send_text(conversation_id, out)  # 走 _last_token（与 send_with_retry 一致）
            await self._transport.send_typing(conversation_id, start=False, context_token=self._last_token.get(conversation_id, ""))
        except Exception as e:
            log.error(f"处理失败: {e}")
            await self._transport.send_typing(conversation_id, start=False, context_token=self._last_token.get(conversation_id, ""))
            await self.send_text(conversation_id, "（内部处理失败，详情见日志）")

    async def _route_inbound(self, conversation_id: str, text: str) -> bool:
        """B：入站路由——enabled 模块的 inbound 订阅按 priority 顺序匹配意图。

        命中 → spawn 模块（--inbound <text> --conversation <id>）：
          rc=0 + stdout → 模块自答（stdout 即回话）→ True（消息被接管）
          rc=3          → 模块主动转 agent → False
          rc 其他        → 记日志降级 agent → False（不阻塞用户）
        多模块竞争：priority 高者先试，接管即止（定稿语义）。
        """
        from modules.registry_index import build_index
        from .indexer import observe
        index = build_index()
        candidates: list[tuple[int, str, str]] = []
        for name, cfg in index.items():
            ib = cfg.get("inbound") or {}
            intents = ib.get("intents") or []
            if not intents:
                continue
            for intent in intents:
                if intent and intent in text:
                    candidates.append((int(ib.get("priority", 0)), name, intent))
                    break
        if not candidates:
            observe(text, routed=False, conversation_id=conversation_id)
            return False
        candidates.sort(key=lambda x: -x[0])  # 高 priority 先试
        names = [name for _pri, name, _intent in candidates]
        for _pri, name, intent in candidates:
            rc, out = await self._run_inbound(name, conversation_id, text)
            if rc == 0:
                if out:
                    await self.send_text(conversation_id, out)
                log.info(f"[inbound] {name} 接管消息（intent={intent!r}）")
                observe(text, modules_hit=names, routed=True, conversation_id=conversation_id)
                return True
            if rc == 3:
                log.info(f"[inbound] {name} 转 agent（intent={intent!r}）")
                observe(text, modules_hit=names, routed=False, conversation_id=conversation_id)
                return False
            log.warning(f"[inbound] {name} 处理失败 rc={rc}（降级 agent）")
            observe(text, modules_hit=names, routed=False, conversation_id=conversation_id)
            return False  # 失败即降级，不连续试多模块
        return False

    async def _run_inbound(self, name: str, conversation_id: str, text: str) -> tuple[int, str]:
        """spawn 模块 inbound 模式；返回 (rc, stdout_text)。

        返回码约定（定稿 B）：0=自答（stdout 即回话文本）/ 3=转 agent / 1=业务失败 / 2=引擎级异常。
        """
        from bridge.paths import resolve_worker_path
        script = resolve_worker_path(MODULES_DIR / name, name)
        if script is None:
            log.error(f"[inbound] 模块脚本不存在: {MODULES_DIR / name / f'{name}_worker.py'}")
            return 2, ""
        cmd = [sys.executable, str(script), "--inbound", text, "--conversation", conversation_id]
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
                creationflags=no_window_flags(),
            )
            try:
                out, err = await asyncio.wait_for(proc.communicate(), timeout=RUN_TIMEOUT)
            except asyncio.TimeoutError:
                proc.kill()
                log.error(f"[inbound] {name} 超时 {RUN_TIMEOUT}s，已终止")
                return 2, ""
            rc = proc.returncode or 0
            out_text = out.decode(errors="replace").strip() if out else ""
            if rc != 0 and err:
                log.error(f"[inbound] {name} rc={rc}: {err.decode(errors='replace')[-500:]}")
            return rc, out_text
        except Exception as e:
            log.error(f"[inbound] {name} spawn 失败: {e}")
            return 2, ""

    async def _check_agents_reload(self) -> None:
        """后台任务：检测模块启停信号 → 兜底基建 + 清 session + 提示最新会话。

        web admin / register CLI 启停模块时写累积列表信号文件 → 本任务检测到
        → ensure_builtins 兜底（tier 基线 / index 目录；索引文件生命周期由
        register 钩子管理）+ 清 _sessions（下次消息 new_session 读最新 instructions）
        + 给最新会话发合并提示。
        累积列表模式：10 秒内开/关多个模块 → 一次处理，不重复清 session。
        """
        from .config import DATA_ROOT
        signal_file = DATA_ROOT / ".config" / ".agents-reload-requested"
        while True:
            await asyncio.sleep(10)
            try:
                if not signal_file.is_file():
                    continue
                import json as _json
                entries = _json.loads(signal_file.read_text(encoding="utf-8"))
                if not isinstance(entries, list):
                    entries = [entries]
                signal_file.unlink(missing_ok=True)
                # 兜底基建（tier 基线/index 目录；索引文件由 register 钩子管理）
                try:
                    from web.agent_gen import ensure_builtins
                    ensure_builtins()
                except Exception as e:
                    log.warning(f"[agents-reload] instructions 兜底失败: {e}")
                # 清 session 缓存：下次消息 new_session 读最新 instructions
                self._agent.clear_sessions()
                # 冷启动装配（乙）：全部活跃会话按各自画像刷新当前档位文件
                from .indexer import refresh_current_tier
                for conv_id in list(self.sessions._last_active):
                    refresh_current_tier(conv_id)
                # 合并所有条目成一条提示
                parts = []
                for entry in entries:
                    name = entry.get("module", "?")
                    enabled = bool(entry.get("enabled"))
                    emoji = "✅" if enabled else "🔴"
                    action = "已启用" if enabled else "已关闭"
                    parts.append(f"{emoji} {name} 模块{action}")
                tip = "；".join(parts) + "，新功能将在新对话中生效"
                # 给最新会话发提示（单用户系统，取 timestamp 最大的）
                latest_conv = None
                latest_ts = 0.0
                for conv_id, ts in self.sessions._last_active.items():
                    if ts > latest_ts:
                        latest_ts = ts
                        latest_conv = conv_id
                if latest_conv:
                    try:
                        await self.send_text(latest_conv, tip)
                    except Exception as e:
                        log.warning(f"[agents-reload] 提示发送失败: {e}")
                names = "、".join(e.get("module", "?") for e in entries)
                log.info(f"[agents-reload] {names}：instructions 已就绪，session 已清，已通知最新会话")
            except Exception as e:
                log.warning(f"[agents-reload] 检查异常: {e}")

    def _check_instructions_wiring(self) -> None:
        """启动只读自查：配置接线和 tier 文件缺失时告警，不自动修改用户文件。"""
        try:
            from web.agent_gen import TIER_BUDGET
            cfg_file = WORKDIR / "opencode.jsonc"
            if not cfg_file.is_file():
                return  # 未配置形态（不放置也能运行），不提示
            text = cfg_file.read_text(encoding="utf-8")
            # jsonc 带注释，粗查键名字面即可（防误报不追求完整解析）
            stripped = "\n".join(
                ln.split("//")[0] for ln in text.splitlines() if ln.strip()
            )
            if '"instructions"' not in stripped:
                log.warning(
                    "[config] opencode.jsonc 缺 instructions 数组——人设档位"
                    "（instructions/tier-current.md）不会被装载。请在该文件中补："
                    '"instructions": ["instructions/tier-current.md"]，'
                    "或重跑向导④（新部署不受影响）"
                )
                return
            instructions_dir = WORKDIR / "instructions"
            current = instructions_dir / "tier-current.md"
            if not current.is_file():
                log.warning("[config] instructions/tier-current.md 缺失——冷启动不会装载当前人设")
            for i in range(5):
                tier = instructions_dir / f"tier{i}.md"
                if not tier.is_file():
                    log.warning("[config] instructions/%s 缺失——tier 人设不完整", tier.name)
                    continue
                try:
                    count = len([line for line in tier.read_text(encoding="utf-8").splitlines() if line.strip()])
                except OSError as e:
                    log.warning("[config] 读取 %s 失败：%s", tier, e)
                    continue
                if count != TIER_BUDGET[i]:
                    log.warning(
                        "[config] %s 非空行数=%s，应为 %s——tier 人设可能不完整",
                        tier.name, count, TIER_BUDGET[i],
                    )
        except Exception as e:  # noqa: BLE001 自查失败不阻塞启动
            log.warning(f"[config] instructions 接线自查失败: {e}")

    async def run(self) -> None:
        from .state import load_or_create_token, load_retry_queue

        WORKDIR.mkdir(parents=True, exist_ok=True)
        self._check_instructions_wiring()

        self._transport = WeChatTransport(account_id="default")
        # 复用已保存登录态（SDK 公共接口 restore_token，vendor 补丁⑧注入——不摸私有成员）
        stored = await self._transport.restore_token()
        if self._transport.needs_login:
            log.critical("未登录，请先完成登录（扫码流程见 docs/开发文档-03 A3）")
            raise SystemExit(1)  # 非零退出，systemd 可感知（配合 StartLimit 停止循环重启）
        await self._transport.connect()
        log.info("[weixin] 已连接 iLink")

        gate = PermissionGate(self.send_text)
        self._agent = ConfirmAcpAgent(
            command=resolve_acp_command(),
            args=["acp", "--port", str(get_cfg("acp.port"))],  # 固定端口，避免与 PWA web(4096) 冲突
            cwd=str(WORKDIR),
            auto_approve=False,  # 由微信确认接管
            gate=gate,
        )
        self.sessions = SessionManager(agent=self._agent)
        await self._agent.on_start()

        self.send_lock = asyncio.Lock()  # 仅 direct 直发串行（防微信 API 限流）；agent/入站不占

        # ---- 主动推送 ----
        self.retry_queue = load_retry_queue()
        self.push_queue: asyncio.Queue = asyncio.Queue(maxsize=100)  # 有界：满则 503
        self.gate = gate

        httpd = start_push_server(self.push_queue)
        spawn_task(self.push_worker(), "push_worker")
        spawn_task(self.retry_worker(), "retry_worker")
        spawn_task(scheduler(), "scheduler")
        from .scheduler import set_push_queue
        set_push_queue(self.push_queue)  # 引擎级告警直投微信（scheduler 同 loop）
        spawn_task(self._check_agents_reload(), "agents_reload")
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
