"""opencode scheduler job 管理（agent 型长任务的定时执行器）＝ wechat-claw 自建 job 调度。

职责（S6 定稿 + 跨平台化）：
- install_job：写 job.json（含 env/XDG 注入）→ 按平台生成精确定时器：
  Linux systemd user timer（OnCalendar）/ macOS launchd（StartCalendarInterval）/
  Windows schtasks（计划任务，列表 cron 拆多任务）
- uninstall_job：停定时器 → 删定时器/单元 → 删 job.json（按 slug 前缀 <模块名>- 精确清理）
- list_jobs：列出全部 job（含 lastRunStatus）
- supervisor 子命令：执行器（平台定时到点触发）——读 job.json → 防重锁 → 执行
  opencode（invocation，与 acp.command 同源寻址）→ 状态写回 + jsonl 日志

约定（定稿）：
- scopeId 固定 "wechat-claw"；slug = <模块名>-<任务名>
- 模块可选带 job.template.json → 用 install_job 登记为正式 job
- 执行器 = 本模块 supervisor 子命令（纯 Python，跨平台；不再依赖 perl）
- 定时载体：每 job 一个平台精确定时器；cron 先经 _parse_cron 统一解析，
  三平台适配器各自生成（行为一致）；cron 无法精确表达（dom+dow 双非 * 等）→ 拒绝登记
- 执行环境：job.env（XDG_* 指向数据根）由 supervisor 执行前注入，跨平台一致
  （替代原 systemd Environment 行；schtasks 无环境注入能力）
- 收敛形态（向导安装 opencode）下 job 执行的 opencode 与 ACP 主链路同配置域

隔离：OPENCODE_SCHED_ROOT / OPENCODE_SYSTEMD_USER_DIR 环境变量可覆盖运行时根（测试/部署自洽）。
"""
from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
import time
from pathlib import Path

log = logging.getLogger("wechat-bridge")

# 运行时根：部署机上 opencode 配置的 scheduler 目录（不进仓库，部署时生成）
_SCHED_ROOT_ENV = "OPENCODE_SCHED_ROOT"
# systemd user 目录（测试隔离可覆盖）
_SYSTEMD_USER_ENV = "OPENCODE_SYSTEMD_USER_DIR"


def sched_root() -> Path:
    """job 数据根：env 覆盖 > 收敛形态（数据根 opencode 配置域）> 默认 opencode 配置域。"""
    env = os.environ.get(_SCHED_ROOT_ENV)
    if env:
        return Path(env)
    from bridge.config import opencode_converged
    conv = opencode_converged()
    if conv is not None:
        return conv / "scheduler"
    return Path.home() / ".config" / "opencode" / "scheduler"


def _systemd_user_dir() -> Path:
    env = os.environ.get(_SYSTEMD_USER_ENV)
    if env:
        return Path(env)
    return Path.home() / ".config" / "systemd" / "user"


def scope_id() -> str:
    return "wechat-claw"


def jobs_dir() -> Path:
    return sched_root() / "scopes" / scope_id() / "jobs"


def _cron_to_oncalendar(expr: str) -> str | None:
    """cron 5 段 → systemd OnCalendar（支持 * 与数字、列表、区间的基础形态）。

    "50 8 * * *" → "*-*-* 08:50:00"；"0 9 1 * *" → "*-*-01 09:00:00"；"0 9 * * 1" → "Mon *-*-* 09:00:00"
    无法表达（如 dom 与 dow 同时非 *）→ None（调用方拒绝）。
    """
    try:
        minute, hour, dom, month, dow = expr.split()
    except ValueError:
        return None
    dow_map = {"0": "Sun", "1": "Mon", "2": "Tue", "3": "Wed", "4": "Thu", "5": "Fri", "6": "Sat"}

    def f(field: str) -> str | None:  # 数字/列表(a,b)/区间(a-b)/星号 → systemd 段（数字补零两位）
        if field == "*":
            return "*"
        if "," in field:
            parts = [p.strip() for p in field.split(",")]
            if all(_is_int(p) for p in parts):
                return ",".join(p.zfill(2) for p in parts)
            return None
        if "-" in field:
            a, b = field.split("-", 1)
            if _is_int(a) and _is_int(b):
                return f"{a.zfill(2)}..{b.zfill(2)}"
            return None
        if _is_int(field):
            return field.zfill(2)
        return None

    min_s, hour_s = f(minute), f(hour)
    dom_s, month_s = f(dom), f(month)
    if min_s is None or hour_s is None or dom_s is None or month_s is None:
        return None
    if dom != "*" and dow != "*":
        return None  # cron 的 dom+dow 是 OR，systemd 是 AND，语义不一致 → 拒绝
    time_s = f"{hour_s}:{min_s}:00"
    if dow != "*":
        dow_s = dow_map.get(dow) or f(dow)
        if dow_s is None:
            return None
        if dow_s in dow_map.values():
            return f"{dow_s} *-*-* {time_s}"
        return None
    if dom_s != "*":
        return f"*-*-{dom_s} {time_s}"
    if month_s != "*":
        return f"*-{month_s}-* {time_s}"
    return f"*-*-* {time_s}"


def _is_int(s: str) -> bool:
    try:
        int(s)
        return True
    except (ValueError, TypeError):
        return False


# ---------- cron 统一解析（三平台共享；平台适配器各自生成定时定义） ----------

_RANGES = {"minute": (0, 59), "hour": (0, 23), "dom": (1, 31), "month": (1, 12), "dow": (0, 6)}


def _expand_field(text: str, lo: int, hi: int) -> list[int]:
    """单字段展开：数字 / * / */N（步长） / a,b（列表） / a-b（区间）→ 升序整数列表。"""
    vals: set[int] = set()
    for piece in str(text).split(","):
        piece = piece.strip()
        if not piece:
            continue
        if piece == "*":
            vals.update(range(lo, hi + 1))
        elif piece.startswith("*/"):
            step = int(piece[2:])
            if step <= 0:
                raise ValueError(f"cron 步长无效: {piece!r}")
            vals.update(range(lo, hi + 1, step))
        elif "-" in piece:
            a, b = (int(x) for x in piece.split("-", 1))
            vals.update(range(max(a, lo), min(b, hi) + 1))
        else:
            v = int(piece)
            if not (lo <= v <= hi):
                raise ValueError(f"cron 字段越界: {piece!r}（{lo}-{hi}）")
            vals.add(v)
    return sorted(vals)


def _parse_cron(expr: str) -> dict[str, list[int]]:
    """cron 5 段 → 展开整数列表（分钟/时/日/月/周）。

    拒绝：日字段与周字段同时非通配（cron 为 OR 语义，平台定时器均为 AND，
    三平台一致拒绝——与原 systemd 行为一致）。
    """
    parts = str(expr).split()
    if len(parts) != 5:
        raise ValueError(f"cron 表达式无效: {expr!r}（需 5 段：分 时 日 月 周）")
    parsed = {}
    for field, text in zip(_RANGES, parts):
        lo, hi = _RANGES[field]
        parsed[field] = _expand_field(text, lo, hi)
    dom_full = set(parsed["dom"]) == set(range(1, 32))
    dow_full = set(parsed["dow"]) == set(range(0, 7))
    if not dom_full and not dow_full:
        raise ValueError(f"cron 日字段与周字段同时非通配（OR 语义平台定时器无法表达）: {expr!r}")
    return parsed


# ---------- 平台适配器 ----------

def _launchd_interval(parsed: dict) -> dict:
    """launchd StartCalendarInterval 字段（多键同时满足=AND；数组表达列表）。"""
    iv: dict = {}
    if set(parsed["minute"]) != set(range(0, 60)):
        iv["Minute"] = parsed["minute"]
    if set(parsed["hour"]) != set(range(0, 24)):
        iv["Hour"] = parsed["hour"]
    if set(parsed["dom"]) != set(range(1, 32)):
        iv["Day"] = parsed["dom"]
    if set(parsed["month"]) != set(range(1, 13)):
        iv["Month"] = parsed["month"]
    if set(parsed["dow"]) != set(range(0, 7)):
        iv["Weekday"] = parsed["dow"]
    if not iv:
        iv["Minute"] = list(range(0, 60))  # 每分钟
    return iv


def _dow_letters(dows: list[int]) -> str:
    """dow 数字列表 → schtasks 字母（0=SU 起，逗号连接）。"""
    names = ["SU", "MO", "TU", "WE", "TH", "FR", "SA"]
    return ",".join(names[d] for d in dows)


def _minute_step(vals: list[int]) -> int | None:
    """升序列表若为 0 起等差步长（且 60 整除）→ 步长；否则 None。"""
    if not vals or vals[0] != 0 or len(vals) < 2:
        return None
    step = vals[1] - vals[0]
    if step <= 0 or any(v - vals[0] != i * step for i, v in enumerate(vals)):
        return None
    if step == 1:
        return 1
    return step if 60 % step == 0 else None


def _hour_step(vals: list[int]) -> int | None:
    if not vals or vals[0] != 0 or len(vals) < 2:
        return None
    step = vals[1] - vals[0]
    if step <= 0 or any(v - vals[0] != i * step for i, v in enumerate(vals)):
        return None
    return step if 24 % step == 0 else None


def _schtasks_plans(parsed: dict, expr: str) -> list[dict]:
    """schtasks 计划列表（列表 cron 拆多任务）。

    每项：{"sc": daily|weekly|monthly|minute|hourly, "mo": str|None,
           "d": str|None, "m": str|None, "st": "HH:MM"}
    无法精确表达（分钟步长不整除 60 且非列表展开等）→ ValueError（拒绝登记，提示明确）。
    """
    m, h = parsed["minute"], parsed["hour"]
    dom, month, dow = parsed["dom"], parsed["month"], parsed["dow"]
    full_m = set(m) == set(range(0, 60))
    full_h = set(h) == set(range(0, 24))
    full_dom = set(dom) == set(range(1, 32))
    full_dow = set(dow) == set(range(0, 7))
    full_month = set(month) == set(range(1, 13))

    # 每分钟（* * * * *）
    if full_m and full_h and full_dom and full_dow and full_month:
        return [{"sc": "minute", "mo": "1", "d": None, "m": None, "st": "00:00"}]
    # 分钟步长且对齐 0（*/N，N 整除 60）——其余字段全量
    if full_h and full_dom and full_dow and full_month:
        step = _minute_step(m)
        if step is not None:
            return [{"sc": "minute", "mo": str(step), "d": None, "m": None, "st": "00:00"}]
        # 分钟列表非 0 起步长（如 5,35）→ 拆 hourly 任务（每小时固定分）
        if len(m) <= 12:
            return [{"sc": "hourly", "mo": "1", "d": None, "m": None, "st": f"00:{mm:02d}"}
                    for mm in m]
        raise ValueError(f"cron 分钟集合 schtasks 无法表达: {expr!r}")
    # 小时步长（分固定、其余全量）→ /sc hourly /mo step /st HH:MM
    if len(m) == 1 and full_h is False and full_dom and full_dow and full_month:
        step = _hour_step(h)
        if step is not None:
            return [{"sc": "hourly", "mo": str(step), "d": None, "m": None,
                     "st": f"{h[0]:02d}:{m[0]:02d}"}]
    # 日字段分类（拒绝规则保证 dom/dow 不同时非全量）
    if not full_dow:
        sc, d, mth = "weekly", _dow_letters(dow), None
    elif not full_dom:
        sc, d, mth = "monthly", ",".join(str(x) for x in dom), None
    else:
        sc, d, mth = "daily", None, None
    if not full_month:
        if sc == "monthly":
            mth = ",".join(str(x) for x in month)
        else:
            raise ValueError(f"cron 月份非通配且非每月几号形态，schtasks 无法表达: {expr!r}")
    if full_h:
        # 每小时 N 个固定分钟 → 拆 hourly 任务
        if len(m) <= 12:
            return [{"sc": "hourly", "mo": "1", "d": d, "m": mth, "st": f"00:{mm:02d}"}
                    for mm in m]
        raise ValueError(f"cron 分钟集合 schtasks 无法表达: {expr!r}")
    plans = []
    for hh in h:
        for mm in m:
            plans.append({"sc": sc, "mo": None, "d": d, "m": mth, "st": f"{hh:02d}:{mm:02d}"})
    return plans


# ---------- job 组装 ----------

def build_job(module: str, name: str, schedule: str, prompt: str,
              timeout: int = 1800, workdir: str | None = None) -> dict:
    """组装 supervisor 认识的 job 定义（定稿：scopeId=wechat-claw，slug=<模块名>-<任务名>）。

    invocation.command = resolve_opencode()（与 acp.command 同源寻址，缺则 ValueError——
    登记前就失败，不写半成品）。
    """
    slug = f"{module}-{name}"
    from bridge.config import WORK_ROOT, resolve_opencode
    workdir = workdir or str(WORK_ROOT)
    command = resolve_opencode()
    if not command:
        raise ValueError(
            "未找到 opencode 可执行文件（acp.command / PATH / ~/.opencode/bin 均无），"
            "job 登记中止"
        )
    return {
        "name": name,
        "slug": slug,
        "scopeId": scope_id(),
        "schedule": schedule,
        "timeoutSeconds": timeout,
        "workdir": workdir,
        "source": "module",
        "module": module,
        "run": {"title": slug, "prompt": prompt},
        "invocation": {
            "command": command,
            "args": ["run", "--title", slug, "--", prompt],
        },
        "env": _job_env(),
    }


def _job_env() -> dict:
    """job 执行环境（XDG_* 指向数据根，与 ACP 主链路同配置域；跨平台由 supervisor 注入）。"""
    from bridge.config import xdg_env
    try:
        return {k: str(v) for k, v in xdg_env().items()}
    except Exception:
        return {}


def _program() -> str:
    """定时器 ExecStart 的程序入口：源码形态 = venv python；打包形态 = exe 自身。

    统一调用：<program> -m bridge.opencode_jobs supervisor <job.json>
    """
    if getattr(sys, "frozen", False):
        return sys.executable
    from bridge.config import DEPLOY_ROOT
    return str(DEPLOY_ROOT / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python"))


def _supervisor_cmd(job_path: Path) -> list[str]:
    """平台定时器调用的 supervisor 命令（统一入口）。"""
    return [_program(), "-m", "bridge.opencode_jobs", "supervisor", str(job_path)]


# ---------- 生命周期 ----------

def _platform_kind() -> str:
    """平台判定（install/uninstall 分支用；独立函数便于测试隔离）。"""
    if sys.platform == "win32":
        return "windows"
    if sys.platform == "darwin":
        return "darwin"
    return "linux"


def install_job(module: str, name: str, schedule: str, prompt: str,
                timeout: int = 1800, workdir: str | None = None, dry: bool = False) -> dict:
    """登记 job：写 job.json + 按平台生成精确定时器（dry=True 只写文件不碰系统）。

    平台载体：Linux systemd user timer / macOS launchd plist / Windows schtasks。
    cron 无法精确表达 → ValueError（三平台一致拒绝，不落半成品）。
    """
    job = build_job(module, name, schedule, prompt, timeout, workdir)
    slug = job["slug"]
    parsed = _parse_cron(schedule)  # 统一解析；拒绝规则（dom+dow 双非 *）在此抛出

    jd = jobs_dir()
    if not jd.exists() or not os.access(jd, os.W_OK):
        jd.mkdir(parents=True, exist_ok=True)
        if not os.access(jd, os.W_OK):
            raise OSError(f"job 目录不可写: {jd}")

    job_path = jd / f"{slug}.json"
    written: list[Path] = []
    try:
        tmp = job_path.with_name(job_path.name + ".tmp")
        tmp.write_text(json.dumps(job, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        os.replace(tmp, job_path)
        written.append(job_path)
    except Exception:
        for p in written:
            try:
                p.unlink(missing_ok=True)
            except OSError:
                pass
        raise

    kind = _platform_kind()
    if kind == "windows":
        plans = _schtasks_plans(parsed, schedule)
        unit_refs = _install_windows_timers(slug, job_path, plans, dry=dry)
    elif kind == "darwin":
        unit_refs = _install_launchd(slug, job_path, parsed, dry=dry)
    else:
        unit_refs = _install_systemd(slug, job_path, schedule, dry=dry)

    return {"ok": True, "slug": slug, "job_path": str(job_path), "timers": unit_refs}


# ---------- 平台定时器生成 ----------

def _install_systemd(slug: str, job_path: Path, schedule: str, dry: bool) -> list[str]:
    """Linux：systemd user timer（OnCalendar 精确触发；supervisor 自写日志，不再依赖 perl/重定向）。"""
    cal = _cron_to_oncalendar(schedule)
    if cal is None:
        raise ValueError(f"[opencode-jobs] schedule {schedule!r} 无法转为 systemd OnCalendar")
    systemd_dir = _systemd_user_dir()
    systemd_dir.mkdir(parents=True, exist_ok=True)
    cmd = _supervisor_cmd(job_path)
    log_path = sched_root().parent / "logs" / "scheduler" / scope_id() / f"{slug}.log"
    svc = systemd_dir / f"opencode-job-{slug}.service"
    timer = systemd_dir / f"opencode-job-{slug}.timer"
    svc_text = (
        "[Unit]\n"
        f"Description=OpenCode Job: {slug}\n"
        "[Service]\n"
        "Type=oneshot\n"
        f"WorkingDirectory={job_path.parent}\n"
        f"Environment=\"PATH=/usr/local/bin:/usr/bin:/bin\"\n"
        f"ExecStart={' '.join(cmd)}\n"
        f"StandardOutput=append:{log_path}\n"
        f"StandardError=append:{log_path}\n"
        "[Install]\n"
        "WantedBy=default.target\n"
    )
    timer_text = (
        "[Unit]\n"
        f"Description=Timer for OpenCode Job: {slug}\n"
        "[Timer]\n"
        f"OnCalendar={cal}\n"
        "Persistent=true\n"
        "[Install]\n"
        "WantedBy=timers.target\n"
    )
    try:
        svc.write_text(svc_text, encoding="utf-8")
        timer.write_text(timer_text, encoding="utf-8")
    except Exception:
        for p in (svc, timer):
            try:
                p.unlink(missing_ok=True)
            except OSError:
                pass
        raise
    if not dry:
        _systemctl("daemon-reload")
        _systemctl("enable", "--now", f"opencode-job-{slug}.timer")
    return [f"opencode-job-{slug}.timer"]


def _install_launchd(slug: str, job_path: Path, parsed: dict, dry: bool) -> list[str]:
    """macOS：launchd plist（StartCalendarInterval 精确触发）。"""
    import plistlib
    launch_agents = Path.home() / "Library" / "LaunchAgents"
    launch_agents.mkdir(parents=True, exist_ok=True)
    plist = launch_agents / f"com.wechat-claw.job.{slug}.plist"
    cmd = _supervisor_cmd(job_path)
    payload = {
        "Label": f"com.wechat-claw.job.{slug}",
        "ProgramArguments": cmd,
        "WorkingDirectory": str(job_path.parent),
        "StartCalendarInterval": _launchd_interval(parsed),
        "ProcessType": "Background",
    }
    try:
        with open(plist, "wb") as f:
            plistlib.dump(payload, f)
    except Exception:
        try:
            plist.unlink(missing_ok=True)
        except OSError:
            pass
        raise
    if not dry:
        _launchctl("unload", str(plist))
        _launchctl("load", "-w", str(plist))
    return [plist.name]


def _install_windows_timers(slug: str, job_path: Path, plans: list[dict], dry: bool) -> list[str]:
    """Windows：schtasks 计划任务（列表 cron 拆多任务；/f 幂等覆盖）。"""
    cmd = _supervisor_cmd(job_path)
    # /tr 引号包裹：整条命令在引号内，内部参数引号用 \" 转义
    tr = " ".join(f'"{c}"' if (" " in c or c.endswith(".exe")) else c for c in cmd)
    tasks: list[str] = []
    for i, plan in enumerate(plans):
        tn = f"wechat-claw-job-{slug}-{i + 1}" if len(plans) > 1 else f"wechat-claw-job-{slug}"
        args = ["schtasks", "/create", "/tn", tn, "/tr", tr, "/sc", plan["sc"], "/f"]
        if plan["mo"]:
            args += ["/mo", plan["mo"]]
        if plan["d"]:
            args += ["/d", plan["d"]]
        if plan["m"]:
            args += ["/m", plan["m"]]
        if plan["st"] and plan["sc"] in ("daily", "weekly", "monthly", "hourly"):
            args += ["/st", plan["st"]]
        tasks.append(tn)
        if not dry:
            try:
                subprocess.run(args, capture_output=True, text=True, timeout=30, check=False)
            except Exception as e:
                log.warning("[opencode-jobs] schtasks %s 失败: %s", tn, e)
    return tasks


def _launchctl(*args: str) -> None:
    try:
        subprocess.run(["launchctl", *args], capture_output=True, text=True, timeout=30, check=False)
    except Exception as e:
        log.warning("[opencode-jobs] launchctl %s 失败: %s", " ".join(args), e)


def uninstall_job(module: str, dry: bool = False) -> dict:
    """按 slug 前缀 <模块名>- 精确清理该模块的 job（停定时器 → 删载体 → 删 job.json）。"""
    jd = jobs_dir()
    removed: list[str] = []
    if jd.is_dir():
        for jf in sorted(jd.glob(f"{module}-*.json")):
            slug = jf.stem
            _uninstall_timers(slug, dry=dry)
            jf.unlink()
            removed.append(slug)
    return {"ok": True, "removed": removed}


def _uninstall_timers(slug: str, dry: bool) -> None:
    """按平台清理该 job 的定时器载体。"""
    kind = _platform_kind()
    if kind == "windows":
        # schtasks：按任务名前缀精确删除
        tasks = [f"wechat-claw-job-{slug}", f"wechat-claw-job-{slug}-1"]
        for tn in tasks:
            if dry:
                continue
            try:
                subprocess.run(["schtasks", "/delete", "/tn", tn, "/f"],
                               capture_output=True, text=True, timeout=30, check=False)
            except Exception as e:
                log.warning("[opencode-jobs] schtasks /delete %s 失败: %s", tn, e)
        return
    if kind == "darwin":
        plist = Path.home() / "Library" / "LaunchAgents" / f"com.wechat-claw.job.{slug}.plist"
        if not dry:
            _launchctl("unload", str(plist))
        try:
            plist.unlink(missing_ok=True)
        except OSError:
            pass
        return
    systemd_dir = _systemd_user_dir()
    if not dry:
        _systemctl("stop", f"opencode-job-{slug}.timer")
        _systemctl("disable", f"opencode-job-{slug}.timer")
    for unit in (f"opencode-job-{slug}.timer", f"opencode-job-{slug}.service"):
        up = systemd_dir / unit
        try:
            up.unlink(missing_ok=True)
        except OSError:
            pass
    if not dry:
        _systemctl("daemon-reload")


def list_jobs() -> list[dict]:
    """列出全部 job（含 lastRunStatus 等元数据）。"""
    jd = jobs_dir()
    items: list[dict] = []
    if jd.is_dir():
        for jf in sorted(jd.glob("*.json")):
            try:
                data = json.loads(jf.read_text(encoding="utf-8"))
                items.append({
                    "slug": data.get("slug", jf.stem),
                    "name": data.get("name", ""),
                    "module": data.get("module", ""),
                    "schedule": data.get("schedule", ""),
                    "lastRunStatus": data.get("lastRunStatus"),
                    "lastRunAt": data.get("lastRunAt"),
                    "timeoutSeconds": data.get("timeoutSeconds"),
                })
            except Exception:
                continue
    return items


def _systemctl(*args: str) -> None:
    """执行 systemctl --user（失败仅告警，不中断流程；日志可见，打包形态不依赖 stderr）。"""
    try:
        subprocess.run(
            ["systemctl", "--user", *args],
            check=True,
            capture_output=True,
            text=True,
            timeout=30,
        )
    except Exception as e:
        log.warning("[opencode-jobs] systemctl --user %s 失败: %s", " ".join(args), e)


# ---------- supervisor 执行器（平台定时器到点触发；移植 supervisor.pl） ----------

def _pid_alive(pid: int) -> bool:
    if not pid:
        return False
    try:
        os.kill(pid, 0)
        return True
    except (OSError, ProcessLookupError):
        return False


def _write_json_atomic(path: Path, data: dict) -> None:
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def _supervisor_exec(job_path: str) -> int:
    """执行单个 job（平台定时器触发）。防重锁 → 置 running → 执行 opencode → 状态写回 → jsonl 日志。"""
    import uuid
    job_path_p = Path(job_path)
    job = json.loads(job_path_p.read_text(encoding="utf-8"))
    slug = str(job.get("slug") or "")
    scope = str(job.get("scopeId") or scope_id())
    if not slug:
        return 1
    root = sched_root()
    locks_dir = root / "scopes" / scope / "locks"
    logs_dir = sched_root().parent / "logs" / "scheduler" / scope
    try:
        locks_dir.mkdir(parents=True, exist_ok=True)
        logs_dir.mkdir(parents=True, exist_ok=True)
    except OSError:
        pass
    log_path = logs_dir / f"{slug}.log"
    lock_path = locks_dir / f"{slug}.json"

    def log_event(event: str) -> None:
        try:
            with open(log_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(
                    {"ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "event": event}, ensure_ascii=False) + "\n")
        except OSError:
            pass

    # 防重：running 锁 + pid 存活
    if lock_path.is_file():
        try:
            lock = json.loads(lock_path.read_text(encoding="utf-8"))
            if _pid_alive(int(lock.get("pid") or 0)):
                log_event(f"skipped already running pid={lock.get('pid')}")
                return 0
        except (ValueError, TypeError, OSError):
            pass
        try:
            lock_path.unlink(missing_ok=True)
        except OSError:
            pass

    invocation = job.get("invocation") or {}
    command = str(invocation.get("command") or "")
    if not command:
        _mark_job_state(job_path_p, job, status="failed", error="job missing invocation")
        log_event("job missing invocation")
        return 1

    run_id = f"{int(time.time())}-{uuid.uuid4().hex[:8]}"
    started_at = time.strftime("%Y-%m-%dT%H:%M:%S%z")
    _write_json_atomic(lock_path, {"pid": os.getpid(), "startedAt": started_at, "runId": run_id})
    _mark_job_state(job_path_p, job, status="running", error=None)
    log_event(f"start runId={run_id}")

    cmd = [command] + [str(a) for a in (invocation.get("args") or [])]
    env = os.environ.copy()
    env.update({str(k): str(v) for k, v in (job.get("env") or {}).items()})
    timeout = int(job.get("timeoutSeconds") or 1800)
    try:
        r = subprocess.run(cmd, cwd=str(job.get("workdir") or Path.cwd()), env=env,
                           capture_output=True, text=True, timeout=timeout)
        status = "ok" if r.returncode == 0 else "failed"
        _mark_job_state(job_path_p, job, status=status, exit_code=r.returncode,
                        error=None if r.returncode == 0 else (r.stderr or r.stdout or "")[-500:])
        log_event(f"finish runId={run_id} status={status} rc={r.returncode}")
        return r.returncode
    except subprocess.TimeoutExpired:
        _mark_job_state(job_path_p, job, status="failed", exit_code=-1,
                        error=f"timeout {timeout}s")
        log_event(f"timeout runId={run_id} {timeout}s")
        return 2
    except Exception as e:  # noqa: BLE001
        _mark_job_state(job_path_p, job, status="failed", exit_code=-1, error=str(e))
        log_event(f"error runId={run_id} {e}")
        return 2
    finally:
        try:
            lock_path.unlink(missing_ok=True)
        except OSError:
            pass


def _mark_job_state(job_path: Path, job: dict, status: str, error: str | None,
                    exit_code: int | None = None) -> None:
    """更新 job.json 的 lastRun* 字段（读取最新内容，避免覆盖他人写入）。"""
    try:
        current = json.loads(job_path.read_text(encoding="utf-8"))
    except Exception:
        current = dict(job)
    current["lastRunAt"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")
    current["lastRunSource"] = "scheduled"
    current["lastRunStatus"] = status
    if error is not None:
        current["lastRunError"] = error
    if exit_code is not None:
        current["lastRunExitCode"] = exit_code
    try:
        _write_json_atomic(job_path, current)
    except OSError:
        pass


# ---------- CLI ----------

def main(argv: list[str] | None = None) -> int:
    argv = argv or sys.argv[1:]
    if not argv:
        print(__doc__)
        return 1
    dry = "--dry-run" in argv
    argv = [a for a in argv if a != "--dry-run"]

    if argv[0] == "install" and len(argv) >= 4:
        module, name, schedule, prompt = argv[1], argv[2], argv[3], argv[4] if len(argv) > 4 else ""
        try:
            r = install_job(module, name, schedule, prompt, dry=dry)
        except ValueError as e:
            print(f"[opencode-jobs] {e}")
            return 1
        print(f"[opencode-jobs] job {r['slug']} 已登记（timers={r['timers']}{'，dry 未装' if dry else ''}）")
        return 0

    if argv[0] == "uninstall" and len(argv) >= 2:
        r = uninstall_job(argv[1], dry=dry)
        print(f"[opencode-jobs] 清理 {len(r['removed'])} 个 job: {r['removed'] or '无'}")
        return 0

    if argv[0] == "list":
        for j in list_jobs():
            status = j.get("lastRunStatus") or "未运行"
            print(f"[{status}] {j['slug']}: {j['name']} schedule={j['schedule']}")
        return 0

    if argv[0] == "supervisor" and len(argv) >= 2:
        return _supervisor_exec(argv[1])

    print(f"[opencode-jobs] 未知命令: {argv[0]}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
