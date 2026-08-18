"""位置数据（数据公共库）。

默认地点 = 北京（39.904179, 116.407387，与 web/static/cities.json 北京市条目一致）。
weather 在用户未配置/查询失败时回退到 DEFAULT_LOC。
"""
from __future__ import annotations

LOCATIONS = {
    "北京": (39.904179, 116.407387),  # 默认回退地点
}

DEFAULT_LOC = LOCATIONS["北京"]
