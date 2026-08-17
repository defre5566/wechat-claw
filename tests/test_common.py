"""common 纯函数最小回归集（URA 第三轮 #5 补建）。

覆盖：调度规则判定器（cron_match / every_interval / _rand_offset）、
任务解析（parse_task_line / trigger_time）、防重 IO（_keep_key）、
N2 回归（sort_due_key 混合 time/date 任务不抛 TypeError）。
"""
from __future__ import annotations

import sys
from datetime import date, datetime, time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from modules.common.io import _keep_key, _keep_ts_key, prune_state_file
from modules.common.task import ParsedTask, parse_task_line, sort_due_key


# ---------- cron_match ----------

def test_cron_match_basic():
    from bridge.scheduler import cron_match
    now = datetime(2026, 8, 17, 8, 29)
    assert cron_match("29 8 * * *", now)
    assert not cron_match("30 8 * * *", now)
    assert cron_match("* * * * *", now)


def test_cron_match_invalid_expr_safe():
    """非法表达式返回 False 不抛异常（N10 回归）。"""
    from bridge.scheduler import cron_match
    now = datetime(2026, 8, 17, 8, 29)
    assert cron_match("*/5 * * * *", now) is False
    assert cron_match("1-5 * * * *", now) is False
    assert cron_match("a b c d e", now) is False


# ---------- every_interval ----------

def test_every_interval():
    from bridge.scheduler import every_interval
    assert every_interval("1m") == 60
    assert every_interval("5m") == 300
    assert every_interval("1h") == 3600
    assert every_interval("30s") == 30


# ---------- _rand_offset ----------

def test_rand_offset_within_bounds():
    from bridge.scheduler import _rand_offset
    for _ in range(100):
        v = _rand_offset("random-5-50")
        assert 5 <= v <= 50
    assert _rand_offset("fixed") == 0
    assert _rand_offset("random-x") == 0


# ---------- parse_task_line / trigger_time ----------

def test_parse_task_line_fields():
    t = parse_task_line("- [ ] 买菜 📅 2026-08-17 ⏰ 09:30 🔔提前15分钟 #todo/生活 🔼")
    assert t is not None
    assert t.due == date(2026, 8, 17)
    assert t.time == time(9, 30)
    assert t.remind_min == 15
    assert t.tags == ["生活"]
    assert t.priority == 3  # 🔼 = 3
    assert t.trigger_time == time(9, 15)  # ⏰ 减去提前量


def test_parse_task_line_no_time():
    t = parse_task_line("- [ ] 写周报 📅 2026-08-17")
    assert t is not None
    assert t.time is None
    assert t.trigger_time is None


def test_parse_task_line_non_task():
    assert parse_task_line("普通文本行") is None
    assert parse_task_line("## 标题") is None


# ---------- sort_due_key（N2 回归） ----------

def test_sort_due_key_mixed_time_and_date():
    """混合有/无 ⏰ 的今日任务排序不抛 TypeError，无 ⏰ 排最后。"""
    with_time = ParsedTask(raw_line="", text="有时刻", due=date(2026, 8, 17), time=time(10, 0))
    no_time = ParsedTask(raw_line="", text="无时刻", due=date(2026, 8, 17), priority=1)
    late_time = ParsedTask(raw_line="", text="晚时刻", due=date(2026, 8, 17), time=time(18, 0))
    items = [no_time, late_time, with_time]
    ordered = sorted(items, key=sort_due_key)
    assert ordered[0] is with_time
    assert ordered[1] is late_time
    assert ordered[2] is no_time


def test_sort_due_key_priority_within_same_time():
    low = ParsedTask(raw_line="", text="低", due=date(2026, 8, 17), priority=5)
    high = ParsedTask(raw_line="", text="高", due=date(2026, 8, 17), priority=1)
    ordered = sorted([low, high], key=sort_due_key)
    assert ordered[0] is high


# ---------- _keep_key ----------

def test_keep_key_date_prefix():
    assert _keep_key("2026-08-17-08", "2026-07-18")
    assert not _keep_key("2026-07-01-10", "2026-07-18")


def test_keep_key_off_cache():
    assert _keep_key("2026-08-17-08_off", "2026-07-18")


def test_keep_ts_key_old_ts_expires():
    """非日期键：值为 epoch 时间戳且早于 cutoff → 过期删除（#9 回归）。"""
    old_value = 1786885320.0  # 08-16 的时间戳
    future_cutoff = old_value + 31 * 86400  # cutoff 在值之后 → 删
    past_cutoff = old_value - 86400  # cutoff 在值之前 → 保留
    assert not _keep_ts_key(old_value, future_cutoff)
    assert _keep_ts_key(old_value, past_cutoff)


def test_keep_ts_key_active_and_non_ts_kept():
    """last_ts（值实时）、_off（分钟数）、字符串值 → 永不过期。"""
    now_ts = 1786947780.0
    assert _keep_ts_key(now_ts, now_ts - 86400)  # last_ts 值最新 → 保留
    assert _keep_ts_key(36, now_ts)  # _off 分钟数（<1e9）→ 保留
    assert _keep_ts_key("2026-08-17", now_ts)  # 字符串 → 保留


def test_prune_state_file_keeps_active_last_ts(tmp_path):
    """值近期的 ts 键保留（30 天后自然过期，见 test_keep_ts_key_old_ts_expires）。"""
    import json
    p = tmp_path / "state.json"
    p.write_text(json.dumps({
        "todo": {"ts": 1786885320.504601, "last_ts": 1786947780.519226},
        "planner": {"morning": "2026-08-17"},
    }))
    prune_state_file(p)
    data = json.loads(p.read_text())
    assert data["todo"]["last_ts"] == 1786947780.519226
    assert data["todo"]["ts"] == 1786885320.504601  # 值未过期 → 保留
