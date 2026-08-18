"""用户位置数据面：location.json（城市英文 key）+ .prev 撤销。

- GUI 经 set_city 写入（地区选择器选定）；模块经 get_city 消费
- 城市中文名/坐标全量库在 web 侧（仅服务选择器与定位反查）；
  本模块只落"用户选定的城市"这一份数据
"""
from __future__ import annotations

from . import _userdata


def get_city() -> str:
    """读用户城市（英文 key）；未设置返回 ''。"""
    data = _userdata.load("location", {})
    return data.get("city", "") if isinstance(data, dict) else ""


def set_city(city: str) -> bool:
    """写用户城市（自动备份 prev，供撤销）。"""
    return _userdata.save("location", {"city": city})


def undo_city() -> str:
    """撤销上次修改；无 prev（无可撤销）返回当前值。"""
    prev = _userdata.undo("location", None)
    if prev is not None:
        return prev.get("city", "") if isinstance(prev, dict) else ""
    return get_city()
