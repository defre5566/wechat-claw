"""① 环境体检：python 版本 / opencode 存在 / 磁盘空间 / 端口 8650 占用。"""
from __future__ import annotations

import shutil
import socket
import subprocess
import sys


def _python_version() -> dict:
    v = sys.version_info
    ok = (v.major, v.minor) >= (3, 11)
    return {"ok": ok, "value": f"{v.major}.{v.minor}.{v.micro}"}


def _opencode_version() -> dict:
    try:
        r = subprocess.run(
            ["opencode", "--version"], capture_output=True, text=True, timeout=15
        )
        if r.returncode == 0:
            out = (r.stdout or r.stderr or "").strip().splitlines()
            return {"ok": True, "value": out[0] if out else "已安装"}
    except Exception:
        pass
    return {"ok": False, "value": "未安装"}


def _disk_space() -> dict:
    try:
        usage = shutil.disk_usage(".")
        free_gb = usage.free / (1024 ** 3)
        return {"ok": free_gb >= 1, "value": f"{free_gb:.1f} GB 剩余"}
    except Exception:
        return {"ok": True, "value": "未知"}


def _port_free(port: int = 8650) -> dict:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.bind(("127.0.0.1", port))
        return {"ok": True, "value": f"{port} 空闲"}
    except OSError:
        return {"ok": False, "value": f"{port} 被占用"}
    finally:
        s.close()


def handle(app, body: dict | None = None) -> dict:
    items = [
        {"key": "python", "name": "Python 版本", **_python_version()},
        {"key": "opencode", "name": "opencode", **_opencode_version()},
        {"key": "disk", "name": "磁盘空间", **_disk_space()},
        {"key": "port", "name": "端口 8650", **_port_free()},
    ]
    app.steps["env_check"] = all(i["ok"] for i in items)
    return {"ok": True, "items": items, "passed": app.steps["env_check"]}
