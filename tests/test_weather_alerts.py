"""common.weather 气象预警测试（code → 预警映射 + code 缓存）。

网络隔离：monkeypatch http_get_json / shared 读写，不触真实网络与共享目录。
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from modules.common import weather


def _loc():
    return {"lat": 41.03, "lon": 113.10, "city": "集宁"}


# ---------- weather_alerts（映射） ----------

def test_alert_mapping_storm(monkeypatch):
    monkeypatch.setattr(weather, "_current_code", lambda *a, **k: 95)
    assert weather.weather_alerts() == ["雷暴预警"]


def test_alert_mapping_heavy_rain(monkeypatch):
    monkeypatch.setattr(weather, "_current_code", lambda *a, **k: 82)
    assert weather.weather_alerts() == ["暴雨预警"]


def test_alert_none_for_clear(monkeypatch):
    monkeypatch.setattr(weather, "_current_code", lambda *a, **k: 0)
    assert weather.weather_alerts() == []


def test_alert_none_on_fetch_failure(monkeypatch):
    monkeypatch.setattr(weather, "_current_code", lambda *a, **k: None)
    assert weather.weather_alerts() == []


# ---------- _current_code（缓存 + 抓取） ----------

def test_current_code_cache_hit(monkeypatch):
    """新鲜缓存命中 → 不抓网。"""
    hits = []
    monkeypatch.setattr(weather, "shared_load", lambda name: {"code": 82, "ts": 10 ** 12})
    monkeypatch.setattr(weather, "http_get_json",
                        lambda *a, **k: (hits.append(1), {"current_weather": {"weathercode": 65}})[1])
    monkeypatch.setattr(weather, "shared_save", lambda name, data: True)
    monkeypatch.setattr(weather, "get_location", _loc)
    assert weather._current_code() == 82
    assert hits == []


def test_current_code_fetch_and_save(monkeypatch):
    """缓存过期/缺失 → 抓一次并写回。"""
    calls = []
    saved = []
    monkeypatch.setattr(weather, "shared_load", lambda name: {"code": 82, "ts": 0})
    monkeypatch.setattr(weather, "http_get_json",
                        lambda *a, **k: (calls.append(1), {"current_weather": {"weathercode": 65}})[1])
    monkeypatch.setattr(weather, "shared_save", lambda name, data: (saved.append(data), True)[1])
    monkeypatch.setattr(weather, "get_location", _loc)
    assert weather._current_code() == 65
    assert len(calls) == 1
    assert saved and saved[0]["code"] == 65


def test_current_code_no_coords(monkeypatch):
    """无坐标 → 返回 None（走 geocoding 兜底前，预警不阻塞）。"""
    monkeypatch.setattr(weather, "shared_load", lambda name: {})
    monkeypatch.setattr(weather, "get_location", lambda: {"city": "集宁"})
    assert weather._current_code() is None
