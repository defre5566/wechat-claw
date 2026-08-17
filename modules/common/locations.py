"""位置数据（数据公共库）。

新增地点 = 在此加一条，天气等模块经 DEFAULT_LOC 使用。切换默认地点改 DEFAULT_LOC 即可。
"""
from __future__ import annotations

LOCATIONS = {
    "集宁": (41.03, 113.10),          # 默认常驻
    "太原小店区": (37.736, 112.556),  # 备用（Open-Meteo geocoding 实测，英文 Xiaodian）
}

DEFAULT_LOC = LOCATIONS["集宁"]