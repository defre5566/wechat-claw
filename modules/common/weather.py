"""天气（Open-Meteo，location 驱动），含共享缓存 + 气象预警。

- fetch_weather：实时抓取（带 3 次重试）；get_weather：带 TTL 缓存（默认 30 分钟，跨模块共享）
- weather_alerts：基于当日天气 code 的恶劣天气预警（暴雨/雷暴/大雪等；洪水等专用预警接口后续查证接入）

天气城市优先级：location.json 坐标（区级基准，选定/定位时写入）
→ city 名 geocoding（兼容旧版英文名数据如 Jining；新版为中文名，
  Open-Meteo 中文支持有限常查不到，失败即落 en 分支）→ 拼音 en 兜底
→ 回退 DEFAULT_LOC（北京）。
注：花粉等地方性数据已移出 → common.localdata（weather 只做纯天气）。
"""
from __future__ import annotations

import json
import time
import urllib.parse
import urllib.request

from .location import get_location
from .locations import DEFAULT_LOC
from .io import shared_load, shared_save

GEO_API = "https://geocoding-api.open-meteo.com/v1/search"
WEATHER_API = "https://api.open-meteo.com/v1/forecast"
WEATHER_CODES = {
    0: ("☀️", "晴"), 1: ("🌤️", "晴"), 2: ("⛅", "多云"), 3: ("☁️", "阴"),
    45: ("🌫️", "雾"), 48: ("🌫️", "雾凇"),
    51: ("🌦️", "小雨"), 53: ("🌦️", "小雨"), 55: ("🌦️", "小雨"),
    61: ("🌧️", "雨"), 63: ("🌧️", "雨"), 65: ("🌧️", "大雨"),
    71: ("❄️", "小雪"), 73: ("❄️", "雪"), 75: ("❄️", "大雪"),
    80: ("🌦️", "阵雨"), 81: ("🌧️", "阵雨"), 82: ("⛈️", "暴雨"),
    95: ("⛈️", "雷暴"), 96: ("⛈️", "雷暴冰雹"), 99: ("⛈️", "大冰雹"),
}

# 恶劣天气 → 预警提示（早报天气段附言用；洪水等专用预警接口查证后另接）
ALERT_CODES = {
    65: "大雨", 75: "大雪", 82: "暴雨", 95: "雷暴", 96: "雷暴伴冰雹", 99: "大冰雹",
}
_ALERT_TTL = 1800.0  # 预警 code 缓存（与天气同节奏，30 分钟）


def http_get_json(url: str, timeout: int = 15, attempts: int = 3, delay: float = 2.0) -> dict | None:
    """GET JSON，失败重试 attempts 次（默认 3 次，间隔 delay 秒），全失败返回 None。"""
    last: dict | None = None
    for i in range(attempts):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "wechat-modules/0.1"})
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except Exception:
            last = None
            if i < attempts - 1:
                time.sleep(delay)
    return last


def _weather_at(lat: float, lon: float, name: str) -> str:
    """按坐标查 Open-Meteo forecast，返回"名称 ☀️ 晴 22°C"；失败返回名称+获取失败。"""
    w = http_get_json(f"{WEATHER_API}?latitude={lat}&longitude={lon}&current_weather=true")
    if not w or "current_weather" not in w:
        return f"{name} ⛅ 天气获取失败"
    cw = w["current_weather"]
    emoji, desc = WEATHER_CODES.get(cw.get("weathercode", 0), ("☀️", "晴"))
    return f"{name} {emoji} {desc} {round(cw.get('temperature', 0))}°C"


def fetch_weather() -> str:
    """按用户位置查天气。优先级：坐标 → geocoding（city 名/兼容旧英文 → en 拼音）→ 回退北京。"""
    loc = get_location()
    lat, lon = loc.get("lat"), loc.get("lon")
    name = str(loc.get("city", "")) or "北京"

    if lat is not None and lon is not None:
        return _weather_at(lat, lon, name)

    # 无坐标（未配置/选定失败）：geocoding city 名（旧数据英文名可直接查到）→ en 拼音 → 回退默认
    for query in (loc.get("city"), loc.get("en")):
        if not query:
            continue
        geo = http_get_json(f"{GEO_API}?{urllib.parse.urlencode({'name': query, 'count': 1, 'language': 'zh'})}")
        if geo and geo.get("results"):
            g = geo["results"][0]
            return _weather_at(g["latitude"], g["longitude"], name)
    return _weather_at(*DEFAULT_LOC, "北京")


def fetch_pollen(today=None) -> str:
    """（已移出）花粉归 common.localdata，此处占位防旧引用崩溃。"""
    raise NotImplementedError("花粉已移至 common.localdata（fetch('pollen')）")


def weather_alerts(use_cache: bool = True, ttl: float = _ALERT_TTL) -> list[str]:
    """恶劣天气预警提示（基于当日天气 code 映射）。

    返回如 ["暴雨预警", "雷暴预警"]；无恶劣天气返回 []。code 带共享缓存（30 分钟）。
    """
    code = _current_code(use_cache, ttl)
    if code is None:
        return []
    desc = ALERT_CODES.get(code)
    return [f"{desc}预警"] if desc else []


def _current_code(use_cache: bool = True, ttl: float = _ALERT_TTL) -> int | None:
    """当前天气 code（带共享缓存 weather_code_cache）；失败返回 None。"""
    if use_cache:
        cache = shared_load("weather_code_cache")
        if cache.get("code") is not None and time.time() - cache.get("ts", 0) < ttl:
            return cache["code"]
    loc = get_location()
    lat, lon = loc.get("lat"), loc.get("lon")
    if lat is None or lon is None:
        return None
    w = http_get_json(f"{WEATHER_API}?latitude={lat}&longitude={lon}&current_weather=true")
    if not w or "current_weather" not in w:
        return None
    code = w["current_weather"].get("weathercode")
    if code is not None:
        shared_save("weather_code_cache", {"code": code, "ts": time.time()})
    return code


def get_weather(use_cache: bool = True, ttl: float = 1800.0) -> str:
    """带缓存的天气文本（共享缓存 weather_cache，TTL 默认 30 分钟）。缓存未命中才真抓取。"""
    if use_cache:
        cache = shared_load("weather_cache")
        if cache.get("text") and time.time() - cache.get("ts", 0) < ttl:
            return cache["text"]
    text = fetch_weather()
    if text:
        shared_save("weather_cache", {"text": text})
    return text
