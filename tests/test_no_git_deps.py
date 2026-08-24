"""零 git 依赖修复测试：entry 升级路径回归 + _git_clone ZIP 化 + gitpull 预检。"""
from __future__ import annotations

import io
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from bridge import module_source as ms
from entry import _maybe_seed_data_root


# ---------- entry.py 升级路径回归（str.read_bytes 崩溃点） ----------

def test_seed_data_root_upgrade_path(tmp_path, monkeypatch):
    """带旧 exe 升级：.version 落后 → 种子化不崩且 exe 被更新。

    修复前 sys.executable（str）.read_bytes() 抛 AttributeError，崩在 .version
    写入之前 → 版本永远不更新 → 每次启动都崩（无 git 机器"打不开"的根因）。
    """
    res_root = tmp_path / "res"
    data_root = tmp_path / "data"
    for d in ("bridge", "web"):
        (res_root / d).mkdir(parents=True)
        (res_root / d / "x.py").write_text("pass", encoding="utf-8")
    fake_exe = tmp_path / "wechat-claw.exe"
    fake_exe.write_bytes(b"NEW-EXE-BYTES")
    # 旧版残留：数据根有旧 exe + 落后版本号 → 触发重播种与 exe 更新分支
    (data_root / ".version").parent.mkdir(parents=True, exist_ok=True)
    (data_root / ".version").write_text("0.1.4", encoding="utf-8")
    old_exe = data_root / "wechat-claw.exe"
    old_exe.parent.mkdir(parents=True, exist_ok=True)
    old_exe.write_bytes(b"OLD-EXE-BYTES")

    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(fake_exe))
    monkeypatch.setattr("entry.DATA_ROOT", data_root)
    monkeypatch.setattr("entry.RESOURCE_ROOT", res_root)
    monkeypatch.setattr("entry.VERSION", "0.1.5")
    monkeypatch.setattr("entry._LOG_FILE", tmp_path / "logs" / "web.log")

    _maybe_seed_data_root()  # 修复前在此抛 AttributeError

    assert (data_root / ".version").read_text(encoding="utf-8") == "0.1.5"
    assert (data_root / "wechat-claw.exe").read_bytes() == b"NEW-EXE-BYTES"


def test_seed_data_root_same_exe_no_copy(tmp_path, monkeypatch):
    """exe 内容相同（同版本重跑）：不复制、不崩。"""
    res_root = tmp_path / "res"
    (res_root / "bridge").mkdir(parents=True)
    data_root = tmp_path / "data"
    exe_bytes = b"SAME-EXE"
    fake_exe = tmp_path / "wechat-claw.exe"
    fake_exe.write_bytes(exe_bytes)
    (data_root / ".version").parent.mkdir(parents=True, exist_ok=True)
    (data_root / ".version").write_text("0.1.4", encoding="utf-8")
    data_exe = data_root / "wechat-claw.exe"
    data_exe.write_bytes(exe_bytes)
    mtime_before = data_exe.stat().st_mtime_ns

    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(fake_exe))
    monkeypatch.setattr("entry.DATA_ROOT", data_root)
    monkeypatch.setattr("entry.RESOURCE_ROOT", res_root)
    monkeypatch.setattr("entry.VERSION", "0.1.5")
    monkeypatch.setattr("entry._LOG_FILE", tmp_path / "logs" / "web.log")

    _maybe_seed_data_root()
    assert data_exe.stat().st_mtime_ns == mtime_before  # 内容相同未重写


# ---------- _git_clone ZIP 化 ----------

def _zip_bytes(entries: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for name, data in entries.items():
            z.writestr(name, data)
    return buf.getvalue()


def test_git_clone_github_zip(monkeypatch, tmp_path):
    """GitHub URL：codeload 直连拉取 + 顶层目录落位（零 git）。"""
    payload = _zip_bytes({"repo-main/manifest.json": b"{}",
                          "repo-main/mod/worker.py": b"pass"})
    fetched = []

    def fake_fetch(url, **kw):
        fetched.append(url)
        return payload

    monkeypatch.setattr(ms, "_fetch_url", fake_fetch)
    dest = tmp_path / "dest"
    assert ms._git_clone("https://github.com/foo/bar.git", dest) is True
    assert (dest / "manifest.json").is_file()
    assert fetched[0].startswith("https://codeload.github.com/foo/bar/zip/")


def test_git_clone_gitee_compat(monkeypatch, tmp_path):
    """旧 gitee URL：自动转 GitHub 同名仓库（gitee 归档需登录不可用）。"""
    payload = _zip_bytes({"repo-main/manifest.json": b"{}"})
    monkeypatch.setattr(ms, "_fetch_url", lambda url, **kw: payload)
    dest = tmp_path / "dest"
    assert ms._git_clone("https://gitee.com/foo/bar.git", dest) is True
    assert (dest / "manifest.json").is_file()


def test_git_clone_rejects_fake200(monkeypatch, tmp_path):
    """假 200（HTML 错误页）：拒收并轮询下一源。"""
    good = _zip_bytes({"repo-main/manifest.json": b"{}"})
    state = {"n": 0}

    def fake_fetch(url, **kw):
        state["n"] += 1
        return b"<!DOCTYPE html>" if state["n"] == 1 else good

    monkeypatch.setattr(ms, "_fetch_url", fake_fetch)
    dest = tmp_path / "dest"
    assert ms._git_clone("https://github.com/foo/bar.git", dest) is True
    assert state["n"] == 2  # 第一个源被拒后轮询到了第二个


def test_git_clone_all_fail(monkeypatch, tmp_path):
    """全源失败返回 False（调用方报 clone 失败，不崩）。"""
    monkeypatch.setattr(ms, "_fetch_url", lambda url, **kw: (_ for _ in ()).throw(IOError("net")))
    assert ms._git_clone("https://github.com/foo/bar.git", tmp_path / "d") is False


def test_official_source_is_github():
    """官方源 URL 已切 GitHub 主源（gitee 归档需登录，纯 HTTP 拉不到）。"""
    assert ms.OFFICIAL_SOURCE["url"].startswith("https://github.com/")


# ---------- gitpull_get 预检 ----------

def test_gitpull_no_git(monkeypatch):
    """无 git 环境：明确提示用「下载最新版」，不报 WinError 2 原文。"""
    import shutil as _sh
    from web.handlers import admin
    monkeypatch.setattr(_sh, "which", lambda _: None)
    r = admin.gitpull_get(None)
    assert r["ok"] is False and "未安装 git" in r["error"]
