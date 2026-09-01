"""③ 项目装配：venv + requirements + vendor -e + 补丁校验（长任务，日志轮询）。

命令幂等可重跑；已存在的步骤自动跳过（pip 快速 no-op、补丁已打 SKIP）。
detect：进面板自动检测 .venv 可用性（可用即放行门禁，无需点按钮）。
"""
from __future__ import annotations

import subprocess
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
    steps = [
        {"stage": "安装依赖（pip install -r）", "cmd": [str(PIP), "install", "-i", "https://pypi.tuna.tsinghua.edu.cn/simple", "-r", str(PROJECT_ROOT / "requirements.txt")]},
        {"stage": "安装 vendor SDK", "cmd": [str(PIP), "install", "-e", str(PROJECT_ROOT / "vendor" / "wechat_agent_sdk")]},
        {"stage": "校验补丁", "cmd": [str(PY), str(PROJECT_ROOT / "patches" / "apply_patches.py"), "--vendor", "--check-only"]},
    ]
    app.start_job("assemble", steps, on_done=lambda ok: _assemble_done(app, ok))
    return {"ok": True, "started": True}


def detect(app, body: dict | None = None) -> dict:
    """检测 .venv 是否可用（进装配面板自动调用；可用 → 服务端标记完成，门禁放行）。"""
    if getattr(__import__("sys"), "frozen", False):
        app.steps["assemble"] = True
        return {"ok": True, "ready": True, "reason": "打包形态无需装配"}
    if not PY.is_file():
        return {"ok": True, "ready": False, "reason": ".venv 不存在，请点「开始装配」"}
    try:
        r = subprocess.run(
            [str(PY), "-c", "import yaml, wechat_agent_sdk"],
            capture_output=True, text=True, timeout=60,
        )
        ready = r.returncode == 0
    except Exception:
        ready = False
    if ready:
        app.steps["assemble"] = True
    return {"ok": True, "ready": ready,
            "reason": "" if ready else "依赖缺失，请点「开始装配」"}


def status(app, body: dict | None = None) -> dict:
    job = app.get_job("assemble")
    if job is None:
        return {"ok": True, "done": False, "lines": []}
    return job
