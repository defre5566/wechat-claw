"""管理 API：登录 / 用户配置 / 用户数据 / AGENTS / 模块 / 日志 / 密码。

除 auth 外均需会话 token（wizard.py 校验 X-Auth header）。
"""
from __future__ import annotations

import base64
import json
import math

from bridge.config import CONFIG_FILE, DEFAULTS_USER, DEPLOY_ROOT, RESOURCE_ROOT
from modules.common import get_city, get_habits, get_location, set_city, set_habits, undo_city, undo_habits
from modules.registry_index import build_index
from web import agent_gen, auth

CITIES_PATH = RESOURCE_ROOT / "web" / "static" / "cities.json"

AVATAR_FILE = DEPLOY_ROOT / ".config" / "avatar.png"
AVATAR_PREV = DEPLOY_ROOT / ".config" / "avatar.prev.png"
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
    }


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
    return {"ok": True}


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

def modules_list(app, body: dict | None = None) -> dict:
    """全量模块列表（含禁用）：register.list_modules（G4：花名册替代排班表）。

    修复前用 build_index（只含 enabled）→ 关掉的模块从后台消失无法复启。
    """
    from modules.register import list_modules
    return {"ok": True, "modules": list_modules()}


def module_get(app, body: dict | None = None) -> dict:
    """读单个模块完整配置（弹窗渲染用）：enabled/schedule/retry/inbound。"""
    from modules.register import get_module
    m = get_module((body or {}).get("name", ""))
    if m is None:
        return {"ok": False, "error": "模块不存在"}, 404
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
        from web.schema.module_schema import validate_module_settings
        ok, clean, errors = validate_module_settings(m.get("settings_schema"), settings)
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
    if ok:
        return {"ok": True}
    return {"ok": False, "error": f"模块 {name} 不存在或保存失败"}, 400


def modules_toggle(app, body: dict | None = None) -> dict:
    body = body or {}
    name = body.get("name", "")
    enabled = bool(body.get("enabled"))
    from modules.register import set_enabled
    if set_enabled(name, enabled):
        return {"ok": True}
    return {"ok": False, "error": f"模块 {name} 不存在或操作失败"}, 400


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
