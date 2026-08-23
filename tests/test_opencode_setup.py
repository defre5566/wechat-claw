"""opencode_setup 检测重试测试：--version 瞬时失败 → 重试成功。"""
from __future__ import annotations

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


def test_detect_retries_then_succeeds(monkeypatch, tmp_path):
    """第一次 --version 失败（刚解压/杀软扫描），重试成功 → 判定已安装。"""
    install_dir = tmp_path / ".opencode" / "bin"
    install_dir.mkdir(parents=True)
    (install_dir / "opencode.exe").write_text("x", encoding="utf-8")
    monkeypatch.setattr(osu, "_INSTALL_DIR", str(install_dir))
    monkeypatch.setattr(osu.shutil, "which", lambda _: None)
    calls = {"n": 0}

    def fake_run(*a, **kw):
        calls["n"] += 1
        if calls["n"] == 1:
            raise FileNotFoundError("access denied / 扫描中")
        return FakeProc(rc=0, out="opencode 1.18.18")

    monkeypatch.setattr(osu.subprocess, "run", fake_run)
    r = osu.detect_installed()
    assert r is not None and r["path"].endswith("opencode.exe")
    assert calls["n"] == 2


def test_detect_fails_after_retries(monkeypatch, tmp_path):
    """两次都失败 → 判定未安装。"""
    install_dir = tmp_path / ".opencode" / "bin"
    install_dir.mkdir(parents=True)
    (install_dir / "opencode.exe").write_text("x", encoding="utf-8")
    monkeypatch.setattr(osu, "_INSTALL_DIR", str(install_dir))
    monkeypatch.setattr(osu.shutil, "which", lambda _: None)
    monkeypatch.setattr(osu.subprocess, "run",
                        lambda *a, **kw: FakeProc(rc=1, out=""))
    assert osu.detect_installed() is None
