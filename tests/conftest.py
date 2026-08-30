"""tests 全局隔离：permissions/module_source 的权限产物与模块路径重定向到临时目录。

register 的 update_module/set_enabled 等会联动 bridge.permissions.refresh_permissions()
落盘 .config/module-permissions.json——源码形态数据根=项目根，不隔离会污染真实部署数据
（CI 上还会触发 selftest 的"已部署"保护误判）。permissions 路径为调用时求值，
OPENCODE_PERMS_ROOT 对每个测试生效。
"""
from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def _isolate_permissions(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENCODE_PERMS_ROOT", str(tmp_path / "perms"))


@pytest.fixture(autouse=True)
def _guard_real_login_state(tmp_path, monkeypatch):
    """防线（260829 F6.2 教训）：测试不得触碰真实登录态目录。

    ① Path.home() 与 HOME env 重定向到 tmp_path/home——同时覆盖 Path.home()
    与 os.path.expanduser("~") 两条寻路；
    ② teardown 哨兵：真实数据根的 agent-SDK 登录态文件若被本次测试删改即 fail
    （test_reject_dirs_cover_sdk_paths 旧版 rmtree 清登录态事故的机制化防线）。
    """
    from bridge.config import DATA_ROOT

    fake_home = tmp_path / "home"
    monkeypatch.setenv("HOME", str(fake_home))
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: fake_home))

    sentinel = DATA_ROOT / "agent-SDK" / "accounts.json"
    before = sentinel.read_bytes() if sentinel.is_file() else None
    yield
    after = sentinel.read_bytes() if sentinel.is_file() else None
    if after != before:
        pytest.fail(
            f"本次测试改动/删除了真实登录态文件 {sentinel}——"
            "写操作请经 monkeypatch 隔离到 tmp_path（参照 test_reject_dirs_cover_sdk_paths）"
        )
