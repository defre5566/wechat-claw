"""scheduler：通用调度引擎（基础设施，不认识任何模块的业务）。

机制：build_index() 拿到全部模块配置 → 每分钟 tick 遍历每条 schedule 规则 →
判定触发（cron / window / every）→ spawn 模块子进程 → 读退出码 →
rc≠0 且 retry 配置非空 → 记失败并按间隔补发（≤max 次）；rc=0 记成功。

所有业务知识（几点跑、补发策略、窗口语义）都在各模块的 module.json 里，不在此代码。
"""
from __future__ import annotations

import asyncio
import logging
import random
import sys
from datetime import datetime, timedelta
from pathlib import Path

from .config import get as get_cfg
from modules.registry_index import build_index

from .state import SCHED_STATE_FILE, load_sched_state, prune_state_file, save_sched_state

log = logging.getLogger("wechat-bridge")

MODULES_DIR = Path(__file__).resolve().parent.parent / "modules"  # 与 registry_index 一致（相对定位）
RUN_TIMEOUT = get_cfg("scheduler.run_timeout_seconds")  # 模块子进程超时保护（秒）


# ---------- 规则判定器（纯函数） ----------

def cron_match(expr: str, now: datetime) -> bool:
    """标准 5 段 cron "分 时 日 月 周"；仅支持 * 与单个数字。

    非法表达式返回 False 并告警，绝不抛异常（否则 _tick 崩溃 → 全部调度静默终止）。
    """
    try:
        minute, hour, dom, month, dow = expr.split()
    except ValueError:
        return False

    def f(field: str, value: int) -> bool:
        if field == "*":
            return True
        try:
            return int(field) == value
        except ValueError:
            log.warning(f"[sched] cron 表达式字段非法: {field!r}（表达式 {expr!r}）")
            return False

    try:
        return (
            f(minute, now.minute)
            and f(hour, now.hour)
            and (dom == "*" or int(dom) == now.day)
            and (month == "*" or int(month) == now.month)
            and (dow == "*" or int(dow) == now.weekday())
        )
    except ValueError:
        log.warning(f"[sched] cron 表达式非法: {expr!r}")
        return False


def every_interval(every: str) -> int:
    """解析 every 间隔为秒："1m"→60, "5m"→300, "1h"→3600。"""
    s = every.strip()
    if s.endswith("m"):
        return int(s[:-1]) * 60
    if s.endswith("h"):
        return int(s[:-1]) * 3600
    if s.endswith("s"):
        return int(s[:-1])
    return int(s)


def _rand_offset(spec: str) -> int:
    """解析 offset 配置：'random-5-50' → [5,50] 随机整数。"""
    if spec.startswith("random-"):
        parts = spec.split("-")
        if len(parts) == 3:
            lo, hi = int(parts[1]), int(parts[2])
            return random.randint(lo, hi)
    return 0


# ---------- 引擎 ----------

async def run_module(name: str, args: list[str] | None = None) -> int:
    """spawn 模块子进程，返回退出码。超时 300s 终止。"""
    script = MODULES_DIR / name / f"{name}_worker.py"
    if not script.is_file():
        log.error(f"[sched] 模块脚本不存在: {script}")
        return 2
    cmd = [sys.executable, str(script)] + (args or [])
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        )
        try:
            out, err = await asyncio.wait_for(proc.communicate(), timeout=RUN_TIMEOUT)
        except asyncio.TimeoutError:
            proc.kill()
            log.error(f"[sched] {name} 超时 {RUN_TIMEOUT}s，已终止")
            return 2
        if proc.returncode == 0:
            if out:
                log.info(f"[sched] {name} 完成: {out.decode()[:200]}")
            return 0
        log.error(f"[sched] {name} 失败 rc={proc.returncode}: {err.decode()[-500:]}")
        return proc.returncode or 1
    except Exception as e:
        log.error(f"[sched] {name} spawn 失败: {e}")
        return 2


def _rule_id(rule: dict, name: str, idx: int) -> str:
    """规则稳定标识（用于状态键）。cron 用 id；无则用 name|idx。"""
    return rule.get("id") or f"{name}|{idx}"


async def scheduler() -> None:
    """主循环：启动立即 tick 一次，此后每分钟 tick。"""
    await _tick()
    while True:
        now = datetime.now()
        nxt = (now + timedelta(minutes=1)).replace(second=0, microsecond=0)
        await asyncio.sleep(max(1.0, (nxt - now).total_seconds() + 0.5))
        await _tick()


async def _tick() -> None:
    index = build_index()
    prune_state_file(SCHED_STATE_FILE)  # 修剪过期窗口键（30 天）
    state = load_sched_state()
    now = datetime.now()
    changed = False

    for name, cfg in index.items():
        rules = cfg.get("schedule") or []
        retry_cfg = cfg.get("retry") or {}
        max_retry = retry_cfg.get("max", 0)
        retry_iv = retry_cfg.get("interval_seconds", 300)

        for i, rule in enumerate(rules):
            rid = _rule_id(rule, name, i)
            mod_state = state.setdefault(name, {})

            # ---- every 规则 ----
            # 周期触发 + 失败补发：失败按 retry 配置补发（≤max 次、间隔 retry_iv，补发窗口内
            # 只走 retry 判定，不按周期重复触发）；未配置补发或已超次 → 清除失败记录并推进
            # last_ts 等下个周期，避免每分钟失败风暴与失败计数无限增长。
            if "every" in rule:
                interval = every_interval(rule["every"])
                failed = mod_state.get(f"{rid}_failed")
                if failed and failed.get("count", 0) > max_retry:
                    # 历史遗留/已超次记录：清除后回退周期判定
                    mod_state.pop(f"{rid}_failed", None)
                    failed = None
                    changed = True
                if failed:
                    due = (
                        failed.get("count", 0) <= max_retry
                        and now.timestamp() - failed.get("ts", 0) >= retry_iv
                    )
                    if not due:
                        continue
                elif now.timestamp() - mod_state.get("last_ts", 0) < interval:
                    continue
                rc = await run_module(name)
                if rc == 0:
                    mod_state["last_ts"] = now.timestamp()
                    mod_state.pop(f"{rid}_failed", None)
                    log.info(f"[sched] {name}[{rid}] 完成（every {rule['every']}）")
                else:
                    cnt = failed.get("count", 0) + 1 if failed else 1
                    if max_retry and cnt <= max_retry:
                        mod_state[f"{rid}_failed"] = {"ts": now.timestamp(), "count": cnt}
                        log.warning(f"[sched] {name}[{rid}] 失败 rc={rc}（补发 {cnt}/{max_retry}）")
                    else:
                        mod_state.pop(f"{rid}_failed", None)
                        mod_state["last_ts"] = now.timestamp()
                        log.warning(f"[sched] {name}[{rid}] 失败 rc={rc}（未配置补发/已超次，下个周期再试）")
                changed = True
                continue

            # ---- window 规则（小时窗口 + 随机偏移，严格版：首 tick 定偏移缓存）----
            if "window" in rule:
                w = rule["window"]
                hours = w.get("hours", "8-21")
                lo, hi = int(hours.split("-")[0]), int(hours.split("-")[1])
                if not (lo <= now.hour <= hi):
                    continue
                hour_key = now.strftime("%Y-%m-%d-%H")
                if mod_state.get(hour_key):
                    continue  # 该小时已处理
                off_key = f"{hour_key}_off"
                if off_key not in mod_state:
                    mod_state[off_key] = _rand_offset(w.get("offset", "random-5-50"))
                    changed = True
                offset = mod_state[off_key]
                if now.minute < offset:
                    continue  # 未到触发时刻
                if now.minute <= 50:
                    rc = await run_module(name)
                    if rc == 0:
                        log.info(f"[sched] {name} 触发（{hour_key}，偏移 {offset} 分钟）")
                    else:
                        _log_fail(name, rid, rc)
                else:
                    log.info(f"[sched] {name} 错过窗口（{hour_key}），本小时跳过")
                mod_state[hour_key] = now.timestamp()
                changed = True
                continue

            # ---- cron 规则 ----
            if "cron" in rule:
                # 成功状态：已记为今天 → 跳过
                done_key = mod_state.get(rid)
                today = now.date().isoformat()
                if done_key == today:
                    continue
                # 补发：失败过且间隔满足且未超次数 → 允许重跑（不要求 cron 再次匹配）
                failed = mod_state.get(f"{rid}_failed")
                is_retry = False
                if failed:
                    age = now.timestamp() - failed.get("ts", 0)
                    if failed.get("count", 0) >= max_retry or age < retry_iv:
                        continue  # 超次放弃，或未到补发间隔
                    is_retry = True
                if is_retry or cron_match(rule["cron"], now):
                    rc = await run_module(name, rule.get("args"))
                    if rc == 0:
                        mod_state[rid] = today
                        mod_state.pop(f"{rid}_failed", None)
                        log.info(f"[sched] {name}[{rid}] 完成")
                    else:
                        cnt = failed.get("count", 0) + 1 if failed else 1
                        mod_state[f"{rid}_failed"] = {"ts": now.timestamp(), "count": cnt}
                        log.warning(
                            f"[sched] {name}[{rid}] 失败 rc={rc}（补发 {cnt}/{max_retry}）"
                        )
                    changed = True

    if changed:
        save_sched_state(state)


def _log_fail(name: str, rid: str, rc: int) -> None:
    log.warning(f"[sched] {name}[{rid}] 失败 rc={rc}")