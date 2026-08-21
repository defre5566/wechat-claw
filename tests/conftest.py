"""tests 全局隔离：permissions/module_source 的权限产物与模块路径重定向到临时目录。

register 的 update_module/set_enabled 等会联动 bridge.permissions.refresh_permissions()
落盘 .config/module-permissions.json——源码形态数据根=项目根，不隔离会污染真实部署数据
（CI 上还会触发 selftest 的"已部署"保护误判）。permissions 路径为调用时求值，
OPENCODE_PERMS_ROOT 对每个测试生效。
"""
from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _isolate_permissions(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENCODE_PERMS_ROOT", str(tmp_path / "perms"))
