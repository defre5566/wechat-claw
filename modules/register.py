"""模块管理器（唯一入口）：注册/更新/启停/列表/卸载。

- 函数库形态：web 后端 import 调用；CLI 形态：python3 modules/register.py ...
- 部署状态（enabled/retry/auto_update）与业务设置统一落数据区 settings.json
  （module.json 只留声明与调度产物；旧版 docstring 的"唯一落盘"表述已过时）
- build_index 实时扫描 module.json + settings.json 判定 enabled
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

from bridge.config import MODULES_ROOT  # noqa: E402  （sys.path 就绪后导入）
from modules.common.io import load_json, time_to_cron  # noqa: E402

MODULES_DIR = MODULES_ROOT
DATA_ROOT = MODULES_DIR / "modules_data"  # 模块用户数据根（代码/数据分家）


def module_data_dir(name: str) -> Path:
    """模块用户数据目录 modules/modules_data/<name>/（设置 + 业务数据）。"""
    return DATA_ROOT / name


# ---------- 内部 IO ----------

def _load_module_json(name: str) -> dict:
    return load_json(MODULES_DIR / name / "module.json", {})


def _load_settings_json(name: str) -> dict:
    """读数据区 settings.json（用户配置值；不存在返回 {}）。"""
    return load_json(module_data_dir(name) / "settings.json", {})


def _save_settings_json(name: str, settings: dict) -> bool:
    """原子写数据区 settings.json（部署状态 enabled/retry/auto_update + 业务设置统一落盘）。"""
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


def _merge_settings(name: str, **updates) -> bool:
    """合并写 settings.json（只更新给定键，保留其他部署状态/业务设置）。"""
    data = _load_settings_json(name)
    data.update(updates)
    return _save_settings_json(name, data)


# ---------- installed.json（版本/来源记录，安装器写） ----------

# 最近一次 job 联动错误（web 保存设置后取走回传，用户可见；键=模块名）
_job_sync_errors: dict[str, str] = {}


def take_job_error(name: str) -> str | None:
    """取走并清空模块最近一次 job 登记错误（无则 None）。"""
    return _job_sync_errors.pop(name, None)


def refresh_module_config(name: str) -> None:
    """更新/恢复后联动：调度 cron 重算 + agent job 重登记 + 设置驱动豁免 + 索引刷新。"""
    if not module_exists(name):
        return
    _sync_schedule_from_settings(name)
    from bridge.jobs import sync_module_jobs
    rj = sync_module_jobs(name)
    if not rj.get("ok"):
        from modules.common.log import log_event
        log_event("WARN", name, "job_sync_fail", rj.get("error", "job 联动失败"))
        _job_sync_errors[name] = rj.get("error", "job 联动失败")
    from bridge.permissions import refresh_permissions
    refresh_permissions()
    from modules.registry_index import invalidate
    invalidate()


def get_module_state(name: str) -> dict:
    """读 installed.json（部署版本记录）：{version, installed_at, source_id}。"""
    sf = module_data_dir(name) / "installed.json"
    if sf.is_file():
        try:
            data = json.loads(sf.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return data
        except Exception:
            pass
    return {}


def save_module_state(
    name: str, version: str = "", source_id: str = "", installed_at: str = "",
    sha256: str = "", files: list | None = None,
) -> bool:
    """写 installed.json（安装/更新后记录版本与来源；源安装模块附完整性基准 sha256+files）。"""
    import datetime
    data = get_module_state(name)
    if version:
        data["version"] = str(version)
    if source_id:
        data["source_id"] = str(source_id)
    if sha256:
        data["sha256"] = str(sha256)
    if files:
        data["files"] = list(files)
    data["installed_at"] = installed_at or datetime.datetime.now().isoformat(timespec="seconds")
    try:
        dd = module_data_dir(name)
        dd.mkdir(parents=True, exist_ok=True)
        sf = dd / "installed.json"
        tmp = sf.with_name(sf.name + ".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        tmp.replace(sf)
        return True
    except OSError:
        return False


# ---------- 部署状态（settings.json 统一管理） ----------

def get_auto_update(name: str) -> bool:
    """模块级自动更新开关（settings.json；缺省 True 跟随全局）。"""
    return bool(_load_settings_json(name).get("auto_update", True))


def set_auto_update(name: str, on: bool) -> bool:
    """模块级自动更新开关（写 settings.json，不动其他键）。"""
    if not module_exists(name):
        return False
    return _merge_settings(name, auto_update=bool(on))


# ---------- schedule_from_settings 联动（设置时间字段 → 调度 cron） ----------

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
        cron = time_to_cron(settings.get(tf))
        if not cron:
            continue
        entry: dict = {"id": phase or tf, "cron": cron}
        if phase:
            entry["args"] = ["--phase", phase]
        schedule.append(entry)
    data["schedule"] = schedule
    _save_module_json(name, data)


def _refresh_integrity_baseline(name: str) -> None:
    """系统改写 module.json 后刷新完整性基准（installed.json 的 sha256）。

    运行时改写（调度联动/设置保存）会改变 module.json 字节，若不刷新基准，
    完整性校验（调度前快检/每日全量）会把系统合法变更误判为本地篡改。
    仅源安装模块有基准（installed.json 含 sha256+files）；本地手写模块跳过。
    """
    try:
        from bridge.module_source import _module_sha256, MODULES_DIR as _MS_MODULES
        st = get_module_state(name)
        sha = st.get("sha256")
        files = st.get("files")
        if not sha or not files:
            return
        actual = _module_sha256(_MS_MODULES / name, files)
        if actual != sha:
            st["sha256"] = actual
            save_module_state(name, sha256=actual, files=files)
    except Exception:
        pass  # 刷新失败不影响主流程（下次安装/更新会重建基准）


def _save_module_json(name: str, data: dict) -> bool:
    try:
        mj = MODULES_DIR / name / "module.json"
        # newline="\n"：Windows 上 write_text 默认把 \n 转 \r\n，会改变文件字节——
        # 完整性基准（sha256）与作者（LF）不一致，显式保持 LF
        mj.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n",
                      encoding="utf-8", newline="\n")
        _refresh_integrity_baseline(name)
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
    })
    if schedule is not None:
        data["schedule"] = schedule
    data.setdefault("schedule", [])
    # 部署状态（enabled/retry/auto_update）进数据区 settings.json：
    # 安装时从 module.json 静态声明复制 retry 默认（若有），enabled 默认关闭，auto_update 默认开
    init_settings = {
        "enabled": False,
        "auto_update": True,
    }
    if retry is not None:  # CLI --retry-json 显式传入优先
        init_settings["retry"] = retry
    elif "retry" in data:
        init_settings["retry"] = data["retry"]
        del data["retry"]  # retry 归部署状态，module.json 不再承载
    ok = _save_module_json(name, data)
    ok = _save_settings_json(name, init_settings) and ok
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
    """保留字段式更新（已存在模块改配置；不碰 token，G1）。

    - purpose/spec/schedule：module.json（声明/联动产物）
    - retry：数据区 settings.json（部署状态，web 弹窗可配）
    - settings：业务设置（settings_schema 对应值），合并写 settings.json——
      **保留部署状态键（enabled/retry/auto_update），不被业务设置覆盖**；
      保存后联动调度 cron + agent job + 设置驱动豁免。
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
    ok = _save_module_json(name, data)
    if settings is not None:
        cur = _load_settings_json(name)
        merged = dict(cur)
        merged.update(settings)  # 部署状态键不在业务设置内，天然保留
        ok_s = _save_settings_json(name, merged)
        ok = ok and ok_s
        if ok_s:
            _sync_schedule_from_settings(name)  # 时间设置 → 调度 cron 联动
            from bridge.jobs import sync_module_jobs
            rj = sync_module_jobs(name)  # agent 型长任务：渲染 + 自动登记/注销（无声明跳过）
            if not rj.get("ok"):
                from modules.common.log import log_event
                log_event("WARN", name, "job_sync_fail", rj.get("error", "job 联动失败"))
                _job_sync_errors[name] = rj.get("error", "job 联动失败")
            from bridge.permissions import refresh_permissions
            refresh_permissions()  # 设置驱动豁免（vault_path 自动放行）
    if retry_set:
        ok = _merge_settings(name, retry=retry) and ok
    if ok:
        from modules.registry_index import invalidate
        invalidate()  # H2：更新后清索引缓存
    return ok


def set_enabled(name: str, enabled: bool) -> bool:
    """唯一启停入口：写数据区 settings.json 的 enabled（module.json 不再承载部署状态）。"""
    if not module_exists(name):
        return False
    ok = _merge_settings(name, enabled=bool(enabled))
    if ok:
        if not enabled:
            from bridge.jobs import unregister_jobs
            unregister_jobs(name)  # 停用 → 注销该模块全部 agent job（无声明无副作用）
        from modules.registry_index import invalidate
        invalidate()
    return ok


def get_module(name: str) -> dict | None:
    """读单个模块配置（含 enabled/retry/settings 值；不存在返回 None）。

    部署状态（enabled/retry/auto_update）从数据区 settings.json 读；
    settings 值同源（业务键）；module.json 只留声明与调度产物。
    """
    if not module_exists(name):
        return None
    data = _load_module_json(name)
    sv = _load_settings_json(name)
    data["enabled"] = bool(sv.get("enabled", False))
    data["retry"] = sv.get("retry")
    data["auto_update"] = bool(sv.get("auto_update", True))
    data["settings"] = sv
    data["version"] = str(get_module_state(name).get("version") or "")
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
        sv = _load_settings_json(name)
        state = get_module_state(name)
        items.append({
            "name": name,
            "purpose": data.get("purpose", ""),
            "schedule": data.get("schedule", []),
            "retry": sv.get("retry"),
            "enabled": bool(sv.get("enabled", False)),
            "auto_update": bool(sv.get("auto_update", True)),
            "version": state.get("version", ""),
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
            # 写信号文件通知 bridge：重生成 AGENTS.md + 清 session + 发提示
            # 累积列表模式：10 秒内开/关多个模块 → bridge 一次处理，不重复清 session
            try:
                import json as _json
                from datetime import datetime
                from bridge.config import DATA_ROOT
                signal = DATA_ROOT / ".config" / ".agents-reload-requested"
                signal.parent.mkdir(parents=True, exist_ok=True)
                entries = []
                if signal.is_file():
                    try:
                        data = _json.loads(signal.read_text(encoding="utf-8"))
                        if isinstance(data, list):
                            entries = data
                    except Exception:
                        pass
                entries.append({"module": name, "enabled": argv[0] == "--enable",
                                 "at": datetime.now().isoformat(timespec="seconds")})
                signal.write_text(_json.dumps(entries, ensure_ascii=False) + "\n", encoding="utf-8")
            except Exception:
                pass
            print(f"[register] {name} 已{'启用' if argv[0] == '--enable' else '关闭'}（AGENTS.md 将在 ~10 秒内重载）")
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
