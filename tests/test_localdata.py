"""common.localdata 测试（区域判定 / 服务可用性 / fetch 缓存与 shared 写入）。

网络隔离：mock SERVICES 的 fetch 函数与 shared 读写，不触真实网络/共享目录。
"""
from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from modules.common import localdata


# ---------- available（区域判定） ----------

def test_available_neimeng():
    loc = {"province": "内蒙古", "city": "集宁"}
    svc = localdata.available(loc)
    assert "pollen" in svc
    assert "typhoon" not in svc


def test_available_coastal():
    loc = {"province": "广东", "city": "广州"}
    svc = localdata.available(loc)
    assert "typhoon" in svc
    assert "pollen" not in svc


def test_available_fallback_city_name():
    """无 province 时城市名兜底（仅内蒙城市可兜底）。"""
    assert "pollen" in localdata.available({"province": "", "city": "乌兰察布"})
    assert "pollen" in localdata.available({"province": "", "city": "呼和浩特"})
    assert localdata.available({"province": "", "city": "广州"}) == []


def test_available_inland_no_service():
    assert localdata.available({"province": "北京", "city": "北京"}) == []


# ---------- fetch ----------

def test_fetch_service_unavailable():
    """服务不在可用列表 → 空结果不抛错。"""
    loc = {"province": "北京", "city": "北京"}
    assert localdata.fetch(loc, service="pollen") == {}
    assert localdata.fetch(loc) == {}


def test_fetch_mock_services_and_cache(monkeypatch):
    """可用服务拉取 + 每日缓存（同日不重复抓）+ 进 shared。"""
    saved = []
    monkeypatch.setattr(localdata, "shared_load", lambda name: {})
    monkeypatch.setattr(localdata, "shared_save",
                        lambda name, data: (saved.append((name, data)), True)[1])

    calls = {"pollen": 0, "typhoon": 0}

    def fake_pollen(loc):
        calls["pollen"] += 1
        return {"level": "中", "detail": "注意防护", "updated_at": "2026-08-20", "city": "乌兰察布"}

    def fake_typhoon(loc):
        calls["typhoon"] += 1
        return {"active": False, "list": []}

    monkeypatch.setitem(localdata.SERVICES, "pollen",
                        {"label": "花粉浓度", "region": ["内蒙古"], "fetch": fake_pollen})
    monkeypatch.setitem(localdata.SERVICES, "typhoon",
                        {"label": "台风动态", "region": ["广东"], "fetch": fake_typhoon})

    # 内蒙古：只拉 pollen
    out = localdata.fetch({"province": "内蒙古", "city": "乌兰察布"})
    assert out == {"pollen": {"level": "中", "detail": "注意防护", "updated_at": "2026-08-20", "city": "乌兰察布"}}
    assert calls == {"pollen": 1, "typhoon": 0}
    assert saved and saved[0][0] == "localdata" and saved[0][1]["date"] == date.today().isoformat()

    # 同日内缓存命中 → 不重复抓
    calls["pollen"] = 0
    monkeypatch.setattr(localdata, "shared_load", lambda name: {
        "date": date.today().isoformat(),
        "data": {"pollen": {"level": "低", "detail": "可正常出行", "updated_at": "x", "city": "乌兰察布"}},
    })
    out2 = localdata.fetch({"province": "内蒙古", "city": "乌兰察布"})
    assert out2["pollen"]["level"] == "低"
    assert calls["pollen"] == 0


def test_fetch_failure_degrades(monkeypatch):
    """抓取失败缺省不抛错（失败项跳过）。"""
    monkeypatch.setattr(localdata, "shared_load", lambda name: {})
    monkeypatch.setattr(localdata, "shared_save", lambda name, data: True)

    def boom(loc):
        raise RuntimeError("网络故障")

    monkeypatch.setitem(localdata.SERVICES, "pollen",
                        {"label": "花粉浓度", "region": ["内蒙古"], "fetch": boom})
    out = localdata.fetch({"province": "内蒙古", "city": "乌兰察布"})
    assert out == {}          # 失败 → 不写 shared、返回空


def test_fetch_single_service_checks_region(monkeypatch):
    """显式指定服务也校验 location 可用性（防御：不请求不适用区域）。"""
    monkeypatch.setattr(localdata, "shared_load", lambda name: {})
    monkeypatch.setattr(localdata, "shared_save", lambda name, data: True)
    calls = []

    def fake_typhoon(loc):
        calls.append(1)
        return {"active": False, "list": []}

    monkeypatch.setitem(localdata.SERVICES, "typhoon",
                        {"label": "台风动态", "region": ["广东"], "fetch": fake_typhoon})
    # 内蒙古位置显式指定 typhoon → 区域不符 → 空结果不抓取
    out = localdata.fetch({"province": "内蒙古", "city": "乌兰察布"}, service="typhoon")
    assert out == {}
    assert calls == []
    # 沿海位置显式指定 → 抓取
    out2 = localdata.fetch({"province": "广东", "city": "广州"}, service="typhoon")
    assert out2 == {"typhoon": {"active": False, "list": []}}
    assert calls == [1]


def test_fetch_pollen_uses_loc_city(monkeypatch):
    """F5.4：花粉 URL 与返回 city 由 location 驱动（不再硬编码乌兰察布）。"""
    captured = {}
    monkeypatch.setattr(localdata, "http_get_json", lambda url: (
        captured.setdefault("url", url),
        {"level": "中", "updatedAt": "2026-08-20"},
    )[1])
    out = localdata._fetch_pollen({"province": "内蒙古", "city": "呼和浩特"})
    from urllib.parse import unquote
    assert "呼和浩特" in unquote(captured["url"])
    assert out["city"] == "呼和浩特"
    assert localdata._fetch_pollen({"province": "内蒙古"}) is None
