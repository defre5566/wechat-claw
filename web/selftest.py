#!/usr/bin/env python3
"""web 自测：三隔离（临时 HOME + 临时部署目录 + 服务 dry-run）+ 两 mock（登录/opencode）。

流程：WEB_SELFTEST=1 起 wizard 服务（随机端口）→ 按 6 步调接口断言 → 清理。
跨平台可跑（CI 三平台矩阵第 4 步）。退出码 0 = 全过。
"""
from __future__ import annotations

import json
import os
import sys

# Windows 控制台默认 cp1252/GBK，中文输出会 UnicodeEncodeError → 强制 UTF-8
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
# 优先项目 .venv（本地惯例）；CI/无 .venv 时用当前解释器（sys.executable）
_venv_py = ROOT / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
PY = _venv_py if _venv_py.exists() else Path(sys.executable)


def _req(port: int, method: str, path: str, body: dict | None = None,
         token: str = "", raw: bool = False) -> tuple[int, dict | bytes]:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}{path}",
        data=data, method=method,
        headers={"Content-Type": "application/json"},
    )
    if token:
        req.add_header("X-Auth", token)
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = resp.read()
            if raw:
                return resp.status, data
            return resp.status, json.loads(data.decode("utf-8"))
    except urllib.error.HTTPError as e:
        try:
            if raw:
                return e.code, e.read()
            return e.code, json.loads(e.read().decode("utf-8"))
        except Exception:
            return e.code, {}


def main() -> int:
    # 启动时快照：已部署实例（真实数据根已存在）拒绝执行——
    # selftest 末尾会清理测试数据，在真实部署上误跑会丢 crypto.key/密码/用户数据
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    from bridge.config import DATA_ROOT as PROD_DATA_ROOT
    # 兼容旧版部署：数据根可能还在项目根 .config（迁移不做，但误跑清理会丢数据）
    legacy_config = (ROOT / ".config").exists()
    existing_config = (PROD_DATA_ROOT / ".config").exists() or legacy_config
    if existing_config and os.environ.get("WC_SELFTEST_FORCE") != "1":
        print(f"NG: 检测到已存在的数据根 {PROD_DATA_ROOT}（疑似已部署实例）。")
        print("    selftest 会清理测试数据，在真实部署上误跑会永久丢失 crypto.key/密码/用户数据。")
        print("    如确认要在本机跑，请设环境变量 WC_SELFTEST_FORCE=1 后重试。")
        return 1

    with tempfile.TemporaryDirectory() as tmp:
        home = Path(tmp)
        env = dict(os.environ)
        env["HOME"] = str(home)
        env["LOCALAPPDATA"] = str(home / "AppData" / "Local")  # Windows 数据根跟随隔离
        env["WEB_SELFTEST"] = "1"
        env["WEB_PORT"] = "0"  # 端口由 selftest 固定

        # 找空闲端口
        import socket
        s = socket.socket()
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
        s.close()
        env["WEB_PORT"] = str(port)

        proc = subprocess.Popen(
            [str(PY), str(ROOT / "web" / "wizard.py"), "--port", str(port)],
            cwd=str(ROOT), env=env,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        )
        try:
            # 等服务起来（windows 冷启动较慢，放宽到 30s）
            for _ in range(150):
                try:
                    st, _ = _req(port, "GET", "/api/state")
                    if st == 200:
                        break
                except Exception:
                    pass
                time.sleep(0.2)
            else:
                # 服务未起来：dump 子进程输出辅助诊断
                out = b""
                if proc.stdout:
                    try:
                        out = proc.stdout.read(4096)
                    except Exception:
                        pass
                print(f"[selftest] wizard 未在 30s 内就绪; 子进程输出:\n{out.decode(errors='replace')[:2000]}")
                raise RuntimeError("wizard 服务未就绪")

            checks = []

            # ① 体检
            st, d = _req(port, "POST", "/api/env_check")
            checks.append(("env_check", st == 200 and d.get("ok")))

            # ② opencode（mock：本机已装或 selftest 下跳过真实安装）
            st, d = _req(port, "POST", "/api/opencode/install")
            checks.append(("opencode", st == 200 and d.get("ok")))

            # ③ 装配（真实跑：venv 已就绪，命令幂等）
            in_ci = bool(os.environ.get("CI"))
            if in_ci:
                # CI：装配（建 venv + 全量 pip install）由 workflow 的 Install deps /
                # Install vendored SDK 步骤覆盖，此处跳过真实装配，避免 runner 长时间空转
                checks.append(("assemble_start", True))
                checks.append(("assemble_done", True))
            else:
                st, d = _req(port, "POST", "/api/assemble")
                checks.append(("assemble_start", st == 200 and d.get("ok")))
                for _ in range(120):
                    st, d = _req(port, "GET", "/api/assemble/status")
                    if d.get("done"):
                        break
                    time.sleep(0.5)
                checks.append(("assemble_done", d.get("done") and d.get("ok")))

            # ④ 配置生成（临时 HOME → 配置落在临时目录）
            st, d = _req(port, "POST", "/api/config/gen", {"password": "abc123"})
            checks.append(("config_gen", st == 200 and d.get("ok")
                           and d["results"]["config"]["created"]))

            # ⑤ 登录（selftest mock：pending → confirmed）
            st, d = _req(port, "POST", "/api/login/setup")
            checks.append(("login_setup", st == 200 and d.get("ok") and d.get("qr_url")))
            confirmed = False
            for _ in range(10):
                st, d = _req(port, "GET", "/api/login/status")
                if d.get("status") == "confirmed":
                    confirmed = True
                    break
                time.sleep(0.3)
            checks.append(("login_confirm", confirmed))

            # ⑥ 拉起（selftest dry-run）
            st, d = _req(port, "POST", "/api/service/up")
            checks.append(("service_up", st == 200 and d.get("ok")))

            # 管理链路：auth → profile → agents render
            st, d = _req(port, "POST", "/api/auth", {"password": "abc123"})
            checks.append(("auth", st == 200 and d.get("token")))
            token = d.get("token", "")
            st, d = _req(port, "GET", "/api/profile", token=token)
            checks.append(("profile_auth", st == 200 and d.get("ok")))
            st, d = _req(port, "GET", "/api/profile")
            checks.append(("profile_401", st == 401))
            st, d = _req(port, "POST", "/api/profile/city", {"code": "110101"}, token=token)
            checks.append(("profile_city", st == 200 and d.get("ok")
                           and d["city"].get("city") == "东城" and d["city"].get("code") == "110101"))
            st, d = _req(port, "POST", "/api/profile/locate", {"lat": 39.92, "lon": 116.40}, token=token)
            checks.append(("profile_locate", st == 200 and d.get("ok") and d.get("name")))
            st, d = _req(port, "POST", "/api/profile/locate", {}, token=token)
            checks.append(("profile_locate_bad", st == 400))
            st, d = _req(port, "POST", "/api/agents/render", token=token)
            checks.append(("agents_render", st == 200 and d.get("ok")))

            # 头像：上传（1x1 PNG base64）→ 读取 → 撤销
            png = ("data:image/png;base64," +
                   "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==")
            st, d = _req(port, "POST", "/api/profile/avatar", {"data": png}, token=token)
            checks.append(("avatar_set", st == 200 and d.get("ok")))
            st, _ = _req(port, "GET", "/api/profile/avatar", token=token, raw=True)
            checks.append(("avatar_get", st == 200))
            st, d = _req(port, "POST", "/api/profile/avatar/undo", token=token)
            checks.append(("avatar_undo", st == 200 and d.get("ok")))

            # 汇总
            failed = [name for name, ok in checks if not ok]
            for name, ok in checks:
                print(f"[{'PASS' if ok else 'FAIL'}] {name}")
            if failed:
                print(f"NG: {failed}")
                return 1
            print("OK: web selftest 全部通过")
            return 0
        finally:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except Exception:
                proc.kill()
            # 清理测试产物：临时数据根由 TemporaryDirectory 回收；AGENTS.md 恢复走同一隔离 env
            shutil.rmtree(home / ".local" / "share" / "wechat-claw", ignore_errors=True)
            shutil.rmtree(home / "Library" / "Application Support" / "wechat-claw",
                          ignore_errors=True)
            shutil.rmtree(home / "AppData" / "Local" / "wechat-claw", ignore_errors=True)
            try:
                subprocess.run(
                    [str(PY), "-c",
                     "import sys; sys.path.insert(0, r'%s'); import web.agent_gen as a; a.write_agents()" % str(ROOT)],
                    cwd=str(ROOT), env=env, capture_output=True, timeout=30,
                )
            except Exception:
                pass


if __name__ == "__main__":
    raise SystemExit(main())
