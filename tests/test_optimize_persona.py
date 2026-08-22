"""opencode 优化人设：双字段截取纯函数回归测试。"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from web.handlers.admin import _extract_sections


def test_extract_sections_two_blocks():
    out = "前言\n【角色设定】\n第一句。第二句。\n【语言习惯】\n甲。乙。"
    result = _extract_sections(out)
    assert result["role"] == "第一句。第二句。"
    assert result["language"] == "甲。乙。"


def test_extract_sections_missing_language():
    out = "【角色设定】\n只有角色。"
    result = _extract_sections(out)
    assert result["role"] == "只有角色。"
    assert result["language"] == ""


def test_extract_sections_language_only():
    out = "【语言习惯】\n只有语言。"
    result = _extract_sections(out)
    assert result["role"] == ""
    assert result["language"] == "只有语言。"


def test_extract_sections_no_markers_fallback():
    out = "纯文本输出"
    result = _extract_sections(out)
    assert result["role"] == "纯文本输出"
    assert result["language"] == ""


def test_extract_sections_strips_whitespace():
    out = "【角色设定】\n  第一句。\n\n【语言习惯】\n  甲。  "
    result = _extract_sections(out)
    assert result["role"] == "第一句。"
    assert result["language"] == "甲。"
