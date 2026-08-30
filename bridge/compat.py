"""compat：主程序↔模块兼容基线判定（issue #4 拍板机制，260830）。

- 基线 = 主程序版本 major.minor（0.1.5 → "0.1"）：基线内对模块承诺
  module.json 字段、common 公共库 API、worker 运行约定不变
- module.json 必须声明 `bridge_compat: ["0.1", ...]`（强制，未声明/格式错
  = 不兼容——防损坏 module.json 混过门禁）
- 主程序跨基线更新后（如 0.1 → 0.2），模块作者应及时适配新特性并更新声明；
  未适配的模块在启用时被拦截并明示原因
"""
from __future__ import annotations

from bridge.config import VERSION


def base_version() -> str:
    """当前主程序兼容基线 = VERSION 的 major.minor。"""
    return ".".join(VERSION.split(".")[:2])


def compat_ok(module_json: dict) -> tuple[bool, str]:
    """校验 module.json 的 bridge_compat 声明。

    返回 (是否兼容, 不兼容原因)；兼容时原因 为空串。
    """
    declared = module_json.get("bridge_compat")
    if not isinstance(declared, list) or not declared:
        return False, "module.json 缺少 bridge_compat 声明（应为如 [\"0.1\"] 的基线数组）"
    if not all(isinstance(x, str) and x.strip() for x in declared):
        return False, "bridge_compat 声明格式非法（应为字符串数组）"
    base = base_version()
    if base in declared:
        return True, ""
    return False, (f"模块声明兼容基线 {declared}，"
                   f"不含当前主程序基线 {base}——请更新模块或调整声明")
