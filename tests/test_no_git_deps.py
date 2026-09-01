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


def test_seed_skip_when_local_newer_multi_digit(tmp_path, monkeypatch):
    """0.1.10 > 0.1.6 元组比较：本地更高跳复制（字符串比较会误判为更旧→误播种）。"""
    res_root = tmp_path / "res"
    (res_root / "bridge").mkdir(parents=True)
    (res_root / "bridge" / "x.py").write_text("v2", encoding="utf-8")
    data_root = tmp_path / "data"
    (data_root / ".version").parent.mkdir(parents=True, exist_ok=True)
    (data_root / ".version").write_text("0.1.10", encoding="utf-8")

    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(tmp_path / "wechat-claw.exe"))
    monkeypatch.setattr("entry.DATA_ROOT", data_root)
    monkeypatch.setattr("entry.RESOURCE_ROOT", res_root)
    monkeypatch.setattr("entry.VERSION", "0.1.6")
    monkeypatch.setattr("entry._LOG_FILE", tmp_path / "logs" / "web.log")

    _maybe_seed_data_root()

    assert not (data_root / "bridge" / "x.py").exists()  # 未复制
    assert (data_root / ".version").read_text(encoding="utf-8") == "0.1.10"  # 未降级覆盖


def test_seed_malformed_local_ver_falls_back_equality(tmp_path, monkeypatch):
    """.version 内容损坏：字节等值兜底返回，不等则按更旧处理（重播种幂等）。"""
    res_root = tmp_path / "res"
    (res_root / "bridge").mkdir(parents=True)
    (res_root / "bridge" / "x.py").write_text("v1", encoding="utf-8")
    data_root = tmp_path / "data"
    (data_root / ".version").parent.mkdir(parents=True, exist_ok=True)
    (data_root / ".version").write_text("garbage", encoding="utf-8")

    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(tmp_path / "wechat-claw.exe"))
    monkeypatch.setattr("entry.DATA_ROOT", data_root)
    monkeypatch.setattr("entry.RESOURCE_ROOT", res_root)
    monkeypatch.setattr("entry.VERSION", "0.1.6")
    monkeypatch.setattr("entry._LOG_FILE", tmp_path / "logs" / "web.log")

    _maybe_seed_data_root()

    assert (data_root / "bridge" / "x.py").read_text(encoding="utf-8") == "v1"
    assert (data_root / ".version").read_text(encoding="utf-8") == "0.1.6"  # 已回写正常版本


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


# ---------- 完整路径回归（抓顶层 import 漏删/NameError 类问题） ----------

def _fake_repo(dest: Path) -> None:
    """落一个含 manifest.json 的假仓库目录（模拟 _git_clone 拉取结果）。"""
    import json as _json
    dest.mkdir(parents=True, exist_ok=True)
    (dest / "manifest.json").write_text(_json.dumps({
        "modules": [{"name": "todo", "version": "1.0", "purpose": "p", "sha256": ""}]
    }), encoding="utf-8")


def test_fetch_source_full_path(monkeypatch, tmp_path):
    """fetch_source 完整路径（源刷新）：mkdtemp/清理/manifest 读取全真走。

    回归 1f125aa 引入的顶层 tempfile 误删 NameError（mock _fetch_url 的单测
    覆盖不到 fetch_source 内部的 mkdtemp 调用）。
    """
    monkeypatch.setattr(ms, "_git_clone",
                        lambda url, dest: (_fake_repo(dest), True)[1])
    src = {"id": "official", "name": "官方模块库", "type": "github",
           "url": "https://github.com/foo/bar.git", "builtin": False}
    res = ms.fetch_source(src)
    assert res["ok"] is True
    assert res["modules"][0]["name"] == "todo"
    assert len(res["fingerprint"]) == 64  # sha256 hex


def test_fetch_source_clone_fail(monkeypatch, tmp_path):
    """clone 失败：返回 ok=False + 明确错误，不抛异常。"""
    monkeypatch.setattr(ms, "_git_clone", lambda url, dest: False)
    res = ms.fetch_source({"id": "x", "type": "github", "url": "https://github.com/foo/bar.git"})
    assert res["ok"] is False and "clone 失败" in res["error"]


def test_update_module_full_path(monkeypatch, tmp_path):
    """update_module_from_source 完整路径（更新）：mkdtemp/备份/复制全真走。"""
    import json as _json
    # 已装模块（含 module.json + worker + token）
    installed = tmp_path / "modules" / "todo"
    installed.mkdir(parents=True)
    (installed / "module.json").write_text(_json.dumps({"name": "todo"}), encoding="utf-8")
    (installed / "todo_worker.py").write_text("pass", encoding="utf-8")
    (installed / "token").write_text("secret", encoding="utf-8")
    monkeypatch.setattr(ms, "MODULES_DIR", tmp_path / "modules")

    # 源侧新包（worker 内容更新）
    def fake_clone(url, dest):
        mod = Path(dest) / "todo"
        mod.mkdir(parents=True)
        (mod / "module.json").write_text(_json.dumps({"name": "todo"}), encoding="utf-8")
        (mod / "todo_worker.py").write_text("# new version", encoding="utf-8")
        (Path(dest) / "manifest.json").write_text(_json.dumps({
            "modules": [{"name": "todo", "version": "2.0", "purpose": "", "sha256": "",
                         "files": ["module.json", "todo_worker.py"]}]
        }), encoding="utf-8")
        return True

    monkeypatch.setattr(ms, "_git_clone", fake_clone)
    # register 联动隔离（本测试只验证 module_source 层）
    monkeypatch.setattr("modules.register.refresh_module_config", lambda name: None)
    monkeypatch.setattr("modules.register.save_module_state", lambda *a, **kw: None)

    sources = [{"id": "official", "name": "官方模块库", "type": "github",
                "url": "https://github.com/foo/bar.git",
                "modules": [{"name": "todo", "version": "2.0", "purpose": "", "sha256": "",
                             "files": ["module.json", "todo_worker.py"]}]}]
    r = ms.update_module_from_source(sources, "official", "todo")
    assert r.get("ok") is True and r.get("updated") is True
    assert (installed / "todo_worker.py").read_text(encoding="utf-8") == "# new version"
    assert (installed / "token").read_text(encoding="utf-8") == "secret"  # token 不轮换
