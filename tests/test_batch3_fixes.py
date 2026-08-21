"""批 3 回归：节假日数据（F3.1）、向导 Job 环形缓冲与超时（F4.4/F3.3）。"""
from __future__ import annotations

from datetime import date

import pytest


# ---------- F3.1 节假日 ----------

def test_holidays_current_year_populated():
    """当年（2026）必须非空且含法定假日；调休补班日不算假期。"""
    from modules.common.holidays import HOLIDAYS, is_holiday
    assert HOLIDAYS, "节假日表为空（每年需更新！）"
    assert "2026-10-01" in HOLIDAYS
    assert is_holiday(date(2026, 10, 1)) == "国庆节"
    assert is_holiday(date(2026, 2, 17)) == "春节"
    assert is_holiday(date(2026, 1, 4)) is None  # 元旦调休补班日
    assert is_holiday(date(2026, 2, 14)) is None  # 春节调休补班日


def test_holidays_known_dates():
    from modules.common.holidays import is_holiday
    assert is_holiday(date(2026, 5, 1)) == "劳动节"
    assert is_holiday(date(2026, 6, 20)) == "端午节"
    assert is_holiday(date(2026, 9, 26)) == "中秋节"
    assert is_holiday(date(2026, 10, 7)) == "国庆节"


# ---------- F4.4 / F3.3 向导 Job ----------

def test_job_lines_ring_buffer():
    from web.wizard import JOB_LINES_LIMIT, Job
    j = Job("t", [])
    for i in range(2600):
        j._add_line(f"line-{i}")
    assert len(j.lines) <= JOB_LINES_LIMIT
    snap = j.snapshot()
    assert any("截断" in ln for ln in snap["lines"])
    assert "line-2599" in j.lines[-1]


def test_job_watchdog_kills_hung_step(monkeypatch):
    import web.wizard as wz
    monkeypatch.setattr(wz, "JOB_TIMEOUT_SECONDS", 1)
    j = wz.Job("t2", [["sleep", "5"]])
    j.start()
    j.thread.join(timeout=10)
    assert j.done and not j.ok
    assert any("超时" in ln for ln in j.lines)
    assert any("失败" in ln for ln in j.lines)