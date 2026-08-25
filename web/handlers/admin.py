"""管理 API：登录 / 用户配置 / 用户数据 / AGENTS / 模块 / 日志 / 密码。

除 auth 外均需会话 token（wizard.py 校验 X-Auth header）。
"""
from __future__ import annotations

import base64
import json
import math

from bridge.config import CONFIG_FILE, DATA_ROOT, DEFAULTS_USER, RESOURCE_ROOT
from modules.common import get_city, get_habits, get_location, set_city, set_habits, undo_city, undo_habits
from modules.registry_index import build_index
from web import agent_gen, auth

CITIES_PATH = RESOURCE_ROOT / "web" / "static" / "cities.json"

AVATAR_FILE = DATA_ROOT / ".config" / "avatar.png"
AVATAR_PREV = DATA_ROOT / ".config" / "avatar.prev.png"
MAX_AVATAR_BYTES = 2 * 1024 * 1024
_cities_cache: list | None = None


def _load_cities() -> list:
    """加载城市库 [code, name, parent, pinyin, lat, lon]（模块级缓存）。"""
    global _cities_cache
    if _cities_cache is None:
        try:
            data = json.loads(CITIES_PATH.read_text(encoding="utf-8"))
            _cities_cache = data if isinstance(data, list) else []
        except Exception:
            _cities_cache = []
    return _cities_cache


def _city_by_code(code: str) -> list | None:
    for row in _load_cities():
        if row[0] == code:
            return row
    return None


def _city_with_coords(code: str) -> list | None:
    """按 code 取条目；自身无坐标则沿 parent 上溯取最近有坐标的祖先。"""
    seen: set[str] = set()
    while code and code not in seen:
        seen.add(code)
        row = _city_by_code(code)
        if row is None:
            return None
        if row[4] is not None and row[5] is not None:
            return row
        code = row[2]
    return None


def _nearest_city(lat: float, lon: float) -> list | None:
    """经纬度 → 最近城市条目（平方距离，经度按 cos(lat) 修正；区级坐标）。"""
    best: list | None = None
    best_d = float("inf")
    cos_lat = math.cos(math.radians(lat))
    for row in _load_cities():
        clat, clon = row[4], row[5]
        if clat is None or clon is None:
            continue
        d = (clat - lat) ** 2 + ((clon - lon) * cos_lat) ** 2
        if d < best_d:
            best_d = d
            best = row
    return best


# ---------- 登录 ----------

def auth_login(app, body: dict | None = None) -> dict:
    pwd = (body or {}).get("password", "")
    if not auth.password_exists():
        # 未设置密码 → 开放期直接放行
        return {"ok": True, "token": auth.create_session(), "open": True}
    if auth.check_password(pwd):
        return {"ok": True, "token": auth.create_session()}
    return {"ok": False, "error": "密码错误"}, 401


def password_change(app, body: dict | None = None) -> dict:
    body = body or {}
    if auth.change_password(body.get("old", ""), body.get("new", "")):
        return {"ok": True}
    return {"ok": False, "error": "旧密码错误或新密码过短"}, 400


# ---------- 用户数据（profile） ----------

def profile_get(app, body: dict | None = None) -> dict:
    return {
        "ok": True,
        "location": get_location(),
        "habits": get_habits(),
        "identity": agent_gen.get_identity(),
        "rules": agent_gen.get_rules(),
        "lifestyle": _load_lifestyle(),
    }


def _load_lifestyle() -> str:
    return str(agent_gen._userdata.load("agent/lifestyle", "") or "")


def _save_lifestyle(value: str) -> bool:
    return agent_gen._userdata.save("agent/lifestyle", value)


def profile_set(app, body: dict | None = None) -> dict:
    body = body or {}
    if "city" in body:
        set_city(str(body["city"]))
    if "habits" in body:
        set_habits([str(h) for h in body["habits"]])
    if "identity" in body:
        agent_gen.set_identity(dict(body["identity"]))
    if "rules" in body:
        agent_gen.set_rules([str(r) for r in body["rules"]])
    if "lifestyle" in body:
        _save_lifestyle(str(body.get("lifestyle", "")))
    return {"ok": True}


def weather_get(app, body: dict | None = None) -> dict:
    """只读天气快照；天气失败不影响后台其他页面。"""
    from modules.common.weather import get_weather_snapshot
    return get_weather_snapshot()


def profile_set_city(app, body: dict | None = None) -> dict:
    """选定城市（GUI 三级联动）：按 code 取本地城市库（名称/拼音/坐标）写入 location.json。"""
    body = body or {}
    code = str(body.get("code", ""))
    row = _city_by_code(code) or _city_with_coords(code)
    if row is None:
        return {"ok": False, "error": "未知城市 code"}, 400
    _code, name, _parent, pinyin, lat, lon = row
    set_city(name, pinyin or "", lat, lon, _code)
    return {"ok": True, "city": get_location()}


def profile_locate(app, body: dict | None = None) -> dict:
    """定位授权：浏览器 geolocation 坐标 → 最近城市条目（区级坐标）写入 location.json。"""
    body = body or {}
    try:
        lat, lon = float(body.get("lat")), float(body.get("lon"))
    except (TypeError, ValueError):
        return {"ok": False, "error": "无效坐标"}, 400
    row = _nearest_city(lat, lon)
    if row is None:
        return {"ok": False, "error": "城市库不可用"}, 500
    _code, name, _parent, pinyin, clat, clon = row
    set_city(name, pinyin or "", clat, clon, _code)
    return {"ok": True, "code": _code, "name": name, "city": get_location()}


# 图片魔数（拒非图片内容被存为 .png）
_IMAGE_MAGIC = (
    (b"\x89PNG\r\n\x1a\n", "image/png", ".png"),
    (b"\xff\xd8\xff", "image/jpeg", ".jpg"),
    (b"GIF87a", "image/gif", ".gif"),
    (b"GIF89a", "image/gif", ".gif"),
    (b"RIFF", "image/webp", ".webp"),  # RIFF....WEBP
)


def _detect_image(raw: bytes) -> tuple[str, str] | None:
    """按魔数识别图片类型，返回 (content_type, ext)；非图片返回 None。"""
    for magic, ctype, ext in _IMAGE_MAGIC:
        if raw.startswith(magic):
            return ctype, ext
    return None


def avatar_set(app, body: dict | None = None) -> dict:
    """上传头像：base64 data URL → 校验魔数 → .config/avatar.png（写前备份 prev）。"""
    data_url = (body or {}).get("data", "")
    if not data_url.startswith("data:image/"):
        return {"ok": False, "error": "无效图片数据"}, 400
    try:
        raw = base64.b64decode(data_url.split(",", 1)[1])
    except Exception:
        return {"ok": False, "error": "图片解码失败"}, 400
    if len(raw) > MAX_AVATAR_BYTES:
        return {"ok": False, "error": "图片超过 2MB"}, 413
    detected = _detect_image(raw)
    if detected is None:
        return {"ok": False, "error": "非有效图片（魔数校验失败）"}, 400
    try:
        AVATAR_FILE.parent.mkdir(parents=True, exist_ok=True)
        if AVATAR_FILE.is_file():
            AVATAR_PREV.write_bytes(AVATAR_FILE.read_bytes())
        AVATAR_FILE.write_bytes(raw)
        return {"ok": True, "size": len(raw), "type": detected[0]}
    except OSError as e:
        return {"ok": False, "error": str(e)}, 500


def avatar_get(app, body: dict | None = None):
    """返回头像字节与类型；不存在返回 None（wizard 按 404 处理）。"""
    if AVATAR_FILE.is_file():
        return AVATAR_FILE.read_bytes(), "image/png"
    return None


def avatar_undo(app, body: dict | None = None) -> dict:
    """撤销头像：有 prev 换回；无 prev 删除（回默认图标）。"""
    try:
        if AVATAR_PREV.is_file():
            AVATAR_FILE.write_bytes(AVATAR_PREV.read_bytes())
            AVATAR_PREV.unlink(missing_ok=True)
        else:
            AVATAR_FILE.unlink(missing_ok=True)
        return {"ok": True}
    except OSError as e:
        return {"ok": False, "error": str(e)}, 500


def profile_undo(app, body: dict | None = None) -> dict:
    field = (body or {}).get("field", "")
    if field == "city":
        undo_city()
    elif field == "habits":
        undo_habits()
    elif field == "identity":
        agent_gen.undo_identity()
    elif field == "rules":
        agent_gen.undo_rules()
    else:
        return {"ok": False, "error": "未知字段"}, 400
    return {"ok": True}


def agents_render(app, body: dict | None = None) -> dict:
    out = agent_gen.write_agents()
    return {"ok": True, "file": str(out)}


_PERSONA_OPT_TEMPLATE = """你是一名专业的人设优化顾问。请把下面的「角色设定」和「语言习惯」分别扩写、优化成更丰满、更可执行、符合个人数字助理定位的文本。

用户称呼：{address}
助理名称：{assistant_name}
当前角色设定：{role}
当前语言习惯：{language}
行为守则：{rules}
兴趣爱好：{habits}
生活习惯：{lifestyle}

要求：
1. 「角色设定」至少 8 句，覆盖：身份定位、陪伴方式、沟通风格、边界意识、主动性原则、与用户的关系、日常行为准则、自我要求
2. 「语言习惯」至少 8 句，覆盖：句式偏好、用词风格、礼貌分寸、解释方式、拒绝方式、提问方式、语气控制、特殊场景用语
3. 语言平实具体，不说空话套话，不要“我是一个……”式的苍白开场
4. 严格按下面的格式输出，不要标题、不要额外解释：

【角色设定】
<优化后的角色设定，至少 8 句>
【语言习惯】
<优化后的语言习惯，至少 8 句>"""


def _extract_sections(output: str) -> dict:
    """按【角色设定】/【语言习惯】标记截取两段；缺标记时尽力回退。"""
    role = language = ""
    if "【角色设定】" in output:
        rest = output.split("【角色设定】", 1)[1]
        if "【语言习惯】" in rest:
            role, language = rest.split("【语言习惯】", 1)
        else:
            role = rest
    elif "【语言习惯】" in output:
        language = output.split("【语言习惯】", 1)[1]
    else:
        role = output
    return {"role": role.strip(), "language": language.strip()}


def optimize_persona(app, body: dict | None = None) -> dict:
    """用 opencode run 优化人设：前端传入表单当前 role/language，返回两段截取结果。"""
    import os
    import subprocess
    body = body or {}
    role_in = str(body.get("role", "") or "").strip()
    lang_in = str(body.get("language", "") or "").strip()
    if not role_in and not lang_in:
        return {"ok": False, "error": "角色设定和语言习惯都为空，无法优化"}, 400
    from bridge.config import get as get_cfg
    from bridge.config import WORK_ROOT, resolve_opencode, xdg_env, no_window_flags
    binary = resolve_opencode()
    if not binary:
        return {"ok": False, "error": "未找到 opencode 可执行文件（acp.command / PATH / ~/.opencode/bin）"}, 400
    model = str(get_cfg("acp.model") or "deepseek/deepseek-chat")
    ident = agent_gen.get_identity()
    prompt = _PERSONA_OPT_TEMPLATE.format(
        address=str(ident.get("address") or ""),
        assistant_name=str(ident.get("assistant_name") or ""),
        role=role_in,
        language=lang_in,
        rules="；".join(agent_gen.get_rules()),
        habits="、".join(get_habits()),
        lifestyle=_load_lifestyle(),
    )
    env = os.environ.copy()
    env.update(xdg_env())
    try:
        # cwd=数据根（=项目根）：opencode run 在此加载 AGENTS.md/opencode.jsonc，
        # 否则进程继承 web 启动目录导致上下文错位
        r = subprocess.run([binary, "run", "-m", model, prompt],
                           capture_output=True, text=True, timeout=120, env=env,
                           cwd=str(WORK_ROOT),
                           creationflags=no_window_flags())
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "opencode 优化超时（120 秒），请稍后重试"}, 504
    output = (r.stdout or r.stderr or "").strip()
    if not output:
        return {"ok": False, "error": "opencode 未返回任何输出"}, 502
    sections = _extract_sections(output)
    if not sections["role"] and not sections["language"]:
        return {"ok": True, "role": output, "language": "", "fallback": True}
    return {"ok": True, **sections}


# ---------- 用户配置（config.yaml 用户段） ----------

def schema_get(app, body: dict | None = None) -> dict:
    """返回 config 用户段 schema（前端按此渲染高级设置表单）。"""
    from web.schema.config_schema import get_schema
    return {"ok": True, "schema": get_schema()}


def settings_get(app, body: dict | None = None) -> dict:
    import yaml
    cfg = {}
    try:
        if CONFIG_FILE.is_file():
            data = yaml.safe_load(CONFIG_FILE.read_text(encoding="utf-8")) or {}
            if isinstance(data, dict):
                cfg = data
    except Exception:
        pass
    return {"ok": True, "settings": cfg, "defaults": DEFAULTS_USER}


def settings_set(app, body: dict | None = None) -> dict:
    import yaml
    from web.schema.config_schema import validate_settings
    body = body or {}
    new_cfg = body.get("settings", {})
    result = validate_settings(new_cfg)
    if not result["ok"]:
        return {"ok": False, "errors": result["errors"]}, 400
    clean = result["clean"]
    try:
        CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
        CONFIG_FILE.write_text(
            yaml.safe_dump(clean, allow_unicode=True, sort_keys=False), encoding="utf-8"
        )
        return {"ok": True}
    except OSError as e:
        return {"ok": False, "error": str(e)}, 500


# ---------- 日志 ----------

def _log_match(line: str, level: str, module: str, keyword: str) -> bool:
    """过滤：level（INFO/WARN/ERROR 子串，JSON Lines 与普通行通用）+ module + keyword。"""
    if level:
        # "WARN" 同时命中 "WARNING"（普通行）与 "level": "WARN"（JSON 行）
        if level.upper() not in line.upper():
            return False
    if module and module not in line:
        return False
    if keyword and keyword not in line:
        return False
    return True


def logs_tail(app, body: dict | None = None) -> dict:
    """日志尾部 + 过滤：level/module/keyword（正则子串，大小写不敏感 level）。"""
    body = body or {}
    n = int(body.get("tail", 200))
    level = str(body.get("level", "")).strip()
    module = str(body.get("module", "")).strip()
    keyword = str(body.get("keyword", "")).strip()
    log_file = DEPLOY_ROOT / "logs" / "system.log"
    lines = []
    if log_file.is_file():
        try:
            text = log_file.read_text(encoding="utf-8", errors="replace")
            lines = [ln for ln in text.splitlines()
                     if _log_match(ln, level, module, keyword)]
            lines = lines[-n:]
        except OSError:
            pass
    return {"ok": True, "lines": lines}


# ---------- 模块（经 register.py / build_index） ----------

# ---------- choice 候选 / 位置服务（模块弹窗渲染数据装配，纯通用机制） ----------

def _module_choices(name: str) -> list[dict]:
    """choice 字段候选：模块 directions.json 键（预设，只读）+ 数据区 prompts/custom/*.json（自定义，可删）。"""
    from modules.register import MODULES_DIR, module_data_dir
    out: list[dict] = []
    dj = MODULES_DIR / name / "directions.json"
    if dj.is_file():
        try:
            data = json.loads(dj.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                for k in data:
                    out.append({"value": k, "preset": True})
        except Exception:
            pass
    custom_dir = module_data_dir(name) / "prompts" / "custom"
    if custom_dir.is_dir():
        for f in sorted(custom_dir.glob("*.json")):
            out.append({"value": f.stem, "preset": False})
    return out


def _enrich_module(m: dict, name: str) -> None:
    """弹窗渲染数据装配：choice 候选注入 + 位置服务列表（show_when_service 前端显隐用）。"""
    schema = m.get("settings_schema")
    if isinstance(schema, list):
        choices = _module_choices(name)
        for section in schema:
            for field in section.get("fields") or []:
                if isinstance(field, dict) and field.get("type") == "choice":
                    field["candidates"] = choices
    from modules.common.localdata import available as _avail
    from modules.common.location import get_location
    try:
        m["location_services"] = _avail(get_location())
    except Exception:
        m["location_services"] = []


_DIRECTION_RE = None


def _valid_direction(name: str) -> bool:
    """方向名白名单：中文/字母/数字/下划线/短横线，≤24 字符（防路径穿越）。"""
    global _DIRECTION_RE
    if _DIRECTION_RE is None:
        import re
        _DIRECTION_RE = re.compile(r"^[\w\u4e00-\u9fa5-]{1,24}$")
    return bool(_DIRECTION_RE.match(name or ""))


def module_prompt_add(app, body: dict | None = None) -> dict:
    """导入自定义简报方向 prompt：写数据区 prompts/custom/<方向名>.json（明文，用户可看可改）。

    同方向名覆盖；保存后 update_module 联动刷新简报 job。
    """
    body = body or {}
    name = str(body.get("name", "")).strip()
    direction = str(body.get("direction", "")).strip()
    prompt = str(body.get("prompt", "")).strip()
    if not name or not direction or not prompt:
        return {"ok": False, "error": "模块名、方向名与内容必填"}, 400
    if not _valid_direction(direction):
        return {"ok": False, "error": "方向名仅允许中文/字母/数字/下划线/短横线，≤24 字符"}, 400
    from modules.register import module_data_dir, update_module
    dd = module_data_dir(name)
    if not dd.is_dir():
        return {"ok": False, "error": "模块不存在"}, 404
    custom_dir = dd / "prompts" / "custom"
    custom_dir.mkdir(parents=True, exist_ok=True)
    f = custom_dir / f"{direction}.json"
    tmp = f.with_name(f.name + ".tmp")
    tmp.write_text(json.dumps({"prompt": prompt}, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(f)
    update_module(name, settings=None)  # 触发 job 联动（prompt 变化 → 重登记）
    return {"ok": True, "direction": direction}


def module_prompt_delete(app, body: dict | None = None) -> dict:
    """删除自定义简报方向（数据区 custom/<方向名>.json）。"""
    body = body or {}
    name = str(body.get("name", "")).strip()
    direction = str(body.get("direction", "")).strip()
    if not name or not direction:
        return {"ok": False, "error": "模块名与方向名必填"}, 400
    if not _valid_direction(direction):
        return {"ok": False, "error": "方向名非法"}, 400
    from modules.register import module_data_dir, update_module
    f = module_data_dir(name) / "prompts" / "custom" / f"{direction}.json"
    try:
        if f.is_file():
            f.unlink()
            update_module(name, settings=None)  # 触发 job 联动（方向减少 → 重登记）
            return {"ok": True}
        return {"ok": False, "error": "方向不存在"}, 404
    except OSError as e:
        return {"ok": False, "error": str(e)}, 500


def module_auto_update(app, body: dict | None = None) -> dict:
    """模块级自动更新开关（弹窗标题栏胶囊）：写数据区 settings.json（register 管）。"""
    body = body or {}
    name = str(body.get("name", "")).strip()
    on = bool(body.get("on"))
    from modules.register import set_auto_update
    if not set_auto_update(name, on):
        return {"ok": False, "error": "模块不存在"}, 404
    return {"ok": True}


def modules_check_updates(app, body: dict | None = None) -> dict:
    """手动检查更新（force=True：绕过全局开关）；结果含 updated/skipped/errors。"""
    from bridge.module_source import check_updates
    result = check_updates(force=True)
    return {"ok": True, **result}


def module_update_now(app, body: dict | None = None) -> dict:
    """手动更新单个模块（web 按钮）：从源直接更新（校验 + .bak + 跳 token）。"""
    body = body or {}
    name = str(body.get("name", "")).strip()
    if not name:
        return {"ok": False, "error": "缺少模块名"}, 400
    from bridge.module_source import load_sources, update_module_from_source
    sources = load_sources()
    for src in sources:
        if any(m.get("name") == name for m in src.get("modules", []) or []):
            r = update_module_from_source(sources, src["id"], name)
            return ({"ok": True, "updated": bool(r.get("updated"))} if r.get("ok")
                    else {"ok": False, "error": r.get("error", "更新失败")}), (200 if r.get("ok") else 400)
    return {"ok": False, "error": f"源中没有模块 {name}"}, 404


def modules_list(app, body: dict | None = None) -> dict:
    """全量模块列表（含禁用）：register.list_modules（G4：花名册替代排班表）。

    修复前用 build_index（只含 enabled）→ 关掉的模块从后台消失无法复启。
    """
    from modules.register import list_modules
    from bridge.module_source import load_sources
    modules = list_modules()
    sources = load_sources()
    source_names = {}
    for source in sources:
        for item in source.get("modules", []) or []:
            source_names[item.get("name")] = source.get("name") or source.get("id") or "模块源"
    for module in modules:
        module["source"] = source_names.get(module["name"], "本地模块")
    return {"ok": True, "modules": modules}


def module_get(app, body: dict | None = None) -> dict:
    """读单个模块完整配置（弹窗渲染用）：enabled/schedule/retry/inbound + 渲染数据装配。"""
    from modules.register import get_module
    name = (body or {}).get("name", "")
    m = get_module(name)
    if m is None:
        return {"ok": False, "error": "模块不存在"}, 404
    _enrich_module(m, name)
    return {"ok": True, "module": m}


def module_update(app, body: dict | None = None) -> dict:
    """保存模块设置（弹窗保存）：走 register.update_module（G1：不碰 token/enabled）。

    settings：按 settings_schema 校验清洗（类型/选项/show_when 丢弃/required_when 必填），
    校验失败返回 400（如 vault 模式 vault_path 空白）。
    """
    body = body or {}
    name = body.get("name", "")
    from modules.register import get_module, update_module
    m = get_module(name)
    if m is None:
        return {"ok": False, "error": "模块不存在"}, 404
    settings = body.get("settings")
    if settings is not None:
        _enrich_module(m, name)  # choice 候选 + 位置服务（show_when_service 校验用）
        from web.schema.module_schema import validate_module_settings
        ok, clean, errors = validate_module_settings(
            m.get("settings_schema"), settings, services=m.get("location_services"))
        if not ok:
            return {"ok": False, "error": "；".join(errors)}, 400
    else:
        clean = None
    ok = update_module(
        name,
        purpose=body.get("purpose"),
        spec=body.get("spec"),
        schedule=body.get("schedule"),
        retry=body.get("retry"),
        retry_set="retry" in body,
        settings=clean,
    )
    if not ok:
        return {"ok": False, "error": f"模块 {name} 不存在或保存失败"}, 400
    # job 自动登记失败带到响应（前端提示；成功保存但登记失败不算 400）
    from modules.register import take_job_error
    job_err = take_job_error(name)
    if job_err:
        return {"ok": True, "job_error": f"设置已保存，但 agent 任务登记失败：{job_err}"}
    return {"ok": True}


def modules_toggle(app, body: dict | None = None) -> dict:
    body = body or {}
    name = body.get("name", "")
    enabled = bool(body.get("enabled"))
    from modules.register import set_enabled
    if not set_enabled(name, enabled):
        return {"ok": False, "error": f"模块 {name} 不存在或操作失败"}, 400
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
        entries.append({"module": name, "enabled": enabled, "at": datetime.now().isoformat(timespec="seconds")})
        signal.write_text(_json.dumps(entries, ensure_ascii=False) + "\n", encoding="utf-8")
    except Exception:
        pass
    return {"ok": True}


def modules_install(app, body: dict | None = None) -> dict:
    """从模块源安装模块：校验（清单/结构/哈希）→ 复制 → 注册（token+数据目录+enabled:false）。

    body: {source_id, name}。
    """
    body = body or {}
    sid = body.get("source_id", "")
    name = body.get("name", "")
    if not sid or not name:
        return {"ok": False, "error": "缺少 source_id 或 name"}, 400
    from bridge.module_source import load_sources, install_module
    sources = load_sources()
    r = install_module(sources, sid, name)
    if r["ok"]:
        return {"ok": True, "name": name, "enabled": False}
    return {"ok": False, "error": r.get("error", "安装失败")}, 400


def modules_remove(app, body: dict | None = None) -> dict:
    """删除模块（二次确认后调用）：uninstall，默认保留用户数据。

    body.purge_data=true → 连 modules_data/<name>/ 一起删（不可恢复）。
    """
    body = body or {}
    name = body.get("name", "")
    keep_data = not bool(body.get("purge_data"))
    from modules.register import uninstall
    if uninstall(name, keep_data=keep_data):
        return {"ok": True, "kept_data": keep_data}
    return {"ok": False, "error": f"模块 {name} 不存在或卸载失败"}, 400


# ---------- 模块源（web 安装模块功能） ----------

def sources_list(app, body: dict | None = None) -> dict:
    """源列表 + 全部模块目录（按源顺序，含 installed 标记）。"""
    from bridge.module_source import load_sources, list_catalog
    sources = load_sources()
    return {"ok": True, "sources": sources, "catalog": list_catalog(sources)}


def source_add(app, body: dict | None = None) -> dict:
    """添加源：github（URL）/ local（路径）→ 拉取列表 → 存入 sources.json。

    body: {type, url, name?}。返回 {ok, source, error?}。
    """
    body = body or {}
    stype = body.get("type", "")
    url = (body.get("url") or "").strip()
    if stype not in ("github", "local") or not url:
        return {"ok": False, "error": "类型或地址无效"}, 400

    from bridge.module_source import (
        load_sources, save_sources, fetch_source, _find_source,
    )
    sources = load_sources()
    # 同名地址去重（github 按 URL，local 按路径）
    for s in sources:
        if s.get("type") == stype and s.get("url") == url:
            return {"ok": False, "error": f"该源已存在（{s.get('name')}）"}, 400

    src = {
        "id": f"{stype}_{len(sources)}",
        "name": (body.get("name") or url.split("/")[-1].replace(".git", "") or stype).strip(),
        "type": stype,
        "url": url,
        "builtin": False,
        "added_at": __import__("datetime").datetime.now().isoformat(timespec="seconds"),
        "fingerprint": "",
        "modules": [],
    }
    res = fetch_source(src)  # 添加源 → 自动拉列表（第 7 条）
    if not res["ok"]:
        return {"ok": False, "error": f"拉取失败: {res['error']}"}, 400
    src["modules"] = res["modules"]
    src["fingerprint"] = res["fingerprint"]
    sources.append(src)
    save_sources(sources)
    return {"ok": True, "source": src}


def source_remove(app, body: dict | None = None) -> dict:
    """删除源（builtin 官方源不可删）。"""
    body = body or {}
    sid = body.get("id", "")
    from bridge.module_source import load_sources, save_sources, _find_source
    sources = load_sources()
    src = _find_source(sources, sid)
    if src is None:
        return {"ok": False, "error": f"源不存在: {sid}"}, 404
    if src.get("builtin"):
        return {"ok": False, "error": "内置官方源不可删除"}, 400
    sources.remove(src)
    save_sources(sources)
    return {"ok": True}


def source_refresh(app, body: dict | None = None) -> dict:
    """手动刷新源模块列表（指纹对比，有新提交则更新缓存）。"""
    body = body or {}
    sid = body.get("id", "")
    from bridge.module_source import load_sources, refresh_source
    sources = load_sources()
    r = refresh_source(sources, sid)
    if not r["ok"]:
        return {"ok": False, "error": r["error"]}, 400
    return {"ok": True, "updated": r["updated"], "modules": r["modules"]}


def autostart_get(app, body: dict | None = None) -> dict:
    """自启动状态（服务/用户级/无 + bridge 运行态）。"""
    from web.handlers.service_up import autostart_status
    return autostart_status()


def autostart_set(app, body: dict | None = None) -> dict:
    """开机自动启动开关：开启/关闭（Windows 非管理员经 UAC 提权）。"""
    body = body or {}
    from web.handlers.service_up import autostart_set as _set
    return _set(bool(body.get("on", False)))


def status_get(app, body: dict | None = None) -> dict:
    """服务运行状态（欢迎区真实检测）：bridge 运行 + 模块数 + 自启模式 + web 自身。"""
    from web.handlers.service_up import autostart_status, _bridge_running
    from modules.registry_index import build_index
    try:
        mods = build_index()
        module_count = len(mods)
    except Exception:
        module_count = 0
    st = autostart_status()
    return {
        "ok": True,
        "bridge_running": _bridge_running(),
        "module_count": module_count,
        "autostart_mode": st.get("mode", "none"),
        "web_ok": True,
    }


def start_bridge(app, body: dict | None = None) -> dict:
    """手动启动 bridge（基础设置页「启动」按钮调用）。"""
    # 预检 opencode：缺失时直接报原因，不产生必败子进程（spawn 报 WinError 2 信息量为零）
    from bridge.config import resolve_opencode
    from bridge.main import OPENCODE_LOOKUP_HINT
    if not resolve_opencode():
        return {"ok": False, "steps": [{
            "cmd": f"opencode 未找到（已查 {OPENCODE_LOOKUP_HINT}），请到初始化向导第二步安装",
            "ok": False,
        }]}
    from web.handlers.service_up import _spawn_bridge_now
    steps = _spawn_bridge_now()
    ok = all(s.get("ok") for s in steps)
    return {"ok": ok, "steps": steps}


def version_get(app, body: dict | None = None) -> dict:
    """版本检测：当前版本（源码 git describe / exe VERSION 常量）+ 最新 release 版本。"""
    import subprocess
    from bridge.config import VERSION
    import json, urllib.request
    # 当前版本
    has_git = False
    current = VERSION
    try:
        r = subprocess.run(["git", "describe", "--tags", "--always"], capture_output=True, text=True, timeout=10)
        if r.returncode == 0 and r.stdout.strip():
            current = r.stdout.strip()
            has_git = True
    except Exception:
        pass
    # 最新版本
    latest = VERSION
    download_url = ""
    try:
        req = urllib.request.Request(
            "https://api.github.com/repos/defre5566/wechat-claw/releases/latest",
            headers={"User-Agent": "wc-version", "Accept": "application/vnd.github+json"},
        )
        with urllib.request.urlopen(req, timeout=3) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            latest = (data.get("tag_name") or "").lstrip("v")
            assets = data.get("assets") or []
            win = next((a for a in assets if "windows" in (a.get("name") or "")), None)
            if win:
                download_url = win.get("browser_download_url") or ""
            if not download_url and data.get("html_url"):
                download_url = data["html_url"]
    except Exception:
        latest = VERSION
    is_latest = current == latest or current.lstrip("v") == latest.lstrip("v")
    return {"ok": True, "current": current, "latest": latest, "is_latest": is_latest, "has_git": has_git, "download_url": download_url}


def gitpull_get(app, body: dict | None = None) -> dict:
    """源码 git pull 更新（高级设置页按钮调用）。"""
    import shutil as _shutil
    import subprocess
    if not _shutil.which("git"):
        return {"ok": False, "error": "本机未安装 git，无法源码更新；请使用「下载最新版」升级 exe"}
    try:
        r = subprocess.run(["git", "pull"], capture_output=True, text=True, timeout=60, cwd=str(DATA_ROOT))
        if r.returncode != 0:
            return {"ok": False, "error": (r.stderr or r.stdout)[:500]}
        return {"ok": True, "output": (r.stdout or "")[:500]}
    except Exception as e:
        return {"ok": False, "error": str(e)}
