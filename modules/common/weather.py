"""天气/花粉（Open-Meteo + 内蒙古疾控），含共享缓存。

- fetch_weather / fetch_pollen：实时抓取（各带 3 次重试）
- get_weather：带 TTL 缓存的天气（跨模块共享，默认 30 分钟）
"""
from __future__ import annotations

import json
import time
import urllib.parse
import urllib.request

from .locations import DEFAULT_LOC
from .io import shared_load, shared_save

CITY = "Jining,Ulanqab,China"
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

# --- 花粉指数（内蒙古疾控欢舒花粉预报，nmgcdc.zw.nm.cn/pollen；当前经第三方域名 nmgcdc.qcurl.cn 转发）---
POLLEN_API = "https://nmgcdc.qcurl.cn/api/forecast"
POLLEN_CITY = "乌兰察布"  # 集宁属乌兰察布市
POLLEN_LEVEL_TAG = {"低": "可正常出行", "较低": "注意防护", "中": "特别敏感人群注意", "较高": "遵医嘱用药", "高": "非必要不外出"}


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


def fetch_weather() -> str:
    """Open-Meteo：地理编码 + forecast，weathercode 映射。返回"集宁 ☀️ 晴 22°C"。"""
    lat, lon = DEFAULT_LOC
    name = "集宁"
    geo = http_get_json(f"{GEO_API}?{urllib.parse.urlencode({'name': CITY, 'count': 1, 'language': 'zh'})}")
    if geo and geo.get("results"):
        g = geo["results"][0]
        lat, lon = g["latitude"], g["longitude"]
        name = g.get("name") or "集宁"
    w = http_get_json(f"{WEATHER_API}?latitude={lat}&longitude={lon}&current_weather=true")
    if not w or "current_weather" not in w:
        return f"{name} ⛅ 天气获取失败"
    cw = w["current_weather"]
    emoji, desc = WEATHER_CODES.get(cw.get("weathercode", 0), ("☀️", "晴"))
    return f"{name} {emoji} {desc} {round(cw.get('temperature', 0))}°C"


def fetch_pollen(today=None) -> str:
    """内蒙古疾控花粉浓度（乌兰察布/集宁）。返回"花粉：中（…）"；失败返回"花粉：获取失败"。"""
    from datetime import date
    d = (today or date.today()).isoformat()
    url = f"{POLLEN_API}?city={urllib.parse.quote(POLLEN_CITY)}&date={d}"
    data = http_get_json(url)
    if not data or not data.get("level"):
        return "花粉：获取失败"
    level = data["level"]
    tag = POLLEN_LEVEL_TAG.get(level)
    return f"花粉：{level}" + (f"（{tag}）" if tag else "")


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