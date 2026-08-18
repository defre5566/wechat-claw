"""管理 API：登录 / 用户配置 / 用户数据 / AGENTS / 模块 / 日志 / 密码。

除 auth 外均需会话 token（wizard.py 校验 X-Auth header）。
"""
from __future__ import annotations

import json
import math

from bridge.config import CONFIG_FILE, DEFAULTS_USER, PROJECT_ROOT
from modules.common import get_city, get_habits, get_location, set_city, set_habits, undo_city, undo_habits
from modules.registry_index import build_index
from web import agent_gen, auth

CITIES_PATH = PROJECT_ROOT / "web" / "static" / "cities.json"
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
    body = body or {}
    new_cfg = body.get("settings", {})
    # 只接受用户段键
    clean = {k: v for k, v in new_cfg.items() if k in DEFAULTS_USER}
    try:
        CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
        CONFIG_FILE.write_text(
            yaml.safe_dump(clean, allow_unicode=True, sort_keys=False), encoding="utf-8"
        )
        return {"ok": True}
    except OSError as e:
        return {"ok": False, "error": str(e)}, 500


# ---------- 日志 ----------

def logs_tail(app, body: dict | None = None) -> dict:
    body = body or {}
    n = int(body.get("tail", 200))
    log_file = PROJECT_ROOT / "logs" / "system.log"
    lines = []
    if log_file.is_file():
        try:
            text = log_file.read_text(encoding="utf-8", errors="replace")
            lines = text.splitlines()[-n:]
        except OSError:
            pass
    return {"ok": True, "lines": lines}


# ---------- 模块（经 register.py / build_index） ----------

def modules_list(app, body: dict | None = None) -> dict:
    index = build_index()
    items = []
    for name, cfg in sorted(index.items()):
        items.append({
            "name": name,
            "purpose": cfg.get("purpose", ""),
            "schedule": cfg.get("schedule", []),
            "retry": cfg.get("retry"),
            "enabled": cfg.get("enabled", False),
        })
    return {"ok": True, "modules": items}


def modules_toggle(app, body: dict | None = None) -> dict:
    body = body or {}
    name = body.get("name", "")
    enabled = bool(body.get("enabled"))
    from modules.register import set_enabled
    if set_enabled(name, enabled):
        return {"ok": True}
    return {"ok": False, "error": f"模块 {name} 不存在或操作失败"}, 400


def modules_install(app, body: dict | None = None) -> dict:
    """从模块源安装模块（G 块实现：本地隐藏模块直接启用；GitHub 源下载）。

    demo 阶段：仅支持启用已存在的本地模块（enabled=true）。
    """
    body = body or {}
    name = body.get("name", "")
    from modules.register import set_enabled
    if set_enabled(name, True):
        return {"ok": True}
    return {"ok": False, "error": f"模块 {name} 不存在或安装失败"}, 400
