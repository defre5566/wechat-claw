"""bridge/jobs.py 渲染与联动测试（job.template + settings → job json / prompt 合成 / 自动登记）。

隔离：monkeypatch jobs.MODULES_DIR/DATA_ROOT 到临时目录；crypto.decrypt / opencode_jobs 打桩。
"""
from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from bridge import jobs


def _mk_module(tmp_path, name="Planner", settings=None, directions=None):
    """构造隔离模块（代码目录 + 数据区 + 基底/directions/job 模板）。"""
    mod_dir = tmp_path / "modules" / name
    data_dir = tmp_path / "modules" / "modules_data" / name
    (mod_dir / "prompts").mkdir(parents=True)
    (data_dir / "prompts" / "custom").mkdir(parents=True)
    (mod_dir / "module.json").write_text(json.dumps({
        "name": name,
        "job_template": "job.template.json",
        "schedule_from_settings": [
            {"phase": "morning", "time_field": "morning_time", "enabled_field": "planner_on"},
        ],
    }), encoding="utf-8")
    (mod_dir / "job.template.json").write_text(json.dumps({
        "name": "早报简报", "title": "早报简报", "slug": "morning-briefing",
        "phase": "morning", "offset_min": 5, "timeoutSeconds": 1800, "output_dir": "briefing",
    }), encoding="utf-8")
    (data_dir / "settings.json").write_text(json.dumps(settings or {
        "planner_on": True, "morning_time": "08:30", "briefing_topics": ["时政"],
    }), encoding="utf-8")
    (mod_dir / "prompts" / "base.prompt.enc").write_text("ENC", encoding="utf-8")
    (mod_dir / "directions.json").write_text(json.dumps(directions or {
        "时政": {
            "keywords": ["高层人事", "政策发布"], "word_limit": 1000,
            "categories": ["高层人事", "政策经济", "社会民生", "国际"],
        },
    }), encoding="utf-8")
    return mod_dir, data_dir


def _setup(tmp_path, monkeypatch):
    monkeypatch.setattr(jobs, "MODULES_DIR", tmp_path / "modules")
    monkeypatch.setattr(jobs, "DATA_ROOT", tmp_path / "modules" / "modules_data")
    monkeypatch.setattr("modules.common.crypto.decrypt", lambda s: "【基底】{date}/{word_limit}/{categories}")


# ---------- _cron_minus ----------

def test_cron_minus():
    assert jobs._cron_minus("30 8 * * *", 5) == "25 8 * * *"
    assert jobs._cron_minus("0 0 * * *", 5) == "55 23 * * *"   # 跨天回绕
    assert jobs._cron_minus("0 21 * * *", 0) == "0 21 * * *"
    assert jobs._cron_minus("bad", 5) is None


# ---------- render_job ----------

def test_render_job_prompt_placeholders(tmp_path, monkeypatch):
    _mk_module(tmp_path)
    _setup(tmp_path, monkeypatch)
    r = jobs.render_job("Planner")
    assert r["ok"], r
    job = r["job"]
    assert job["schedule"] == "25 8 * * *"                       # 08:30 - 5min
    assert str(date.today().year) in job["prompt"]               # {date}
    assert "1000" in job["prompt"]                               # {word_limit}
    assert "高层人事" in job["prompt"]                            # {categories} 注入
    assert "时政：高层人事、政策发布" in job["prompt"]             # 方向关键词
    assert job["slug"] == "morning-briefing"
    assert job["timeoutSeconds"] == 1800
    assert job["output_dir"].endswith("modules_data/Planner/briefing")


def test_render_job_custom_prompt(tmp_path, monkeypatch):
    _, data_dir = _mk_module(tmp_path, settings={
        "planner_on": True, "morning_time": "08:30",
        "briefing_topics": ["时政", "自定义A"],
    })
    (data_dir / "prompts" / "custom" / "自定义A.json").write_text(
        json.dumps({"prompt": "侧重科技与产业"}), encoding="utf-8")
    _setup(tmp_path, monkeypatch)
    r = jobs.render_job("Planner")
    assert r["ok"], r
    assert "用户自定义方向 自定义A" in r["job"]["prompt"]
    assert "侧重科技与产业" in r["job"]["prompt"]


def test_render_job_no_template(tmp_path, monkeypatch):
    (tmp_path / "modules" / "plain").mkdir(parents=True)
    (tmp_path / "modules" / "plain" / "module.json").write_text(
        json.dumps({"name": "plain"}), encoding="utf-8")
    _setup(tmp_path, monkeypatch)
    r = jobs.render_job("plain")
    assert not r["ok"] and "无 job.template.json" in r["error"]


def test_render_job_decrypt_failure_degrades(tmp_path, monkeypatch):
    """基底解密失败 → 降级文案，不抛异常。"""
    _mk_module(tmp_path)
    _setup(tmp_path, monkeypatch)
    monkeypatch.setattr("modules.common.crypto.decrypt", lambda s: (_ for _ in ()).throw(RuntimeError("密钥缺失")))
    r = jobs.render_job("Planner")
    assert r["ok"]
    assert "基底 prompt 解密失败" in r["job"]["prompt"]


# ---------- sync_module_jobs（register 联动入口） ----------

def test_sync_module_jobs_installs(tmp_path, monkeypatch):
    _mk_module(tmp_path)
    _setup(tmp_path, monkeypatch)
    calls = {}

    def fake_install(**kw):
        calls.update(kw)
        return {"ok": True, "slug": "Planner-morning-briefing", "on_calendar": "*-*-* 08:25:00"}

    monkeypatch.setattr("bridge.opencode_jobs.install_job", fake_install)
    r = jobs.sync_module_jobs("Planner")
    assert r["ok"] and not r.get("skipped")
    assert calls["module"] == "Planner"
    assert calls["schedule"] == "25 8 * * *"
    assert "时政" in calls["prompt"]


def test_sync_module_jobs_unregisters_when_disabled(tmp_path, monkeypatch):
    """planner_on=false → 注销 job（不登记）。"""
    _mk_module(tmp_path, settings={"planner_on": False, "morning_time": "08:30", "briefing_topics": ["时政"]})
    _setup(tmp_path, monkeypatch)
    calls = {}
    installed = []

    def fake_uninstall(module):
        calls["module"] = module
        return {"ok": True, "removed": ["Planner-morning-briefing"]}

    monkeypatch.setattr("bridge.opencode_jobs.install_job", lambda **kw: installed.append(kw) or {"ok": True})
    monkeypatch.setattr("bridge.opencode_jobs.uninstall_job", fake_uninstall)
    r = jobs.sync_module_jobs("Planner")
    assert r["ok"]
    assert calls["module"] == "Planner"
    assert installed == []                                      # 未登记


def test_sync_module_jobs_skips_without_template(tmp_path, monkeypatch):
    (tmp_path / "modules" / "plain").mkdir(parents=True)
    (tmp_path / "modules" / "plain" / "module.json").write_text(
        json.dumps({"name": "plain"}), encoding="utf-8")
    _setup(tmp_path, monkeypatch)
    r = jobs.sync_module_jobs("plain")
    assert r == {"ok": True, "skipped": True}


def test_sync_module_jobs_registration_failure_reported(tmp_path, monkeypatch):
    """登记失败 → 明确报错（不静默降级）。"""
    _mk_module(tmp_path)
    _setup(tmp_path, monkeypatch)
    monkeypatch.setattr("bridge.opencode_jobs.install_job",
                        lambda **kw: (_ for _ in ()).throw(ValueError("schedule 无法转 OnCalendar")))
    r = jobs.sync_module_jobs("Planner")
    assert not r["ok"]
    assert "登记失败" in r["error"]


def test_unregister_jobs(tmp_path, monkeypatch):
    calls = {}

    def fake_uninstall(module):
        calls["module"] = module
        return {"ok": True, "removed": []}

    monkeypatch.setattr("bridge.opencode_jobs.uninstall_job", fake_uninstall)
    r = jobs.unregister_jobs("Planner")
    assert r["ok"] and calls["module"] == "Planner"
