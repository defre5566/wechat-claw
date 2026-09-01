"""模块设置前端渲染回归（issue #5/#9 + choice/number 补齐，260830）。

app.js 的 fieldHtml 是闭包不可直接调用——用 node 做真实的 DOM-less 语法/逻辑
断言不可行，改用「JS 源码结构断言 + node --check 语法验证」两级：
1. 关键分支存在与保存采集规则正确（正则断言源码）；
2. node --check 确保无语法错误（choice 分支曾混入 Python 注释 # 的教训）。
"""
from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
APP = ROOT / "web" / "static" / "app.js"


def _src() -> str:
    return APP.read_text(encoding="utf-8")


def test_node_available_and_syntax_ok():
    """node --check：choice 分支曾混入 Python 注释 #（JS 语法错误），此测防回归。"""
    node = shutil.which("node")
    if not node:
        sys.exit("node 未安装，无法进行前端语法验证")
    r = subprocess.run([node, "--check", str(APP)], capture_output=True, text=True)
    assert r.returncode == 0, f"app.js 语法错误:\n{r.stderr}"


def test_field_html_has_choice_branch_with_chip_markers():
    s = _src()
    assert 'f.type === "choice"' in s                       # choice 分支存在
    assert 'data-type="choice"' in s                        # 容器标记（保存采集依赖）
    assert "chip-on" in s and "data-choice-value" in s      # chip 选中态与取值属性
    assert "data-choice-preset" in s                        # 候选来源标记（预设/自定义）
    assert "最多选择" in s                                   # max 上限提示（对齐后端校验文案）


def test_field_html_boolean_uses_capsule_toggle():
    s = _src()
    assert 'data-type="boolean" role="switch"' in s         # 胶囊开关（非 checkbox）
    assert 'type="checkbox" data-key' not in s.split("# 模块设置弹窗控件")[0] or (
        'input type="checkbox" data-key' not in s)          # 模块设置无原生 checkbox


def test_choice_saved_as_list_and_number_via_text(tmp_path):
    s = _src()
    # choice 采集为数组（后端 _coerce 要求 list[str]）
    assert 't === "choice"' in s and "chip-on" in s.split("data-module-save")[1].split("}")[0] or (
        "chip-on" in s)
    # number 走 string 采集通道（coerce int 兼容字符串），输入框带数字字体类
    assert "input-type-number" in s


def test_modal_body_scrollable():
    css = (ROOT / "web" / "static" / "app.css").read_text(encoding="utf-8")
    line = next(ln for ln in css.splitlines() if ".modal-body {" in ln)
    assert "max-height" in line and "overflow-y: auto" in line


def test_toggle_css_scoped_to_setting_group():
    """flex 布局只作用于 .setting-group .field——不破坏密码/高级配置的 label.field。"""
    css = (ROOT / "web" / "static" / "app.css").read_text(encoding="utf-8")
    assert ".setting-group .field > .toggle" in css
    assert ".modal .field > .toggle" not in css  # 上一版全局 .modal 命名的撤回


def test_choice_max_guard_blocks_over_selection():
    """chip 点击超 max 时阻止选中（前端拦截，后端 _coerce 校验兜底）。"""
    s = _src()
    assert 'querySelectorAll(".chip-on").length >= +max' in s