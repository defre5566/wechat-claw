"""模块管理器（唯一入口）：注册/更新/启停/列表/卸载。

- 函数库形态：web 后端 import 调用；CLI 形态：python3 modules/register.py ...
- module.json 是唯一落盘（enabled/调度/retry/token 均在此）；build_index 实时扫描
- enabled 语义：缺失或 false = 关闭（不调度、不推送、不进 index）；显式 true = 启用；
  新注册默认写 false（手动启用后才运行）

用法（CLI）:
  python3 modules/register.py <name> [--purpose 用途] [--spec 规范] \
      [--schedule-json 'json'] [--retry-json 'json|null']
  python3 modules/register.py --enable <name> | --disable <name> | --list | --uninstall <name>
  python3 modules/register.py --reissue-token <name>   # 仅 token 缺失时补发（H8）
"""
from __future__ import annotations

import json
import secrets
import shutil
import sys
from pathlib import Path

MODULES_DIR = Path(__file__).resolve().parent
# CLI 直接运行（python3 modules/register.py）时 sys.path[0]=modules/，
# 需把项目根加进 sys.path，否则 `from modules.registry_index import invalidate` 找不到包
if str(MODULES_DIR.parent) not in sys.path:
    sys.path.insert(0, str(MODULES_DIR.parent))

DATA_ROOT = MODULES_DIR / "modules_data"  # 模块用户数据根（代码/数据分家）


def module_data_dir(name: str) -> Path:
    """模块用户数据目录 modules/modules_data/<name>/（设置 + 业务数据）。"""
    return DATA_ROOT / name


# ---------- 内部 IO ----------

def _load_module_json(name: str) -> dict:
    mj = MODULES_DIR / name / "module.json"
    if mj.is_file():
        try:
            data = json.loads(mj.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return data
        except Exception:
            pass
    return {}


def _load_settings_json(name: str) -> dict:
    """读数据区 settings.json（用户配置值；不存在返回 {}）。"""
    sf = module_data_dir(name) / "settings.json"
    if sf.is_file():
        try:
            data = json.loads(sf.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return data
        except Exception:
            pass
    return {}


def _save_settings_json(name: str, settings: dict) -> bool:
    """原子写数据区 settings.json。"""
    try:
        dd = module_data_dir(name)
        dd.mkdir(parents=True, exist_ok=True)
        sf = dd / "settings.json"
        tmp = sf.with_name(sf.name + ".tmp")
        tmp.write_text(json.dumps(settings, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        tmp.replace(sf)
        return True
    except OSError:
        return False


# ---------- schedule_from_settings 联动（设置时间字段 → 调度 cron） ----------

def _time_to_cron(t) -> str | None:
    """'HH:MM' → cron 'MM HH * * *'；非法返回 None。"""
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


def _sync_schedule_from_settings(name: str) -> None:
    """schedule_from_settings 联动：按设置时间字段生成 cron 写回 module.json schedule。

    module.json 声明：`"schedule_from_settings": [{"phase": "morning", "time_field": "morning_time", "enabled_field": "planner_on"}]`
    - time_field：设置中的时刻（HH:MM）→ cron
    - enabled_field：设置中的总开关（false → 该项不生成调度）
    """
    data = _load_module_json(name)
    sfs = data.get("schedule_from_settings")
    if not isinstance(sfs, list) or not sfs:
        return
    settings = _load_settings_json(name)
    schedule: list[dict] = []
    for item in sfs:
        if not isinstance(item, dict):
            continue
        phase = str(item.get("phase", ""))
        tf = str(item.get("time_field", ""))
        ef = item.get("enabled_field")
        if ef and settings.get(ef) is False:
            continue
        cron = _time_to_cron(settings.get(tf))
        if not cron:
            continue
        entry: dict = {"id": phase or tf, "cron": cron}
        if phase:
            entry["args"] = ["--phase", phase]
        schedule.append(entry)
    data["schedule"] = schedule
    _save_module_json(name, data)


def _save_module_json(name: str, data: dict) -> bool:
    try:
        mj = MODULES_DIR / name / "module.json"
        mj.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return True
    except OSError:
        return False


def module_exists(name: str) -> bool:
    return (MODULES_DIR / name / "module.json").is_file()


# ---------- 函数库 ----------

def register_module(
    name: str,
    purpose: str = "",
    spec: str = "规范.md",
    schedule: list | None = None,
    retry=None,
) -> dict:
    """注册新模块：建目录 + 生成 token（0600）+ 写 module.json（enabled 默认 false）。

    仅用于"新注册"（模块不存在）；已存在模块改配置走 update_module（不换 token，G1）。
    """
    mod_dir = MODULES_DIR / name
    mod_dir.mkdir(parents=True, exist_ok=True)
    module_data_dir(name).mkdir(parents=True, exist_ok=True)  # 数据区（设置/业务数据）

    token = secrets.token_hex(32)
    token_file = mod_dir / "token"
    # O_CREAT|O_EXCL + 0600：原子创建，避免"先写后 chmod"短暂 644 窗口
    import os
    fd = os.open(str(token_file), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(token)

    data = _load_module_json(name)
    data.update({
        "name": name,
        "purpose": purpose,
        "spec": spec,
        "enabled": False,  # 新注册默认关闭，手动启用后才运行
    })
    if schedule is not None:
        data["schedule"] = schedule
    if retry is not None:
        data["retry"] = retry
    data.setdefault("schedule", [])
    data.setdefault("retry", None)
    ok = _save_module_json(name, data)
    if ok:
        from modules.registry_index import invalidate
        invalidate()  # H2：注册后清索引缓存，下个 build_index 立即生效
    return {"ok": ok, "name": name, "enabled": False, "token_file": str(token_file)}


def update_module(
    name: str,
    purpose: str | None = None,
    spec: str | None = None,
    schedule: list | None = None,
    retry=None,
    retry_set: bool = False,
    settings: dict | None = None,
) -> bool:
    """保留字段式更新 module.json（已存在模块改配置；不碰 token/enabled，G1）。

    settings：模块设置（settings_schema 对应的当前值），仅显式传入时更新；
             值存数据区 modules_data/<name>/settings.json（module.json 只留声明，升级不丢），
             保存后刷新豁免（settings 中 type=path 字段自动豁免，如 Obsidian vault_path）。
    """
    if not module_exists(name):
        return False
    data = _load_module_json(name)
    if purpose is not None:
        data["purpose"] = purpose
    if spec is not None:
        data["spec"] = spec
    if schedule is not None:
        data["schedule"] = schedule
    if retry_set:
        data["retry"] = retry
    ok = _save_module_json(name, data)
    if settings is not None:
        ok_s = _save_settings_json(name, settings)
        ok = ok and ok_s
        if ok_s:
            _sync_schedule_from_settings(name)  # 时间设置 → 调度 cron 联动
            from bridge.jobs import sync_module_jobs
            rj = sync_module_jobs(name)  # agent 型长任务：渲染 + 自动登记/注销（无声明跳过）
            if not rj.get("ok"):
                from modules.common.log import log_event
                log_event("WARN", name, "job_sync_fail", rj.get("error", "job 联动失败"))
            from bridge.permissions import refresh_permissions
            refresh_permissions()  # 设置驱动豁免（vault_path 自动放行）
    if ok:
        from modules.registry_index import invalidate
        invalidate()  # H2：更新后清索引缓存
    return ok


def set_enabled(name: str, enabled: bool) -> bool:
    """唯一启停入口：写 module.json 的 enabled 字段。"""
    if not module_exists(name):
        return False
    data = _load_module_json(name)
    data["enabled"] = bool(enabled)
    ok = _save_module_json(name, data)
    if ok:
        if not enabled:
            from bridge.jobs import unregister_jobs
            unregister_jobs(name)  # 停用 → 注销该模块全部 agent job（无声明无副作用）
        from modules.registry_index import invalidate
        invalidate()
    return ok


def get_module(name: str) -> dict | None:
    """读单个模块配置（含 enabled 与 settings 值；不存在返回 None）。

    settings 值从数据区 settings.json 读（module.json 只留声明）。
    """
    if not module_exists(name):
        return None
    data = _load_module_json(name)
    data["enabled"] = bool(data.get("enabled", False))
    sv = _load_settings_json(name)
    if sv:
        data["settings"] = sv
    return data


def list_modules() -> list[dict]:
    """全部模块（含禁用）：name/enabled/purpose/schedule/retry。"""
    items = []
    for mod_dir in sorted(MODULES_DIR.iterdir()):
        if not mod_dir.is_dir():
            continue
        mj = mod_dir / "module.json"
        if not mj.is_file():
            continue
        name = mod_dir.name
        data = _load_module_json(name)
        items.append({
            "name": name,
            "purpose": data.get("purpose", ""),
            "schedule": data.get("schedule", []),
            "retry": data.get("retry"),
            "enabled": bool(data.get("enabled", False)),
        })
    return items


def uninstall(name: str, keep_data: bool = True) -> bool:
    """卸载模块：删除模块代码目录（文件 + token + module.json）。

    keep_data=True（默认）：保留 modules_data/<name>/（用户数据，重装可沿用）；
    keep_data=False：连数据目录一起删（不可恢复）。
    """
    mod_dir = MODULES_DIR / name
    if not mod_dir.is_dir():
        return False
    try:
        shutil.rmtree(mod_dir)
        if not keep_data:
            dd = module_data_dir(name)
            if dd.is_dir():
                shutil.rmtree(dd)
        # G5：同步清调度记录本上该模块分区（cron 字符串键永不 prune，卸载必须主动清，
        # 否则同名重装会复用旧 done_key 跳过当日）
        from bridge.state import load_sched_state, save_sched_state
        state = load_sched_state()
        if name in state:
            del state[name]
            save_sched_state(state)
        from bridge.permissions import refresh_permissions
        refresh_permissions()  # 卸载后撤销该模块豁免
        from bridge.jobs import unregister_jobs
        unregister_jobs(name)  # 卸载 → 注销该模块 agent job（无声明无副作用）
        from modules.registry_index import invalidate
        invalidate()
        return True
    except OSError:
        return False


def reissue_token(name: str) -> bool:
    """补发模块 token（H8）：仅当 token 文件缺失时生成新 token；存在则拒绝。

    守住 G1"模块存在期间不轮换 token"——只有"卡丢了"才补一张。
    """
    if not module_exists(name):
        print(f"[register] 模块不存在: {name}")
        return False
    token_file = MODULES_DIR / name / "token"
    if token_file.exists():
        print(f"[register] {name} 的 token 已存在，不轮换（仅 token 缺失时才补发）")
        return False
    import os
    token = secrets.token_hex(32)
    fd = os.open(str(token_file), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(token)
    from modules.registry_index import invalidate
    invalidate()
    print(f"[register] {name} 已补发 token（0600）")
    return True


# ---------- CLI ----------

def main(argv: list[str] | None = None) -> int:
    argv = argv or sys.argv[1:]
    if not argv:
        print(__doc__)
        return 1

    if argv[0] == "--list":
        for m in list_modules():
            state = "启用" if m["enabled"] else "关闭"
            print(f"[{state}] {m['name']}: {m['purpose'] or '（无用途）'} schedule={m['schedule']!r}")
        return 0

    if argv[0] in ("--enable", "--disable") and len(argv) >= 2:
        name = argv[1]
        if set_enabled(name, argv[0] == "--enable"):
            print(f"[register] {name} 已{'启用' if argv[0] == '--enable' else '关闭'}")
            return 0
        print(f"[register] 模块不存在: {name}")
        return 1

    if argv[0] == "--uninstall" and len(argv) >= 2:
        keep_data = "--purge-data" not in argv
        if uninstall(argv[1], keep_data=keep_data):
            if keep_data:
                print(f"[register] {argv[1]} 已卸载（用户数据保留在 modules_data/{argv[1]}/，重装可沿用）")
            else:
                print(f"[register] {argv[1]} 已卸载（含用户数据，已彻底删除）")
            return 0
        print(f"[register] 卸载失败: {argv[1]}")
        return 1

    if argv[0] == "--permissions":
        from bridge.permissions import collect_permissions, apply_permissions
        perms = collect_permissions()
        apply_permissions(perms)
        for op in ("edit", "write"):
            print(f"[register] {op}:")
            for p in sorted(perms.get(op, {})):
                print(f"    {p}: allow")
        print("[register] 已写 .config/module-permissions.json（合并进 opencode.jsonc 的 permission.edit/write 生效）")
        return 0

    if argv[0] == "--reissue-token" and len(argv) >= 2:
        return 0 if reissue_token(argv[1]) else 1

    if argv[0] == "--sync-jobs" and len(argv) >= 2:
        from bridge.jobs import sync_jobs
        r = sync_jobs(argv[1])
        if not r["ok"]:
            print(f"[register] sync-jobs: {r.get('error', '失败')}")
            return 1
        print(f"[register] {argv[1]} job 已渲染: {r['job']['title']} @ {r['job']['schedule']}")
        print(r.get("install_hint", ""))
        return 0

    # 注册/更新（G1 分流：已存在模块 → update_module 不换 token；新模块 → register_module 发卡）
    name = argv[0]
    opts: dict[str, str] = {}
    i = 1
    while i < len(argv):
        if argv[i] in ("--purpose", "--spec", "--schedule-json", "--retry-json") and i + 1 < len(argv):
            opts[argv[i][2:]] = argv[i + 1]
            i += 2
        else:
            i += 1

    if module_exists(name):
        ok = update_module(
            name,
            purpose=opts.get("purpose"),
            spec=opts.get("spec"),
            schedule=json.loads(opts["schedule-json"]) if "schedule-json" in opts else None,
            retry=json.loads(opts["retry-json"]) if "retry-json" in opts else None,
            retry_set="retry-json" in opts,
        )
        if not ok:
            print(f"[register] 更新失败: {name}")
            return 1
        print(f"[register] {name} 已更新（token 不变，enabled 保持原状）")
        return 0

    result = register_module(
        name,
        purpose=opts.get("purpose", ""),
        spec=opts.get("spec", "规范.md"),
        schedule=json.loads(opts["schedule-json"]) if "schedule-json" in opts else None,
        retry=json.loads(opts["retry-json"]) if "retry-json" in opts else None,
    )
    if not result["ok"]:
        print(f"[register] 注册失败: {name}")
        return 1
    print(f"[register] {name}: token -> {result['token_file']} (0600 已生成)")
    print(f"[register] enabled=false（默认关闭，--enable 开启后才会被调度）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
