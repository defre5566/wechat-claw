"""② opencode 检测与自动安装（长任务 Job，进度可监控）。

- 检测：PATH 中 opencode → ~/.opencode/bin/opencode（官方默认安装目录，Windows 同名 .exe）
- 安装（仅打包形态 exe）：
  - 从 exe 包内 vendor/opencode/opencode.exe 复制到 ~/.opencode/bin/，零网络依赖
- 源码形态：用户手动放置 opencode 到 ~/.opencode/bin/，检测后自动识别
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from datetime import datetime

SELFTEST = os.environ.get("WEB_SELFTEST") == "1"

# 官方默认安装目录（install 脚本 INSTALL_DIR；Windows 同名）
_INSTALL_DIR = os.path.expanduser("~/.opencode/bin")
_DOC_URL = "https://opencode.ai/docs/install"

# 向导安装标记：存在 = opencode 由 wechat-claw 安装（XDG 数据收敛到数据根、卸载时一并删除）
from bridge.config import DATA_ROOT  # noqa: E402

_INSTALL_MARKER = DATA_ROOT / ".config" / "opencode-installed.json"


def _bin_name() -> str:
    return "opencode.exe" if os.name == "nt" else "opencode"


def detect_installed() -> dict | None:
    """检测 opencode：PATH 优先，其次官方默认安装目录。返回 {version, path} 或 None。"""
    exe = _bin_name()
    cands: list[str] = []
    which = shutil.which("opencode")
    if which:
        cands.append(which)
    for name in ("opencode.exe", "opencode"):  # Windows zip 解压后文件名不固定
        p = os.path.join(_INSTALL_DIR, name)
        if os.path.isfile(p) and p not in cands:
            cands.append(p)
    for p in cands:
        # 刚解压/杀软（Defender 实时扫描）期 --version 可能瞬时失败：重试 3 次再判不存在
        for attempt in (1, 2, 3):
            try:
                r = subprocess.run([p, "--version"], capture_output=True, text=True, timeout=15)
            except Exception:
                r = None
            if r is not None and r.returncode == 0:
                out = (r.stdout or r.stderr or "").strip().splitlines()
                return {"version": out[0] if out else "已安装", "path": p}
            if attempt < 3:
                import time
                time.sleep(0.5)
    return None


def build_install_commands() -> list[dict]:
    """分平台安装命令（仅打包形态 exe 有效：从包内复制 opencode 到安装目录）。"""
    if os.name == "nt":
        # 打包形态：从 exe 解包目录 RESOURCE_ROOT 复制 opencode.exe
        from bridge.config import RESOURCE_ROOT
        bundled = RESOURCE_ROOT / "vendor" / "opencode" / "opencode.exe"
        if bundled.is_file():
            install_dir = _INSTALL_DIR.replace("'", "''")
            ps = (
                "$ErrorActionPreference='Stop';"
                f"$dir='{install_dir}';"
                "New-Item -ItemType Directory -Force -Path $dir | Out-Null;"
                f"Copy-Item -Force '{bundled}' -Destination (Join-Path $dir 'opencode.exe');"
            )
            return [{"stage": "安装捆绑的 opencode", "cmd": ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", ps]}]
    return []


def install_done(app, ok: bool) -> None:
    """安装 Job 完成回调：成功 → 标记步骤完成 + 写向导安装标记（收敛隔离/卸载删除依据）。"""
    if not ok:
        return
    app.steps["opencode"] = True
    try:
        _INSTALL_MARKER.parent.mkdir(parents=True, exist_ok=True)
        d = detect_installed()
        _INSTALL_MARKER.write_text(
            json.dumps({
                "version": d["version"] if d else "",
                "installed_at": datetime.now().isoformat(timespec="seconds"),
                "method": "wizard",
            }, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    except OSError:
        pass


def detect(app, body: dict | None = None) -> dict:
    """纯检测（前端「重新检测」用）：已装 → already；未装 → 返回手动安装命令/文档。"""
    if SELFTEST:
        app.steps["opencode"] = True
        return {"ok": True, "already": True, "version": "selftest-mock"}
    d = detect_installed()
    if d:
        app.steps["opencode"] = True
        return {"ok": True, "already": True, "version": d["version"]}
    return {
        "ok": False,
        "missing": True,
        "cmd": "",
        "doc": _DOC_URL,
        "hint": "未检测到 opencode，请手动下载 opencode-windows-x64.zip 解压后放置到 " + _INSTALL_DIR,
    }


def install(app, body: dict | None = None) -> dict:
    """自动安装：已装 → already；任务运行中 → running；否则启动长任务 Job。"""
    if SELFTEST:
        app.steps["opencode"] = True
        return {"ok": True, "already": True, "version": "selftest-mock"}
    d = detect_installed()
    if d:
        app.steps["opencode"] = True
        return {"ok": True, "already": True, "version": d["version"]}
    if app.job_running():
        return {"ok": False, "error": "已有任务运行中"}, 409
    cmds = build_install_commands()
    if not cmds:
        return {"ok": False, "error": "未找到捆绑的 opencode（源码形态需手动安装）"}, 400
    app.start_job("opencode_install", cmds, on_done=lambda ok: install_done(app, ok))
    return {"ok": True, "started": True}


def status(app, body: dict | None = None) -> dict:
    """安装 Job 增量日志（前端轮询显示进度）。started=false 表示从未启动。"""
    job = app.get_job("opencode_install")
    if job is None:
        return {"ok": True, "started": False, "done": False, "lines": []}
    snap = job
    snap["started"] = True
    return snap


# 兼容旧调用（selftest 用 POST /api/opencode/install 断言 ok）
handle = install
