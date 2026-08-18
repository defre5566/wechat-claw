"""② opencode 安装：官方脚本为主（长任务，日志轮询）。

- Linux/macOS：curl -fsSL https://opencode.ai/install | bash
- Windows：官方 PowerShell 安装脚本
- 已安装（opencode --version 成功）→ 前端显示"已安装可跳过"，本 handler 不执行
"""
from __future__ import annotations

import os
import shlex
import subprocess


def _detect() -> dict:
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


def handle(app, body: dict | None = None) -> dict:
    detect = _detect()
    if detect["ok"]:
        app.steps["opencode"] = True
        return {"ok": True, "already": True, "version": detect["value"]}
    if app.job_running():
        return {"ok": False, "error": "已有装配/安装任务运行中"}, 409

    if os.name == "nt":
        script = (
            "irm https://opencode.ai/install.ps1 | iex"
        )
        commands = [["powershell", "-NoProfile", "-Command", script]]
    else:
        script = "curl -fsSL https://opencode.ai/install | bash"
        commands = [["bash", "-c", script]]
    app.start_job("opencode", commands, on_done=app._opencode_done)
    return {"ok": True, "started": True}


def status(app, body: dict | None = None) -> dict:
    job = app.get_job("opencode")
    if job is None:
        return {"ok": True, "done": False, "lines": []}
    return job
