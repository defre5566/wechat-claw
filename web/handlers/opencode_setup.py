"""② opencode 检测与自动安装（长任务 Job，进度可监控）。

- 检测：PATH 中 opencode → ~/.opencode/bin/opencode（官方默认安装目录，Windows 同名 .exe）
- 安装（web 打开前由 wizard 启动时自动触发，也可在向导内手动点）：
  - Linux/macOS：官方安装脚本 `curl -fsSL https://opencode.ai/install | bash -s -- --no-modify-path`
    （脚本处理 arch/musl/AVX2 baseline，非交互、无需 sudo、失败退出码非 0；
    --no-modify-path 不污染 shell 配置，由 config_gen 写 acp.command 绝对路径）
  - Windows：官方 install.ps1 已下线（404），改为直接下载官方 release zip 解压到
    %USERPROFILE%\\.opencode\\bin\\（不依赖 bash/包管理器；只下载官方二进制，不执行远程脚本）
- 安装成功 → 重新检测 → app.steps["opencode"] = True（前端门禁放行）
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

# GitHub release 镜像代理（opencode 下载，按优先级轮询，全部失败才报错）
_MIRRORS = [
    "https://ghproxy.com/https://github.com",
    "https://github.moeyy.xyz/https://github.com",
    "https://mirror.ghproxy.com/https://github.com",
]
# raw.githubusercontent.com 镜像（POSIX 安装脚本用，与 _MIRRORS 独立）
_RAW_MIRRORS = [
    "https://ghproxy.com/https://raw.githubusercontent.com",
    "https://github.moeyy.xyz/https://raw.githubusercontent.com",
    "https://mirror.ghproxy.com/https://raw.githubusercontent.com",
]

# 已知风险（如实声明）：curl|bash 无 checksum 校验（官方脚本动态逻辑无法固定摘要），
# 信任 https://opencode.ai/install 脚本本身；对供应链敏感者可手动安装后点「重新检测」
_POSIX_CMD = "( " + " || ".join(
    "curl -fsSL --retry 3 '" + m + "/anomalyco/opencode/main/scripts/install.sh'"
    for m in _RAW_MIRRORS
) + " ) | bash -s -- --no-modify-path"
# Windows：官方 release zip（x64），下载解压即可，无管道执行远程脚本
_WIN_ZIP_URL = _MIRRORS[0] + "/anomalyco/opencode/releases/latest/download/opencode-windows-x64.zip"

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
    """分平台安装命令（作为长任务 Job 依次执行，带阶段标注）。"""
    if os.name == "nt":
        # PowerShell：多镜像轮询下载 → 解压到安装目录 → 归一化 opencode.exe → 清理
        install_dir = _INSTALL_DIR.replace("'", "''")
        # 构建镜像 URL 列表（每个镜像 + opencode zip 路径）
        urls = "', '".join(
            m + "/anomalyco/opencode/releases/latest/download/opencode-windows-x64.zip"
            for m in _MIRRORS
        )
        ps = (
            "$ErrorActionPreference='Stop';"
            f"$dir='{install_dir}';"
            "New-Item -ItemType Directory -Force -Path $dir | Out-Null;"
            "$tmp=Join-Path $env:TEMP 'opencode-windows-x64.zip';"
            "$urls=@('" + urls + "');"
            "$downloaded=$false;"
            "foreach ($u in $urls) { try { Write-Host '尝试下载: '$u;"
            "Invoke-WebRequest -Uri $u -OutFile $tmp -ErrorAction Stop;"
            "$downloaded=$true; break } catch { Write-Host '下载失败: '$u } };"
            "if (-not $downloaded) { throw '所有下载源均失败' };"
            "Expand-Archive -Force -Path $tmp -DestinationPath $dir;"
            "$exe=Get-ChildItem -Path $dir -Recurse -File | Where-Object { $_.Name -like 'opencode*' } | "
            "Select-Object -First 1;"
            "if ($exe) { Copy-Item -Force $exe.FullName (Join-Path $dir 'opencode.exe') };"
            "Remove-Item -Force $tmp"
        )
        return [{"stage": "下载并安装 opencode（官方 release zip，多镜像轮询）",
                 "cmd": ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", ps]}]
    return [{"stage": "下载并安装 opencode（官方脚本，多镜像轮询）",
             "cmd": ["bash", "-c", f"set -o pipefail; {_POSIX_CMD}"]}]


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
        "cmd": _POSIX_CMD if os.name != "nt" else _WIN_ZIP_URL,
        "doc": _DOC_URL,
        "hint": "未检测到 opencode，可点「自动安装」或复制下方命令手动安装",
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
    app.start_job("opencode_install", build_install_commands(), on_done=lambda ok: install_done(app, ok))
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
