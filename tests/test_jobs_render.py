"""bridge/jobs.py 渲染与联动测试（通用 job 引擎：prompt 必填 + 占位符 + 无业务预设）。

隔离：monkeypatch jobs.MODULES_DIR/DATA_ROOT 到临时目录；opencode_jobs 打桩。
"""
from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from bridge import jobs  # noqa: E402


def _mk_module(tmp_path, name="Planner", settings=None, template=None, module_json=None):
    """构造隔离模块（代码目录 + 数据区 + job 模板）。"""
    mod_dir = tmp_path / "modules" / name
    data_dir = tmp_path / "modules" / "modules_data" / name
    mod_dir.mkdir(parents=True, exist_ok=True)
    (data_dir / "prompts" / "custom").mkdir(parents=True)
    (mod_dir / "module.json").write_text(json.dumps(module_json or {
        "name": name,
        "job_template": "job.template.json",
        "schedule_from_settings": [
            {"phase": "morning", "time_field": "morning_time", "enabled_field": "planner_on"},
        ],
    }), encoding="utf-8")
    (mod_dir / "job.template.json").write_text(json.dumps(template or {
        "name": "早报简报", "title": "早报简报", "slug": "morning-briefing",
        "phase": "morning", "offset_min": 5, "timeoutSeconds": 1800, "output_dir": "briefing",
        "prompt": "生成一份信息简报，写入 {output_path}。日期：{date}。方向：{settings:briefing_topics}。自定义：{custom_prompts}",
    }), encoding="utf-8")
    (data_dir / "settings.json").write_text(json.dumps(settings or {
        "planner_on": True, "morning_time": "08:30", "briefing_topics": ["时政", "经济"],
    }), encoding="utf-8")
    return mod_dir, data_dir


def _setup(tmp_path, monkeypatch):
    monkeypatch.setattr(jobs, "MODULES_DIR", tmp_path / "modules")
    monkeypatch.setattr(jobs, "DATA_ROOT", tmp_path / "modules" / "modules_data")


# ---------- _cron_minus ----------

def test_cron_minus():
    assert jobs._cron_minus("30 8 * * *", 5) == "25 8 * * *"
    assert jobs._cron_minus("0 0 * * *", 5) == "55 23 * * *"   # 跨天回绕
    assert jobs._cron_minus("0 21 * * *", 0) == "0 21 * * *"
    assert jobs._cron_minus("bad", 5) is None


# ---------- render_job（通用引擎） ----------

def test_render_job_prompt_placeholders(tmp_path, monkeypatch):
    _mk_module(tmp_path)
    _setup(tmp_path, monkeypatch)
    r = jobs.render_job("Planner")
    assert r["ok"], r
    job = r["job"]
    assert job["schedule"] == "25 8 * * *"                       # 08:30 - 5min
    assert str(date.today().year) in job["prompt"]               # {date}
    assert "时政、经济" in job["prompt"]                          # {settings:briefing_topics}（列表顿号连接）
    assert "briefing" in job["prompt"] and str(Path(job["output_dir"])) in job["prompt"]  # {output_path}
    assert job["slug"] == "morning-briefing"
    assert job["timeoutSeconds"] == 1800
    assert Path(job["output_dir"]).parts[-3:] == ("modules_data", "Planner", "briefing")


def test_render_job_no_prompt_rejected(tmp_path, monkeypatch):
    """缺 prompt 字段 → 拒绝登记（模块作者必须写清任务指示，无备胎）。"""
    _mk_module(tmp_path, template={
        "name": "x", "slug": "x", "phase": "morning",
        "fallback_prompt": "旧备胎字段应被忽略",
    })
    _setup(tmp_path, monkeypatch)
    r = jobs.render_job("Planner")
    assert not r["ok"]
    assert "缺少 prompt 字段" in r["error"]


def test_render_job_custom_prompts_via_placeholder(tmp_path, monkeypatch):
    """{custom_prompts} 占位符：web 导入的自定义 prompt 注入（通用用户数据）。"""
    _, data_dir = _mk_module(tmp_path)
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
    assert "时政、经济" in calls["prompt"]


def test_sync_module_jobs_unregisters_when_disabled(tmp_path, monkeypatch):
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