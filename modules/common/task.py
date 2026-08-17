"""任务解析（vault 扫描 + 待办解析 + DL:: 里程碑）。

正则规则与 Obsidian Dataview 视图、各模块规范.md 保持一致（单一事实源）。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, time
from pathlib import Path

VAULT_TODO_DIR = Path.home() / "文档" / "private" / "01-Todo"

TASK_LINE = re.compile(r"^-\s+\[( |x|X)\]\s+(.+)$")
DUE = re.compile(r"📅\s*(\d{4}-\d{2}-\d{2})")
TIME = re.compile(r"⏰\s*(\d{1,2}):(\d{2})")
REMIND = re.compile(r"🔔提前(\d+)分钟")
DONE = re.compile(r"✅\s*(\d{4}-\d{2}-\d{2})")
RECUR = re.compile(r"🔁")
TAG = re.compile(r"#todo/(\S+)")
PRIORITY = {"⏫": 1, "🔺": 2, "🔼": 3, "🔽": 4, "⏬": 5}

DL_MILESTONE = re.compile(r"^\s*DL::\s*(\d{4}-\d{2}-\d{2})\s*\|\s*(.+?)\s*$")


@dataclass
class ParsedTask:
    raw_line: str
    text: str
    due: date | None = None
    time: time | None = None
    remind_min: int | None = None
    done_date: date | None = None
    recurring: bool = False
    tags: list[str] = field(default_factory=list)
    priority: int = 3
    file: Path | None = None

    @property
    def key(self) -> str:
        """防重 key：日期|任务正文（同一任务当天只推一次）。"""
        d = self.due or date.today()
        return f"{d.isoformat()}|{self.text}"

    @property
    def trigger_time(self) -> time | None:
        """实际触发时刻（⏰ 减去 🔔 提前量）。"""
        if not self.time:
            return None
        minutes = self.time.hour * 60 + self.time.minute - (self.remind_min or 0)
        minutes %= 1440
        return time(minutes // 60, minutes % 60)


def strip_fields(text: str) -> str:
    """去掉任务行里的 emoji 字段，留纯正文。"""
    for pat in (DUE, TIME, REMIND, DONE, RECUR, TAG, re.compile(r"[⏫🔺🔼🔽⏬]"), re.compile(r"\s+")):
        text = pat.sub(" ", text)
    return text.strip()


def parse_task_line(line: str, file: Path | None = None) -> ParsedTask | None:
    """解析一行待办；非任务行返回 None。"""
    m = TASK_LINE.match(line)
    if not m:
        return None
    mark, body = m.group(1), m.group(2)
    due_m = DUE.search(body)
    time_m = TIME.search(body)
    remind_m = REMIND.search(body)
    done_m = DONE.search(body)

    def _d(match) -> date | None:
        return date.fromisoformat(match.group(1)) if match else None

    def _t(match) -> time | None:
        return time(int(match.group(1)), int(match.group(2))) if match else None

    return ParsedTask(
        raw_line=line.rstrip("\n"),
        text=strip_fields(body),
        due=_d(due_m),
        time=_t(time_m),
        remind_min=int(remind_m.group(1)) if remind_m else None,
        done_date=_d(done_m),
        recurring=bool(RECUR.search(body)),
        tags=TAG.findall(body),
        priority=next((PRIORITY[k] for k in PRIORITY if k in body), 3),
        file=file,
    )


def sort_due_key(t: ParsedTask) -> tuple[time, int]:
    """今日待办排序键：有 ⏰（含提前量）按触发时刻，无 ⏰ 按 23:59 排最后，再按优先级。

    planner/todo 共用；⚠️ 不允许混用 time 与 date 比较（TypeError）。
    """
    return (t.trigger_time or t.time or time(23, 59), t.priority)


def scan_todo_files(directory: Path = VAULT_TODO_DIR) -> list[ParsedTask]:
    """扫描目录下全部 Todo-*.md，返回所有任务行（含已完成，供筛选）。"""
    tasks: list[ParsedTask] = []
    if not directory.is_dir():
        return tasks
    for path in sorted(directory.glob("Todo-*.md")):
        in_code = False
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except OSError:
            continue
        for line in lines:
            stripped = line.strip()
            if stripped.startswith("```"):
                in_code = not in_code
                continue
            if in_code:
                continue
            if stripped.startswith("<!--"):
                continue
            task = parse_task_line(line.strip(), path)
            if task:
                tasks.append(task)
    return tasks


def parse_milestones(directory: Path = VAULT_TODO_DIR) -> list[tuple[date, str]]:
    """解析 DL:: 里程碑：[(日期, 名称)]。排除注释行。"""
    result: list[tuple[date, str]] = []
    if not directory.is_dir():
        return result
    for path in sorted(directory.glob("Todo-*.md")):
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except OSError:
            continue
        for line in lines:
            if line.strip().startswith("<!--"):
                continue
            m = DL_MILESTONE.match(line)
            if m:
                name = m.group(2).split("|")[0].strip()
                result.append((date.fromisoformat(m.group(1)), name))
    return result