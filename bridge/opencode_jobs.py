"""opencode scheduler job 管理（agent 型长任务的定时执行器）＝ wechat-claw 自建 job 调度。

职责（S6 定稿）：
- install_job：写 job.json + 生成 systemd user timer（自建 supervisor.pl 执行，与
  opencode 官方 scheduler 插件互不依赖；数据根 = 数据根/opencode/config/scheduler
  （向导安装收敛形态）或系统默认 opencode 配置域，OPENCODE_SCHED_ROOT 可覆盖）
- uninstall_job：停 timer → 删 timer/service → 删 job.json（按 slug 前缀 <模块名>- 精确清理，不碰他模块）
- list_jobs：列出全部 job（含 lastRunStatus）

约定（定稿）：
- scopeId 固定 "wechat-claw"（不带哈希）；slug = <模块名>-<任务名>
- 模块可选带 job.template.json → 用 install_job 登记为正式 job
- supervisor = vendor/opencode-scheduler/supervisor.pl（perl，生产验证）；ExecStart 路径
  运行时探测（perl）落位，opencode 经 bridge.config.resolve_opencode 解析（与 acp.command 同源）
- 仅 Linux systemd user timer（生产形态）；macOS launchd / Windows 计划任务留待需要时
- 收敛形态（向导安装 opencode）下 systemd 单元注入 XDG_* 到数据根，job 执行的
  opencode 与 ACP 主链路同配置域

隔离：OPENCODE_SCHED_ROOT 环境变量可覆盖运行时根（测试/部署自洽）。
"""
from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
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


def _vendor_supervisor() -> Path:
    """vendor 的 supervisor.pl（只读资源，打包形态随 exe 解包目录提供）。"""
    from bridge.config import RESOURCE_ROOT
    return RESOURCE_ROOT / "vendor" / "opencode-scheduler" / "supervisor.pl"


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
    }


def _perl() -> str:
    """perl 解释器绝对路径（supervisor.pl 执行器）；找不到给出明确错误。"""
    p = shutil.which("perl")
    if p:
        return p
    raise ValueError("未找到 perl 解释器（supervisor.pl 需要），job 登记中止")


# ---------- 生命周期 ----------

def install_job(module: str, name: str, schedule: str, prompt: str,
                timeout: int = 1800, workdir: str | None = None, dry: bool = False) -> dict:
    """登记 job：写 job.json + 生成 systemd user timer（dry=True 只写文件不碰 systemctl）。

    失败语义：二进制缺失/目录不可写/OnCalendar 无法表达 → 抛异常且回滚已写文件，
    不落半成品（调用方明确报错，不静默降级）。
    """
    job = build_job(module, name, schedule, prompt, timeout, workdir)
    slug = job["slug"]
    cal = _cron_to_oncalendar(schedule)
    if cal is None:
        raise ValueError(f"[opencode-jobs] schedule {schedule!r} 无法转为 systemd OnCalendar")

    jd = jobs_dir()
    if not jd.exists() or not os.access(jd, os.W_OK):
        jd.mkdir(parents=True, exist_ok=True)
        if not os.access(jd, os.W_OK):
            raise OSError(f"job 目录不可写: {jd}")

    systemd_dir = _systemd_user_dir()
    systemd_dir.mkdir(parents=True, exist_ok=True)

    # 收敛形态：systemd 单元注入 XDG_*（job 执行的 opencode 与 ACP 主链路同配置域）
    from bridge.config import xdg_env
    xdgs = xdg_env()
    xdg_lines = "".join(
        f"Environment=\"{k}={v}\"\n" for k, v in sorted(xdgs.items())
    ) if xdgs else ""

    job_path = jd / f"{slug}.json"
    supervisor = _vendor_supervisor()
    perl = _perl()
    log_path = sched_root().parent / "logs" / "scheduler" / scope_id() / f"{slug}.log"
    svc = systemd_dir / f"opencode-job-{slug}.service"
    timer = systemd_dir / f"opencode-job-{slug}.timer"
    svc_text = (
        "[Unit]\n"
        f"Description=OpenCode Job: {job['name']}\n"
        "[Service]\n"
        "Type=oneshot\n"
        f"WorkingDirectory={job['workdir']}\n"
        f"Environment=\"PATH=/usr/local/bin:/usr/bin:/bin\"\n"
        f"{xdg_lines}"
        f"ExecStart={perl} {supervisor} {job_path}\n"
        f"StandardOutput=append:{log_path}\n"
        f"StandardError=append:{log_path}\n"
        "[Install]\n"
        "WantedBy=default.target\n",
    )
    timer_text = (
        "[Unit]\n"
        f"Description=Timer for OpenCode Job: {job['name']}\n"
        "[Timer]\n"
        f"OnCalendar={cal}\n"
        "Persistent=true\n"
        "[Install]\n"
        "WantedBy=timers.target\n"
    )

    written: list[Path] = []
    try:
        tmp = job_path.with_name(job_path.name + ".tmp")
        tmp.write_text(json.dumps(job, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        os.replace(tmp, job_path)
        written.append(job_path)
        svc.write_text(svc_text, encoding="utf-8")
        written.append(svc)
        timer.write_text(timer_text, encoding="utf-8")
        written.append(timer)
    except Exception:
        for p in written:
            try:
                p.unlink(missing_ok=True)
            except OSError:
                pass
        raise

    if not dry:
        _systemctl("daemon-reload")
        _systemctl("enable", "--now", f"opencode-job-{slug}.timer")
    return {"ok": True, "slug": slug, "job_path": str(job_path),
            "timer": f"opencode-job-{slug}.timer", "on_calendar": cal}


def uninstall_job(module: str, dry: bool = False) -> dict:
    """按 slug 前缀 <模块名>- 精确清理该模块的 job（停 timer → 删 timer/service → 删 job.json）。"""
    jd = jobs_dir()
    removed: list[str] = []
    systemd_dir = _systemd_user_dir()
    if jd.is_dir():
        for jf in sorted(jd.glob(f"{module}-*.json")):
            slug = jf.stem
            if not dry:
                _systemctl("stop", f"opencode-job-{slug}.timer")
                _systemctl("disable", f"opencode-job-{slug}.timer")
            for unit in (f"opencode-job-{slug}.timer", f"opencode-job-{slug}.service"):
                up = systemd_dir / unit
                if up.exists():
                    up.unlink()
            jf.unlink()
            removed.append(slug)
    if removed and not dry:
        _systemctl("daemon-reload")
    return {"ok": True, "removed": removed}


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
        print(f"[opencode-jobs] job {r['slug']} 已登记（OnCalendar={r['on_calendar']}{'，dry 未装 timer' if dry else ''}）")
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

    print(f"[opencode-jobs] 未知命令: {argv[0]}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
