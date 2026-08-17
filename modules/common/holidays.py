"""法定节假日（每年按国务院办公厅通知更新）。

更新流程：每年 11-12 月国务院发布次年《关于XX年部分节假日安排的通知》后，
在 HOLIDAYS_YYYY 中补齐次年日期。键 = ISO 日期（如 "2026-10-01"），值 = 节假日名。
"""
from __future__ import annotations

from datetime import date

HOLIDAYS_2026: dict[str, str] = {
    # "2026-01-01": "元旦",
}

HOLIDAYS: dict[str, dict[str, str]] = {
    "2026": HOLIDAYS_2026,
}


def is_holiday(d: date | None = None) -> str | None:
    """当天是否法定节假日；是则返回名称，否则 None。"""
    d = d or date.today()
    return HOLIDAYS.get(str(d.year), {}).get(d.isoformat())