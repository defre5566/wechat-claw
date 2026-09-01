"""农历 / 节气 / 三伏 / 三九（lunar-python）。

早晚报与 emotion 可读这些字段生成节气/三伏/三九相关建议与关心。
"""
from __future__ import annotations

from datetime import date

from lunar_python import Solar


def get_lunar(d: date | None = None) -> dict:
    """当天农历信息：干支年 / 农历月日 / 生肖 / 节气。"""
    d = d or date.today()
    s = Solar.fromYmd(d.year, d.month, d.day)
    l = s.getLunar()
    return {
        "year_ganzhi": l.getYearInGanZhi(),
        "month": l.getMonthInChinese(),
        "day": l.getDayInChinese(),
        "zodiac": l.getAnimal(),
        "jieqi": l.getJieQi() or None,
    }


def get_jieqi(d: date | None = None) -> str | None:
    """当天是否为节气日；是则返回节气名，否则 None。"""
    return get_lunar(d)["jieqi"]


def get_fufu(d: date | None = None) -> list[str]:
    """当前三伏状态。返回如 ['初伏第3天']；非三伏期返回 []。"""
    d = d or date.today()
    l = Solar.fromYmd(d.year, d.month, d.day).getLunar()
    f = l.getFu()
    return [f.toString()] if f else []


def in_fufu(d: date | None = None) -> bool:
    """是否三伏期间（用于"三伏天"关怀）。"""
    return bool(get_fufu(d))


def get_jiujiu(d: date | None = None) -> list[str]:
    """当前数九状态。返回如 ['一九第3天']；非数九期返回 []。"""
    d = d or date.today()
    l = Solar.fromYmd(d.year, d.month, d.day).getLunar()
    sj = l.getShuJiu()
    return [sj.toFullString()] if sj else []


def in_jiujiu(d: date | None = None) -> bool:
    """是否数九期间（用于"数九寒天"关怀）。"""
    return bool(get_jiujiu(d))