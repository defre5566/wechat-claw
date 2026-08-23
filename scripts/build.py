#!/usr/bin/env python3
"""wechat-claw 打包脚本：PyInstaller 单文件构建（按平台）。

用法:
    .venv/bin/python scripts/build.py            # 本平台构建
    .venv/bin/python scripts/build.py --check    # 只检查环境（PyInstaller 是否可装）

产物：dist/wechat-claw（Linux/macOS）或 dist/wechat-claw.exe（Windows）
说明：PyInstaller 不能交叉编译——Windows/macOS 产物需在对应平台执行本脚本。
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

# Windows 控制台默认 cp1252，中文 print 会 UnicodeEncodeError（CI pwsh 管道下必现）
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parent.parent
SPEC = ROOT / "scripts" / "wechat-claw.spec"
VENV_PY = ROOT / ".venv" / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")


def ensure_pyinstaller() -> None:
    try:
        import PyInstaller  # noqa: F401
    except ImportError:
        print("[build] 安装 PyInstaller ...")
        r = subprocess.run([str(VENV_PY), "-m", "pip", "install", "-i", "https://pypi.tuna.tsinghua.edu.cn/simple", "pyinstaller"], cwd=str(ROOT))
        if r.returncode != 0:
            sys.exit("[build] PyInstaller 安装失败")


def main() -> int:
    if "--check" in sys.argv:
        ensure_pyinstaller()
        _check_opencode()
        print("[build] 环境就绪")
        return 0
    ensure_pyinstaller()
    _check_opencode()
    # vendor SDK 需在环境内（PyInstaller 收集依赖）
    cmd = [str(VENV_PY), "-m", "PyInstaller", "--noconfirm", "--clean", str(SPEC)]
    print("[build]", " ".join(cmd))
    r = subprocess.run(cmd, cwd=str(ROOT))
    if r.returncode != 0:
        print("[build] 打包失败")
        return 1
    out = ROOT / "dist"
    print(f"[build] 完成：{out}")
    for f in sorted(out.iterdir()):
        print(f"  - {f.name} ({f.stat().st_size / 1024 / 1024:.1f} MB)")
    return 0


def _check_opencode() -> None:
    """检查 opencode 捆绑文件（vendor/opencode/opencode.exe）。存在则打包进 exe，不存在则跳过。"""
    oc = ROOT / "vendor" / "opencode" / "opencode.exe"
    if oc.is_file():
        print(f"[build] opencode 已捆绑（{oc.stat().st_size / 1024 / 1024:.1f} MB），将打包进 exe")
    else:
        print("[build] 警告：vendor/opencode/opencode.exe 不存在，opencode 未捆绑")
        print("[build] 如需捆绑，请手动下载 opencode-windows-x64.zip 解压后放至该位置")


if __name__ == "__main__":
    raise SystemExit(main())
