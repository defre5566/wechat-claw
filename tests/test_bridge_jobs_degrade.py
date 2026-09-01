"""批次 C（260830）：#11 自适应降级 + 告警三层 + job_registered 三态。

- install_job：平台载体 OSError → carrier=bridge 降级（job.json 标记 + 告警事件）
- scheduler._agent_job_check：跨越检测 / last_run 防重 / Persistent 补跑 / spawn supervisor
- 告警：_push_wechat_alert 微信直投 + 每模块每事件每日去重
- jobs.job_registered：三态（systemd / bridge 降级 / 未登记）
"""
from __future__ import annotations

import asyncio
import json
import types
from datetime import datetime, timedelta
from pathlib import Path

import pytest


# ---------- fixtures ----------

@pytest.fixture
def env(tmp_path, monkeypatch):
    """隔离数据根 / sched_root / systemd 目录 + 告警队列收集器。"""
    import bridge.config as cfg
    import bridge.opencode_jobs as oj
    import bridge.scheduler as sch

    data = tmp_path / "data"
    data.mkdir()
    sched_root = tmp_path / "sched"
    monkeypatch.setenv("OPENCODE_SCHED_ROOT", str(sched_root))
    monkeypatch.setenv("OPENCODE_SYSTEMD_USER_DIR", str(tmp_path / "systemd"))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setattr(cfg, "DATA_ROOT", data)
    monkeypatch.setattr(cfg, "WORK_ROOT", data)
    monkeypatch.setattr(oj, "DATA_ROOT", data, raising=False)

    sent: list[dict] = []

    class FakeQueue:
        async def put(self, item):
            sent.append(item)

    sch.set_push_queue(FakeQueue())
    sch._worker_missing_alerted.clear()
    return types.SimpleNamespace(
        data=data, sched_root=sched_root, sent=sent,
        jobs_dir=sched_root / "scopes" / "wechat-claw" / "jobs",
        monkeypatch=monkeypatch,
    )


def _write_job(env, slug="Planner-morning-briefing", module="Planner", carrier="bridge",
               cron="30 7 * * *", last_run=None):
    env.jobs_dir.mkdir(parents=True, exist_ok=True)
    data = {"slug": slug, "module": module, "schedule": cron,
            "timeoutSeconds": 60, "carrier": carrier}
    if last_run:
        data["last_run"] = last_run
    jp = env.jobs_dir / f"{slug}.json"
    jp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return jp


# ---------- install_job 降级 ----------

def test_install_job_degrades_on_readonly_systemd(tmp_path, monkeypatch):
    """platform 载体 OSError（Errno 30 只读等）→ carrier=bridge + job.json 标记。

    平台适配器按 _platform_kind()：Windows _install_windows_timers /
    macOS _install_launchd / Linux _install_systemd（各自抛 OSError 都应降级）。
    """
    import bridge.opencode_jobs as oj
    monkeypatch.setattr("bridge.config.resolve_opencode", lambda: "/usr/bin/opencode")
    kinds = {"windows": "_install_windows_timers", "darwin": "_install_launchd"}
    target = kinds.get(oj._platform_kind(), "_install_systemd")
    monkeypatch.setattr(oj, target,
                        lambda *a, **k: (_ for _ in ()).throw(OSError(30, "Read-only file system")))
    monkeypatch.setattr(oj, "jobs_dir", lambda: tmp_path / "sched-jobs")
    (tmp_path / "sched-jobs").mkdir(parents=True)

    r = oj.install_job("Planner", "早报简报", "30 7 * * *", prompt="p", dry=False)
    assert r["ok"] is True and r["carrier"] == "bridge" and "Read-only" in r["degraded_reason"]
    jp = tmp_path / "sched-jobs" / f"{r['slug']}.json"
    assert json.loads(jp.read_text(encoding="utf-8"))["carrier"] == "bridge"


def test_install_job_config_error_still_raises(tmp_path, monkeypatch):
    """cron 配置错不降级（降级也跑不了）。"""
    import bridge.opencode_jobs as oj
    monkeypatch.setattr("bridge.config.resolve_opencode", lambda: "/usr/bin/opencode")
    monkeypatch.setattr(oj, "jobs_dir", lambda: tmp_path / "sched-jobs2")
    (tmp_path / "sched-jobs2").mkdir(parents=True)
    with pytest.raises(ValueError, match="cron 表达式无效"):
        oj.install_job("Planner", "早报简报", "bad cron expr", prompt="p", dry=False)


# ---------- _agent_job_check：触发/防重/补发 ----------

def _now_for(cron_hour, cron_min):
    """返回一个恰好命中 cron 的 now（今天）。"""
    now = datetime.now().replace(hour=cron_hour, minute=cron_min, second=10, microsecond=0)
    return now


def test_agent_job_check_fires_and_dedups(env):
    import bridge.scheduler as sch
    jp = _write_job(env, cron="30 7 * * *")
    now = _now_for(7, 30)
    asyncio.run(sch._agent_job_check(now))
    assert json.loads(jp.read_text(encoding="utf-8"))["last_run"] == now.date().isoformat()
    assert any("已由 bridge 内置调度触发" in s["text"] for s in env.sent)

    # 同日第二次到点：不重复执行（last_run 防重），无新增触发告警
    n_before = len([s for s in env.sent if "已由 bridge 内置调度触发" in s["text"]])
    asyncio.run(sch._agent_job_check(now.replace(minute=31)))
    assert len([s for s in env.sent if "已由 bridge 内置调度触发" in s["text"]]) == n_before


def test_agent_job_check_skips_platform_carrier(env):
    import bridge.scheduler as sch
    jp = _write_job(env, carrier="systemd")
    now = _now_for(7, 30)
    asyncio.run(sch._agent_job_check(now))
    assert "last_run" not in json.loads(jp.read_text(encoding="utf-8"))


def test_agent_job_check_persistent_catchup(env):
    import bridge.scheduler as sch
    jp = _write_job(env, cron="30 7 * * *", last_run=(datetime.now() - timedelta(days=1)).date().isoformat())
    now = datetime.now().replace(hour=20, minute=0)  # 今天 7:30 已过、last_run 是昨天 → 补跑
    asyncio.run(sch._agent_job_check(now))
    assert json.loads(jp.read_text(encoding="utf-8"))["last_run"] == now.date().isoformat()


# ---------- 告警：微信直投 + 每日去重 ----------

def test_worker_missing_alert_goes_to_wechat_queue_once(env):
    import bridge.scheduler as sch
    asyncio.run(sch._worker_missing_alert("Ghost"))
    asyncio.run(sch._worker_missing_alert("Ghost"))
    hits = [s for s in env.sent if "worker 脚本缺失" in s["text"]]
    assert len(hits) == 1 and hits[0]["text"].startswith("[引擎告警]")


def test_push_queue_none_falls_back_silently(tmp_path, monkeypatch):
    import bridge.scheduler as sch
    sch.set_push_queue(None)
    asyncio.run(sch._worker_missing_alert("Ghost"))  # 不抛（只写事件日志）


# ---------- job_registered 三态 ----------

def test_job_registered_three_states(env, monkeypatch):
    import bridge.jobs as jobs
    monkeypatch.setattr("bridge.config.DATA_ROOT", env.data, raising=False)
    monkeypatch.setattr(jobs, "DATA_ROOT", env.data, raising=False)

    # 未登记
    assert jobs.job_registered("NoJob")[0] is False

    # 降级态
    jp = _write_job(env, slug="Planner-morning-briefing", module="Planner", carrier="bridge")
    ok, why = jobs.job_registered("Planner")
    assert ok and "bridge" in why

    # systemd 态
    (env.jobs_dir / f"{jp.stem}.json").write_text(
        json.dumps({"slug": jp.stem, "module": "Planner", "schedule": "30 7 * * *", "carrier": "systemd"}),
        encoding="utf-8")
    ok2, why2 = jobs.job_registered("Planner")
    assert ok2 and "bridge" not in why2
