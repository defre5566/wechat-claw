"""用户喜好数据面：habit.json（列表）+ .prev 撤销。

- GUI 经 set_habits 写入（兴趣胶囊）；模块经 get_habits 消费
"""
from __future__ import annotations

from . import _userdata


def get_habits() -> list[str]:
    """读喜好列表；未设置返回 []。"""
    data = _userdata.load("habit", [])
    return data if isinstance(data, list) else []


def set_habits(habits: list[str]) -> bool:
    """写喜好列表（自动备份 prev，供撤销）。"""
    return _userdata.save("habit", list(habits))


def undo_habits() -> list[str]:
    """撤销上次修改；无 prev（无可撤销）返回当前值。"""
    prev = _userdata.undo("habit", None)
    if prev is not None:
        return prev if isinstance(prev, list) else []
    return get_habits()
