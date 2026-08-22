"""service_up 自启动/提权测试：管理员检测、用户级自启、状态检测、UAC 提权（全部 dry-run/mock）。"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import web.handlers.service_up as su


@pytest.fixture(autouse=True)
def _dry(monkeypatch):
    """强制 dry-run + Windows 平台分支：不写真实文件、不执行真实命令（UAC/reg/sc 全部 mock）。"""
    monkeypatch.setattr(su, "SELFTEST", True)
    monkeypatch.setattr(su, "_is_admin", lambda: True)  # 各测试自行覆盖
    monkeypatch.setattr(su.os, "name", "nt")  # 测试 Windows 分支（本机 Linux）


class FakeProc:
    def __init__(self, rc=0, out="", err=""):
        self.returncode = rc
        self.stdout = out
        self.stderr = err


def _mk_run(monkeypatch, mapping):
    """mock subprocess.run：按命令首段路由（sc/reg/systemctl/launchctl）。"""
    def fake_run(cmd, *a, **kw):
        key = cmd[0]
        out = mapping.get(key, FakeProc(rc=1))
        return out if isinstance(out, FakeProc) else FakeProc(**out)
    monkeypatch.setattr("web.handlers.service_up.subprocess.run", fake_run)


# ---------- 管理员检测双路径 ----------

def test_service_up_windows_non_admin_user_autostart(monkeypatch):
    monkeypatch.setattr(su, "_is_admin", lambda: False)
    steps = su._service_up_windows()
    assert all(s["ok"] for s in steps)
    cmds = " ".join(s["cmd"] for s in steps)
    assert "reg" in cmds and "Run" in cmds and "wechat-claw-bridge" in cmds  # HKCU Run 键
    assert "UAC" in cmds  # 说明文字提示允许 UAC


def test_service_up_windows_admin_nssm(monkeypatch):
    monkeypatch.setattr(su, "_is_admin", lambda: True)
    steps = su._service_up_windows()
    cmds = " ".join(s["cmd"] for s in steps)
    assert "nssm" in cmds and "install" in cmds  # 管理员 → 系统服务


# ---------- 用户级自启（HKCU Run + VBS） ----------

def test_user_autostart_reg_command_shape():
    steps = su._user_autostart_reg()
    cmds = [s["cmd"] for s in steps]
    reg = next(c for c in cmds if "reg add" in c)
    assert 'wscript.exe' in reg and "wechat-claw-bridge.vbs" in reg  # Run 键指向无窗口 VBS
    assert "CurrentVersion\\Run" in reg  # HKCU Run 键


def test_user_autostart_unreg():
    steps = su._user_autostart_unreg()
    assert any("reg delete" in s["cmd"] for s in steps)


# ---------- 状态检测 ----------

def test_autostart_status_system(monkeypatch):
    _mk_run(monkeypatch, {"sc": {"rc": 0, "out": "STATE              : 4  RUNNING"}})
    st = su.autostart_status()
    assert st["mode"] == "system"


def test_autostart_status_user(monkeypatch):
    _mk_run(monkeypatch, {"sc": {"rc": 1}, "reg": {"rc": 0, "out": "wechat-claw-bridge"}})
    st = su.autostart_status()
    assert st["mode"] == "user"


def test_autostart_status_none(monkeypatch):
    _mk_run(monkeypatch, {"sc": {"rc": 1}, "reg": {"rc": 1}})
    st = su.autostart_status()
    assert st["mode"] == "none"


# ---------- UAC 提权与开关 ----------

def test_uac_elevate_selftest_dry(monkeypatch):
    monkeypatch.setattr(su, "_is_admin", lambda: False)
    steps = su._uac_elevate(True)
    assert all(s["ok"] for s in steps)
    assert all(s.get("dry") for s in steps)  # selftest 下不真实弹 UAC


def test_autostart_set_on_non_admin_uac(monkeypatch):
    monkeypatch.setattr(su, "_is_admin", lambda: False)
    r = su.autostart_set(True)
    assert r["ok"] and r["uac_required"] is True
    cmds = " ".join(s["cmd"] for s in r["steps"])
    assert "UAC" in cmds  # 提权步骤存在


def test_autostart_set_off_non_admin(monkeypatch):
    monkeypatch.setattr(su, "_is_admin", lambda: False)
    r = su.autostart_set(False)
    assert r["ok"] and r["uac_required"] is True
    cmds = " ".join(s["cmd"] for s in r["steps"])
    assert "reg delete" in cmds or "UAC" in cmds


def test_autostart_set_on_admin_direct(monkeypatch):
    monkeypatch.setattr(su, "_is_admin", lambda: True)
    r = su.autostart_set(True)
    assert r["ok"] and r["uac_required"] is False
    assert any("nssm" in s["cmd"] for s in r["steps"])
