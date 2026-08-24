"""entry.py — Windows exe 入口（引火）。

只做三件事：
1. 种子化：解包到 DATA_ROOT
2. 移交：DATA_ROOT 副本启动，原 exe 退出
3. 启动 web 服务（web.wizard.main）

worker 子进程（bridge / job / install）在模块级拦截，不进 main()。
"""
from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path

from bridge.config import DATA_ROOT, RESOURCE_ROOT, VERSION, no_window_flags

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


def _maybe_seed_data_root() -> None:
    """首启种子化（仅 frozen 形态）：复制平台代码到 DATA_ROOT。"""
    if not getattr(sys, "frozen", False):
        return
    DATA_ROOT_ = DATA_ROOT
    RES_ROOT = RESOURCE_ROOT
    ver_file = DATA_ROOT_ / ".version"
    local_ver = ver_file.read_text().strip() if ver_file.is_file() else ""
    if local_ver == VERSION:
        return
    if local_ver and local_ver > VERSION:
        _log(f"[entry] 本地版本 {local_ver} 高于 exe 版本 {VERSION}，跳过复制")
        return
    for dirname in ("bridge", "modules", "patches", "web", "vendor"):
        src = RES_ROOT / dirname
        if not src.is_dir():
            continue
        for f in src.rglob("*"):
            if "__pycache__" in f.parts or f.suffix == ".pyc" or not f.is_file():
                continue
            rel = f.relative_to(src)
            dst = DATA_ROOT_ / dirname / rel
            if dst.is_file():
                try:
                    if hashlib.sha256(dst.read_bytes()).hexdigest() == hashlib.sha256(f.read_bytes()).hexdigest():
                        continue
                except OSError:
                    pass
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(f, dst)
    exe_src = sys.executable
    exe_dst = DATA_ROOT_ / "wechat-claw.exe"
    try:
        if exe_dst.is_file() and exe_dst.read_bytes() == exe_src.read_bytes():
            pass
        else:
            exe_dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(exe_src, exe_dst)
    except OSError:
        pass
    try:
        ver_file.write_text(VERSION, encoding="utf-8")
        _log(f"[entry] 首启种子化完成（版本 {VERSION}）")
    except OSError:
        pass


def _relaunch_from_data_root(argv: list[str]) -> bool:
    """如果当前 exe 不在 DATA_ROOT 中，移交到 DATA_ROOT 副本后退出。"""
    if not getattr(sys, "frozen", False):
        return False
    installed = DATA_ROOT / "wechat-claw.exe"
    if not installed.is_file():
        return False
    current = Path(sys.executable).resolve()
    if current == installed.resolve():
        return False
    _log(f"[entry] 移交到 DATA_ROOT 副本: {installed}")
    subprocess.Popen(
        [str(installed)] + argv,
        creationflags=no_window_flags(),
    )
    return True


# ── Worker 子进程入口（模块级拦截，不进 main()） ──
if len(sys.argv) > 1 and sys.argv[1] == "-m" and len(sys.argv) > 2:
    if sys.argv[2] == "bridge.main":
        from bridge import main as bridge_main
        import asyncio
        asyncio.run(bridge_main.main())
        sys.exit(0)
    if sys.argv[2] == "bridge.opencode_jobs":
        from bridge import opencode_jobs
        sys.exit(opencode_jobs.main(sys.argv[3:]))
    if sys.argv[2] == "bridge.opencode_install":
        from web.handlers.opencode_setup import install_bundled_sync
        sys.exit(0 if install_bundled_sync() else 1)


def main() -> int:
    """exe 入口：种子化 → 移交 → 启动 web 服务。"""
    argv = sys.argv[1:]
    _maybe_seed_data_root()
    if _relaunch_from_data_root(argv):
        return 0
    from web.wizard import main as web_main
    return web_main()


if __name__ == "__main__":
    raise SystemExit(main())