"""opencode_jobs 跨平台化测试：cron 解析、三平台定时器生成、supervisor 执行器。"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from bridge import opencode_jobs as oj


@pytest.fixture
def iso(monkeypatch, tmp_path):
    """隔离：scheduler 根 + systemd 目录 + home（launchd plist）到临时目录。"""
    monkeypatch.setenv("OPENCODE_SCHED_ROOT", str(tmp_path / "sched"))
    monkeypatch.setenv("OPENCODE_SYSTEMD_USER_DIR", str(tmp_path / "systemd-user"))
    monkeypatch.setattr(Path, "home", lambda: tmp_path / "home")
    return tmp_path


# ---------- cron 解析 ----------

@pytest.mark.parametrize("expr,expected", [
    ("0 8 * * *", {"minute": [0], "hour": [8], "dom": list(range(1, 32)), "month": list(range(1, 13)), "dow": list(range(0, 7))}),
    ("*/30 * * * *", {"minute": [0, 30], "hour": list(range(0, 24)), "dom": list(range(1, 32)), "month": list(range(1, 13)), "dow": list(range(0, 7))}),
    ("5,35 * * * *", {"minute": [5, 35], "hour": list(range(0, 24)), "dom": list(range(1, 32)), "month": list(range(1, 13)), "dow": list(range(0, 7))}),
    ("0 8 * * 1", {"minute": [0], "hour": [8], "dom": list(range(1, 32)), "month": list(range(1, 13)), "dow": [1]}),
    ("0 8 1 * *", {"minute": [0], "hour": [8], "dom": [1], "month": list(range(1, 13)), "dow": list(range(0, 7))}),
    ("15 1,3,5 * * *", {"minute": [15], "hour": [1, 3, 5], "dom": list(range(1, 32)), "month": list(range(1, 13)), "dow": list(range(0, 7))}),
    ("0-30 * * * *", {"minute": list(range(0, 31)), "hour": list(range(0, 24)), "dom": list(range(1, 32)), "month": list(range(1, 13)), "dow": list(range(0, 7))}),
])
def test_parse_cron_forms(expr, expected):
    assert oj._parse_cron(expr) == expected


@pytest.mark.parametrize("expr", ["0 8 1 * 1", "bad expr here", "0 8 * * * *", "61 * * * *"])
def test_parse_cron_rejects(expr):
    with pytest.raises(ValueError):
        oj._parse_cron(expr)


# ---------- launchd 适配器 ----------

def test_launchd_interval_daily():
    iv = oj._launchd_interval(oj._parse_cron("0 8 * * *"))
    assert iv == {"Minute": [0], "Hour": [8]}


def test_launchd_interval_weekly():
    iv = oj._launchd_interval(oj._parse_cron("0 8 * * 1"))
    assert iv == {"Minute": [0], "Hour": [8], "Weekday": [1]}


def test_launchd_interval_monthly():
    iv = oj._launchd_interval(oj._parse_cron("0 8 1 * *"))
    assert iv == {"Minute": [0], "Hour": [8], "Day": [1]}


def test_launchd_interval_every_minute():
    iv = oj._launchd_interval(oj._parse_cron("* * * * *"))
    assert set(iv["Minute"]) == set(range(0, 60))


# ---------- schtasks 适配器（拆任务） ----------

def test_schtasks_daily():
    plans = oj._schtasks_plans(oj._parse_cron("0 8 * * *"), "0 8 * * *")
    assert plans == [{"sc": "daily", "mo": None, "d": None, "m": None, "st": "08:00"}]


def test_schtasks_minute_step():
    plans = oj._schtasks_plans(oj._parse_cron("*/30 * * * *"), "*/30 * * * *")
    assert plans == [{"sc": "minute", "mo": "30", "d": None, "m": None, "st": "00:00"}]


def test_schtasks_list_splits_tasks():
    plans = oj._schtasks_plans(oj._parse_cron("5,35 * * * *"), "5,35 * * * *")
    assert len(plans) == 2
    assert plans[0]["st"] == "00:05" and plans[1]["st"] == "00:35"


def test_schtasks_weekly():
    plans = oj._schtasks_plans(oj._parse_cron("0 8 * * 1"), "0 8 * * 1")
    assert plans[0]["sc"] == "weekly" and plans[0]["d"] == "MO" and plans[0]["st"] == "08:00"


def test_schtasks_monthly():
    plans = oj._schtasks_plans(oj._parse_cron("0 8 1 * *"), "0 8 1 * *")
    assert plans[0]["sc"] == "monthly" and plans[0]["d"] == "1" and plans[0]["st"] == "08:00"


def test_schtasks_multi_hour_splits():
    plans = oj._schtasks_plans(oj._parse_cron("15 1,3,5 * * *"), "15 1,3,5 * * *")
    assert len(plans) == 3
    assert [p["st"] for p in plans] == ["01:15", "03:15", "05:15"]


def test_schtasks_every_minute():
    plans = oj._schtasks_plans(oj._parse_cron("* * * * *"), "* * * * *")
    assert plans == [{"sc": "minute", "mo": "1", "d": None, "m": None, "st": "00:00"}]


# ---------- install_job 三平台载体生成 ----------

def _fake_opencode(monkeypatch):
    monkeypatch.setattr(oj, "_program", lambda: "/fake/.venv/bin/python")
    monkeypatch.setattr("bridge.config.resolve_opencode",
                        lambda: "/fake/opencode")


def test_install_systemd(iso, monkeypatch):
    monkeypatch.setattr(oj, "_platform_kind", lambda: "linux")
    _fake_opencode(monkeypatch)
    r = oj.install_job("Planner", "morning-briefing", "0 8 * * *", "prompt-x", dry=True)
    assert r["ok"] and r["timers"] == ["opencode-job-Planner-morning-briefing.timer"]
    svc = iso / "systemd-user" / "opencode-job-Planner-morning-briefing.service"
    text = svc.read_text(encoding="utf-8")
    assert "ExecStart=/fake/.venv/bin/python -m bridge.opencode_jobs supervisor" in text
    assert "perl" not in text
    timer = iso / "systemd-user" / "opencode-job-Planner-morning-briefing.timer"
    assert "OnCalendar=*-*-* 08:00:00" in timer.read_text(encoding="utf-8")


def test_install_windows(iso, monkeypatch):
    monkeypatch.setattr(oj, "_platform_kind", lambda: "windows")
    _fake_opencode(monkeypatch)
    ran: list[list[str]] = []
    monkeypatch.setattr("bridge.opencode_jobs.subprocess.run",
                        lambda args, **kw: ran.append(args) or subprocess.CompletedProcess(args, 0))
    r = oj.install_job("Planner", "morning", "0 8 * * *", "prompt-x", dry=True)
    assert r["ok"] and r["timers"] == ["wechat-claw-job-Planner-morning"]
    assert ran == []  # dry 不执行 schtasks
    # 非 dry：schtasks 命令生成
    r2 = oj.install_job("Planner", "morning", "0 8 * * *", "prompt-x", dry=False)
    assert len(ran) == 1
    args = ran[0]
    assert args[0] == "schtasks" and "/sc" in args and "daily" in args and "/st" in args and "08:00" in args
    assert "wechat-claw-job-Planner-morning" in args
    # 工作目录包装：/tr 必须含 cmd /c + cd /d（schtasks 无 workdir 参数，防 CWD=System32 找不到 bridge）
    tr = args[args.index("/tr") + 1]
    assert tr.startswith("cmd /c ") and 'cd /d "' in tr and "bridge.opencode_jobs" in tr


def test_install_macos(iso, monkeypatch):
    monkeypatch.setattr(oj, "_platform_kind", lambda: "darwin")
    _fake_opencode(monkeypatch)
    r = oj.install_job("Planner", "morning", "0 8 * * *", "prompt-x", dry=True)
    plist = iso / "home" / "Library" / "LaunchAgents" / "com.wechat-claw.job.Planner-morning.plist"
    import plistlib
    data = plistlib.loads(plist.read_bytes())
    assert data["Label"] == "com.wechat-claw.job.Planner-morning"
    assert data["ProgramArguments"][-1].endswith("morning.json")
    assert data["StartCalendarInterval"] == {"Minute": [0], "Hour": [8]}


def test_install_rejects_dom_dow(iso, monkeypatch):
    monkeypatch.setattr(oj, "_platform_kind", lambda: "linux")
    _fake_opencode(monkeypatch)
    with pytest.raises(ValueError):
        oj.install_job("Planner", "weird", "0 8 1 * 1", "prompt-x", dry=True)
    assert not list((iso / "sched" / "scopes" / "wechat-claw" / "jobs").glob("*.json"))


# ---------- supervisor 执行器 ----------

def _mk_job(tmp_path, command=None, args=None, env=None):
    root = tmp_path / "sched"
    jd = root / "scopes" / "wechat-claw" / "jobs"
    jd.mkdir(parents=True, exist_ok=True)
    job = {
        "name": "t", "slug": "Mod-t", "scopeId": "wechat-claw", "schedule": "0 8 * * *",
        "timeoutSeconds": 30, "workdir": str(tmp_path), "source": "module", "module": "Mod",
        "run": {"title": "Mod-t", "prompt": "p"},
        "invocation": {"command": command if command is not None else sys.executable, "args": args if args is not None else ["-c", "print('hi')"]},
        "env": env or {},
    }
    jp = jd / "Mod-t.json"
    jp.write_text(json.dumps(job, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return jp


def test_supervisor_exec_ok(iso, monkeypatch):
    jp = _mk_job(iso)
    rc = oj._supervisor_exec(str(jp))
    assert rc == 0
    state = json.loads(jp.read_text(encoding="utf-8"))
    assert state["lastRunStatus"] == "ok" and state["lastRunExitCode"] == 0
    logf = iso / "logs" / "scheduler" / "wechat-claw" / "Mod-t.log"
    assert "finish runId=" in logf.read_text(encoding="utf-8")


def test_supervisor_exec_skip_when_running(iso, monkeypatch):
    jp = _mk_job(iso)
    locks = iso / "sched" / "scopes" / "wechat-claw" / "locks"
    locks.mkdir(parents=True, exist_ok=True)
    (locks / "Mod-t.json").write_text(json.dumps({"pid": os.getpid(), "startedAt": "x", "runId": "y"}),
                                      encoding="utf-8")
    rc = oj._supervisor_exec(str(jp))
    assert rc == 0  # 跳过执行
    state = json.loads(jp.read_text(encoding="utf-8"))
    assert "lastRunStatus" not in state  # 未执行未写状态


def test_supervisor_exec_missing_command(iso, monkeypatch):
    jp = _mk_job(iso, command="", args=[])
    rc = oj._supervisor_exec(str(jp))
    assert rc == 1
    state = json.loads(jp.read_text(encoding="utf-8"))
    assert state["lastRunStatus"] == "failed" and "missing invocation" in state["lastRunError"]
