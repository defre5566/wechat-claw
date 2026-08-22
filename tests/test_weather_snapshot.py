"""逐小时天气快照的纯函数回归测试。"""
from __future__ import annotations

from modules.common import weather


def test_fetch_weather_snapshot_returns_current_and_hourly(monkeypatch):
    monkeypatch.setattr(
        weather,
        "get_location",
        lambda: {"city": "集宁", "lat": 40.99, "lon": 113.13},
    )
    monkeypatch.setattr(
        weather,
        "http_get_json",
        lambda _url: {
            "current": {
                "time": "2026-08-22T12:00",
                "temperature_2m": 18.4,
                "weather_code": 0,
                "wind_speed_10m": 8.2,
            },
            "hourly": {
                "time": [
                    "2026-08-22T11:00",
                    "2026-08-22T12:00",
                    "2026-08-22T13:00",
                    "2026-08-22T14:00",
                    "2026-08-22T15:00",
                ],
                "temperature_2m": [17.8, 18.4, 18.8, 19.2, 18.6],
                "weather_code": [0, 0, 0, 1, 2],
            },
        },
    )

    result = weather.fetch_weather_snapshot()

    assert result["ok"] is True
    assert result["city"] == "集宁"
    assert result["current"]["temperature"] == 18
    assert [item["time"] for item in result["hourly"]] == ["12:00", "13:00", "14:00", "15:00"]
    assert result["hourly"][-1]["description"] == "多云"


def test_fetch_weather_snapshot_returns_safe_failure(monkeypatch):
    monkeypatch.setattr(
        weather,
        "get_location",
        lambda: {"city": "集宁", "lat": 40.99, "lon": 113.13},
    )
    monkeypatch.setattr(weather, "http_get_json", lambda _url: None)

    result = weather.fetch_weather_snapshot()

    assert result == {"ok": False, "city": "集宁", "error": "天气暂时无法获取"}
