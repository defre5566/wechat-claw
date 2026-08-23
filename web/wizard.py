"""wizard.py：wechat-claw web 服务入口（127.0.0.1:8650，venv 内运行）。

- ThreadingHTTPServer + 集中路由表（向导 6 步 + 管理 API）
- 长任务：单槽 Job（subprocess + 日志环形缓冲 + 增量轮询）
- 静态文件：realpath 前缀校验防路径穿越；POST body 上限 1MB
- 管理接口：X-Auth 会话 token 校验（未设置密码时开放）
- WEB_SELFTEST=1：登录 mock（PENDING→CONFIRMED）、service_up dry-run
"""
from __future__ import annotations

import importlib
import json
import os
import shlex
import subprocess
import sys
import threading
import time
import webbrowser
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

# Windows 控制台默认 cp1252，中文 print 会 UnicodeEncodeError 导致启动崩溃 → 强制 UTF-8
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

# 项目根入 sys.path（python web/wizard.py 运行时 sys.path[0]=web/）
_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from bridge.config import DATA_ROOT, RESOURCE_ROOT

HOST = "127.0.0.1"
PORT = int(os.environ.get("WEB_PORT", "8650"))
STATIC = RESOURCE_ROOT / "web" / "static"
MAX_BODY = 1 * 1024 * 1024
SELFTEST = os.environ.get("WEB_SELFTEST") == "1"
# 隔离 UI 预览：仅用于本地预览实例，允许 admin.html 自动创建临时 session。
PREVIEW = os.environ.get("WEB_PREVIEW") == "1"
# 防 DNS rebinding / CSRF：Host 主机名白名单 + Origin/Referer 同源校验
_ALLOWED_HOSTNAMES = {"127.0.0.1", "localhost", "[::1]", "::1"}

# 运行日志（windowed 打包形态无控制台，落数据根 logs/web.log 便于排查）
_LOG_FILE = DATA_ROOT / "logs" / "web.log"


def _log(msg: str) -> None:
    try:
        _LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(_LOG_FILE, "a", encoding="utf-8") as f:
            f.write(f"{datetime.now():%Y-%m-%d %H:%M:%S} {msg}\n")
    except Exception:
        pass
    try:
        print(msg)
    except Exception:
        pass


def _origin_ok(header: str) -> bool:
    """Origin/Referer 缺省放行（curl 不带）；存在则主机名须在白名单。"""
    if not header:
        return True
    # 取 scheme://host[:port] 中的 host 部分
    try:
        from urllib.parse import urlparse
        host = (urlparse(header).hostname or "").lower()
    except Exception:
        return False
    return host in _ALLOWED_HOSTNAMES


# ---------- 长任务 ----------

# 长任务资源保护：日志环形截断（保留最近 N 行）+ 单步超时 watchdog（卡死命令自动 kill）
JOB_LINES_LIMIT = 2000
JOB_TIMEOUT_SECONDS = 600


class Job:
    """一个长任务：依次执行命令（可带阶段标注），日志入环形缓冲，增量轮询。

    steps 元素：list[list[str]] 兼容旧式命令；dict = {"stage": 阶段文案, "cmd": [...]}
    """

    def __init__(self, name: str, steps: list, on_done=None):
        self.name = name
        self.steps = steps
        self.on_done = on_done
        self.stage = ""
        self.lines: list[str] = []
        self.done = False
        self.ok = False
        self.pos = 0
        self._lock = threading.Lock()
        self._trunc_note: str | None = None
        self.thread = threading.Thread(target=self._run, daemon=True)

    def _add_line(self, text: str) -> None:
        with self._lock:
            self.lines.append(text)
            if len(self.lines) > JOB_LINES_LIMIT:
                dropped = len(self.lines) - JOB_LINES_LIMIT
                del self.lines[:dropped]
                self.pos = max(0, self.pos - dropped)
                if self._trunc_note is None:
                    self._trunc_note = f"[job] 日志过长已截断（保留最近 {JOB_LINES_LIMIT} 行）"

    def _run(self) -> None:
        for st in self.steps:
            if isinstance(st, dict):
                self.stage = st.get("stage", "")
                cmd = st["cmd"]
            else:
                cmd = st
            self._add_line("$ " + " ".join(shlex.quote(c) for c in cmd))
            try:
                p = subprocess.Popen(
                    cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                    text=True, encoding="utf-8", errors="replace",
                )
                assert p.stdout is not None
                # 读线程泵日志（Windows 无 select-pipe，跨平台统一线程方案）
                pump_out = []

                def _pump():
                    for line in p.stdout:
                        pump_out.append(line.rstrip())

                pump = threading.Thread(target=_pump, daemon=True)
                pump.start()
                # watchdog：单步超时强制 kill（防 curl 挂起永久占单槽）
                deadline = time.monotonic() + JOB_TIMEOUT_SECONDS
                while p.poll() is None:
                    if time.monotonic() > deadline:
                        p.kill()
                        self._add_line(f"[job] 单步超时 {JOB_TIMEOUT_SECONDS}s，已强制终止")
                        break
                    time.sleep(0.5)
                rc = p.wait()
                pump.join(timeout=2)
                for line in pump_out:
                    self._add_line(line)
            except Exception as e:  # noqa: BLE001
                rc = 1
                self._add_line(f"[job] {e}")
            if rc != 0:
                self._add_line(f"[job] 失败 rc={rc}")
                self.done = True
                self.ok = False
                if self.on_done:
                    self.on_done(False)
                return
        self.done = True
        self.ok = True
        if self.on_done:
            self.on_done(True)

    def start(self) -> None:
        self.thread.start()

    def snapshot(self) -> dict:
        with self._lock:
            lines = self.lines[self.pos:]
            self.pos = len(self.lines)
            note = self._trunc_note
            if note:
                self._trunc_note = None
                lines = [note, *lines]
            return {"done": self.done, "ok": self.ok, "stage": self.stage, "lines": lines}


class WizardApp:
    """进程内单例状态：步骤 / 登录 / 长任务。"""

    def __init__(self):
        self.steps: dict[str, bool] = {}
        self.login: dict = {}
        self.jobs: dict[str, Job] = {}

    def job_running(self) -> bool:
        return any(not j.done for j in self.jobs.values())

    def start_job(self, name: str, commands: list[list[str]], on_done=None) -> Job:
        self.jobs[name] = Job(name, commands, on_done)
        self.jobs[name].start()
        return self.jobs[name]

    def get_job(self, name: str) -> dict | None:
        job = self.jobs.get(name)
        return job.snapshot() if job else None

    def _opencode_done(self, ok: bool) -> None:
        if ok:
            self.steps["opencode"] = True


APP = WizardApp()


# ---------- 路由表（handler 模块, 函数名, 是否需要会话） ----------

def _h(module: str, func: str, need_auth: bool = False):
    return (module, func, need_auth)


ROUTES = {
    ("POST", "/api/env_check"): _h("env_check", "handle"),
    ("POST", "/api/opencode/detect"): _h("opencode_setup", "detect"),
    ("POST", "/api/opencode/install"): _h("opencode_setup", "install"),
    ("GET", "/api/opencode/status"): _h("opencode_setup", "status"),
    ("POST", "/api/assemble"): _h("assemble", "handle"),
    ("POST", "/api/assemble/detect"): _h("assemble", "detect"),
    ("GET", "/api/assemble/status"): _h("assemble", "status"),
    ("POST", "/api/config/gen"): _h("config_gen", "handle"),
    ("POST", "/api/login/setup"): _h("login", "setup"),
    ("GET", "/api/login/status"): _h("login", "status"),
    ("POST", "/api/service/up"): _h("service_up", "handle"),

    ("POST", "/api/auth"): _h("admin", "auth_login"),
    ("POST", "/api/admin/password"): _h("admin", "password_change", True),
    ("GET", "/api/profile"): _h("admin", "profile_get", True),
    ("GET", "/api/admin/weather"): _h("admin", "weather_get", True),
    ("POST", "/api/profile"): _h("admin", "profile_set", True),
    ("POST", "/api/profile/city"): _h("admin", "profile_set_city", True),
    ("POST", "/api/profile/locate"): _h("admin", "profile_locate", True),
    ("POST", "/api/profile/undo"): _h("admin", "profile_undo", True),
    ("POST", "/api/agents/render"): _h("admin", "agents_render", True),
    ("POST", "/api/agents/optimize_persona"): _h("admin", "optimize_persona", True),
    ("POST", "/api/profile/avatar"): _h("admin", "avatar_set", True),
    ("POST", "/api/profile/avatar/undo"): _h("admin", "avatar_undo", True),
    ("GET", "/api/admin/schema"): _h("admin", "schema_get", True),
    ("GET", "/api/admin/settings"): _h("admin", "settings_get", True),
    ("POST", "/api/admin/settings"): _h("admin", "settings_set", True),
    ("GET", "/api/admin/autostart"): _h("admin", "autostart_get", True),
    ("POST", "/api/admin/autostart"): _h("admin", "autostart_set", True),
    ("GET", "/api/admin/logs"): _h("admin", "logs_tail", True),
    ("POST", "/api/admin/logs"): _h("admin", "logs_tail", True),
    ("GET", "/api/admin/modules"): _h("admin", "modules_list", True),
    ("POST", "/api/admin/modules/toggle"): _h("admin", "modules_toggle", True),
    ("POST", "/api/admin/modules/install"): _h("admin", "modules_install", True),
    ("POST", "/api/admin/modules/remove"): _h("admin", "modules_remove", True),
    ("GET", "/api/admin/sources"): _h("admin", "sources_list", True),
    ("POST", "/api/admin/sources"): _h("admin", "sources_list", True),
    ("POST", "/api/admin/sources/add"): _h("admin", "source_add", True),
    ("POST", "/api/admin/sources/remove"): _h("admin", "source_remove", True),
    ("POST", "/api/admin/sources/refresh"): _h("admin", "source_refresh", True),
    ("POST", "/api/admin/module/get"): _h("admin", "module_get", True),
    ("POST", "/api/admin/module/update"): _h("admin", "module_update", True),
    ("POST", "/api/admin/module/prompt_add"): _h("admin", "module_prompt_add", True),
    ("POST", "/api/admin/module/prompt_delete"): _h("admin", "module_prompt_delete", True),
    ("POST", "/api/admin/module/auto_update"): _h("admin", "module_auto_update", True),
    ("POST", "/api/admin/modules/check_updates"): _h("admin", "modules_check_updates", True),
    ("POST", "/api/admin/module/update_now"): _h("admin", "module_update_now", True),
}


def _state() -> dict:
    from web import auth
    return {
        "ok": True,
        "steps": APP.steps,
        "password_set": auth.password_exists(),
        "selftest": SELFTEST,
    }


def _call_route(app, module_name: str, func_name: str, body: dict | None):
    mod = importlib.import_module(f"web.handlers.{module_name}")
    fn = getattr(mod, func_name)
    result = fn(app, body)
    if isinstance(result, tuple):
        data, status = result
        return data, status
    return result, 200


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    # ---- 辅助 ----

    def _json(self, code: int, obj: dict) -> None:
        data = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def _file(self, path: Path, content_type: str) -> None:
        self._file_bytes(path.read_bytes(), content_type)

    def _redirect(self, location: str) -> None:
        """302 跳转（入口智能指向用）。"""
        self.send_response(302)
        self.send_header("Location", location)
        self.send_header("Cache-Control", "no-store")
        self.end_headers()

    def _file_bytes(self, data: bytes, content_type: str) -> None:
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        try:
            self.wfile.write(data)
        except (ConnectionAbortedError, ConnectionResetError, BrokenPipeError, OSError):
            pass  # 客户端提前断开（刷新/关闭页面）：良性，静默不刷 traceback

    def log_message(self, *args) -> None:  # 静默 access log
        return

    def _guard(self) -> bool:
        """DNS rebinding / CSRF 防护：Host 主机名白名单 + Origin/Referer 同源。"""
        host = (self.headers.get("Host", "") or "").split(":", 1)[0].lower()
        if host not in _ALLOWED_HOSTNAMES:
            return False
        origin = self.headers.get("Origin", "") or self.headers.get("Referer", "")
        if not _origin_ok(origin):
            return False
        return True

    # ---- 静态文件（realpath 防穿越） ----

    def _serve_static(self, rel: str) -> None:
        base = STATIC.resolve()
        target = (base / rel.lstrip("/")).resolve()
        if not target.is_relative_to(base) or not target.is_file():
            self.send_error(404)
            return
        suffix = target.suffix.lower()
        ctype = {
            ".html": "text/html; charset=utf-8",
            ".css": "text/css; charset=utf-8",
            ".js": "application/javascript; charset=utf-8",
            ".png": "image/png",
            ".svg": "image/svg+xml",
            ".json": "application/json; charset=utf-8",
        }.get(suffix, "application/octet-stream")
        if PREVIEW and rel.lstrip("/") == "admin.html":
            data = target.read_bytes().replace(
                b'<body data-app="admin">',
                b'<body data-app="admin" data-preview="1">',
                1,
            )
            self._file_bytes(data, ctype)
            return
        self._file(target, ctype)

    # ---- 入口 ----

    def do_GET(self) -> None:  # noqa: N802
        if not self._guard():
            self._json(403, {"ok": False, "error": "forbidden host/origin"})
            return
        path = self.path.split("?")[0]
        if path == "/":
            # 入口智能指向：已部署（.config/config.yaml 存在）→ 工作台；未初始化 → 向导
            if (DATA_ROOT / ".config" / "config.yaml").is_file():
                self._redirect("/admin.html")
            else:
                self._serve_static("wizard.html")
            return
        if path == "/api/state":
            self._json(200, _state())
            return
        if path.startswith("/static/"):
            self._serve_static(path[len("/static/"):])
            return
        if path in ("/wizard.html", "/admin.html", "/login.html"):
            self._serve_static(path.lstrip("/"))
            return
        if path == "/api/profile/avatar":
            from web.handlers import admin
            result = admin.avatar_get(APP)
            if result is None:
                self._json(404, {"ok": False, "error": "no avatar"})
            else:
                data, ctype = result
                self._file_bytes(data, ctype)
            return
        route = ROUTES.get(("GET", path))
        if route is not None:
            self._dispatch(route, None)
            return
        # 兜底：static 根下同路径资源——页面以相对路径引用 theme.css/api.js 等，
        # 若不兜底会 404（UI 裸样式 + 向导脚本失效），realpath 校验防穿越
        if not path.startswith("/api/"):
            self._serve_static(path.lstrip("/"))
            return
        self._json(404, {"ok": False, "error": "not found"})

    def do_POST(self) -> None:  # noqa: N802
        if not self._guard():
            self._json(403, {"ok": False, "error": "forbidden host/origin"})
            return
        path = self.path.split("?")[0]
        route = ROUTES.get(("POST", path))
        if route is None:
            self._json(404, {"ok": False, "error": "not found"})
            return
        # body 上限
        try:
            length = int(self.headers.get("Content-Length", 0) or 0)
        except ValueError:
            self._json(400, {"ok": False, "error": "invalid content-length"})
            return
        if length > MAX_BODY:
            self._json(413, {"ok": False, "error": "payload too large"})
            return
        body: dict | None = None
        if length:
            try:
                body = json.loads(self.rfile.read(length).decode("utf-8"))
            except Exception:
                self._json(400, {"ok": False, "error": "invalid json"})
                return
        self._dispatch(route, body)

    def _dispatch(self, route, body) -> None:
        module_name, func_name, need_auth = route
        if need_auth:
            from web import auth
            token = self.headers.get("X-Auth", "")
            if not auth.check_session(token):
                self._json(401, {"ok": False, "error": "unauthorized"})
                return
        try:
            data, status = _call_route(APP, module_name, func_name, body)
        except Exception as e:  # noqa: BLE001
            data, status = {"ok": False, "error": str(e)}, 500
        self._json(status, data)


def _maybe_autostart_opencode(app: WizardApp) -> None:
    """web 打开前即后台自动安装 opencode（未安装时；selftest 跳过）。"""
    from web.handlers import opencode_setup
    if opencode_setup.SELFTEST:
        return
    if opencode_setup.detect_installed():
        return
    if app.job_running():
        return
    cmds = opencode_setup.build_install_commands()
    if cmds:
        app.start_job("opencode_install", cmds, on_done=lambda ok: opencode_setup.install_done(app, ok))
        _log("[wizard] opencode 未安装，已在后台启动自动安装（web 界面可看进度）")


def main(argv: list[str] | None = None) -> int:
    argv = argv or sys.argv[1:]
    # 打包形态 bridge 模式：`wechat-claw -m bridge.main`（service_up / nssm 启动命令）
    if len(argv) >= 2 and argv[0] == "-m" and argv[1] == "bridge.main":
        from bridge import main as bridge_main

        try:
            import asyncio
            asyncio.run(bridge_main.main())
        except KeyboardInterrupt:
            pass
        except SystemExit:
            raise  # 未登录等语义：非零退出码透传（服务管理器可见）
        except Exception as e:  # noqa: BLE001
            _log(f"[wizard] bridge 启动失败: {e}")
            return 1
        return 0
    port = PORT
    for i, a in enumerate(argv):
        if a == "--port" and i + 1 < len(argv):
            port = int(argv[i + 1])

    httpd = ThreadingHTTPServer((HOST, port), Handler)
    url = f"http://{HOST}:{port}/"
    _log(f"[wizard] 服务已启动: {url}")
    if not SELFTEST:
        try:
            webbrowser.open(url)
        except Exception:  # noqa: BLE001
            _log(f"[wizard] 请手动打开浏览器访问 {url}")
        _maybe_autostart_opencode(APP)
        # Windows 入口解耦注册（幂等）：开始菜单快捷方式 + VBS 启动器（8650 探测 → 开浏览器）
        try:
            from web.handlers.service_up import ensure_win_shortcuts
            ensure_win_shortcuts()
        except Exception:  # noqa: BLE001  注册失败不阻塞 web 启动
            pass
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        _log("[wizard] 已停止")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
