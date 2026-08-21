"""模块 agent 型长任务（job）注册联动：job.template.json + settings → job json + 自动登记。

职责（register --sync-jobs / update_module 调用）：
- 读模块 job.template.json + 数据区 settings → 渲染 job json（触发时刻 = 对应 phase 时刻 - offset_min；
  prompt = 基底（预设 decrypt + 占位符替换）+ 所选方向参数 + 用户自定义 prompt）
- 生成 systemd user 单元文本（timer + service）到模块数据区 jobs/（审计留存）
- **自动登记**到 opencode scheduler（bridge.opencode_jobs.install_job：写 job.json + systemd 单元 + systemctl enable）；
  登记失败明确报错，不静默降级

占位符（基底 prompt 内）：{date}=生成日 / {word_limit}=所选方向字数之和 / {categories}=方向分类。
"""
from __future__ import annotations

import json
import os
import sys
from datetime import date
from pathlib import Path

from bridge.config import MODULES_ROOT

MODULES_DIR = MODULES_ROOT
DATA_ROOT = MODULES_DIR / "modules_data"
# 测试隔离点（与 permissions/module_source 同源）
if os.environ.get("OPENCODE_PERMS_ROOT"):
    _root = Path(os.environ["OPENCODE_PERMS_ROOT"])
    MODULES_DIR = _root / "modules"
    DATA_ROOT = MODULES_DIR / "modules_data"


def module_data_dir(name: str) -> Path:
    return DATA_ROOT / name


def _load_json(path: Path) -> dict | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except Exception:
        return None


def _cron_minus(cron: str, offset_min: int) -> str | None:
    """cron 'MM HH * * *' 前移 offset_min 分钟；非法返回 None。"""
    parts = cron.split()
    if len(parts) < 2:
        return None
    try:
        total = int(parts[0]) + int(parts[1]) * 60 - int(offset_min)
        total %= 24 * 60
        return f"{total % 60} {total // 60} * * *"
    except Exception:
        return None


def _apply_placeholders(text: str, ctx: dict) -> str:
    """替换 prompt 占位符：{date}/{word_limit}/{categories}；缺省兜底不报错。

    {output_path} 不在此替换（render_job 层按模块数据区 output_dir 注入）。
    """
    return (text
            .replace("{date}", str(ctx.get("date") or ""))
            .replace("{word_limit}", str(ctx.get("word_limit") or 1000))
            .replace("{categories}", str(ctx.get("categories") or "")))


def _compose_prompt(mod_dir: Path, data_dir: Path, settings: dict, template: dict) -> str:
    """合成 job prompt：基底（预设 decrypt + 占位符替换）→ 所选方向参数 → 用户自定义 prompt。

    占位符上下文：word_limit = 所选方向 word_limit 之和（默认 1000）；
    categories = 有分类的方向分行拼接（如时政四分类）。
    """
    topics = settings.get("briefing_topics") or []
    if not isinstance(topics, list):
        topics = []
    directions = _load_json(mod_dir / "directions.json") or {}
    selected = {t: directions[t] for t in topics if isinstance(directions.get(t), dict)}

    # 占位符上下文
    word_limit = sum(int(d.get("word_limit") or 0) for d in selected.values()) or 1000
    cat_lines = [f"{t}：{'/'.join(map(str, d.get('categories') or []))}"
                 for t, d in selected.items() if d.get("categories")]
    ctx = {
        "date": date.today().isoformat(),
        "word_limit": word_limit,
        "categories": "\n".join(cat_lines),
    }

    parts: list[str] = []

    # 1. 基底（预设 prompts/*.enc，crypto 解密）+ 占位符替换
    prompts_dir = mod_dir / "prompts"
    base_enc = prompts_dir / "base.prompt.enc"
    if base_enc.is_file():
        try:
            from modules.common.crypto import decrypt
            parts.append(_apply_placeholders(decrypt(base_enc.read_text(encoding="utf-8")), ctx))
        except Exception:
            parts.append("（基底 prompt 解密失败，按通用信息简报流程生成）")

    # 2. 所选方向关键词（directions.json 预设，只读）
    if selected:
        kw_lines: list[str] = []
        for t, d in selected.items():
            kws = d.get("keywords", [])
            if isinstance(kws, list) and kws:
                kw_lines.append(f"- {t}：{'、'.join(map(str, kws))}")
        if kw_lines:
            parts.append("本次简报方向与关键词（合并执行）：\n" + "\n".join(kw_lines))

    # 3. 用户自定义 prompt（custom/*.json，同方向名覆盖）
    custom_dir = data_dir / "prompts" / "custom"
    for t in topics:
        cf = custom_dir / f"{t}.json"
        if cf.is_file():
            c = _load_json(cf)
            if isinstance(c, dict) and c.get("prompt"):
                parts.append(f"[用户自定义方向 {t}]\n{c['prompt']}")

    return "\n\n".join(parts) if parts else template.get("fallback_prompt", "")


def render_job(name: str) -> dict:
    """渲染模块 job（存在声明时）：{ok, job?, error?, systemd?}。"""
    mod_dir = MODULES_DIR / name
    data_dir = module_data_dir(name)
    mj = _load_json(mod_dir / "module.json") or {}
    job_file = mj.get("job_template")
    if isinstance(job_file, str):
        jt = _load_json(mod_dir / job_file)          # 字符串声明 = 相对模块目录
    else:
        jt = _load_json(job_file or (mod_dir / "job.template.json"))
    if not jt:
        return {"ok": False, "error": "模块无 job.template.json 声明（非 agent 型模块）"}

    settings = _load_json(data_dir / "settings.json") or {}
    sfs = mj.get("schedule_from_settings") or []

    # 触发时刻：对应 phase 的时刻 - offset_min
    cron = None
    for item in sfs:
        if isinstance(item, dict) and item.get("phase") == jt.get("phase"):
            tf = str(item.get("time_field", ""))
            c = _time_to_cron(settings.get(tf))
            if c:
                cron = _cron_minus(c, int(jt.get("offset_min", 0)))
            break
    if not cron:
        return {"ok": False, "error": "无法由 settings 计算触发时刻（缺 schedule_from_settings 或时刻字段）"}

    prompt = _compose_prompt(mod_dir, data_dir, settings, jt)
    output_dir = data_dir / jt.get("output_dir", "briefing")
    prompt = prompt.replace("{output_path}", str(output_dir))  # 基底输出路径占位符

    job = {
        "name": jt.get("name", f"{name} job"),
        "title": jt.get("title", f"{name}-job"),
        "slug": jt.get("slug", f"{name}-job"),
        "phase": jt.get("phase", ""),
        "schedule": cron,
        "timeoutSeconds": jt.get("timeoutSeconds", 1800),
        "output_dir": str(output_dir),
        "workdir": jt.get("workdir"),
        "prompt": prompt,
    }
    jobs_dir = data_dir / "jobs"
    jobs_dir.mkdir(parents=True, exist_ok=True)
    job_path = jobs_dir / f"{job['slug']}.json"
    tmp = job_path.with_name(job_path.name + ".tmp")
    tmp.write_text(json.dumps(job, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(job_path)

    systemd = _systemd_units(name, job)
    return {"ok": True, "job": job, "job_file": str(job_path), "systemd": systemd}


def _time_to_cron(t) -> str | None:
    if not isinstance(t, str):
        return None
    try:
        hh, mm = t.strip().split(":")
        h, m = int(hh), int(mm)
        if not (0 <= h < 24 and 0 <= m < 60):
            return None
        return f"{m} {h} * * *"
    except Exception:
        return None


def _systemd_units(name: str, job: dict) -> dict:
    """生成 systemd user 单元文本（timer + service，参照生产 supervisor 模式）。"""
    slug = job["slug"]
    supervisor = "/usr/bin/opencode"  # agent 执行器（可配置）
    timer = (
        "[Unit]\n"
        f"Description=Timer for {name} job: {job['name']}\n\n"
        "[Timer]\n"
        f"OnCalendar=*-*-* {job['schedule'].split()[1]}:{job['schedule'].split()[0]}:00\n"
        "Persistent=true\n\n"
        "[Install]\n"
        "WantedBy=timers.target\n"
    )
    service = (
        "[Unit]\n"
        f"Description={name} job: {job['name']}\n\n"
        "[Service]\n"
        "Type=oneshot\n"
        f"ExecStart={supervisor} run --title {slug} -- {job['slug']}\n"
        f"Environment=PLANNER_JOB_FILE={job['output_dir']}/../jobs/{slug}.json\n"
        "StandardOutput=journal\n"
        "StandardError=journal\n"
    )
    return {"timer": timer, "service": service}


def sync_jobs(name: str) -> dict:
    """register --sync-jobs 入口：渲染 + 写 job json/systemd 单元（数据区审计留存）+ 自动登记。

    自动登记 = bridge.opencode_jobs.install_job（写 scheduler job.json + systemd 单元 +
    systemctl enable）。登记失败返回 error（调用方明确提示，不静默降级）。
    """
    r = render_job(name)
    if not r["ok"]:
        return r
    jobs_dir = module_data_dir(name) / "jobs"
    for ext, text in r["systemd"].items():
        (jobs_dir / f"{r['job']['slug']}.{ext}").write_text(text, encoding="utf-8")

    from bridge.opencode_jobs import install_job as _install_job
    try:
        inst = _install_job(
            module=name,
            name=r["job"]["title"],
            schedule=r["job"]["schedule"],
            prompt=r["job"]["prompt"],
            timeout=r["job"]["timeoutSeconds"],
            workdir=r["job"].get("workdir"),
        )
    except Exception as e:
        return {"ok": False, "error": f"opencode scheduler 登记失败: {e}",
                "job": r["job"], "job_file": r["job_file"]}

    r["install_hint"] = (
        f"job json（数据区留存）: {r['job_file']}\n"
        f"已自动登记 opencode scheduler: {inst['slug']}（OnCalendar={inst['on_calendar']}）"
    )
    return r


def unregister_jobs(name: str) -> dict:
    """卸载/停用联动：按 slug 前缀 <模块名>- 精确注销该模块全部 job（不碰他模块）。"""
    from bridge.opencode_jobs import uninstall_job as _uninstall_job
    return _uninstall_job(name)


def sync_module_jobs(name: str) -> dict:
    """register 保存设置后的统一联动入口（通用，任何带 job_template 的模块可用）。

    - 模块无 job_template 声明 → 跳过（skipped=True，不打扰非 agent 型模块）
    - 声明了但对应 phase 的 enabled_field=false（如 planner_on=false）→ 注销 job
    - 否则 → 渲染 + 自动登记（sync_jobs）
    """
    mod_dir = MODULES_DIR / name
    mj = _load_json(mod_dir / "module.json") or {}
    jt_ref = mj.get("job_template")
    if not jt_ref:
        return {"ok": True, "skipped": True}
    if isinstance(jt_ref, str):
        jt = _load_json(mod_dir / jt_ref) or {}     # 字符串声明 = 相对模块目录
    else:
        jt = _load_json(jt_ref) or {}
    phase = str(jt.get("phase", ""))
    settings = _load_json(module_data_dir(name) / "settings.json") or {}
    for item in mj.get("schedule_from_settings") or []:
        if isinstance(item, dict) and item.get("phase") == phase:
            ef = item.get("enabled_field")
            if ef and settings.get(ef) is False:
                return unregister_jobs(name)
            break
    return sync_jobs(name)
