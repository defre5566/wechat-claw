"""web/schema/module_schema.py 测试（choice 多选词条 + show_when_service 服务条件）。"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from web.schema.module_schema import validate_module_settings

SCHEMA = [
    {"section": "简报", "desc": "简报设置", "fields": [
        {"key": "briefing_topics", "type": "choice",
         "candidates": ["时政", "热点", "自定义A"], "max": 3, "default": ["热点"]},
        {"key": "pollen_on", "type": "boolean", "show_when_service": "pollen", "default": False},
        {"key": "module_name", "type": "string", "default": ""},
    ]},
]


# ---------- choice ----------

def test_choice_valid():
    ok, clean, errs = validate_module_settings(SCHEMA, {"briefing_topics": ["时政", "热点"]})
    assert ok and clean["briefing_topics"] == ["时政", "热点"] and not errs


def test_choice_empty_allowed():
    """不选（空列表）允许保存。"""
    ok, clean, errs = validate_module_settings(SCHEMA, {"briefing_topics": []})
    assert ok and clean["briefing_topics"] == [] and not errs


def test_choice_max_exceeded():
    ok, _clean, errs = validate_module_settings(
        SCHEMA, {"briefing_topics": ["时政", "热点", "自定义A", "自定义A"]})
    assert not ok and any("最多选择 3 项" in e for e in errs)


def test_choice_filters_unknown_candidates():
    """非法候选静默过滤（候选可变，如自定义 prompt 被删除后旧值仍保存）。"""
    ok, clean, errs = validate_module_settings(SCHEMA, {"briefing_topics": ["时政", "已删除方向"]})
    assert ok and clean["briefing_topics"] == ["时政"] and not errs


def test_choice_not_list():
    ok, _clean, errs = validate_module_settings(SCHEMA, {"briefing_topics": "时政"})
    assert not ok and errs


def test_choice_non_string_items():
    ok, _clean, errs = validate_module_settings(SCHEMA, {"briefing_topics": [1, 2]})
    assert not ok and errs


# ---------- show_when_service ----------

def test_service_condition_drops_when_unavailable():
    """服务不可用（services 不含 pollen）→ 丢弃提交值。"""
    ok, clean, errs = validate_module_settings(SCHEMA, {"pollen_on": True}, services=["typhoon"])
    assert ok and "pollen_on" not in clean and not errs


def test_service_condition_keeps_when_available():
    ok, clean, errs = validate_module_settings(SCHEMA, {"pollen_on": True}, services=["pollen"])
    assert ok and clean["pollen_on"] is True


def test_service_condition_ignored_without_services():
    """services 不传（None）→ 不校验服务条件（向后兼容）。"""
    ok, clean, errs = validate_module_settings(SCHEMA, {"pollen_on": True})
    assert ok and clean["pollen_on"] is True


# ---------- 既有行为回归 ----------

def test_select_invalid_option():
    schema = [{"section": "s", "fields": [
        {"key": "ds", "type": "select", "options": ["a", "b"], "default": "a"},
    ]}]
    ok, _clean, errs = validate_module_settings(schema, {"ds": "c"})
    assert not ok and errs


def test_unknown_keys_dropped():
    ok, clean, errs = validate_module_settings(SCHEMA, {"module_name": "x", "hack": "y"})
    assert ok and "hack" not in clean
