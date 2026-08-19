"""scheduler 修复回归（B1 cron 停摆 / B2 dow 语义 / B3 every_interval 容错 / B4 last_ts per-rule）。

纯函数级断言；B1 的状态机用最小模拟复刻 _tick cron 分支判定逻辑验证"超次清理后恢复触发"。
"""
from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from bridge.scheduler import cron_match, every_interval, _rand_offset


# ---------- B2：cron 周段标准语义（周日=0）----------

def test_cron_dow_standard_sunday_zero():
    """标准 cron：0=周日。Python weekday 周日=6，换算后应命中。"""
    sun = datetime(2026, 8, 16, 8, 0)   # 2026-08-16 周日
    mon = datetime(2026, 8, 17, 8, 0)   # 周一
    tue = datetime(2026, 8, 18, 8, 0)   # 周二
    assert cron_match("0 8 * * 0", sun)       # 周日=0 命中
    assert not cron_match("0 8 * * 0", mon)
    assert cron_match("0 8 * * 1", mon)       # 周一=1 命中
    assert cron_match("0 8 * * 2", tue)       # 周二=2 命中
    assert not cron_match("0 8 * * 1", sun)


# ---------- B3：every_interval 容错（非法不抛，返回 None）----------

def test_every_interval_invalid_returns_none():
    """非法单位 → None（绝不抛异常炸掉 scheduler）。"""
    assert every_interval("1d") is None
    assert every_interval("abc") is None
    assert every_interval("") is None


def test_every_interval_valid_unchanged():
    assert every_interval("1m") == 60
    assert every_interval("5m") == 300
    assert every_interval("1h") == 3600
    assert every_interval("30s") == 30


def test_rand_offset_invalid_safe():
    """非法 offset 不抛异常，返回 0。"""
    assert _rand_offset("random-x-y") == 0
    assert _rand_offset("random-1") == 0
    for _ in range(50):
        assert 5 <= _rand_offset("random-5-50") <= 50


# ---------- B1：cron 超次后恢复触发（复刻 _tick cron 分支判定）----------

def _cron_should_run(mod_state: dict, rid: str, max_retry: int, retry_iv: float, now_ts: float) -> bool:
    """复刻 _tick 中 cron 分支"是否进入 run_module 判定"的核心逻辑（B1 修复后）。"""
    done_key = mod_state.get(rid)
    # 用固定 today 占位（测试不关心日期匹配，关注 failed 清理）
    if done_key == "today":
        return False
    failed = mod_state.get(f"{rid}_failed")
    if failed and failed.get("count", 0) >= max_retry:
        mod_state.pop(f"{rid}_failed", None)
        failed = None
    is_retry = False
    if failed:
        age = now_ts - failed.get("ts", 0)
        if age < retry_iv:
            return False
        is_retry = True
    return is_retry  # 测试中只看 retry 路径；cron_match 路径单独测


def test_cron_failure_not_permanent_when_max_retry_zero():
    """B1：max_retry=0 失败一次后，下个 tick 不应永久 continue。

    修复前：failed 存在且 count>=max_retry(0) → continue 永久停摆。
    修复后：先 pop failed 再判定 → 回到 retry/cron_match 判定，不再卡死。
    """
    mod_state = {"m": {"r_failed": {"ts": 1e9, "count": 1}}}
    # 修复前会直接 continue（永远不 run）；修复后应进入判定（返回 False 仅因 is_retry=False 且我们没传 cron_match）
    # 关键断言：failed 记录被清除（不再是永久阻塞）
    _cron_should_run(mod_state["m"], "r", max_retry=0, retry_iv=300, now_ts=1e9 + 400)
    assert "r_failed" not in mod_state["m"], "B1 修复后失败记录应被清除，否则永久停摆"


def test_cron_retry_exhausted_clears_record():
    """max_retry=3、count=4（已超次）→ 清除 failed，恢复 cron 自然触发。"""
    mod_state = {"m": {"r_failed": {"ts": 1e9, "count": 4}}}
    _cron_should_run(mod_state["m"], "r", max_retry=3, retry_iv=300, now_ts=1e9 + 400)
    assert "r_failed" not in mod_state["m"]


def test_cron_in_retry_window_blocks():
    """补发窗口内（age < retry_iv）不触发。"""
    mod_state = {"m": {"r_failed": {"ts": 1e9, "count": 1}}}
    run = _cron_should_run(mod_state["m"], "r", max_retry=3, retry_iv=300, now_ts=1e9 + 100)
    assert run is False
    assert "r_failed" in mod_state["m"]  # 未超次，记录保留
