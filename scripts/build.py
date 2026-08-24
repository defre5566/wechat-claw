#!/usr/bin/env python3
"""wechat-claw 打包脚本：PyInstaller 单文件构建（按平台）。

用法:
    .venv/bin/python scripts/build.py              # 本平台构建（自动下载 opencode）
    .venv/bin/python scripts/build.py --no-download # 跳过 opencode 下载
    .venv/bin/python scripts/build.py --check       # 只检查环境

产物：dist/wechat-claw（Linux/macOS）或 dist/wechat-claw.exe（Windows）
说明：PyInstaller 不能交叉编译——Windows/macOS 产物需在对应平台执行本脚本。
Windows 构建时自动下载 opencode 并捆绑进 exe（CI 与本地均适用）。
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

# GitHub release 镜像（opencode 下载，按优先级轮询，全部失败才报错）
# ghproxy.com 已失效（200 返回 HTML 错误页）不再收录；假 200 由 BadZipFile 兜底跳过
_OPENCODE_URLS = [
    "https://github.moeyy.xyz/https://github.com/anomalyco/opencode/releases/latest/download/opencode-windows-x64.zip",
    "https://mirror.ghproxy.com/https://github.com/anomalyco/opencode/releases/latest/download/opencode-windows-x64.zip",
    "https://github.com/anomalyco/opencode/releases/latest/download/opencode-windows-x64.zip",
]


def ensure_pyinstaller() -> None:
    try:
        import PyInstaller  # noqa: F401
    except ImportError:
        print("[build] 安装 PyInstaller ...")
        r = subprocess.run([str(VENV_PY), "-m", "pip", "install", "-i", "https://pypi.tuna.tsinghua.edu.cn/simple", "pyinstaller"], cwd=str(ROOT))
        if r.returncode != 0:
            sys.exit("[build] PyInstaller 安装失败")


def _fetch(url: str, connect_budget: int = 20, total_budget: int = 300) -> bytes:
    """下载 URL 全量内容，分阶段预算（与 web/handlers/opencode_setup.py 同方案）。

    DNS 挂起不受 urlopen timeout 控制（失效镜像单源可拖数分钟）——
    连接期 Event 短预算 + 下载期总预算，超时弃源换下一个。
    """
    import threading
    import urllib.request
    connected = threading.Event()
    result: dict = {}

    def _worker() -> None:
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "wechat-claw-build"})
            with urllib.request.urlopen(req, timeout=30) as resp:
                connected.set()
                chunks: list[bytes] = []
                while True:
                    chunk = resp.read(65536)
                    if not chunk:
                        break
                    chunks.append(chunk)
                result["data"] = b"".join(chunks)
        except Exception as e:  # noqa: BLE001
            result["err"] = e

    t = threading.Thread(target=_worker, daemon=True)
    t.start()
    if not connected.wait(connect_budget):
        raise TimeoutError(f"{connect_budget}s 内未建立连接（DNS/网络挂起）")
    t.join(total_budget - connect_budget)
    if t.is_alive():
        raise TimeoutError(f"下载超总预算 {total_budget}s")
    if "err" in result:
        raise result["err"]
    return result["data"]


def _download_opencode() -> bool:
    """下载 opencode-windows-x64.zip 并解压到 vendor/opencode/opencode.exe。"""
    import io
    import zipfile
    oc = ROOT / "vendor" / "opencode" / "opencode.exe"
    if oc.is_file():
        print(f"[build] opencode 已存在（{oc.stat().st_size / 1024 / 1024:.1f} MB），跳过下载")
        return True
    if sys.platform != "win32":
        print("[build] 非 Windows 平台，跳过 opencode 下载")
        return False
    oc_parent = oc.parent
    oc_parent.mkdir(parents=True, exist_ok=True)
    for url in _OPENCODE_URLS:
        try:
            print(f"[build] 下载 opencode: {url}")
            data = _fetch(url)
            if data[:2] != b"PK":  # 镜像假 200（HTML 错误页）快速跳过
                raise ValueError("返回内容非 zip（镜像失效）")
            z = zipfile.ZipFile(io.BytesIO(data))
            for name in z.namelist():
                if "opencode.exe" in name or "opencode" in name.lower() and not name.endswith("/"):
                    with z.open(name) as f:
                        oc.write_bytes(f.read())
                    oc.chmod(0o755)
                    print(f"[build] opencode 下载完成（{oc.stat().st_size / 1024 / 1024:.1f} MB）")
                    return True
            print(f"[build] zip 中未找到 opencode.exe")
        except Exception as e:
            print(f"[build] 下载失败: {e}")
            continue
    print("[build] 所有下载源均失败，opencode 未捆绑")
    return False


def main() -> int:
    if "--check" in sys.argv:
        ensure_pyinstaller()
        _check_opencode()
        print("[build] 环境就绪")
        return 0
    ensure_pyinstaller()
    if "--no-download" not in sys.argv:
        _download_opencode()
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


if __name__ == "__main__":
    raise SystemExit(main())
