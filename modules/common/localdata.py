"""地方性数据层（非纯气象的区域服务，按 location 判可用）。

- SERVICES 注册表：每个服务声明适用区域（province 粒度）+ fetch 函数
- `available(loc) -> list[str]`：按 location 判可用服务
- `fetch(loc, service=None) -> dict`：拉全部可用（None）或单个；失败缺省不抛错
- 每日缓存 + 符合 location 的数据进 shared（`shared_save("localdata")`——客观数据，
  其他模块/agent 可读；与简报"私有"不同）
- 第一版服务：pollen（内蒙古疾控，location 驱动）、typhoon（中央气象台台风网，沿海）

返回格式统一：`{"pollen": {"level", "detail", "updated_at", "city"}, "typhoon": {"active", "list"}}`
"""
from __future__ import annotations

import json
import re
import time
import urllib.parse
import urllib.request
from datetime import date

from .location import get_location
from .io import shared_load, shared_save
from .weather import http_get_json

# ---------- 区域判定 ----------

_COASTAL_PROVINCES = {"广东", "广西", "海南", "福建", "浙江", "上海", "江苏", "山东", "辽宁", "河北", "天津"}
_NEIMENG_CITIES = {
    "集宁", "乌兰察布", "呼和浩特", "包头", "鄂尔多斯", "赤峰", "通辽",
    "呼伦贝尔", "乌海", "巴彦淖尔", "锡林郭勒", "兴安盟", "阿拉善",
}


def _province(loc: dict) -> str:
    """省份判定：location.province 优先；缺省时城市名关键词匹配（仅内蒙可兜底）。"""
    p = str(loc.get("province") or "").strip()
    if p:
        return p
    city = str(loc.get("city") or "")
    for c in _NEIMENG_CITIES:
        if c in city:
            return "内蒙古"
    return ""


# ---------- 数据抓取（各服务私有） ----------

def _http_get_text(url: str, timeout: int = 20, attempts: int = 3, delay: float = 2.0) -> str | None:
    """GET 文本（JSONP 等非纯 JSON 用），失败重试。"""
    last = None
    for i in range(attempts):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "wechat-modules/0.1"})
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.read().decode("utf-8", errors="replace")
        except Exception:
            if i < attempts - 1:
                time.sleep(delay)
    return last


def _jsonp_unwrap(text: str) -> dict | None:
    """解析 JSONP 响应（callback(({...})) 或 callback({...}) → dict）。

    直接取首尾花括号之间的 JSON 文本，兼容单/双层括号包装。
    """
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end <= start:
        return None
    try:
        data = json.loads(text[start:end + 1])
        return data if isinstance(data, dict) else None
    except Exception:
        return None


# --- pollen（内蒙古疾控；生产原在 weather，location 驱动移入） ---

POLLEN_API = "https://nmgcdc.qcurl.cn/api/forecast"
POLLEN_LEVEL_TAG = {
    "低": "可正常出行", "较低": "注意防护", "中": "特别敏感人群注意",
    "较高": "遵医嘱用药", "高": "非必要不外出",
}


def _fetch_pollen(loc: dict) -> dict | None:
    d = date.today().isoformat()
    url = f"{POLLEN_API}?city={urllib.parse.quote('乌兰察布')}&date={d}"
    data = http_get_json(url)
    if not data or not data.get("level"):
        return None
    level = data["level"]
    return {
        "level": level,
        "detail": POLLEN_LEVEL_TAG.get(level, ""),
        "updated_at": data.get("updatedAt", ""),
        "city": "乌兰察布",
    }


# --- typhoon（中央气象台台风网 typhoon.nmc.cn，公开 JSONP 接口） ---

TYPHOON_LIST_API = "http://typhoon.nmc.cn/weatherservice/typhoon/jsons/list_default?t={ts}&callback=typhoon_jsons_list_default"
TYPHOON_VIEW_API = "http://typhoon.nmc.cn/weatherservice/typhoon/jsons/view_{tid}?t={ts}&callback=typhoon_jsons_view_{tid}"
TYPHOON_GRADE = {"TC": "热带气旋", "TD": "热带低压", "TS": "热带风暴", "STS": "强热带风暴",
                 "TY": "台风", "STY": "强台风", "SuperTY": "超强台风"}


def _fetch_typhoon(loc: dict) -> dict | None:
    """活动台风列表（名称/编号/最新强度/位置）；无活动返回 active=false。"""
    ts = int(time.time() * 1000)
    text = _http_get_text(TYPHOON_LIST_API.format(ts=ts))
    data = _jsonp_unwrap(text) if text else None
    if not data or not isinstance(data.get("typhoonList"), list):
        return None
    active = [t for t in data["typhoonList"] if isinstance(t, list) and len(t) > 7 and t[7] == "start"]
    out: list[dict] = []
    for t in active[:3]:  # 最多汇总 3 个（台风通常 1-2 个）
        tid, name_en, name_cn, num = t[0], t[1], t[2], t[3]
        entry: dict = {"id": tid, "name": name_cn if name_cn and name_cn != "null" else name_en,
                       "num": str(num or "")}
        vt = _http_get_text(TYPHOON_VIEW_API.format(tid=tid, ts=ts))
        vd = _jsonp_unwrap(vt) if vt else None
        if vd and isinstance(vd.get("typhoon"), list) and len(vd["typhoon"]) > 8:
            points = vd["typhoon"][8]
            if points:
                last = points[-1]
                if len(last) > 7:
                    entry["grade"] = TYPHOON_GRADE.get(last[3], "")
                    entry["lat"] = last[5]
                    entry["lon"] = last[4]
                    entry["vmax"] = last[7]
        out.append(entry)
    return {"active": bool(out), "list": out}


# ---------- 服务注册表 ----------

SERVICES = {
    "pollen": {"label": "花粉浓度", "region": ["内蒙古"], "fetch": _fetch_pollen},
    "typhoon": {"label": "台风动态", "region": sorted(_COASTAL_PROVINCES), "fetch": _fetch_typhoon},
}


def available(loc: dict | None = None) -> list[str]:
    """按 location 判可用服务（province 粒度）。"""
    loc = loc or get_location()
    prov = _province(loc)
    return [name for name, svc in SERVICES.items() if prov in svc["region"]]


def fetch(loc: dict | None = None, service: str | None = None) -> dict:
    """拉取可用服务（None = 全部），每日缓存 + 进 shared；失败项缺省不抛错。

    显式指定服务也校验 location 可用性（防御：不请求不适用区域的服务）；
    调用方先 available() 是优化，fetch 自身兜底。
    """
    loc = loc or get_location()
    targets = [service] if service else available(loc)
    if not targets:
        return {}
    if service and service not in available(loc):
        return {}

    cache = shared_load("localdata") or {}
    today = date.today().isoformat()
    data = dict(cache.get("data") or {})
    out: dict = {}

    for s in targets:
        entry = data.get(s)
        if entry is not None and cache.get("date") == today:
            out[s] = entry
            continue
        try:
            res = SERVICES[s]["fetch"](loc)
        except Exception:
            res = None
        if res is not None:
            out[s] = res

    if out:
        data.update(out)
        shared_save("localdata", {"date": today, "data": data})
    return out
