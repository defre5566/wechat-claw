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

# G3：window 重叠规则被窗格拦截的告警去重（进程内存级 {name:hour_key:rid}，防刷屏）
_window_warned: set[str] = set()


# ---------- 规则判定器（纯函数） ----------

def cron_match(expr: str, now: datetime) -> bool:
    """标准 5 段 cron "分 时 日 月 周"；支持 * / 数字 / a,b 列表 / a-b 区间 / */n 步进。

    周段语义：0=周日、1=周一、…、6=周六（标准 cron，非 Python weekday）。
    非法表达式返回 False 并告警，绝不抛异常（否则 _tick 崩溃 → 全部调度静默终止）。
    """
    try:
        minute, hour, dom, month, dow = expr.split()
    except ValueError:
        return False

    try:
        return (
            _match_field(minute, now.minute, "minute", expr)
            and _match_field(hour, now.hour, "hour", expr)
            and _match_field(dom, now.day, "dom", expr)
            and _match_field(month, now.month, "month", expr)
            # 周段：标准 cron（周日=0）→ Python weekday（周一=0）换算
            and _match_field(dow, (now.weekday() + 1) % 7, "dow", expr)
        )
    except ValueError:
        log.warning(f"[sched] cron 表达式非法: {expr!r}")
        return False


def _match_field(field: str, value: int, label: str, expr: str) -> bool:
    """匹配单个 cron 字段：* / N / a,b 列表 / a-b 区间 / */n 步进。非法 → False + 告警。"""
    if field == "*":
        return True
    if "," in field:  # 列表：任一命中即真
        return any(_match_one(p, value, label, expr) for p in field.split(","))
    return _match_one(field, value, label, expr)


def _match_one(field: str, value: int, label: str, expr: str) -> bool:
    if "/" in field:  # 步进：仅支持 */n（a-b/n 组合暂不做，定稿）
        base, step_s = field.split("/", 1)
        try:
            step = int(step_s)
        except ValueError:
            log.warning(f"[sched] cron {label} 字段非法: {field!r}（表达式 {expr!r}）")
            return False
        if base != "*" or step <= 0:
            log.warning(f"[sched] cron {label} 字段暂不支持: {field!r}（仅支持 */n 步进）")
            return False
        return value % step == 0
    if "-" in field:  # 区间 a-b
        try:
            lo, hi = (int(x) for x in field.split("-", 1))
        except ValueError:
            log.warning(f"[sched] cron {label} 字段非法: {field!r}（表达式 {expr!r}）")
            return False
        return lo <= value <= hi
    try:
        return int(field) == value
    except ValueError:
        log.warning(f"[sched] cron {label} 字段非法: {field!r}（表达式 {expr!r}）")
        return False


def every_interval(every: str) -> int | None:
    """解析 every 间隔为秒："1m"→60, "5m"→300, "1h"→3600。

    非法单位（如 "1d"）→ None（容错，绝不抛异常，否则 _tick 崩溃 → 全部调度静默终止）。
    """
    s = every.strip()
    try:
        if s.endswith("m"):
            return int(s[:-1]) * 60
        if s.endswith("h"):
            return int(s[:-1]) * 3600
        if s.endswith("s"):
            return int(s[:-1])
        return int(s)
    except ValueError:
        log.warning(f"[sched] every 表达式非法: {every!r}（应形如 1m/5m/1h/30s）")
        return None


def _rand_offset(spec: str) -> int:
    """解析 offset 配置：'random-5-50' → [5,50] 随机整数。非法 → 0。"""
    if spec.startswith("random-"):
        parts = spec.split("-")
        if len(parts) == 3:
            try:
                lo, hi = int(parts[1]), int(parts[2])
                return random.randint(lo, hi)
            except ValueError:
                log.warning(f"[sched] offset 表达式非法: {spec!r}")
    return 0


def _window_hours(spec: str) -> tuple[int, int] | None:
    """解析 window hours "8-21" → (8,21)；非法/倒序 → None + 告警（H1：绝不抛异常，防连坐全调度）。"""
    try:
        lo_s, hi_s = spec.split("-")
        lo, hi = int(lo_s), int(hi_s)
        if 0 <= lo <= hi <= 23:
            return lo, hi
    except (ValueError, AttributeError):
        pass
    log.warning(f"[sched] window hours 非法: {spec!r}（应形如 8-21，且 0≤lo≤hi≤23）")
    return None


def _parse_once(spec: str) -> datetime | None:
    """解析 once 时刻："2026-09-01T09:00"（本地时间）。非法 → None + 告警。"""
    try:
        return datetime.fromisoformat(spec)
    except (ValueError, TypeError):
        log.warning(f"[sched] once 表达式非法: {spec!r}（应形如 2026-09-01T09:00）")
        return None


def _next_once_retry(once_at: datetime, now: datetime) -> datetime:
    """once 超次后的下次尝试 = 最近一个未来的 once_at 时刻（每日一次，直至成功）。"""
    nxt = once_at
    while nxt <= now:
        nxt += timedelta(days=1)
    return nxt


# ---------- 引擎 ----------

async def run_module(name: str, args: list[str] | None = None) -> int:
    """spawn 模块子进程，返回退出码。超时 300s 终止。

    H7：args 含 --dry-run（测试参数混入生产调度）→ 拒绝执行 + ERROR + rc=2（不记成功不补发）。
    """
    if args and "--dry-run" in args:
        log.error(f"[sched] {name} 规则 args 含 --dry-run（测试参数混入生产调度），拒绝执行，请从 module.json 删除")
        return 2
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
                log.info(f"[sched] {name} 完成: {out.decode(errors='replace')[:200]}")
            return 0
        log.error(f"[sched] {name} 失败 rc={proc.returncode}: {err.decode(errors='replace')[-500:]}")
        return proc.returncode or 1
    except Exception as e:
        log.error(f"[sched] {name} spawn 失败: {e}")
        return 2


def _rule_id(rule: dict, name: str, idx: int) -> str:
    """规则稳定标识（用于状态键）。cron 用 id；无则用 name|idx。"""
    return rule.get("id") or f"{name}|{idx}"


async def scheduler() -> None:
    """主循环：启动立即 tick 一次，此后每分钟 tick。_tick 异常被吞（记日志不退出）。"""
    await _tick()
    while True:
        now = datetime.now()
        nxt = (now + timedelta(minutes=1)).replace(second=0, microsecond=0)
        await asyncio.sleep(max(1.0, (nxt - now).total_seconds() + 0.5))
        try:
            await _tick()
        except Exception as e:  # noqa: BLE001  绝不让单个 tick 异常杀死调度循环
            log.error(f"[sched] _tick 异常（已吞，下次 tick 继续）: {e}")


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
            # per-rule last_ts 等下个周期，避免每分钟失败风暴与失败计数无限增长。
            if "every" in rule:
                interval = every_interval(rule["every"])
                if interval is None:
                    continue  # 非法 every，已告警，跳过此规则
                last_ts_key = f"{rid}_last_ts"
                failed = mod_state.get(f"{rid}_failed")
                if failed and failed.get("count", 0) >= max_retry:  # A2：与 cron 一致（>=）
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
                elif now.timestamp() - mod_state.get(last_ts_key, 0) < interval:
                    continue
                rc = await run_module(name, rule.get("args"))  # H6：every 也传规则 args
                if rc == 0:
                    mod_state[last_ts_key] = now.timestamp()
                    mod_state.pop(f"{rid}_failed", None)
                    log.info(f"[sched] {name}[{rid}] 完成（every {rule['every']}）")
                else:
                    if rc >= 2 or rc < 0:
                        # H5：引擎级异常（脚本缺失/超时/spawn 失败/信号杀）→ 不补发，推进周期
                        mod_state.pop(f"{rid}_failed", None)
                        mod_state[last_ts_key] = now.timestamp()
                        log.error(f"[sched] {name}[{rid}] 引擎级异常 rc={rc}（不补发，下周期再试）")
                    else:
                        cnt = failed.get("count", 0) + 1 if failed else 1
                        if max_retry and cnt <= max_retry:
                            mod_state[f"{rid}_failed"] = {"ts": now.timestamp(), "count": cnt}
                            log.warning(f"[sched] {name}[{rid}] 失败 rc={rc}（补发 {cnt}/{max_retry}）")
                        else:
                            mod_state.pop(f"{rid}_failed", None)
                            mod_state[last_ts_key] = now.timestamp()
                            log.warning(f"[sched] {name}[{rid}] 失败 rc={rc}（未配置补发/已超次，下个周期再试）")
                changed = True
                continue

            # ---- window 规则（小时窗口 + 随机偏移：每窗格一次随机通知；G2 支持窗格内补发）----
            if "window" in rule:
                w = rule["window"]
                if not isinstance(w, dict):  # H1：window 非 dict → 跳过 + 告警
                    log.warning(f"[sched] {name} window 规则非法: {rule!r}")
                    continue
                hours = _window_hours(w.get("hours", "8-21"))  # H1：防御解析，非法只跳过不连坐
                if hours is None:
                    continue
                lo, hi = hours
                if not (lo <= now.hour <= hi):
                    continue
                hour_key = now.strftime("%Y-%m-%d-%H")
                if mod_state.get(hour_key):
                    # G3：被同模块另一条规则抢占（重叠 hours 共享窗格）→ 告警防误用无声
                    who = mod_state.get(f"{hour_key}_by")
                    if who and who != rid:
                        wk = f"{name}:{hour_key}:{rid}"
                        if wk not in _window_warned:
                            _window_warned.add(wk)
                            log.warning(
                                f"[sched] {name}[{rid}] 本窗格已被规则 {who} 处理"
                                "（同一模块多条 window 规则 hours 重叠时共享每小时一次；"
                                "想多时段固定提醒请用 cron/every）"
                            )
                    continue  # 该小时已处理
                off_key = f"{hour_key}_off"
                if off_key not in mod_state:
                    mod_state[off_key] = _rand_offset(w.get("offset", "random-5-50"))
                    changed = True
                offset = mod_state[off_key]
                # G2：失败补发判定（失败过且到间隔 → 越过 offset 直接补发）
                failed = mod_state.get(f"{rid}_failed")
                retry_due = False
                if failed:
                    retry_due = (
                        failed.get("count", 0) <= max_retry
                        and now.timestamp() - failed.get("ts", 0) >= retry_iv
                    )
                    if not retry_due:
                        continue  # 补发间隔未到：本窗格保留机会
                if not retry_due:
                    if now.minute < offset:
                        continue  # 未到触发时刻
                    if now.minute > 50:
                        log.info(f"[sched] {name} 错过窗口（{hour_key}），本小时跳过")
                        mod_state[hour_key] = now.timestamp()
                        mod_state[f"{hour_key}_by"] = rid
                        changed = True
                        continue
                rc = await run_module(name, rule.get("args"))  # H6：window 也传规则 args
                if rc == 0:
                    mod_state.pop(f"{rid}_failed", None)
                    log.info(f"[sched] {name}[{rid}] 触发（{hour_key}，偏移 {offset} 分钟）")
                elif rc >= 2 or rc < 0:
                    # H5：引擎级异常（脚本缺失/超时/信号杀）→ 本窗格放弃，不补发
                    log.error(f"[sched] {name}[{rid}] 引擎级异常 rc={rc}（本窗格放弃）")
                else:
                    cnt = failed.get("count", 0) + 1 if failed else 1
                    if max_retry and cnt <= max_retry:
                        mod_state[f"{rid}_failed"] = {"ts": now.timestamp(), "count": cnt}
                        log.warning(f"[sched] {name}[{rid}] 失败 rc={rc}（窗格内补发 {cnt}/{max_retry}）")
                        changed = True
                        continue  # 不写 hour_key：保留本窗格补发机会
                    else:
                        log.warning(f"[sched] {name}[{rid}] 失败 rc={rc}（未配置补发/已超次，本窗格放弃）")
                mod_state[hour_key] = now.timestamp()
                mod_state[f"{hour_key}_by"] = rid
                changed = True
                continue

            # ---- cron 规则 ----
            if "cron" in rule:
                # 成功状态：已记为今天 → 跳过
                done_key = mod_state.get(rid)
                today = now.date().isoformat()
                if done_key == today:
                    continue
                # 失败记录：已超次（含 max_retry=0）→ 清除，恢复按 cron 自然触发
                # （对齐文档"3 次后放弃，次日按 cron 自然重来"；避免永久停摆）
                failed = mod_state.get(f"{rid}_failed")
                if failed and failed.get("count", 0) >= max_retry:
                    mod_state.pop(f"{rid}_failed", None)
                    failed = None
                    changed = True
                # 补发：失败过且间隔满足且未超次数 → 允许重跑（不要求 cron 再次匹配）
                is_retry = False
                if failed:
                    age = now.timestamp() - failed.get("ts", 0)
                    if age < retry_iv:
                        continue  # 未到补发间隔
                    is_retry = True
                if is_retry or cron_match(rule["cron"], now):
                    rc = await run_module(name, rule.get("args"))
                    if rc == 0:
                        mod_state[rid] = today
                        mod_state.pop(f"{rid}_failed", None)
                        log.info(f"[sched] {name}[{rid}] 完成")
                    elif rc >= 2 or rc < 0:
                        # H5：引擎级异常（脚本缺失/超时/信号杀）→ 不记 failed 不记 done，
                        # 今天放弃，次日 cron 自然再跑
                        log.error(f"[sched] {name}[{rid}] 引擎级异常 rc={rc}（不补发，次日 cron 自然再跑）")
                    else:
                        cnt = failed.get("count", 0) + 1 if failed else 1
                        mod_state[f"{rid}_failed"] = {"ts": now.timestamp(), "count": cnt}
                        log.warning(
                            f"[sched] {name}[{rid}] 失败 rc={rc}（补发 {cnt}/{max_retry}）"
                        )
                    changed = True

            # ---- once 规则（一次性提醒：到点跑一次，成功永久完成，永不重跑）----
            if "once" in rule:
                once_at = _parse_once(rule["once"])
                if once_at is None:
                    continue  # 非法 once，已告警，跳过此规则
                done_key = f"once:{rule['once']}"            # 基于时刻字符串：规则顺序变化不漂移
                failed_key = f"once:{rule['once']}_failed"
                if mod_state.get(done_key):
                    continue  # 已永久完成
                if now < once_at:
                    continue  # 未到点
                # 补发判定：失败按 retry 间隔补发；超次不放弃——次日同刻继续（once 承诺不能无声丢）
                failed = mod_state.get(failed_key)
                retry_due = False
                if failed:
                    retry_due = now.timestamp() - failed.get("ts", 0) >= retry_iv
                    if not retry_due:
                        continue
                rc = await run_module(name, rule.get("args"))
                if rc == 0:
                    mod_state[done_key] = now.isoformat()  # 永久完成勾
                    mod_state.pop(failed_key, None)
                    log.info(f"[sched] {name}[{rid}] once 完成（{rule['once']}）")
                elif rc >= 2 or rc < 0:
                    # H5：引擎级异常 → 次日同刻再试
                    nxt = _next_once_retry(once_at, now)
                    mod_state[failed_key] = {"ts": nxt.timestamp(), "count": 1}
                    log.error(f"[sched] {name}[{rid}] once 引擎级异常 rc={rc}（{nxt:%Y-%m-%d %H:%M} 再试）")
                else:
                    cnt = failed.get("count", 0) + 1 if failed else 1
                    if max_retry and cnt <= max_retry:
                        mod_state[failed_key] = {"ts": now.timestamp(), "count": cnt}
                        log.warning(f"[sched] {name}[{rid}] once 失败 rc={rc}（补发 {cnt}/{max_retry}）")
                    else:
                        # 超次不放弃：下次尝试 = 最近的未来 once_at 时刻
                        nxt = _next_once_retry(once_at, now)
                        mod_state[failed_key] = {"ts": nxt.timestamp(), "count": cnt}
                        log.error(
                            f"[sched] {name}[{rid}] once 失败 rc={rc}（已超次，{nxt:%Y-%m-%d %H:%M} 再试直至成功）"
                        )
                changed = True
                continue

    if changed:
        save_sched_state(state)