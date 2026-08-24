"""opencode_setup 测试：检测重试 / 部署目录识别 / 捆绑同步部署 / 下载 Job 命令。"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from web.handlers import opencode_setup as osu


class FakeProc:
    def __init__(self, rc=0, out=""):
        self.returncode = rc
        self.stdout = out
        self.stderr = ""


def _make_exe(tmp_path, monkeypatch, *, which=None):
    """隔离安装目录与官方目录，返回安装目录路径。"""
    install_dir = tmp_path / "bin"
    install_dir.mkdir(parents=True, exist_ok=True)
    official_dir = tmp_path / "official" / "bin"
    official_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(osu, "_INSTALL_DIR", install_dir)
    monkeypatch.setattr(osu, "_OFFICIAL_DIR", str(official_dir))
    monkeypatch.setattr(osu.shutil, "which", lambda _: which)
    return install_dir


def test_detect_retries_then_succeeds(monkeypatch, tmp_path):
    """第一次 --version 失败（刚解压/杀软扫描），重试成功 → 判定已安装。"""
    install_dir = _make_exe(tmp_path, monkeypatch)
    (install_dir / osu._bin_name()).write_text("x", encoding="utf-8")
    calls = {"n": 0}

    def fake_run(*a, **kw):
        calls["n"] += 1
        if calls["n"] == 1:
            raise FileNotFoundError("access denied / 扫描中")
        return FakeProc(rc=0, out="opencode 1.18.18")

    monkeypatch.setattr(osu.subprocess, "run", fake_run)
    r = osu.detect_installed()
    assert r is not None and r["path"].endswith(osu._bin_name())
    assert calls["n"] == 2


def test_detect_fails_after_retries(monkeypatch, tmp_path):
    """重试全失败 → 判定未安装。"""
    install_dir = _make_exe(tmp_path, monkeypatch)
    (install_dir / osu._bin_name()).write_text("x", encoding="utf-8")
    monkeypatch.setattr(osu.subprocess, "run",
                        lambda *a, **kw: FakeProc(rc=1, out=""))
    assert osu.detect_installed() is None


def test_detect_prefers_deploy_dir_over_official(monkeypatch, tmp_path):
    """本系统部署目录与官方目录都有 → 部署目录优先（候选顺序）。"""
    install_dir = _make_exe(tmp_path, monkeypatch)
    (install_dir / osu._bin_name()).write_text("x", encoding="utf-8")
    (Path(osu._OFFICIAL_DIR) / "opencode").write_text("x", encoding="utf-8")
    monkeypatch.setattr(osu.subprocess, "run",
                        lambda *a, **kw: FakeProc(rc=0, out="opencode 1.0"))
    r = osu.detect_installed()
    assert r is not None and r["path"] == str(install_dir / osu._bin_name())


def test_install_bundled_sync_copies_with_platform_name(monkeypatch, tmp_path):
    """捆绑部署：复制到部署目录，文件名按平台（Linux 不带 .exe）。"""
    install_dir = _make_exe(tmp_path, monkeypatch)
    bundled = tmp_path / "vendor" / "opencode" / osu._bin_name()
    bundled.parent.mkdir(parents=True)
    bundled.write_bytes(b"binary")
    monkeypatch.setattr(osu, "_find_bundled", lambda: bundled)
    monkeypatch.setattr(osu, "_INSTALL_MARKER", tmp_path / ".config" / "marker.json")
    monkeypatch.setattr(osu.subprocess, "run",
                        lambda *a, **kw: FakeProc(rc=0, out="opencode 1.18"))
    assert osu.install_bundled_sync() is True
    deployed = install_dir / osu._bin_name()
    assert deployed.is_file() and deployed.read_bytes() == b"binary"


def test_build_install_commands_no_sh_windows(monkeypatch, tmp_path):
    """下载 Job 命令：Windows 下不得再产出 sh -c（回归：旧实现 Windows 必炸）。"""
    monkeypatch.setattr(os, "name", "nt")
    monkeypatch.setattr(osu.sys, "frozen", False, raising=False)
    cmds = osu.build_install_commands()
    assert len(cmds) == 1
    argv = cmds[0]["cmd"]
    assert "sh" not in argv
    assert "--download-install" in argv
    # 源码形态：脚本绝对路径可直接运行
    assert Path(argv[1]).name == "opencode_setup.py"


def test_asset_name_platform(monkeypatch):
    assert osu._asset_name() is not None
    assert osu._asset_name().startswith("opencode-")
