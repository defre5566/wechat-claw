"""用户位置数据面：location.json（选定城市三件套 + code）+ .prev 撤销。

结构：{code, city, en, lat, lon}
- code：cities.json 条目 code（6 位，前端联动高亮用）
- city：中文名（天气显示名）
- en：拼音（Open-Meteo geocoding 中文查不到时的英文兜底）
- lat/lon：区级中心坐标（天气直接按坐标查，区级基准）

GUI 经 set_city 写入（地区选择器选定 / 定位授权）；模块经 get_location 消费。
旧数据兼容：旧格式（仅 city 英文名如 Jining）city 有值即正常返回，
lat/lon 为空时天气层走 geocoding 兜底（英文名可查到）。
"""
from __future__ import annotations

from . import _userdata

FIELDS = ("code", "city", "en", "lat", "lon")


def get_location() -> dict:
    """读用户位置完整信息；未设置/无 city 返回 {}。"""
    data = _userdata.load("location", {})
    if not isinstance(data, dict) or not data.get("city"):
        return {}
    return {k: data.get(k) for k in FIELDS}


def get_city() -> str:
    """读用户城市中文名；未设置返回 ''。"""
    return str(get_location().get("city", ""))


def set_city(city: str, en: str = "", lat=None, lon=None, code: str = "") -> bool:
    """写用户位置（自动备份 prev，供撤销）。"""
    data = {"code": code or "", "city": city, "en": en or "",
            "lat": lat, "lon": lon}
    return _userdata.save("location", data)


def undo_city() -> str:
    """撤销上次修改；无 prev（无可撤销）返回当前值。"""
    prev = _userdata.undo("location", None)
    if isinstance(prev, dict) and prev.get("city"):
        return str(prev["city"])
    return get_city()
