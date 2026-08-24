"""② opencode 检测与自动安装（长任务 Job，进度可监控）。

- 检测：PATH → DATA_ROOT/bin（本系统部署）→ ~/.opencode/bin（官方默认安装目录）
  vendor 捆绑位置不算已安装（bridge 主链路寻址不到，需先部署）
- 安装（收敛到工作目录根 DATA_ROOT/bin，文件名按平台）：
  - vendor 捆绑（RESOURCE_ROOT/DATA_ROOT 下 vendor/opencode/）→ 同步复制，零网络
  - 无捆绑 → Job 子进程 Python 下载器（GitHub release + 镜像，zip/tar.gz 解压）
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import urllib.request
from datetime import datetime
from pathlib import Path

# 直接脚本运行引导（源码形态下载 Job：python .../opencode_setup.py --download-install）
if __package__ in (None, ""):  # pragma: no cover - 引导路径
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

SELFTEST = os.environ.get("WEB_SELFTEST") == "1"

# 官方默认安装目录（识别用户自装；Windows 同名 .exe）
_OFFICIAL_DIR = os.path.expanduser("~/.opencode/bin")
_DOC_URL = "https://github.com/anomalyco/opencode/releases"

# 向导安装标记：存在 = opencode 由 wechat-claw 安装（XDG 数据收敛到数据根、卸载时一并删除）
from bridge.config import DATA_ROOT, RESOURCE_ROOT  # noqa: E402

_INSTALL_DIR = DATA_ROOT / "bin"  # 本系统部署目录（收敛到工作目录根）
_INSTALL_MARKER = DATA_ROOT / ".config" / "opencode-installed.json"

# 下载源：GitHub release + 镜像（前缀拼官方 URL，空串 = 官方直连）。
# ghproxy.com 已失效（200 返回 HTML 错误页 + 读挂起）不再收录；
# 镜像不可达时最坏各耗 45s 后自动落到下一源，直连兜底。
_RELEASE_BASE = "https://github.com/anomalyco/opencode/releases/latest/download/"
_MIRRORS = [
    "https://github.moeyy.xyz/",
    "https://mirror.ghproxy.com/",
    "",
]


def _bin_name() -> str:
    return "opencode.exe" if os.name == "nt" else "opencode"


def _asset_name() -> str | None:
    """按平台选 release asset（anomalyco/opencode latest 命名）。"""
    machine = (os.uname().machine if hasattr(os, "uname") else "").lower() or "amd64"
    arch = "arm64" if machine in ("arm64", "aarch64") else "x64"
    if os.name == "nt":
        return f"opencode-windows-{arch}.zip"
    if sys.platform == "darwin":
        return f"opencode-darwin-{arch}.zip"
    if sys.platform.startswith("linux"):
        return f"opencode-linux-{arch}.tar.gz"
    return None


def detect_installed() -> dict | None:
    """检测 opencode：PATH → 本系统部署目录 → 官方默认目录。返回 {version, path} 或 None。"""
    cands: list[str] = []
    which = shutil.which("opencode")
    if which:
        cands.append(which)
    for p in (Path(_INSTALL_DIR) / _bin_name(),):
        if p.is_file() and str(p) not in cands:
            cands.append(str(p))
    for name in ("opencode.exe", "opencode"):  # Windows zip 解压后文件名不固定
        p = os.path.join(_OFFICIAL_DIR, name)
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


def _find_bundled() -> Path | None:
    """查找捆绑的 opencode 文件：RESOURCE_ROOT 优先，DATA_ROOT 兜底（按平台文件名）。"""
    name = _bin_name()
    for root in (RESOURCE_ROOT, DATA_ROOT):
        bundled = root / "vendor" / "opencode" / name
        if bundled.is_file():
            return bundled
    return None


def _write_marker(method: str) -> None:
    """写向导安装标记（XDG 收敛/卸载删除依据）；检测不出版本也照写。"""
    _INSTALL_MARKER.parent.mkdir(parents=True, exist_ok=True)
    d = detect_installed()
    _INSTALL_MARKER.write_text(
        json.dumps({
            "version": d["version"] if d else "",
            "installed_at": datetime.now().isoformat(timespec="seconds"),
            "method": method,
        }, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def install_bundled_sync() -> bool:
    """同步安装捆绑的 opencode（零子进程、零转义、零竞态）。"""
    bundled = _find_bundled()
    if bundled is None:
        return False
    install_dir = Path(_INSTALL_DIR)
    install_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(str(bundled), str(install_dir / _bin_name()))
    try:
        _write_marker("autostart")
    except OSError:
        pass
    return True


def _download_bytes(url: str, connect_budget: int = 20, total_budget: int = 240) -> bytes:
    """下载 URL 全量内容，分阶段预算控制。

    实测问题与对策：
    - DNS 挂起不受 urlopen timeout 控制（不可达镜像单源可拖 180s）
      → 连接期 Event + connect_budget 短预算，超时弃源
    - release 文件 ~58MB，慢速网络需要较长下载窗口
      → 下载期 total_budget 总预算 + socket timeout 30s（单块 30s 无数据判死）
    daemon 线程随短命子进程退出销毁，无泄漏影响。
    """
    import threading
    import time
    connected = threading.Event()
    result: dict = {}

    def _worker() -> None:
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "wechat-claw-install"})
            with urllib.request.urlopen(req, timeout=30) as resp:
                connected.set()  # 响应头已到，连接可用
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


def _looks_valid(data: bytes, asset: str) -> bool:
    """内容 magic 校验：镜像可能返回 200 的 HTML 错误页（ghproxy 等已失效），
    zip 须以 PK 开头、tar.gz 须以 gzip magic 开头，不匹配视为该源失败。"""
    if asset.endswith(".zip"):
        return data[:2] == b"PK"
    return data[:2] == b"\x1f\x8b"


def _extract_exe(data: bytes, asset: str) -> bytes | None:
    """从 zip/tar.gz 中定位 opencode 可执行文件内容。"""
    import io

    exe_names = {"opencode", "opencode.exe"}
    if asset.endswith(".zip"):
        import zipfile
        z = zipfile.ZipFile(io.BytesIO(data))
        for name in z.namelist():
            if Path(name).name.lower() in exe_names:
                with z.open(name) as f:
                    return f.read()
        return None
    import tarfile
    t = tarfile.open(fileobj=io.BytesIO(data))
    for member in t.getmembers():
        if Path(member.name).name.lower() in exe_names and member.isfile():
            f = t.extractfile(member)
            if f is not None:
                return f.read()
    return None


def download_install_sync() -> tuple[bool, str]:
    """下载安装 opencode 到 DATA_ROOT/bin（Python 实现，三平台统一，不依赖 sh/curl）。

    返回 (ok, error)：全部镜像失败时 error 为最后错误。
    """
    asset = _asset_name()
    if asset is None:
        return False, f"不支持的平台：{sys.platform}"
    print(f"[install] 下载 opencode: {asset}")
    data: bytes | None = None
    err = ""
    for mirror in _MIRRORS:
        url = mirror + _RELEASE_BASE + asset
        try:
            print(f"[install] 尝试源: {url}")
            data = _download_bytes(url)
            if not _looks_valid(data, asset):
                err = f"{url}: 返回内容非 {asset} 格式（镜像失效）"
                print(f"[install] 失败: {err}")
                continue
            print(f"[install] 下载完成（{len(data) / 1024 / 1024:.1f} MB）")
            break
        except Exception as e:  # noqa: BLE001
            err = f"{url}: {e}"
            print(f"[install] 失败: {err}")
            continue
    if data is None:
        return False, f"所有下载源均失败（最后错误：{err}）"
    exe = _extract_exe(data, asset)
    if exe is None:
        return False, f"{asset} 中未找到 opencode 可执行文件"
    install_dir = Path(_INSTALL_DIR)
    install_dir.mkdir(parents=True, exist_ok=True)
    target = install_dir / _bin_name()
    target.write_bytes(exe)
    if os.name != "nt":
        target.chmod(0o755)
    print(f"[install] 已安装到 {target}")
    try:
        _write_marker("download")
    except OSError:
        pass
    return True, ""


def run_install_cli(argv: list[str]) -> int:
    """子进程入口（下载 Job / exe worker 拦截共用）。"""
    if "--download-install" in argv:
        ok, err = download_install_sync()
        if not ok:
            print(f"[install] {err}")
        return 0 if ok else 1
    return 2


def build_install_commands() -> list[dict]:
    """下载安装 Job 命令（无捆绑时）。

    - 打包形态：exe 经 entry.py 模块级拦截 `-m bridge.opencode_install`
    - 源码形态：脚本绝对路径直接跑（自带 sys.path 引导，不依赖 cwd）
    """
    if getattr(sys, "frozen", False):
        cmd = [sys.executable, "-m", "bridge.opencode_install", "--download-install"]
    else:
        cmd = [sys.executable, str(RESOURCE_ROOT / "web" / "handlers" / "opencode_setup.py"),
               "--download-install"]
    return [{"stage": "下载安装 opencode", "cmd": cmd}]


def install_done(app, ok: bool) -> None:
    """安装 Job 完成回调：成功 → 标记步骤完成（marker 由下载器内写入）。"""
    if not ok:
        return
    app.steps["opencode"] = True


def detect(app, body: dict | None = None) -> dict:
    """纯检测（前端「重新检测」用）：已装 → already；未装 → bundled 标记 + 安装提示。"""
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
        "bundled": _find_bundled() is not None,
        "doc": _DOC_URL,
        "hint": "未检测到 opencode，请点击「安装 opencode」自动安装，或手动下载解压后放置到 "
                + str(_INSTALL_DIR),
    }


def install(app, body: dict | None = None) -> dict:
    """自动安装：已装 → already；有捆绑 → 同步部署（立即完成）；否则启动下载 Job。"""
    if SELFTEST:
        app.steps["opencode"] = True
        return {"ok": True, "already": True, "version": "selftest-mock"}
    d = detect_installed()
    if d:
        app.steps["opencode"] = True
        return {"ok": True, "already": True, "version": d["version"]}
    if _find_bundled() is not None:
        if install_bundled_sync():
            d2 = detect_installed()
            app.steps["opencode"] = True
            return {"ok": True, "installed": True, "version": d2["version"] if d2 else ""}
        return {"ok": False, "error": "捆绑的 opencode 部署失败（检查数据根 bin/ 目录权限）"}, 500
    if app.job_running():
        return {"ok": False, "error": "已有任务运行中"}, 409
    cmds = build_install_commands()
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

if __name__ == "__main__":  # 源码形态下载 Job 子进程入口
    raise SystemExit(run_install_cli(sys.argv[1:]))
