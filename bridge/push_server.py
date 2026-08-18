"""push_server：本地 HTTP 推送入口（绑定可配，config.yaml push.*，默认 127.0.0.1:9898）。

职责：鉴权 → 校验 body → 入队。接收并行（ThreadingHTTPServer），处理串行（push_worker 消费）。
鉴权数据源：registry index（模块 token_hash）+ push_token。
"""
from __future__ import annotations

import asyncio
import hashlib
import http.server
import json
import logging
import threading

from .config import get
from modules.registry_index import build_index

from .state import load_or_create_token

log = logging.getLogger("wechat-bridge")

PUSH_HOST = get("push.host")
PUSH_PORT = get("push.port")
MAX_PUSH_BODY = get("push.max_body_mb") * 1024 * 1024  # 超限 413

# 推送类型（与模块契约保持一致）
PUSH_DIRECT_TYPES = {"briefing", "notification", "text", "file"}  # 原文发送（file 发文件原件）
PUSH_AGENT_TYPES = {"reminder", "alert"}                          # 进 agent 队列加工后发送


def _token_valid(token: str, push_token: str) -> bool:
    """Bearer/X-Token 命中 push_token 或 sha256 命中 registry 任一模块哈希。"""
    if token and (token == push_token):
        return True
    if token:
        digest = hashlib.sha256(token.encode()).hexdigest()
        return any(m.get("token_hash") == digest for m in build_index().values())
    return False


def start_push_server(push_queue: asyncio.Queue) -> http.server.ThreadingHTTPServer:
    """启动 HTTP 入口（独立线程），请求仅鉴权+校验+入队。返回 httpd。"""
    push_token = load_or_create_token()
    loop = asyncio.get_running_loop()

    class PushHandler(http.server.BaseHTTPRequestHandler):
        def _reply(self, code: int, obj: dict) -> None:
            data = json.dumps(obj).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_POST(self) -> None:
            if self.path != "/push":
                self._reply(404, {"ok": False, "error": "not found"})
                return
            auth = self.headers.get("Authorization", "")
            xtoken = self.headers.get("X-Token", "")
            bearer = auth[7:] if auth.lower().startswith("bearer ") else ""
            token = bearer or xtoken
            if not _token_valid(token, push_token):
                short = (token or "无")[:4]
                log.warning(
                    f"[push] 鉴权失败 ip={self.client_address[0]} token={short}****"
                )
                self._reply(401, {"ok": False, "error": "unauthorized"})
                return
            try:
                length = int(self.headers.get("Content-Length", 0) or 0)
            except ValueError:
                self._reply(400, {"ok": False, "error": "invalid content-length"})
                return
            if length > MAX_PUSH_BODY:
                self._reply(413, {"ok": False, "error": "payload too large"})
                return
            try:
                body = json.loads(self.rfile.read(length).decode("utf-8")) if length else {}
            except Exception:
                self._reply(400, {"ok": False, "error": "invalid json"})
                return
            ptype = body.get("type")
            if not isinstance(ptype, str) or ptype not in (PUSH_DIRECT_TYPES | PUSH_AGENT_TYPES):
                self._reply(400, {"ok": False, "error": "missing or invalid type"})
                return
            if push_queue.full():
                self._reply(503, {"ok": False, "error": "queue full"})
                return
            asyncio.run_coroutine_threadsafe(push_queue.put(body), loop)
            self._reply(200, {"ok": True})

        def log_message(self, *args) -> None:  # 静默 access log
            return

    httpd = http.server.ThreadingHTTPServer((PUSH_HOST, PUSH_PORT), PushHandler)
    httpd.daemon_threads = True
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd