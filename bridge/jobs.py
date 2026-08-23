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
import logging
import os
import re
import sys
from datetime import date
from pathlib import Path

from bridge.config import MODULES_ROOT
from modules.common.io import load_json, time_to_cron

log = logging.getLogger("wechat-bridge")

MODULES_DIR = MODULES_ROOT
DATA_ROOT = MODULES_DIR / "modules_data"
# 测试隔离点（与 permissions/module_source 同源）
if os.environ.get("OPENCODE_PERMS_ROOT"):
    _root = Path(os.environ["OPENCODE_PERMS_ROOT"])
    MODULES_DIR = _root / "modules"
    DATA_ROOT = MODULES_DIR / "modules_data"


def module_data_dir(name: str) -> Path:
    return DATA_ROOT / name


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


# 通用占位符正则：模板可引用模块设置任意键（如 {settings:briefing_topics}）
_SETTINGS_PLACEHOLDER = re.compile(r"\{settings:([A-Za-z_][A-Za-z0-9_]*)\}")


def _apply_placeholders(text: str, ctx: dict, settings: dict) -> str:
    """替换通用占位符：{date}/{module_data}/{output_path}/{settings:<key>}/{custom_prompts}。

    缺键兜底（置空不报错）；custom_prompts 段由调用方放入 ctx。
    """
    out = (text
           .replace("{date}", str(ctx.get("date") or ""))
           .replace("{module_data}", str(ctx.get("module_data") or ""))
           .replace("{output_path}", str(ctx.get("output_path") or ""))
           .replace("{custom_prompts}", str(ctx.get("custom_prompts") or "").strip(" \n")))

    def _sub(match: re.Match) -> str:
        key = match.group(1)
        val = settings.get(key)
        if isinstance(val, list):
            return "、".join(str(x) for x in val)
        return str(val) if val is not None else ""

    return _SETTINGS_PLACEHOLDER.sub(_sub, out)


def _load_custom_prompts(data_dir: Path) -> str:
    """用户自定义方向 prompt（web 导入，模块数据区 prompts/custom/*.json）→ 文本段。

    通用用户数据机制（任何模块可用）；无自定义返回空。
    """
    custom_dir = data_dir / "prompts" / "custom"
    if not custom_dir.is_dir():
        return ""
    parts: list[str] = []
    for f in sorted(custom_dir.glob("*.json")):
        c = load_json(f)
        if isinstance(c, dict) and c.get("prompt"):
            parts.append(f"[用户自定义方向 {f.stem}]\n{c['prompt']}")
    return "\n\n".join(parts)


def _load_encrypted_template(mod_dir: Path) -> str | None:
    """模块任务模板：明文 base.prompt.md 优先（模块源现行规范）。

    兼容旧：base.prompt.enc（作者加密）存在时尝试解密——跨机密钥不同（部署机
    crypto.key 随机生成）解密几乎必然失败，失败按缺失处理（不再拒绝登记）。
    """
    plain = mod_dir / "prompts" / "base.prompt.md"
    if plain.is_file():
        try:
            txt = plain.read_text(encoding="utf-8")
            return txt.strip() or None
        except Exception as e:
            log.warning("[jobs] base.prompt.md 读取失败: %s", e)
            return None
    enc = mod_dir / "prompts" / "base.prompt.enc"
    if not enc.is_file():
        return None
    try:
        from modules.common.crypto import decrypt
        text = decrypt(enc.read_text(encoding="utf-8"))
        return str(text).strip() or None
    except Exception as e:
        log.warning("[jobs] base.prompt.enc 解密失败（按缺失处理）: %s", e)
        return None


def _compose_prompt(mod_dir: Path, data_dir: Path, settings: dict, template: dict,
                    output_path: str | None = None) -> str | None:
    """合成 job prompt（通用模板合成，不含任何模块业务知识）。

    指示来源二选一（模块作者必须提供其一，否则拒绝登记）：
    - job.template.json 的 `prompt` 字段（明文，支持通用占位符
      {date}/{module_data}/{output_path}/{settings:<键>}/{custom_prompts}）
    - 模块自带加密模板 prompts/base.prompt.enc（引擎解密；其后自动追加用户
      自定义方向段——加密模板内无法写占位符，故由引擎代拼）
    用户自定义方向段（web 导入的 prompts/custom/*.json）在两条路径下都会并入。
    """
    tpl_prompt = template.get("prompt")
    if tpl_prompt and str(tpl_prompt).strip():
        ctx = {
            "date": date.today().isoformat(),
            "module_data": str(data_dir),
            "output_path": str(output_path or ""),
            "custom_prompts": _load_custom_prompts(data_dir),
        }
        return _apply_placeholders(str(tpl_prompt), ctx, settings)
    base = _load_encrypted_template(mod_dir)
    if base is None:
        return None
    custom = _load_custom_prompts(data_dir)
    if custom:
        base = base.rstrip() + "\n\n" + custom
    ctx = {
        "date": date.today().isoformat(),
        "module_data": str(data_dir),
        "output_path": str(output_path or ""),
        "custom_prompts": "",
    }
    return _apply_placeholders(base, ctx, settings)


def render_job(name: str) -> dict:
    """渲染模块 job（存在声明时）：{ok, job?, error?, systemd?}。"""
    mod_dir = MODULES_DIR / name
    data_dir = module_data_dir(name)
    mj = load_json(mod_dir / "module.json") or {}
    job_file = mj.get("job_template")
    if isinstance(job_file, str):
        jt = load_json(mod_dir / job_file)          # 字符串声明 = 相对模块目录
    else:
        jt = load_json(job_file or (mod_dir / "job.template.json"))
    if not jt:
        return {"ok": False, "error": "模块无 job.template.json 声明（非 agent 型模块）"}

    settings = load_json(data_dir / "settings.json") or {}
    sfs = mj.get("schedule_from_settings") or []

    # 触发时刻：对应 phase 的时刻 - offset_min
    cron = None
    for item in sfs:
        if isinstance(item, dict) and item.get("phase") == jt.get("phase"):
            tf = str(item.get("time_field", ""))
            c = time_to_cron(settings.get(tf))
            if c:
                cron = _cron_minus(c, int(jt.get("offset_min", 0)))
            break
    if not cron:
        return {"ok": False, "error": "无法由 settings 计算触发时刻（缺 schedule_from_settings 或时刻字段）"}

    output_dir = data_dir / jt.get("output_dir", "briefing")
    prompt = _compose_prompt(mod_dir, data_dir, settings, jt, output_path=str(output_dir))
    if not prompt:
        return {"ok": False,
                "error": "job 缺少任务指示：job.template.json 需写清 prompt 或提供 prompts/base.prompt.enc"}

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
    mj = load_json(mod_dir / "module.json") or {}
    jt_ref = mj.get("job_template")
    if not jt_ref:
        return {"ok": True, "skipped": True}
    if isinstance(jt_ref, str):
        jt = load_json(mod_dir / jt_ref) or {}     # 字符串声明 = 相对模块目录
    else:
        jt = load_json(jt_ref) or {}
    phase = str(jt.get("phase", ""))
    settings = load_json(module_data_dir(name) / "settings.json") or {}
    for item in mj.get("schedule_from_settings") or []:
        if isinstance(item, dict) and item.get("phase") == phase:
            ef = item.get("enabled_field")
            if ef and settings.get(ef) is False:
                return unregister_jobs(name)
            break
    return sync_jobs(name)
