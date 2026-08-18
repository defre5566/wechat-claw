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
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

# 项目根入 sys.path（python web/wizard.py 运行时 sys.path[0]=web/）
_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

HOST = "127.0.0.1"
PORT = int(os.environ.get("WEB_PORT", "8650"))
STATIC = Path(__file__).resolve().parent / "static"
MAX_BODY = 1 * 1024 * 1024
SELFTEST = os.environ.get("WEB_SELFTEST") == "1"


# ---------- 长任务 ----------

class Job:
    """一个长任务：依次执行命令，日志入环形缓冲，增量轮询。"""

    def __init__(self, name: str, commands: list[list[str]], on_done=None):
        self.name = name
        self.commands = commands
        self.on_done = on_done
        self.lines: list[str] = []
        self.done = False
        self.ok = False
        self.pos = 0
        self.thread = threading.Thread(target=self._run, daemon=True)

    def _run(self) -> None:
        for cmd in self.commands:
            self.lines.append("$ " + " ".join(shlex.quote(c) for c in cmd))
            try:
                p = subprocess.Popen(
                    cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                    text=True, encoding="utf-8", errors="replace",
                )
                assert p.stdout is not None
                for line in p.stdout:
                    self.lines.append(line.rstrip())
                rc = p.wait()
            except Exception as e:  # noqa: BLE001
                rc = 1
                self.lines.append(f"[job] {e}")
            if rc != 0:
                self.lines.append(f"[job] 失败 rc={rc}")
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
        lines = self.lines[self.pos:]
        self.pos = len(self.lines)
        return {"done": self.done, "ok": self.ok, "lines": lines}


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
    ("POST", "/api/opencode/install"): _h("opencode_setup", "handle"),
    ("GET", "/api/opencode/status"): _h("opencode_setup", "status"),
    ("POST", "/api/assemble"): _h("assemble", "handle"),
    ("GET", "/api/assemble/status"): _h("assemble", "status"),
    ("POST", "/api/config/gen"): _h("config_gen", "handle"),
    ("POST", "/api/login/setup"): _h("login", "setup"),
    ("GET", "/api/login/status"): _h("login", "status"),
    ("POST", "/api/service/up"): _h("service_up", "handle"),

    ("POST", "/api/auth"): _h("admin", "auth_login"),
    ("POST", "/api/admin/password"): _h("admin", "password_change", True),
    ("GET", "/api/profile"): _h("admin", "profile_get", True),
    ("POST", "/api/profile"): _h("admin", "profile_set", True),
    ("POST", "/api/profile/city"): _h("admin", "profile_set_city", True),
    ("POST", "/api/profile/locate"): _h("admin", "profile_locate", True),
    ("POST", "/api/profile/undo"): _h("admin", "profile_undo", True),
    ("POST", "/api/agents/render"): _h("admin", "agents_render", True),
    ("POST", "/api/profile/avatar"): _h("admin", "avatar_set", True),
    ("POST", "/api/profile/avatar/undo"): _h("admin", "avatar_undo", True),
    ("GET", "/api/admin/settings"): _h("admin", "settings_get", True),
    ("POST", "/api/admin/settings"): _h("admin", "settings_set", True),
    ("GET", "/api/admin/logs"): _h("admin", "logs_tail", True),
    ("POST", "/api/admin/logs"): _h("admin", "logs_tail", True),
    ("GET", "/api/admin/modules"): _h("admin", "modules_list", True),
    ("POST", "/api/admin/modules/toggle"): _h("admin", "modules_toggle", True),
    ("POST", "/api/admin/modules/install"): _h("admin", "modules_install", True),
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

    def _file_bytes(self, data: bytes, content_type: str) -> None:
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *args) -> None:  # 静默 access log
        return

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
        self._file(target, ctype)

    # ---- 入口 ----

    def do_GET(self) -> None:  # noqa: N802
        path = self.path.split("?")[0]
        if path == "/":
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
        if route is None:
            self._json(404, {"ok": False, "error": "not found"})
            return
        self._dispatch(route, None)

    def do_POST(self) -> None:  # noqa: N802
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


def main(argv: list[str] | None = None) -> int:
    argv = argv or sys.argv[1:]
    port = PORT
    for i, a in enumerate(argv):
        if a == "--port" and i + 1 < len(argv):
            port = int(argv[i + 1])

    httpd = ThreadingHTTPServer((HOST, port), Handler)
    url = f"http://{HOST}:{port}/"
    print(f"[wizard] 服务已启动: {url}")
    if not SELFTEST:
        try:
            webbrowser.open(url)
        except Exception:  # noqa: BLE001
            print(f"[wizard] 请手动打开浏览器访问 {url}")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n[wizard] 已停止")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
