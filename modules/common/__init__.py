"""common 母模组（数据公共库）统一出口。

向后兼容：re-export 旧 common.py 全部符号——三 worker 的 `from common import ...` 零改动。
新增共享能力从这里导出，模块按需 import 字段/函数。
"""
from __future__ import annotations

from .crypto import decrypt, encrypt
from bridge.config import get as get_config, get
from .calendar import get_fufu, get_jieqi, get_lunar, in_fufu
from .holidays import is_holiday
from .io import load_sent_json, prune_state_file, save_sent_json, shared_load, shared_save
from .locations import DEFAULT_LOC, LOCATIONS
from .location import get_city, set_city, undo_city
from .habit import get_habits, set_habits, undo_habits
from .log import log_event
from .push import load_token, post_push
from .task import (
    ParsedTask,
    parse_milestones,
    parse_task_line,
    scan_todo_files,
    sort_due_key,
    strip_fields,
    VAULT_TODO_DIR,
)
from .weather import (
    fetch_pollen,
    fetch_weather,
    get_weather,
    http_get_json,
    WEATHER_CODES,
)