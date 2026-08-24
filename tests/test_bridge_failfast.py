"""bridge 启动 fail-fast 测试：opencode 未找到时明确报错，不裸名盲试。"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from bridge import main as bridge_main


def test_resolve_acp_command_failfast(monkeypatch):
    """resolve_opencode 找不到 → SystemExit(1)，错误信息含三个已查位置。"""
    monkeypatch.setattr("bridge.config.resolve_opencode", lambda: None)
    with pytest.raises(SystemExit) as ei:
        bridge_main.resolve_acp_command()
    assert ei.value.code == 1


def test_resolve_acp_command_returns_path(monkeypatch):
    """resolve_opencode 成功 → 原样返回绝对路径。"""
    monkeypatch.setattr("bridge.config.resolve_opencode",
                        lambda: "C:/wechat-claw/bin/opencode.exe")
    assert bridge_main.resolve_acp_command() == "C:/wechat-claw/bin/opencode.exe"


def test_start_bridge_precheck_missing(monkeypatch):
    """start_bridge 预检：opencode 缺失 → ok=False 且不 spawn（无必败子进程）。"""
    from web.handlers import admin
    spawned = []
    monkeypatch.setattr("bridge.config.resolve_opencode", lambda: None)
    monkeypatch.setattr("web.handlers.service_up._spawn_bridge_now",
                        lambda: spawned.append(1) or [])
    r = admin.start_bridge(None)
    assert r["ok"] is False and not spawned
    assert "opencode 未找到" in r["steps"][0]["cmd"]


def test_resolve_opencode_windows_names(monkeypatch, tmp_path):
    """resolve_opencode 候选：数据根 bin 按 Windows 双文件名查找（.exe 优先）。

    注意不能 patch 真实 os.name（pathlib 按 os.name 分派 Path 类，全局改会
    把 PosixPath 变 WindowsPath 毒化所有路径判断）——用 stub 替换 _os。
    """
    import bridge.config as bc

    import os as _real_os

    class _OsNtStub:
        name = "nt"
        access = staticmethod(_real_os.access)
        X_OK = _real_os.X_OK

    monkeypatch.setattr(bc, "get", lambda key, default=None: "opencode")
    monkeypatch.setattr(bc._shutil, "which", lambda _: None)
    monkeypatch.setattr(bc, "_os", _OsNtStub)
    monkeypatch.setattr(bc, "WORK_ROOT", tmp_path)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    exe = bin_dir / "opencode.exe"
    exe.write_bytes(b"x")
    exe.chmod(0o755)
    found = bc.resolve_opencode()
    assert found is not None and found.endswith("opencode.exe")
