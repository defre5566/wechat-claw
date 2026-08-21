"""③ 项目装配：venv + requirements + vendor -e + 补丁校验（长任务，日志轮询）。

命令幂等可重跑；已存在的步骤自动跳过（pip 快速 no-op、补丁已打 SKIP）。
"""
from __future__ import annotations

from pathlib import Path

from bridge.config import PROJECT_ROOT

PY = PROJECT_ROOT / ".venv" / ("Scripts/python.exe" if __import__("os").name == "nt" else "bin/python")
PIP = PROJECT_ROOT / ".venv" / ("Scripts/pip.exe" if __import__("os").name == "nt" else "bin/pip")


def _assemble_done(app, ok: bool) -> None:
    """装配 Job 完成回调：成功 → 步骤完成（服务端落状态，门禁/重访可用）。"""
    if ok:
        app.steps["assemble"] = True


def handle(app, body: dict | None = None) -> dict:
    if getattr(__import__("sys"), "frozen", False):
        # 打包形态：Python/依赖已随可执行文件，无需装配
        app.steps["assemble"] = True
        return {"ok": True, "skipped": True, "reason": "打包形态无需装配"}
    if app.job_running():
        return {"ok": False, "error": "已有装配/安装任务运行中"}, 409
    commands = [
        [str(PIP), "install", "-r", str(PROJECT_ROOT / "requirements.txt")],
        [str(PIP), "install", "-e", str(PROJECT_ROOT / "vendor" / "wechat_agent_sdk")],
        [str(PY), str(PROJECT_ROOT / "patches" / "apply_patches.py"), "--vendor", "--check-only"],
    ]
    app.start_job("assemble", commands, on_done=lambda ok: _assemble_done(app, ok))
    return {"ok": True, "started": True}


def status(app, body: dict | None = None) -> dict:
    job = app.get_job("assemble")
    if job is None:
        return {"ok": True, "done": False, "lines": []}
    return job
